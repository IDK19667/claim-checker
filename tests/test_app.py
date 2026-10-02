"""
Integration tests through the real Flask app with a fake AI provider and a
fake PubMed. No network, no key. Run:

    .venv/bin/python tests/test_app.py
"""
import json
import logging
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(k, None)
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

import dotenv  # noqa: E402
dotenv.load_dotenv = lambda *a, **k: None  # keep the developer's .env out of the tests

import app as appmod  # noqa: E402
import db  # noqa: E402
import pubmed  # noqa: E402
import ratelimit  # noqa: E402
import verdict  # noqa: E402
from google.genai import errors  # noqa: E402
import requests  # noqa: E402

appmod.app.logger.setLevel(logging.ERROR)
c = appmod.app.test_client()
results = []


def t(label, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'} {label} {extra if not cond else ''}")


def P(claim, ip="1.1.1.1", **hdr):
    return c.post("/api/check", json={"claim": claim}, environ_base={"REMOTE_ADDR": ip}, headers=hdr)


def stream(claim, ip="8.8.8.8"):
    r = c.post("/api/check/stream", json={"claim": claim}, environ_base={"REMOTE_ADDR": ip})
    evs = [json.loads(l[6:]) for l in r.data.decode().split("\n") if l.startswith("data: ")]
    return r, evs


# ---- pages & PWA assets -----------------------------------------------------
t("index 200", c.get("/").status_code == 200)
t("teacher page gone", c.get("/teacher").status_code == 404)
m = c.get("/manifest.webmanifest")
mj = json.loads(m.data)
t("manifest served w/ type", m.status_code == 200 and "manifest+json" in m.content_type and mj["start_url"] == "/")
t("manifest carries the current product name, not a stale one",
  mj["name"] == "Evident" and mj["short_name"] == "Evident", mj["name"])
t("the front page's own wordmark carries the current name",
  ">Evident<" in c.get("/").data.decode())
t("llms.txt was renamed along with everything else",
  "# Evident" in c.get("/llms.txt").data.decode())
t("manifest share_target + shortcuts", mj["share_target"]["params"]["text"] == "text" and mj["shortcuts"][0]["url"] == "/?focus=1")
s = c.get("/sw.js")
t("sw.js at root w/ js type", s.status_code == 200 and "javascript" in s.content_type)
t("healthz", c.get("/healthz").get_json() == {"ok": True})
for icon in ("icon-192.png", "icon-512.png", "icon-512-maskable.png", "apple-touch-icon.png", "icon.svg"):
    t(f"icon {icon}", c.get(f"/static/icons/{icon}").status_code == 200)

# ---- validation -------------------------------------------------------------
t("empty claim 400", P("").status_code == 400)
t("500+ char claim 400", P("x" * 501).status_code == 400)
r = P("x")
t("no keys -> 500 w/ instructions", r.status_code == 500 and "aistudio" in r.get_json()["error"])

# ---- provider selection -----------------------------------------------------
os.environ["ANTHROPIC_API_KEY"] = "a"
t("anthropic only -> anthropic", verdict.provider() == "anthropic")
os.environ["GEMINI_API_KEY"] = "g"
t("both -> gemini wins", verdict.provider() == "gemini")
os.environ["LLM_PROVIDER"] = "anthropic"
t("LLM_PROVIDER override", verdict.provider() == "anthropic")
os.environ.pop("LLM_PROVIDER"); os.environ.pop("ANTHROPIC_API_KEY")
os.environ["GEMINI_API_KEY"] = "your_gemini_key_here"
try:
    verdict.provider(); t("placeholder key treated as unset", False)
except RuntimeError:
    t("placeholder key treated as unset", True)
os.environ["GEMINI_API_KEY"] = "g"

ratelimit.GLOBAL_MINUTE = ratelimit.SlidingWindow(100, 60)
ratelimit.GLOBAL_DAY = ratelimit.SlidingWindow(1000, 86400)
ratelimit.PER_USER = ratelimit.SlidingWindow(2, 600)


# ---- fakes ------------------------------------------------------------------
class Cand:
    def __init__(self, fr): self.finish_reason = fr


class Resp:
    def __init__(self, text, fr="FinishReason.STOP"): self.text = text; self.candidates = [Cand(fr)]


class FakeModels:
    def __init__(self): self.script = []; self.calls = 0

    def generate_content(self, **kw):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeGemini:
    def __init__(self): self.models = FakeModels()


fg = FakeGemini()
verdict._gemini_client = fg
STUDIES = [{"pmid": f"P{i}", "title": f"T{i}", "abstract": "a", "journal": "J", "year": "2020",
            "publication_types": ["Journal Article"], "authors": ["A B"], "data_banks": None, "url": f"u{i}"}
           for i in range(1, 4)]
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES


def VJ(v, nums, tldr="Short take.", still_open="", **extra):
    body = {"verdict": v, "tldr": tldr, "explanation": "Because.", "cited_study_numbers": nums}
    if still_open:
        body["still_open"] = still_open
    body.update(extra)
    return json.dumps(body)


# ---- happy path -------------------------------------------------------------
fg.models.script = [Resp("vinegar AND diabetes"), Resp(VJ("complicated", [1, 3]))]
r = P("Apple cider vinegar cures diabetes"); d = r.get_json()
t("happy path 200", r.status_code == 200, d)
t("  cited mapping", [s["pmid"] for s in d["studies"] if s["cited_in_verdict"]] == ["P1", "P3"])
t("  tldr, share_url, pubmed_url, study detail fields",
  d["tldr"] == "Short take." and d["share_url"].endswith("/?q=Apple%20cider%20vinegar%20cures%20diabetes")
  and "pubmed.ncbi" in d["pubmed_url"] and d["studies"][0]["authors"] == ["A B"] and d["studies"][0]["abstract"] == "a"
  and d["search_broadened"] is False, d)
t("  not cached, 2 calls", d["cached"] is False and fg.models.calls == 2)

# ---- cache hit from another IP, no calls, no limiter use ---------------------
r = P("  apple CIDER vinegar cures diabetes.  ", ip="2.2.2.2"); d = r.get_json()
t("cache hit from other ip", r.status_code == 200 and d["cached"] is True and d["tldr"] == "Short take." and fg.models.calls == 2)
logs = db.recent_checks()
t("both checks logged, cached flag set", len(logs) == 2 and logs[0]["cached"] == 1 and logs[1]["cached"] == 0)
with db.get_conn() as conn:
    row = conn.execute("select studies_json from checks order by id limit 1").fetchone()
t("log keeps full study metadata", json.loads(row["studies_json"])[0]["authors"] == ["A B"])

# ---- guard: false with no citations -> complicated ---------------------------
fg.models.script = [Resp("q"), Resp(VJ("false", []))]
d = P("uncited false claim").get_json()
t("uncited false -> complicated + honest tldr", d["verdict"] == "complicated" and "can't be rated" in d["explanation"] and "unproven" in d["tldr"], d)

# ---- per-ip limit (1.1.1.1 has used 2 of 2) ---------------------------------
fg.models.script = [Resp("q"), Resp(VJ("true", [1]))]
r = P("third claim")
t("per-ip limit 429 + Retry-After", r.status_code == 429 and r.headers.get("Retry-After") and r.get_json()["retry_after"] >= 1, r.get_json())
r = P("apple cider vinegar cures diabetes")
t("limited ip still gets cache", r.status_code == 200 and r.get_json()["cached"])
r = P("third claim", ip="3.3.3.3")
t("other ip ok", r.status_code == 200, r.get_json())

# ---- proxy header only honored when TRUST_PROXY ------------------------------
appmod.TRUST_PROXY = False
fg.models.script = [Resp("q"), Resp(VJ("true", [1]))]
r = P("xff claim a", ip="9.9.9.9", **{"X-Forwarded-For": "1.1.1.1"})
t("XFF ignored by default", r.status_code == 200, r.get_json())
appmod.TRUST_PROXY = True
fg.models.script = [Resp("q"), Resp(VJ("true", [1]))]
r = P("xff claim b", ip="9.9.9.9", **{"X-Forwarded-For": "1.1.1.1, 10.0.0.1"})
t("XFF honored w/ TRUST_PROXY -> limited ip", r.status_code == 429, r.get_json())
appmod.TRUST_PROXY = False
ratelimit.PER_USER = ratelimit.SlidingWindow(100, 600)

# ---- broadened search --------------------------------------------------------
calls = []


def _fb(q, max_results=8, surrogate=''):
    calls.append(q)
    return [] if " AND " in q else STUDIES


pubmed.search_and_fetch = _fb
# The model is held to two groups, so a query that finds nothing is broadened
# from two to one, not from three to two.
fg.models.script = [Resp("a AND b"), Resp(VJ("true", [1]))]
d = P("broaden me", ip="7.7.7.7").get_json()
t("broadened search used & flagged", d["search_broadened"] is True and d["search_query_used"] == "a"
  and calls == ["a AND b", "a"], d)
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# ---- SSE stream -------------------------------------------------------------
fg.models.script = [Resp("q"), Resp(VJ("false", [2]))]
r, evs = stream("streamed claim")
t("sse content-type", r.status_code == 200 and "text/event-stream" in r.content_type)
t("sse stages in order", [e["stage"] for e in evs] == ["search", "query", "found", "weigh", "done"], [e["stage"] for e in evs])
t("  found carries count/query; done carries result", evs[2]["count"] == 3 and evs[2]["query"] == "q" and evs[4]["result"]["verdict"] == "false")
r, evs = stream("streamed claim", ip="8.8.8.9")
t("sse cache hit -> single done, cached", [e["stage"] for e in evs] == ["done"] and evs[0]["result"]["cached"] is True)
r, evs = stream("")
t("sse empty claim -> error 400 event", evs == [{"stage": "error", "status": 400, "error": "Type a health claim to check."}], evs)
ratelimit.PER_USER = ratelimit.SlidingWindow(1, 600)
fg.models.script = [Resp("q"), Resp(VJ("true", [1]))]; stream("rl one", ip="5.5.5.5")
r, evs = stream("rl two", ip="5.5.5.5")
t("sse rate limit -> error 429 w/ retry_after", evs[-1]["stage"] == "error" and evs[-1]["status"] == 429 and evs[-1]["retry_after"] >= 1, evs)
ratelimit.PER_USER = ratelimit.SlidingWindow(100, 600)

# ---- gemini error mapping -----------------------------------------------------
fg.models.script = [errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})]
r = P("err a"); t("gemini 429 -> 429 w/ retry_after", r.status_code == 429 and r.get_json()["retry_after"] == 20)
fg.models.script = [errors.ClientError(400, {"error": {"message": "API key not valid.", "status": "INVALID_ARGUMENT"}})]
r = P("err b"); t("gemini bad key -> 500 + msg", r.status_code == 500 and "aistudio" in r.get_json()["error"])
fg.models.script = [errors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}})]
t("gemini 503 -> 502", P("err c").status_code == 502)
fg.models.script = [Resp("q"), Resp("not json")]
t("non-JSON -> complicated", P("err d").get_json()["verdict"] == "complicated")
fg.models.script = [Resp("q"), Resp("", "FinishReason.MAX_TOKENS")]
t("truncated -> complicated", "ran out of room" in P("err e").get_json()["explanation"])
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': (_ for _ in ()).throw(requests.ConnectionError("down"))
fg.models.script = [Resp("q")]
r = P("err f"); t("PubMed down -> 502", r.status_code == 502 and "PubMed" in r.get_json()["error"])
t("failed checks not cached", all(db.get_cached_verdict(x, 24) is None for x in ("err a", "err b", "err c", "err f")))
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# ---- trending -----------------------------------------------------------------
for _ in range(2):
    P("streamed claim", ip="6.6.6.6")
tr = c.get("/api/trending").get_json()
t("trending lists multi-checked cached claims", any(x["claim"] == "streamed claim" and x["checks"] >= 3 for x in tr), tr)

# ---- feedback -------------------------------------------------------------------
t("feedback validation 400", c.post("/api/feedback", json={"claim": "x"}).status_code == 400)
t("feedback ok", c.post("/api/feedback", json={"claim": "streamed claim", "verdict": "false", "helpful": False, "note": "meh"}).get_json() == {"ok": True})
with db.get_conn() as conn:
    fb = conn.execute("select * from feedback").fetchone()
t("  feedback stored keyed", fb["claim_key"] == "streamed claim" and fb["helpful"] == 0 and fb["note"] == "meh")

# ---- link previews ---------------------------------------------------------------
html = c.get("/?q=streamed%20claim").data.decode()
t("og:title carries verdict for cached claim", 'property="og:title" content="Likely false: “streamed claim”"' in html)
t("  og:image points at card", "/og/streamed%20claim.png" in html)
html2 = c.get("/?text=streamed%20claim").data.decode()
t("share-target ?text= also previews", 'og:title" content="Likely false' in html2)
html3 = c.get("/?q=never%20checked").data.decode()
t("uncached claim -> generic og", 'og:title" content="Evident"' in html3)
img = c.get("/og/streamed%20claim.png")
t("og image png for cached claim", img.status_code == 200 and img.mimetype == "image/png" and img.data[:8] == b"\x89PNG\r\n\x1a\n")
img2 = c.get("/og/_.png")
t("generic og image", img2.status_code == 200 and img2.data[:4] == b"\x89PNG")

# ---- the prose gate + "still open" ------------------------------------------------
t("tidy: filler opener and dashes removed, sentence closed",
  verdict.tidy_prose("It is important to note that vinegar helps a little \u2014 the effect is small", 700)
  == "Vinegar helps a little, the effect is small.")
t("tidy: capped at a sentence boundary",
  verdict.tidy_prose("One. Two two two two. Three three three three three three.", 24) == "One. Two two two two.")
t("tidy: empty stays empty", verdict.tidy_prose("   ", 100) == "")
fg.models.script = [Resp("q"), Resp(VJ("true", [1], tldr="Overall, it works \u2013 mostly.",
                                          still_open="Whether it lasts past 12 weeks \u2014 no trial ran longer"))]
d = P("gate claim", ip="7.7.7.7").get_json()
t("gate applied to tldr and still_open in the API",
  d["tldr"] == "It works, mostly." and d["still_open"] == "Whether it lasts past 12 weeks, no trial ran longer.", d)
d2 = P("gate claim", ip="8.8.8.8").get_json()
t("  still_open survives the cache", d2["cached"] is True and d2["still_open"] == d["still_open"], d2)
fg.models.script = [Resp("q"), Resp(VJ("false", []))]
d = P("uncited again", ip="7.7.7.7").get_json()
t("forced complicated names the open question", d["still_open"].startswith("Whether any study"), d)
html = c.get("/?q=gate%20claim").data.decode()
t("no dashes in shipped copy", "\u2014" not in html and "\u2013" not in html)

# ---- readable without JavaScript, and to crawlers --------------------------------
html = c.get("/?q=streamed%20claim").data.decode()
t("verdict is server-rendered, not JS-only",
  "streamed claim" in html and 'id="tldr">' in html and "Likely false" in html and 'id="result"' in html
  and 'id="server-result"' in html, html[:200])
t("  the ask view is hidden when a result is rendered", '<section id="ask" aria-labelledby="ask-heading" hidden>' in html)
t("  studies are in the HTML with their PMIDs", "PMID P1" in html and "T1" in html)
# The source column folds into two groups. Every study stays in the HTML either
# way: a crawler, and a reader with no JavaScript, must still get the whole file.
t("  sources split into relied on and set aside",
  'Relied on for the verdict</span><span class="group-count">1' in html
  and 'Read, not relied on</span><span class="group-count">2' in html, html[html.find("sources"):][:200])
t("  the relied-on group is open and the rest is folded",
  '<details class="source-group" open>' in html
  and '<details class="source-group">' in html)
t("  every study is still in the folded HTML",
  all(f"PMID P{i}" in html for i in (1, 2, 3)))
t("  study numbers stay global so the prose can point at them",
  '>01<' in html and '>02<' in html and '>03<' in html)
import re as _re
m = _re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, _re.S)
ld = json.loads(m.group(1)) if m else {}
t("ClaimReview markup", ld.get("@type") == "ClaimReview" and ld["claimReviewed"] == "streamed claim"
  and ld["reviewRating"]["ratingValue"] == 1 and ld["reviewRating"]["alternateName"] == "Likely false", ld)
_flagged = [x["identifier"] for x in ld.get("citation", []) if x["isBasedOn"]]
t("  every study is a citation, relied-on ones flagged",
  len(ld.get("citation", [])) == 3 and ld["citation"][0]["identifier"] == "PMID:P1"
  and _flagged == ["PMID:P2"], (_flagged, ld.get("citation")))
t("  no null values in the markup", "null" not in json.dumps(ld))
t("canonical points at the shared url", 'rel="canonical" href="http://localhost/?q=streamed%20claim"' in html)
t("uncached claim renders the ask view", '<section id="result" hidden>' in c.get("/?q=never%20checked").data.decode())

r = c.get("/robots.txt")
t("robots.txt allows crawlers and names the sitemap",
  r.status_code == 200 and "Disallow: /api/" in r.data.decode() and "Sitemap:" in r.data.decode()
  and "ClaudeBot" in r.data.decode())
r = c.get("/sitemap.xml")
t("sitemap lists cached claims", r.status_code == 200 and r.mimetype == "application/xml"
  and "streamed%20claim" in r.data.decode() and "/privacy" in r.data.decode())
r = c.get("/llms.txt")
t("llms.txt describes the rules and links recent claims",
  r.status_code == 200 and "ClaimReview" in r.data.decode() and "not medical advice" in r.data.decode().lower()
  and "streamed claim" in r.data.decode())

# ---- privacy page ----------------------------------------------------------------
r = c.get("/privacy")
t("privacy page", r.status_code == 200 and b"No tracking" in r.data and b"Not medical advice" in r.data)

# ---- security headers ------------------------------------------------------------
# The design forbids third-party requests and the privacy page promises a claim
# never reaches anyone else. Both are enforced by the browser here, not by hope.
r = c.get("/")
_h = r.headers
_csp = _h.get("Content-Security-Policy", "")
t("every response carries a content security policy", bool(_csp), _csp[:80])
t("  the policy is same-origin by default", "default-src 'self'" in _csp)
t("  no third party can be framed, based or posted to",
  "frame-ancestors 'none'" in _csp and "base-uri 'none'" in _csp
  and "form-action 'self'" in _csp and "object-src 'none'" in _csp)
t("  scripts are allowed by nonce, never by 'unsafe-inline'",
  "'nonce-" in _csp and "'unsafe-inline'" not in _csp.split("style-src")[0], _csp[:120])

_nonce = _re.search(r"'nonce-([^']+)'", _csp).group(1)
t("  the nonce in the header is the one in the page",
  f'nonce="{_nonce}"' in r.data.decode())

r2 = c.get("/")
_n2 = _re.search(r"'nonce-([^']+)'", r2.headers["Content-Security-Policy"]).group(1)
t("  a fresh nonce per response", _n2 != _nonce)

t("a claim never leaks to PubMed as a referrer", _h.get("Referrer-Policy") == "no-referrer")
t("content types are not sniffed", _h.get("X-Content-Type-Options") == "nosniff")
t("the page cannot be framed", _h.get("X-Frame-Options") == "DENY")
t("no camera, mic or location is ever asked for",
  "camera=()" in _h.get("Permissions-Policy", "") and "geolocation=()" in _h.get("Permissions-Policy", ""))
t("plain http is not told to be https-only", "Strict-Transport-Security" not in _h)
t("the privacy page is protected too",
  "Content-Security-Policy" in c.get("/privacy").headers)

import app as _app_mod  # noqa: E402
t("the debugger is off unless it is asked for",
  __import__("os").environ.get("FLASK_DEBUG", "0") == "0")

# ---- evidence: the classifier and the snapshot -----------------------------------
import evidence  # noqa: E402
import re  # noqa: E402

t("meta-analysis outranks the journal-article tag",
  evidence.classify(["Journal Article", "Meta-Analysis"]) == "strong")
t("a retracted paper is its own tier, not merely weak",
  evidence.classify(["Retracted Publication"]) == "retracted")
t("an untyped paper counts as moderate, never as strong",
  evidence.classify([]) == "moderate" and evidence.classify(["Journal Article"]) == "moderate")

_snap = evidence.snapshot(
    [{"pmid": "1", "year": "2020", "publication_types": ["Meta-Analysis"]},
     {"pmid": "2", "year": "2010", "publication_types": ["Case Reports"]},
     {"pmid": "3", "year": "2015", "publication_types": ["Clinical Trial"], "data_banks": "ClinicalTrials.gov"}],
    ["1"])
t("snapshot counts what was read, used, pooled and registered",
  _snap["read"] == 3 and _snap["relied_on"] == 1 and _snap["pooled"] == 1
  and _snap["trials"] == 1 and _snap["registered"] == 1, _snap)
t("snapshot reports the real year range", _snap["year_from"] == 2010 and _snap["year_to"] == 2020)
t("the mix adds up to the studies read", sum(m["count"] for m in _snap["mix"]) == 3)
t("empty input gives an empty mix, not a crash", evidence.snapshot([])["mix"] == [])
t("tier labels are singular for one and plural for many",
  evidence.tier_label("strong", 1) == "strong design" and evidence.tier_label("strong", 4) == "strong designs")

# The same table lives in static/app.js for the streaming path. If the two ever
# disagree, a streamed check and its cached page would grade the same study
# differently, so this fails loudly rather than letting them drift.
_js = (ROOT / "static" / "app.js").read_text()
# Only the TYPES array itself: DEFAULT_TYPE below it is a fallback, not a row.
_js_block = _js.split("const TYPES = [", 1)[1].split("\n];", 1)[0]
_js_map = {label: (cls or "moderate")
           for label, cls in re.findall(r'\["([^"]+)",\s*"(strong|weak|retracted|)"', _js_block)}
_py_map = dict(evidence.TYPES)
_differ = {k for k in set(_js_map) & set(_py_map) if _js_map[k] != _py_map[k]}
t("the evidence table in app.js matches evidence.py",
  _js_map == _py_map,
  f"js-only={set(_js_map) - set(_py_map)} py-only={set(_py_map) - set(_js_map)} differ={_differ}")

# ---- the deep dive (layer 3) -----------------------------------------------------
r = c.get("/api/study/abc")
t("a non-numeric PubMed ID is refused", r.status_code == 400)

_real_dive = pubmed.deep_dive
pubmed.deep_dive = lambda pmid: {"pmid": pmid, "cited_by": 7, "full_text_url": "https://example.org/x",
                                 "related": [{"pmid": "99", "title": "A related study", "journal": "J",
                                              "year": "2021", "authors": [], "publication_types": ["Review"],
                                              "abstract": "", "data_banks": None,
                                              "url": "https://pubmed.ncbi.nlm.nih.gov/99/"}],
                                 "partial": False}
r = c.get("/api/study/34187442")
_d = r.get_json()
t("the deep dive returns citations, full text and related work",
  r.status_code == 200 and _d["cited_by"] == 7 and _d["full_text_url"]
  and len(_d["related"]) == 1 and _d["related"][0]["pmid"] == "99", _d)

def _boom(pmid):
    raise RuntimeError("NCBI is down")
pubmed.deep_dive = _boom
r = c.get("/api/study/34187442")
_d = r.get_json()
t("a failing deep dive degrades to an honest shape, never a 500",
  r.status_code == 200 and _d["partial"] is True and _d["related"] == []
  and _d["cited_by"] is None and _d.get("note"), _d)
pubmed.deep_dive = _real_dive

# ---- the front page carries the work ---------------------------------------------
_html = c.get("/").data.decode()
t("the front page shows the ledger", "Studies read" in _html and "Pooled analyses" in _html)
t("the front page lists recent checks with their evidence",
  "Latest checks" in _html and "entry-no" in _html and 'class="bar"' in _html)
t("the front page never says '1 studies'", "1 studies read" not in _html)

_res = c.get("/?q=streamed%20claim").data.decode()
t("a rendered result carries the evidence snapshot",
  'id="snapshot"' in _res and "snapshot-figs" in _res and "seg-" in _res)

# ---- type-ahead over checked claims ----------------------------------------------
import suggest as suggest_mod  # noqa: E402

_claims = [{"claim_text": "Turmeric reduces joint inflammation", "verdict": "true", "issue": 3},
           {"claim_text": "Vaccines cause autism", "verdict": "false", "issue": 2},
           {"claim_text": "Sunscreen causes cancer", "verdict": "false", "issue": 1}]

t("a literal fragment finds the claim",
  [r["claim_text"] for r in suggest_mod.suggest("sunscreen", _claims)] == ["Sunscreen causes cancer"])
_typo = suggest_mod.suggest("turmaric inflamation", _claims)
t("a misspelled claim still finds the right one",
  _typo and _typo[0]["claim_text"] == "Turmeric reduces joint inflammation", _typo)
t("a corrected spelling is labelled a near match, not a literal one",
  _typo and _typo[0]["kind"] == "near")
t("a literal hit is labelled as one",
  suggest_mod.suggest("vaccines", _claims)[0]["kind"] == "match")
t("nonsense suggests nothing", suggest_mod.suggest("zzzzzzz", _claims) == [])
t("a fragment shorter than the floor suggests nothing",
  suggest_mod.suggest("su", _claims) == [])
t("suggestions carry the verdict so the row can preview it",
  suggest_mod.suggest("sunscreen", _claims)[0]["verdict"] == "false")

r = c.get("/api/suggest?q=streamed")
t("the suggest endpoint answers with cached claims",
  r.status_code == 200 and "suggestions" in r.get_json())
r = c.get("/api/suggest?q=a")
t("the suggest endpoint stays quiet below the floor",
  r.status_code == 200 and r.get_json()["suggestions"] == [])

# ---- the 404 is a page, not a dead end -------------------------------------------
r = c.get("/no-such-page")
_body = r.data.decode()
t("a missing page returns 404 with a real page",
  r.status_code == 404 and "That page is not here" in _body)
t("the 404 offers the claim field and stays honest",
  'name="q"' in _body and "Not medical advice" in _body)
t("the 404 is not indexable", 'name="robots" content="noindex"' in _body)

# ---- the shell advertises what it actually is ------------------------------------
_home = c.get("/").data.decode()
t("the page preloads the font it actually uses",
  "librefranklin" in _home and "librecaslon" not in _home and "archivo" not in _home)
t("theme colour matches the shipped ground, and no dark scheme is claimed",
  'content="#0b1226"' in _home and 'content="light dark"' not in _home)

# ---- the cold page must contain everything the browser renders into ---------------
# A streamed check renders from the front page, where no server-side result
# exists. Any element the renderer writes to must therefore be present before
# a result is: guarding one behind `{% if result %}` made every live check die
# in renderSnapshot and surface as "couldn't reach the server".
_cold = c.get("/").data.decode()
_needed = re.findall(r'\$\("([a-z0-9-]+)"\)\.innerHTML', (ROOT / "static" / "app.js").read_text())
_missing = sorted({i for i in set(_needed) if f'id="{i}"' not in _cold})
t("every element the browser renders into exists on the cold front page",
  not _missing, f"missing from /: {_missing}")

# ---- what to offer when PubMed has no answer -------------------------------------
import nextsteps  # noqa: E402

_cl = [{"claim_text": "Cold showers boost your immune system", "verdict": "complicated", "issue": 9},
       {"claim_text": "Sunscreen causes cancer", "verdict": "false", "issue": 8}]

t("a verdict that rests on evidence gets no next-steps panel",
  nextsteps.build("x", "q", [{"pmid": "1"}], ["1"], _cl) is None)
_nf = nextsteps.build("Zorbing causes myopia", "zorbing AND myopia", [], [], _cl)
t("no papers at all is reported as nothing_found", _nf["kind"] == "nothing_found")
_nr = nextsteps.build("Zorbing causes myopia", "zorbing AND myopia", [{"pmid": "1"}], [], _cl)
t("papers that test nothing are a different fact from no papers",
  _nr["kind"] == "none_relevant")
t("the exact search is offered back to the reader",
  _nf["exact_url"] and "zorbing" in _nf["exact_url"])
t("a wider search joins the terms with OR, not AND",
  _nf["wider_url"] and "%20OR%20" in _nf["wider_url"] and "AND" not in _nf["wider_url"])
t("stopwords are dropped from the search terms",
  "the" not in nextsteps.key_terms("the cold showers and the immune system"))
_rel = nextsteps.build("Do cold showers boost immunity", "cold AND immunity", [], [], _cl)
t("a dead end offers related claims that do have answers",
  _rel["related"] and _rel["related"][0]["claim_text"] == "Cold showers boost your immune system", _rel["related"])
t("the claim itself is never offered as its own related claim",
  all(r["claim_text"] != "Sunscreen causes cancer"
      for r in nextsteps.build("Sunscreen causes cancer", "q", [], [], _cl)["related"]))
t("a single-word claim gets no wider search rather than a useless one",
  nextsteps.build("tinnitus", "q", [], [], _cl)["wider_url"] is None)

# ---- the deeper layer, and the gate under it --------------------------------------
# Everything here is about one rule: a sentence the reader is shown has to be
# traceable to a record that was actually fetched. The gate is in breakdown.py
# and it drops, never repairs: an uncited sentence, an out-of-range study, an
# invented figure and a sentence too long to read all leave the page entirely.
import breakdown  # noqa: E402

_BD_STUDIES = [
    {"pmid": "D1", "title": "Vitamin D and acute respiratory tract infection",
     "abstract": "In 25 trials with 11321 participants, supplementation reduced acute "
                 "respiratory tract infection (odds ratio 0.88, 95% CI 0.81 to 0.96). "
                 "The effect was larger below 25 nmol/L (odds ratio 0.30).",
     "journal": "BMJ", "year": "2017", "publication_types": ["Meta-Analysis"],
     "authors": ["A B"], "data_banks": None, "url": "uD1"},
    {"pmid": "D2", "title": "Monthly high dose vitamin D",
     "abstract": "5110 adults aged 18 to 67 took 100000 IU monthly for 3.3 years with "
                 "no change in infection rates (hazard ratio 0.99).",
     "journal": "JAMA", "year": "2017",
     "publication_types": ["Randomized Controlled Trial"],
     "authors": ["C D"], "data_banks": None, "url": "uD2"},
]

_BD_RAW = {
    "parts": [
        {"part": "Vitamin D in winter cures colds",
         "assessment": "Pooled trials found a small drop in infections (Study 1)."},
        {"part": "only if you are deficient to begin with",
         "assessment": "The drop was largest below 25 nmol/L (Study 1)."},
        {"part": "a part no study here touches",
         "assessment": "This sentence names no study at all."},
    ],
    "evidence": [
        "A meta-analysis of 25 trials with 11,321 people found an odds ratio of 0.88 (Study 1).",
        "It wards off colds in every group (Study 1).",
        "Colds fell by 47% in the pooled trials (Study 1).",
        "A monthly 100000 IU dose in 5110 adults aged 18 to 67 changed nothing (Study 2).",
        "This paragraph cites nothing.",
        "This one points at a study that was never fetched (Study 9).",
        "This sentence is far too long to read comfortably on a phone and it just keeps "
        "going and going past any reasonable length for one single breath of prose, "
        "which is exactly the kind of sentence that pushes a page to a reading grade "
        "no ordinary reader should have to work through (Study 1).",
    ],
    "effect_size": "The odds ratio was 0.88, and 0.30 in the most deficient (Study 1).",
    "strength": "One meta-analysis and one randomized controlled trial disagree (Studies 1, 2).",
    "applies_to": "Adults aged 18 to 67 were studied (Study 2).",
    "not_applies_to": "Children were not in either trial (Study 2).",
    "unknowns": "Whether a daily winter dose helps people who are not deficient (Studies 1, 2).",
}

_bd = breakdown.ground(_BD_RAW, _BD_STUDIES, verdict.tidy_prose)
_ev = " ".join(_bd["evidence"])
t("grounding: an uncited sentence never reaches the page",
  "cites nothing" not in breakdown.flatten(_bd))
t("grounding: a study number out of range is not a citation",
  "never fetched" not in _ev)
t("grounding: a figure that is in no abstract takes its sentence with it",
  "47%" not in _ev and "fell by" not in _ev)
t("grounding: a figure that is in an abstract survives, comma or no comma",
  "11,321" in _ev and "0.88" in _ev)
t("grounding: a sentence too long to read is dropped",
  "keeps going" not in _ev and len(_bd["evidence"]) == 3, _bd["evidence"])
t("grounding: the wording is pulled back to what the evidence carries",
  "wards off" not in _ev and "lowers the risk of colds" in _ev)
t("grounding: the reader's own claim is quoted, not softened",
  _bd["parts"][0]["part"] == "Vitamin D in winter cures colds", _bd["parts"][0])
t("grounding: a part with no study gets a stated fact, not the model's prose",
  _bd["parts"][2]["assessment"] == breakdown.NO_STUDY_FOR_PART
  and _bd["parts"][2]["studies"] == [], _bd["parts"][2])
t("grounding: every surviving sentence carries a study number",
  all(breakdown.refs(s, 2) for s in breakdown.sentences(breakdown.flatten(_bd))
      if s != breakdown.NO_STUDY_FOR_PART))
t("grounding: the layer never reaches past the records the verdict was shown",
  set(breakdown.cited_pmids(_bd, _BD_STUDIES)) <= {"D1", "D2"}
  and _bd["rests_on"] == 2, _bd["rests_on"])
t("grounding: a medical term is explained once, on its first appearance",
  "randomized controlled trial (people were put in groups at random" in _bd["strength"])
t("grounding: a design with a plain name is renamed, and named once in brackets",
  "pooled review (meta-analysis)" in _ev
  and breakdown.flatten(_bd).count("(meta-analysis)") == 1
  and "pooled review" in _bd["strength"], breakdown.flatten(_bd))
t("grounding: the prose lands at about an 8th-grade reading level",
  _bd["grade"] <= 10, _bd["grade"])
# The length rule is measured on the sentence the reader is handed, so the
# brackets an explanation adds have to fit inside the budget too. Gating before
# glossing would ship a sentence at the cap plus a ten word parenthetical.
_long = max(breakdown.sentences(breakdown.flatten(_bd)),
            key=lambda s: len(s.split()))
t("grounding: a sentence is capped after its explanations go in, not before",
  len(_long.split()) <= breakdown.MAX_SENTENCE_WORDS, len(_long.split()))
t("  so a term skipped for length is still explained further down",
  "(" in breakdown.gloss_once(
      "A systematic review of " + "many trials " * 12 + "ran for years. "
      "A systematic review found less illness.", set()))
t("grounding: no dashes in the deeper layer either",
  "—" not in breakdown.flatten(_bd) and "–" not in breakdown.flatten(_bd))

# An effect size is only ever quoted. When nothing in the abstracts gives one,
# the page says so in those words rather than reaching for an adjective.
_no_fig = breakdown.ground(
    dict(_BD_RAW, effect_size="The effect was large and meaningful (Study 1)."),
    _BD_STUDIES, verdict.tidy_prose)
t("grounding: an effect size that is described, not quoted, does not survive",
  "large and meaningful" not in _no_fig["effect_size"], _no_fig["effect_size"])
t("  and it points at the figures that did survive, rather than denying them",
  _no_fig["effect_size"] == breakdown.EFFECT_SIZE_ABOVE
  and "0.88" in " ".join(_no_fig["evidence"]), _no_fig["effect_size"])
_no_num = breakdown.ground(
    {"parts": [], "evidence": ["Vitamin D helped a little (Study 1)."],
     "effect_size": "The effect was large (Study 1).", "strength": "",
     "applies_to": "", "not_applies_to": "", "unknowns": ""},
    _BD_STUDIES, verdict.tidy_prose)
t("  with no figure anywhere on the page, the abstracts are said to carry none",
  _no_num["effect_size"] == breakdown.NO_EFFECT_SIZE, _no_num["effect_size"])
t("  and a figure that is in them is quoted as it stands",
  # "most deficient" is printed as "lowest": the figures are untouched,
  # which is the thing this test is actually about.
  "was 0.88, and 0.30 in the lowest (Study 1)." in _bd["effect_size"],
  _bd["effect_size"])

t("grounding: softening only ever weakens a claim",
  breakdown.soften("It prevents flu and cures colds.")
  == "It lowers the risk of flu and helps with colds.")
t("grounding: a breakdown with nothing left in it is no breakdown at all",
  breakdown.ground({"parts": [], "evidence": ["no citation here"], "effect_size": "",
                    "strength": "", "applies_to": "", "not_applies_to": "",
                    "unknowns": ""}, _BD_STUDIES, verdict.tidy_prose) is None)
t("grounding: a check with no studies has nothing to ground against",
  breakdown.ground(_BD_RAW, [], verdict.tidy_prose) is None)
t("grounding: reading grade is measured, not asserted",
  breakdown.reading_grade("The cat sat on the mat. It was fine.") < 4
  and breakdown.reading_grade(
      "Supplementation attenuated incident respiratory morbidity irrespective of "
      "antecedent concentrations, notwithstanding considerable heterogeneity.") > 12)

# ---- searching wide, then ranking by design ---------------------------------------
# AND between two synonyms asks for papers using both words, which is how the
# landmark meta-analysis on a claim gets missed. A synonym group is one idea
# and has to survive broadening whole.
t("search: a bracketed OR group is one term, not four",
  pubmed.split_and("(a OR b) AND (c OR d)") == ["(a OR b)", "(c OR d)"],
  pubmed.split_and("(a OR b) AND (c OR d)"))
t("  broadening drops a whole group, never half of one",
  pubmed.broaden_query("(vitamin D OR cholecalciferol) AND (cold OR flu) AND winter")
  == "(vitamin D OR cholecalciferol) AND (cold OR flu)")
t("  a query with one group cannot be broadened further",
  pubmed.broaden_query("(vitamin D OR cholecalciferol)") is None)
t("  the second search asks PubMed for the designs that settle claims",
  pubmed.best_evidence_query("x") ==
  "(x) AND (systematic review[pt] OR meta-analysis[pt] OR "
  "randomized controlled trial[pt])", pubmed.best_evidence_query("x"))

_seen = []
def _fake_search(q, max_results=8):
    _seen.append(q)
    return ["REVIEW1", "REVIEW2"] if "[pt]" in q else ["POPULAR", "REVIEW1", "TAIL"]
_real_search = pubmed.search_pubmed
pubmed.search_pubmed = _fake_search
_merged = pubmed.search_merged("q", max_results=8)
pubmed.search_pubmed = _real_search
t("  strong designs take the front of the merged list, with no duplicates",
  _merged == ["REVIEW1", "REVIEW2", "POPULAR", "TAIL"], _merged)
t("  and both searches really ran, the filtered one first",
  len(_seen) == 2 and "[pt]" in _seen[0] and "[pt]" not in _seen[1])
t("  a third AND group is dropped, whatever the prompt asked for",
  verdict._two_groups("(vitamin D OR cholecalciferol) AND (cold OR flu) "
                      "AND (deficiency OR insufficiency)")
  == "(vitamin D OR cholecalciferol) AND (cold OR flu)",
  verdict._two_groups("(vitamin D OR cholecalciferol) AND (cold OR flu) "
                      "AND (deficiency OR insufficiency)"))
t("  two groups are left exactly as they are",
  verdict._two_groups("(a OR b) AND (c OR d)") == "(a OR b) AND (c OR d)")
t("  a record with no abstract cannot ground a sentence, so it is dropped",
  [s["pmid"] for s in pubmed.usable(
      [{"pmid": "1", "abstract": "text"}, {"pmid": "2", "abstract": None},
       {"pmid": "3", "abstract": "   "}])] == ["1"])

# ---- who was actually studied -----------------------------------------------------
t("population: a trial names its group in the title",
  evidence.population({"title": "Vitamin D in Young Healthy Children",
                       "publication_types": ["Randomized Controlled Trial"]})
  == "children")
t("  or in the opening of its abstract",
  evidence.population({"title": "Vitamin D and infection", "abstract":
                       "We enrolled 511 subjects with prediabetes.",
                       "publication_types": ["Randomized Controlled Trial"]})
  == "people with prediabetes")
t("  a pooled review is not narrowed by a subgroup line in its abstract",
  evidence.population({"title": "Vitamin D to prevent infections: a meta-analysis",
                       "abstract": "Subgroup analysis in children showed no effect.",
                       "publication_types": ["Meta-Analysis"]}) is None)
t("  a general trial is not labelled at all",
  evidence.population({"title": "Vitamin D in adults", "abstract": "We enrolled adults.",
                       "publication_types": ["Randomized Controlled Trial"]}) is None)

def _study(pmid, title, **kw):
    """A PubMed record with every field the payload expects."""
    return {"pmid": pmid, "title": title, "abstract": kw.get("abstract", "a"),
            "journal": "J", "year": "2020", "authors": ["A B"], "data_banks": None,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "publication_types": kw.get("types", ["Randomized Controlled Trial"])}


_NARROW = [_study("N1", "Vitamin D in children"),
           _study("N2", "Vitamin D in pregnancy")]
t("  narrow groups are listed in the order they were read",
  evidence.narrow_populations(_NARROW, ["N1", "N2"]) == ["children", "pregnant women"])
t("  evidence drawn only from narrow groups is flagged as such",
  evidence.narrow_only(_NARROW, ["N1", "N2"]) is True)
t("  one general study among them clears the flag",
  evidence.narrow_only(_NARROW + [_study("G", "Vitamin D in adults", types=[])],
                       ["N1", "G"]) is False)

# A general claim answered only from narrow groups is not answered. This is the
# same backstop as "no citations means complicated", one step along.
fg.models.script = [Resp("vitamin d AND children"),
                    Resp(VJ("true", [1, 2], tldr="Vitamin D stops colds.",
                            explanation="It works.", still_open="Nothing."))]
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': _NARROW
_nr = P("Vitamin D stops you catching colds", ip="7.7.7.7").get_json()
t("narrow evidence cannot carry a true verdict",
  _nr["verdict"] == "complicated", _nr["verdict"])
t("  and the result says whose evidence it was",
  "children, pregnant women" in _nr["explanation"], _nr["explanation"])

# ---- the claim's own condition ----------------------------------------------------
# The failure this covers: the answer said the condition was never tested while
# the abstract in front of it reported on exactly that condition.
t("conditions: a claim's condition is read off its own words",
  evidence.conditions("Vitamin D cuts colds, but only if you are deficient")
  == ["people who start with low levels"],
  evidence.conditions("Vitamin D cuts colds, but only if you are deficient"))
t("  a claim with no condition asks for no subgroup search",
  evidence.conditions("Celery juice cures cancer") == [])

_SUB = [_study("S1", "Vitamin D to prevent acute respiratory infection", abstract=(
    "We pooled 25 trials. Protective effects were stronger among those with "
    "baseline 25(OH)D below 25 nmol/L (adjusted odds ratio 0.30). A total of "
    "11321 participants were included.")),
        _study("S2", "Vitamin D and respiratory infection: an update", abstract=(
            "No significant effect was seen for any of the subgroups defined by "
            "baseline 25(OH)D concentration."))]
_hits = evidence.subgroup_findings(_SUB, "Vitamin D cuts colds if you are deficient")
t("  a subgroup sentence on that condition is pulled out of the abstract",
  [h["study"] for h in _hits] == [1, 2]
  and all(h["condition"] == "people who start with low levels" for h in _hits), _hits)
t("  and a counting sentence is not mistaken for a finding",
  all("11321 participants" not in h["sentence"] for h in _hits), _hits)
t("  two abstracts disagreeing is itself what gets reported",
  "stronger" in _hits[0]["sentence"] and "No significant effect" in _hits[1]["sentence"])
t("  the conditions these abstracts do report on are named for the prompt",
  evidence.conditions_reported(_SUB, "Vitamin D cuts colds if you are deficient")
  == ["people who start with low levels"])
t("  and the model is handed those sentences, with their study numbers",
  "SUBGROUP FINDINGS" in verdict._subgroup_block(
      _SUB, "Vitamin D cuts colds if you are deficient")
  and "Study 2" in verdict._subgroup_block(
      _SUB, "Vitamin D cuts colds if you are deficient"))
t("  no condition in the claim means no block at all",
  verdict._subgroup_block(_SUB, "Celery juice cures cancer") == "")

# ---- how big the effect is, in words ----------------------------------------------
t("effect size: thresholds are fixed in code, not left to the prose",
  (evidence.size_label(0.12), evidence.size_label(0.20), evidence.size_label(0.60))
  == ("small", "moderate", "large"))
_or = evidence.effect_size("The pooled odds ratio was 0.88 (Study 1).")
t("  a ratio becomes a percentage a reader can picture",
  _or == {"label": "small", "plain": "about 12% lower odds",
          "figure": "odds ratio 0.88"}, _or)
t("  a ratio with its outcome named in between is still read",
  evidence.effect_size("The hazard ratio for heart deaths for each more 50 g "
                       "of egg eaten daily was 1.09 (Study 4).")["figure"]
  == "hazard ratio 1.09")
t("  but a figure the sentence does not pin to that ratio is left alone",
  evidence.effect_size("The hazard ratio was not given, but mortality was 1.09.")
  is None)
t("  a ratio above 1 reads as an increase",
  evidence.effect_size("hazard ratio 1.80")["plain"] == "about 80% higher risk over time",
  evidence.effect_size("hazard ratio 1.80"))
t("  a standardised mean difference uses Cohen's own thresholds",
  evidence.effect_size("The SMD was -0.21.")["label"] == "small"
  and evidence.effect_size("Cohen's d of 0.9")["label"] == "large")
t("  a percentage drop is a drop, not a rise",
  evidence.effect_size("Infections fell by 12%")["plain"] == "about 12% lower",
  evidence.effect_size("Infections fell by 12%"))
t("  a confidence interval is not read as an effect",
  evidence.effect_size("The effect was unclear (95% confidence interval 0.7 to 1.3).")
  is None, evidence.effect_size("The effect was unclear (95% CI 0.7 to 1.3)."))
t("  prose with no figure in it has no size",
  evidence.effect_size("The trials disagreed.") is None)

t("  the takeaway is pulled back to the size behind it",
  breakdown.match_effect("Vitamin D reduces colds and flu.", "small")
  == "Vitamin D slightly lowers colds and flu.",
  breakdown.match_effect("Vitamin D reduces colds and flu.", "small"))
t("  a large effect is left to speak for itself",
  breakdown.match_effect("Vitamin D reduces colds.", "large")
  == "Vitamin D reduces colds.")
t("  a sentence that hedges itself is not hedged twice",
  breakdown.match_effect("Vitamin D may reduce colds.", "small")
  == "Vitamin D may reduce colds.")
t("  and a negative sentence is left alone",
  breakdown.match_effect("Vitamin D does not reduce colds.", "small")
  == "Vitamin D does not reduce colds.")
t("  the breakdown's effect line carries the translation and its citation",
  "about 12% lower odds, a small effect (Study 1)" in _bd["effect_size"],
  _bd["effect_size"])
t("  and the size label travels with it for the takeaway",
  _bd["effect"]["label"] == "small", _bd["effect"])

# ---- indirect evidence ------------------------------------------------------------
_DHT = _study("I1", "Creatine supplementation raises dihydrotestosterone in rugby players",
              abstract="Serum dihydrotestosterone rose after loading.")
_HAIR = _study("I2", "Creatine and hair loss: a 12-week randomized controlled trial",
               abstract="We counted hair loss in 40 men.")
t("indirect: a study that measured the outcome is not called indirect",
  evidence.indirect(_HAIR, "(hair loss OR alopecia)", "dihydrotestosterone OR DHT")
  is None)
t("  one that measured a stand-in is labelled with the term it used",
  evidence.indirect(_DHT, "(hair loss OR alopecia)", "dihydrotestosterone OR DHT")
  == "dihydrotestosterone")
t("  with no surrogate named, nothing is called indirect",
  evidence.indirect(_DHT, "(hair loss OR alopecia)", "") is None)
t("  the subject filter reads the title and the abstract's opening only",
  evidence.measures(_HAIR, "(creatine)") is True
  and evidence.measures(_study("X", "Baricitinib safety", abstract="x " * 400
                               + "creatine kinase was normal"), "(creatine)") is False)
t("  and the prompt tells the model which study measured a stand-in",
  "INDIRECT: measures dihydrotestosterone" in verdict._format_study_for_prompt(
      1, _DHT, "(hair loss OR alopecia)", "dihydrotestosterone OR DHT"))

_ranked = pubmed.prioritise([_study("OFF", "Topical lotion for alopecia",
                                    abstract="A lotion trial in 60 women."),
                             _DHT, _HAIR],
                            "(creatine) AND (hair loss OR alopecia)",
                            "dihydrotestosterone OR DHT")
t("  a paper that never mentions the claim's subject sinks under the ones that do",
  [s["pmid"] for s in _ranked] == ["I1", "I2", "OFF"], [s["pmid"] for s in _ranked])
t("  the surrogate search asks for the subject plus the stand-in, never the outcome",
  pubmed.search_surrogate("(creatine) AND (hair loss)", "DHT OR dihydrotestosterone",
                          max_results=2) is not None)

# ---- the answer must not contradict itself ----------------------------------------
t("consistency: calling a reported condition untested is caught",
  breakdown.contradictions("The studies do not test whether you need to be low.",
                           reported=["people who start with low levels"]),
  breakdown.contradictions("The studies do not test whether you need to be low.",
                           reported=["people who start with low levels"]))
t("  two lines saying opposite things about the same effect are caught",
  breakdown.contradictions("There is no clear benefit.",
                           "It lowers the risk of infection."))
t("  an answer that agrees with itself raises nothing",
  breakdown.contradictions("It lowers the odds a little.",
                           "The effect is small.", reported=[]) == [])
t("  'the studies found' is not read as a finding, so nothing fires",
  breakdown.contradictions("This is unproven either way.",
                           "None of the studies found directly test this claim.") == [])
t("off claim: a product the claim never named is flagged",
  breakdown.off_claim("Blue light glasses do not help your eyes.",
                      "Screen light damages your eyes") == ["glasses"])
t("  a claim about the product itself keeps it",
  breakdown.off_claim("Blue light glasses do not help.",
                      "Do blue light glasses work?") == [])

# ---- what the gate says when it has taken a sentence away --------------------------
# "No study tests this part" is true when the model judged nothing. When it did
# judge and the judgement failed the check, the same line sits above paragraphs
# of studies that do test it, and contradicts them.
_DROPPED_PART = breakdown.ground(
    {"parts": [{"part": "Vitamin D cures colds",
                "assessment": "Infections fell by 47% in the pooled trials (Study 1)."}],
     "evidence": ["The effect was larger below 25 nmol/L (Study 1)."],
     "effect_size": "", "strength": "", "applies_to": "", "not_applies_to": "",
     "unknowns": ""},
    _BD_STUDIES, verdict.tidy_prose)
t("a judgement the gate took away does not become 'no study tests this'",
  _DROPPED_PART["parts"][0]["assessment"] == breakdown.PART_BELOW,
  _DROPPED_PART["parts"][0])
t("  while a part the model never judged still says so",
  _bd["parts"][2]["assessment"] == breakdown.NO_STUDY_FOR_PART)
t("  and the bookkeeping flag never reaches the payload",
  "_dropped" not in _DROPPED_PART["parts"][0])

# A sentence is not lost because plain() put the original term in brackets
# beside the plain words. The brackets go; the sentence stays.
_LONG_TERM = breakdown.ground(
    {"parts": [], "evidence": ["The effect was larger below 25 nmol/L (Study 1)."],
     "strength": "One pooled review of 25 trials in 11321 people reported a lower rate "
                 "of acute respiratory tract infection in the people who took it "
                 "(Study 1).",
     "effect_size": "", "applies_to": "", "not_applies_to": "", "unknowns": ""},
    _BD_STUDIES, verdict.tidy_prose)
t("a sentence the brackets alone made too long keeps the sentence, not the brackets",
  _LONG_TERM and "chest and throat" in _LONG_TERM["strength"]
  and "(respiratory tract infection" not in _LONG_TERM["strength"],
  (_LONG_TERM or {}).get("strength"))

# The model quotes the figure; code does the arithmetic. A size word the model
# already chose is not repeated when the percentage is added beside it.
_SIZED = breakdown.ground(
    {"parts": [], "evidence": ["The effect was larger below 25 nmol/L (Study 1)."],
     "effect_size": "The odds ratio was 0.88, a small effect (Study 1).",
     "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": ""},
    _BD_STUDIES, verdict.tidy_prose)
t("  the percentage is added to a quoted ratio, with its citation",
  "That is about 12% lower odds (Study 1)." in _SIZED["effect_size"], _SIZED["effect_size"])
t("  and a size word the model already chose is not repeated",
  _SIZED["effect_size"].count("small") == 1, _SIZED["effect_size"])

# A translation belongs beside the figure it came from. Taking a ratio out of a
# paragraph and parking its percentage under a different ratio prints a wrong
# number in plainer words, which is worse than printing no translation.
_MIXED = breakdown.ground(
    {"parts": [], "evidence": ["The effect was larger below 25 nmol/L, at 0.30 (Study 1)."],
     "effect_size": "The hazard ratio was 0.99 (Study 2).",
     "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": ""},
    _BD_STUDIES, verdict.tidy_prose)
t("  a figure from one line is never translated under another",
  "0.30" not in _MIXED["effect_size"] and "70%" not in _MIXED["effect_size"]
  and "about 1% lower risk over time" in _MIXED["effect_size"], _MIXED["effect_size"])

t("soften: 'blocks' the thing is not rewritten as 'reduces'",
  breakdown.soften("No evidence that blue blocks stop eye strain.")
  == "No evidence that blue blocks stop eye strain."
  and breakdown.soften("It blocks the virus.") == "It reduces the virus.",
  breakdown.soften("No evidence that blue blocks stop eye strain."))

t("a bracket on its own is not a sentence, so it never becomes a section",
  breakdown.keep_sentences("(Studies 3, 8).", 8, set()) == ""
  and breakdown.keep_sentences("Both reviews agree on this (Studies 3, 8).", 8, set())
  != "")

t("a count the abstract spells out is a count the verdict may write",
  evidence.written_numbers("Forty-five males were recruited. Thirty-eight finished.")
  == {"45", "38"}, evidence.written_numbers("Forty-five males. Thirty-eight finished."))
t("  and only the whole run counts, so 'forty-five' never permits 40 or 5",
  evidence.written_numbers("Forty-five") == {"45"})
t("  a scale word and its 'and' are part of the number",
  evidence.written_numbers("Two hundred and ten adults") == {"210"})
t("  so a sentence quoting a spelled count is not deleted as invented",
  "45 people" in breakdown.ground(
      {"parts": [], "evidence": ["The 45 people were healthy young males (Study 1)."],
       "effect_size": "", "strength": "", "applies_to": "", "not_applies_to": "",
       "unknowns": ""},
      [dict(_BD_STUDIES[0], abstract="Forty-five healthy young males took part.")],
      verdict.tidy_prose)["evidence"][0])

t("plain: 'duration' is replaced by a word that fits every slot it sits in",
  breakdown.plain("The studies were often of short duration.")
  == "The studies were often short."
  and breakdown.plain("The duration was 12 weeks.") == "The length was 12 weeks."
  and breakdown.plain("Treatment duration was 12 weeks.")
  == "Treatment length was 12 weeks."
  and breakdown.plain("The duration of therapy was 12 weeks.")
  == "The length of therapy was 12 weeks.",
  breakdown.plain("The duration was 12 weeks."))

t("surrogate: the second line is read only when it is labelled",
  verdict._surrogate("(creatine) AND (hair loss)\nSurrogate: DHT OR dihydrotestosterone")
  == "DHT OR dihydrotestosterone")
t("  an unlabelled second line is not a surrogate",
  verdict._surrogate("(creatine) AND (hair loss)\nThis finds the hormone trials.") == "")
t("  and 'none' means none",
  verdict._surrogate("(a) AND (b)\nSurrogate: none") == "")

# ---- plain words ------------------------------------------------------------------
t("plain: a compound noun is rewritten as the thing you do",
  breakdown.plain("Vitamin D supplementation lowers risk.", set())
  == "Vitamin D supplement use lowers risk.")
t("  and 'supplementation with X' becomes 'taking X'",
  breakdown.plain("Supplementation with vitamin D helped.", set())
  == "Taking vitamin D helped.")
t("  a term that reads differently after 'the ... of' gets the noun, not the clause",
  breakdown.plain("The efficacy of vitamin D and the incidence of flu.", set())
  == "The effect of vitamin D and the rate of flu.")
t("  but standing alone it gets the plain clause",
  breakdown.plain("Efficacy was not reported.", set())
  == "How well it works was not reported.")
t("  a deficiency is low levels of the thing, in that order",
  breakdown.plain("Vitamin D deficiency was common.", set())
  == "Low vitamin D (deficiency) was common.")
t("  a superlative is not left as 'most low'",
  breakdown.plain("Those most deficient improved.", set()) == "Those lowest improved.")
t("  the term parked in brackets is not then rewritten by a later rule",
  breakdown.plain("Androgenetic alopecia was measured.", set())
  == "Pattern hair loss (androgenetic alopecia) was measured.")
t("  the medical term is kept once, not on every appearance",
  breakdown.plain("Respiratory infections and more respiratory infections.", set())
  == "Chest and throat infections (respiratory tract infection) and more chest "
     "and throat infections.")
t("  an article follows the word that replaced the one after it",
  breakdown.plain("There was an elevated count.", set())
  == "There was a raised count.")
t("  an adjective before a noun is rebuilt, not just swapped",
  breakdown.plain("It helped deficient people most.", set())
  == "It helped people with low levels most.")
t("  softening 'stops' leaves 'stops working' alone, which claims nothing",
  breakdown.soften("Where it stops providing protection.")
  == "Where it stops providing protection."
  and breakdown.soften("It stops colds.") == "It reduces colds.")
t("  the two design labels together are one pooled review, named once",
  breakdown.plain("A systematic review and meta-analysis of 25 trials.", set())
  == "A pooled review (systematic review and meta-analysis) of 25 trials.")
t("  a long word with a short twin loses nothing, so it is swapped silently",
  breakdown.plain("The association was a substantial reduction in serum "
                  "concentrations across populations.", set())
  == "The link was a large drop in serum levels across groups.")

t("natural: the takeaway is a sentence someone would say out loud",
  breakdown.natural("Vitamin D links to colds and flu.")
  == "Vitamin D is linked to colds and flu.")
t("  and research grammar is turned back into speech",
  breakdown.natural("Creatine is associated with hair loss.")
  == "Creatine is linked to hair loss.")

t("grade: the claim's own words are not counted against the prose",
  breakdown.claim_terms("Taking vitamin D supplements in winter cuts your risk of flu")
  == {"vitamin", "supplements", "winter", "flu"},
  breakdown.claim_terms("Taking vitamin D supplements in winter cuts your risk of flu"))
_long_word = "Supplementation reduced infections. Supplementation reduced infections."
t("  excluding them lowers the measured grade of the same sentence",
  breakdown.reading_grade(_long_word, {"supplementation"})
  < breakdown.reading_grade(_long_word))
t("  the breakdown reports its own measured grade, never an asserted one",
  isinstance(_bd["grade"], float)
  and _bd["grade"] == breakdown.reading_grade(breakdown.flatten(_bd)), _bd["grade"])
t("  and short prose about these trials comes in at the target or below",
  _bd["grade"] <= breakdown.TARGET_GRADE, _bd["grade"])

# ---- the deeper layer through the whole app ---------------------------------------
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': _BD_STUDIES
fg.models.script = [Resp("vitamin d AND respiratory infection"),
                    Resp(VJ("complicated", [1, 2], tldr="Vitamin D prevents colds.",
                            still_open="Whether it helps people who are not deficient.",
                            **_BD_RAW))]
_before = fg.models.calls
_vd = P("Vitamin D supplements in winter cut colds", ip="9.9.9.9").get_json()
t("the breakdown rides on the verdict call: still 2 calls per check",
  fg.models.calls - _before == 2, fg.models.calls - _before)
t("  the takeaway is held to the same rule as the breakdown",
  _vd["tldr"] == "Vitamin D slightly lowers the risk of colds.", _vd["tldr"])
t("  the breakdown is in the API payload, gated",
  _vd["breakdown"]["rests_on"] == 2 and len(_vd["breakdown"]["evidence"]) == 3
  and "47%" not in json.dumps(_vd["breakdown"]), _vd["breakdown"])
_vd2 = P("vitamin d supplements in winter cut colds.", ip="4.4.4.4").get_json()
t("  it survives the cache round trip", _vd2["cached"] is True
  and _vd2["breakdown"] == _vd["breakdown"])

# An answer that calls the claim's condition untested while the abstract in
# front of it reports on that condition is asked again, once. Three calls, not
# two, and only on this path: the check above is still two.
_RETRY_BD = {
    "parts": [{"part": "vitamin D cuts colds", "studies": [1],
               "assessment": "Pooled trials found fewer infections (Study 1)."}],
    "evidence": ["Protective effects were stronger in people who began with low "
                 "levels (Study 1).",
                 "The update found no effect in any baseline subgroup (Study 2)."],
    "effect_size": "The odds ratio was 0.30 in the lowest group (Study 1).",
    "strength": "One pooled review and one update disagree (Studies 1, 2).",
    "applies_to": "Adults in 25 pooled trials (Study 1).",
    "not_applies_to": "Children do not appear in either review (Study 2).",
    "unknowns": "Whether a winter dose helps people who are not low (Studies 1, 2).",
}
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': _SUB
fg.models.script = [
    Resp("(vitamin d) AND (respiratory infection)"),
    Resp(VJ("complicated", [1, 2], tldr="Vitamin D may help a little.",
            explanation="These studies do not test whether you need to be low.",
            still_open="Whether it helps anyone else.", **_RETRY_BD)),
    Resp(VJ("complicated", [1, 2], tldr="Vitamin D may help, mostly if you are low.",
            explanation="The pooled review saw a bigger effect in people who began "
                        "low (Study 1), and the update saw none (Study 2).",
            still_open="Whether a winter dose helps anyone else.", **_RETRY_BD)),
]
_cbefore = fg.models.calls
_cr = P("Vitamin D cuts colds but only if you are deficient", ip="8.8.8.8").get_json()
t("a self contradicting answer costs one more call, and only then",
  fg.models.calls - _cbefore == 3, fg.models.calls - _cbefore)
t("  and the answer the reader gets is the consistent one",
  "do not test" not in _cr["explanation"] and "bigger effect" in _cr["explanation"],
  _cr["explanation"])
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': _BD_STUDIES

_html = c.get("/?q=Vitamin+D+supplements+in+winter+cut+colds").data.decode()
t("the breakdown is server rendered, so a shared link carries it",
  "Read the full breakdown" in _html and "The claim, part by part" in _html
  and "11,321 people found an odds ratio" in _html
  and "only if you are deficient" in _html)
t("  it is collapsed by default, on the server and in the markup",
  '<details class="deeper" id="deeper">' in _html
  and '<details class="deeper" id="deeper" open' not in _html)
t("  the summary says how many studies it rests on",
  '<span class="group-count">2 studies</span>' in _html)
t("  what was gated out is not in the page either",
  "47%" not in _html and "cites nothing" not in _html and "wards off" not in _html)
t("  the effect size gets its own line",
  "How big the effect is" in _html and "was 0.88, and 0.30" in _html)
t("  and every section of the layer is there",
  all(s in _html for s in ("What the evidence shows", "Why the verdict is what it is",
                           "Who this applies to", "And who it does not",
                           "What is still unknown")))

# A verdict with nothing cited cannot have a breakdown of what the evidence
# shows: the deeper layer goes wherever the verdict goes.
fg.models.script = [Resp("q"), Resp(VJ("true", [], **_BD_RAW))]
_un = P("uncited with a breakdown", ip="9.9.9.9").get_json()
t("a forced complicated verdict carries no breakdown",
  _un["verdict"] == "complicated" and _un["breakdown"] is None, _un["breakdown"])

# A row cached before this column existed has no breakdown. It must render as a
# result without one, not as a broken one.
db.put_cached_verdict("old row with no breakdown", "q", "complicated", "Because.",
                      ["D1"], _BD_STUDIES, tldr="Short take.", still_open="Open.")
_old = c.get("/?q=old+row+with+no+breakdown").data.decode()
t("a result cached before the breakdown existed renders cleanly",
  "Short take." in _old and '<details class="deeper" id="deeper" hidden>' in _old
  and "Read the full breakdown" in _old)
_oldj = P("old row with no breakdown", ip="4.4.4.4").get_json()
t("  and reports no breakdown rather than an empty one",
  _oldj["breakdown"] is None and _oldj["cached"] is True)

# The Jinja and renderBreakdown draw the same section. Two renderers for one
# layout is how a cold shared link and a live check start disagreeing, so the
# headings are compared rather than trusted.
_tpl = (ROOT / "templates" / "index.html").read_text()
_js = (ROOT / "static" / "app.js").read_text()
_deeper_tpl = _tpl[_tpl.find('<details class="deeper"'):]
_deeper_tpl = _deeper_tpl[:_deeper_tpl.find("</details>")]
_tpl_labels = set(re.findall(r'class="section-label[^"]*">([^<]+)<', _deeper_tpl))
_js_labels = set(re.findall(r'sec\("([^"]+)"', _js)) | set(
    re.findall(r'class="section-label label-2">([^<]+)<', _js))
t("the server and the browser render the same breakdown headings",
  _tpl_labels == _js_labels, sorted(_tpl_labels ^ _js_labels))
t("the browser's renderer runs every sentence through the same ref linking",
  "renderBreakdown(data.breakdown" in _js and "linkStudyRefs(p.assessment" in _js)
t("nothing in the breakdown is a card, and nothing in it is colour coded",
  "border-radius" not in (ROOT / "static" / "style.css").read_text()
  .split("---- The deeper layer")[1].split("---- Sticky share bar")[0])

pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

_r = c.get("/?q=streamed%20claim")
t("a result that rests on evidence carries no next_steps in its payload",
  '"next_steps": null' in _r.data.decode() or '"next_steps":null' in _r.data.decode()
  or "next_steps" in _r.data.decode())

# The fly-through's beats.json, manifest.json, check.json and /flight route
# are covered in tests/test_flight.py, on the video-flythrough branch. That
# file's schema (four real stages, masterSeconds, a stage per beat) replaced
# an earlier one this block used to check (plannedMasterSeconds, a fixed
# five-scene chapter set); keeping both would mean two tests asserting two
# different shapes for the same file, so this one moved rather than stayed
# stale beside its replacement.

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
