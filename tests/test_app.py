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
pubmed.search_and_fetch = lambda q, max_results=8: STUDIES


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


def _fb(q, max_results=8):
    calls.append(q)
    return [] if q.count(" AND ") == 2 else STUDIES


pubmed.search_and_fetch = _fb
fg.models.script = [Resp("a AND b AND c"), Resp(VJ("true", [1]))]
d = P("broaden me", ip="7.7.7.7").get_json()
t("broadened search used & flagged", d["search_broadened"] is True and d["search_query_used"] == "a AND b"
  and calls == ["a AND b AND c", "a AND b"], d)
pubmed.search_and_fetch = lambda q, max_results=8: STUDIES

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
pubmed.search_and_fetch = lambda q, max_results=8: (_ for _ in ()).throw(requests.ConnectionError("down"))
fg.models.script = [Resp("q")]
r = P("err f"); t("PubMed down -> 502", r.status_code == 502 and "PubMed" in r.get_json()["error"])
t("failed checks not cached", all(db.get_cached_verdict(x, 24) is None for x in ("err a", "err b", "err c", "err f")))
pubmed.search_and_fetch = lambda q, max_results=8: STUDIES

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
t("uncached claim -> generic og", 'og:title" content="Claim Checker"' in html3)
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

_r = c.get("/?q=streamed%20claim")
t("a result that rests on evidence carries no next_steps in its payload",
  '"next_steps": null' in _r.data.decode() or '"next_steps":null' in _r.data.decode()
  or "next_steps" in _r.data.decode())

# ---- the fly-through timeline ----------------------------------------------------
# beats.json drives a pinned canvas. A gap, an overlap or a missing chapter would
# show as a stall or a blank caption mid-scroll, which is exactly the kind of fault
# that is invisible until someone scrolls the whole hero on a phone.
_beats_path = ROOT / "static" / "flight" / "beats.json"
if _beats_path.exists():
    _bt = json.loads(_beats_path.read_text())
    _bs = _bt["beats"]
    t("every beat has an id, a scroll distance and a chapter key",
      all(b.get("id") and isinstance(b.get("vh"), int) and "chapter" in b for b in _bs))
    t("beat ids are unique", len({b["id"] for b in _bs}) == len(_bs))
    t("every beat is either a footage range or a held frame, never both",
      all(("from" in b) != ("hold" in b) for b in _bs), [b["id"] for b in _bs if ("from" in b) == ("hold" in b)])
    _segs = [(b["from"], b["to"]) for b in _bs if "from" in b]
    t("footage ranges run forwards", all(a < z for a, z in _segs))
    t("footage ranges are contiguous, no gaps or overlaps",
      all(abs(_segs[i][1] - _segs[i + 1][0]) < 1e-6 for i in range(len(_segs) - 1)),
      [(_segs[i][1], _segs[i + 1][0]) for i in range(len(_segs) - 1)
       if abs(_segs[i][1] - _segs[i + 1][0]) >= 1e-6])
    t("the timeline covers the whole planned master",
      _segs[0][0] == 0 and abs(_segs[-1][1] - _bt["plannedMasterSeconds"]) < 1e-6)
    t("every chapter a beat names is defined",
      all(b["chapter"] in _bt["chapters"] for b in _bs if b["chapter"]))
    t("every defined chapter is used by at least one beat",
      set(_bt["chapters"]) == {b["chapter"] for b in _bs if b["chapter"]})
    t("mobile focusX stays inside the frame",
      all(0 <= b.get("mobile", {}).get("focusX", 0.5) <= 1 for b in _bs))
    t("mobile pacing is shorter than desktop, never longer",
      all(b.get("mobile", {}).get("vh", b["vh"]) <= b["vh"] for b in _bs))
    t("the opening beat holds a still so the claim input is usable first",
      _bs[0].get("hold") == 0 and _bs[0]["vh"] >= 60)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
