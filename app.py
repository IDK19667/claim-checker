import copy
import functools
import gzip
import hashlib
import hmac
import logging
import json
import os
import re
import secrets
import subprocess
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import sqlite3
from html import escape
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()  # must run before verdict.py reads the provider keys

import requests
from flask import (Flask, abort, Response, g, make_response, jsonify, redirect, render_template, request,
                   send_from_directory)
from werkzeug.security import safe_join

import breakdown as breakdown_mod
import display
import db
import evidence
import og
import pubmed
import nextsteps
import ratelimit
import safety_lines
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
app.jinja_env.globals["source_groups"] = display.source_groups
app.jinja_env.globals["desk_stops"] = display.desk_stops
app.jinja_env.globals["desk_lines"] = display.desk_lines
app.jinja_env.globals["rests_by_design"] = display.rests_by_design
app.jinja_env.globals["desk_refs"] = display.desk_refs
app.jinja_env.globals["desk_named"] = display.desk_named
app.jinja_env.globals["safe_href"] = display.safe_href
app.jinja_env.globals["typed_note"] = display.typed_note
app.jinja_env.globals["typed_note_parts"] = display.typed_note_parts
app.jinja_env.filters["sentence_case"] = display.sentence_case
app.jinja_env.filters["with_time"] = display.with_time
# The one sentence on privacy that the footer and the top of /privacy both
# print, from here, so the two cannot drift. It says what /privacy says in
# full: the claim text is kept, nothing that identifies the person is, and
# the text goes to the AI provider the page names.
PRIVACY_LINE = "Claim text is stored without anything that identifies you and is sent to {provider} to check it."


def privacy_line(provider: str, after_colon: bool = False) -> str:
    """The privacy sentence for this provider. after_colon lowers its first
    letter, for the /privacy lede, where it runs on after "The short version:"."""
    line = PRIVACY_LINE.format(provider=provider)
    return line[0].lower() + line[1:] if after_colon else line


app.jinja_env.globals["privacy_line"] = privacy_line


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


# The model's SDK is imported on a check's first model call, and on a fresh
# process that import (google.genai, about a second of work here, more on a
# small host) sat inside the first check of every start, a free host's wake
# included. Now it is done once, in the background, after the first response
# of the process has gone out: never in the way of the page that woke the
# server, and usually done before the reader has typed a claim. It sends
# nothing anywhere and builds nothing: imports only. A check that comes
# first does the same work itself, as before.
_warm = {"started": False}
_warm_lock = threading.Lock()


def _warm_provider() -> None:
    """Import what the first model call imports: google.genai with its
    errors and httpx (the default provider), or anthropic. Imports only."""
    try:
        if verdict.provider() == "anthropic":
            import anthropic  # noqa: F401
        else:
            import httpx  # noqa: F401
            from google import genai  # noqa: F401
            from google.genai import errors  # noqa: F401
    except Exception as e:  # noqa: BLE001 — a check that needs it says what is wrong
        app.logger.info("The model SDK was not imported ahead: %s", type(e).__name__)


def _warm_once() -> None:
    with _warm_lock:
        if _warm["started"]:
            return
        _warm["started"] = True
    threading.Thread(target=_warm_provider, name="warm-provider", daemon=True).start()


@app.after_request
def _warm_after_first(response: Response) -> Response:
    if not _warm["started"]:
        response.call_on_close(_warm_once)
    return response


# Static files carry a stamp: every static address a page prints ends in
# ?v= and the first 10 hex digits of the file's sha256 (url_for adds it), and
# a request with the right stamp may be kept for a year, because a changed
# file gets a new address. Without a stamp, or with a wrong one, a file is
# served as before (no-cache, revalidated). Fonts are named in CSS, which
# cannot be stamped without a build step; their files never change (a
# changed font gets a new file name), so /static/fonts/ is long-cached as it
# is and never stamped (a stamped preload would not match the CSS address
# and the font would download twice). A stamp is worked out the first time
# an address is printed and again when the file changes on disk.
LONG_CACHE = "public, max-age=31536000, immutable"
_stamps: dict = {}


def static_stamp(filename: str) -> str | None:
    """First 10 hex digits of the static file's sha256, or None if there is no such file."""
    path = safe_join(app.static_folder, filename)
    try:
        st = os.stat(path) if path else None
    except OSError:
        st = None
    if st is None or not os.path.isfile(path):
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _stamps.get(filename)
    if hit and hit[0] == key:
        return hit[1]
    with open(path, "rb") as f:
        stamp = hashlib.sha256(f.read()).hexdigest()[:10]
    _stamps[filename] = (key, stamp)
    return stamp


def _stampable(filename: str) -> bool:
    return not filename.startswith("fonts/")


@app.url_defaults
def _stamp_static(endpoint, values) -> None:
    if endpoint == "static" and "v" not in values and _stampable(values.get("filename", "")):
        stamp = static_stamp(values.get("filename", ""))
        if stamp:
            values["v"] = stamp


_STATIC_IN_TEXT = re.compile(r'"/static/([^"?#]+)"')


def stamped_text(text: str) -> str:
    """Every "/static/..." string in a served text file (the manifest, sw.js) with its stamp."""
    def one(m):
        name = m.group(1)
        stamp = static_stamp(name) if _stampable(name) else None
        return f'"/static/{name}?v={stamp}"' if stamp else m.group(0)
    return _STATIC_IN_TEXT.sub(one, text)


@app.after_request
def _static_cache(response: Response) -> Response:
    if request.endpoint != "static" or response.status_code not in (200, 304):
        return response
    name = (request.view_args or {}).get("filename", "")
    if not _stampable(name) or (request.args.get("v") and request.args.get("v") == static_stamp(name)):
        response.headers["Cache-Control"] = LONG_CACHE
    return response


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
    cited = display.relied_pmids(cached.get("cited_studies"), studies)
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


def _public_claims(rows, n, key="claim_text", indexable=False):
    """
    The one screen every public list passes (home's latest checks, the 404
    page, trending, llms.txt, type-ahead; tests list each address): the first
    n rows whose claim may be shown in a public list (display.public_claim: a
    curated example, or a claim that does not look personal and whose every
    word is vouched for by the closed list or the studies it read).
    indexable=True also needs it to be a curated example, for the machine index.
    A row that carries its studies is judged on them; a light row (trending,
    type-ahead, llms.txt) by its saved answer, remembered per row
    (_public_views). Callers fetch about three times n so a filtered list
    stays near its old size.
    """
    rows = list(rows)
    light = [r for r in rows if "studies" not in r and not (indexable and not display.indexable_claim(r[key]))]
    views = _public_views(light, key) if light else {}
    out = []
    for r in rows:
        if indexable and not display.indexable_claim(r[key]):
            continue
        if "studies" in r:
            ok = display.public_claim(r[key], r["studies"])
        else:
            view = views.get(r.get("claim_key") or db.normalize_claim(r[key]))
            ok = bool(view and view[1])
            # A light row carries its stored stamp: shown as the page shows it.
            r = dict(r, verdict=view[2]) if ok and "verdict" in r else r
        if ok:
            out.append(r)
    return out[:n]


# What the one screen decided for each saved row a light list showed, by
# claim key: (saved date, public, the verdict its page shows). A row saved again has a new date and is
# judged again. Capped, oldest out first, so memory stays bounded; a list
# call reads only the rows it has not seen (one query, db.list_rows).
PUBLIC_MEMO_MAX = 5000
_public_memo: "OrderedDict[str, tuple]" = OrderedDict()
_public_memo_lock = threading.Lock()


def _public_views(rows, key="claim_text") -> dict:
    keys = {}
    for r in rows:
        k = r.get("claim_key") or db.normalize_claim(r[key])
        keys[k] = r.get("created_at")
    found, need = {}, []
    with _public_memo_lock:
        for k, at in keys.items():
            hit = _public_memo.get(k)
            if hit and (at is None or hit[0] == at):
                _public_memo.move_to_end(k)
                found[k] = hit
            else:
                need.append(k)
    if need:
        try:
            fetched = db.list_rows(need)
        except sqlite3.Error as e:
            app.logger.warning("Could not read rows for a public list: %s", e)
            fetched = []
        with _public_memo_lock:
            for row in fetched:
                view = (row["created_at"], display.public_claim(row["claim_text"], row["studies"]),
                        _shown_verdict(row))
                _public_memo[row["claim_key"]] = view
                found[row["claim_key"]] = view
            while len(_public_memo) > PUBLIC_MEMO_MAX:
                _public_memo.popitem(last=False)
    return found


LIST_OVERFETCH = 3


# The real check the trust section offers as proof. It is read from the
# saved answers by its key, so the proof is always the answer the site serves.
EXAMPLE_CLAIM = "Apple cider vinegar cures diabetes"


def _as_shown(studies) -> list:
    """`studies` as a reader is shown them: a withdrawn paper (retracted, or
    a journal notice) is set aside for that reason, whatever else is true of
    it, so its off-topic reason is not shown or counted beside it. Each
    paper is counted once, as background. Copies; the records themselves,
    and every reading made from them, are left as they are."""
    return [dict(s, off_topic=None, background=evidence.withdrawn(s))
            if isinstance(s, dict) and evidence.withdrawn(s) and s.get("off_topic") else s
            for s in studies or []]


def _list_entries(rows) -> list[dict]:
    """
    Saved answers as a public list shows them: the stamp the result page
    would show (_listed) and the evidence behind it. The Latest list and the
    trust section's example both go through here, so the two cannot disagree.
    """
    return [dict(v, evidence=evidence.snapshot(_as_shown(v["studies"]), v["cited"]),
                 shelf=evidence.shelf(_as_shown(v["studies"]), v["cited"]))
            for v in map(_listed, rows)]


def _latest(n: int) -> list[dict]:
    """
    The newest n saved answers a public list may show, as recent_verdicts
    reads them. Only a whole row (db.whole: its short answer and its
    still-open line, the same test the example and a result page use) is a
    finished answer; one that is not is never shown as one. Rows are fetched
    in growing pages until the list is full or the table runs out.
    """
    limit = n * LIST_OVERFETCH
    while True:
        rows = db.recent_verdicts(limit)
        whole = [r for r in rows if db.whole(r)]
        out = _one_per_question(_public_claims(whole, len(whole)), verdict_of=_shown_verdict)[:n]
        if len(out) >= n or len(rows) < limit:
            return out
        limit *= 2


# The home page's two lists (the latest answers and the trust example) are
# kept in this process's memory for HOME_LIST_SECONDS, so a plain home visit
# inside that window makes no database round trip (on the live site each one
# is a request to the remote database). A save in this process drops the
# copy (db.after_save), so the reader who just checked sees their answer. A
# refresh that fails serves the last good copy and never raises to the reader.
HOME_LIST_SECONDS = float(os.environ.get("HOME_LIST_SECONDS", "60"))
_home_lists: dict = {}
_home_lock = threading.Lock()


def _home_list(name: str, fetch):
    """fetch()'s value, from memory while it is younger than HOME_LIST_SECONDS."""
    now = time.monotonic()
    with _home_lock:
        hit = _home_lists.get(name)
    if hit and now - hit[0] < HOME_LIST_SECONDS:
        return hit[1]
    try:
        value = fetch()
    except Exception:  # noqa: BLE001
        if hit:
            app.logger.warning("Home list %s could not be refreshed; serving the last good copy", name)
            return hit[1]
        raise
    # A remote store that failed answers from the local file instead (db._routed),
    # which is no refresh: keep the last good copy then.
    if hit and db.store_status() == "turso-failing":
        return hit[1]
    with _home_lock:
        _home_lists[name] = (time.monotonic(), value)
    return value


@db.after_save
def _drop_home_lists() -> None:
    with _home_lock:
        _home_lists.clear()


# Words a public list ignores when it asks whether two rows are one claim in
# another order: suggest's filler words, but never a word that gives a
# direction or a comparison, nor a negation (those are not in _STOP anyway).
_LIST_FILLER = suggest_mod._STOP - {"than", "over", "under", "into", "from", "before", "after"}
_LIST_ORDERED = frozenset({"than", "vs", "versus"})


def _list_fold(w: str) -> str:
    if len(w) > 4 and w.endswith("es"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


@functools.lru_cache(maxsize=8192)
def _list_key(claim: str) -> tuple:
    """(claim_fold key, the claim's words as a set, its hedges, first word,
    last word, whether word order carries meaning), for _one_per_question."""
    fold = db.claim_fold(claim)
    words = [_list_fold(w) for w in fold if not w.startswith("~") and w not in _LIST_FILLER]
    hedges = tuple(sorted(w for w in fold if w.startswith("~")))
    if not words:
        return fold, None, hedges, None, None, True
    return fold, frozenset(words), hedges, words[0], words[-1], bool(_LIST_ORDERED & set(words))


def _one_per_question(rows: list[dict], key: str = "claim_text", verdict_of=None) -> list[dict]:
    """
    Each question once, newest first; the newest row stands, under its own
    saved wording. Two rows are one question when their db.claim_fold keys
    are equal ("Does creatine cause hair loss" and "Creatine causes hair
    loss"), or when they show the same verdict (verdict_of, the stamp as the
    page shows it) and, after claim_fold and the filler words, hold the same
    set of words with a final s or es folded, the same hedge, no "than" or
    "versus", and the same first or last word ("Calcium and vitamin D
    strengthen bones" and "Vitamin D and calcium strengthen bones"). So a
    claim and its opposite, two doses, two groups of people and "stress causes
    acne" against "acne causes stress" all stay. A row whose key is empty is
    never merged with another. Display only: no saved row or key changes.
    """
    verdict_of = verdict_of or (lambda r: r.get("verdict"))
    seen, groups, out = set(), {}, []
    for r in rows:
        fold, words, hedges, first, last, ordered = _list_key(r[key])
        if fold and fold in seen:
            continue
        group = (words, hedges, verdict_of(r)) if words and not ordered else None
        if group and any(f == first or l_ == last for f, l_ in groups.get(group, ())):
            continue
        seen.add(fold)
        if group:
            groups.setdefault(group, []).append((first, last))
        out.append(r)
    return out


def _trust_example():
    """
    The trust section's real example, {claim, verdict, tldr, checked, read,
    href}, from the saved answer for EXAMPLE_CLAIM. None when that answer is
    missing or incomplete (no studies, no short answer, no still-open line, an
    unknown verdict or an unreadable date): the section then renders without
    it rather than with an invented example.
    """
    try:
        row = _home_list("example", lambda: db.get_cached_by_key(db.normalize_claim(EXAMPLE_CLAIM)))
        if not row or not row["studies"] or row["verdict"] not in VERDICT_LABELS \
                or not row["tldr"].strip() or not row["still_open"].strip():
            return None
        d = display.as_instant(row["cached_at"])
        if d is None:
            raise ValueError("unreadable date")
        v = _list_entries([{"claim_text": row["claim_text"], "search_query": row["search_query"],
                            "verdict": row["verdict"], "tldr": row["tldr"], "still_open": row["still_open"],
                            "created_at": row["cached_at"], "studies": row["studies"],
                            "cited": row["cited_studies"]}])[0]
    except (sqlite3.Error, ValueError, KeyError, TypeError, AttributeError) as e:
        app.logger.warning("Could not build the trust example: %s", e)
        return None
    return {"claim": v["claim_text"], "verdict": v["verdict"], "tldr": v["tldr"],
            "checked": display.home_day(d), "checked_iso": d.isoformat(), "read": v["evidence"]["read"],
            "href": "/?q=" + quote(v["claim_text"])}


def _render_home():
    claim = _claim_from_query_args()
    preview = None
    cached = None
    jsonld = None
    noindex = False
    typed = None
    if claim:
        cached = db.get_cached_by_key(db.normalize_claim(claim))
        # A link in other words than a saved claim's (a shared or typed
        # address) reaches the same saved answer the check would give, so
        # the page is complete before any script runs: the saved answer,
        # under its saved wording, with the note naming the reader's words.
        if not cached:
            same = _equivalent(claim)
            cached = db.get_cached_by_key(db.normalize_claim(same)) if same else None
            typed = claim if cached else None
        if cached:
            label = VERDICT_LABELS.get(cached["verdict"], cached["verdict"])
            share_url = f"{_base_url()}/?q={quote(cached['claim_text'])}"
            preview = {
                "title": f"{label}: “{cached['claim_text']}”",
                "description": cached["tldr"] or cached["explanation"],
                "image": f"{_base_url()}/og/{quote(db.normalize_claim(cached['claim_text'] if typed else claim), safe='')}.png",
                "url": share_url,
            }
            jsonld = _claim_review_jsonld(cached, share_url)
            # Indexable only when the claim is one of the site's own examples, has no
            # personal details and the saved record is sound. Anything else is noindex.
            noindex = not display.indexable_claim(cached["claim_text"]) or bool(display.saved_record_problems(
                cached["claim_text"], cached["verdict"], cached["cited_studies"], cached["studies"],
                cached.get("breakdown"), cached.get("search_query"), cached.get("cached_at")))
            cached = _check_response(
                cached["claim_text"], cached.get("search_query") or "", cached["verdict"],
                cached["tldr"], cached["explanation"], cached["cited_studies"], cached["studies"],
                _base_url(), cached_at=cached["cached_at"], still_open=cached.get("still_open", ""),
                breakdown=cached.get("breakdown"), typed=typed, typed_reason="wording" if typed else None)
            # The link preview and the markup say what the page says, with
            # the page's one certainty and stamp (both can be capped above).
            label = VERDICT_LABELS.get(cached["verdict"], cached["verdict"])
            preview.update(display.result_meta(cached["claim"], label, cached["tldr"] or cached["explanation"]))
            jsonld["reviewRating"]["ratingValue"] = RATING.get(cached["verdict"], 3)
            jsonld["reviewRating"]["alternateName"] = label
            jsonld["reviewRating"]["ratingExplanation"] = cached["tldr"] or cached["explanation"]
    # The front page carries the work instead of describing it: the most
    # recent verdicts with the evidence behind each. Best-effort; an empty
    # database must still render a usable page.
    try:
        latest = _home_list("latest", lambda: _list_entries(_latest(10)))
    except sqlite3.Error as e:
        app.logger.warning("Could not build the front page's latest checks: %s", e)
        latest = []
    db.count_event("view.result" if cached else "view.home")
    return render_template("index.html", preview=preview, base_url=_base_url(),
                           result=cached, jsonld=jsonld, labels=VERDICT_LABELS, noindex=noindex,
                           latest=latest,
                           example=None if cached else _trust_example(),
                           provider=verdict.provider_label(), on_gemini=_on_gemini(),
                           today=datetime.now().strftime("%A, %B %-d, %Y"))


@app.route("/")
def index():
    """The checker first, then the recent checks."""
    return _render_home()


@app.route("/checks")
def checks():
    """
    The checker on its own once had this address. The home page is that page
    now, so old links land there, with any ?q= they carry.
    """
    qs = request.query_string.decode("utf-8", "replace")
    return redirect("/" + ("?" + qs if qs else ""), code=301)


@app.route("/flight")
def flight_redirect():
    """The fly-through moved to the home page. Old links keep working."""
    return redirect("/", code=301)


# Paths no crawler needs. Repeated in every group below: a crawler that has a
# group of its own reads only that group and ignores the one for "*".
_ROBOTS_DISALLOW = ["Disallow: /api/", "Disallow: /stats"]


@app.route("/robots.txt")
def robots():
    lines = ["User-agent: *", "Allow: /", *_ROBOTS_DISALLOW, ""]
    # The verdicts are meant to be quotable. Say so rather than leaving
    # the AI crawlers to guess from silence.
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"):
        lines += [f"User-agent: {bot}", "Allow: /", *_ROBOTS_DISALLOW]
    lines += ["", f"Sitemap: {_base_url()}/sitemap.xml"]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


SITEMAP_RESULTS_MAX = 500
SITEMAP_SCAN_MAX = 50000
_sitemap_memo = {"stamp": None, "paths": []}


def _sitemap_result_paths() -> list:
    """
    /?q= paths of the saved answers whose claim is one of the curated examples,
    has no personal details and passes the saved-record check, newest first.
    Built from every row, so it is remembered until the table changes (its row
    count or newest save date), not rebuilt on each crawl.
    """
    try:
        stamp = db.saved_stamp()
        if stamp == _sitemap_memo["stamp"]:
            return _sitemap_memo["paths"]
        paths = []
        # Scan far more rows than the sitemap can hold: only the few curated claims
        # qualify, and they must be found however many typed claims were saved after them.
        for r in db.saved_records(SITEMAP_SCAN_MAX):
            if not display.indexable_claim(r["claim_text"]):
                continue
            if len(paths) >= SITEMAP_RESULTS_MAX:
                break
            try:
                cited, studies = json.loads(r["cited_json"] or "[]"), json.loads(r["studies_json"] or "[]")
                bd = json.loads(r["breakdown_json"]) if r["breakdown_json"] else None
            except (ValueError, TypeError):
                continue
            if display.saved_record_problems(r["claim_text"], r["verdict"], cited, studies, bd,
                                             r["search_query"], r["created_at"]):
                continue
            paths.append((f"/?q={quote(r['claim_text'])}", (r["created_at"] or "")[:10]))
        _sitemap_memo.update(stamp=stamp, paths=paths)
        return paths
    except Exception as e:  # noqa: BLE001 - the sitemap must never be a 500
        app.logger.warning("Sitemap results left out: %s", type(e).__name__)
        return []


@app.route("/sitemap.xml")
def sitemap():
    base = _base_url()
    # The pages, then the curated example claims whose saved answer is sound. Any
    # other result page is left out and is marked noindex on its own page.
    urls = [(f"{base}/", "1.0", ""), (f"{base}/about", "0.5", ""),
            (f"{base}/method", "0.5", ""), (f"{base}/corrections", "0.4", ""),
            (f"{base}/privacy", "0.3", "")]
    urls += [(base + path, "0.6", day) for path, day in _sitemap_result_paths()]
    body = "".join(
        f"<url><loc>{escape(u)}</loc>{f'<lastmod>{d}</lastmod>' if d else ''}<priority>{p}</priority></url>"
        for u, p, d in urls
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")
    return Response(xml, mimetype="application/xml")


@app.route("/llms.txt")
def llms_txt():
    """A plain description of this site for language models that read one."""
    base = _base_url()
    recent = _one_per_question(_public_claims(db.recent_cached(25 * LIST_OVERFETCH), 25 * LIST_OVERFETCH, indexable=True))[:25]
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
        latest = [_listed(v) for v in _latest(5)]
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
    in JSON the front end can read. It is counted for the day, as a total.
    """
    db.count_event("error.server")
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


# The method page's own version. Bump both when a rule it describes changes.
METHOD_VERSION = "2"
METHOD_CHANGED = "17 November 2026"

# Where removal and correction requests go. A plain mailto link, no form.
CONTACT_EMAIL = os.environ.get("EVIDENT_EMAIL", "hello@checkevident.com").strip() or None


@app.route("/privacy")
def privacy():
    db.count_event("view.info")
    return render_template("privacy.html", base_url=_base_url(), provider=verdict.provider_label(),
                           on_gemini=_on_gemini())


# ---- /stats: the day counts, behind a token --------------------------------------------

class _RedactToken(logging.Filter):
    """The token rides in the address, and some servers log the address. Whatever
    is logged by the web server says REDACTED where it was."""
    _pat = re.compile(r"([?&]token(?:=|%3[dD]))[^&\s\"']*", re.IGNORECASE)

    def filter(self, record):
        try:
            record.msg = self._pat.sub(r"\1REDACTED", str(record.getMessage()))
            record.args = ()
        except Exception:  # noqa: BLE001
            pass
        return True


for _name in ("werkzeug", "gunicorn.access"):
    logging.getLogger(_name).addFilter(_RedactToken())

STATS_DAYS = 30
STATS_LABELS = [
    ("Page views", [("view.home", "Home"), ("view.result", "A saved result"), ("view.info", "About, method, corrections, privacy")]),
    ("Checks", [("check.submitted", "Claims submitted"), ("check.completed", "Results completed")]),
    ("Shares", [("share.click", "Share button"), ("share.copy", "Copy link button")]),
    ("Errors the server saw", [("error.server", "Server errors"), ("provider.rate_limit", "AI provider: rate limited (429)"),
                               ("provider.unavailable", "AI provider: unavailable (503 or unreachable)"),
                               ("provider.auth", "AI provider: key rejected or missing"), ("provider.other", "AI provider: other error"),
                               ("pubmed.failure", "PubMed failures")]),
    ("Errors the page showed", [("client_error.invalid", "Claim could not be checked"), ("client_error.network", "Could not reach the server"),
                                ("client_error.limit", "Too many checks"), ("client_error.provider", "The checker could not finish")]),
]
_STATS_COLUMNS = [("Views", ("view.home", "view.result", "view.info")), ("Claims", ("check.submitted",)),
                  ("Results", ("check.completed",)), ("Shares", ("share.click", "share.copy")),
                  ("Errors", ("error.server", "provider.rate_limit", "provider.unavailable", "provider.auth",
                              "provider.other", "pubmed.failure"))]


@app.route("/stats")
def stats():
    """
    Per-day totals for the last 30 days, for the person who runs the site. It
    exists only when STATS_TOKEN is set; without it the page is a 404 like any
    other path. The token is compared in constant time, never logged, and a
    wrong one gets the same 404. Send it as `?token=...` or, to keep it out of
    proxy logs, as an `Authorization: Bearer <token>` header.
    """
    token = (os.environ.get("STATS_TOKEN") or "").strip()
    if not token:
        abort(404)
    given = request.args.get("token", "")
    _auth = request.headers.get("Authorization", "")
    if not given and _auth[:7].lower() == "bearer ":
        given = _auth[7:].strip()
    if not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
        # Only a failed attempt spends the limit, so behind a shared proxy
        # address bad guesses can never lock the owner out. Either way: 404.
        ratelimit.STATS.check(f"ip:{_client_ip()}")
        abort(404)
    db.flush_counts()
    rows = db.counts_since(STATS_DAYS)
    by_day: dict = {}
    totals: dict = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["name"]] = r["count"]
        totals[r["name"]] = totals.get(r["name"], 0) + r["count"]
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(STATS_DAYS)]
    table = [{"day": d, "cells": [sum(by_day.get(d, {}).get(n, 0) for n in names) for _, names in _STATS_COLUMNS]} for d in days]
    groups = [{"title": title, "rows": [{"label": label, "total": totals.get(name, 0), "today": by_day.get(days[0], {}).get(name, 0)}
                                        for name, label in items]} for title, items in STATS_LABELS]
    resp = make_response(render_template("stats.html", base_url=_base_url(), columns=[c for c, _ in _STATS_COLUMNS],
                                         table=table, groups=groups, days=STATS_DAYS))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


_INFO_PAGES = {
    "about": ("About", "Who makes Evident, what it does and what it does not do."),
    "method": ("Methodology", "How a check works and the rules every verdict must pass."),
    "corrections": ("Corrections and contact", "Report a wrong answer or get in touch."),
}


def _info(slug):
    title, desc = _INFO_PAGES[slug]
    db.count_event("view.info")
    return render_template("info.html", slug=slug, title=title, desc=desc,
                           base_url=_base_url(), provider=verdict.provider_label(),
                           labels=VERDICT_LABELS, max_studies=MAX_STUDIES,
                           version=METHOD_VERSION, changed=METHOD_CHANGED,
                           contact_email=CONTACT_EMAIL)


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
        receipt = og.strongest_cited(cached["studies"],
                                     display.relied_pmids(cached["cited_studies"], cached["studies"]))
        # The card's short answer is the page's, with the page's certainty.
        # If the gate cannot run on an old or odd row, the card still renders,
        # with the stored short answer: a link preview must never be a 500.
        try:
            page = _check_response(
                cached["claim_text"], cached.get("search_query") or "", cached["verdict"],
                cached["tldr"], cached["explanation"], cached["cited_studies"], cached["studies"],
                _base_url(), cached_at=cached["cached_at"], still_open=cached.get("still_open", ""),
                breakdown=cached.get("breakdown"))
            short, stamp, still = page["tldr"], page["verdict"], page.get("still_open") or ""
        except Exception as e:  # noqa: BLE001
            app.logger.warning("Card for %s fell back to the stored short answer: %s", key, type(e).__name__)
            short = cached.get("tldr") or ""
            # The stored stamp, but never "true" without the cap having run.
            stamp = "complicated" if cached["verdict"] == "true" else cached["verdict"]
            still = cached.get("still_open") or ""
        png = og.render_card(cached["claim_text"], stamp, short, len(cached["studies"]), receipt, still)
    else:
        # No claim (the home page, an info page, or a key with nothing saved):
        # the brand card, never a made-up claim with a verdict on it.
        png = og.render_brand()
    return Response(png, mimetype="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.route("/manifest.webmanifest")
def manifest():
    resp = _stamped_file("manifest.webmanifest", "application/manifest+json")
    return resp


def _stamped_file(name: str, mimetype: str) -> Response:
    """A static text file with its static addresses stamped, revalidated as before (ETag)."""
    with open(os.path.join(app.static_folder, name), encoding="utf-8") as f:
        body = stamped_text(f.read())
    resp = Response(body, mimetype=mimetype)
    resp.set_etag(hashlib.sha256(body.encode("utf-8")).hexdigest()[:16])
    resp.headers["Cache-Control"] = "no-cache"
    return resp.make_conditional(request)


@app.route("/sw.js")
def service_worker():
    # Served from the root so its scope covers the whole app.
    return _stamped_file("sw.js", "application/javascript")


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


def _contribution(s: dict, relied: bool) -> str:
    """E5: one sentence for "What this study contributes": its authors'
    conclusion cut to a sentence, else its design and who it was run in.
    Empty when the record gives nothing beyond the design's name ("A study.",
    "A meta-analysis." carry nothing: brief E3 N12)."""
    why_aside = s.get("off_topic") or evidence.withdrawn(s) or (
        evidence.background_why(s) if s.get("background") else "")
    if why_aside and not relied:
        return f"Set aside: {str(why_aside).rstrip('.')}."
    said = breakdown_mod.sentences(evidence.conclusion(s) or "")
    if said:
        one = said[0].strip()
        return one if one.endswith((".", "!", "?")) else one + "."
    design = (evidence.strongest_label(s.get("publication_types")) or "study").lower()
    who = evidence.population(s)
    a = "An" if design[:1] in "aeiou" else "A"
    # Never a population the record does not give, and never the design's
    # name alone.
    return f"{a} {design} in {who}." if who else ""


def _study_extra(s: dict, cited: set) -> dict:
    """The E1, E3 and E5 fields of one study, on the way out, so a saved
    answer gets them too. `indirect` is always false and its reason empty
    (brief E4 D1: the keyword mark left this release); the fields stay so
    page code keeps working and a later label read by the model can fill
    them."""
    return {
        "cites_retracted": evidence.cited_retracted(s) or None,
        "caution": evidence.retraction_caution(s) or None,
        "indirect": False,
        "indirect_reason": "",
        "contribution": _contribution(s, s.get("pmid") in cited),
    }


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
        "tier": evidence.shown_tier(s),
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
        # A retracted paper or a journal notice is background for any claim,
        # a saved record from before that rule included.
        "background": bool(s.get("background")) or bool(evidence.withdrawn(s)),
        "background_why": evidence.withdrawn(s) or evidence.background_why(s),
    }


def _flag_retractions(studies):
    """evidence.flag_retracted_citations with the one PubMed lookup. The
    lookup runs on the search pool and only returns ids; the flags are put
    on the studies here, on the request thread, and only from a result that
    came in time. A late result is thrown away and never touches them."""
    ids = evidence.retraction_lookup_ids(studies)
    found = _retraction_result(pubmed._ahead(pubmed.retracted_among, ids)) if ids else set()

    def lookup(_ids):
        if found is None:
            raise LookupError("the retraction lookup could not be made")
        return found
    return evidence.flag_retracted_citations(studies, lookup if ids else None)


# How long a check waits for that lookup before going on without it: the
# one constant, pubmed.RETRACTION_LOOKUP_SECONDS (4), for everything.
RETRACTION_WAIT = pubmed.RETRACTION_LOOKUP_SECONDS


def _retraction_result(future):
    """The retracted ids the lookup found, or None when it failed or ran
    late. A lookup that fails or runs late never stops the check."""
    try:
        return set(future.result(timeout=RETRACTION_WAIT) or ())
    except Exception:  # noqa: BLE001 — the check goes on without it
        app.logger.warning("The retraction lookup could not be made")
        return None


def _next_steps_safe(claim, query_used, studies, cited):
    """Never let the empty-state helper take down a check that worked."""
    # nextsteps.build answers None whenever the verdict rests on studies,
    # so the saved claims (500 rows) are read only when it will use them.
    if studies and cited:
        return None
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


def _from_guidance(tldr, claim) -> bool:
    """A stamp set by verdict.follow_guidelines (verdict.from_guidance: the
    lead, on a claim guidance is read for). /method says the verdict follows
    that guidance, so no output-time cap may overrule it."""
    return verdict.from_guidance(tldr, claim)


def _on_gemini() -> bool:
    """Whether checks go to Gemini, whose free tier terms the home page and
    the privacy page warn about. With no key set the warning stays: it is
    the cautious reading, and the site cannot run checks then anyway."""
    try:
        return verdict.provider() == "gemini"
    except RuntimeError:
        return True


def _readings(studies, cited, search_query) -> list[set]:
    """The studies used, and, when code set any aside as measuring another
    outcome, the same with those put back (verdict.set_aside). A cap or a
    lower certainty either reading earns applies to the page."""
    groups = pubmed.split_and(search_query or "")
    aside = set(verdict.set_aside(
        studies or [], {s.get("pmid"): s.get("off_topic") for s in studies or [] if isinstance(s, dict)},
        groups[1] if len(groups) > 1 else "", "", groups[0] if groups else "")) - set(cited)
    return [set(cited), set(cited) | aside] if cited and aside else [set(cited)]


def _capped_true(verdict_value, claim, studies, cited, outcome, tldr="") -> bool:
    """Whether a "true" has to be shown as "complicated": exactly the cap a
    fresh check gets in verdict.py (the lead review, read strictly, rates
    itself low or very low, or only small and pilot trials), and nothing
    looser. A line whose range starts low ("Low to moderate", "Low to high")
    is not a low rating: a review that rates several outcomes differently
    gives that range, and capping on it turned well-supported vaccine
    answers into "It's complicated, but the certainty is low"."""
    if verdict_value != "true" or not cited or _from_guidance(tldr, claim):
        return False
    comparative = bool(breakdown_mod._COMPARATIVE_CLAIM.search(claim or ""))
    return bool(evidence.certainty_cap(studies, cited, comparative, outcome))


def _nothing_relied(claim, verdict_value, tldr, studies=None):
    """(stamp, takeaway, unseated) for an answer none of whose citations is
    relied on, the one decision a result page and every list share. An
    answer the guidance rule set keeps its stamp only where a fresh check
    would let guidance alone rate the claim with nothing cited
    (verdict.guidance_may_rate): a citation set aside (a retracted paper)
    does not unseat it there. Anywhere else a rated answer becomes "Not
    enough evidence", a guidance answer with the guidance sentence after
    the untested takeaway, the shape a fresh check writes. An answer that
    lists no study at all gets the takeaway a fresh check writes when the
    search finds nothing, not one about studies that "don't test" it."""
    if verdict_value == "insufficient":
        return verdict_value, tldr, False
    guided = verdict.from_guidance(tldr, claim)
    if guided and verdict.guidance_may_rate(claim, verdict_value):
        return verdict_value, tldr, False
    lead = verdict.UNTESTED_TLDR if studies else verdict.NO_STUDIES_TLDR
    out = verdict.unrated_guidance_tldr(tldr, lead) if guided else lead
    if verdict.forgoes_treatment(claim) == "forgo":  # as a fresh check writes it
        out = f"{out} {verdict.FORGO_DOCTOR}"
    return "insufficient", out, True


def _shown_verdict(v: dict) -> str:
    """The verdict a saved answer's result page shows. Every public list
    reads a saved answer's stamp from here (or from _listed, which this is),
    so no list is ever surer than the page it links to."""
    return _listed(v)["verdict"]


def _listed(v: dict) -> dict:
    """A recent-checks entry with the stamp the page itself would show."""
    # Off-topic studies are not relied on here either (see _check_response),
    # so the shelf marks and the stamp match the result page.
    cited = display.relied_pmids(v.get("cited"), v.get("studies"))
    if not cited or set(v.get("cited") or []) - cited:
        v = dict(v, cited=[p for p in v.get("cited") or [] if p in cited])
        if not cited:
            stamp, tldr, unseated = _nothing_relied(v.get("claim_text"), v.get("verdict"), v.get("tldr"),
                                                    v.get("studies"))
            if unseated:
                return dict(v, verdict=stamp, **({"tldr": tldr} if "tldr" in v else {}))
    outcome = _outcome_group(v.get("search_query"))
    try:
        if any(_capped_true(v["verdict"], v["claim_text"], v["studies"], used, outcome, v.get("tldr"))
               for used in _readings(v["studies"], cited, v.get("search_query"))):
            return dict(v, verdict="complicated")
    except Exception:  # noqa: BLE001
        if v.get("verdict") == "true" and not _from_guidance(v.get("tldr"), v.get("claim_text")):
            return dict(v, verdict="complicated")
    return v


def _retracted_review_rules(explanation, breakdown, studies, cited):
    """E1c(iv), as brief E3 N1 has it: a flag never removes a sentence. When
    a review relied on cites a retracted study, one sentence naming it
    (evidence.retracted_review_sentence) stands once at the end of the
    explanation, and once at the end of each breakdown list ("says",
    "evidence") holding a sentence that cites such a review. Untouched when
    no relied-on study is flagged."""
    count = len(studies or [])
    flagged = [i + 1 for i, s in enumerate(studies or [])
               if evidence.cited_retracted(s) and s.get("pmid") in cited]
    if not flagged:
        return explanation, breakdown
    said = evidence.retracted_review_sentence(flagged)
    if said not in (explanation or ""):
        explanation = f"{(explanation or '').strip()} {said}".strip()
    if isinstance(breakdown, dict):
        bd = dict(breakdown)
        for key in ("says", "evidence"):
            items = bd.get(key)
            if not isinstance(items, list):
                continue
            named = sorted({n for x in items if isinstance(x, str)
                            for n in breakdown_mod.refs(x, count) if n in flagged})
            if named:
                line = evidence.retracted_review_sentence(named)
                if line not in items:
                    bd[key] = [*items, line]
        breakdown = bd
    return explanation, breakdown


def _statement_two(verdict_value, absolute, breakdown, studies, cited, outcome, queries) -> str:
    """Statement 2 (evidence.effect_statement), or "". Its sentence is the
    first effect sentence citing a relied-on study in people (the flag
    ignored), preferring one that names the claim's outcome family when that
    family is recognised; a sentence travels with its "That ..." translation,
    and is tidied like any model prose."""
    text = breakdown.get("effect_size") if isinstance(breakdown, dict) else ""
    if not absolute or verdict_value not in evidence.STATEMENT_TWO_VERDICTS or not isinstance(text, str):
        return ""
    count = len(studies or [])
    units = []
    for x in breakdown_mod.sentences(text):
        if x.startswith("That ") and units:
            units[-1] = f"{units[-1]} {x}"
        else:
            units.append(x)
    good = [u for u in units
            if any(studies[n - 1].get("pmid") in cited and evidence.human_evidence(studies[n - 1])
                   for n in breakdown_mod.refs(u, count))]
    if not good:
        return ""
    family = evidence.outcome_families(evidence.claim_outcome_terms(queries))
    pick = next((u for u in good if len(family) == 1 and evidence.names_family(u, family[0])), good[0])
    return evidence.effect_statement(verdict_value, absolute, verdict.tidy_prose(pick, 4000), studies, cited,
                                     outcome, cites=breakdown_mod.refs(pick, count))


def _check_response(claim, search_query, verdict_value, tldr, explanation, cited_studies, studies,
                    base_url, cached_at=None, broadened=False, still_open="", breakdown=None,
                    typed=None, typed_reason=None):
    # A study classified off topic is never relied on. A fresh check already
    # leaves such studies out of its cited list (verdict.py, the relevance
    # gate); an old or odd saved record may not, so the page drops them here
    # and every count, mark and line below reads the same set. If that leaves
    # a rated verdict resting on nothing, the rule a fresh check obeys
    # applies: it becomes "Not enough evidence".
    cited = display.relied_pmids(cited_studies, studies)
    # No sentence a reader sees as reasoning cites a study the page shows as
    # set aside (withdrawn, a journal notice, or off topic), whether or not
    # the answer listed it: a fresh check has already taken it out of the
    # cited list, so nothing below would hold its sentences to anything.
    # A sentence citing any such study goes whole, even one that also cites
    # a study relied on: taking only the set-aside number out would credit
    # its words to the other study. Text that cites none of them is left
    # exactly as it was.
    count = len(studies or [])
    aside = {i + 1 for i, s in enumerate(studies or []) if s.get("off_topic") or evidence.withdrawn(s)}
    if aside:
        unaside = lambda t: breakdown_mod.drop_any_citing(t, aside, count)
        explanation = unaside(explanation or "")
        if breakdown and any(unaside(t) != t for t in breakdown_mod.prose_of(breakdown)):
            breakdown = breakdown_mod.map_prose(breakdown, unaside)
    # Code never calls a figure not significant when its own interval or p
    # value rules that out (breakdown.say_nulls); a check saved before that
    # rule loses such a sentence here.
    explanation = breakdown_mod.unsaid_nulls(explanation or "")
    if breakdown and any(breakdown_mod.unsaid_nulls(t) != t for t in breakdown_mod.prose_of(breakdown)):
        breakdown = breakdown_mod.map_prose(breakdown, breakdown_mod.unsaid_nulls)
    # A rated verdict resting on nothing is "Not enough evidence", as on a
    # fresh check, save where guidance alone may rate it (_nothing_relied).
    if not cited or set(cited_studies or []) - cited:
        unseated = False
        if not cited:
            verdict_value, tldr, unseated = _nothing_relied(claim, verdict_value, tldr, studies)
        if unseated:
            explanation = (explanation.rstrip() + " None of the studies found directly "
                           "test this claim, so there isn't enough evidence to rate it.").strip()
            breakdown = None
    outcome = _outcome_group(search_query)
    # Every sentence that cites a study is held to the studies it cites, on
    # the way out, so a cached check is held to it too: none may lean on a
    # study the verdict did not rely on, name a group, a figure or a count
    # its studies do not give. "Still open" is never touched.
    relied = {i + 1 for i, s in enumerate(studies or []) if s.get("pmid") in cited}
    if relied:
        # An explanation the gate empties stays empty: the short answer is
        # already on the page, and repeating it would pass it off as reasoning.
        explanation = breakdown_mod.held_to_sources(explanation, studies, relied, claim=claim)
        breakdown = breakdown_mod.hold_breakdown(breakdown, studies, relied, claim=claim)
    # An answer the guidance rule set is never followed by words saying the
    # claim cannot be rated: the page says it rests on that guidance instead.
    if verdict.from_guidance(tldr, claim):
        explanation, still_open, breakdown = verdict.guidance_prose(explanation, still_open, breakdown)
        breakdown = verdict.guidance_effect(breakdown)
        # A guidance answer that relies on no study cites none for a finding:
        # "Studies show ... (Study 5)" four sentences before code says nothing
        # here tests the claim is a contradiction. Code's own sentences say
        # what the papers are; a sentence that cites no study stays.
        if not cited and verdict_value != "insufficient":
            kept = [s for s in breakdown_mod.sentences(explanation or "") if not breakdown_mod.refs(s, count)]
            if len(kept) != len(breakdown_mod.sentences(explanation or "")):
                explanation = " ".join(kept)
                if verdict.GUIDANCE_RESTS not in explanation:
                    explanation = f"{explanation} {verdict.GUIDANCE_RESTS}".strip()
            # The folded sections too: no part, paragraph or field cites a
            # study for a finding. A part whose answer goes is dropped, not
            # pointed at paragraphs of studies the answer does not rest on;
            # a section left with nothing is not rendered.
            if breakdown and any(breakdown_mod.refs(t, count) for t in breakdown_mod.prose_of(breakdown)):
                uncited = lambda t: breakdown_mod.drop_any_citing(t, range(1, count + 1), count)
                breakdown = breakdown_mod.map_prose(
                    {**breakdown, "parts": [p for p in breakdown.get("parts") or []
                                            if uncited(p.get("assessment") or "")]}, uncited)
    # One certainty for the whole page, settled on the way out so a check
    # cached before the rule existed gets it too. The evidence line is never
    # surer than the least sure thing the page says, and a certainty phrase
    # in the prose that is surer than the line is rewritten down to it.
    stated = evidence.stated_certainty(tldr, explanation, *breakdown_mod.prose_of(breakdown))
    # A study code set aside as measuring another outcome loses its citation,
    # never its say: the line is the less sure of the one over the studies
    # used and the one with those put back, so setting one aside never lifts
    # a certainty word, and a cap either reading earns applies. Every reading
    # sizes only a figure about the claim itself (line_label).
    readings = _readings(studies, cited, search_query)
    sized = breakdown_mod.line_label(breakdown, claim, outcome)
    withheld = bool(((breakdown or {}).get("effect") or {}).get("label")) and not sized
    # The effect box follows the same gate: a size the line may not carry is
    # said nowhere by code, neither as a label nor as "That is ... a large
    # effect" under the figure. The figure and its own sentence stay.
    if withheld:
        breakdown = breakdown_mod.unsized(breakdown)

    def line_for(stamp, pool=None):
        pool = studies if pool is None else pool
        lines = [evidence.evidence_line(
            stamp, pool, used, claim, sized,
            breakdown_mod.absolute_claim(claim),
            bool(breakdown_mod._COMPARATIVE_CLAIM.search(claim or "")),
            outcome, stated=stated, withheld=withheld) for used in readings]
        sure = lambda line: (evidence._rank(evidence.line_certainty(line))
                             if evidence.line_certainty(line) else len(evidence.CERTAINTY_LEVELS))
        return min(lines, key=sure)

    evidence_line = line_for(verdict_value)
    # The stamp follows the same cap a fresh check gets in verdict.py: a
    # "true" the evidence cannot carry (the lead review rates itself low, or
    # only small and pilot trials) is not shown as likely true, and nor is a
    # "true" over a line that says low certainty. A check cached before the
    # cap existed, or before its line was scoped, gets it here.
    if any(_capped_true(verdict_value, claim, studies, used, outcome, tldr) for used in readings):
        verdict_value = "complicated"
        if not re.search(r"\bcertainty\b[^.]*\blow\b", tldr or "", re.IGNORECASE):
            leaned = f"{(tldr or '').rstrip().rstrip('.')}, but the certainty of that evidence is low."
            tldr = (leaned if tldr and len(leaned) <= verdict.TLDR_MAX
                    and not breakdown_mod.stamp_conflict(verdict_value, leaned, "", claim)
                    else verdict.LOW_CERTAINTY_TLDR)
        evidence_line = line_for(verdict_value)
    # A figure that was not significant is said as one, in words that do not
    # contradict an answer that there is an effect.
    if breakdown and breakdown_mod.answers_effect(verdict_value, claim):
        breakdown = breakdown_mod.map_prose(breakdown, breakdown_mod.null_alone)
    level = evidence.line_certainty(evidence_line)
    if level:
        tldr = evidence.cap_certainty(tldr, level)
        explanation = evidence.cap_certainty(explanation, level)
        breakdown = breakdown_mod.map_prose(breakdown, lambda t: evidence.cap_certainty(t, level))
    # An answer the guidance rule set did not come from these studies, so the
    # line shown makes no claim about how strong they are for or against the
    # claim. The studies' own line still set the cap on the prose above.
    # It shows whenever any study is listed, even when none is relied on (the
    # one cited was set aside), so the line is never blank under such an
    # answer; with no study listed at all the page keeps its own wording.
    if verdict.from_guidance(tldr, claim) and (evidence_line or studies):
        evidence_line = verdict.GUIDANCE_LINE
    # The response's effect field says what the page says (after every
    # rewrite above, since settling the effect can set it afresh).
    if breakdown and breakdown_mod.answers_effect(verdict_value, claim):
        breakdown = breakdown_mod.effect_alone(breakdown)
    # A "That is ..." sentence travels with the figure it explains.
    breakdown = breakdown_mod.translations_follow(breakdown, withheld)
    queries = [search_query] + [q for q in [pubmed.broaden_query(search_query or "")] if q]
    # No bare strength word (E2): the line a reader sees says whether the
    # studies support the claim, then how sure or on what kind of study; a
    # second line says what they found instead for an absolute claim. The
    # old line above still set every cap; only its wording is replaced.
    absolute = breakdown_mod.absolute_claim(claim)
    if evidence_line == verdict.GUIDANCE_LINE:
        shown_line, effect_line = evidence_line, ""
    else:
        effect_line = _statement_two(verdict_value, absolute, breakdown, studies, cited, outcome, queries)
        # Certainty is said once: beside a second statement, or when a flagged
        # review lowered the level (no review states that certainty), the
        # first statement ends with its basis (brief E3 N2, N9).
        flag_lowered = False
        if any(evidence.cited_retracted(s) for s in studies or []):
            plain = [{k: v for k, v in s.items() if k != "cites_retracted"} for s in studies]
            rank = lambda line: (evidence._rank(evidence.line_certainty(line))
                                 if evidence.line_certainty(line) else len(evidence.CERTAINTY_LEVELS))
            flag_lowered = rank(evidence_line) < rank(line_for(verdict_value, plain))
        shown_line = evidence.claim_statement(verdict_value, evidence_line, studies, cited, absolute,
                                              basis_only=bool(effect_line) or flag_lowered)
    # One figure, once (E4): within everything a reader sees, a sentence
    # that only repeats figures already given goes. Code only.
    explanation, breakdown = breakdown_mod.one_figure_once(explanation, breakdown, len(studies or []))
    # A review relied on cites a retracted paper (E1): the answer names it,
    # once in the explanation and once in each list citing it. Last, so no
    # rule above can take the sentence out; a flag never removes a sentence.
    explanation, breakdown = _retracted_review_rules(explanation, breakdown, studies, cited)
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
    # The fixed safety lines (safety_lines.py), on the way out like the lines
    # above, so a saved answer gets the same lines as a fresh one and the
    # library is never hidden behind saved answers. They travel in their own
    # field and change nothing else; with no line the key is absent.
    safety = safety_lines.safety(claim)
    response = {
        "claim": claim,
        # What the reader typed, present only when the answer is to other
        # words than theirs, so the page can say so: "spelling" when the
        # spelling was fixed, "wording" when a saved check of the same claim
        # in other words was used. Both null otherwise.
        "typed": typed,
        "typed_reason": (typed_reason or "spelling") if typed else None,
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
        "review_line": _review_line(cached_at, studies, cited),
        # The instant behind the line's day: the page wraps the day in <time>
        # and the script prints the reader's own day from it.
        "review_when": _review_when(cached_at),
        "share_url": f"{base_url}/?q={quote(claim)}",
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/?term={quote(search_query or claim)}",
        # Counts the reader can check us on, computed from PubMed's own
        # metadata rather than from anything the model said.
        "evidence": evidence.snapshot(_as_shown(studies), cited),
        # What that evidence shows about the claim, for the line under the
        # chart: "Strong evidence of a small effect, not a cure". The level
        # word alone described the designs, not the claim.
        "evidence_line": shown_line,
        # What the studies found instead, for a claim promising something
        # absolute that is not rated true (evidence.effect_statement), or "".
        "effect_line": effect_line,
        # Present only when the check came back without usable evidence, so
        # a dead end offers somewhere to go instead of stopping.
        "next_steps": _next_steps_safe(claim, search_query, studies, cited),
        "studies": [{**_study_payload(s, cited), **_study_extra(s, cited)}
                    for s in _as_shown(studies)],
    }
    if safety:
        response["safety"] = safety
    return response


def _review_instant(cached_at) -> datetime:
    """When the check ran: the saved date, or now (to the minute, so one
    answer built twice reads the same) for a check that just ran."""
    return ((display.as_instant(cached_at) if cached_at else None)
            or datetime.now(timezone.utc).replace(second=0, microsecond=0))


def _review_when(cached_at) -> dict:
    when = _review_instant(cached_at)
    return {"iso": when.isoformat(), "day": display.home_day(when)}


def _review_line(cached_at, studies, cited) -> str:
    """The dated line under the verdict: when the check ran, and the years of
    the studies it relied on. "Checked 14 November 2026, from studies
    published 2019 to 2025." Built here once, so the page a shared link
    renders and a streamed check say the same thing. The day is the home
    zone's (display.home_day); the script prints the reader's own."""
    line = f"Checked {display.home_day(_review_instant(cached_at))}"
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
        db.log_check(claim, verdict_value, explanation, _without_references(studies), cached=cached)
    except sqlite3.Error as e:
        app.logger.warning("Could not log check: %s", e)


def _run_check(claim: str, client_ip: str, base_url: str):
    """
    The pipeline, with the day counts around it: a claim that reaches it is
    "submitted", one that ends in a result is "completed". Only the totals are
    kept; see db.COUNTER_NAMES.
    """
    if claim and len(claim) <= MAX_CLAIM_CHARS:
        db.count_event("check.submitted")
    stages = _run_check_stages(claim, client_ip, base_url)
    try:
        for ev in stages:
            if ev.get("stage") == "done":
                db.count_event("check.completed")
            yield ev
    finally:
        # A reader who leaves at "done" closes this generator: close the
        # stages too, now, so the bookkeeping after "done" runs at once.
        stages.close()


def _current(cached, claim):
    """
    A saved answer, or None when it is out of date because it relied on a
    study the current code sets aside as measuring another outcome. Its short
    answer and reasons were written with that study in view, and code cannot
    tell which of its sentences lean on it, so it is not patched and served:
    the check runs fresh, as it does for any answer past its window.
    """
    if not cached:
        return cached
    # One that relied on a retracted paper or a journal notice is out of
    # date the same way: the paper is background now, for any claim.
    if any(isinstance(s, dict) and s.get("pmid") in set(cached.get("cited_studies") or [])
           and evidence.withdrawn(s) for s in cached.get("studies") or []):
        return None
    groups = pubmed.split_and(cached.get("search_query") or "")
    if len(groups) < 2:
        return cached
    cited = set(cached.get("cited_studies") or [])
    hit = [s for s in cached.get("studies") or [] if isinstance(s, dict) and s.get("pmid") in cited
           and evidence.measured_outcome(s, groups[1], "", groups[0]) is False]
    if not hit:
        return cached
    try:
        saved = db.get_cached_query(claim, verdict.QUERY_PROMPT_VERSION)
    except sqlite3.Error:
        saved = None
    surrogate = saved[1] if saved else ""
    if any(evidence.measured_outcome(s, groups[1], surrogate or "", groups[0]) is False for s in hit):
        return None
    return cached


def _run_check_stages(claim: str, client_ip: str, base_url: str):
    """
    Yields stage dicts. Terminal stages are "done" (with "result") and
    "error" (with "error", "status", optional "retry_after" and "way_out").

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

    def from_cache(cached, typed=None, reason=None, saved_as=None):
        # A saved answer found under another wording is shown, shared and
        # logged under the wording it was saved with, never the typed one.
        shown = saved_as or claim
        _log_check_safe(shown, cached["verdict"], cached["explanation"], cached["studies"], cached=True)
        return {"stage": "done", "result": _check_response(
            shown, cached["search_query"], cached["verdict"], cached["tldr"], cached["explanation"],
            cached["cited_studies"], cached["studies"], base_url, cached_at=cached["cached_at"],
            still_open=cached.get("still_open", ""), breakdown=cached.get("breakdown"), typed=typed,
            typed_reason=reason)}

    # A saved answer in its window that _current calls out of date is checked
    # fresh. Until a fresh answer replaces it, it is still the answer this
    # site gave: when the fresh check cannot finish, for any defined reason,
    # the reader gets it, exactly as before, and nothing is overwritten.
    stale = []

    def current(found, key, typed=None, reason=None):
        now = _current(found, key)
        if found and now is None and not stale:
            stale.append((found, typed, reason, key))
        return now

    def or_saved(ev):
        """The error stage, or the out-of-date saved answer when there is one."""
        if stale:
            app.logger.warning("A fresh check of an out-of-date saved answer did not finish "
                               "(%s); the saved answer is shown", ev.get("status"))
            return from_cache(*stale[0])
        return ev

    # 1. Cache first: a repeat of a recent claim costs nothing and is
    #    never rate-limited. Viral claims get checked by many people.
    cached = current(db.get_cached_verdict(claim, VERDICT_CACHE_HOURS), claim)
    if cached:
        yield from_cache(cached)
        return
    # The same claim in other words ("Does coffee cause cancer?" for "Coffee
    # causes cancer") gets the same saved answer, as free as a repeat. One
    # older than the cache window is checked again under its saved wording,
    # so its one row is refreshed and no second row for it is made.
    typed, reason = None, None
    same = _equivalent(claim)
    if same:
        cached = current(db.get_cached_verdict(same, VERDICT_CACHE_HOURS), same, claim, "wording")
        if cached:
            yield from_cache(cached, claim, "wording", same)
            return
        typed, reason, claim = claim, "wording", same

    # 2. Rate limit only the checks that will actually hit the AI provider.
    allowed, message, retry_after = ratelimit.check(f"ip:{client_ip}")
    if not allowed:
        yield or_saved({"stage": "error", "status": 429, "error": message, "retry_after": retry_after})
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
        if understood != claim:
            # Misspelled. Everything from here runs on the claim as meant:
            # the verdict, the code's own checks, the cache and the share
            # link. A correctly spelled check of it may already be cached.
            # The note says what the reader typed, and that it was spelling.
            typed, reason, claim = typed or claim, "spelling", understood
            cached = current(db.get_cached_verdict(claim, VERDICT_CACHE_HOURS), claim, typed, reason)
            if cached:
                yield from_cache(cached, typed, reason)
                return
            same = _equivalent(claim)
            if same:
                cached = current(db.get_cached_verdict(same, VERDICT_CACHE_HOURS), same, typed, reason)
                if cached:
                    yield from_cache(cached, typed, reason, same)
                    return
                claim = same
                again = db.get_cached_query(claim, verdict.QUERY_PROMPT_VERSION)
                if again:
                    search_query, surrogate, _ = again
        yield {"stage": "query", "query": search_query, "claim": claim if typed else None,
               "typed_reason": reason}
        studies, query_used, broadened = pubmed.search_with_fallback(
            search_query, max_results=MAX_STUDIES, surrogate=surrogate)
        yield {"stage": "found", "count": len(studies), "query": query_used, "broadened": broadened}
        kept = _steady(claim, studies)
        if kept:
            yield from_cache(kept, typed, reason, claim if typed else None)
            return
        # Which reviews cite a paper later retracted: one lookup at most, and
        # none when a saved answer is served above.
        retraction_check = _flag_retractions(studies)
        if studies:
            yield {"stage": "weigh"}
        result = verdict.weigh_evidence(claim, studies, query_used, surrogate)
    except RuntimeError as e:
        # Missing/misconfigured API key: the operator's problem, not the
        # reader's. The detail goes to the log; the page says what to do.
        app.logger.error("Check could not run: %s", e)
        db.count_event("provider.auth")
        yield or_saved({"stage": "error", "status": 500, "error": UNAVAILABLE_MESSAGE})
        return
    except verdict.ProviderError as e:
        status = {"auth": 500, "rate_limit": 429, "unavailable": 502}.get(e.kind, 502)
        db.count_event("provider." + (e.kind if e.kind in ("auth", "rate_limit", "unavailable") else "other"))
        suffix = " Wait a few seconds and try again." if e.kind == "rate_limit" else ""
        # A rejected key names .env and the provider's console: for the
        # operator's log, never for the page.
        shown = UNAVAILABLE_MESSAGE if e.kind == "auth" else e.detail + suffix
        if e.kind == "auth":
            app.logger.error("The AI provider rejected its key: %s", e.detail)
        elif getattr(e, "code", None) is not None:
            # The provider's status code is for the log, never the page.
            app.logger.warning("AI provider error %s (%s): %s", e.code, e.kind, e.detail)
        ev = {"stage": "error", "status": status, "error": shown}
        # Which way out the page offers beside the message: other words only,
        # or other words beside trying again. Absent, trying again is the way.
        if getattr(e, "way_out", ""):
            ev["way_out"] = e.way_out
        if e.kind == "rate_limit":
            ev["retry_after"] = 20
        yield or_saved(ev)
        return
    except requests.RequestException:
        db.count_event("pubmed.failure")
        yield or_saved({"stage": "error", "status": 502,
                        "error": "Couldn't reach PubMed right now. Try again in a minute."})
        return
    except Exception:  # noqa: BLE001 — last line of defence; never crash a check
        # The exception text is for the log. A reader gets the problem and a way out.
        app.logger.exception("Check failed")
        db.count_event("error.server")
        yield or_saved({"stage": "error", "status": 500,
                        "error": "Something went wrong while checking this claim. Try again in a moment."})
        return

    # The answer goes to the reader first and is saved and logged after,
    # in the finally below, which runs whether the reader reads on or
    # closes the stream at "done". One exception: an answer that rests on
    # no study shows next steps, which list the saved claims, this one
    # included, so there the writes come first as before.
    shows_saved = not (studies and display.relied_pmids(result["cited_studies"], studies))
    if shows_saved:
        _save_and_log(claim, query_used, result, studies)
    else:
        # What is saved is what was there before the response was built,
        # as when the writes came first.
        kept = copy.deepcopy((result, studies))
    response = _check_response(
        claim, query_used, result["verdict"], result["tldr"], result["explanation"],
        result["cited_studies"], studies, base_url, broadened=broadened,
        still_open=result.get("still_open", ""), breakdown=result.get("breakdown"), typed=typed,
        typed_reason=reason)
    if retraction_check:
        response["retraction_check"] = retraction_check
    try:
        yield {"stage": "done", "result": response}
    finally:
        if not shows_saved:
            _save_and_log(claim, query_used, *kept)


def _without_references(studies):
    """The studies as they are stored: a review's reference list (up to
    hundreds of ids) is read once, for the retraction check, and never
    stored or sent. Its result, cites_retracted, is kept."""
    return [{k: v for k, v in s.items() if k != "references"} if isinstance(s, dict) else s
            for s in studies or []]


def _save_and_log(claim, query_used, result, studies):
    """Save a fresh answer and log the check. A write that fails is logged
    and never reaches the reader, who may already have the answer."""
    studies = _without_references(studies)
    try:
        db.put_cached_verdict(claim, query_used, result["verdict"], result["explanation"],
                              result["cited_studies"], studies, tldr=result["tldr"],
                              still_open=result.get("still_open", ""),
                              breakdown=result.get("breakdown"), method_stamp=verdict.METHOD_STAMP)
    except Exception as e:  # noqa: BLE001 — sqlite or Turso; the answer is already out
        app.logger.warning("Could not save a fresh answer: %s", e)
    try:
        _log_check_safe(claim, result["verdict"], result["explanation"], studies)
    except Exception as e:  # noqa: BLE001
        app.logger.warning("Could not log check: %s", e)


def _steady(claim: str, studies: list) -> dict | None:
    """
    A saved answer past the cache window, kept rather than made again: when
    it is whole, has no problem the page cannot repair, was made by the code
    and model in force now (its method stamp), and the search just run found
    exactly the records it read (`_same_records`). Its date is refreshed
    and it is returned as get_cached_verdict reads it. Otherwise None, and the
    check runs as before: a row with no stamp or an old one is always made
    again once, so every fix reaches every saved claim.
    """
    if VERDICT_CACHE_HOURS <= 0:      # the cache is switched off: always check again
        return None
    try:
        saved = db.get_cached_by_key(db.normalize_claim(claim))
        # An empty stamp (one that could not be worked out) equals nothing,
        # a missing stamp included.
        if not (verdict.METHOD_STAMP and db.servable(saved)
                and saved.get("method_stamp") == verdict.METHOD_STAMP):
            return None
        if not _same_records(saved["studies"], studies or []):
            return None
        # One that relied on a study code now sets aside is out of date
        # (_current), whatever else is the same, and is made again.
        if _current(saved, saved["claim_text"]) is None:
            return None
        db.refresh_saved(saved["claim_text"])
        return db.get_cached_verdict(saved["claim_text"], VERDICT_CACHE_HOURS)
    except sqlite3.Error as e:
        app.logger.warning("Could not read a saved answer to keep: %s", e)
        return None


# What makes a study the same record as the one a saved answer read: its id,
# its title, its abstract and its publication types, which is where PubMed
# marks a retraction, a correction or an expression of concern.
_RECORD_FIELDS = ("pmid", "title", "abstract", "publication_types")


def _record(study) -> tuple | None:
    """A study's identity as a record, or None when it lacks a field above."""
    if not isinstance(study, dict) or any(k not in study for k in _RECORD_FIELDS):
        return None
    types = study["publication_types"]
    if not isinstance(types, (list, tuple)):
        return None
    return (str(study["pmid"]), study["title"], study["abstract"], tuple(sorted(map(str, types))))


def _same_records(saved: list, fresh: list) -> bool:
    """
    True when the search found the same records the saved answer read: the
    same set of studies, each with the same id, title, abstract and
    publication types. Order and repeats do not matter. A study on either
    side that lacks one of those fields is never the same.
    """
    a = [_record(s) for s in saved or []]
    b = [_record(s) for s in fresh or []]
    if None in a or None in b:
        return False
    return set(a) == set(b)


def _equivalent(claim: str) -> str | None:
    """
    The saved wording of this same claim when it differs from the claim's
    own key (a lookup by that key has already been made), or None. Looking
    is a convenience: a storage error never sinks the check.
    """
    try:
        same = db.find_equivalent(claim)
    except sqlite3.Error as e:
        app.logger.warning("Could not look for an equivalent claim: %s", e)
        return None
    return same if same and db.normalize_claim(same) != db.normalize_claim(claim) else None


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
        if "way_out" in final:
            body["way_out"] = final["way_out"]
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
        run = _run_check(claim, client_ip, base_url)
        try:
            for ev in run:
                yield "data: " + json.dumps(ev) + "\n\n"
        finally:
            run.close()

    return Response(events(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


COUNT_BODY_MAX = 200  # bytes: a name and nothing else


@app.route("/api/count", methods=["POST"])
def api_count():
    """
    One more to a per-day total, for the few things only the page can see:
    a share click, and the kind of error the page showed. The body is a name
    from a fixed list (db.CLIENT_COUNTERS) and nothing else; anything else is
    refused. Nothing about the sender is read or kept, and a browser that
    sends Global Privacy Control is not counted.
    """
    if (request.content_length or 0) > COUNT_BODY_MAX:
        return jsonify({"error": "Not a count."}), 400
    payload = request.get_json(force=True, silent=True)
    name = payload.get("name") if isinstance(payload, dict) and len(payload) == 1 else None
    if name not in db.CLIENT_COUNTERS:
        return jsonify({"error": "Not a count."}), 400
    allowed, _ = ratelimit.COUNT.check(f"ip:{_client_ip()}")
    if not allowed:
        return jsonify({"error": "Too many."}), 429
    if request.headers.get("Sec-GPC") != "1":
        db.count_event(name)
    return "", 204


@app.route("/api/trending")
def api_trending():
    return jsonify([{"claim": r["claim"], "verdict": r["verdict"], "checks": r["checks"]}
                    for r in _one_per_question(_public_claims(db.trending(limit=8 * LIST_OVERFETCH), 8 * LIST_OVERFETCH,
                                                              key="claim"), key="claim")[:8]])


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
        db.count_event("pubmed.failure")
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
    # Type-ahead is a public list like the others: it offers only claims the
    # one screen (_public_claims) lets any reader see.
    claims = _one_per_question(_public_claims(claims, len(claims)))
    shown = (_suggested(r) for r in suggest_mod.suggest(q, claims))
    return jsonify({"suggestions": [r for r in shown if r is not None]})


def _suggested(r: dict) -> dict | None:
    """A suggestion with the stamp its page would show. Only a "true" can be
    capped, and at most a handful are offered, so only those few rows are read.
    A "true" whose row cannot be read for the cap (a race, or a key that no
    longer matches) is None, and left out: never shown uncapped."""
    if r.get("verdict") != "true":
        return r
    try:
        row = db.get_cached_by_key(db.normalize_claim(r["claim_text"]))
        if not row:
            return None
        return dict(r, verdict=_shown_verdict({
            "claim_text": row["claim_text"], "verdict": row["verdict"],
            "search_query": row.get("search_query") or "", "tldr": row.get("tldr") or "",
            "studies": row["studies"], "cited": row["cited_studies"]}))
    except Exception:  # noqa: BLE001
        return dict(r, verdict="complicated")


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
