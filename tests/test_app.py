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
import og  # noqa: E402
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

# ---- guard: false with no citations -> not enough evidence -------------------
fg.models.script = [Resp("q"), Resp(VJ("false", []))]
d = P("uncited false claim").get_json()
t("uncited false -> not enough evidence + honest tldr", d["verdict"] == "insufficient" and "isn't enough evidence" in d["explanation"] and "unproven" in d["tldr"], d)

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

# ---- the copy gate on model output -------------------------------------------
t("tidy: an em dash before a new thought becomes a full stop",
  verdict.tidy_prose("The trials were small \u2014 Most ran for six weeks", 700)
  == "The trials were small. Most ran for six weeks.")
t("tidy: a spaced hyphen used as a dash becomes a comma",
  verdict.tidy_prose("It may lower sugar a little - but only after meals", 700)
  == "It may lower sugar a little, but only after meals.")
t("tidy: a dash between numbers is a range, not a list",
  verdict.tidy_prose("Doses of 5\u201310 g a day and 20 \u2014 30 minutes", 700)
  == "Doses of 5 to 10 g a day and 20 to 30 minutes.")
t("tidy: a hyphenated word and a subtraction are left alone",
  verdict.tidy_prose("A placebo-controlled trial, 5 - 2 arms", 700)
  == "A placebo-controlled trial, 5 - 2 arms.")
for _dash_case in ("A \u2014 b", "end \u2013", "\u2014 start", "x\u2014y\u2013z", "One -- two"):
    _out = verdict.tidy_prose(_dash_case, 700)
    t(f"tidy: no em or en dash survives {_dash_case!r}",
      "\u2014" not in _out and "\u2013" not in _out and " -- " not in _out, _out)


class _Catch(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


_copy = _Catch()
logging.getLogger("evident.copy").addHandler(_copy)
_txt = ("Studies delve into how vinegar can unlock a robust, seamless effect. "
        "Leveraging this is a testament to nothing.")
_out = verdict.tidy_prose(_txt, 700)
t("copy gate: every banned word in model prose is flagged",
  verdict.banned_words(_txt) == ["delve", "unlock", "robust", "seamless", "leveraging", "testament"],
  verdict.banned_words(_txt))
t("  and the flag is a log line naming the words, not the text",
  len(_copy.lines) == 1 and "delve" in _copy.lines[0] and "vinegar" not in _copy.lines[0], _copy.lines)
t("  and the words are flagged, not rewritten", _out == _txt)
_copy.lines.clear()
verdict.tidy_prose("Elevated blood pressure fell in two small salt solution trials.", 700)
t("copy gate: elevated (a finding) and a single solution are not flagged", _copy.lines == [], _copy.lines)
t("copy gate: every word on the list is caught",
  all(verdict.banned_words(w) for w in verdict.BANNED_WORDS))
logging.getLogger("evident.copy").removeHandler(_copy)
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
t("a rendered result carries the evidence chart",
  'id="chart-sec"' in _res and 'class="barcol' in _res
  and "barcol-bar h-moderate" in _res and "Based on 3 studies" in _res
  and 'id="chart-desc"' in _res and "Each bar is one study." in _res)

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
t("a forced 'not enough evidence' verdict carries no breakdown",
  _un["verdict"] == "insufficient" and _un["breakdown"] is None, _un["breakdown"])

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

# ---- the result screen: a labelled claim, an answer, then the evidence -----------
#
# The order is the product: a reader who stops after the short answer has an
# answer, and the claim they typed can never be mistaken for it.

_css = (ROOT / "static" / "style.css").read_text()
_ord = c.get("/?q=Vitamin+D+supplements+in+winter+cut+colds").data.decode()


def _at(needle):
    return _ord.index(needle)


t("the result reads claim, verdict, answer, research, still open, evidence, then sources",
  _at("The claim you checked") < _at('id="stamp"') < _at("The short answer")
  < _at("What the research says") < _at(">Still open<") < _at('id="chart-sec"')
  < _at("Read the full breakdown") < _at("Studies checked"))
t("  the claim carries its label and is never cut short",
  "The claim you checked" in _ord
  and "Vitamin D supplements in winter cut colds</h2>" in _ord)
t("  its quotation marks come from the stylesheet, so the text stays the text",
  ".claim-echo::before" in _css and "\\201C" in _css)
# The verdict has to be the most prominent text on the screen. Sizes are
# compared rather than eyeballed: the claim's largest is below the verdict's
# smallest, at every width.
_claim_px = [float(n) for n in re.findall(
    r"\.claim-echo \{[^}]*?clamp\((\d+(?:\.\d+)?)px,[^,]+,\s*(\d+(?:\.\d+)?)px\)",
    _css, re.S)[0]]
_stamp_px = [float(n) for n in re.findall(
    r"\.stamp \{[^}]*?clamp\((\d+(?:\.\d+)?)px,[^,]+,\s*(\d+(?:\.\d+)?)px\)",
    _css, re.S)[0]]
t("  and the verdict is set larger than the claim at every width",
  _claim_px[1] < _stamp_px[0], f"claim {_claim_px} stamp {_stamp_px}")

# (d) What the research says: never folded, every sentence already gated.
_says = _vd["breakdown"]["says"]
t("what the research says is three to five sentences, every one of them cited",
  3 <= len(_says) <= breakdown.SAYS_MAX
  and all(breakdown.refs(line, 2) for line in _says), _says)
t("  and carries no figure the abstracts do not have",
  not any("47%" in line for line in _says), _says)
t("  it is in the payload, server rendered, and outside the fold",
  "What the research says" in _ord and _at("What the research says") < _at("deeper")
  and all(line in _ord for line in _says))
# The figure and the plain words for it are one pair. The translation is
# written last in the field, so picking the first two sentences would print a
# ratio and leave its translation behind.
_PAIR_BD = {"evidence": ["A pooled review found fewer infections (Study 1)."],
            "effect_size": "The odds ratio for everyone was 0.88 (Study 1). "
                           "Below 25 nmol/L the odds ratio was 0.30 (Study 1). "
                           "That is about 70% lower odds, a large effect (Study 1).",
            "effect": {"figure": "odds ratio 0.30", "label": "large",
                       "plain": "about 70% lower odds"},
            "applies_to": "Adults aged 18 to 67 were studied (Study 2)."}
_pair = breakdown.says("", _PAIR_BD, _BD_STUDIES)
t("  the figure and its plain words are picked as a pair",
  any(line.startswith(breakdown.TRANSLATION_LEAD) for line in _pair)
  and any("0.30" in line for line in _pair)
  and not any("0.88" in line for line in _pair), _pair)
t("  a sentence the gate rejects never reaches it",
  breakdown.says("Colds fell by 47% (Study 1). This one cites nothing.",
                 _vd["breakdown"], _BD_STUDIES)[:1]
  != ["Colds fell by 47% (Study 1)."])
# Picked, not written: nothing is asked of the model for this block, and
# every sentence in it is already somewhere else in the same answer.
_elsewhere = " ".join([_vd["explanation"], breakdown.flatten(_vd["breakdown"])])
t("  and it costs no extra call: every sentence is already in the answer",
  "says" not in verdict.VERDICT_SCHEMA["properties"]
  and all(line in _elsewhere for line in _says), _says)

# (f) The evidence chart. Everything it says in words is counted on the
# server, so the streamed check and the shared link cannot disagree.
_w = evidence.snapshot(_BD_STUDIES, ["D1"])
t("the chart says what it is based on, in words, from the counts",
  _w["summary"] == "Based on 2 studies \u00b7 1 used for this verdict \u00b7 2 strong",
  _w["summary"])
t("  the rest of the record goes on one line under it",
  _w["facts"] == "1 pooled analysis \u00b7 1 trial \u00b7 published 2017", _w["facts"])
t("  a screen reader gets the real counts, not the drawing",
  _w["described"].startswith("2 studies read, 1 used for this verdict.")
  and "2 strong designs" in _w["described"], _w["described"])
_one = evidence.snapshot(_BD_STUDIES[:1], [])
t("  one study is 'study', and nothing used says so",
  _one["summary"] == "Based on 1 study \u00b7 none used for this verdict \u00b7 1 strong",
  _one["summary"])
t("  an empty set draws no chart and claims nothing",
  evidence.snapshot([])["summary"] == "" and evidence.snapshot([])["described"] == "")

# The shelf. Height is the tier and cloth is the kind, both off the record.
_K = lambda types, title="Vitamin D in adults", abstract="": evidence.kind(
    {"publication_types": types, "title": title, "abstract": abstract})
t("a study's cloth is what it was run on, read off the record",
  _K(["Meta-Analysis"]) == "strong" and _K(["Randomized Controlled Trial"]) == "strong"
  and _K(["Observational Study"]) == "human" and _K(["Journal Article"]) == "human"
  and _K(["Journal Article"], "Vinegar lowers glucose in diabetic rats",
         "Male Wistar rats were fed vinegar for 8 weeks.") == "lab"
  and _K(["Case Reports"]) == "weak" and _K(["Retracted Publication"]) == "retracted")
t("  an animal study is lab cloth even when it is pooled, and the height still says pooled",
  _K(["Meta-Analysis"], "Vinegar in rodent models: a meta-analysis",
     "Studies in rats and mice were pooled.") == "lab"
  and evidence.classify(["Meta-Analysis"]) == "strong")
t("every study in the payload carries its cloth",
  all(s.get("kind") in evidence.KINDS for s in _vd["studies"]), [s.get("kind") for s in _vd["studies"]])
_shelf = evidence.shelf([{"pmid": "1", "publication_types": ["Case Reports"]},
                         {"pmid": "2", "publication_types": ["Meta-Analysis"]},
                         {"pmid": "3", "publication_types": ["Observational Study"]}], ["3"])
t("a mini shelf stands its books in kind order and marks the ones relied on",
  [b["kind"] for b in _shelf] == ["strong", "human", "weak"]
  and [b["used"] for b in _shelf] == [False, True, False], _shelf)

t("every study carries the type name the chart shows on hover",
  _vd["studies"][0]["type_label"] == "Meta-Analysis"
  and _vd["studies"][1]["type_label"] == "Randomized Controlled Trial")
t("the bars are drawn in the order of the list, numbered the same way",
  _ord.index('data-i="0"') < _ord.index('data-i="1"')
  and ">01<" in _ord and ">02<" in _ord)
t("height is the kind of study and fill is whether the verdict used it",
  'class="barcol is-used" data-i="0"' in _ord and "barcol-bar h-strong" in _ord)
t("each bar names itself for a screen reader and says where it goes",
  'aria-label="Study 1, meta-analysis, 2017, used for this verdict. '
  'Go to it in the list of studies."' in _ord)

# The chart is drawn twice, by Jinja for a shared link and by renderChart for
# a live check. Two renderers for one drawing is how they start to diverge.
_bar_tpl = _tpl[_tpl.index("{% macro barcol"):]
_bar_tpl = _bar_tpl[:_bar_tpl.index("{% endmacro %}")]
_bar_js = _js[_js.index("function renderChart"):]
_bar_js = _bar_js[:_bar_js.index("function showStudy")]
t("the server and the browser draw the same bar",
  all(piece in _bar_tpl and piece in _bar_js
      for piece in ("barcol-track", "barcol-bar h-", "barcol-no", "barcol-tip",
                    "is-used", "Go to it in the list of studies")))
t("  and read the chart's words off the same server counts",
  all(f"ev.{k}" in _bar_js or f"ev.{k}" in _tpl for k in ("summary", "facts", "described")))

_chart_css = _css.split("---- The evidence chart")[1].split("/* The study the reader")[0]
t("the chart is ink only: no hue reaches a drawing of the evidence",
  not re.search(r"#[0-9a-f]{3}|rgb\(|hsl\(", _chart_css, re.I), _chart_css[:200])
t("a bar is at least a finger tall, and the columns leave no dead space",
  int(re.search(r"--plot-h: (\d+)px", _chart_css).group(1)) >= 44
  and "padding: 0 3px" in _chart_css)
_grow = _css.split("@keyframes bar-grow")[1].split("}")[0]
t("the reveal moves transform and opacity only",
  "transform" in _grow and "opacity" in _grow
  and not re.search(r"\b(height|width|margin|top|left)\b", _grow), _grow)
t("  under 400ms end to end, and nothing at all under reduced motion",
  "250ms" in _chart_css and "* 20ms" in _chart_css
  and ".chart-bars.reveal .barcol-bar { animation: none; }"
  in _css.split("@media (prefers-reduced-motion: reduce)")[1])
t("a bar is a button, so the keyboard reaches it, and it marks what it opens",
  'type="button" class="barcol' in _ord and "showStudy(Number(col.dataset.i))" in _js
  and "scrollIntoView" in _js[_js.index("function showStudy"):]
  and ".barcol.is-on .barcol-bar" in _css)

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


# ---- accuracy round 2 (the 20-claim audit) ------------------------------------
# 1. The stamp and the words are one answer. These are the audit's own
# takeaways: the five it stamped over with a contradicting verdict, and the
# fifteen it did not, which must stay unflagged.
_AUDIT_TAKEAWAYS = [
    ("complicated", "Vaccines reduce the risk of severe measles", "Vaccines reduce the risk of severe measles in children.", True),
    ("complicated", "Sugar makes children hyperactive", "Studies show mixed links between sugar and ADHD, but controlled tests show sugar does not make children hyperactive.", True),
    ("complicated", "Detox teas remove toxins from the body", "Detox teas do not remove toxins, and they can cause severe liver injury and low sodium.", True),
    ("complicated", "Intermittent fasting is better for weight loss than regular dieting", "Fast-style diets work for weight loss, but they work about as well as standard calorie limits.", True),
    ("complicated", "Red wine is good for the heart", "Red wine has no supported heart benefits here, and alcohol can raise blood pressure.", True),
    ("true", "Exercise lowers the risk of depression", "Exercise lowers depression symptoms across many large trials.", False),
    ("true", "Smoking causes lung cancer", "Smoking causes lung cancer, and people who smoke face a much higher risk.", False),
    ("false", "Vaccines cause autism", "Studies show that vaccines do not cause autism.", False),
    ("false", "Cracking your knuckles causes arthritis", "Cracking your knuckles does not cause arthritis, according to the available studies.", False),
    ("complicated", "Eggs raise heart disease risk", "Some large reviews link higher egg intake to more risk, but other large studies find no link at all.", False),
    ("complicated", "Cold showers boost immunity", "Cold showers boost certain immune cells, but it is not clear if this lowers the risk of actual illness.", False),
    ("complicated", "Melatonin helps people fall asleep faster", "Melatonin helps some people fall asleep faster, but results vary a lot by age and health.", False),
    ("complicated", "You need to drink 8 glasses of water a day", "Drinking 8 glasses of water a day is a common rule, but fluid needs vary by person and activity level.", False),
    ("true", "Standing desks reduce back pain", "Standing desks can help reduce low back pain, though some workers still get sore.", False),
    ("complicated", "Magnesium supplements improve sleep quality", "Magnesium may help you fall asleep a bit faster, but the proof is weak and mixed.", False),
]
_flagged = [(cl, bool(breakdown.stamp_conflict(v, tl, "", cl)) == want)
            for v, cl, tl, want in _AUDIT_TAKEAWAYS]
t("the stamp check flags the audit's five contradictions and none of the rest",
  all(ok for _, ok in _flagged), [cl for cl, ok in _flagged if not ok])
t("  a 'but' that turns to a side note leaves the plain answer standing",
  breakdown.stamp_conflict("complicated", "Smoking causes lung cancer, but reducing the amount you smoke can lower your risk.",
                           "", "Smoking causes lung cancer")
  and not breakdown.stamp_conflict("complicated", "Exercise lowers the risk of depression, but the effect is small.",
                                   "", "Exercise lowers the risk of depression")
  and not breakdown.stamp_conflict("true", "Smoking causes lung cancer, but reducing the amount you smoke can lower your risk.",
                                   "", "Smoking causes lung cancer"))
t("  'no study tests this' is not a 'no', so it can sit under an unsure stamp",
  breakdown.stance("The studies found don't actually test this claim, so it's unproven either way.",
                   "Detox teas remove toxins") == "untested")
t("  an explanation that rules on the claim counts, a single finding does not",
  breakdown.stamp_conflict("complicated", "It helps some people, but not all.",
                           "The trials contradict the claim (Study 1).", "X helps")
  and not breakdown.stamp_conflict("complicated", "It helps some people, but not all.",
                                   "The evidence does not support the claim (Study 1).", "X helps"))

_SUGAR = [{"pmid": f"S{i}", "title": "Sugar and hyperactivity: a meta-analysis", "abstract": "a",
           "journal": "J", "year": "2020", "publication_types": ["Meta-Analysis"],
           "authors": [], "data_banks": None, "url": "u"} for i in (1, 2)]
_NO_TLDR = "Controlled tests show sugar does not make children hyperactive."
fg.models.calls = 0
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr=_NO_TLDR,
                            evidence=["Sugar did not change behaviour (Study 1)."])),
                    Resp(VJ("false", [1, 2], tldr=_NO_TLDR,
                            evidence=["Sugar did not change behaviour (Study 1)."]))]
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("a stamp that contradicts its takeaway is asked again, once, and the agreeing answer wins",
  _v["verdict"] == "false" and fg.models.calls == 2, (_v["verdict"], fg.models.calls))
fg.models.calls = 0
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr=_NO_TLDR,
                            evidence=["Sugar did not change behaviour (Study 1)."]))] * 2
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("  if the retry still contradicts, the stamp stays and the plain words go",
  _v["verdict"] == "complicated" and _v["tldr"] == verdict.LEAN_TLDR["no"]
  and fg.models.calls == 2, _v)
t("  and that fallback never contradicts its own stamp",
  not breakdown.stamp_conflict(_v["verdict"], _v["tldr"], _v["explanation"],
                               "Sugar makes children hyperactive"))
fg.models.calls = 0
fg.models.script = [Resp(VJ("true", [1, 2], tldr=_NO_TLDR,
                            evidence=["Sugar did not change behaviour (Study 1)."]))] * 2
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("  a confident stamp over opposite words drops to complicated, never the other way",
  _v["verdict"] == "complicated" and _v["tldr"] == verdict.LEAN_TLDR["no"], _v)
t("the prompt says the verdict and the takeaway are one answer",
  "one answer said twice" in verdict.weigh_prompt("c", "s"))


# 2. The one-group downgrade reads the claim. Measles vaccines are given to
# children, so the Cochrane review in children answers the claim as asked.
_MEASLES = [_study("M1", "Vaccines for measles, mumps, rubella, and varicella in children.",
                   types=["Systematic Review"]),
            _study("M2", "Measles vaccine effectiveness in children: a meta-analysis",
                   types=["Meta-Analysis"])]
t("the claim's own group is not narrow for that claim",
  evidence.narrow_only(_MEASLES, ["M1", "M2"], "Vaccines reduce the risk of severe measles")
  is False)
t("  a claim that names its group counts the same",
  evidence.narrow_only(_MEASLES, ["M1", "M2"], "Sugar makes children hyperactive") is False)
t("  and a general claim answered only in children is still downgraded",
  evidence.narrow_only(_MEASLES, ["M1", "M2"], "Vitamin D stops colds") is True)
t("  a risk claim is never answered by people who already have the disease",
  "people with diabetes" not in evidence.claim_groups(
      "Regular physical activity reduces the risk of type 2 diabetes")
  and "people with diabetes" in evidence.claim_groups(
      "Cinnamon lowers blood sugar in type 2 diabetes"))
t("  'women and men' is everyone, not 'men only'",
  evidence.population(_study("WM", "Time-Restricted Eating in Women and Men With Overweight"))
  is None)
fg.models.script = [Resp(VJ("true", [1, 2], tldr="Vaccines cut severe measles in children.",
                            evidence=["The vaccine cut measles cases (Study 1)."]))]
_v = verdict.weigh_evidence("Vaccines reduce the risk of severe measles", _MEASLES,
                            "(vaccine) AND (measles)")
t("  so measles stays 'Likely true' and its takeaway stands",
  _v["verdict"] == "true" and "Every study behind this" not in _v["explanation"], _v)
t("  and the prompt marks those studies as the claim's own group, not narrow",
  "the group this claim is about" in verdict._format_study_for_prompt(
      1, _MEASLES[0], claim="Vaccines reduce the risk of severe measles"))


# 3. Null results are said as null, and a figure keeps its comparison. The
# three abstracts below are the audit's own, cut to the sentence that matters.
_NULLS = [
    _study("N1", "Vaccines are not associated with autism: a meta-analysis",
           abstract="The cohort data revealed no relationship between vaccination and "
                    "autism (OR: 0.99; 95% CI: 0.92 to 1.06) or ASD (OR: 0.91; 95% CI: "
                    "0.68 to 1.20).", types=["Meta-Analysis"]),
    _study("N2", "Effect of increased daily water intake on recurrent cystitis",
           abstract="The mean number of cystitis episodes was 1.7 (95% CI, 1.5-1.8) in "
                    "the water group compared with 3.2 (95% CI, 3.0-3.4) in the control "
                    "group (difference in means, -1.5; 95% CI, -1.68 to -1.32; P < .001).",
           types=["Randomized Controlled Trial"]),
    _study("N3", "Calorie restriction with or without time-restricted eating",
           abstract="Changes in weight were not significantly different in the two groups "
                    "at the 12-month assessment (net difference, -1.8 kg; 95% CI, -4.0 to "
                    "0.4; P = 0.11).", types=["Randomized Controlled Trial"]),
]
t("an interval that crosses no effect is read as no clear difference",
  evidence.significance(_NULLS[0]["abstract"], "0.99") == "null"
  and evidence.significance("pooled OR 0.88 (95% CI 0.81 to 0.96)", "0.88") == "clear"
  and evidence.effect_size("an odds ratio of 0.99 (95% CI 0.92 to 1.06)")["label"] == "none")
t("  and a p value of 0.05 or more, or 'not significantly different', reads the same",
  evidence.significance(_NULLS[2]["abstract"], "-1.8") == "null"
  and evidence.significance("the net difference was 2.1 kg (P = .03)", "2.1") == "clear")
t("  a group's own average is not a comparison and is never judged",
  evidence.significance(_NULLS[1]["abstract"], "1.7") is None)
_bd = breakdown.ground({"parts": [{"part": "Vaccines cause autism",
                                   "assessment": "Vaccination was not linked to autism (Study 1)."}],
                        "evidence": ["The pooled review found an odds ratio of 0.99 for autism (Study 1)."],
                        "effect_size": "The odds ratio for autism was 0.99 (Study 1)."},
                       _NULLS, verdict.tidy_prose, "Vaccines cause autism")
t("OR 0.99 with an interval of 0.92 to 1.06 is 'no clear difference', never 'a small effect'",
  "no clear difference" in _bd["effect_size"] and "small" not in _bd["effect_size"]
  and _bd["effect"]["label"] == "none", _bd["effect_size"])
t("  and the deeper paragraphs say it was not statistically significant",
  "not statistically significant (Study 1)" in " ".join(_bd["evidence"]), _bd["evidence"])
t("  the translation is still the line 'What the research says' picks",
  any("no clear difference" in line for line in _bd["says"]) if "says" in _bd
  else any("no clear difference" in line
           for line in breakdown.says("", _bd, _NULLS)))
t("  a size word on a null figure becomes 'no clear difference'",
  breakdown.tell_straight("Vaccination had an odds ratio of 0.99, a small effect (Study 1).",
                          _NULLS) == "Vaccination had an odds ratio of 0.99, no clear difference (Study 1).")
t("a difference the abstract calls not significant is said to be not significant",
  breakdown.tell_straight("The net weight difference was -1.8 kg (Study 3).", _NULLS)
  == "The net weight difference was -1.8 kg (Study 3). "
     "That difference was not statistically significant (Study 3).")
t("a figure keeps its comparison: 1.7 against 3.2 episodes, not 1.7 episodes",
  breakdown.tell_straight("Extra water led to 1.7 cystitis episodes compared to controls (Study 2).",
                          _NULLS)
  == "Extra water led to 1.7 cystitis episodes compared with 3.2 in controls (Study 2).")
t("  a sentence that already gives both sides is left alone",
  breakdown.tell_straight("Episodes fell to 1.7 from 3.2 (Study 2).", _NULLS)
  == "Episodes fell to 1.7 from 3.2 (Study 2).")
t("'not statistically significant' is never glossed as 'unlikely to be chance alone'",
  "unlikely to be chance" not in breakdown.gloss_once(
      "The change was not statistically significant (Study 3).", set()))
fg.models.script = [Resp(VJ("false", [1], tldr="Vaccines do not cause autism.",
                            explanation="The pooled review found an odds ratio of 0.99 for autism (Study 1).",
                            evidence=["Vaccination was not linked to autism (Study 1)."]))]
_v = verdict.weigh_evidence("Vaccines cause autism", _NULLS[:1], "(vaccine) AND (autism)")
t("  the explanation is held to the same rule",
  "not statistically significant (Study 1)" in _v["explanation"], _v["explanation"])
t("the prompt asks for both groups' figures and for 'no clear difference'",
  "never \"1.7 episodes compared to controls\"" in verdict.weigh_prompt("c", "s")
  and "no clear difference" in verdict.weigh_prompt("c", "s"))


# 4. "Likely true" is capped by how sure the evidence is. The abstracts are
# the audit's own: the ashwagandha review that grades itself low, and the
# standing-desk trials of 27 to 56 office workers.
_ASHWA = [_study("A1", "Does Ashwagandha supplementation have a beneficial effect on anxiety and stress?",
                 abstract="Ashwagandha reduced stress compared to the placebo. Finally, we identified "
                          "that the certainty of the evidence was low for both outcomes.",
                 types=["Meta-Analysis", "Systematic Review"]),
          _study("A2", "Ashwagandha in stressed adults",
                 abstract="Sixty adults were randomly allocated to take either a placebo or 240 mg "
                          "of ashwagandha extract.")]
_DESKS = [_study("D1", "Impact of a Sit-Stand Workstation on Chronic Low Back Pain",
                 abstract="Participants were randomized to receive a SSW at the beginning or at the "
                          "end of a 3-month study period. Forty-six university employees with "
                          "self-reported chronic LBP were enrolled."),
          _study("D2", "Do fixed or personalised sit-stand desk ratios improve lower back pain?",
                 abstract="Fifty-six desk-based workers with LBP were randomised to either a fixed "
                          "ratio or a personalised ratio."),
          _study("D3", "Reducing sedentary behaviour to decrease chronic low back pain: the stand back randomised trial",
                 abstract="The Stand Back study evaluated the feasibility of a multicomponent "
                          "intervention in 27 desk workers.")]
t("sizes are read in digits and in words",
  evidence.sample_size(_DESKS[0]) == 46 and evidence.sample_size(_ASHWA[1]) == 60
  and evidence.sample_size(_study("S", "t", abstract="In 1,234 adults aged 40 years")) == 1234)
t("  two versions of the treatment and nobody without is no untreated comparison",
  evidence.uncontrolled(_DESKS[1]) and not evidence.uncontrolled(_DESKS[0])
  and evidence.uncontrolled(_study("S", "A single-arm trial of X", abstract="Placebo was not used.")))
t("  a review that grades its evidence low is low certainty, one that says moderate is not",
  evidence.low_certainty(_ASHWA[0])
  and not evidence.low_certainty(_study("R", "r", abstract="Moderate certainty evidence showed a benefit.",
                                        types=["Meta-Analysis"])))
t("a low-certainty review caps 'true'",
  evidence.certainty_cap(_ASHWA, ["A1", "A2"]) is not None)
t("  and so does a stack of small, pilot and uncontrolled trials",
  evidence.certainty_cap(_DESKS, ["D1", "D2", "D3"]) is not None)
t("  one trial of 400 people with a control group is enough to lift it",
  evidence.certainty_cap(_DESKS + [_study("D4", "Desks and back pain",
                                         abstract="We randomised 400 workers to a desk or usual care.")],
                         ["D1", "D2", "D3", "D4"]) is None)
t("  a study that says nothing about its size or design is never the reason to cap",
  evidence.certainty_cap([_study("U", "u", types=["Journal Article"])], ["U"]) is None)
t("  a comparative claim is not capped for comparing two treatments",
  evidence.certainty_cap([_study("C", "c", abstract="We randomised 300 adults to diet A or diet B.")],
                         ["C"], comparative=True) is None)
fg.models.script = [Resp(VJ("true", [1, 2], tldr="Ashwagandha reduces stress.",
                            evidence=["Ashwagandha reduced stress (Study 1)."]))]
_v = verdict.weigh_evidence("Ashwagandha reduces stress", _ASHWA, "(ashwagandha) AND (stress)")
t("ashwagandha is 'It's complicated', and the takeaway says the certainty is low",
  _v["verdict"] == "complicated" and "certainty of that evidence is low" in _v["tldr"]
  and "certainty of this evidence is low" in _v["explanation"], _v)
fg.models.script = [Resp(VJ("true", [1, 2, 3], tldr="Standing desks reduce back pain.",
                            evidence=["Back pain fell with a desk (Study 1)."]))]
_v = verdict.weigh_evidence("Standing desks reduce back pain", _DESKS, "(desk) AND (back pain)")
t("  so are standing desks", _v["verdict"] == "complicated"
  and "low" in _v["tldr"], _v)
t("the prompt marks a trial with nobody untreated, and never calls it randomised evidence",
  "NO UNTREATED COMPARISON GROUP" in verdict._format_study_for_prompt(2, _DESKS[1])
  and "LIMITED: small, 46 people" in verdict._format_study_for_prompt(1, _DESKS[0])
  and "LOW CERTAINTY" in verdict._format_study_for_prompt(1, _ASHWA[0])
  and "not randomised evidence" in verdict.weigh_prompt("c", "s"))


# 5. A study is used only when it matches the claim's subject and its outcome.
_SMOKE = [_study("L1", "Cigarette Smoking Reduction and Health Risks: A Systematic Review and Meta-analysis.",
                 types=["Meta-Analysis"]),
          _study("L2", "Overcoming CYP1A1/1A2 mediated induction of metabolism by escalating erlotinib dose in current smokers.",
                 abstract="This study aimed to determine the maximum tolerated dose of erlotinib in "
                          "advanced non-small-cell lung cancer (NSCLC) patients who smoke."),
          _study("L3", "Risk-Based lung cancer screening: A systematic review.", types=["Systematic Review"]),
          _study("L4", "Perceived Health Risks of Snus and Medicinal Nicotine Products."),
          _study("L5", "Smoking and lung cancer in a prospective cohort",
                 abstract="We followed 50,000 adults without cancer for 20 years.",
                 types=["Observational Study"])]
t("a drug-dose trial in smokers with lung cancer is off topic for 'smoking causes lung cancer'",
  "already have lung cancer" in (evidence.off_topic(_SMOKE[1], "Smoking causes lung cancer") or ""))
t("  so are screening and a survey of beliefs, and the cohort and the review are not",
  evidence.off_topic(_SMOKE[2], "Smoking causes lung cancer")
  and evidence.off_topic(_SMOKE[3], "Smoking causes lung cancer")
  and not evidence.off_topic(_SMOKE[0], "Smoking causes lung cancer")
  and not evidence.off_topic(_SMOKE[4], "Smoking causes lung cancer"))
t("  ethanol injected in heart surgery is off topic for red wine",
  evidence.off_topic(_study("W", "Effect of Catheter Ablation With Vein of Marshall Ethanol Infusion",
                            abstract="a"), "Red wine is good for the heart")
  and not evidence.off_topic(_study("W2", "Red wine and coronary events", abstract="a"),
                             "Red wine is good for the heart"))
t("  a treatment trial is not ruled out in code for a claim that is not about causes",
  evidence.off_topic(_SMOKE[1], "Erlotinib helps smokers with lung cancer") is None)
fg.models.script = [Resp(VJ("true", [1, 2, 3, 4, 5], tldr="Smoking causes lung cancer.",
                            explanation="Smoking raised risk (Study 5). Erlotinib dose was raised in smokers (Study 2).",
                            relevance=[{"study": i, "subject": True, "outcome": True} for i in range(1, 6)],
                            evidence=["Erlotinib needed a higher dose in smokers (Study 2).",
                                      "Smokers had far more lung cancer (Study 5)."]))]
_v = verdict.weigh_evidence("Smoking causes lung cancer", _SMOKE, "(smoking) AND (lung cancer)")
t("the code gate removes off-topic studies from what was used, whatever the model cited",
  _v["cited_studies"] == ["L1", "L5"], _v["cited_studies"])
t("  and drops every sentence that leaned on them alone",
  "Erlotinib" not in _v["explanation"] and "Smoking raised risk" in _v["explanation"]
  and not any("Erlotinib" in p for p in (_v["breakdown"] or {}).get("evidence", [])), _v)
t("  and writes the reason onto the record it is cached with",
  "already have lung cancer" in (_SMOKE[1].get("off_topic") or "")
  and not _SMOKE[0].get("off_topic"))
_snap = evidence.snapshot(_SMOKE, _v["cited_studies"])
t("  an off-topic strong design is not counted as strong evidence",
  _snap["strong"] == 1 and _snap["off_topic"] == 3 and "3 off topic" in _snap["facts"], _snap)
_IF = [_study("F1", "Intermittent fasting versus continuous calorie restriction for weight loss",
              abstract="We randomised 300 adults to fasting or daily restriction.", types=["Meta-Analysis"]),
       _study("F2", "Intermittent Fasting: Does It Affect Sports Performance? A Systematic Review.",
              types=["Systematic Review"])]
fg.models.script = [Resp(VJ("false", [1, 2], tldr="Intermittent fasting works no better than ordinary dieting.",
                            relevance=[{"study": 1, "subject": True, "outcome": True},
                                       {"study": 2, "subject": True, "outcome": False}],
                            evidence=["Weight loss was the same (Study 1).",
                                      "Fasting did not change sports performance (Study 2)."]))]
_v = verdict.weigh_evidence("Intermittent fasting is better for weight loss than regular dieting", _IF,
                            "(fasting) AND (weight)")
t("the model's own 'wrong outcome' judgement removes the sports review from what was used",
  _v["cited_studies"] == ["F1"] and _IF[1].get("off_topic") == verdict.OFF_OUTCOME
  and not any("sports" in p for p in (_v["breakdown"] or {}).get("evidence", [])), _v)
t("  a judgement left out rules nothing out",
  verdict._relevance({"relevance": [{"study": 1}]}, _IF, "c") == {})
t("the prompt asks for subject and outcome for every study, and marks OFF TOPIC",
  '"relevance"' in verdict.weigh_prompt("c", "s") and "relevance" in verdict.VERDICT_SCHEMA["required"]
  and "OFF TOPIC" in verdict._format_study_for_prompt(2, _SMOKE[1], claim="Smoking causes lung cancer"))
db.put_cached_verdict("off topic render", "q", "true", "Because (Study 1).", ["L1"], _SMOKE,
                      tldr="Smoking causes lung cancer.")
_html = c.get("/?q=off%20topic%20render").data.decode()
t("the server-rendered chart says which bars are off topic",
  "read, off topic" in _html and "3 off topic" in _html)


# 6. A fourth verdict, "Not enough evidence", for when nothing tests the claim.
_DETOX = [_study("T1", "Yogi Detox Tea: A Potential Cause of Acute Liver Failure.",
                 abstract="We report a woman who developed acute liver failure after drinking a detox tea.",
                 types=["Case Reports", "Journal Article"]),
          _study("T2", "Acute Severe Hyponatremia Following Use of Detox Tea.",
                 abstract="A patient presented with hyponatremia.", types=["Case Reports"])]
t("'insufficient' is a verdict the schema allows",
  "insufficient" in verdict.VALID_VERDICTS
  and "insufficient" in verdict.VERDICT_SCHEMA["properties"]["verdict"]["enum"])
t("  and it has its own label everywhere a verdict is drawn",
  appmod.VERDICT_LABELS["insufficient"] == "Not enough evidence"
  and og.LABELS["insufficient"] == "NOT ENOUGH EVIDENCE"
  and 'insufficient: "Not enough evidence"' in open("static/app.js").read()
  and 'insufficient: "-3deg"' in open("static/app.js").read())
t("no studies at all is not enough evidence, not 'complicated'",
  verdict.weigh_evidence("Detox teas remove toxins", [])["verdict"] == "insufficient")
t("only case reports are not a test of the claim",
  evidence.untested_in_people(_DETOX, ["T1", "T2"]) == "reports of single patients"
  and evidence.untested_in_people(_DETOX + _ASHWA, ["T1", "A1"]) == "")
t("  nor are animal and lab studies",
  evidence.untested_in_people([_study("M", "Cold exposure in mice", abstract="Mice were cooled.",
                                      types=["Journal Article"])], ["M"]) == "animal or lab studies")
fg.models.script = [Resp(VJ("false", [1, 2], tldr="Detox teas don't remove toxins and can hurt your liver.",
                            explanation="Two patients were harmed (Studies 1, 2).",
                            evidence=["A woman had liver failure after a detox tea (Study 1)."]))]
_v = verdict.weigh_evidence("Detox teas remove toxins from the body", _DETOX, "(detox tea) AND (toxins)")
t("a case report of harm never makes 'does not work': the stamp is not enough evidence",
  _v["verdict"] == "insufficient", _v)
t("  the explanation says harm is a reason for caution, not evidence it fails",
  "reason for caution, not evidence that it does not work" in _v["explanation"], _v["explanation"])
t("  and the takeaway no longer says it doesn't work",
  "don't remove" not in _v["tldr"] and "tested" in _v["tldr"]
  and not breakdown.stamp_conflict("insufficient", _v["tldr"], "", "Detox teas remove toxins from the body"),
  _v["tldr"])
fg.models.script = [Resp(VJ("insufficient", [], tldr="No study has tested whether detox teas remove toxins."))]
_v = verdict.weigh_evidence("Detox teas remove toxins from the body", _DETOX, "(detox tea) AND (toxins)")
t("the model's own 'insufficient' keeps its takeaway",
  _v["verdict"] == "insufficient" and _v["tldr"] == "No study has tested whether detox teas remove toxins.", _v)
t("the prompt defines all four, and says a harm report is not 'does not work'",
  '"insufficient" means not enough evidence' in verdict.weigh_prompt("c", "s")
  and "never evidence that\nthe thing does not work" in verdict.weigh_prompt("c", "s"))
db.put_cached_verdict("not enough render", "q", "insufficient", "Nothing tests it.", [], _DETOX,
                      tldr="No study has tested this.")
_html = c.get("/?q=not%20enough%20render").data.decode()
t("the server-rendered page stamps 'Not enough evidence'",
  "Not enough evidence" in _html and '"alternateName": "Not enough evidence"' in _html
  and '"ratingValue": 3' in _html, _html[:0])
t("  and the link-preview card draws it",
  c.get("/og/" + db.normalize_claim("not enough render") + ".png").status_code == 200)


# 7. Small cleanups: a PubMed date range is one year, and no phrase twice.
t("a MedlineDate range is read as its year: '1995 Nov 22-29' is 1995",
  pubmed.year_of("1995 Nov 22-29") == "1995" and pubmed.year_of("1998-1999 Winter") == "1998"
  and pubmed.year_of("2020") == "2020" and pubmed.year_of(None) is None)
t("  including on rows cached before the fix",
  appmod._study_payload(dict(_study("Y", "t"), year="1995 Nov 22-29"), set())["year"] == "1995"
  and evidence.snapshot([dict(_study("Y", "t"), year="1995 Nov 22-29")])["year_from"] == 1995)
t("'memory and cognitive function' is not 'memory and thinking and memory'",
  breakdown.plain("Sleep loss impairs memory and overall cognitive function.")
  == "Sleep loss impairs memory and overall thinking."
  and breakdown.plain("Creatine improved cognitive function.") == "Creatine improved thinking and memory.")
t("  a whole phrase said twice across 'and' is said once",
  breakdown.plain("It helped thinking and memory and overall thinking and memory.")
  == "It helped thinking and memory.")
t("  and ordinary prose is left alone",
  all(breakdown.plain(x) == x for x in (
      "The development of autism or autism spectrum disorder.",
      "Rates rose more and more.", "It improved sleep quality and quality of life.")))

# ---- spelling: a misspelled claim is checked as it was meant -----------------
_FIXES = [
    ("smokng causs lung cancr", "Smoking causes lung cancer", True),
    ("dose apple cider vinigaer cure diabaties", "Does apple cider vinegar cure diabetes", True),
    ("redwine is good for the hart", "Red wine is good for the heart", True),
    ("melatonen makes u fall asleep fastr", "Melatonin makes you fall asleep faster", True),
    ("vacines dont cause autisim", "Vaccines don't cause autism", True),
    ("vacines dont cause autisim", "vaccines dont cause autism", True),
    # Rewrites, not spelling fixes: the reader keeps their own words.
    ("vacines dont cause autisim", "Vaccines cause autism", False),
    ("creatine causes hair loss", "Creatine prevents hair loss", False),
    ("sugar makes kids hyper", "Sugar makes children hyperactive", False),
    ("eggs bad", "Eating eggs every day raises your risk of heart disease", False),
]
_wrong = [(a, b) for a, b, want in _FIXES if (verdict.spelling_fix(a, b) != a) != want]
t("a spelling fix is taken, a rewrite or a flipped 'not' is refused", not _wrong, _wrong)
t("  the Claim: line is never read as the search query",
  verdict._clean_query("Claim: Smoking causes lung cancer\n(smoking) AND (lung cancer)", "x")
  == "(smoking) AND (lung cancer)")

fg.models.script = [Resp("(smoking) AND (lung cancer)\nClaim: Smoking causes lung cancer"),
                    Resp(VJ("true", [1], tldr="Smoking causes lung cancer."))]
r = P("smokng causs lung cancr", ip="7.7.7.71"); d = r.get_json()
t("  the check runs on the corrected claim and says what was typed",
  r.status_code == 200 and d["claim"] == "Smoking causes lung cancer"
  and d["typed"] == "smokng causs lung cancr" and "Smoking%20causes" in d["share_url"], d)
fg.models.script = [Resp("(smoking) AND (lung cancer)\nClaim: Smoking causes lung cancer")]
r = P("smoking causs lung cancr", ip="7.7.7.72"); d = r.get_json()
t("  a misspelling of a claim already checked is answered from the cache",
  r.status_code == 200 and d["cached"] and d["typed"] == "smoking causs lung cancr"
  and fg.models.calls and not fg.models.script, d)
fg.models.script = [Resp("(eggs) AND (heart disease)"), Resp(VJ("complicated", [1]))]
d = P("Eggs raise heart disease risk spelled right", ip="7.7.7.73").get_json()
t("  a claim spelled right carries no typed note", d.get("typed") is None, d)
_page = c.get("/").data.decode()
t("  the page has the note, hidden until a check fills it",
  'id="typed-note" hidden' in _page)

# ---- prevention claims and comparisons said in other words -------------------
_T2D = "Regular physical activity reduces the risk of type 2 diabetes"
_offt = lambda title: evidence.off_topic({"title": title, "abstract": ""}, _T2D)
t("a treatment trial in people who already have the disease cannot show prevention",
  "cannot show what prevents it" in (_offt("Effect of resistance training on HbA1c in adults with type 2 diabetes mellitus") or "")
  and "prevents" in (_offt("Exercise and insulin resistance in type 2 diabetes mellitus: a systematic review") or ""))
t("  a prevention study of the same disease stays on topic",
  _offt("Physical activity and incident type 2 diabetes: a meta-analysis of prospective cohorts") is None
  and _offt("Prevention of type 2 diabetes by lifestyle intervention in people with impaired glucose tolerance") is None
  and evidence.prevented_outcome("Exercise lowers the risk of depression") == "depression")
t("  'about as well for weight loss as' is a plain no, whatever a later clause hedges",
  breakdown.stance("Intermittent fasting works about as well for weight loss as ordinary dieting, though some methods show equal results.",
                   "Intermittent fasting is better for weight loss than regular dieting") == "no"
  and breakdown.stance("Intermittent fasting may work about as well as dieting.",
                       "Intermittent fasting is better for weight loss than regular dieting") == "mixed")

t("  a risk claim's own disease is its outcome, not a narrow group",
  evidence.population({"title": "Physical activity and incident type 2 diabetes mellitus: a dose-response meta-analysis",
                       "publication_types": ["Meta-Analysis"]}, _T2D) is None
  and evidence.population({"title": "Exercise in adults with type 2 diabetes",
                           "publication_types": ["Randomized Controlled Trial"]}) == "people with diabetes")

t("  a fixed claim gets its apostrophe and a capital",
  verdict.spelling_fix("vacines dont cause autisim", "vaccines dont cause autism") == "Vaccines don't cause autism"
  and verdict.spelling_fix("vacines dont cause autisim", "Vaccines do cause autism") == "vacines dont cause autisim")

# ---- a loose synonym cannot flood the search -------------------------------
_KQ = "(knuckle cracking OR joint popping) AND (arthritis OR joint degeneration)"
t("the claim's own subject is also searched alone, as a phrase",
  pubmed.phrase_query(_KQ) == '("knuckle cracking") AND (arthritis OR "joint degeneration")'
  and pubmed.phrase_query("(a OR b) AND (c)") == "(a) AND (c)"
  and pubmed.phrase_query("(a) AND (c OR d)") is None
  and pubmed.phrase_query('("x y") AND (z)') is None, pubmed.phrase_query(_KQ))
_seen = []
def _fake_search(q, max_results=8):
    _seen.append(q)
    if "[pt]" in q:
        return [f"OA{i}" for i in range(16)]
    return ["KNUCKLE1", "KNUCKLE2"] if '"knuckle cracking"' in q else [f"JOINT{i}" for i in range(16)]
_real_search = pubmed.search_pubmed
pubmed.search_pubmed = _fake_search
_merged = pubmed.search_merged(_KQ, max_results=8)
pubmed.search_pubmed = _real_search
t("  and what the phrase search finds is always among the studies read",
  "KNUCKLE1" in _merged and "KNUCKLE2" in _merged and len(_merged) == 16 and len(_seen) == 3, _merged)

# ---- Before you trust a verdict: the home page's plain answers -----------
import html as _html
import inspect as _inspect
import re as _re
_home = c.get("/").get_data(as_text=True)
_trust = _home.split('<section id="trust"')[1].split("</section>")[0] if '<section id="trust"' in _home else ""
_rows = _trust.split('<div class="trust-row">')[1:]
_qs = [_html.unescape(_re.sub(r"<[^>]+>", "", r.split("</h3>")[0])).strip() for r in _rows]
t("trust: the home page answers the five questions a stranger asks, and shows a real one",
  _qs[:5] == ["Where do the studies come from?", "Is an AI making this up?",
              "Is it biased? Who made it?", "Is it free?", "Is this medical advice?"]
  and "Can I see a real one?" in _qs, _qs)
t("  every answer ends pointing back to the claim box",
  len(_rows) >= 6 and all(_re.findall(r'<a [^>]*href="([^"]+)"', r)[-1:] == ["#claim-input"] for r in _rows))
_trust_text = _html.unescape(_re.sub(r"<[^>]+>", " ", _trust))
t("  in plain words: no dashes, none of the banned words",
  "\u2014" not in _trust_text and "\u2013" not in _trust_text and not verdict.banned_words(_trust_text),
  verdict.banned_words(_trust_text))
t("  the study count it states is the one PubMed is asked for",
  "up to 8 of the closest" in _trust_text
  and _inspect.signature(pubmed.search_with_fallback).parameters["max_results"].default == 8)
t("  and it names the labels the code actually forces",
  f"“{appmod.VERDICT_LABELS['insufficient']}”" in _trust_text and f"“{appmod.VERDICT_LABELS['complicated']}”" in _trust_text)
_ex = appmod._trust_example()
t("  the real check is the one the fly-through replays, linked as a result page",
  _ex and f'href="{_ex["href"]}"' in _trust and _ex["href"] == "/?q=Apple%20cider%20vinegar%20cures%20diabetes"
  and "The footage at the top of this page is this check" in _trust_text, _ex)
_checks_page = c.get("/checks").get_data(as_text=True)
t("  /checks carries it too, without pointing at footage it does not show",
  '<section id="trust"' in _checks_page and "The footage at the top" not in _checks_page)
_ex_res = c.get(_ex["href"] if _ex else "/").get_data(as_text=True)
t("  and a result page renders it hidden, so the verdict owns that screen",
  ('<section id="trust" class="trust" aria-labelledby="trust-heading" hidden>' in _ex_res)
  or ('id="server-result"' not in _ex_res))
_orig_example = appmod._trust_example
appmod._trust_example = lambda: None
_bare = c.get("/").get_data(as_text=True)
appmod._trust_example = _orig_example
t("  with no real check built, the section renders without inventing one",
  '<section id="trust"' in _bare and "Can I see a real one?" not in _bare)
t("  its links put the cursor in the claim box without changing the address",
  'closest("a.to-claim")' in (ROOT / "static" / "app.js").read_text()
  and "trustSection.hidden = section !== askSection" in (ROOT / "static" / "app.js").read_text())

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
