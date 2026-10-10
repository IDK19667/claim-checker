import hashlib
import json
import os
import secrets
from datetime import datetime
import sqlite3
from html import escape
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()  # must run before verdict.py reads the provider keys

import requests
from flask import (Flask, Response, g, jsonify, redirect, render_template, request,
                   send_from_directory)

import db
import evidence
import og
import pubmed
import nextsteps
import ratelimit
import suggest as suggest_mod
import verdict

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# How long a verdict for a given claim is reused before re-checking.
# 0 disables caching.
VERDICT_CACHE_HOURS = float(os.environ.get("VERDICT_CACHE_HOURS", "24"))

# Set TRUST_PROXY=1 when running behind a reverse proxy / hosting platform
# so rate limiting sees the real client IP instead of the proxy's.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "") in ("1", "true", "yes")

# Public URL for absolute links in share previews (e.g. https://claims.example).
# Falls back to the request host.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

MAX_CLAIM_CHARS = 500

VERDICT_LABELS = {"true": "Likely true", "false": "Likely false", "complicated": "It's complicated",
                  "insufficient": "Not enough evidence"}


# ---------------------------------------------------------------------
# Security headers.
#
# The design already forbids third-party requests: fonts are self-hosted,
# there is no analytics and no CDN. A strict policy makes the browser
# enforce that rule instead of trusting every future change to honour it.
#
# Referrer-Policy is a privacy rule, not just a security one. A result URL
# carries the claim in its query string, so sending it as a referrer to
# PubMed when someone opens a study would hand a third party the thing the
# privacy page promises never to share.
# ---------------------------------------------------------------------

# Inline style attributes set the evidence bar widths from real percentages,
# so style-src has to allow them. Scripts get a per-response nonce instead.
CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)


@app.before_request
def _make_csp_nonce() -> None:
    g.csp_nonce = secrets.token_urlsafe(16)


@app.context_processor
def _expose_csp_nonce() -> dict:
    return {"csp_nonce": getattr(g, "csp_nonce", "")}


@app.after_request
def _security_headers(response: Response) -> Response:
    response.headers.setdefault(
        "Content-Security-Policy",
        CSP_TEMPLATE.format(nonce=getattr(g, "csp_nonce", "")),
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    )
    # Only promise HTTPS-only to browsers that already reached us over HTTPS,
    # so a local or plain-HTTP deployment cannot lock itself out.
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

db.init_db()


# ---------------------------------------------------------------------
# Pages & PWA assets
# ---------------------------------------------------------------------

def _base_url() -> str:
    return PUBLIC_URL or request.url_root.rstrip("/")


def _claim_from_query_args() -> str:
    """?q= from our own links; ?text=/?title= from the Android share target."""
    for key in ("q", "text", "title"):
        v = (request.args.get(key) or "").strip()
        if v:
            return v[:MAX_CLAIM_CHARS]
    return ""


# ClaimReview has one scale. "Not enough evidence" sits in the middle with
# "It's complicated"; the alternateName is what tells the two apart.
RATING = {"true": 5, "false": 1, "complicated": 3, "insufficient": 3}


def _claim_review_jsonld(cached: dict, url: str) -> dict:
    """
    schema.org ClaimReview for the verdict on this page, plus the PubMed
    studies as citations. Written so a crawler or a language model can read
    the conclusion and check its sources without running our JavaScript.
    The reviewer is declared as software, not a person: the verdict is
    written by a model, and the markup says so.
    """
    studies = cached.get("studies") or []
    cited = set(cached.get("cited_studies") or [])
    return {
        "@context": "https://schema.org",
        "@type": "ClaimReview",
        "url": url,
        "datePublished": (cached.get("cached_at") or "")[:10],
        "claimReviewed": cached["claim_text"],
        "author": {
            "@type": "Organization",
            "name": "Evident",
            "url": PUBLIC_URL or "/",
            "description": "Checks health claims against studies indexed on PubMed. "
                           "Verdicts are written by an AI model that reads the abstracts.",
        },
        "reviewRating": {
            "@type": "Rating",
            "ratingValue": RATING.get(cached["verdict"], 3),
            "bestRating": 5,
            "worstRating": 1,
            "alternateName": VERDICT_LABELS.get(cached["verdict"], cached["verdict"]),
            "ratingExplanation": cached.get("tldr") or cached.get("explanation") or "",
        },
        "itemReviewed": {"@type": "Claim", "text": cached["claim_text"]},
        "citation": [_citation(s, s.get("pmid") in cited) for s in studies],
    }


def _citation(s: dict, relied_on: bool) -> dict:
    c = {
        "@type": "ScholarlyArticle",
        "name": s.get("title"),
        "url": s.get("url"),
        "identifier": f"PMID:{s.get('pmid')}",
        "isBasedOn": relied_on,
    }
    if pubmed.year_of(s.get("year")):
        c["datePublished"] = pubmed.year_of(s.get("year"))
    if s.get("journal"):
        c["isPartOf"] = {"@type": "Periodical", "name": s["journal"]}
    return c


# The footage is 552 frames per size, and Flask serves static files with
# "no-cache", so every visit asked the server about every frame again: on a
# free instance at about 0.2s each, the fly-through sat on "Loading footage"
# for most of a minute. The frames are served instead from a path that
# carries a fingerprint of the footage's own manifests, with a year-long,
# immutable cache. The browser keeps them, and so can the CDN in front of
# Render. Rebuilding the footage rewrites its manifests (frame sizes, byte
# totals), which changes the fingerprint, so a returning reader never sees
# old frames under a new check.
FOOTAGE_MAX_AGE = 365 * 24 * 3600
_footage_version_memo: list[str] = []


def _footage_version() -> str:
    if not _footage_version_memo:
        base = os.path.join(app.static_folder, "flight")
        h = hashlib.sha256()
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            if name.endswith(".json"):
                with open(os.path.join(base, name), "rb") as f:
                    h.update(name.encode() + b"\0" + f.read())
        _footage_version_memo.append(h.hexdigest()[:10])
    return _footage_version_memo[0]


@app.route("/footage/<ver>/<path:filename>")
def footage(ver: str, filename: str):
    resp = send_from_directory(os.path.join(app.static_folder, "flight"), filename,
                               max_age=FOOTAGE_MAX_AGE)
    resp.headers["Cache-Control"] = f"public, max-age={FOOTAGE_MAX_AGE}, immutable"
    return resp


def _flight_context():
    """
    The fly-through's own data, or None if it is not built.

    The home page must render without it: a fresh checkout has no frame
    sequence, and a missing or unreadable check.json is not a reason to
    serve a 500 or, worse, an invented check. The checker below it is the
    page's actual job and works either way.
    """
    base = os.path.join(app.static_folder, "flight")
    try:
        with open(os.path.join(base, "check.json")) as f:
            check = json.load(f)
        with open(os.path.join(base, "beats.json")) as f:
            beats = json.load(f)
        with open(os.path.join(base, "manifest.json")) as f:
            poster = json.load(f)["poster"]
    except (OSError, ValueError):
        return None
    d = datetime.fromisoformat(check["checkedAt"])
    check["checkedAt_long"] = f"{d.day} {d.strftime('%B %Y')}"
    return {"check": check, "chapters": beats["chapters"], "stills": _FLIGHT_STILLS,
            "credits": _FLIGHT_CREDITS, "poster": poster,
            "base": f"/footage/{_footage_version()}"}


def _trust_example():
    """
    The real check the trust section offers as proof: the one the fly-through
    replays, read from the same file, so the two can never disagree. None if
    it is not built, and the section renders without it rather than with an
    invented example.
    """
    try:
        with open(os.path.join(app.static_folder, "flight", "check.json")) as f:
            check = json.load(f)
        d = datetime.fromisoformat(check["checkedAt"])
        return {"claim": check["claim"], "verdict": check["verdict"], "tldr": check["tldr"],
                "checked": f"{d.day} {d.strftime('%B %Y')}",
                "read": len(check.get("studies") or []),
                "href": "/?q=" + quote(check["claim"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _render_home(with_flight: bool):
    claim = _claim_from_query_args()
    preview = None
    cached = None
    jsonld = None
    if claim:
        cached = db.get_cached_by_key(db.normalize_claim(claim))
        if cached:
            label = VERDICT_LABELS.get(cached["verdict"], cached["verdict"])
            share_url = f"{_base_url()}/?q={quote(cached['claim_text'])}"
            preview = {
                "title": f"{label}: “{cached['claim_text']}”",
                "description": cached["tldr"] or cached["explanation"],
                "image": f"{_base_url()}/og/{quote(db.normalize_claim(claim), safe='')}.png",
                "url": share_url,
            }
            jsonld = _claim_review_jsonld(cached, share_url)
            cached = _check_response(
                cached["claim_text"], cached.get("search_query") or "", cached["verdict"],
                cached["tldr"], cached["explanation"], cached["cited_studies"], cached["studies"],
                _base_url(), cached_at=cached["cached_at"], still_open=cached.get("still_open", ""),
                breakdown=cached.get("breakdown"))
    # The front page carries the work instead of describing it: real counts
    # and the most recent verdicts with the evidence behind each. Both are
    # best-effort; an empty database must still render a usable page.
    try:
        ledger = db.ledger()
        latest = [dict(v, evidence=evidence.snapshot(v["studies"], v["cited"]))
                  for v in db.recent_verdicts(10)]
    except sqlite3.Error as e:
        app.logger.warning("Could not build the front page ledger: %s", e)
        ledger, latest = None, []
    # The fly-through is the home page's first screen, but never on a result
    # page: a shared link loaded cold gets a still header rather than a 23MB
    # frame sequence it did not ask for.
    flight = _flight_context() if (with_flight and not cached) else None
    # The band's picture on a result page: one still of the verdict shot,
    # 28KB, where a live check plays the clip itself. A cold link gets the
    # same layout and none of the video. Only if it has actually been built,
    # so a fresh checkout renders a result page with no band rather than a
    # broken one.
    band_still = None
    if cached and os.path.exists(
            os.path.join(app.static_folder, "footage", BAND_STILL)):
        band_still = BAND_STILL
    return render_template("index.html", preview=preview, base_url=_base_url(),
                           result=cached, jsonld=jsonld, labels=VERDICT_LABELS,
                           ledger=ledger, latest=latest, flight=flight,
                           band_still=band_still,
                           example=None if cached else _trust_example(),
                           provider=verdict.provider_label(),
                           today=datetime.now().strftime("%A, %B %-d, %Y"))


@app.route("/")
def index():
    """The fly-through, then the checker, then the ledger and recent checks."""
    return _render_home(with_flight=True)


@app.route("/checks")
def checks():
    """
    The checker on its own, with no footage above it: the page the home
    page used to be. Same ledger, same recent checks, same everything, for
    anyone who wants the tool and not the film.
    """
    return _render_home(with_flight=False)


@app.route("/flight")
def flight_redirect():
    """The fly-through moved to the home page. Old links keep working."""
    return redirect("/", code=301)


@app.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "",
        # The verdicts are meant to be quotable. Say so rather than leaving
        # the AI crawlers to guess from silence.
        "User-agent: GPTBot",
        "Allow: /",
        "User-agent: ClaudeBot",
        "Allow: /",
        "User-agent: PerplexityBot",
        "Allow: /",
        "User-agent: Google-Extended",
        "Allow: /",
        "",
        f"Sitemap: {_base_url()}/sitemap.xml",
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    base = _base_url()
    urls = [(f"{base}/", "1.0"), (f"{base}/checks", "0.8"), (f"{base}/privacy", "0.3")]
    for row in db.recent_cached(200):
        urls.append((f"{base}/?q={quote(row['claim_text'])}", "0.7"))
    body = "".join(
        f"<url><loc>{escape(u)}</loc><priority>{p}</priority></url>" for u, p in urls
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")
    return Response(xml, mimetype="application/xml")


@app.route("/llms.txt")
def llms_txt():
    """A plain description of this site for language models that read one."""
    base = _base_url()
    recent = db.recent_cached(25)
    lines = [
        "# Evident",
        "",
        "> Paste a health claim; it is checked against studies indexed on PubMed "
        "and answered with a verdict, the studies the verdict relied on, and one "
        "line naming what is still open.",
        "",
        "Verdicts are written by an AI model that reads the abstracts of up to 8 "
        "PubMed records. A verdict may only cite studies the model was shown, and "
        "a verdict with no citations is forced to \"not enough evidence\" in code. "
        "\"Likely false\" requires studies that contradict the claim; an unstudied "
        "claim, or one with only case reports or lab studies, is \"not enough "
        "evidence\", never false. \"It's complicated\" is for real evidence that is "
        "mixed, partial or uncertain. This is not medical advice.",
        "",
        "## How to read a result page",
        "",
        f"- Any claim can be checked at {base}/?q=YOUR+CLAIM",
        "- Each result page carries schema.org ClaimReview markup with the verdict, "
        "the rating, and every PubMed study as a citation. `isBasedOn: true` marks "
        "the studies the verdict actually relied on.",
        "",
        "## Pages",
        "",
        f"- [How a check works]({base}/): one real check replayed as footage, "
        f"with the checker on the same page",
        f"- [Check a claim]({base}/checks): the tool on its own, no footage",
        f"- [Privacy]({base}/privacy): what is kept, what is sent where, what is never collected",
    ]
    if recent:
        lines += ["", "## Recently checked claims", ""]
        for row in recent:
            label = VERDICT_LABELS.get(row["verdict"], row["verdict"])
            lines.append(f"- [{row['claim_text']}]({base}/?q={quote(row['claim_text'])}): {label}")
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.errorhandler(404)
def not_found(_e):
    """
    A dead end is still a page. It offers the one thing the visitor came
    for, a claim field, plus recent checks, so a stale or mistyped link
    never ends the visit. Best effort: an unreachable database must still
    render the page rather than turning a 404 into a 500.
    """
    try:
        latest = db.recent_verdicts(5)
    except sqlite3.Error:
        latest = []
    return render_template("404.html", latest=latest, labels=VERDICT_LABELS), 404


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", base_url=_base_url(), provider=verdict.provider_label())


@app.route("/og/<path:key>.png")
def og_image(key):
    cached = db.get_cached_by_key(key)
    if cached:
        receipt = og.strongest_cited(cached["studies"], cached["cited_studies"])
        png = og.render_card(cached["claim_text"], cached["verdict"], cached["tldr"], len(cached["studies"]), receipt)
    else:
        png = og.render_card("Saw a health claim? Check it against the research.", "complicated", "", 0)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.route("/manifest.webmanifest")
def manifest():
    resp = send_from_directory(app.static_folder, "manifest.webmanifest")
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


@app.route("/sw.js")
def service_worker():
    # Served from the root so its scope covers the whole app.
    resp = send_from_directory(app.static_folder, "sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


# "How a check works": a scroll fly-through of one real, cached check,
# reachable from a quiet link on the front page rather than replacing its
# hero. Everything it shows is read from files on disk: the timeline from
# beats.json and the check from check.json, which
# scripts/export_flight_check.py exports out of the verdict cache. If either
# file is missing the route 404s rather than inventing a placeholder check,
# because a fabricated verdict is the one thing this product must never show.
_FLIGHT_STILLS = [
    {"file": "still-1-claim.webp",
     "alt": "A finger scrolling a phone lying on a desk, its screen lit.",
     "caption": "A claim can reach anyone, anywhere, in a moment."},
    {"file": "still-2-archive.webp",
     "alt": "Rows of bound volumes on library shelves, viewed down the aisle.",
     "caption": "We step back and search the published research, not the internet."},
    {"file": "still-3-weighing.webp",
     "alt": "A researcher at a laboratory bench, working at a computer.",
     "caption": "Each study is weighed, graded by what kind of evidence it is."},
    {"file": "still-4-write.webp",
     "alt": "A hand annotating handwritten research notes on a desk.",
     "caption": "The findings get written up, ready to stand behind."},
    {"file": "still-5-publish.webp",
     "alt": "A laptop screen showing an open-access research guide webpage.",
     "caption": "Then the record goes public, so anyone can check it."},
]

# Clips with a licence that requires attribution would list a short credit
# line here ({"clip": "...", "credit": "..."}); the round-5 set is Pexels
# License / Pixabay License throughout (free, no attribution required), so
# this is empty in practice, but flight.html still renders it when present
# rather than assuming it will always stay empty.
_FLIGHT_CREDITS = []

# The band's still on a result page. It is the first frame of the same loop
# a live check ends on (scripts/build_footage.py), so a shared link and a
# check that just finished show the same picture.
BAND_STILL = "verdict.webp"


# ---------------------------------------------------------------------
# The check pipeline, as a generator of stage events. Both the JSON and
# the streaming endpoint consume it, so the logic lives in one place.
# ---------------------------------------------------------------------

def _client_ip() -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _study_payload(s: dict, cited: set) -> dict:
    return {
        "pmid": s["pmid"],
        "title": s["title"],
        "journal": s["journal"],
        # Normalised here too, for rows cached before pubmed.year_of existed.
        "year": pubmed.year_of(s.get("year")),
        "authors": s.get("authors") or [],
        "publication_types": s["publication_types"],
        # The study's tier, classified once on the server from PubMed's own
        # publication types, so both renderers draw the same evidence field
        # instead of each deciding for itself.
        "tier": evidence.classify(s["publication_types"]),
        # The most informative type name, for the one line the evidence
        # chart shows on hover and focus. Same reason as the tier.
        "type_label": evidence.strongest_label(s["publication_types"]),
        # The narrow group this study was run in, or null for a general
        # population. Classified on the server for the same reason as the
        # tier: it is a fact about the record, and both renderers need it.
        "population": evidence.population(s),
        "abstract": s.get("abstract"),
        "data_banks": s.get("data_banks"),
        "url": s["url"],
        "cited_in_verdict": s["pmid"] in cited,
        # Why this record is not evidence for the claim, or null. Set by
        # the relevance gate in verdict.py and cached with the record.
        "off_topic": s.get("off_topic"),
    }


def _next_steps_safe(claim, query_used, studies, cited):
    """Never let the empty-state helper take down a check that worked."""
    try:
        return nextsteps.build(claim, query_used, studies, cited, db.all_claims())
    except (sqlite3.Error, ValueError, TypeError) as e:
        app.logger.warning("Could not build next steps: %s", e)
        return None


def _check_response(claim, search_query, verdict_value, tldr, explanation, cited_studies, studies,
                    base_url, cached_at=None, broadened=False, still_open="", breakdown=None,
                    typed=None):
    cited = set(cited_studies or [])
    return {
        "claim": claim,
        # What the reader typed, present only when its spelling was fixed
        # and the check ran on the corrected claim, so the page can say so.
        "typed": typed,
        "search_query_used": search_query,
        "search_broadened": broadened,
        "verdict": verdict_value,
        "tldr": tldr,
        "explanation": explanation,
        "still_open": still_open or "",
        # The deeper layer, or None. Collapsed wherever it is rendered, and
        # absent on results cached before it existed.
        "breakdown": breakdown,
        "cached": cached_at is not None,
        "cached_at": cached_at,
        "share_url": f"{base_url}/?q={quote(claim)}",
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/?term={quote(search_query or claim)}",
        # Counts the reader can check us on, computed from PubMed's own
        # metadata rather than from anything the model said.
        "evidence": evidence.snapshot(studies, cited),
        # Present only when the check came back without usable evidence, so
        # a dead end offers somewhere to go instead of stopping.
        "next_steps": _next_steps_safe(claim, search_query, studies, cited),
        "studies": [_study_payload(s, cited) for s in studies],
    }


def _log_check_safe(claim, verdict_value, explanation, studies, cached=False):
    """Logging is a side effect; it must never sink a check the user waited for."""
    try:
        db.log_check(claim, verdict_value, explanation, studies, cached=cached)
    except sqlite3.Error as e:
        app.logger.warning("Could not log check: %s", e)


def _run_check(claim: str, client_ip: str, base_url: str):
    """
    Yields stage dicts. Terminal stages are "done" (with "result") and
    "error" (with "error", "status", optional "retry_after").

    Everything request-bound (ip, base_url) is passed in: when streamed,
    this generator runs after the request context has ended.
    """
    if not claim:
        yield {"stage": "error", "status": 400, "error": "Type a health claim to check."}
        return
    if len(claim) > MAX_CLAIM_CHARS:
        yield {"stage": "error", "status": 400,
               "error": f"That's a long one. Trim the claim to under {MAX_CLAIM_CHARS} characters."}
        return

    def from_cache(cached, typed=None):
        _log_check_safe(claim, cached["verdict"], cached["explanation"], cached["studies"], cached=True)
        return {"stage": "done", "result": _check_response(
            claim, cached["search_query"], cached["verdict"], cached["tldr"], cached["explanation"],
            cached["cited_studies"], cached["studies"], base_url, cached_at=cached["cached_at"],
            still_open=cached.get("still_open", ""), breakdown=cached.get("breakdown"), typed=typed)}

    # 1. Cache first: a repeat of a recent claim costs nothing and is
    #    never rate-limited. Viral claims get checked by many people.
    cached = db.get_cached_verdict(claim, VERDICT_CACHE_HOURS)
    if cached:
        yield from_cache(cached)
        return

    # 2. Rate limit only the checks that will actually hit the AI provider.
    allowed, message, retry_after = ratelimit.check(f"ip:{client_ip}")
    if not allowed:
        yield {"stage": "error", "status": 429, "error": message, "retry_after": retry_after}
        return

    # 3. The real thing, stage by stage.
    try:
        yield {"stage": "search"}
        search_query, surrogate, understood = verdict.extract_search_terms(claim)
        typed = None
        if understood != claim:
            # Misspelled. Everything from here runs on the claim as meant:
            # the verdict, the code's own checks, the cache and the share
            # link. A correctly spelled check of it may already be cached.
            typed, claim = claim, understood
            cached = db.get_cached_verdict(claim, VERDICT_CACHE_HOURS)
            if cached:
                yield from_cache(cached, typed)
                return
        yield {"stage": "query", "query": search_query, "claim": claim if typed else None}
        studies, query_used, broadened = pubmed.search_with_fallback(
            search_query, max_results=8, surrogate=surrogate)
        yield {"stage": "found", "count": len(studies), "query": query_used, "broadened": broadened}
        if studies:
            yield {"stage": "weigh"}
        result = verdict.weigh_evidence(claim, studies, query_used, surrogate)
    except RuntimeError as e:
        # Missing/misconfigured API key -> tell the operator plainly, don't 500.
        yield {"stage": "error", "status": 500, "error": str(e)}
        return
    except verdict.ProviderError as e:
        status = {"auth": 500, "rate_limit": 429, "unavailable": 502}.get(e.kind, 502)
        suffix = " Wait a few seconds and try again." if e.kind == "rate_limit" else ""
        ev = {"stage": "error", "status": status, "error": e.detail + suffix}
        if e.kind == "rate_limit":
            ev["retry_after"] = 20
        yield ev
        return
    except requests.RequestException:
        yield {"stage": "error", "status": 502,
               "error": "Couldn't reach PubMed right now. Try again in a minute."}
        return
    except Exception as e:  # noqa: BLE001 — last line of defence; never crash a check
        yield {"stage": "error", "status": 500,
               "error": f"Something went wrong while checking this claim: {e}"}
        return

    db.put_cached_verdict(claim, query_used, result["verdict"], result["explanation"],
                          result["cited_studies"], studies, tldr=result["tldr"],
                          still_open=result.get("still_open", ""),
                          breakdown=result.get("breakdown"))
    _log_check_safe(claim, result["verdict"], result["explanation"], studies)
    yield {"stage": "done", "result": _check_response(
        claim, query_used, result["verdict"], result["tldr"], result["explanation"],
        result["cited_studies"], studies, base_url, broadened=broadened,
        still_open=result.get("still_open", ""), breakdown=result.get("breakdown"), typed=typed)}


def _claim_from_body() -> str:
    payload = request.get_json(force=True, silent=True) or {}
    return (payload.get("claim") or "").strip()


@app.route("/api/check", methods=["POST"])
def api_check():
    final = None
    for ev in _run_check(_claim_from_body(), _client_ip(), _base_url()):
        final = ev
    if final["stage"] == "error":
        body = {"error": final["error"]}
        if "retry_after" in final:
            body["retry_after"] = final["retry_after"]
        resp = jsonify(body)
        if "retry_after" in final:
            resp.headers["Retry-After"] = str(final["retry_after"])
        return resp, final["status"]
    return jsonify(final["result"])


@app.route("/api/check/stream", methods=["POST"])
def api_check_stream():
    """Same pipeline, as server-sent events so the UI can show real progress."""
    claim = _claim_from_body()
    client_ip = _client_ip()
    base_url = _base_url()

    def events():
        for ev in _run_check(claim, client_ip, base_url):
            yield "data: " + json.dumps(ev) + "\n\n"

    return Response(events(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/api/trending")
def api_trending():
    return jsonify(db.trending())


@app.route("/api/study/<pmid>")
def api_study(pmid):
    """
    Layer 3: what the rest of the literature did with one study.

    Never fails the sheet. If NCBI is slow or down the reader still has the
    abstract, so every error path returns a usable, honest shape rather than
    a status code the sheet would have to translate into an apology.
    """
    if not pmid.isdigit() or len(pmid) > 12:
        return jsonify({"error": "That is not a PubMed ID."}), 400
    allowed, _ = ratelimit.DEEP_DIVE.check(f"ip:{_client_ip()}")
    if not allowed:
        return jsonify({"pmid": pmid, "cited_by": None, "full_text_url": None,
                        "related": [], "partial": True,
                        "note": "Too many lookups just now. The abstract is still here."}), 429
    try:
        dive = pubmed.deep_dive(pmid)
    except Exception as e:  # noqa: BLE001 - a deep dive must never 500 the sheet
        app.logger.warning("Deep dive failed for %s: %s", pmid, e)
        return jsonify({"pmid": pmid, "cited_by": None, "full_text_url": None,
                        "related": [], "partial": True,
                        "note": "Could not reach PubMed for the extra detail."})
    dive["related"] = [_study_payload(s, set()) for s in dive["related"]]
    return jsonify(dive)


@app.route("/api/suggest")
def api_suggest():
    """
    Type-ahead over claims already checked. No model call, no network: this
    reads SQLite and ranks locally, so it is free and instant, and steering
    someone onto a cached answer costs nothing instead of a round trip.
    """
    q = (request.args.get("q") or "").strip()[:MAX_CLAIM_CHARS]
    if len(q) < suggest_mod.MIN_QUERY:
        return jsonify({"suggestions": []})
    try:
        claims = db.all_claims()
    except sqlite3.Error as e:
        app.logger.warning("Could not read claims for suggestions: %s", e)
        return jsonify({"suggestions": []})
    return jsonify({"suggestions": suggest_mod.suggest(q, claims)})


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    payload = request.get_json(force=True, silent=True) or {}
    claim = (payload.get("claim") or "").strip()
    if not claim or "helpful" not in payload:
        return jsonify({"error": "claim and helpful are required."}), 400
    allowed, _ = ratelimit.FEEDBACK.check(f"ip:{_client_ip()}")
    if not allowed:
        return jsonify({"ok": True})  # silently drop floods; nothing for the user to do
    try:
        db.add_feedback(claim, payload.get("verdict"), bool(payload.get("helpful")),
                        str(payload.get("note") or ""))
    except sqlite3.Error as e:
        app.logger.warning("Could not save feedback: %s", e)
    return jsonify({"ok": True})


if __name__ == "__main__":
    # HOST=0.0.0.0 makes it reachable from a phone on the same Wi-Fi.
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        # Off by default: HOST defaults to 0.0.0.0, and the Werkzeug debugger
        # is a remote shell for anyone on the same network.
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
        threaded=True,
    )
