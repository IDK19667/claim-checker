import gzip
import hashlib
import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
import sqlite3
from html import escape
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()  # must run before verdict.py reads the provider keys

import requests
from flask import (Flask, Response, g, jsonify, redirect, render_template, request,
                   send_from_directory)

import breakdown as breakdown_mod
import display
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
# How many PubMed records a check reads. /method states it.
MAX_STUDIES = 8
VERDICT_CACHE_HOURS = float(os.environ.get("VERDICT_CACHE_HOURS", "24"))

# Set TRUST_PROXY=1 when running behind a reverse proxy / hosting platform
# so rate limiting sees the real client IP instead of the proxy's.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "") in ("1", "true", "yes")

# Public URL for absolute links in share previews (e.g. https://claims.example).
# Falls back to the request host.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

MAX_CLAIM_CHARS = 500

# What a reader sees when the checker itself is not set up or its key was
# refused. The specifics are logged; they are not the reader's to fix.
UNAVAILABLE_MESSAGE = "The checker is not available right now. Try again in a little while."

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


# What the result page prints from the breakdown, each sentence once.
app.jinja_env.globals["breakdown_view"] = display.breakdown_view
app.jinja_env.globals["confidence_line"] = display.confidence_line
app.jinja_env.globals["design_note"] = display.design_note
app.jinja_env.globals["study_why"] = display.study_why


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

# Text is sent gzipped to a browser that accepts it. The origin used to send
# the shell raw and rely on whatever sat in front of it, so a page's weight
# (and the numbers scripts/perf measures) depended on the host, and a free
# instance reached directly sent every byte. Standard library only. Images,
# fonts and video are already compressed and are left alone, as is the event
# stream (it has to flush each event) and anything partial.
_GZIP_TYPES = {"text/html", "text/css", "text/plain", "text/javascript", "application/javascript", "application/json",
               "application/manifest+json", "application/xml", "image/svg+xml"}


@app.after_request
def _compress(response: Response) -> Response:
    if (response.status_code != 200 or response.mimetype not in _GZIP_TYPES
            or "Content-Encoding" in response.headers or "Range" in request.headers
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()):
        return response
    response.direct_passthrough = False
    data = response.get_data()
    if len(data) < 600:
        return response
    response.set_data(gzip.compress(data, compresslevel=6, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    response.headers.add("Vary", "Accept-Encoding")
    tag = response.get_etag()[0]
    if tag:
        response.set_etag(tag, weak=True)  # the bytes differ from the plain copy's
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


def _trust_example():
    """
    The real check the trust section offers as proof, read from the exported
    check.json so it is never invented. None if
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


def _render_home(checks_page: bool = False):
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
            # The link preview and the markup say what the page says, with
            # the page's one certainty and stamp (both can be capped above).
            label = VERDICT_LABELS.get(cached["verdict"], cached["verdict"])
            preview["title"] = f"{label}: “{cached['claim']}”"
            preview["description"] = cached["tldr"] or cached["explanation"]
            jsonld["reviewRating"]["ratingValue"] = RATING.get(cached["verdict"], 3)
            jsonld["reviewRating"]["alternateName"] = label
            jsonld["reviewRating"]["ratingExplanation"] = cached["tldr"] or cached["explanation"]
    # The front page carries the work instead of describing it: real counts
    # and the most recent verdicts with the evidence behind each. Both are
    # best-effort; an empty database must still render a usable page.
    try:
        ledger = db.ledger()
        latest = [dict(v, evidence=evidence.snapshot(v["studies"], v["cited"]),
                       shelf=evidence.shelf(v["studies"], v["cited"]))
                  for v in map(_listed, db.recent_verdicts(10))]
    except sqlite3.Error as e:
        app.logger.warning("Could not build the front page ledger: %s", e)
        ledger, latest = None, []
    return render_template("index.html", preview=preview, base_url=_base_url(),
                           result=cached, jsonld=jsonld, labels=VERDICT_LABELS,
                           ledger=ledger, latest=latest,
                           example=None if cached else _trust_example(),
                           provider=verdict.provider_label(), checks_page=checks_page,
                           today=datetime.now().strftime("%A, %B %-d, %Y"))


@app.route("/")
def index():
    """The checker first, then the ledger and recent checks."""
    return _render_home()


@app.route("/checks")
def checks():
    """
    The checker on its own, with no footage above it: the page the home
    page used to be. Same ledger, same recent checks, same everything, for
    anyone who wants the tool and not the film.
    """
    return _render_home(checks_page=True)


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
    # Static pages only. A typed claim is something a person wrote; listing
    # it here would publish it to every crawler, which the privacy page
    # does not say and the product does not want.
    urls = [(f"{base}/", "1.0"), (f"{base}/checks", "0.8"), (f"{base}/about", "0.5"),
            (f"{base}/method", "0.5"), (f"{base}/corrections", "0.4"),
            (f"{base}/privacy", "0.3")]
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
        f"- [Check a claim]({base}/): the checker, with how a check works below it",
        f"- [Check a claim, tool only]({base}/checks): the checker on its own",
        f"- [About]({base}/about): who makes Evident, what it does and what it does not do",
        f"- [Method]({base}/method): how a check works and the rules every verdict must pass",
        f"- [Corrections]({base}/corrections): report a wrong answer or get in touch",
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
        latest = [_listed(v) for v in db.recent_verdicts(5)]
    except sqlite3.Error:
        latest = []
    return render_template("404.html", latest=latest, labels=VERDICT_LABELS), 404


@app.errorhandler(405)
def method_not_allowed(_e):
    """A wrong verb is a dead end like a wrong path: say so, and never as a stack page."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "That is not something this address accepts."}), 405
    return not_found(_e)[0], 405


@app.errorhandler(500)
def server_error(_e):
    """
    The last line of defence for any page route: a plain page with a way
    out, never the framework's error page. The cause goes to the log (Flask
    has already logged it); nothing about it is shown. The API routes answer
    in JSON the front end can read.
    """
    if request.path.startswith("/api/"):
        return jsonify({"error": "Something went wrong on our side. Try again in a moment."}), 500
    body = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex"><title>Something went wrong · Evident</title>'
            '<link rel="stylesheet" href="/static/style.css"></head><body class="page-doc">'
            '<main class="wrap doc"><h1 class="hero">Something went wrong.</h1>'
            '<p class="lede">That was on our side, not yours. Try again in a moment.</p>'
            '<p><a class="link-btn" href="/">Back to Evident</a></p>'
            '<p class="fine">Not medical advice. This summarizes published research and can be wrong. '
            'For your own health, talk to a clinician.</p></main></body></html>')
    return Response(body, status=500, mimetype="text/html")


@app.route("/favicon.ico")
def favicon():
    """Browsers and crawlers ask for this path whatever the page's <link> says."""
    resp = send_from_directory(app.static_folder, "icons/icon-192.png", mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# Who runs Evident. Each is shown on /about only when set, so the page never
# carries a placeholder and never says anything the owner has not decided.
ABOUT = {
    "owner": os.environ.get("EVIDENT_OWNER", "").strip() or None,
    "who": os.environ.get("EVIDENT_WHO", "").strip() or None,
    "country": os.environ.get("EVIDENT_COUNTRY", "").strip() or None,
    "funding": os.environ.get("EVIDENT_FUNDING", "").strip() or None,
    "contact": os.environ.get("EVIDENT_CONTACT", "").strip() or None,
}


@app.route("/about")
def about():
    return render_template("about.html", base_url=_base_url(), provider=verdict.provider_label(),
                           about=ABOUT, labels=VERDICT_LABELS)


# The method page's own version. Bump both when a rule it describes changes.
METHOD_VERSION = "1"
METHOD_CHANGED = "15 November 2026"


@app.route("/method")
def method():
    return render_template("method.html", base_url=_base_url(), provider=verdict.provider_label(),
                           labels=VERDICT_LABELS, version=METHOD_VERSION, changed=METHOD_CHANGED,
                           max_studies=MAX_STUDIES)


@app.route("/corrections")
def corrections():
    return render_template("corrections.html", base_url=_base_url())


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", base_url=_base_url(), provider=verdict.provider_label())


_INFO_PAGES = {
    "about": ("About", "Who makes Evident, what it does and what it does not do."),
    "method": ("Methodology", "How a check works and the rules every verdict must pass."),
    "corrections": ("Corrections and contact", "Report a wrong answer or get in touch."),
}


def _info(slug):
    title, desc = _INFO_PAGES[slug]
    return render_template("info.html", slug=slug, title=title, desc=desc,
                           base_url=_base_url(), provider=verdict.provider_label())


@app.route("/about")
def about():
    return _info("about")


@app.route("/method")
def method():
    return _info("method")


@app.route("/corrections")
def corrections():
    return _info("corrections")


@app.route("/og/<path:key>.png")
def og_image(key):
    cached = db.get_cached_by_key(key)
    if cached:
        receipt = og.strongest_cited(cached["studies"], cached["cited_studies"])
        # The card's short answer is the page's, with the page's certainty.
        # If the gate cannot run on an old or odd row, the card still renders,
        # with the stored short answer: a link preview must never be a 500.
        try:
            page = _check_response(
                cached["claim_text"], cached.get("search_query") or "", cached["verdict"],
                cached["tldr"], cached["explanation"], cached["cited_studies"], cached["studies"],
                _base_url(), cached_at=cached["cached_at"], still_open=cached.get("still_open", ""),
                breakdown=cached.get("breakdown"))
            short, stamp = page["tldr"], page["verdict"]
        except Exception as e:  # noqa: BLE001
            app.logger.warning("Card for %s fell back to the stored short answer: %s", key, type(e).__name__)
            short = cached.get("tldr") or ""
            # The stored stamp, but never "true" without the cap having run.
            stamp = "complicated" if cached["verdict"] == "true" else cached["verdict"]
        png = og.render_card(cached["claim_text"], stamp, short, len(cached["studies"]), receipt)
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


def _deployed_commit():
    """Short sha of the running code, so a deploy can be checked from outside.

    Render sets RENDER_GIT_COMMIT (the full sha); locally, ask git once.
    Any failure gives "unknown". Never raises."""
    try:
        sha = os.environ.get("RENDER_GIT_COMMIT", "").strip()
        if sha:
            return sha[:7]
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.strip()
        return out[:7] or "unknown"
    except Exception:
        return "unknown"


# Computed once at import, never per request.
DEPLOYED_COMMIT = _deployed_commit()


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "commit": DEPLOYED_COMMIT, "store": db.store_status()})


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
        # The spine's cloth on the shelf: who or what the study was run on,
        # and whether it counts at all. Height is the tier; this is the rest.
        "kind": evidence.kind(s),
        # The word lettered on that spine.
        "spine": evidence.spine(s),
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
        # True for a record that is background only, never counted as used:
        # an animal or lab study read for a claim about people, an opinion
        # piece, a buying guide. Set in verdict.py. `background_why` says
        # which, in the words the study list uses.
        "background": bool(s.get("background")),
        "background_why": evidence.background_why(s),
    }


def _next_steps_safe(claim, query_used, studies, cited):
    """Never let the empty-state helper take down a check that worked."""
    try:
        return nextsteps.build(claim, query_used, studies, cited, db.all_claims())
    except (sqlite3.Error, ValueError, TypeError) as e:
        app.logger.warning("Could not build next steps: %s", e)
        return None


def _original_check_date(claim, cached_at):
    """
    The date this claim was first checked, when that is a different day from
    the saved answer's own (a re-check replaces the saved date). From the
    check log only; None when the log has nothing, either date is unreadable,
    or the log's date is not earlier. Nothing is guessed.
    """
    try:
        first = db.first_fresh_check(db.normalize_claim(claim))
        a = datetime.fromisoformat(first) if first else None
        b = datetime.fromisoformat(cached_at) if cached_at else None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    if a is None or b is None or a.date() >= b.date():
        return None
    return first


def _check_response(claim, search_query, verdict_value, tldr, explanation, cited_studies, studies,
                    base_url, cached_at=None, broadened=False, still_open="", breakdown=None,
                    typed=None):
    cited = set(cited_studies or [])
    outcome = _outcome_group(search_query)
    # Every sentence that cites a study is held to the studies it cites, on
    # the way out, so a cached check is held to it too: none may lean on a
    # study the verdict did not rely on, name a group, a figure or a count
    # its studies do not give. "Still open" is never touched.
    relied = {i + 1 for i, s in enumerate(studies or []) if s.get("pmid") in cited}
    if relied:
        explanation = breakdown_mod.held_to_sources(explanation, studies, relied, claim=claim) or tldr
        breakdown = breakdown_mod.hold_breakdown(breakdown, studies, relied, claim=claim)
    # One certainty for the whole page, settled on the way out so a check
    # cached before the rule existed gets it too. The evidence line is never
    # surer than the least sure thing the page says, and a certainty phrase
    # in the prose that is surer than the line is rewritten down to it.
    stated = evidence.stated_certainty(tldr, explanation, *breakdown_mod.prose_of(breakdown))
    evidence_line = evidence.evidence_line(
        verdict_value, studies, cited, claim,
        ((breakdown or {}).get("effect") or {}).get("label"),
        breakdown_mod.absolute_claim(claim),
        bool(breakdown_mod._COMPARATIVE_CLAIM.search(claim or "")),
        outcome, stated=stated)
    # The stamp follows the same cap a fresh check gets in verdict.py: a
    # "true" the evidence cannot carry (the lead review rates itself low, or
    # only small and pilot trials) is not shown as likely true, and nor is a
    # "true" over a line that says low certainty. A check cached before the
    # cap existed, or before its line was scoped, gets it here.
    if _capped_true(verdict_value, claim, studies, cited, outcome, evidence_line):
        verdict_value = "complicated"
        if not re.search(r"\bcertainty\b[^.]*\blow\b", tldr or "", re.IGNORECASE):
            leaned = f"{(tldr or '').rstrip().rstrip('.')}, but the certainty of that evidence is low."
            tldr = (leaned if tldr and len(leaned) <= verdict.TLDR_MAX
                    and not breakdown_mod.stamp_conflict(verdict_value, leaned, "", claim)
                    else verdict.LOW_CERTAINTY_TLDR)
        evidence_line = evidence.evidence_line(
            verdict_value, studies, cited, claim,
            ((breakdown or {}).get("effect") or {}).get("label"),
            breakdown_mod.absolute_claim(claim),
            bool(breakdown_mod._COMPARATIVE_CLAIM.search(claim or "")),
            outcome, stated=stated)
    level = evidence.line_certainty(evidence_line)
    if level:
        tldr = evidence.cap_certainty(tldr, level)
        explanation = evidence.cap_certainty(explanation, level)
        breakdown = breakdown_mod.map_prose(breakdown, lambda t: evidence.cap_certainty(t, level))
    if breakdown:
        # Added on the way out rather than stored, so a check cached before
        # these existed gets them too: each relied-on study's conclusion in
        # its authors' words, and the line saying what the fold holds.
        breakdown = {**breakdown, "concluded": evidence.conclusions(studies, cited)}
        # "Rests on N" is the number of studies the verdict used, read from
        # the same cited-study mapping that marks each study "used". A count
        # stored inside an old breakdown can disagree with it, so it is
        # recomputed here and never trusted. Nothing stored is rewritten.
        if "rests_on" in breakdown:
            breakdown["rests_on"] = sum(1 for s in studies if s["pmid"] in cited)
        breakdown["contents"] = breakdown_mod.contents(breakdown)
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
        # The first real check of this claim, only when it was on an earlier
        # day than cached_at (the last re-check). Otherwise null.
        "first_checked_at": _original_check_date(claim, cached_at) if cached_at else None,
        "share_url": f"{base_url}/?q={quote(claim)}",
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/?term={quote(search_query or claim)}",
        # Counts the reader can check us on, computed from PubMed's own
        # metadata rather than from anything the model said.
        "evidence": evidence.snapshot(studies, cited),
        # What that evidence shows about the claim, for the line under the
        # chart: "Strong evidence of a partial effect, not a cure". The level
        # word alone described the designs, not the claim.
        "evidence_line": evidence_line,
        # Present only when the check came back without usable evidence, so
        # a dead end offers somewhere to go instead of stopping.
        "next_steps": _next_steps_safe(claim, search_query, studies, cited),
        "studies": [_study_payload(s, cited) for s in studies],
    }


def _review_line(cached_at, studies, cited) -> str:
    """The dated line under the verdict: when the check ran, and the years of
    the studies it relied on. "Checked 14 November 2026, from studies
    published 2019 to 2025." Built here once, so the page a shared link
    renders and a streamed check say the same thing."""
    try:
        when = datetime.fromisoformat(cached_at) if cached_at else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        when = datetime.now(timezone.utc)
    line = f"Checked {when.day} {when.strftime('%B %Y')}"
    used = [s for s in studies or [] if s.get("pmid") in cited]
    years = sorted({int(y) for y in (str(s.get("year") or "")[:4] for s in used) if y.isdigit()})
    if not years:
        return line + "."
    if len(used) == 1:
        return f"{line}, from a study published in {years[0]}."
    if len(years) == 1:
        return f"{line}, from studies published in {years[0]}."
    return f"{line}, from studies published {years[0]} to {years[-1]}."


def _outcome_group(search_query) -> str:
    """The query's outcome group, the second of its ANDed groups, or ""."""
    groups = pubmed.split_and(search_query or "")
    return groups[1] if len(groups) > 1 else ""


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
        # The same claim always runs the same search: a saved one is reused,
        # and only a claim never seen before asks the model for one.
        saved = db.get_cached_query(claim, verdict.QUERY_PROMPT_VERSION)
        if saved:
            search_query, surrogate, understood = saved
        else:
            search_query, surrogate, understood = verdict.extract_search_terms(claim)
            # A model that answered nothing leaves the claim itself as the
            # query. That is a stopgap, not a search worth keeping.
            if search_query.strip().lower() != claim.strip().lower():
                db.put_cached_query(claim, verdict.QUERY_PROMPT_VERSION,
                                    search_query, surrogate, understood)
                if understood != claim:
                    db.put_cached_query(understood, verdict.QUERY_PROMPT_VERSION,
                                        search_query, surrogate, understood)
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
            search_query, max_results=MAX_STUDIES, surrogate=surrogate)
        yield {"stage": "found", "count": len(studies), "query": query_used, "broadened": broadened}
        if studies:
            yield {"stage": "weigh"}
        result = verdict.weigh_evidence(claim, studies, query_used, surrogate)
    except RuntimeError as e:
        # Missing/misconfigured API key: the operator's problem, not the
        # reader's. The detail goes to the log; the page says what to do.
        app.logger.error("Check could not run: %s", e)
        yield {"stage": "error", "status": 500, "error": UNAVAILABLE_MESSAGE}
        return
    except verdict.ProviderError as e:
        status = {"auth": 500, "rate_limit": 429, "unavailable": 502}.get(e.kind, 502)
        suffix = " Wait a few seconds and try again." if e.kind == "rate_limit" else ""
        # A rejected key names .env and the provider's console: for the
        # operator's log, never for the page.
        shown = UNAVAILABLE_MESSAGE if e.kind == "auth" else e.detail + suffix
        if e.kind == "auth":
            app.logger.error("The AI provider rejected its key: %s", e.detail)
        ev = {"stage": "error", "status": status, "error": shown}
        if e.kind == "rate_limit":
            ev["retry_after"] = 20
        yield ev
        return
    except requests.RequestException:
        yield {"stage": "error", "status": 502,
               "error": "Couldn't reach PubMed right now. Try again in a minute."}
        return
    except Exception:  # noqa: BLE001 — last line of defence; never crash a check
        # The exception text is for the log. A reader gets the problem and a way out.
        app.logger.exception("Check failed")
        yield {"stage": "error", "status": 500,
               "error": "Something went wrong while checking this claim. Try again in a moment."}
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


# What a "Report a wrong answer" note starts with in the feedback table. The
# table has no kind column and gets none: a report is a thumbs-down whose
# note begins with this, so nothing about the schema or what is stored changes.
REPORT_PREFIX = "wrong answer: "
MAX_NOTE_CHARS = 280


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """
    Anonymous. A thumb, or a report that an answer is wrong, tied to the claim
    and never to the sender: no cookie, no account, and the rate limiter's
    in-memory key (the address) is never written down.
    """
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        payload = {}
    claim = str(payload.get("claim") or "").strip()[:MAX_CLAIM_CHARS]
    report = payload.get("report") is True
    if not claim or ("helpful" not in payload and not report):
        return jsonify({"error": "Say which claim this is about."}), 400
    allowed, _ = ratelimit.FEEDBACK.check(f"ip:{_client_ip()}")
    if not allowed:
        return jsonify({"ok": True})  # silently drop floods; nothing for the user to do
    verdict_value = payload.get("verdict")
    if verdict_value not in VERDICT_LABELS:
        verdict_value = None
    note = " ".join(str(payload.get("note") or "").split())[:MAX_NOTE_CHARS]
    if report:
        note = (REPORT_PREFIX + note).strip()
    try:
        db.add_feedback(claim, verdict_value, False if report else bool(payload.get("helpful")), note)
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
