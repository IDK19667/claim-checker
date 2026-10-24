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
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(k, None)
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

import dotenv  # noqa: E402
dotenv.load_dotenv = lambda *a, **k: None  # keep the developer's .env out of the tests

import app as appmod  # noqa: E402
import copyrules  # noqa: E402
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
        self.configs = getattr(self, "configs", []) + [kw.get("config") or {}]
        self.prompts = getattr(self, "prompts", []) + [kw.get("contents") or ""]
        # An empty script is a provider that is down: every call that can
        # fail open (the faithfulness check, the direction read) does.
        if not self.script:
            raise errors.ServerError(503, {"error": {"message": "script empty"}})
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
# The guideline check is one more call on high-stakes claims. It is tested
# on its own near the end, and stays out of every scripted check until then.
_real_guideline_view = verdict.guideline_view
verdict.guideline_view = lambda claim: None

# A refused PubMed call is asked once more before the check gives up.
class _R:
    def __init__(self, code): self.status_code = code
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))
_real_get, _real_retry = pubmed.requests.get, pubmed.RETRY_AFTER
pubmed.RETRY_AFTER = 0
_codes = [429, 200]
pubmed.requests.get = lambda *a, **k: _R(_codes.pop(0))
t("pubmed: a call NCBI turns away (429) is asked once more", pubmed._get("esearch.fcgi", {}).status_code == 200)
_codes = [503, 503]
try:
    pubmed._get("esearch.fcgi", {}); _gave_up = False
except requests.HTTPError:
    _gave_up = True
t("  and a second refusal still fails, into the check's defined PubMed error", _gave_up and not _codes)
pubmed.requests.get, pubmed.RETRY_AFTER = _real_get, _real_retry


def DIR(answer):
    """The one-word reading of a takeaway's direction (verdict.classify_direction)."""
    return Resp(json.dumps({"answer": answer}))


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
  == "The trials were small. Most ran for 6 weeks.")
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
t("  and the words are replaced with plain ones, the sentence still reads",
  _out == "Studies look into how vinegar can open up a smooth effect. Using this is a sign of nothing.", _out)
_copy.lines.clear()
verdict.tidy_prose("Elevated blood pressure fell in two small salt solution trials.", 700)
t("copy gate: elevated (a finding) and a single solution are not flagged", _copy.lines == [], _copy.lines)
t("copy gate: every word on the list is caught",
  all(verdict.banned_words(w) for w in verdict.BANNED_WORDS))

# The table that does the replacing (copyrules.py). Every word on the list,
# in a typical sentence, comes out gone and still English. The two first rows
# are the real ones: both reached a page before the list was enforced in code.
_CR = [
    ("A review notes that claims of anti-aging effects lack robust clinical evidence in humans (Study 7).",
     "A review notes that claims of anti-aging effects lack good clinical evidence in humans (Study 7)."),
    ("There is an urgent need to undertake methodologically robust research on multimodal care.",
     "There is an urgent need to undertake well designed research on multimodal care."),
    ("Larger robust clinical trials are warranted.", "Larger clinical trials are warranted."),
    ("Robust evidence from trials shows a small benefit.", "Evidence from trials shows a small benefit."),
    ("A robust link was found between X and Y.", "A link was found between X and Y."),
    ("A robust effect appeared in adults.", "An effect appeared in adults."),
    ("The association remained robust after adjustment for age.",
     "The association remained consistent after adjustment for age."),
    ("The result was not robust to sensitivity analyses.",
     "The result was not stable under sensitivity analyses."),
    ("The data are robust, but small.", "The data are consistent, but small."),
    ("Consistent and robust evidence supports this.", "Consistent evidence supports this."),
    ("There is no robust evidence that vinegar helps.", "There is no good evidence that vinegar helps."),
    ("The robustness of the findings is unclear.", "The consistency of the findings is unclear."),
    ("Vitamin D was robustly linked to fewer colds.", "Vitamin D was linked to fewer colds."),
    ("Studies leverage large datasets and leveraged trials, leveraging this.",
     "Studies use large datasets and used trials, using this."),
    ("Leverage the evidence. He has little leverage over outcomes.",
     "Use the evidence. He has little influence over outcomes."),
    ("Seamless integration; it works seamlessly with diet.", "Smooth integration; it works smoothly with diet."),
    ("It empowers patients to manage care. Patients were empowered to quit.",
     "It helps patients to manage care. Patients were helped to quit."),
    ("Many feel empowered to ask, and an empowering approach helps.",
     "Many feel able to ask, and a supportive approach helps."),
    ("Patient empowerment is key. They empower people.", "Patient autonomy is key. They help people."),
    ("Vinegar can unlock weight loss; it unlocks benefits, unlocked others, unlocking more.",
     "Vinegar can open up weight loss; it opens up benefits, opened up others, opening up more."),
    ("Actionable advice is lacking. The findings are actionable.",
     "Practical advice is lacking. The findings are practical."),
    ("A data-driven approach helps; data driven methods too.", "A data-based approach helps; data-based methods too."),
    ("There are no easy solutions. Quick solutions fail. Digital health solutions are sold.",
     "There are no easy answers. Quick fixes fail. Digital health options are sold."),
    ("Solutions to the problem are unclear.", "Answers to the problem are unclear."),
    ("This is a testament to its popularity. Testaments to care.",
     "This is a sign of its popularity. Signs of care."),
    ("The research landscape is changing. The landscape of nutrition research is wide.",
     "The research is changing. The state of nutrition research is wide."),
    ("The current landscape shows little.", "The current picture shows little."),
    ("Studies delve into diet. One delved deeper. Delving into causes, they delve further.",
     "Studies look into diet. One looked deeper. Looking into causes, they look further."),
    ("Saturated fat elevates LDL cholesterol. Exercise may elevate mood.",
     "Saturated fat raises LDL cholesterol. Exercise may raise mood."),
    ("Elevate the legs to reduce swelling.", "Raise the legs to reduce swelling."),
]
_CR_BAD = [(w, g) for w, n in _CR for g in [copyrules.clean_text(w)] if g != n]
t("copy table: every banned word comes out as the plain word, in a typical sentence",
  not _CR_BAD, _CR_BAD[:2])
_cr_hits = {h for was, _ in _CR for h in copyrules.banned_words(was)}
t("  and every word on the list has a sentence above",
  all(any(w[:5] in h for h in _cr_hits) for w in verdict.BANNED_WORDS),
  [w for w in verdict.BANNED_WORDS if not any(w[:5] in h for h in _cr_hits)])
t("  nothing banned is left, in any of them", all(not copyrules.banned_words(n) for _, n in _CR))
t("  a second pass changes nothing", all(copyrules.clean_text(n) == n for _, n in _CR))
t("  no double spaces, no dashes, the sentence still opens with a capital and closes as it did",
  all("  " not in n and "\u2014" not in n and "\u2013" not in n
      and n[:1].isupper() == was[:1].isupper() and n[-1] == was[-1] for was, n in _CR))
t("  a sentence with none of them is returned untouched, and so is anything that is not text",
  copyrules.clean_text("Vinegar did not change blood sugar.") == "Vinegar did not change blood sugar."
  and copyrules.clean_text(None) is None and copyrules.clean_text(7) == 7)
_FORBID = ("strong", "high quality", "high-quality", "high certainty", "proven", "definitive",
           "conclusive", "convincing", "compelling")
t("copy table: no swap ever raises the certainty of the sentence it is in",
  all(f not in n.lower() or f in was.lower() for was, n in _CR for f in _FORBID)
  and not [r for p, r in copyrules.REPLACEMENTS if isinstance(r, str)
           and any(f in r.lower() for f in _FORBID)])
t("  a denial stays a denial: no robust evidence is no good evidence, never plain evidence",
  copyrules.clean_text("Studies found no robust evidence of benefit.") == "Studies found no good evidence of benefit."
  and copyrules.clean_text("They lack robust trials.") == "They lack good trials.")
# The same words, meaning something else. A saline solution is medicine, a
# natural landscape is a place, elevated blood pressure is a finding, and
# robusta is a coffee.
_KEPT = ["Oral rehydration solutions were compared.", "Glucose-electrolyte solutions worked.",
         "5% dextrose solutions were used.", "Hypertonic saline solutions raised sodium.",
         "Solutions containing zinc were tested.", "A saline solution and a glucose solution.",
         "Exposure to natural landscapes helps.", "Elevated blood pressure fell in two trials.",
         "Elevation of the legs helped.", "Coffea robusta beans were roasted."]
t("copy table: a liquid, a place, a finding and a species keep their words",
  all(copyrules.clean_text(k) == k and not copyrules.banned_words(k) for k in _KEPT),
  [k for k in _KEPT if copyrules.clean_text(k) != k])
# Every form of every word in the places a model puts them: opening a sentence,
# before a noun, after "was", before "and", "or", "but" and a comma. Whatever
# the swap, the sentence must not be left with a stray conjunction, a gap, a
# lost capital or a banned word.
import re as _re  # noqa: E402
_FORMS = ["leverage", "leverages", "leveraged", "leveraging", "seamless", "seamlessly", "empower",
          "empowers", "empowered", "empowering", "empowerment", "unlock", "unlocks", "unlocked",
          "unlocking", "robust", "robustly", "robustness", "non-robust", "actionable", "data-driven",
          "data driven", "solutions", "testament", "testaments", "landscape", "landscapes", "delve",
          "delves", "delved", "delving", "elevate", "elevates", "elevating"]
_PLACES = ["{w} matters.", "The {w} trial.", "It was {w}.", "It was {w} and clear.",
           "A {w} and unusual result.", "Both {w} and plain.", "{w}, but small.",
           "A {w}, but small, effect.", "It is {w} or fragile.", "{w} and well tolerated.",
           "They did not {w} it.", "No {w} evidence was found.", "Is it {w}?", "{w} (Study 1) matters.",
           "The study found {w}", "More {w} than before."]
_CONJ = _re.compile(r"\b(?:a|an|the|both|of|to|is|was|it|they|no|not|more)\s+(?:and|or|but)\b", _re.I)
_odd = []
for _f in _FORMS:
    for _pl in _PLACES:
        _s = _pl.format(w=_f)
        _s = _s[:1].upper() + _s[1:] if _pl.startswith("{w}") else _s
        _o = copyrules.clean_text(_s)
        if (copyrules.banned_words(_o) or not _o.strip() or _re.search(r"  | ,|,,|^\s|\s$", _o)
                or (_s[:1].isupper() and _o[:1].islower()) or copyrules.clean_text(_o) != _o
                or (_CONJ.search(_o) and not _CONJ.search(_s))):
            _odd.append((_s, _o))
t(f"copy table: {len(_FORMS)} forms in {len(_PLACES)} places each leave a clean, whole sentence",
  not _odd, _odd[:3])
_cp_in = {"tldr": "A robust result.", "claim": "Does vinegar unlock energy?",
          "studies": [{"title": "A robust landscape of solutions", "abstract": "We delve into it.",
                       "url": "https://example.org/robust", "cited_in_verdict": True,
                       "authors": ["Robust Delve"]}],
          "breakdown": {"parts": [{"part": "vinegar unlock", "assessment": "It unlocks energy (Study 1)."}],
                        "evidence": ["Leveraged trials (Study 1)."]}, "n": 3, "none": None}
_cp_was = json.dumps(_cp_in, sort_keys=True)
_cp = copyrules.clean_payload(_cp_in, "Does vinegar unlock energy?")
t("copy table: a payload is cleaned all the way down, and what is not ours is left as published",
  _cp["tldr"] == "A result." and _cp["breakdown"]["evidence"] == ["Used trials (Study 1)."]
  and _cp["breakdown"]["parts"][0]["assessment"] == "It opens up energy (Study 1)."
  and _cp["studies"] == _cp_in["studies"] and _cp["claim"] == _cp_in["claim"]
  and _cp["n"] == 3 and _cp["none"] is None, _cp)
t("  the reader's own words, quoted back piece by piece, stay as typed",
  _cp["breakdown"]["parts"][0]["part"] == "vinegar unlock"
  and copyrules.clean_payload({"part": "vinegar unlock"})["part"] == "vinegar open up")
t("  and the input is left exactly as it was", json.dumps(_cp_in, sort_keys=True) == _cp_was)
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
_slip = "The precise impact across every diverse global population remains fully mapped out."
_cc = _Catch()
logging.getLogger("evident.copy").addHandler(_cc)
fg.models.script = [Resp("q"), Resp(VJ("true", [1], still_open=_slip))]
d = P("still open slip claim", ip="7.7.9.1").get_json()
t("still open: through a real check, a line that says the question is closed is replaced by the "
  "gap every set of studies has",
  d["still_open"] == verdict.STILL_OPEN_GENERAL and any("still-open line" in x for x in _cc.lines), d)
fg.models.script = [Resp("q"), Resp(VJ("false", [], still_open=_slip))]
d = P("still open slip claim with nothing cited", ip="7.7.9.2").get_json()
t("  and with no study used it is the question of whether anyone has tested it",
  d["still_open"] == verdict.STILL_OPEN_UNTESTED, d)
_cc.lines.clear()
fg.models.script = [Resp("q"), Resp(VJ("true", [1], still_open="Whether the effect remains well established after 12 weeks."))]
d = P("still open kept claim", ip="7.7.9.3").get_json()
t("  while a line that names what is open reaches the page as written",
  d["still_open"] == "Whether the effect remains well established after 12 weeks." and _cc.lines == [], (d, _cc.lines))
logging.getLogger("evident.copy").removeHandler(_cc)
html = c.get("/?q=gate%20claim").data.decode()
t("no dashes in shipped copy", "\u2014" not in html and "\u2013" not in html)

# The same words from a model that ignores the prompt. The fake provider fills
# every line a reader can see with banned words, and each way out (the API,
# the cache, the page, the card) has to come out clean. The studies' titles
# and abstracts are the papers' own words and stay as published.
import html as _esc  # noqa: E402
_BAN = _re.compile(r"leverag|seamless|empower|unlock|robust|actionable|data[- ]driven|solutions|"
                   r"testament|landscape|delv(?:e|es|ed|ing)\b|elevat(?:e|es|ing)\b", _re.I)
_THEIRS = {"claim", "typed", "search_query_used", "title", "abstract", "journal", "authors", "pmid",
           "publication_types", "data_banks"}


def _stuffed(obj, path=""):
    """Every banned word left in a payload's own strings, and where it sits.
    This pattern is deliberately not copyrules's: a check that shares the
    code's blind spot would pass for the same reason the code does."""
    if isinstance(obj, str):
        return [f"{path}: {m.group(0)}" for m in _BAN.finditer(obj)]
    if isinstance(obj, dict):
        return [h for k, v in obj.items() if k not in _THEIRS and not k.endswith("url")
                for h in _stuffed(v, f"{path}.{k}")]
    if isinstance(obj, list):
        return [h for i, v in enumerate(obj) for h in _stuffed(v, f"{path}[{i}]")]
    return []


_STUFF_STUDIES = [
    {"pmid": "K1", "title": "A robust landscape of leveraged solutions",
     "abstract": "BACKGROUND: Vinegar is common. CONCLUSIONS: The effect was robust and seamless "
                 "across the adults studied here.",
     "journal": "J", "year": "2020", "publication_types": ["Randomized Controlled Trial"],
     "authors": ["A B"], "data_banks": None, "url": "uK1"},
    {"pmid": "K2", "title": "Unlocking empowerment: a testament to data-driven delving",
     "abstract": "We delve into a robust landscape of actionable solutions.",
     "journal": "J", "year": "2021", "publication_types": ["Randomized Controlled Trial"],
     "authors": ["C D"], "data_banks": None, "url": "uK2"},
]
_STUFF_RAW = {
    "parts": [{"part": "vinegar burns belly fat",
               "assessment": "The trial found a robust, seamless drop in waist size (Study 1)."}],
    "evidence": ["A robust trial leveraged a data-driven design to delve into waist size (Study 1).",
                 "Its authors called it a testament to actionable solutions that empower people (Study 2)."],
    "effect_size": "The drop was robust in the group studied (Study 1).",
    "strength": "Both trials are robust and seamless (Studies 1, 2).",
    "applies_to": "Adults in a robust sample were studied (Study 1).",
    "not_applies_to": "Children sit outside this landscape of trials (Study 1).",
    "unknowns": "Whether it will unlock lasting benefits for people with elevated risk (Study 2).",
}
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': _STUFF_STUDIES
fg.models.script = [Resp("q"), Resp(VJ(
    "complicated", [1, 2], tldr="Vinegar can unlock a robust, seamless effect.",
    explanation="Studies leverage a data-driven landscape, and this is a testament to "
                "actionable solutions that empower patients (Study 1).",
    still_open="Whether it will unlock robust benefits over time.", **_STUFF_RAW))]
_sd = P("Vinegar burns belly fat", ip="6.6.6.1").get_json()
t("a model that stuffs its answer with banned words reaches the reader clean",
  _stuffed(_sd) == [], _stuffed(_sd))
t("  in the takeaway, the explanation and the open question, in the plain words",
  _sd["tldr"] == "Vinegar can open up a smooth effect."
  and _sd["still_open"] == "Whether it will open up benefits over time."
  and _sd["explanation"].startswith("Studies use a data-based picture, and this is a sign of"), _sd)
t("  and in the breakdown, which was there to be cleaned, and the conclusion quoted from an abstract",
  _sd["breakdown"] and len(_sd["breakdown"]["evidence"]) == 2
  and _sd["breakdown"]["strength"] == "Both trials are consistent and smooth (Studies 1, 2)."
  and _sd["breakdown"]["concluded"] == [
      {"n": 1, "said": "The effect was consistent and smooth across the adults studied here."}], _sd["breakdown"])
t("  the claim, and each study's title and abstract, stay exactly as published",
  _sd["claim"] == "Vinegar burns belly fat"
  and [s["title"] for s in _sd["studies"]] == [s["title"] for s in _STUFF_STUDIES]
  and [s["abstract"] for s in _sd["studies"]] == [s["abstract"] for s in _STUFF_STUDIES])


def _outside_the_papers(page):
    """A page with each study's title and abstract taken out, in every form a
    page prints them: they are the papers' own words and are shown as published."""
    for st in _STUFF_STUDIES:
        for key in ("title", "abstract"):
            for form in (st[key], _esc.escape(st[key]), _esc.escape(st[key], quote=False),
                         json.dumps(st[key])[1:-1]):
                page = page.replace(form, "")
    return page


_sp = c.get("/?q=Vinegar+burns+belly+fat").get_data(as_text=True)
t("  and its server-rendered page is clean too, with the papers' own words still on it",
  not _BAN.findall(_outside_the_papers(_sp)) and "A robust landscape of leveraged solutions" in _sp
  and "Vinegar can open up a smooth effect." in _sp, _BAN.findall(_outside_the_papers(_sp)))
_sd2 = P("Vinegar burns belly fat", ip="6.6.6.2").get_json()
t("  and so is the cache hit that follows",
  _sd2["cached"] is True and _stuffed(_sd2) == [] and _sd2["tldr"] == _sd["tldr"], _stuffed(_sd2))

# A verdict cached before a word was banned (or by code that skipped the gate)
# has the word in its row. The row is left as it was written and every read of
# it is clean: the API, the page, the lists, the card.
_OLD_BD = {k: v for k, v in _sd["breakdown"].items() if k not in ("concluded", "contents")}
_OLD_BD.update(evidence=["A robust trial leveraged a data-driven design (Study 1)."],
               strength="Robust and seamless, a testament to the landscape (Studies 1, 2).",
               says=["Studies delve into actionable solutions (Study 1)."],
               unknowns="Whether it can unlock lasting benefits (Study 2).")
_OLD_ARGS = ("old stuffed row", "q", "complicated", "Leveraging a data-driven landscape (Study 1).",
             ["K1", "K2"], _STUFF_STUDIES)
db.put_cached_verdict(*_OLD_ARGS, tldr="A robust, seamless result.",
                      still_open="Whether it can unlock more.", breakdown=_OLD_BD)
with db.get_conn() as conn:
    _row = conn.execute("select tldr, explanation, breakdown_json from verdict_cache "
                        "where claim_key = ?", (db.normalize_claim("old stuffed row"),)).fetchone()
t("an old cached row still holds the words as it was written",
  "robust" in _row["tldr"] and "Leveraging" in _row["explanation"] and "robust" in _row["breakdown_json"])
_old_key = db.normalize_claim("old stuffed row")
_old_reads = {"get_cached_verdict": db.get_cached_verdict("old stuffed row", 24),
              "get_cached_by_key": db.get_cached_by_key(_old_key),
              "recent_verdicts": next(v for v in db.recent_verdicts(50) if v["claim_text"] == "old stuffed row")}
t("  but every way the database hands it back is clean",
  all(_stuffed(v) == [] for v in _old_reads.values())
  and _old_reads["get_cached_verdict"]["tldr"] == "A smooth result."
  and _old_reads["get_cached_by_key"]["explanation"] == "Using a data-based picture (Study 1).",
  {k: _stuffed(v) for k, v in _old_reads.items()})
_oldd = P("old stuffed row", ip="6.6.6.3").get_json()
t("  and so is the API, which still gives the studies' own titles",
  _oldd["cached"] is True and _stuffed(_oldd) == [] and _oldd["tldr"] == "A smooth result."
  and _oldd["breakdown"]["strength"] == "Smooth, a sign of the picture (Studies 1, 2)."
  and _oldd["studies"][0]["title"] == _STUFF_STUDIES[0]["title"], _stuffed(_oldd))
_oldp = c.get("/?q=old+stuffed+row").get_data(as_text=True)
t("  and the page it renders, meta tags and markup included",
  not _BAN.findall(_outside_the_papers(_oldp)) and "A smooth result." in _oldp,
  _BAN.findall(_outside_the_papers(_oldp)))
_card = c.get("/og/" + urllib.parse.quote(_old_key) + ".png").data
_receipt = og.strongest_cited(_STUFF_STUDIES, ["K1", "K2"])
t("  and so is the share card, which draws the cleaned takeaway and the study's title as published",
  _card == og._render("old stuffed row", "complicated", "A smooth result.", 2, tuple(_receipt))
  and _card != og._render("old stuffed row", "complicated", "A robust, seamless result.", 2, tuple(_receipt)))
t("  and render_card is itself a choke point: any caller's takeaway is cleaned",
  og.render_card("c", "true", "A robust result.", 1) == og.render_card("c", "true", "A result.", 1))

# A line that code wrote after the gates, here the one the health bodies' view
# becomes, is still cleaned by the last pass over the finished result.
_copy2 = _Catch()
logging.getLogger("evident.copy").addHandler(_copy2)
verdict.guideline_view = lambda claim: {"verdict": "false", "bodies": ["WHO"],
                                        "says": "vaccines are not linked to autism in any robust study."}
fg.models.script = [Resp(VJ("true", [1, 2]))]
_gl = verdict.weigh_evidence("Vaccines cause autism", _STUFF_STUDIES, "vaccines AND autism")
verdict.guideline_view = lambda claim: None
logging.getLogger("evident.copy").removeHandler(_copy2)
t("the last pass cleans a line no earlier gate saw, and logs that it had to",
  _gl["verdict"] == "false" and _stuffed(_gl) == []
  and _gl["tldr"] == "Major health guidelines say: vaccines are not linked to autism in any study."
  and any("last pass" in x and "robust" in x for x in _copy2.lines), (_gl["tldr"], _copy2.lines))
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# The pages and scripts that ship carry none of the words either.
_ship = {p: c.get(p).get_data(as_text=True) for p in ("/", "/checks", "/privacy", "/llms.txt", "/no-such-page")}
# (The recent checks on these pages show the studies' titles as published, and
# the two checks above have banned words in theirs.)
t("the home page, the checker, the privacy page and the 404 carry none of the banned words",
  all(not _BAN.findall(_outside_the_papers(h)) for h in _ship.values()),
  {p: _BAN.findall(_outside_the_papers(h)) for p, h in _ship.items()})
t("  nor does the script that draws a result and builds the share text, nor the stylesheet",
  not _BAN.findall((ROOT / "static" / "app.js").read_text())
  and not _BAN.findall((ROOT / "static" / "style.css").read_text()))

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
  "Latest checks" in _html and "entry-no" in _html and 'class="mini"' in _html)
t("the front page never says '1 studies'", "1 studies read" not in _html)

_res = c.get("/?q=streamed%20claim").data.decode()
t("a rendered result carries the evidence chart",
  'id="chart-sec"' in _res and 'class="barcol' in _res
  and "barcol-bar h-moderate" in _res and "Based on 3 studies" in _res
  and 'id="chart-desc"' in _res and "Each book is one study, numbered as in the list." in _res)

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
t("the page preloads the fonts it actually uses",
  "bricolagegrotesque" in _home and "instrumentsans" in _home
  and "librefranklin" not in _home and "librecaslon" not in _home and "archivo" not in _home)
t("theme colour matches the shipped ground, and no dark scheme is claimed",
  'content="#0f1211"' in _home and 'content="light dark"' not in _home)

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
t("  and under a 'not' it leaves the verb alone, since weakening it there would strengthen the sentence",
  breakdown.soften("Vitamin C does not cure cancer.") == "Vitamin C does not cure cancer."
  and breakdown.soften("Garlic doesn't cure HIV.") == "Garlic doesn't cure HIV."
  and breakdown.soften("There is no evidence that it prevents colds.") == "There is no evidence that it prevents colds."
  and breakdown.soften("It helps some cancers. It cures none.") == "It helps some cancers. It helps with none."
  and breakdown.soften("Zinc never prevents colds, but it cures boredom.")
  == "Zinc never prevents colds, but it helps with boredom.",
  breakdown.soften("Zinc never prevents colds, but it cures boredom."))
t("  and inside 'none show that', 'did not find that' or 'little evidence that', however far back",
  breakdown.soften("But none of these trials show that apple cider vinegar can cure diabetes.")
  == "But none of these trials show that apple cider vinegar can cure diabetes."
  and breakdown.soften("The trial did not find that daily garlic capsules prevent colds.")
  == "The trial did not find that daily garlic capsules prevent colds."
  and breakdown.soften("There is little evidence that drinking green tea every day cures anything.")
  == "There is little evidence that drinking green tea every day cures anything."
  and breakdown.soften("No side effects were seen, and vinegar cures diabetes.")
  == "No side effects were seen, and vinegar helps with diabetes.")
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
    if "systematic review OR meta-analysis)" in q:
        return ["MAIN"]
    return ["REVIEW1", "REVIEW2"] if "[pt]" in q else ["POPULAR", "REVIEW1", "TAIL"]
_real_search = pubmed.search_pubmed
pubmed.search_pubmed = _fake_search
_merged = pubmed.search_merged("q", max_results=8)
pubmed.search_pubmed = _real_search
t("  strong designs take the front of the merged list, with no duplicates",
  _merged == ["MAIN", "REVIEW1", "REVIEW2", "POPULAR", "TAIL"], _merged)
t("  and every search really ran, the review search first, then the filtered one",
  len(_seen) == 3 and _seen[0] == "(q) AND (systematic review OR meta-analysis)"
  and "[pt]" in _seen[1] and "[pt]" not in _seen[2], _seen)

# Round 3, rule 6. The main review on a topic is found with the claim's own
# words alone, not with every synonym ORed in.
t("search: the review search uses the first, as-typed term of each group",
  pubmed.review_query("(zinc OR zinc lozenges OR zinc acetate) AND (cold duration OR "
                      "common cold OR upper respiratory infection)")
  == "(zinc) AND (cold duration) AND (systematic review OR meta-analysis)",
  pubmed.review_query("(zinc OR zinc lozenges) AND (cold duration OR common cold)"))
t("  a query with one group still gets one, and an empty one does not",
  pubmed.review_query("knuckle cracking") == "(knuckle cracking) AND (systematic review OR "
  "meta-analysis)" and pubmed.review_query("") is None)
_seen = []
def _fake_search(q, max_results=8):
    _seen.append((q, max_results))
    if q.endswith("(systematic review OR meta-analysis)"):
        return ["COCHRANE", "OTHER_SR", "THIRD_SR", "FOURTH_SR"][:max_results]
    if "[pt]" in q:
        return [f"PT{i}" for i in range(16)]
    return [f"GEN{i}" for i in range(16)]
pubmed.search_pubmed = _fake_search
_merged = pubmed.search_merged("(sunscreen OR sunblock) AND (skin cancer OR melanoma)",
                               max_results=8)
pubmed.search_pubmed = _real_search
t("  its top reviews lead the list, however many synonym searches find",
  _merged[:3] == ["COCHRANE", "OTHER_SR", "THIRD_SR"] and "FOURTH_SR" not in _merged
  and len(_merged) == 16, _merged)
t("  and it is one extra search, asked for only the review slots",
  sum(1 for q, _ in _seen if q.endswith("(systematic review OR meta-analysis)")) == 1
  and ("(sunscreen) AND (skin cancer) AND (systematic review OR meta-analysis)",
       pubmed.REVIEW_SLOTS) in _seen, _seen)
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
t("  nor is a narrative review or a guideline, which enrolled nobody; its title still counts",
  evidence.population({"title": "Antimicrobial treatment of ENT infections.",
                       "abstract": "Acute otitis media in children may need antibiotics.",
                       "publication_types": ["Journal Article", "Review"]}) is None
  and evidence.population({"title": "Sore throat: antibiotic prescribing",
                           "abstract": "Most children and adults recover without antibiotics.",
                           "publication_types": ["Practice Guideline"]}) is None
  and evidence.population({"title": "Pediatric sinusitis.", "abstract": "a",
                           "publication_types": ["Review"]}) == "children")
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

# The holdout's harmful miss: a general review that names children where it
# turns to ear infections was read as a study of children, and code turned
# "antibiotics do not cure colds" into "it's complicated".
_ENT = [_study("E1", "Antimicrobial treatment of ENT infections.", types=["Journal Article", "Review"],
               abstract="Acute otitis media in children and streptococcal pharyngitis may need "
                        "antibiotics. Antibiotics should not be used for the common cold.")]
fg.models.script = [Resp("antibiotics AND common cold"),
                    Resp(VJ("false", [1], tldr="Antibiotics do not cure the common cold.",
                            explanation="Antibiotics should not be used for the common cold (Study 1).",
                            still_open="Nothing."))]
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': _ENT
_ent = P("Antibiotics cure the common cold", ip="7.7.7.8").get_json()
t("a review that names children in passing does not make the evidence narrow",
  _ent["verdict"] == "false" and "one group" not in _ent["explanation"],
  (_ent["verdict"], _ent["explanation"]))

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
t("  a British spelling or a plural is the same term",
  evidence.measures(_study("H", "Efficacy of homoeopathic treatment: a systematic review"),
                    "(homeopathy OR homeopathic treatment)")
  and evidence.measures(_study("A", "The effect of abdominal exercises on abdominal fat"),
                        "(ab exercises OR abdominal exercise)")
  and evidence.measures(_study("D", "Zinc for paediatric diarrhoea"), "(diarrhea)")
  and evidence.measures(_study("P", "Probiotic use and colds"), "(probiotics)"))
t("  but a longer word is still another word",
  not evidence.measures(_study("C", "Creatinine clearance in older adults"), "(creatine)")
  and not evidence.measures(_study("M", "Tested in mice"), "(mouse)"))
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
t("  'efficacy' that describes a noun after it is the noun 'effect', never 'how well it works endpoint'",
  breakdown.plain("The trial found vitamin E had no benefit on the primary efficacy endpoint (Study 2).")
  == "The trial found vitamin E had no benefit on the primary effect endpoint (Study 2)."
  and breakdown.plain("An efficacy analysis found no difference (Study 3).")
  == "An effect analysis found no difference (Study 3)."
  and breakdown.plain("The efficacy data were mixed (Study 3).") == "The effect data were mixed (Study 3)."
  and breakdown.plain("Efficacy outcomes improved (Study 3).") == "Effect outcomes improved (Study 3)."
  and breakdown.plain("Several effectiveness measures improved (Study 3).")
  == "Several effect measures improved (Study 3).")
t("  and so is 'efficacy and safety', where the clause would sit before an 'and'",
  breakdown.plain("The trial tested the efficacy and safety of zinc (Study 3).")
  == "The trial tested the effect and safety of zinc (Study 3)."
  and breakdown.plain("Reviews accepted efficacy and safety (Study 3).")
  == "Reviews accepted effect and safety (Study 3).")
t("  but a clause is still a clause where the term ends its phrase, and a design name keeps its term",
  breakdown.plain("The trial assessed its efficacy in adults (Study 3).")
  == "The trial tested how well it works in adults (Study 3)."
  and breakdown.plain("The trials compared efficacy between groups (Study 3).")
  == "The trials compared how well it works between groups (Study 3)."
  and breakdown.plain("A phase 3 efficacy trial found no benefit.") == "A phase 3 efficacy trial found no benefit."
  and breakdown.plain("The effectiveness studies agree (Study 3).") == "The effectiveness studies agree (Study 3)."
  and breakdown.plain("A cost-effectiveness analysis found savings (Study 3).")
  == "A cost-effectiveness analysis found savings (Study 3)."
  and breakdown.plain("The cost effectiveness of the drug was high (Study 3).")
  == "The cost effectiveness of the drug was high (Study 3)."
  and breakdown.plain("The efficacy-related outcomes improved (Study 3).")
  == "The efficacy-related outcomes improved (Study 3).")
t("  'additional' is 'extra' before one thing and 'more' before several: never 'each more cup'",
  breakdown.plain("Each additional cup of coffee was associated with a 7 percent lower risk (Study 2).")
  == "Each extra cup of coffee was linked to a 7 percent lower risk (Study 2)."
  and breakdown.plain("An additional trial found no benefit (Study 3).") == "An extra trial found no benefit (Study 3)."
  and breakdown.plain("There was no additional benefit (Study 3).") == "There was no extra benefit (Study 3)."
  and breakdown.plain("Additional studies are needed.") == "More studies are needed."
  and breakdown.plain("Additional research is needed.") == "More research is needed."
  and breakdown.plain("Two additional trials found no benefit (Study 3).")
  == "Two more trials found no benefit (Study 3).")
t("  'reduction' is a drop where something fell, and stays a reduction after 'without' or in a diet",
  breakdown.plain("A reduction in pain of 5 points was seen (Study 1).") == "A drop in pain of 5 points was seen (Study 1)."
  and breakdown.plain("Large trials showed no reduction in heart attacks (Study 6).")
  == "Large trials showed no drop in heart attacks (Study 6)."
  and breakdown.plain("Reductions in blood pressure were seen (Study 4).") == "Drops in blood pressure were seen (Study 4)."
  and breakdown.plain("People who continue heavy smoking without reduction (Study 4).")
  == "People who continue heavy smoking without reduction (Study 4)."
  and breakdown.plain("Fasting was not superior to regular calorie reduction (Study 1).")
  == "Fasting was not superior to regular calorie reduction (Study 1)."
  and breakdown.plain("Weight reduction was seen in both groups (Study 4).")
  == "Weight loss was seen in both groups (Study 4).")
t("  the word that says who they were is not followed by 'people': never 'human people'",
  breakdown.plain("Human participants were studied (Study 1).") == "People were studied (Study 1)."
  and breakdown.plain("Male participants had a larger effect (Study 1).") == "Men had a larger effect (Study 1)."
  and breakdown.plain("Female subjects lost more weight (Study 1).") == "Women lost more weight (Study 1)."
  and breakdown.plain("Male and female participants were included (Study 1).")
  == "Men and women were included (Study 1)."
  and breakdown.plain("Participants in the treatment group lost weight (Study 1).")
  == "People in the treatment group lost weight (Study 1).")
t("  'general population' and 'population-based' are fixed phrases, not 'general groups'",
  breakdown.plain("Who was studied: general populations and active smokers (Study 2).")
  == "Who was studied: general populations and active smokers (Study 2)."
  and breakdown.plain("Population-based cohorts found no link (Study 1).") == "Population-based cohorts found no link (Study 1)."
  and breakdown.plain("Studies in diverse populations found no link (Study 1).")
  == "Studies in diverse groups found no link (Study 1).")
t("  'vitamin deficiency' has no letter to put the 'low' before, and a long letter code still has one",
  breakdown.plain("Carotenoids reverse vision loss from vitamin deficiency (Study 7).")
  == "Carotenoids reverse vision loss from low vitamin levels (deficiency) (Study 7)."
  and breakdown.plain("People with vitamin B12 deficiency were studied (Study 1).")
  == "People with low vitamin B12 (deficiency) were studied (Study 1).")
t("  'mortality' is a death rate, and 'lower' or 'higher' of it is fewer or more deaths, with its verb",
  breakdown.plain("The mortality rate was lower in the treated group (Study 1).")
  == "The death rate was lower in the treated group (Study 1)."
  and breakdown.plain("Lower mortality was linked to exercise (Study 1).")
  == "Fewer deaths were linked to exercise (Study 1)."
  and breakdown.plain("Higher mortality was seen in smokers (Study 1).") == "More deaths were seen in smokers (Study 1)."
  and breakdown.plain("Lower mortality risk was seen (Study 1).") == "Lower death risk was seen (Study 1)."
  and breakdown.plain("Infant mortality rose (Study 1).") == "Infant deaths rose (Study 1).")
t("  'concentration' is a level only for an amount; for attention it stays",
  breakdown.plain("Serum concentrations of vitamin D rose (Study 1).") == "Serum levels of vitamin D rose (Study 1)."
  and breakdown.plain("Melatonin improved concentration and memory in adults (Study 1).")
  == "Melatonin improved concentration and memory in adults (Study 1).")
t("  'including' before a count is a statement of who was in, not an example",
  breakdown.plain("The trial, including 300 adults, found no effect (Study 1).")
  == "The trial, including 300 adults, found no effect (Study 1)."
  and breakdown.plain("Foods, including fish and nuts, were studied (Study 1).")
  == "Foods, like fish and nuts, were studied (Study 1).")
t("  'dietary interventions' keeps its plural, 'supplemented groups' its noun, and 'memory and cognitive "
  "function' says memory once",
  breakdown.plain("Dietary interventions were studied (Study 1).") == "Diet changes were studied (Study 1)."
  and breakdown.plain("Supplemented groups improved (Study 1).") == "Groups given supplements improved (Study 1)."
  and breakdown.plain("Memory and cognitive function improved (Study 1).") == "Memory and thinking improved (Study 1)."
  and breakdown.plain("Cognitive function improved (Study 1).") == "Thinking and memory improved (Study 1).")

t("natural: the takeaway is a sentence someone would say out loud",
  breakdown.natural("Vitamin D links to colds and flu.")
  == "Vitamin D is linked to colds and flu.")
t("  and research grammar is turned back into speech",
  breakdown.natural("Creatine is associated with hair loss.")
  == "Creatine is linked to hair loss.")
t("  and 'link' used as a thing is left alone, never 'a small are linked to'",
  breakdown.natural("Green tea has a small link to weight loss.") == "Green tea has a small link to weight loss."
  and breakdown.natural("Coffee has links to lower risk.") == "Coffee has links to lower risk."
  and breakdown.natural("Studies found no link to cancer.") == "Studies found no link to cancer."
  and breakdown.natural("Sugary drinks link to diabetes.") == "Sugary drinks are linked to diabetes.")
t("  nor after a describing word with the verb before it: never 'shows mixed is linked to'",
  breakdown.natural("Red wine shows mixed links to heart health.") == "Red wine shows mixed links to heart health."
  and breakdown.natural("Coffee has conflicting links to cancer.") == "Coffee has conflicting links to cancer.")
t("  nor after a possessive: never \"Red wine's are linked to heart health is mixed\"",
  breakdown.natural("Red wine's link to heart health is mixed.") == "Red wine's link to heart health is mixed."
  and breakdown.natural("Patients' links to care were studied.") == "Patients' links to care were studied."
  and breakdown.natural("Wine links to heart health.") == "Wine is linked to heart health.")

t("still open: a line that says the question is closed is not a line about what is open",
  breakdown.says_settled(_slip)
  and breakdown.says_settled("How the dose works in children is well established.")
  and breakdown.says_settled("Long-term safety remains known."))
t("  and a line that names a gap, a doubt or a future finding is kept",
  not any(breakdown.says_settled(x) for x in (
      "The precise impact across every diverse global population remains to be fully mapped out.",
      "The precise impact across every diverse global population remains not yet fully mapped out.",
      "The precise impact across every diverse global population has yet to be fully mapped out.",
      "The precise impact across every diverse global population is not yet fully mapped out.",
      "Whether it lasts past 12 weeks, since no trial ran longer.",
      "Whether it works in adults is open, but the effect on blood sugar is fully understood.",
      "Nothing.", "Open.",
      "A large trial in older adults that found no effect would reopen the question.",
      "Trials were tested at a single dose.",
      "The effect in children is unknown.", "")))
t("  and none of the likely raw sentences for it loses its 'not' on the way to the page",
  all(breakdown.for_reading(verdict.tidy_prose(x, verdict.STILL_OPEN_MAX), []) == x for x in (
      "The precise impact across every diverse global population remains to be fully mapped out.",
      "The precise impact across every diverse global population remains not yet fully mapped out.",
      "The precise impact across every diverse global population has yet to be fully mapped out.",
      "The precise impact across every diverse global population is not yet fully mapped out.")))

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
t("the breakdown rides on the verdict call: one search, one verdict, one faithfulness read",
  fg.models.calls - _before == 3, fg.models.calls - _before)
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
  fg.models.calls - _cbefore == 4, fg.models.calls - _cbefore)
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
# A list item with role="button" around a PubMed link is a control inside a
# control, which screen readers cannot reach cleanly (axe: serious).
t("  each study opens from a real button, not a list item playing one",
  '<button type="button" class="study-go" aria-label="Deep dive on study 1">' in _html
  and 'role="button"' not in _html.split('id="sources"', 1)[-1].split("</section>", 1)[0]
  and '<button type="button" class="study-go" aria-label="Deep dive on study ${i + 1}">'
      in (ROOT / "static" / "app.js").read_text()
  and ".study-go::after { content: \"\"; position: absolute; inset: 0; }"
      in (ROOT / "static" / "style.css").read_text())
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
# The one rounded thing in the layer is its Open control, at the controls'
# radius. Nothing that holds reading is boxed.
_deeper_css = ((ROOT / "static" / "style.css").read_text()
               .split("---- The deeper layer")[1].split("---- Sticky share bar")[0])
_open_rule = re.search(r"\n\.deeper-open \{[^}]*\}", _deeper_css)
t("nothing in the breakdown is a card, and nothing in it is colour coded",
  _open_rule is not None and "border-radius: var(--r-control)" in _open_rule.group(0)
  and "border-radius" not in _deeper_css.replace(_open_rule.group(0), "")
  and "data-verdict" not in _deeper_css)

# ---- what each study concluded, in its authors' words ----------------------------
#
# Read off the abstracts, never written by the model: a labelled conclusion
# section, or an explicit concluding sentence in an abstract with no headings.
# Anything less certain is left out rather than guessed at.


def _abs(text):
    return {"pmid": "X", "abstract": text}


t("a structured abstract's conclusion section is quoted",
  evidence.conclusion(_abs("BACKGROUND: Colds are common. RESULTS: Fewer colds. "
                           "CONCLUSIONS: Vitamin D modestly reduced colds."))
  == "Vitamin D modestly reduced colds.")
t("  an inline numbered heading counts too",
  evidence.conclusion(_abs("(3) Results: 12 trials. (4) Conclusions: Zinc shortened "
                           "colds by about a day."))
  == "Zinc shortened colds by about a day.")
t("  so does the BMJ's heading with no colon",
  evidence.conclusion(_abs("Results were of moderate quality.Conclusions\xa0Vitamin D "
                           "supplementation was safe and protected against infection."))
  == "Vitamin D supplementation was safe and protected against infection.")
t("  an unstructured abstract is quoted from its concluding sentence",
  evidence.conclusion(_abs("We pooled six trials. Effects varied. In conclusion, "
                           "creatine did not change hair loss in these trials."))
  == "In conclusion, creatine did not change hair loss in these trials.")
t("  and with no concluding sentence nothing is guessed",
  evidence.conclusion(_abs("We pooled six trials. We also explore future work.")) == "")
t("  a headed abstract with no conclusion heading quotes nothing",
  evidence.conclusion(_abs("BACKGROUND: Colds. RESULTS: In conclusion, it worked.")) == "")
t("  the registration trailer is dropped and dashes become words",
  evidence.conclusion(_abs("CONCLUSIONS: Doses of 1000–2000 IU were safe. "
                           "Trial registration: NCT01234567."))
  == "Doses of 1000 to 2000 IU were safe.")
t("  only studies the verdict used, numbered as on the shelf",
  evidence.conclusions([_abs("CONCLUSIONS: The first trial worked well."),
                        {**_abs("CONCLUSIONS: The second trial did not."), "pmid": "Y"}],
                       {"Y"})
  == [{"n": 2, "said": "The second trial did not."}])

t("the breakdown's head says what is inside it",
  breakdown.contents({"parts": [1], "concluded": [1], "effect_size": "x",
                      "applies_to": "y", "unknowns": "z"})
  == "The claim part by part, each study's own conclusion, how big the effect is "
     "and who it applies to.")
t("  and says nothing when there is nothing to say",
  breakdown.contents({}) == "")

_CON_STUDIES = [{**_BD_STUDIES[0], "pmid": "C1",
                 "abstract": _BD_STUDIES[0]["abstract"] + " CONCLUSIONS: Vitamin D "
                 "supplementation was safe and protected against infection overall."},
                {**_BD_STUDIES[1], "pmid": "C2"}]
db.put_cached_verdict("conclusions render", "q", "complicated", "Because (Study 1).",
                      ["C1", "C2"], _CON_STUDIES, tldr="Short take.", still_open="Open.",
                      breakdown=breakdown.ground(_BD_RAW, _CON_STUDIES, verdict.tidy_prose))
_conj = P("conclusions render", ip="4.4.4.4").get_json()
t("the payload carries each used study's conclusion, and the head's contents line",
  _conj["breakdown"]["concluded"] == [
      {"n": 1, "said": "Vitamin D supplementation was safe and protected against "
                       "infection overall."}]
  and "each study's own conclusion" in _conj["breakdown"]["contents"],
  _conj["breakdown"].get("concluded"))
_conh = c.get("/?q=conclusions+render").data.decode()
t("  the conclusions are server rendered as quotes, labelled by study",
  "What each study concluded" in _conh
  and '<blockquote class="said-q">Vitamin D supplementation was safe' in _conh
  and "Study 1 · " in _conh)
t("  and the head says what is inside before it is opened",
  '<span class="deeper-what" id="deeper-what">The claim part by part, each study' in _conh)
t("  the browser draws the same quotes, never linking inside them",
  "What each study concluded" in _js and "escapeHtml(c.said)" in _js)

# After a check the reader ran, the page moves down a little so the answer is
# not mistaken for the whole result. Once, never on a shared link, never against
# a reader who is already scrolling, and a jump rather than a glide for anyone
# who asked for less motion.
_nudge = _js[_js.find("function nudgeToAnswer"):]
_nudge = _nudge[:_nudge.find("\n}\n")]
_check_fn = _js[_js.find("async function check"):]
t("after a check the page moves to the answer, once",
  _js.count("nudgeToAnswer();") == 1 and "nudgeToAnswer();" in _check_fn)
t("  any touch, wheel or key from the reader cancels it",
  all(e in _nudge for e in ('"wheel"', '"touchstart"', '"pointerdown"', '"keydown"')))
t("  it honours reduced motion and waits for a hidden tab",
  "prefers-reduced-motion: reduce" in _nudge and "visibilitychange" in _nudge)

# ---- the result screen: a labelled claim, an answer, then the evidence -----------
#
# The order is the product: a reader who stops after the short answer has an
# answer, and the claim they typed can never be mistaken for it.

_css = (ROOT / "static" / "style.css").read_text()
_sheet = _css[_css.find(".verdict-block {"):].split("}")[0]
t("the answer is a sheet that floats: a lighter panel, a fine rule edge, lifted onto the night head, a soft shadow",
  "background: var(--panel)" in _sheet and "border: 1px solid var(--rule)" in _sheet
  and "margin: -24px -8px 0" in _sheet and "box-shadow:" in _sheet and "var(--shadow-near)" in _sheet
  and "--shadow-near: rgba(8, 10, 9, 0.08);" in _css)
_done = _js[_js.find("renderResult(final.result);"):][:900]
t("after a check the address carries the claim as checked, so a fixed spelling stays fixed on reload",
  'encodeURIComponent(final.result.claim || claim)' in _done
  and "encodeURIComponent(claim))" not in _done)
_ord = c.get("/?q=Vitamin+D+supplements+in+winter+cut+colds").data.decode()


def _at(needle):
    return _ord.index(needle)


t("the result reads claim, verdict, answer, research, still open, breakdown, evidence, then sources",
  _at("The claim you checked") < _at('id="stamp"') < _at("The short answer")
  < _at("What the research says") < _at(">Still open<") < _at("Read the full breakdown")
  < _at('id="chart-sec"') < _at("Studies checked"))
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
_elsewhere = " ".join([_vd["explanation"], breakdown.flatten(_vd["breakdown"])]).lower()
_unlabel = lambda line: line[len(breakdown.WHO_LABEL):] if line.startswith(breakdown.WHO_LABEL) else line
# The labelled line may lose words that repeat its label ("were studied"),
# so it is held to every word coming from the answer, not every character.
_words = lambda text: set(re.findall(r"[a-z0-9]+", text.lower()))
t("  and it costs no extra call: every sentence is already in the answer",
  "says" not in verdict.VERDICT_SCHEMA["properties"]
  and all(_unlabel(line).lower() in _elsewhere
          or (line.startswith(breakdown.WHO_LABEL) and _words(_unlabel(line)) <= _words(_elsewhere))
          for line in _says), _says)
# A population line is often a bare noun phrase. Under "What the research
# says" it reads as a stray fragment unless it carries its label.
_who = breakdown.says("", {"applies_to": "Adults with type 2 diabetes, overweight, or obesity (Studies 1, 2)."},
                      _BD_STUDIES)
t("  the population line carries its label, and drops a common word's capital",
  _who == ["Who was studied: adults with type 2 diabetes, overweight, or obesity (Studies 1, 2)."], _who)
t("  a name keeps its capital, and the label is never doubled",
  breakdown.who_label("HIV patients on treatment (Study 1).") == "Who was studied: HIV patients on treatment (Study 1)."
  and breakdown.who_label("Who was studied: adults (Study 1).") == "Who was studied: adults (Study 1).")
# The live vinegar check wrote "These findings apply to adults with...":
# under the label that read "Who was studied: these findings apply to".
_lead = breakdown.who_label("These findings apply to adults with type 2 diabetes (Studies 1, 2, 8).")
t("  a lead-in that repeats the label is dropped",
  _lead == "Who was studied: adults with type 2 diabetes (Studies 1, 2, 8).", _lead)
# And endings from the 21-claim run: "...were included in these studies".
_tails = [breakdown.who_label(x) for x in (
    "General groups and geriatric patients were included in these studies (Studies 1, 2).",
    "Office workers using sit-stand desks were studied (Study 1).",
    "Healthy adults and athletes studied in the cited trials (Studies 1, 2).",
    "Smokers tested in large systematic reviews (Studies 2, 7).")]
t("  so is an ending that repeats it, and a common first word loses its capital",
  _tails == ["Who was studied: general groups and geriatric patients (Studies 1, 2).",
             "Who was studied: office workers using sit-stand desks (Study 1).",
             "Who was studied: healthy adults and athletes (Studies 1, 2).",
             "Who was studied: smokers tested in large systematic reviews (Studies 2, 7)."], _tails)

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
_S = lambda types, title="Vitamin D in adults", abstract="": evidence.spine(
    {"publication_types": types, "title": title, "abstract": abstract})
t("a spine is lettered with the design in a reader's words, and no year",
  _S(["Meta-Analysis"]) == "Meta-analysis" and _S(["Randomized Controlled Trial"]) == "Randomized trial"
  and _S(["Case Reports"]) == "One case" and _S(["Journal Article"]) == "Study"
  and _S(["Retracted Publication", "Meta-Analysis"]) == "Meta-analysis")
t("  a lab study says what it was run on, unless it is a pooled design",
  _S(["Comparative Study"], "Vinegar lowers glucose in diabetic rats",
     "Male Wistar rats were fed vinegar for 8 weeks.") == "Animal or lab"
  and _S(["Meta-Analysis"], "Vinegar in rodent models: a meta-analysis",
         "Studies in rats and mice were pooled.") == "Meta-analysis")
t("the shelf's key lists only the cloths on the shelf, in legend order",
  [k["kind"] for k in evidence.snapshot([{"pmid": "1", "publication_types": ["Case Reports"]},
                                         {"pmid": "2", "publication_types": ["Meta-Analysis"]}])["kinds"]]
  == ["strong", "weak"])
t("every study in the payload carries the word for its spine",
  all(isinstance(s.get("spine"), str) and s["spine"] for s in _vd["studies"]))

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
      for piece in ("barcol-track", "barcol-bar h-", " k-", "barcol-spine", "barcol-no",
                    "barcol-tip", "is-used", "Go to it in the list of studies")))
t("  and read the chart's words off the same server counts",
  all(f"ev.{k}" in _bar_js or f"ev.{k}" in _tpl for k in ("summary", "facts", "described")))

_chart_css = _css.split("---- The evidence chart")[1].split("/* The study the reader")[0]
t("the chart draws only in named tokens: no raw colour reaches the evidence",
  not re.search(r"#[0-9a-f]{3}|rgb\(|hsl\(", _chart_css, re.I), _chart_css[:200])
t("  the cloth says what kind of study it is, and nothing is coloured by verdict",
  all(f".k-{k}" in _css for k in ("strong", "human", "lab", "weak"))
  and "data-verdict" not in _css
  and "data-verdict" not in (ROOT / "static" / "flight.css").read_text())
t("  a spine word that cannot stand up on its book is left off, never cut",
  "function fitSpines" in _js and "fitSpines();" in _bar_js
  and ".h-retracted .barcol-spine { display: none; }" in _css)
t("a bar is at least a finger tall, and the gap between books is inside the "
  "button, so no strip between them is dead to a finger",
  int(re.search(r"--plot-h: (\d+)px", _chart_css).group(1)) >= 44
  and ".barcol-track { height: var(--plot-h); display: flex; align-items: flex-end; padding: 0 1px; }" in _chart_css)
t("a book is as wide as a book: columns stop at 46px and the row ends at a "
  "bookend, so a short list never stretches into a bar chart",
  "flex: 0 1 46px;" in _chart_css and ".chart-bars::after {" in _chart_css)
t("a relied-on book is solid cloth and a book read but not used is outlined "
  "in its cloth on a pale wash, never faded, on the shelf, the mini shelves "
  "and the share card",
  not re.search(r"barcol-bar[^{]*\{[^}]*opacity", _chart_css.split("@keyframes bar-grow")[0])
  and "opacity" not in _css.split(".mini {")[1].split("/* Before you trust")[0]
  and ".barcol:not(.is-used) .barcol-bar:not(.h-retracted) {" in _chart_css
  and "border: 2px solid var(--k" in _chart_css
  and ".mini i:not(.on):not(.h-retracted) {" in _css
  and ".is-used .barcol-bar::before {" in _chart_css
  and "globalAlpha" not in _js and "CARD.wash" in _js and "strokeRect" in _js
  and "Outlined books were read but not used." in (ROOT / "templates" / "index.html").read_text())
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
_SUGAR_NO = Resp(VJ("complicated", [1, 2], tldr=_NO_TLDR,
                     evidence=["Sugar did not change behaviour (Study 1)."]))
fg.models.script = [_SUGAR_NO, DIR("no"),
                    Resp(VJ("false", [1, 2], tldr=_NO_TLDR,
                            evidence=["Sugar did not change behaviour (Study 1)."]))]
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("a stamp that contradicts its takeaway is asked again, once, and the agreeing answer wins",
  _v["verdict"] == "false" and fg.models.calls == 4, (_v["verdict"], fg.models.calls))
fg.models.calls = 0
fg.models.script = [_SUGAR_NO, DIR("no"), _SUGAR_NO]
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("  if the retry still contradicts, the stamp follows the takeaway's plain no",
  _v["verdict"] == "false" and _v["tldr"] == _NO_TLDR and fg.models.calls == 4, _v)
t("  and a retry that writes the same sentence costs no second reading",
  fg.models.script == [] and fg.models.calls == 4)
t("  and the words are kept, never swapped for a stock line",
  not breakdown.stamp_conflict(_v["verdict"], _v["tldr"], _v["explanation"],
                               "Sugar makes children hyperactive")
  and not hasattr(verdict, "LEAN_TLDR") and "point towards" not in _v["tldr"])
fg.models.calls = 0
_SUGAR_TRUE = Resp(VJ("true", [1, 2], tldr=_NO_TLDR,
                       evidence=["Sugar did not change behaviour (Study 1)."]))
fg.models.script = [_SUGAR_TRUE, DIR("no"), _SUGAR_TRUE]
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("  a stamp over opposite words follows the words, whichever way they point",
  _v["verdict"] == "false" and _v["tldr"] == _NO_TLDR, _v)
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
  evidence.review_certainty(_ASHWA[0]) in ("low", "very low")
  and evidence.review_certainty(_study("R", "r", abstract="Moderate certainty evidence showed a benefit.",
                                       types=["Meta-Analysis"])) == "moderate")
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
  and og.LABELS["insufficient"] == "Not enough evidence"
  and 'insufficient: "Not enough evidence"' in open("static/app.js").read())
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
    ("red wine is gud for ur hart", "Red wine is good for your heart", True),
    ("y does coffe make u pee", "Why does coffee make you pee", True),
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
t("  a claim that says 'not' is answered no by a plain yes, so its 'false' stays false",
  breakdown.stance("Smoking causes lung cancer, and it is the leading cause worldwide.",
                   "Smoking does not cause lung cancer") == "no"
  and breakdown.stamp_conflict("false", "Smoking does cause lung cancer.", "",
                               "Smoking does not cause lung cancer") is None
  and breakdown.stance("Vaccines do not cause autism.", "Vaccines have no link to autism") == "yes"
  and breakdown.stance("Smoking may raise lung cancer risk.", "Smoking does not cause lung cancer") == "mixed")
t("  a 'not' inside 'people who don't' is not the claim saying no",
  breakdown.stance("People who sleep less gain more weight.",
                   "People who do not sleep enough gain weight") == "yes"
  and breakdown.stance("Red wine is not good for your heart.", "Red wine is good for your heart") == "no")
t("  'should not stop' is a plain no to 'can safely stop'",
  breakdown.stance("People who have had a heart attack should not stop taking statins, as trials show they prevent repeat attacks.",
                   "People who have had a heart attack can safely stop taking statins") == "no"
  and breakdown.stamp_conflict("false", "Pregnant women should not drink alcohol.", "",
                               "Drinking a little alcohol during pregnancy is safe") is None
  and breakdown.claim_denies("You should not eat eggs every day")
  and not breakdown.claim_denies("People who should not drink milk feel bloated"))
t("  'not more dangerous than' is a plain no to 'more dangerous than'",
  breakdown.stance("The MMR vaccine is not more dangerous than measles, which causes serious complications and death.",
                   "The MMR vaccine is more dangerous than measles itself") == "no"
  and breakdown.stance("Raw milk isn't healthier than pasteurized milk.",
                       "Raw milk is healthier than pasteurized milk") == "no"
  and breakdown.stance("Raw milk may not be healthier than pasteurized milk.",
                       "Raw milk is healthier than pasteurized milk") == "mixed")
t("  softening's own negation check leaves the verdict reader's alone",
  breakdown._NEGATED.search("No, smoking is the main cause of lung cancer") is None)

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
  "KNUCKLE1" in _merged and "KNUCKLE2" in _merged and len(_merged) == 16 and len(_seen) == 4, _merged)

# A phrase PubMed has never seen is split into loose words, and slang like
# "ab exercises" became "ab" (abnormalities) AND exercise.
_asked = []
def _fake_unknown(q, max_results=8):
    _asked.append(q)
    for phrase in ("ab exercises", "wobbly knee"):
        if f'"{phrase}"' in q:
            pubmed._UNKNOWN_PHRASES[q] = [phrase]
    return ["LOOSE1"] if "ab exercises" in q else ["KNEE1"]
pubmed.search_pubmed = _fake_unknown
_slang = pubmed.search_phrases('("ab exercises") AND ("belly fat" OR "abdominal fat")')
_outcome = pubmed.search_phrases('("knuckle cracking") AND (arthritis OR "wobbly knee")')
pubmed.search_pubmed = _real_search
t("  a subject PubMed does not know as a phrase is not searched as one",
  _slang == [] and sum("ab exercises" in q for q in _asked) == 1, (_slang, _asked))
t("  an unknown outcome phrase is dropped and the search asked again",
  _outcome == ["KNEE1"] and _asked[-1] == '("knuckle cracking") AND (arthritis)'
  and not pubmed._UNKNOWN_PHRASES, (_outcome, _asked))

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
_pending = (ROOT / "static" / "app.js").read_text().split("function renderPending")[1].split("\nfunction ")[0]
t("  and a check in progress hides it, so the work is what the reader sees",
  "if (trustSection) trustSection.hidden = true;" in _pending)

# ---- the same claim always runs the same search
fg.models.script = [Resp("(salt) AND (blood pressure)"), Resp(VJ("complicated", [1]))]
_n0 = fg.models.calls
P("Salt raises blood pressure in everyone", ip="9.9.4.1")
t("the search call asks for temperature 0",
  fg.models.configs[_n0].get("temperature") == 0, fg.models.configs[_n0:])
t("  and so does the call that writes the verdict, both with a fixed seed",
  fg.models.configs[_n0 + 1].get("temperature") == 0
  and fg.models.configs[_n0].get("seed") == 0 == fg.models.configs[_n0 + 1].get("seed"),
  fg.models.configs[_n0:])
t("  and the search is saved for the claim",
  db.get_cached_query("salt raises blood pressure in everyone.", verdict.QUERY_PROMPT_VERSION)
  == ("(salt) AND (blood pressure)", "", "Salt raises blood pressure in everyone"))
with db.get_conn() as conn:
    conn.execute("DELETE FROM verdict_cache")
fg.models.script = [Resp(VJ("complicated", [1]))]
_n0 = fg.models.calls
_d = P("Salt raises blood pressure in everyone", ip="9.9.4.2").get_json()
t("  a re-check reuses it: one call, the verdict, and the same search",
  fg.models.calls == _n0 + 1 and _d["search_query_used"] == "(salt) AND (blood pressure)", _d.get("search_query_used"))

# ---- accuracy round 4 (one rule for the stamp) --------------------------------
# The general rule (accuracy round 4): the takeaway's direction pins the
# stamp. Yes, no and "nothing tested this" each have one stamp; a hedged
# takeaway has none of its own and sits beside any lean.
_COLD = "Cold showers boost immunity"
t("each plain answer pins one stamp, and a hedged one pins none",
  breakdown.words_stamp("complicated", _NO_TLDR, "Sugar makes children hyperactive") == "false"
  and breakdown.words_stamp("complicated", "Smoking causes lung cancer.", "Smoking causes lung cancer") == "true"
  and breakdown.words_stamp("complicated", "No study has tested whether cold showers boost immunity.", _COLD) == "insufficient"
  and breakdown.words_stamp("true", "Exercise slightly lowers the risk of depression.", "Exercise lowers the risk of depression") is None
  and breakdown.words_stamp("complicated", "Magnesium may help sleep a bit, but the proof is weak.",
                            "Magnesium supplements improve sleep quality") is None)
t("  'it's complicated' over 'nothing has tested this' is now a contradiction",
  breakdown.stamp_conflict("complicated", "No study has tested whether cold showers boost immunity.", "", _COLD)
  and not breakdown.stamp_conflict("insufficient", "No study has tested whether cold showers boost immunity.", "", _COLD))
_WATER = "You need to drink 8 glasses of water a day"
t("  the answer after 'so' is the conclusion, and 'eight' is the claim's '8'",
  breakdown.stance("Daily water needs vary by person, and drinks like milk can hydrate you too, "
                   "so eight glasses is not a hard rule.", _WATER) == "no"
  and breakdown.stance("Smoking causes lung cancer, so quitting lowers the risk.",
                       "Smoking causes lung cancer") == "yes"
  and breakdown.stance("Creatine can help some memory tasks, so it may be worth it for some people.",
                       "Creatine improves cognitive performance") == "mixed")
t("  calling the claim itself unsettled is 'not shown', a hedge on a finding is not",
  breakdown.stance("Cold showers may alter immune markers, but it is not clear they truly boost immunity.", _COLD) == "untested"
  and breakdown.stance("Whether vitamin D supplements prevent winter infections is unclear.",
                       "Vitamin D supplements prevent winter infections") == "untested"
  and breakdown.stance("Vitamin D may help a little, but it is unclear whether the effect matters.",
                       "Vitamin D supplements prevent winter infections") == "mixed"
  and breakdown.stance("Magnesium may help you fall asleep a bit faster, but the evidence is unclear.",
                       "Magnesium supplements improve sleep quality") == "mixed")
t("  'not clearly' and 'for most people' hedge a no",
  breakdown.stance("Eating an egg a day does not clearly raise heart risk for most people, so moderate "
                   "intake looks fine.", "Eggs raise heart disease risk") == "mixed")
_UNTESTED_COLD = "No study has tested whether cold showers boost immunity."
_COLDS = [_study("C1", "Cold water immersion and immune cell counts in adults: a randomized trial",
                 abstract="Adults took cold showers for 30 days.")]
fg.models.calls = 0
fg.models.prompts = []
_COLD_R = Resp(VJ("complicated", [1], tldr=_UNTESTED_COLD,
                   evidence=["Cold showers changed immune cell counts (Study 1)."]))
fg.models.script = [_COLD_R, DIR("untested"), _COLD_R]
_stamp = _Catch()
logging.getLogger("evident.stamp").addHandler(_stamp)
_v = verdict.weigh_evidence(_COLD, [dict(x) for x in _COLDS], "(cold shower) AND (immunity)")
logging.getLogger("evident.stamp").removeHandler(_stamp)
t("  the direction is read by one small call at temperature 0",
  fg.models.calls == 4 and f'Answer: "{_UNTESTED_COLD}"' in fg.models.prompts[1]
  and fg.models.configs[-3].get("temperature") == 0, fg.models.prompts[1])
t("  and the retry is shown both halves and told they must agree",
  f'the takeaway, "{_UNTESTED_COLD}"' in fg.models.prompts[2]
  and 'the verdict is "complicated"' in fg.models.prompts[2]
  and "must agree" in fg.models.prompts[2] and '"insufficient"' in fg.models.prompts[2],
  fg.models.prompts[2:])
t("  still apart, 'nothing tested this' makes it 'Not enough evidence', and says so in the log",
  _v["verdict"] == "insufficient" and _v["tldr"] == _UNTESTED_COLD
  and len(_stamp.lines) == 1 and "follows the takeaway" in _stamp.lines[0], (_v, _stamp.lines))
_ASH_R = Resp(VJ("complicated", [1, 2], tldr="Ashwagandha reduces stress.",
                  evidence=["Ashwagandha reduced stress (Study 1)."]))
fg.models.script = [_ASH_R, DIR("yes"), _ASH_R]
_v = verdict.weigh_evidence("Ashwagandha reduces stress", _ASHWA, "(ashwagandha) AND (stress)")
t("  a plain yes followed to 'true' still meets every gate: low certainty brings it back",
  _v["verdict"] == "complicated" and "certainty of that evidence is low" in _v["tldr"], _v)
_VITD_R = Resp(VJ("complicated", [1, 2], tldr="Vitamin D stops colds.",
                   evidence=["Vitamin D cut colds (Study 1)."]))
fg.models.script = [_VITD_R, DIR("yes"), _VITD_R]
_v = verdict.weigh_evidence("Vitamin D stops colds", _MEASLES, "(vitamin d) AND (colds)")
t("  and so does the one-group downgrade",
  _v["verdict"] == "complicated" and "Every study behind this" in _v["explanation"]
  and not breakdown.stamp_conflict(_v["verdict"], _v["tldr"], "", "Vitamin D stops colds"), _v)
_GUIDE = "Daily water needs vary by person and food intake, so 8 glasses is just a rough guide."
_WATER_S = [_study("H1", "Human water needs: a review of hydration in adults", types=["Systematic Review"])]
_GUIDE_R = Resp(VJ("complicated", [1], tldr=_GUIDE, evidence=["Water needs varied (Study 1)."]))
# The same takeaway under a claim that is not a requirement ("is the right
# amount"), so the rule for a fixed requirement does not decide these and the
# small call still does; the requirement itself is tested further down.
_AMOUNT = "8 glasses of water a day is the right amount"
t("  the code screen misreads 'just a rough guide' as a plain yes",
  breakdown.words_stamp("complicated", _GUIDE, _AMOUNT) == "true")
fg.models.calls = 0
fg.models.script = [_GUIDE_R, DIR("no"), _GUIDE_R]
_v = verdict.weigh_evidence(_AMOUNT, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  so the small call decides, and the stamp becomes 'Likely false', never 'Likely true'",
  _v["verdict"] == "false" and _v["tldr"] == _GUIDE and fg.models.calls == 3, _v)
fg.models.calls = 0
fg.models.script = [_GUIDE_R, DIR("mixed")]
_v = verdict.weigh_evidence(_AMOUNT, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  the small call is told a fixed rule that 'varies' or is 'a rough guide' is answered no",
  "only a rough guide or rule of thumb" in verdict._direction_prompt(_WATER, _GUIDE)
  and "there is not one" in verdict._direction_prompt(_WATER, _GUIDE))
t("  a reading that agrees with the stamp means a false alarm: no retry, nothing changes",
  _v["verdict"] == "complicated" and fg.models.calls == 2, (_v, fg.models.calls))
fg.models.calls = 0
fg.models.script = [_GUIDE_R, Resp("not json")]
_v = verdict.weigh_evidence(_AMOUNT, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  and a reading that fails changes nothing either",
  _v["verdict"] == "complicated" and _v["tldr"] == _GUIDE and fg.models.calls == 2, (_v, fg.models.calls))
fg.models.calls = 0
fg.models.script = [_GUIDE_R]
_v = verdict.weigh_evidence(_WATER, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  for a claim that states a requirement the rule decides at once: 'false', no small call",
  _v["verdict"] == "false" and _v["tldr"] == _GUIDE and fg.models.calls == 1
  and breakdown.words_stamp("false", _GUIDE, _WATER) is None
  and not breakdown.stamp_conflict("false", _GUIDE, "", _WATER), (_v, fg.models.calls))
t("  the screen reads a reason after ', as' as the reason, not the answer",
  breakdown.stance("Cracking your knuckles does not cause arthritis, as studies show no link.",
                   "Cracking your knuckles causes arthritis") == "no"
  and breakdown.stance("Smoking causes lung cancer, as smoking remains the main cause.",
                       "Smoking causes lung cancer") == "yes")
fg.models.script = [Resp(VJ("false", [1, 2], tldr=_NO_TLDR,
                            explanation="The claim is true for some children (Study 1). Sugar did not change behaviour (Study 2).",
                            evidence=["Sugar did not change behaviour (Study 1)."]))] * 2
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("  an explanation sentence that rules against an agreed answer is the one that goes",
  _v["verdict"] == "false" and "The claim is true" not in _v["explanation"]
  and "did not change behaviour" in _v["explanation"], _v)


# Red wine: reviews of how a compound might work are background, and a main
# review that puts the link down to lifestyle is said in the short answer.
_WINE = "Red wine is good for the heart"
_WINE_SR = _study("W1", "Alcohol Consumption and Cardiovascular Health.",
                  types=["Journal Article", "Systematic Review"],
                  abstract="The study population included a total of 1,579,435 individuals based on 56 "
                           "cohorts. Although moderate wine consumption is probably associated with low "
                           "cardiovascular disease events, there are many confounding factors, in "
                           "particular, lifestyle, genetic, and socioeconomic associations with wine "
                           "drinking, which likely explain much of the association with wine and "
                           "reduced cardiovascular disease events.")
_WINE_RES = _study("W2", "Resveratrol and Cardiovascular Diseases.", types=["Journal Article", "Review"],
                   abstract="RES is known for its antioxidant and anti-inflammatory properties and for its "
                            "ability to upregulate endothelial NO synthase. In bovine aortic endothelial "
                            "cells under glucose-induced oxidative stress, RES restored the activity of an "
                            "enzyme. Preclinical studies have made it possible to identify molecular "
                            "targets; however, there are limited human clinical trials.")
_WINE_POLY = _study("W3", "Cardioprotective effects of dietary polyphenols.", types=["Journal Article", "Review"],
                    abstract="Dietary polyphenols have been shown to possess cardioprotective effects. Many "
                             "studies have shown inhibitory effects of polyphenols on LDL oxidation. These "
                             "compounds possess additional cardioprotective functions.")
_ASHWA_REV = _study("W4", "Effects of Withania somnifera (Ashwagandha) on Stress", types=["Journal Article", "Review"],
                    abstract="This review summarises preclinical and clinical studies. Reports of human trials "
                             "and animal studies were collected. Extracts exhibited anti-stress activity in "
                             "animal and human studies, through GABAergic pathways and active compounds. It "
                             "appears safe for human use.")
t("a narrative review of compounds and mechanisms is background, like the lab work it reviews",
  evidence.mechanism_review(_WINE_RES) and evidence.mechanism_review(_WINE_POLY)
  and evidence.background([_WINE_SR, _WINE_RES, _WINE_POLY], _WINE) == ["W2", "W3"])
t("  a review that sums up human trials is not, and a systematic review never is",
  not evidence.mechanism_review(_ASHWA_REV) and not evidence.mechanism_review(_WINE_SR)
  and not evidence.mechanism_review(_study("W5", "Polyphenols and the heart", types=["Review"],
                                           abstract="We randomised 300 adults. Antioxidant compounds "
                                                    "acted on molecular pathways in cells.")))
t("a review that says lifestyle likely explains its link is confounded; the routine caveat is not",
  evidence.confounded(_WINE_SR)
  and not evidence.confounded(_study("W6", "Eggs and heart disease: a meta-analysis", types=["Meta-Analysis"],
                                     abstract="Residual confounding cannot be excluded."))
  and not evidence.confounded(_study("W7", "Wine and the heart", types=["Review"],
                                     abstract="Confounding likely explains much of the association.")))
t("  a takeaway that puts the link down to other factors says so, one that only names them does not",
  breakdown.says_confounded("Wine drinkers have fewer heart problems, but lifestyle and other factors "
                            "likely explain much of that link.")
  and not breakdown.says_confounded("Moderate wine may lower heart risks, but lifestyle factors and "
                                    "heavy drinking complicate the picture."))
t("  and saying so is a qualified answer, never a plain yes",
  breakdown.stance("Wine drinkers have fewer heart problems, but the main review says lifestyle likely "
                   "explains much of that.", _WINE) == "mixed")
t("  a 'true' resting on that review is capped, and its line says why",
  evidence.certainty_cap([_WINE_SR], ["W1"]) == evidence.CONFOUNDED_REASON
  and evidence.evidence_line("complicated", [_WINE_SR], ["W1"], _WINE).startswith("Low certainty")
  and not evidence.evidence_line("false", [_WINE_SR], ["W1"], _WINE).startswith("Low certainty"))
t("  only a set with such a review gets the extra paragraph",
  "Study 1 says the link it found is likely explained" in verdict._confounded_note([_WINE_SR, _WINE_RES])
  and verdict._confounded_note([_WINE_RES, _WINE_POLY]) == "")
_WINE_SET = [_WINE_SR, _WINE_RES, _WINE_POLY]
_WINE_MAY = "Moderate wine may lower heart risks, but lifestyle factors and heavy drinking complicate the picture."
_WINE_SAYS = "Wine drinkers have fewer heart problems, but lifestyle and other factors likely explain much of that link."
fg.models.calls = 0
fg.models.prompts = []
fg.models.script = [Resp(VJ("complicated", [1, 2, 3], tldr=_WINE_MAY,
                            evidence=["Wine was linked to fewer heart deaths (Study 1)."])),
                    Resp(VJ("complicated", [1, 2, 3], tldr=_WINE_SAYS,
                            evidence=["Wine was linked to fewer heart deaths (Study 1)."]))]
_v = verdict.weigh_evidence(_WINE, [dict(x) for x in _WINE_SET], "(red wine) AND (heart)")
t("red wine: the resveratrol reviews are background, the takeaway that skipped confounding is asked again",
  _v["cited_studies"] == ["W1"] and fg.models.calls == 3 and _v["tldr"] == _WINE_SAYS
  and "Study 1, a review the answer relies on" in fg.models.prompts[1]
  and "Study 1 says the link it found is likely explained" in fg.models.prompts[0], (_v, fg.models.calls))
fg.models.script = [Resp(VJ("complicated", [1, 2, 3], tldr=_WINE_MAY,
                            evidence=["Wine was linked to fewer heart deaths (Study 1)."]))] * 2
_v = verdict.weigh_evidence(_WINE, [dict(x) for x in _WINE_SET], "(red wine) AND (heart)")
t("  still left out, the takeaway is replaced, not added to",
  _v["tldr"] == verdict.CONFOUNDED_TLDR and "may lower" not in _v["tldr"]
  and not breakdown.stamp_conflict(_v["verdict"], _v["tldr"], "", _WINE), _v)
fg.models.script = [Resp(VJ("true", [1], tldr=_WINE_SAYS,
                            evidence=["Wine was linked to fewer heart deaths (Study 1)."]))]
_v = verdict.weigh_evidence(_WINE, [dict(x) for x in _WINE_SET], "(red wine) AND (heart)")
t("  and a 'true' over it is 'It's complicated', with the reason in the explanation",
  _v["verdict"] == "complicated" and "lifestyle and other factors likely explain" in _v["explanation"], _v)

# ---- accuracy round 3 (apple cider vinegar "cures" diabetes) -------------------
_ACV = [_study("A1", "Apple cider vinegar and glycemic control in type 2 diabetes: a meta-analysis",
               types=["Meta-Analysis"], abstract="Adults with type 2 diabetes took vinegar."),
        _study("A2", "Apple cider vinegar in patients with type 2 diabetes: a randomized trial",
               abstract="Patients with type 2 diabetes took 30 ml a day."),
        _study("A3", "Apple cider vinegar attenuates lipid profile in normal and diabetic rats.",
               types=["Journal Article"], abstract="Diabetic rats were fed vinegar for four weeks.")]
# 1. A claim that promises the whole thing is tested as written. The live
# check stamped "It's complicated" over "it does not cure it".
_ACV_CLAIM = "Apple cider vinegar cure diabetes"
_ACV_TLDR = "Apple cider vinegar can lower blood sugar a bit in people with diabetes, but it does not cure it."
t("a claim that says cure, reverses or completely prevents is absolute",
  breakdown.absolute_claim(_ACV_CLAIM) == "cure"
  and breakdown.absolute_claim("Low carb diets reverse type 2 diabetes") == "reverse"
  and breakdown.absolute_claim("Sunscreen completely prevents skin cancer") == "completely prevents")
t("  and plain 'prevents', cured meats, a subject and a claim that says no are not",
  not any(breakdown.absolute_claim(c) for c in (
      "Vitamin D supplements prevent winter infections", "Cured meats cause cancer",
      "Eliminating sugar clears skin", "Reverse dieting boosts metabolism",
      "Vinegar does not cure diabetes")))
t("  'it lowers blood sugar a bit, but it does not cure it' is a plain no to it",
  breakdown.stance(_ACV_TLDR, _ACV_CLAIM) == "no"
  and breakdown.stance("It lowers blood sugar a little, but it is not a cure.", _ACV_CLAIM) == "no"
  and not breakdown.stamp_conflict("false", _ACV_TLDR, "", _ACV_CLAIM))
t("  the noun is not softened: 'rather than a full cure' stays a full cure",
  breakdown.soften("Modest control rather than a full cure.") == "Modest control rather than a full cure."
  and breakdown.soften("There is no cure for it.") == "There is no cure for it."
  and breakdown.soften("Vinegar cures diabetes.") == "Vinegar helps with diabetes.")
t("  curing diabetes is about people with diabetes, so their trials are not 'one narrow group'",
  "people with diabetes" in evidence.claim_groups(_ACV_CLAIM)
  and "people with diabetes" not in evidence.claim_groups("Regular physical activity reduces the risk of type 2 diabetes")
  and not evidence.narrow_only([_study("N1", "Vinegar in adults with type 2 diabetes")], ["N1"], _ACV_CLAIM))
_abs_prompt = verdict.weigh_prompt(_ACV_CLAIM, "S")
_plain_prompt = verdict.weigh_prompt("Vitamin D supplements prevent winter infections", "S")
t("  only an absolute claim's prompt carries the note, and the old example goes",
  'it says "cure"' in _abs_prompt and 'is not "cures\ndiabetes"' not in _abs_prompt
  and "promises the whole thing" not in _plain_prompt
  and '(for example, "slightly lowers blood sugar" is not "cures\ndiabetes")' in _plain_prompt)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr=_ACV_TLDR,
                            evidence=["Vinegar lowered fasting blood sugar (Study 1)."]))]
_v = verdict.weigh_evidence(_ACV_CLAIM, [dict(s) for s in _ACV], "(vinegar) AND (diabetes)")
t("  'complicated' over 'it does not cure it' becomes 'Likely false', and says why",
  _v["verdict"] == "false" and _v["tldr"] == _ACV_TLDR
  and 'The claim says "cure"' in _v["explanation"], _v)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Vinegar can lower blood sugar a bit in people with diabetes.",
                            evidence=["Vinegar lowered fasting blood sugar (Study 1)."]))]
_v = verdict.weigh_evidence(_ACV_CLAIM, [dict(s) for s in _ACV], "(vinegar) AND (diabetes)")
t("  words that never say no leave the stamp alone",
  _v["verdict"] == "complicated", _v)
fg.models.script = [Resp(VJ("complicated", [3], tldr=_ACV_TLDR,
                            evidence=["Rats had lower blood sugar (Study 3)."]))]
_v = verdict.weigh_evidence(_ACV_CLAIM, [dict(s) for s in _ACV], "(vinegar) AND (diabetes)")
t("  and a rat study alone never makes it false",
  _v["verdict"] == "insufficient" and _v["cited_studies"] == [], _v)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Cinnamon lowers blood sugar a bit, but it does not cure it.",
                            evidence=["Cinnamon lowered fasting blood sugar (Study 1)."]))] * 2
_v = verdict.weigh_evidence("Cinnamon lowers blood sugar", [dict(s) for s in _ACV], "(cinnamon) AND (blood sugar)")
t("  a claim that promises less is never raised",
  _v["verdict"] == "complicated", _v)

# ---- accuracy round 4 (a claim that says everyone needs a fixed amount) ---------
# "You need 8 glasses of water a day" came back "It's complicated" over a
# takeaway that said needs vary and 8 glasses is only a rough guide. The
# takeaway had already ruled the requirement out as worded, so the stamp
# follows it, the way it does for a claim that promises a cure.
t("a claim that says everyone needs, must or has to do a fixed thing is a requirement",
  all(breakdown.universal_requirement(c) for c in (
      "You need to drink 8 glasses of water a day", "Everyone needs 8 hours of sleep a night",
      "Adults must take 1000 mg of calcium daily", "You have to walk 10,000 steps a day",
      "You should eat five servings of vegetables every day",
      "Eight hours of sleep is necessary")))
t("  and a claim that says no, hedges, names a group or has no fixed amount is not",
  not any(breakdown.universal_requirement(c) for c in (
      "You don't need 8 glasses of water a day", "Most adults need 8 hours of sleep",
      "You may need 8 glasses of water a day", "Not everyone needs 8 hours of sleep",
      "People with diabetes should check their blood sugar daily",
      "Drinking water helps weight loss", "You need to exercise",
      "Water is essential for life")))
t("a takeaway that says the need varies, or the number is a guide, rejects it",
  all(breakdown.says_need_varies(x) for x in (
      "Water needs vary a lot by person and food, so 8 glasses is only a rough guide.",
      "How much sleep a person needs depends on age and health.",
      "There is no single amount of calcium that everyone needs.",
      "Eight hours is a rule of thumb, not a strict rule.",
      "Individual needs differ, so no fixed number of steps fits everyone.",
      "People need different amounts of vitamin D depending on sun exposure.")))
t("  and one that keeps the number for most people, denies the variation, or is about a result does not",
  not any(breakdown.says_need_varies(x) for x in (
      "Needs vary by person, but 8 hours suits most adults.",
      "Most adults need 7 to 9 hours, and needs vary a little.",
      "Sleep needs do not vary much between healthy adults.",
      "There is no evidence that needs vary.",
      "Eight glasses is not just a rough guide, it is a firm requirement.",
      "The effect of vitamin D varies by person.",
      "Results vary across studies and the certainty is low.",
      "Daily walking lowers the risk of early death.")))

_REQ = [("You need to drink 8 glasses of water a day", "water", "hydration",
         "Water needs vary a lot by person and food, so 8 glasses is only a rough guide."),
        ("Everyone needs 8 hours of sleep a night", "sleep", "duration",
         "How much sleep a person needs depends on age and health, and no single number fits everyone."),
        ("Adults must take 1000 mg of calcium daily", "calcium", "intake",
         "Calcium requirements differ by age, sex and diet, so a daily figure is only a rule of thumb."),
        ("You have to walk 10,000 steps a day", "steps", "walking",
         "The 10,000 step goal is not a strict rule, and the benefit varies by person.")]


def _req_set(word, other):
    return [_study("Q1", f"{word.title()} and {other} in healthy adults: a systematic review",
                   types=["Systematic Review"], abstract=f"We reviewed {word} and {other} in adults."),
            _study("Q2", f"{word.title()} and {other}: a randomized trial in adults",
                   abstract=f"Adults were given different amounts of {word}.")]


_req_log = []
_req_handler = logging.Handler()
_req_handler.emit = lambda rec: _req_log.append(rec.getMessage())
logging.getLogger("evident.stamp").addHandler(_req_handler)
for _c, _w, _o, _tl in _REQ:
    fg.models.script = [Resp(VJ("complicated", [1, 2], tldr=_tl,
                                evidence=[f"Needs differed between people (Study 1)."]))] * 2
    _req_log.clear()
    _v = verdict.weigh_evidence(_c, _req_set(_w, _o), f"({_w}) AND ({_o})")
    t(f"'complicated' over a takeaway that says the need varies becomes 'false': {_c}",
      _v["verdict"] == "false" and _v["tldr"] == _tl
      and "The claim states a fixed requirement for everyone" in _v["explanation"]
      and not re.search("[–—]", json.dumps(_v, ensure_ascii=False)), _v)
    t("  and the change is logged",
      any("fixed requirement" in m and "'false'" in m for m in _req_log), _req_log)
logging.getLogger("evident.stamp").removeHandler(_req_handler)

_water = "You need to drink 8 glasses of water a day"
_water_tl = _REQ[0][3]
_water_set = _req_set("water", "hydration")
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Water needs vary by person, but 8 glasses suits most adults.",
                            evidence=["Needs differed between people (Study 1)."]))] * 2
_v = verdict.weigh_evidence(_water, [dict(s) for s in _water_set], "(water) AND (hydration)")
t("  a takeaway that keeps the number for most people leaves the stamp alone",
  _v["verdict"] == "complicated", _v)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Water intake and hydration studies are mixed and low certainty.",
                            evidence=["Needs differed between people (Study 1)."]))] * 2
_v = verdict.weigh_evidence(_water, [dict(s) for s in _water_set], "(water) AND (hydration)")
t("  and so does one that never says the need varies",
  _v["verdict"] == "complicated", _v)
fg.models.script = [Resp(VJ("insufficient", [1, 2], tldr=_water_tl,
                            evidence=["Needs differed between people (Study 1)."]))] * 2
_v = verdict.weigh_evidence(_water, [dict(s) for s in _water_set], "(water) AND (hydration)")
t("  'Not enough evidence' is never raised to 'false'",
  _v["verdict"] == "insufficient", _v)
fg.models.script = [Resp(VJ("true", [1, 2], tldr=_water_tl,
                            evidence=["Needs differed between people (Study 1)."]))] * 2
_v = verdict.weigh_evidence(_water, [dict(s) for s in _water_set], "(water) AND (hydration)")
t("  and a 'true' is not touched by this rule",
  _v["verdict"] != "false", _v)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Water needs vary a lot by person and food.",
                            evidence=["Needs differed between people (Study 1)."]))] * 2
_v = verdict.weigh_evidence("Drinking water helps weight loss", [dict(s) for s in _water_set], "(water) AND (weight loss)")
t("  a claim that is not a requirement is never raised, whatever the takeaway says",
  _v["verdict"] == "complicated", _v)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr=_water_tl,
                            evidence=["Needs differed between people (Study 1)."]))] * 2
_v = verdict.weigh_evidence("You don't need 8 glasses of water a day", [dict(s) for s in _water_set],
                            "(water) AND (hydration)")
t("  and a claim that already says you do not need it is answered the ordinary way",
  _v["verdict"] == "complicated" and "fixed requirement" not in _v["explanation"], _v)

# 2. Animal and lab studies are background for a claim about people: never
# counted as used, whatever the model cited, and grouped on their own.
t("a claim is about people unless it names animals or the lab",
  evidence.about_people("Apple cider vinegar cures diabetes")
  and not evidence.about_people("Chocolate is poisonous to dogs")
  and not evidence.about_people("Turmeric kills cancer cells in vitro"))
fg.models.script = [Resp(VJ("complicated", [1, 2, 3], tldr="It lowers blood sugar a little.",
                            evidence=["Vinegar lowered blood sugar (Study 1)."]))]
_v = verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", [dict(s) for s in _ACV],
                            "(vinegar) AND (blood sugar)")
t("  a rat study the model cited is not counted as used",
  _v["cited_studies"] == ["A1", "A2"], _v["cited_studies"])
_acv_studies = [dict(s) for s in _ACV]
fg.models.script = [Resp(VJ("complicated", [1, 2, 3], tldr="It lowers blood sugar a little.",
                            evidence=["Vinegar lowered blood sugar (Study 1)."]))]
verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", _acv_studies, "(vinegar) AND (blood sugar)")
t("  it is marked background on its record, and nothing else is",
  [bool(s.get("background")) for s in _acv_studies] == [False, False, True], _acv_studies)
_snap = evidence.snapshot(_acv_studies, ["A1", "A2"])
t("  the chart counts it as background, not used",
  "2 used for this verdict" in _snap["summary"]
  and "1 animal or lab, background only" in _snap["facts"], _snap)
t("  and says one of them in the singular",
  "1 of them an animal or lab study, background only" in _snap["described"]
  and "2 of them animal or lab studies, background only" in evidence.snapshot(
      _acv_studies + [dict(_acv_studies[2], pmid="A4")], ["A1", "A2"])["described"], _snap["described"])
fg.models.script = [Resp(VJ("complicated", [1, 2, 3], tldr="It lowers blood sugar a little.",
                            evidence=["Vinegar lowered blood sugar in people (Study 1).",
                                      "Rats given vinegar had lower blood sugar (Study 3)."]))]
_v = verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", [dict(s) for s in _ACV],
                            "(vinegar) AND (blood sugar)")
t("  the breakdown's 'Rests on' count leaves it out too",
  _v["breakdown"] and _v["breakdown"]["rests_on"] == 1, _v["breakdown"])
fg.models.script = [Resp(VJ("true", [3], tldr="Vinegar lowers blood sugar.",
                            evidence=["Rats had lower blood sugar (Study 3)."]))]
_v = verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", [dict(s) for s in _ACV],
                            "(vinegar) AND (blood sugar)")
t("  resting on nothing but a rat study is 'not enough evidence', and says why",
  _v["verdict"] == "insufficient" and _v["cited_studies"] == []
  and "animal or lab studies" in _v["explanation"], _v)
_dog = [_study("D1", "Theobromine poisoning in dogs fed chocolate", types=["Journal Article"],
               abstract="Dogs fed chocolate developed signs of poisoning.")]
fg.models.script = [Resp(VJ("true", [1], tldr="Chocolate can poison dogs.",
                            evidence=["Dogs fed chocolate were poisoned (Study 1)."]))]
_v = verdict.weigh_evidence("Chocolate is poisonous to dogs", _dog, "(chocolate) AND (dogs)")
t("  a claim about dogs still counts a study in dogs",
  _v["cited_studies"] == ["D1"], _v)
t("  'what the research says' never picks a line resting only on a rat study",
  breakdown.says("Rats had lower sugar (Study 3). People had lower sugar (Study 1).",
                 {"evidence": []}, _ACV, skip={3}) == ["People had lower sugar (Study 1)."])
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in _ACV]
fg.models.script = [Resp("(vinegar) AND (blood sugar)"),
                    Resp(VJ("complicated", [1, 2, 3], tldr="It lowers blood sugar a little.",
                            evidence=["Vinegar lowered blood sugar (Study 1)."]))]
_r = P("Apple cider vinegar lowers blood sugar after meals", ip="9.9.3.1"); _d = _r.get_json()
_page = c.get("/?q=" + urllib.parse.quote(_d["claim"])).get_data(as_text=True)
t("  the API marks it, and the page and the script both group it as background",
  [s["background"] for s in _d["studies"]] == [False, False, True]
  and [s["cited_in_verdict"] for s in _d["studies"]] == [True, True, False]
  and "Background only: animal and lab studies" in _page
  and "Background only: animal and lab studies" in (ROOT / "static" / "app.js").read_text(), _d["studies"])
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# 3. The line under the chart says what the evidence shows about the claim,
# not how strong the designs were. "So the evidence is strong" under "Likely
# false" read as strong evidence for the cure.
_el = evidence.evidence_line
t("the evidence line says what the evidence is evidence of",
  _el("false", _ACV, ["A1", "A2"], _ACV_CLAIM, None, "cure") == "Strong evidence of a partial effect, not a cure"
  and _el("false", _ACV, ["A1", "A2"], _ACV_CLAIM, "small", "cure") == "Strong evidence of a small effect, not a cure"
  and _el("true", _ACV, ["A1", "A2"], "Vinegar lowers blood sugar", "moderate")
  == "Strong evidence for this claim, a moderate effect"
  and _el("false", _ACV, ["A1", "A2"], "Vinegar raises blood sugar") == "Strong evidence against this claim"
  and _el("complicated", _ACV, ["A1", "A2"], "Vinegar lowers blood sugar in diabetes", "small")
  == "Strong evidence of a small effect, but no plain yes or no")
t("  it names the one group, calls a pilot limited, and is empty with nothing used",
  _el("complicated", _MEASLES, ["M1", "M2"], "Vitamin D stops colds") == "Moderate evidence, but only in children"
  and _el("complicated", [_study("L1", "Pilot trial of vinegar", abstract="A pilot trial in 12 adults.")],
          ["L1"], "Vinegar lowers blood sugar").startswith("Limited evidence")
  and _el("insufficient", _ACV, [], _ACV_CLAIM) == "" and _el("false", _ACV, [], _ACV_CLAIM) == "")
_rec = (ROOT / "static" / "app.js").read_text().split("function recordSentence")[1].split("\nfunction ")[0]
t("  the record line prints it, and no longer calls the designs 'the evidence'",
  "data.evidence_line" in (ROOT / "static" / "app.js").read_text()
  and "the evidence is <mark>" not in _rec and "escapeHtml(line)" in _rec)
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in _ACV]
fg.models.script = [Resp("(vinegar) AND (diabetes)"),
                    Resp(VJ("complicated", [1, 2], tldr=_ACV_TLDR,
                            evidence=["Vinegar lowered fasting blood sugar (Study 1)."]))]
_d = P("Apple cider vinegar cures type 2 diabetes", ip="9.9.3.2").get_json()
t("  a real check carries it in the payload, under the 'Likely false' it explains",
  _d["verdict"] == "false" and _d["evidence_line"] == "Strong evidence of a partial effect, not a cure", _d)
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# 3b. The line never claims more than the designs behind it support. "Strong"
# is for randomized trials that are not small; small trials are "Limited";
# cohort and case-control studies are named for what they are; anything else
# keeps "Moderate". A review's own certainty word never lifts the first two.
_RCT_BIG = _study("RB1", "Folic acid and neural tube defects: a randomized trial",
                  abstract="METHODS: 1,817 women were randomized to folic acid or placebo. RESULTS: Fewer defects.")
_RCT_SMALL = _study("RS1", "Cocoa and blood pressure: a randomized trial",
                    abstract="METHODS: 40 adults were randomized to cocoa or placebo for two weeks. RESULTS: Pressure fell.")
_PILOT_RCT = _study("RP1", "Mouth taping: a pilot study",
                    abstract="A pilot study of 20 adults who were randomized to tape or none.")
_REV_SMALL = _study("RV1", "Ginger: a meta-analysis", types=["Meta-Analysis", "Systematic Review"],
                    abstract="We pooled ten randomized controlled trials. The trials were small and of short duration.")
_COHORT_MA = _study("CO1", "Coffee and diabetes: a meta-analysis of cohort studies", types=["Meta-Analysis"],
                    abstract="We pooled twelve prospective cohort studies. Higher intake was linked with lower risk.")
_CC_REV = _study("CC9", "Deodorant and breast cancer: a meta-analysis", types=["Meta-Analysis"],
                 abstract="We pooled seven case-control studies.")
_UMBRELLA = _study("UM1", "Coffee and health: an umbrella review", types=["Systematic Review"],
                   abstract="We reviewed meta-analyses of coffee intake and health outcomes. Coffee was linked with lower risk.")
_FUTURE = _study("FU1", "Coffee and diabetes: a systematic review", types=["Systematic Review"],
                 abstract="We pooled eight cohort studies. These findings warrant further investigation in randomized trials.")
_ANCILLARY = _study("AN1", "Supplements and survival: a prospective study ancillary to a trial",
                    abstract="A prospective study ancillary to a trial of 3,000 adults evaluated associations "
                             "between supplement use and survival, using Cox regression.")
_CHAPTER = _study("CH1", "Antioxidants: a chapter", types=["Journal Article"], abstract="A narrative chapter.")
_NONRANDOM = _study("NR1", "Ginger: a non-randomized trial", types=["Clinical Trial"],
                    abstract="In this non-randomized trial, 300 adults chose ginger and 300 acted as a control group.")
_SR_UNSAID = _study("SU1", "Ginger for nausea: a systematic review", types=["Systematic Review"],
                    abstract="We reviewed studies of ginger and nausea.")
_REV_HIGH = _study("RH1", "Ginger: a meta-analysis", types=["Meta-Analysis"],
                   abstract="Twelve randomized trials of 4,000 people were pooled. High certainty evidence shows "
                            "ginger reduces vomiting.")
_COHORT_HIGH = _study("CH2", "Ginger: a meta-analysis of cohorts", types=["Meta-Analysis"],
                      abstract="Twelve cohort studies were pooled. High certainty evidence shows ginger reduces vomiting.")
_SMALL_HIGH = _study("SH1", "Ginger: a meta-analysis", types=["Meta-Analysis"],
                     abstract="Ten randomized trials were pooled. The trials were small. High certainty evidence "
                              "shows ginger reduces vomiting.")
_COHORT_LOW = _study("CL1", "Ginger: a meta-analysis of cohorts", types=["Meta-Analysis"],
                     abstract="Twelve cohort studies were pooled. Low certainty evidence shows ginger reduces vomiting.")
_REV_MOD = _study("RM1", "Ginger: a meta-analysis", types=["Meta-Analysis"],
                  abstract="Twelve randomized trials of 4,000 people were pooled. Moderate certainty evidence "
                           "shows ginger reduces vomiting.")
_REV_LOW = _study("RL1", "Ginger: a meta-analysis", types=["Meta-Analysis"],
                  abstract="Ten randomized trials of 3,000 people were pooled. Low certainty evidence suggests "
                           "ginger reduces vomiting.")
_V = "(vomiting)"


def _line(verdict, studies, ids, claim="Ginger helps nausea", **kw):
    return evidence.evidence_line(verdict, studies, ids, claim, **kw)


t("rule 1: Strong is for randomized trials that are not small",
  _line("true", [_RCT_BIG], ["RB1"]) == "Strong evidence for this claim"
  and _line("true", [_COHORT_MA, _RCT_BIG], ["CO1", "RB1"]) == "Strong evidence for this claim"
  and _line("true", [_RCT_SMALL, _RCT_BIG], ["RS1", "RB1"]) == "Strong evidence for this claim"
  and _line("true", [_REV_HIGH], ["RH1"], outcome=_V) == "High certainty evidence for this claim")
t("  and not when any review used rates its own evidence low on the outcome, not only the lead one",
  _line("true", [_REV_MOD, _REV_LOW], ["RM1", "RL1"], outcome=_V) == "Low certainty evidence for this claim"
  and _line("true", [_REV_MOD], ["RM1"], outcome=_V) == "Moderate certainty evidence for this claim"
  and evidence.rated_low_on_outcome([_REV_MOD, _REV_LOW], ["RM1", "RL1"], _V)
  and not evidence.rated_low_on_outcome([_REV_MOD, _REV_LOW], ["RM1"], _V))
t("rule 2: small trials, pilots and reviews of small trials say Limited",
  _line("true", [_RCT_SMALL], ["RS1"]) == "Limited evidence for this claim"
  and _line("true", [_PILOT_RCT], ["RP1"]) == "Limited evidence for this claim"
  and _line("true", [_REV_SMALL], ["RV1"]) == "Limited evidence for this claim"
  and _line("true", [_REV_SMALL, _RCT_SMALL], ["RV1", "RS1"]) == "Limited evidence for this claim"
  and _line("complicated", [_PILOT_RCT, _REV_SMALL], ["RP1", "RV1"]) == "Limited evidence, but no plain yes or no")
t("  a review's high certainty word never lifts small trials above Limited",
  _line("true", [_SMALL_HIGH], ["SH1"], outcome=_V) == "Limited evidence for this claim")
t("rule 3: cohort and case-control studies say what they are, in these words",
  _line("true", [_COHORT_MA], ["CO1"])
  == "Evidence from observational studies (can't show cause) for this claim"
  and _line("false", [_CC_REV], ["CC9"], effect_label="none")
  == "Evidence from observational studies (can't show cause) of no clear effect"
  and _line("false", [_COHORT_MA], ["CO1"])
  == "Evidence from observational studies (can't show cause) against this claim")
t("  a review that never names a design does not undo it, nor do a book chapter or an umbrella review",
  _line("true", [_COHORT_MA, _UMBRELLA], ["CO1", "UM1"]).startswith("Evidence from observational studies")
  and _line("false", [_ANCILLARY, _CHAPTER], ["AN1", "CH1"], effect_label="none").startswith(
      "Evidence from observational studies (can't show cause)"))
t("  a sentence asking for randomized trials does not make a review of cohorts a review of trials",
  evidence.line_design(_FUTURE) == "observational"
  and _line("true", [_FUTURE], ["FU1"]).startswith("Evidence from observational studies"))
t("  a blinded, placebo-controlled review is never called observational",
  _line("true", [_COHORT_MA, _study("PB1", "Sugar and behaviour: a meta-analysis", types=["Meta-Analysis"],
        abstract="Studies that gave sugar or a placebo and blinded the children were pooled.")],
        ["CO1", "PB1"]) == "Moderate evidence for this claim")
t("rule 4: a review's high or moderate word shows only over trials that are not small",
  _line("true", [_COHORT_HIGH], ["CH2"], outcome=_V)
  == "Evidence from observational studies (can't show cause) for this claim"
  and _line("true", [_REV_HIGH], ["RH1"], outcome=_V) == "High certainty evidence for this claim"
  and _line("true", [_COHORT_LOW], ["CL1"], outcome=_V)
  == "Low certainty evidence from observational studies (can't show cause) for this claim")
_COHORT_VL = _study("CV1", "Ginger: a meta-analysis of cohorts", types=["Meta-Analysis"],
                    abstract="Twelve cohort studies were pooled. Very low certainty evidence shows ginger "
                             "reduces vomiting.")
_COHORT_RANGE = _study("CR1", "Ginger: a meta-analysis of cohorts", types=["Meta-Analysis"],
                       abstract="Twelve cohort studies were pooled. The certainty of the evidence ranged from "
                                "very low to low.")
_OBS = "from observational studies (can't show cause)"
t("  a low rating over observational studies keeps both facts, whatever the branch",
  _line("true", [_COHORT_VL], ["CV1"], outcome=_V) == f"Very low certainty evidence {_OBS} for this claim"
  and _line("true", [_COHORT_RANGE], ["CR1"], outcome=_V)
  == f"Very low to low certainty evidence {_OBS} for this claim"
  and _line("true", [_COHORT_LOW], ["CL1"], outcome=_V, effect_label="small")
  == f"Low certainty evidence {_OBS} for this claim, a small effect"
  and _line("false", [_COHORT_LOW], ["CL1"], outcome=_V) == f"Low certainty evidence {_OBS} against this claim"
  and _line("false", [_COHORT_LOW], ["CL1"], outcome=_V, effect_label="none")
  == f"Low certainty evidence {_OBS} of no clear effect"
  and _line("false", [_COHORT_LOW], ["CL1"], "Ginger cures nausea", outcome=_V, effect_label="small",
            absolute="cure") == f"Low certainty evidence {_OBS} of a small effect, not a cure"
  and _line("complicated", [_COHORT_LOW], ["CL1"], outcome=_V)
  == f"Low certainty evidence {_OBS}, but no plain yes or no"
  and _line("complicated", [_COHORT_LOW], ["CL1"], outcome=_V, effect_label="none")
  == f"Low certainty evidence {_OBS} of no clear effect, but no plain yes or no"
  and _line("complicated", [_COHORT_LOW], ["CL1"], outcome=_V, effect_label="small")
  == f"Low certainty evidence {_OBS} of a small effect, but no plain yes or no"
  and _line("complicated", [_study("CK1", "Vitamin D and colds in children: a meta-analysis of cohort studies",
                                   types=["Meta-Analysis"],
                                   abstract="Twelve cohort studies in children were pooled. Low certainty "
                                            "evidence shows vitamin D reduces colds.")],
            ["CK1"], "Vitamin D stops colds", outcome="(colds)")
  == f"Low certainty evidence {_OBS}, but only in children"
  and evidence.level_word([_COHORT_LOW], ["CL1"]) == "Observational")
fg.models.script = [Resp(VJ("true", [1], tldr="Strong evidence says ginger reduces vomiting.",
                            explanation="High-quality evidence from cohorts shows less vomiting (Study 1).",
                            strength="The evidence is strong (Study 1)."))]
_low_obs = verdict.weigh_evidence("Ginger reduces vomiting", [dict(_COHORT_LOW)], "(ginger) AND (vomiting)")
t("  and the prose is still held to observational and low certainty, not strong",
  "trong" not in json.dumps(_low_obs) and "high-quality" not in json.dumps(_low_obs)
  and _low_obs["tldr"].startswith("Low certainty observational evidence says"), _low_obs)

# The prose follows the line's opening, whatever it is. "Strong evidence" is
# for randomized trials, or reviews of them, that no review rates low; every
# other opening pulls the takeaway back to its own word.
_tp = breakdown.temper
_OBS_LOW = "Low certainty evidence from observational studies (can't show cause)"
t("the prose gate takes its word from whatever the line opens with, except Strong and High certainty",
  _tp("There is strong evidence it works.", "Moderate evidence") == "There is moderate evidence it works."
  and _tp("Strong evidence shows a drop.", "Weak evidence") == "Weak evidence shows a drop."
  and _tp("Strong evidence shows a drop.", "Limited evidence") == "Limited evidence shows a drop."
  and _tp("High-certainty evidence shows a drop.", "Low certainty evidence")
  == "Low certainty evidence shows a drop."
  and _tp("Strong evidence shows a drop.", "Very low certainty evidence")
  == "Very low certainty evidence shows a drop."
  and _tp("Strong evidence shows a drop.", "Moderate certainty evidence")
  == "Moderate certainty evidence shows a drop."
  and _tp("Strong evidence shows a drop.", "Very low to low certainty evidence")
  == "Very low to low certainty evidence shows a drop."
  and _tp("Strong evidence shows a drop.", "Evidence from observational studies (can't show cause)")
  == "Observational evidence shows a drop."
  and _tp("Strong evidence shows a drop.", _OBS_LOW) == "Low certainty observational evidence shows a drop."
  and _tp("The evidence is strong.", "Moderate evidence") == "The evidence is moderate."
  and _tp("The evidence is strong.", "Low certainty evidence") == "The evidence is of low certainty."
  and _tp("The evidence is strong.", _OBS_LOW) == "The evidence is observational and of low certainty."
  and _tp("The evidence is of high quality.", "Weak evidence") == "The evidence is weak."
  and all(_tp(x, "Low certainty evidence") == x for x in (
      "There is no strong evidence that it works.", "The evidence is not strong.",
      "Stronger evidence is needed to be sure.", "Strong evidence for the claim is lacking.",
      "It does not strongly suggest a benefit."))
  and _tp("Strong evidence shows a drop.", "Strong evidence") == "Strong evidence shows a drop."
  and _tp("Strong evidence shows a drop.", "High certainty evidence") == "Strong evidence shows a drop.")
fg.models.script = [Resp(VJ("true", [1], tldr="Strong evidence says ginger helps nausea.",
                            explanation="High-quality evidence shows less nausea (Study 1). The evidence is strong.",
                            strength="The evidence is strong (Study 1)."))]
_mixed = verdict.weigh_evidence("Ginger helps nausea", [dict(_NONRANDOM)], "(ginger) AND (nausea)")
t("  a mixed-design verdict whose takeaway says strong evidence comes out moderate evidence",
  evidence.line_head("true", [_NONRANDOM], ["NR1"]) == "Moderate evidence"
  and _mixed["tldr"].startswith("Moderate evidence says ginger helps nausea")
  and "The evidence is moderate" in _mixed["explanation"]
  and "trong" not in json.dumps(_mixed) and "high-quality" not in json.dumps(_mixed), _mixed)
fg.models.script = [Resp(VJ("true", [1], tldr="High-certainty evidence says ginger reduces vomiting.",
                            explanation="Evidence of high certainty shows less vomiting (Study 1).",
                            strength="The evidence is strong (Study 1)."))]
_lowrev = verdict.weigh_evidence("Ginger reduces vomiting", [dict(_REV_LOW)], "(ginger) AND (vomiting)")
t("  a trial review rated low, with high-certainty evidence in the prose, comes out low certainty evidence",
  evidence.line_head(_lowrev["verdict"], [_REV_LOW], ["RL1"], False, _V) == "Low certainty evidence"
  and _lowrev["tldr"].startswith("Low certainty evidence says ginger reduces vomiting")
  and "low certainty evidence shows less vomiting" in _lowrev["explanation"].lower()
  and "trong" not in json.dumps(_lowrev) and "high-certainty" not in json.dumps(_lowrev).lower()
  and "high certainty" not in json.dumps(_lowrev).lower(), _lowrev)
_RCT_COLDS = _study("RC1", "Vitamin C and colds: a randomized trial",
                    abstract="METHODS: 1,817 adults were randomized to vitamin C or placebo. RESULTS: Fewer colds.")
fg.models.script = [Resp(VJ("true", [1], tldr="Strong evidence says vitamin C prevents colds.",
                            explanation="High-quality evidence shows fewer colds (Study 1). The evidence is strong.",
                            strength="The evidence is strong (Study 1)."))]
_strong = verdict.weigh_evidence("Vitamin C prevents colds", [dict(_RCT_COLDS)], "(vitamin C) AND (colds)")
t("  and a Strong line leaves strong evidence alone",
  evidence.line_head("true", [_RCT_COLDS], ["RC1"]) == "Strong evidence"
  and _strong["tldr"].startswith("Strong evidence says vitamin C")
  and "High-quality evidence shows fewer colds" in _strong["explanation"]
  and "The evidence is strong" in _strong["explanation"], _strong)
t("rule 5: the rest of the line is kept, whichever way it opens",
  _line("false", [_COHORT_MA], ["CO1"], "Coffee cures diabetes", effect_label="small", absolute="cure")
  == "Evidence from observational studies (can't show cause) of a small effect, not a cure"
  and _line("complicated", [_COHORT_MA], ["CO1"], effect_label="none")
  == "Evidence from observational studies (can't show cause) of no clear effect, but no plain yes or no"
  and _line("complicated", [_COHORT_MA], ["CO1"], effect_label="small")
  == "Evidence from observational studies (can't show cause) of a small effect, but no plain yes or no"
  and _line("complicated", [_COHORT_MA], ["CO1"])
  == "Evidence from observational studies (can't show cause), but no plain yes or no"
  and _line("false", [_RCT_SMALL], ["RS1"], "Cocoa cures hypertension", effect_label="small", absolute="cure")
  == "Limited evidence of a small effect, not a cure"
  and _line("false", [_RCT_SMALL], ["RS1"], effect_label="none") == "Limited evidence of no clear effect"
  and _line("false", [_RCT_SMALL], ["RS1"]) == "Limited evidence against this claim"
  and _line("complicated", [_RCT_SMALL], ["RS1"], effect_label="small")
  == "Limited evidence of a small effect, but no plain yes or no")
t("  the weakest wording opens no line with the word Strong",
  not any("trong" in _line(v, [x], [x["pmid"]], effect_label=e)
          for v in ("true", "false", "complicated") for e in (None, "none", "small")
          for x in (_COHORT_MA, _RCT_SMALL, _PILOT_RCT, _REV_SMALL, _UMBRELLA)))
t("  a group is named once: a sex label beside a more specific group is dropped",
  evidence.narrow_populations([_study("P1", "Vitamin A in pregnancy"), _study("P2", "Vitamin A in women")],
                              ["P1", "P2"]) == ["pregnant women"]
  and evidence.narrow_populations([_study("P3", "Vitamin A in women")], ["P3"]) == ["women only"])
t("rule 6: non-randomized controlled trials and reviews that never name a design stay Moderate",
  _line("true", [_NONRANDOM], ["NR1"]) == "Moderate evidence for this claim"
  and _line("true", [_SR_UNSAID], ["SU1"]) == "Moderate evidence for this claim"
  and evidence.evidence_basis([_NONRANDOM], ["NR1"]) == "mixed")
_HANDWASH = _study("HW1", "Hand washing and infection: a systematic review", types=["Systematic Review"],
                   abstract="We reviewed studies of hand washing. Causality could not be inferred, and there is "
                            "a need for high-quality evidence.")
_HANDWASH_R = _study("HW2", "Hand washing and infection: a systematic review", types=["Systematic Review"],
                     abstract="We reviewed studies of hand washing. The certainty of the evidence ranged from "
                              "very low to moderate. More high-quality evidence is needed.")
t("a wish for better evidence is not a rating: 'need for high-quality evidence' is not 'high'",
  evidence.review_certainty(_HANDWASH) is None
  and _line("true", [_HANDWASH], ["HW1"]) == "Moderate evidence for this claim"
  and _line("true", [_HANDWASH_R], ["HW2"]) == "Very low to moderate certainty evidence for this claim"
  and evidence.review_certainty(_study("HW3", "Review", types=["Systematic Review"],
      abstract="High certainty evidence is needed. No high quality evidence was found. Evidence of high "
               "quality is lacking.")) is None
  and evidence.review_certainty(_study("HW4", "Review", types=["Systematic Review"],
      abstract="Mortality did not differ, low certainty evidence.")) == "low")
_DC = [_study("DC1", "Dark chocolate and blood pressure: a randomized trial",
              abstract="In 30 adults randomized to dark chocolate or none for two weeks, pressure fell."),
       _study("DC2", "Cocoa and blood pressure: a meta-analysis", types=["Meta-Analysis"],
              abstract="We pooled ten randomized trials of 300 people. The trials were small and short.")]
t("small trials and a review of them are Limited, however the verdict reads",
  _line("true", _DC, ["DC1", "DC2"], "Dark chocolate lowers blood pressure", effect_label="small")
  == "Limited evidence for this claim, a small effect")

# The prose follows the line: a model that read cohort studies still says
# "strong evidence", and the page would say it beside "observational".
_tp = breakdown.temper
t("the prose gate swaps strong, high-quality and high certainty for the line's own word",
  _tp("There is strong evidence that coffee lowers diabetes risk.", "Observational")
  == "There is observational evidence that coffee lowers diabetes risk."
  and _tp("Strong evidence shows a small drop (Study 1).", "Limited")
  == "Limited evidence shows a small drop (Study 1)."
  and _tp("The review found high certainty evidence of a small effect.", "Observational")
  == "The review found observational evidence of a small effect."
  and _tp("High-quality evidence from cohorts suggests a link.", "Observational")
  == "Observational evidence from cohorts suggests a link."
  and _tp("The evidence is strong and consistent.", "Limited") == "The evidence is limited and consistent."
  and _tp("Strong data show a link, and the studies strongly suggest more.", "Observational")
  == "Data show a link, and the studies suggest more.")
t("  and it leaves a negation, a wish, a denial and any other level alone",
  all(_tp(x, "Observational") == x for x in (
      "There is no strong evidence that it works.", "The evidence is not strong.",
      "Stronger evidence is needed to be sure.", "More strong evidence is needed.",
      "They lack strong evidence.", "Strong evidence for the claim is lacking.",
      "It does not strongly suggest a benefit.", "The strongest studies here support this claim."))
  and _tp("There is strong evidence that it works.", "Strong") == "There is strong evidence that it works."
  and _tp("There is strong evidence that it works.", "Strong evidence")
  == "There is strong evidence that it works."
  and _tp("There is strong evidence that it works.", "High certainty evidence")
  == "There is strong evidence that it works."
  and _tp("There is strong evidence that it works.", "") == "There is strong evidence that it works."
  and _tp("", "Limited") == "" and _tp(None, "Limited") == "")
fg.models.script = [Resp(VJ(
    "true", [1], tldr="Strong evidence says coffee lowers the risk of type 2 diabetes.",
    explanation="A pooled review of cohort studies found high-quality evidence of lower risk (Study 1). "
                "The evidence is strong.",
    still_open="Whether strong evidence from trials would agree.",
    evidence=["A review of twelve cohort studies found a lower risk with more coffee (Study 1)."],
    strength="The evidence is strong because twelve studies agree (Study 1)."))]
_obs = verdict.weigh_evidence("Coffee lowers the risk of type 2 diabetes", [dict(_COHORT_MA)],
                              "(coffee) AND (type 2 diabetes)")
_said = json.dumps({k: _obs[k] for k in ("tldr", "explanation", "still_open", "breakdown")})
t("  through a real check, the takeaway, reason and breakdown never call cohort evidence strong",
  _obs["verdict"] == "true" and "trong" not in _said and "high-quality" not in _said
  and "Observational evidence says coffee lowers" in _obs["tldr"], _obs)
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(_COHORT_MA)]
fg.models.script = [Resp("(coffee) AND (type 2 diabetes)"),
                    Resp(VJ("true", [1], tldr="There is strong evidence that coffee lowers the risk.",
                            explanation="High certainty evidence from cohorts shows a lower risk (Study 1).",
                            evidence=["A review of twelve cohort studies found a lower risk (Study 1)."],
                            strength="The evidence is strong (Study 1)."))]
_d = P("Coffee lowers the risk of type 2 diabetes", ip="9.9.4.1").get_json()
_text = json.dumps({k: v for k, v in _d.items() if k not in ("evidence", "studies")})
t("  and an observational-only check carries no 'Strong' or 'strong evidence' in its payload",
  _d["evidence_line"].startswith("Evidence from observational studies (can't show cause)")
  and "Strong" not in _text and "strong evidence" not in _text and "high certainty" not in _text.lower(), _d)
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# 4. Figures are rounded for reading after the gate has checked them, and a
# measured change keeps its unit, or says it never had one.
_ACV_ABS = ("RESULTS: ACV significantly reduced fasting blood sugar (FBS) (WMD: -21.929 mg/dL, "
            "95% CI: -29.19, -14.67, p < 0.001) and HbA1c (WMD: -1.53, 95% CI: -2.65, -0.41, "
            "p = 0.008) and increased insulin (WMD: 2.059 \u03bcu/ml). Seven studies, 319 people.")
_ACV_R = [dict(_ACV[0], abstract=_ACV_ABS)] + _ACV[1:]
_fr = breakdown.for_reading
t("a figure with three decimals is rounded, and an HbA1c change with no unit is left out",
  _fr("Fasting blood sugar fell by 21.929 mg/dL and HbA1c fell by 1.53 in adults (Study 1).", _ACV_R)
  == "Fasting blood sugar fell by about 22 mg/dL in adults (Study 1).")
t("  the live run's own sentences keep about 22 mg/dL and lose the 1.53 and its interval",
  _fr("Study 1 showed drops in fasting blood sugar with a weighted mean difference of -21.929 mg/dL "
      "and HbA1c with -1.53.", _ACV_R)
  == "Study 1 showed drops in fasting blood sugar with an average difference of about 22 mg/dL."
  and _fr("Fasting blood sugar was reduced by -21.929 mg/dL (95% CI: -29.19, -14.67) (Study 1). HbA1c "
          "was reduced by a weighted mean difference of -1.53 (95% CI: -2.65, -0.41) (Study 1).", _ACV_R)
  == "Fasting blood sugar was reduced by about 22 mg/dL (likely range 15 to 29) (Study 1)."
  and _fr("In Study 1, fasting blood sugar fell by 21.929 mg/dL, and HbA1c fell by 1.53 "
          "(95% CI -2.65 to -0.41).", _ACV_R) == "In Study 1, fasting blood sugar fell by about 22 mg/dL.")
t("  a sentence that is only the bare figure goes, and 'Study 1' is not a figure to keep",
  _fr("In Study 1, HbA1c fell by 1.53. It helps a bit.", _ACV_R) == "It helps a bit."
  and _fr("HbA1c fell by 1.53 (Study 1).", _ACV_R) == "")
t("  a unit the model wrote for a figure the abstract printed bare is no unit at all",
  _fr("Fasting blood sugar decreased by about 22 milligrams per deciliter and HbA1c decreased "
      "by 1.53 percent in patients with type 2 diabetes (Study 1).", _ACV_R)
  == "Fasting blood sugar decreased by about 22 milligrams per deciliter in patients with "
     "type 2 diabetes (Study 1)."
  and _fr("HbA1c decreased by 1.53 percent (Study 1).", _ACV_R) == "")
t("  a unit the model dropped is put back from the abstract",
  _fr("Insulin rose by 2.059 (Study 1).", _ACV_R) == "Insulin rose by 2.06 \u03bcU/mL (Study 1).")
t("  an HbA1c change in % is said in percentage points; a level in % is left alone",
  _fr("HbA1c dropped by 0.5% (Study 2).", _ACV_R) == "HbA1c dropped by 0.5 percentage points (Study 2)."
  and _fr("People with HbA1c over 7.5% took part (Study 2).", _ACV_R)
  == "People with HbA1c over 7.5% took part (Study 2).")
t("  p-values, ratios, counts, years and study numbers are never touched",
  _fr("In 2019, 7 trials of 319 people found odds ratio 0.88, p = 0.0042 (Studies 1, 2).", _ACV_R)
  == "In 2019, 7 trials of 319 people found odds ratio 0.88, p = 0.0042 (Studies 1, 2).")
t("  an interval is rounded without 'about', and a range carries its unit once",
  _fr("FBS fell 21.929 mg/dL (95% CI 29.19 to 14.67) (Study 1).", _ACV_R)
  == "FBS fell about 22 mg/dL (likely range 29 to 15) (Study 1)."
  and _fr("Glucose fell between 12.345 and 30.555 mg/dL (Study 1).", _ACV_R)
  == "Glucose fell between about 12 and 31 mg/dL (Study 1).")
t("  one range, one precision: both ends round alike, but two separate figures do not",
  _fr("Sleep came 17.19 min sooner (95% CI -27.19 to -7.44, p = 0.0006) (Study 1).", _ACV_R)
  == "Sleep came about 17 min sooner (likely range -27 to -7, p = 0.0006) (Study 1)."
  and _fr("It took between 7.44 and 27.19 minutes (Study 1).", _ACV_R)
  == "It took between about 7 and 27 minutes (Study 1)."
  and _fr("Odds ratio 0.88 (95% CI 0.81 to 0.96) (Study 1).", _ACV_R)
  == "Odds ratio 0.88 (likely range 0.81 to 0.96) (Study 1)."
  and _fr("Weight fell by 2.35 and 12.67 kg in two groups (Study 1).", _ACV_R)
  == "Weight fell by 2.35 and 13 kg in two groups (Study 1).")
_once = _fr("Fasting blood sugar fell by 21.929 mg/dL and HbA1c fell by 1.53 (Study 1).", _ACV_R)
t("  and reading it twice changes nothing", _fr(_once, _ACV_R) == _once, _once)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="It lowers blood sugar a little.",
                            effect_size="Fasting blood sugar fell by 21.929 mg/dL and HbA1c by 1.53 (Study 1).",
                            evidence=["Vinegar lowered fasting blood sugar by 21.929 mg/dL (Study 1)."]))]
_v = verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", [dict(s) for s in _ACV_R],
                            "(vinegar) AND (blood sugar)")
_bd = _v["breakdown"] or {}
t("  a real verdict prints the rounded figure everywhere, after the gate kept it",
  "about 22 mg/dL" in _bd.get("effect_size", "") and "1.53" not in _bd.get("effect_size", "")
  and not any("21.929" in x for x in [_bd.get("effect_size", "")] + _bd.get("evidence", []) + _bd.get("says", [])),
  _bd)
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="It lowers blood sugar a little.",
                            effect_size="HbA1c fell by 1.53 (Study 1).",
                            evidence=["HbA1c fell by 1.53 (Study 1).", "Vinegar lowered fasting blood sugar (Study 2)."]))]
_v = verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", [dict(s) for s in _ACV_R],
                            "(vinegar) AND (blood sugar)")
_bd = _v["breakdown"] or {}
_seen = " ".join([_v["tldr"], _v["explanation"], _v["still_open"], _bd.get("effect_size", "")]
                 + _bd.get("evidence", []) + _bd.get("says", []))
t("  no unitless figure reaches the page; a field that was only that says it is left out",
  "1.53" not in _seen and "no unit given" not in _seen and "\x00" not in _seen
  and _bd.get("effect_size") == breakdown.UNITLESS_LEFT_OUT
  and _bd.get("evidence") == ["Vinegar lowered fasting blood sugar (Study 2)."], _bd)
fg.models.script = [Resp(VJ("insufficient", [], tldr="No trial has tested this in people.",
                            explanation="HbA1c fell by 1.53 (Study 1)."))]
_v = verdict.weigh_evidence("Apple cider vinegar lowers blood sugar", [dict(s) for s in _ACV_R],
                            "(vinegar) AND (blood sugar)")
t("  an explanation that was only a unitless figure is not replaced by a note about units",
  _v["explanation"] == verdict.NO_REASON_LEFT and "unit" not in _v["explanation"], _v["explanation"])
t("  and the footnote says figures are rounded",
  "rounded for reading" in (ROOT / "templates" / "index.html").read_text()
  and "rounded for reading" in (ROOT / "static" / "app.js").read_text())

# ---- the relevance gate: only tests of the claim in people are used ----------------
# Synthetic records only: the holdout claims stay out of the tests.
_GAR_CLAIM = "Garlic supplements lower cholesterol"
_GAR_Q = "(garlic OR allium sativum) AND (cholesterol OR LDL)"
_GAR_TRIAL = ("Adults with raised cholesterol were randomized to garlic powder or placebo for "
              "twelve weeks. LDL cholesterol was measured at the start and at the end, and "
              "participants kept a food diary throughout the trial period.")
_GAR_ESSAY = ("Garlic has been prized in kitchens and folk medicine for thousands of years, and "
              "its reputation as a friend of the heart has only grown with the supplement trade. "
              "This essay reflects on why the idea is so appealing, how marketing has shaped it, "
              "and what a careful reader might ask before reaching for a bottle of capsules. It "
              "closes with thoughts on how such ideas spread, and why they tend to last so long.")
_GAR = [_study("G1", "Garlic powder and LDL cholesterol in adults: a randomized trial", abstract=_GAR_TRIAL),
        _study("G2", "Dietary garlic and serum cholesterol in growing pigs", types=["Journal Article"],
               abstract="Forty growing pigs were fed garlic for six weeks and serum cholesterol was measured."),
        _study("G3", "Commentary on garlic and heart health", types=["Journal Article"]),
        _study("G4", "What to look for in a garlic supplement", types=["Journal Article"]),
        _study("G5", "Garlic and the heart: an old idea", types=["Journal Article"], abstract=_GAR_ESSAY)]
t("relevance gate: farm animals, commentaries, essays and buying guides are background",
  evidence.background_reasons(_GAR, _GAR_CLAIM)
  == {"G2": evidence.BG_LAB, "G3": evidence.BG_OPINION, "G4": evidence.BG_GUIDE, "G5": evidence.BG_OPINION},
  evidence.background_reasons(_GAR, _GAR_CLAIM))
t("  dairy cows and heifers are animals, whatever the abstract says",
  evidence.preclinical(_study("C1", "Garlic oil and milk cholesterol in dairy cows", types=["Journal Article"],
                              abstract="Milk from the herd was sampled weekly."))
  and evidence.preclinical(_study("C2", "Garlic in the diet of Holstein heifers", types=["Journal Article"])))
t("  an abstract that reports people studied is no essay, and a stub record is not one either",
  not evidence.opinion(_study("E1", "Garlic and the heart", types=["Journal Article"],
                              abstract=_GAR_ESSAY + " We surveyed 300 participants."))
  and not evidence.opinion(_study("E2", "Garlic and the heart", types=["Journal Article"], abstract="Short.")))
t("  a study in dogs is never an opinion piece, so a claim about dogs can still use it",
  evidence.background_reasons([_study("D1", "Garlic and anaemia in dogs", types=["Journal Article"],
                                      abstract="Dogs fed garlic developed anaemia.")],
                              "Garlic is poisonous to dogs") == {})
t("  a safety review is off topic for a claim that something works",
  evidence.off_topic(_study("S1", "Long-term safety of melatonin: a systematic review", types=["Systematic Review"]),
                     "Melatonin helps you fall asleep") == evidence.SAFETY_ONLY
  and evidence.off_topic(_study("S2", "Efficacy and safety of melatonin for insomnia", types=["Systematic Review"]),
                         "Melatonin helps you fall asleep") is None
  and evidence.off_topic(_study("S3", "Long-term safety of melatonin: a systematic review", types=["Systematic Review"]),
                         "Melatonin is safe for children") is None)
_ONION = _study("O1", "Onion extract and LDL cholesterol in adults: a randomized trial",
                abstract=_GAR_TRIAL.replace("garlic powder", "onion extract"))
t("  a record that never names the claim's subject cannot be about it",
  not evidence.names_subject(_ONION, "(garlic OR allium sativum)")
  and evidence.names_subject(_study("A1", "Allium sativum and lipids", abstract=_GAR_TRIAL.replace("garlic", "the bulb")),
                             "(garlic OR allium sativum)")
  and evidence.names_subject(_study("A2", "Lipids in adults", abstract="Too short to rule out."),
                             "(garlic OR allium sativum)"))

def _rel(n, subject=True, outcome=True, in_people=True):
    return {"study": n, "subject": subject, "outcome": outcome, "in_people": in_people}

_gar = [dict(s) for s in _GAR] + [dict(_ONION)]
fg.models.script = [Resp(VJ("true", [1, 2, 3, 4, 5, 6], tldr="Garlic lowers cholesterol a little.",
                            evidence=["Garlic lowered LDL cholesterol (Study 1)."],
                            relevance=[_rel(i) for i in range(1, 7)]))] * 3
_v = verdict.weigh_evidence(_GAR_CLAIM, _gar, _GAR_Q)
t("  through a real verdict, only the trial in people that names garlic is used",
  _v["cited_studies"] == ["G1"], _v["cited_studies"])
t("  the onion trial is off topic, the rest are background with their reasons",
  _gar[5].get("off_topic") == verdict.OFF_SUBJECT
  and [s.get("background") for s in _gar[:5]]
  == [None, evidence.BG_LAB, evidence.BG_OPINION, evidence.BG_GUIDE, evidence.BG_OPINION],
  [(s["pmid"], s.get("background"), s.get("off_topic")) for s in _gar])
_gar2 = [dict(_GAR[0]), dict(_study("G6", "Garlic and cholesterol: a narrative overview", types=["Review"],
                                    abstract=_GAR_TRIAL))]
fg.models.script = [Resp(VJ("true", [1, 2], tldr="Garlic lowers cholesterol a little.",
                            evidence=["Garlic lowered LDL cholesterol (Study 1)."],
                            relevance=[_rel(1, in_people=False), _rel(2, in_people=False)]))] * 3
_v = verdict.weigh_evidence(_GAR_CLAIM, _gar2, _GAR_Q)
t("  a paper the model reads as measuring nothing in people is background; a human trial never is",
  _v["cited_studies"] == ["G1"] and _gar2[1].get("background") == evidence.BG_NOT_TESTED
  and not _gar2[0].get("background"), (_v["cited_studies"], _gar2))
_snap = evidence.snapshot(_gar, ["G1"])
t("  the chart names the group for what is in it, not only animal and lab studies",
  _snap["background_label"] == "Background only: not tests of this claim"
  and "4 background only" in _snap["facts"] and "opinion pieces" in _snap["described"],
  _snap)
t("  and the payload says why each one is background",
  appmod._study_payload(_gar[3], set())["background_why"] == evidence.BG_GUIDE
  and appmod._study_payload({**_gar[1], "background": True}, set())["background_why"] == evidence.BG_LAB)
t("  the page and the script both print the server's group name",
  "background_label" in (ROOT / "templates" / "index.html").read_text()
  and "background_label" in (ROOT / "static" / "app.js").read_text())

# A sentence that cites an off-topic study with only background beside it rests
# on no study that tests the claim and leans on one that tests something else.
# One on background alone is context and stays; one on a study used stays.
_MIXED = ("Adults given garlic had lower LDL (Study 1). "
          "Pigs and adults on onion both improved (Studies 2, 6). "
          "Pigs fed garlic had lower serum cholesterol (Study 2). "
          "Garlic and onion each lowered LDL in adults (Studies 1, 6). "
          "Onion lowered LDL in adults (Study 6). "
          "Garlic is sold as capsules, powder and oil (Studies 3, 4, 6).")
_kept = breakdown.drop_citing(_MIXED, {6}, 6, {2, 3, 4, 5})
t("off-topic plus background: dropped like one citing only the off-topic study",
  "Pigs and adults on onion" not in _kept and "Garlic is sold" not in _kept
  and "Onion lowered LDL in adults" not in _kept, _kept)
t("  background alone is context, so it stays",
  "Pigs fed garlic had lower serum cholesterol (Study 2)." in _kept, _kept)
t("  one study used saves it, off-topic or background beside it or not",
  "Adults given garlic had lower LDL (Study 1)." in _kept
  and "Garlic and onion each lowered LDL in adults (Studies 1, 6)." in _kept, _kept)
t("  without the background list it only drops what rests on off-topic studies, as before",
  breakdown.drop_citing(_MIXED, {6}, 6).count("(Studies 2, 6)") == 1
  and "(Study 6)" not in breakdown.drop_citing(_MIXED, {6}, 6)
  and breakdown.drop_citing(_MIXED, (), 6, {2}) == _MIXED)
t("  a background list alone drops nothing: it needs an off-topic study in the sentence",
  breakdown.drop_citing(_MIXED, (), 6, {2, 3, 4, 5}) == _MIXED
  and breakdown.drop_citing("Pigs fed garlic had lower serum cholesterol (Study 2).", {6}, 6, {2})
  == "Pigs fed garlic had lower serum cholesterol (Study 2).")
_gar3 = [dict(s) for s in _GAR] + [dict(_ONION)]
fg.models.script = [Resp(VJ("true", [1, 2, 3, 4, 5, 6], tldr="Garlic lowers cholesterol a little.",
                            explanation="Garlic powder lowered LDL cholesterol in adults (Study 1). "
                                        "Pigs fed garlic had lower cholesterol, as did adults given onion extract "
                                        "(Studies 2, 6). Pigs fed garlic had lower serum cholesterol (Study 2).",
                            evidence=["Garlic powder lowered LDL cholesterol in adults (Study 1).",
                                      "Pigs fed garlic had lower cholesterol, as did adults given onion extract "
                                      "(Studies 2, 6)."],
                            strength="Pigs fed garlic had lower cholesterol, as did adults given onion extract "
                                     "(Studies 2, 6).",
                            applies_to="Adults with raised cholesterol (Studies 1, 6).",
                            not_applies_to="Adults on onion extract (Studies 5, 6).",
                            relevance=[_rel(i) for i in range(1, 7)]))] * 3
_v = verdict.weigh_evidence(_GAR_CLAIM, _gar3, _GAR_Q)
_bd = _v["breakdown"] or {}
t("  through a real verdict, the explanation loses the off-topic plus background sentence only",
  "Garlic powder lowered LDL cholesterol in adults (Study 1)." in _v["explanation"]
  and "Pigs fed garlic had lower serum cholesterol (Study 2)." in _v["explanation"]
  and "onion" not in _v["explanation"], _v["explanation"])
t("  and so does every breakdown field: paragraphs, strength, who it applies to and who it does not",
  _bd.get("evidence") == ["Garlic powder lowered LDL cholesterol in adults (Study 1)."]
  and not _bd.get("strength") and not _bd.get("not_applies_to")
  and "(Studies 1, 6)" in (_bd.get("applies_to") or ""), _bd)

# ---- the certainty line: the lead review's own words ---------------------------------
_GIN_CLAIM = "Ginger reduces vomiting"
_GIN_Q = "(ginger OR zingiber) AND (vomiting OR emesis)"
def _gin_review(pmid, abstract, types=("Meta-Analysis",)):
    return _study(pmid, "Ginger for nausea and vomiting: a meta-analysis", types=list(types),
                  abstract=abstract)
_SPLIT = ("Twelve randomized trials in adults were pooled. Moderate-certainty evidence shows ginger "
          "reduces vomiting; low-certainty evidence suggests it reduces headache.")
t("certainty: a review's own rating is read in its own words",
  evidence.review_certainty(_gin_review("R1", "Low-certainty evidence suggests ginger reduces vomiting.")) == "low"
  and evidence.review_certainty(_gin_review("R2", "The certainty of evidence ranged from very low to moderate.")) == "very low"
  and evidence.review_certainty(_gin_review("R3", "Ginger reduced vomiting (GRADE: very low).")) == "very low"
  and evidence.review_certainty(_gin_review("R4", "The quality of the evidence was rated as high.")) == "high")
t("  'high-quality trials are needed' rates nothing, and a trial is never a lead review",
  evidence.review_certainty(_gin_review("R5", "More high-quality trials are needed.")) is None
  and evidence.review_certainty(_study("T1", "Ginger in pregnancy",
                                       abstract="Low-certainty evidence suggests ginger reduces vomiting.")) is None)
t("  a review that rates each outcome is read on the claim's own outcome, lowest when none match",
  evidence.review_certainty(_gin_review("R6", _SPLIT), "(vomiting OR emesis)") == "moderate"
  and evidence.review_certainty(_gin_review("R6", _SPLIT), "(headache)") == "low"
  and evidence.review_certainty(_gin_review("R6", _SPLIT), "(sleep)") == "low"
  and evidence.review_certainty(_gin_review("R6", _SPLIT)) == "low")
_LOWREV = _gin_review("L1", "Ten randomized trials were pooled. Low-certainty evidence suggests ginger reduces vomiting.")
_VLREV = _gin_review("L2", "Ten randomized trials were pooled. Very low certainty evidence suggests ginger reduces vomiting.")
_MODREV = _gin_review("L3", _SPLIT)
_PLAIN = _gin_review("L4", "Ten randomized trials were pooled and ginger reduced vomiting.")
t("  the evidence line never says Strong when the lead review says low or very low",
  evidence.evidence_line("complicated", [_LOWREV], ["L1"], _GIN_CLAIM, outcome="(vomiting)")
  == "Low certainty evidence, but no plain yes or no"
  and evidence.evidence_line("false", [_VLREV], ["L2"], _GIN_CLAIM, outcome="(vomiting)")
  == "Very low certainty evidence against this claim"
  and evidence.evidence_line("true", [_MODREV], ["L3"], _GIN_CLAIM, outcome="(vomiting OR emesis)")
  == "Moderate certainty evidence for this claim"
  and evidence.evidence_line("true", [_PLAIN], ["L4"], _GIN_CLAIM, outcome="(vomiting)")
  == "Strong evidence for this claim")
_CASECONTROL = _study("CC1", "Deodorant use and breast cancer: a meta-analysis of case-control studies",
                      types=["Meta-Analysis"], abstract="We pooled seven case-control studies.")
_TRIALREV = _study("TR1", "A meta-analysis", types=["Meta-Analysis"],
                   abstract="We pooled twelve randomized controlled trials.")
t("  and never Strong when the only strong designs pool observational studies",
  evidence.evidence_line("false", [_CASECONTROL], ["CC1"], "Deodorant causes breast cancer",
                         effect_label="none")
  == "Evidence from observational studies (can't show cause) of no clear effect"
  and evidence.evidence_line("false", [_CASECONTROL, _TRIALREV], ["CC1", "TR1"], "x",
                             effect_label="none") == "Strong evidence of no clear effect"
  and evidence.observational_only([_CASECONTROL], ["CC1"])
  and not evidence.observational_only([_CASECONTROL, _TRIALREV], ["CC1", "TR1"]))
t("  the lead review is the first one used that rates itself",
  evidence.lead_certainty([_PLAIN, _VLREV, _MODREV], ["L4", "L2", "L3"], "(vomiting)") == "very low"
  and evidence.lead_certainty([_PLAIN, _VLREV], ["L4"], "(vomiting)") is None)
t("  a true verdict is capped on the claim's outcome, not on another one",
  evidence.certainty_cap([_MODREV], ["L3"], outcome="(vomiting OR emesis)") is None
  and "low" in (evidence.certainty_cap([_MODREV], ["L3"], outcome="(headache)") or "")
  and "very low" in (evidence.certainty_cap([_VLREV], ["L2"], outcome="(vomiting)") or ""))
_BRACKETED = _gin_review("L5", "Twelve cohorts were pooled. Ginger prevented vomiting in 80% (RR 0.20, "
                         "95% CI 0.10 to 0.40; 7 cohort studies; 1,200 adults; moderate certainty evidence). "
                         "Given after surgery, ginger prevented vomiting in 60% (RR 0.40; 2 cohort studies; "
                         "low certainty evidence). Ginger did not change headache (RR 1.10; 3 cohort studies; "
                         "very low certainty evidence).")
_SEVERE = "(severe vomiting OR vomiting episodes OR hospitalization for vomiting)"
t("certainty: a rating inside brackets stays with the finding it rates",
  evidence.certainty_on_outcome(_BRACKETED, "(vomiting)")
  and evidence.certainty_range(_BRACKETED, "(vomiting)") == "low to moderate"
  and evidence.review_certainty(_BRACKETED, "(headache)") == "very low")
t("  an outcome no finding names whole is read on the word its terms share",
  evidence.certainty_on_outcome(_BRACKETED, _SEVERE)
  and evidence.certainty_range(_BRACKETED, _SEVERE) == "low to moderate")
t("  only a review that rates the outcome low and nowhere higher can cap a true",
  evidence.review_certainty(_BRACKETED, _SEVERE) == "low"
  and evidence.review_certainty(_BRACKETED, _SEVERE, strict=True) is None
  and evidence.certainty_cap([_BRACKETED], ["L5"], outcome=_SEVERE) is None
  and evidence.review_certainty(_BRACKETED, "(headache)", strict=True) == "very low"
  and evidence.review_certainty(_gin_review("R6", _SPLIT), "(sleep)", strict=True) is None)
t("  and the certainty line gives the review's own range, never Strong",
  evidence.evidence_line("true", [_BRACKETED], ["L5"], _GIN_CLAIM, outcome=_SEVERE)
  == "Low to moderate certainty evidence from observational studies (can't show cause) "
     "for this claim")
fg.models.script = [Resp(VJ("true", [1], tldr="Ginger prevents vomiting.",
                            evidence=["Ginger prevented vomiting in 80% (Study 1)."]))] * 3
fg.models.prompts = []
_v = verdict.weigh_evidence(_GIN_CLAIM, [dict(_BRACKETED)], "(ginger) AND " + _SEVERE)
t("  through a real verdict, a narrower question's low rating neither caps it nor flags the review",
  _v["verdict"] == "true" and "Twelve cohorts were pooled" in fg.models.prompts[0]
  and "LOW CERTAINTY: this review" not in fg.models.prompts[0], _v)
fg.models.script = [Resp(VJ("true", [1], tldr="Ginger reduces vomiting.",
                            evidence=["Ginger reduced vomiting (Study 1)."]))] * 3
_v = verdict.weigh_evidence(_GIN_CLAIM, [dict(_VLREV)], _GIN_Q)
t("  through a real verdict, a very low certainty review cannot carry a true",
  _v["verdict"] == "complicated" and "very low" in _v["explanation"]
  and "LOW CERTAINTY: this review rates its own evidence on this outcome as very low certainty"
  in fg.models.prompts[-1], _v)
fg.models.script = [Resp(VJ("true", [1], tldr="Ginger reduces vomiting.",
                            evidence=["Ginger reduced vomiting (Study 1)."]))] * 3
_v = verdict.weigh_evidence(_GIN_CLAIM, [dict(_MODREV)], _GIN_Q)
t("  and a review sure of this outcome, unsure of another, keeps it",
  _v["verdict"] == "true", _v)
_animal_sr = {**_study("B1", "Ginger and vomiting in rats: a systematic review of animal studies",
                       types=["Systematic Review"]), "background": evidence.BG_LAB}
_offtopic_ma = {**_study("B2", "Ginger and wound healing: a meta-analysis", types=["Meta-Analysis"]),
                "off_topic": verdict.OFF_SUBJECT}
_snap = evidence.snapshot([_animal_sr, _offtopic_ma, dict(_PLAIN)], ["L4"])
t("  only on-topic studies in people count as strong designs",
  _snap["strong"] == 1 and "1 strong" in _snap["summary"], _snap["summary"])
t("  the page builds the line from the query's outcome group",
  appmod._outcome_group("(ginger) AND (vomiting OR emesis)") == "(vomiting OR emesis)"
  and appmod._outcome_group("ginger") == "")

# ---- the evidence hierarchy: randomized trials outrank weaker studies ----------------
_TEA_CLAIM = "Green tea lowers blood pressure"
_TEA_Q = "(green tea) AND (blood pressure)"
_TEA_MA = _study("H1", "Green tea and blood pressure: a meta-analysis of randomized controlled trials",
                 types=["Meta-Analysis"], abstract="We pooled 14 randomized controlled trials in adults.")
_TEA_COHORT = _study("H2", "Green tea drinking and blood pressure in a cohort of adults",
                     types=["Observational Study"],
                     abstract="A cohort of 5,000 adults was followed for ten years.")
_TEA_SURVEY = _study("H3", "Green tea habits and blood pressure", types=["Journal Article"],
                     abstract="A survey of 2,000 adults asked how much green tea they drank.")
_TEA_PILOT = _study("H4", "Green tea and blood pressure: a randomized pilot trial",
                    abstract="A pilot trial of 20 adults randomized to green tea or placebo.")
_TEA_RCT_NO = _study("H5", "Green tea and blood pressure: a randomized trial",
                     abstract="Adults were randomized to green tea or placebo; 400 completed it.")
t("hierarchy: designs are ranked from the record",
  [evidence.design_rank(x) for x in (_TEA_MA, _TEA_COHORT, _TEA_SURVEY, _TEA_PILOT)]
  == ["trial", "weaker", "weaker", "trial"]
  and evidence.design_rank(_study("M", "Tea: a meta-analysis", types=["Meta-Analysis"],
                                  abstract="We pooled 9 cohort studies.")) == "weaker"
  and evidence.design_rank(_study("X", "Tea: a meta-analysis", types=["Meta-Analysis"],
                                  abstract="We pooled randomized trials and cohort studies.")) == ""
  and evidence.design_rank(_study("N", "Tea: a narrative review", types=["Review"])) == ""
  and evidence.design_rank(_study("R", "Green tea in hypertensive rats", types=["Journal Article"])) == "weaker")
t("  the weaker ones are named in a reader's words",
  [evidence.weaker_kind(x) for x in (_TEA_COHORT, _TEA_SURVEY)] == ["observational studies", "surveys"])

def _f(n, finding):
    return {"study": n, "subject": True, "outcome": True, "in_people": True, "finding": finding}

_TIE = VJ("complicated", [1, 2, 3], tldr="The evidence on green tea is mixed.",
          explanation="Some studies found lower blood pressure and some found none.",
          evidence=["Pooled trials found lower blood pressure (Study 1)."],
          relevance=[_f(1, "for"), _f(2, "against"), _f(3, "against")])
fg.models.calls = 0
fg.models.script = [Resp(_TIE), Resp(_TIE), Resp(_TIE)]
_v = verdict.weigh_evidence(_TEA_CLAIM, [dict(x) for x in (_TEA_MA, _TEA_COHORT, _TEA_SURVEY)], _TEA_Q)
t("  trials say yes, a cohort and a survey say no: the verdict follows the trials, never a tie",
  _v["verdict"] == "true" and _v["tldr"] == verdict.RANKED_TLDR["true"], _v)
t("  and the answer says the weaker studies disagree, by number",
  "Randomized trials (Study 1) found it does" in _v["explanation"]
  and "observational studies and surveys that point the other way (Studies 2 and 3)" in _v["explanation"],
  _v["explanation"])
t("  the model was asked once more before code turned the stamp",
  fg.models.calls == 3 and "Trials outrank them" in fg.models.prompts[-2], fg.models.calls)
fg.models.script = [Resp(_TIE), Resp(VJ("true", [1], tldr="Green tea lowers blood pressure a little.",
                                        explanation="Pooled trials found it lowers blood pressure; "
                                                    "observational studies found no link, which counts for less.",
                                        evidence=["Pooled trials found lower blood pressure (Study 1)."],
                                        relevance=[_f(1, "for"), _f(2, "against"), _f(3, "against")]))]
_v = verdict.weigh_evidence(_TEA_CLAIM, [dict(x) for x in (_TEA_MA, _TEA_COHORT, _TEA_SURVEY)], _TEA_Q)
t("  a retry that follows the trials in its own words is the answer",
  _v["verdict"] == "true" and _v["tldr"] == "Green tea lowers blood pressure a little."
  and "Randomized trials (Study 1)" not in _v["explanation"], _v)
_NO = VJ("true", [1, 2], tldr="Green tea lowers blood pressure.",
         explanation="A cohort study linked green tea with lower blood pressure.",
         evidence=["Green tea drinkers had lower blood pressure (Study 2)."],
         relevance=[_f(1, "against"), _f(2, "for")])
fg.models.script = [Resp(_NO), Resp(_NO), Resp(_NO)]
_v = verdict.weigh_evidence(_TEA_CLAIM, [dict(x) for x in (_TEA_RCT_NO, _TEA_COHORT)], _TEA_Q)
t("  a large trial that found nothing outranks a cohort that found a link",
  _v["verdict"] == "false" and _v["tldr"] == verdict.RANKED_TLDR["false"]
  and "found it does not" in _v["explanation"], _v)
_CIN_CLAIM = "Cinnamon cures diabetes"
_CIN_MA = _study("H6", "Cinnamon and blood sugar: a meta-analysis of randomized controlled trials",
                 types=["Meta-Analysis"], abstract="We pooled 10 randomized controlled trials in adults with diabetes.")
_CIN_COHORT = _study("H7", "Cinnamon intake and diabetes in a cohort of adults",
                     types=["Observational Study"],
                     abstract="A cohort of 3,000 adults was followed for eight years.")
_CURE = VJ("false", [1], tldr="Cinnamon does not cure diabetes, though it may lower blood sugar a little.",
           explanation="Pooled trials found slightly lower blood sugar (Study 1), but none found a cure.",
           evidence=["Pooled trials found slightly lower blood sugar (Study 1)."],
           relevance=[_f(1, "for"), _f(2, "against")])
fg.models.calls = 0
fg.models.script = [Resp(_CURE), Resp(_CURE), Resp(_CURE)]
_v = verdict.weigh_evidence(_CIN_CLAIM, [dict(x) for x in (_CIN_MA, _CIN_COHORT)],
                            "(cinnamon) AND (diabetes OR blood sugar)")
t("  trials marked 'for' never turn the model's no into a yes",
  _v["verdict"] == "false" and _v["tldr"] != verdict.RANKED_TLDR["true"]
  and "fairer test" not in _v["explanation"]
  and not any("Trials outrank them" in p for p in fg.models.prompts[-fg.models.calls:]), _v)
_PILOT = VJ("complicated", [1, 2], tldr="The evidence is mixed.",
            evidence=["A pilot trial found lower blood pressure (Study 1)."],
            relevance=[_f(1, "for"), _f(2, "against")])
fg.models.script = [Resp(_PILOT), Resp(_PILOT)]
_v = verdict.weigh_evidence(_TEA_CLAIM, [dict(x) for x in (_TEA_PILOT, _TEA_COHORT)], _TEA_Q)
t("  one small pilot trial does not outrank everything else",
  _v["verdict"] == "complicated", _v)
_SPLITTRIALS = VJ("complicated", [1, 2, 3], tldr="The evidence is mixed.",
                  evidence=["Pooled trials found lower blood pressure (Study 1)."],
                  relevance=[_f(1, "for"), _f(2, "against"), _f(3, "against")])
fg.models.script = [Resp(_SPLITTRIALS), Resp(_SPLITTRIALS)]
_v = verdict.weigh_evidence(_TEA_CLAIM, [dict(x) for x in (_TEA_MA, _TEA_RCT_NO, _TEA_COHORT)], _TEA_Q)
t("  trials that disagree with each other are a real 'complicated'",
  _v["verdict"] == "complicated", _v)
fg.models.script = [Resp(VJ("true", [1], tldr="Green tea lowers blood pressure a little.",
                            explanation="Pooled trials found it lowers blood pressure (Study 1).",
                            evidence=["Pooled trials found lower blood pressure (Study 1)."],
                            relevance=[_f(1, "for"), _f(2, "against")]))]
_v = verdict.weigh_evidence(_TEA_CLAIM, [dict(x) for x in (_TEA_MA, _TEA_COHORT)], _TEA_Q)
t("  an answer that follows the trials but never mentions the dissent gets one sentence saying so",
  _v["verdict"] == "true" and "count for less" in _v["explanation"]
  and _v["tldr"] == "Green tea lowers blood pressure a little.", _v)

# ---- a placebo-controlled, blinded study is a trial though it never says "randomized" ------
# Synthetic records; none is a holdout claim. The pooled review below describes its trials the
# way an older review does: a placebo condition, blinded subjects, no mention of randomizing.
_SG_CLAIM = "Sugar makes children hyperactive"
_SG_Q = "(sugar) AND (hyperactivity OR ADHD)"
_SG_TRIALS = _study("S3", "The effect of sugar on behavior in children: a meta-analysis",
                    types=["Journal Article", "Meta-Analysis"],
                    abstract="Studies were required to give a known quantity of sugar, to use a placebo "
                             "condition and to blind the subjects, parents and research staff. Sixteen "
                             "reports met the criteria. The confidence interval for all 14 mean effect "
                             "sizes included 0. Sugar does not affect the behavior of children.")
_SG_OBS1 = _study("S1", "Sugar and ADHD symptoms: a systematic review and meta-analysis",
                  types=["Journal Article", "Meta-Analysis", "Systematic Review"],
                  abstract="In this meta-analysis of observational studies, two cross-sectional and two "
                           "case-control studies were included. Sugar intake was linked with ADHD symptoms.")
_SG_OBS2 = _study("S2", "Dietary carbohydrates and ADHD symptoms: a systematic review",
                  types=["Systematic Review", "Journal Article"],
                  abstract="Thirty-eight observational studies were included. Most reported positive "
                           "associations between sweets and ADHD symptoms.")
t("blinded trials: a review of placebo-controlled, blinded studies ranks as trials without the word randomized",
  [evidence.design_rank(x) for x in (_SG_TRIALS, _SG_OBS1, _SG_OBS2)] == ["trial", "weaker", "weaker"]
  and not evidence.says_randomized(_SG_TRIALS))
_blind = lambda abstract, types=("Journal Article", "Meta-Analysis"): _study(
    "B", "Sugar and behavior in children", types=list(types), abstract=abstract)
t("  but a mention of a placebo is not enough on its own: a wish for such trials describes nothing done",
  evidence.design_rank(_blind("Further placebo-controlled trials are needed to settle this.")) == ""
  and evidence.design_rank(_blind("We pooled studies of sugar in children. Future double-blind "
                                  "studies should test it.")) == "")
t("  a review that names an observational design stays the weaker kind, placebo or not",
  evidence.design_rank(_blind("We pooled cohort studies; unlike placebo-controlled trials they "
                              "cannot show cause.")) == "weaker")
t("  one that names both designs, or one that says it was not randomized, counts for neither",
  evidence.design_rank(_blind("We pooled randomized trials and cohort studies, some placebo-controlled.")) == ""
  and evidence.design_rank(_blind("We pooled non-randomized, placebo-controlled studies.")) == "")
t("  a narrative review, and an animal study, are not turned into trials by a placebo group",
  evidence.design_rank(_blind("A placebo group was used.", types=("Review",))) == ""
  and evidence.design_rank(dict(_blind("Rats were given a placebo, blinded to the keeper.",
                                       types=("Journal Article",)), title="Sugar in rats")) == "weaker")
t("  a single study typed as a trial, double-blind against a placebo, is a trial",
  evidence.design_rank(_blind("Children took sugar or a placebo, double-blind.",
                              types=("Clinical Trial",))) == "trial"
  and evidence.design_rank(_blind("Children took sugar or a placebo, double-blind.",
                                  types=("Journal Article",))) == ""
  and evidence.design_rank(_blind("Children took sugar and were watched.",
                                  types=("Clinical Trial",))) == "")
t("  a controlled trial that is not marked randomized is called a controlled trial in the ranking",
  evidence.says_randomized(_SG_TRIALS) is False
  and evidence.says_randomized(_TEA_RCT_NO) is True)

_SG_SAYS = VJ("complicated", [1, 2, 3], tldr="Older reviews find no effect, but newer studies show a link.",
              explanation="A pooled review found no effect of sugar (Study 3). Newer reviews report a link "
                          "with ADHD symptoms (Studies 1, 2).",
              evidence=["A pooled review found no effect of sugar on behavior (Study 3)."],
              relevance=[_f(1, "for"), _f(2, "for"), _f(3, "against")])
fg.models.calls = 0
fg.models.script = [Resp(_SG_SAYS)] * 3
_v = verdict.weigh_evidence(_SG_CLAIM, [dict(x) for x in (_SG_OBS1, _SG_OBS2, _SG_TRIALS)], _SG_Q)
t("  a pooled review of blinded trials that found nothing outranks reviews of observational links",
  _v["verdict"] == "false" and _v["tldr"] == verdict.CONTROLLED_TLDR["false"], _v)
t("  and the page says controlled, not randomized, because the trials never said randomized",
  "Controlled trials (Study 3) found it does not" in _v["explanation"]
  and "Randomized trials (Study 3)" not in _v["explanation"]
  and "reviews of observational studies that point the other way (Studies 1 and 2)" in _v["explanation"]
  and _v["tldr"].startswith("Controlled trials say no"), _v)
t("  the model was asked once more first, in the same words",
  fg.models.calls == 3 and "Controlled trials (Study 3) found it does not work" in fg.models.prompts[-2],
  fg.models.calls)
t("  a takeaway code wrote for controlled trials is not sent to be checked either",
  verdict._code_takeaway(verdict.CONTROLLED_TLDR["false"]))
_SG_RCT = dict(_SG_TRIALS, abstract=_SG_TRIALS["abstract"] + " The studies were randomized.")
fg.models.script = [Resp(_SG_SAYS)] * 3
_v = verdict.weigh_evidence(_SG_CLAIM, [dict(x) for x in (_SG_OBS1, _SG_OBS2, _SG_RCT)], _SG_Q)
t("  one that says it was randomized keeps the word",
  _v["verdict"] == "false" and _v["tldr"] == verdict.RANKED_TLDR["false"]
  and "Randomized trials (Study 3) found it does not" in _v["explanation"], _v)
_SG_FOR = VJ("complicated", [1, 2, 3], tldr="The studies disagree.",
             explanation="A pooled review found an effect of sugar (Study 3), as did newer reviews "
                         "(Studies 1, 2).",
             evidence=["A pooled review found an effect of sugar on behavior (Study 3)."],
             relevance=[_f(1, "for"), _f(2, "for"), _f(3, "for")])
fg.models.script = [Resp(_SG_FOR)] * 3
_v = verdict.weigh_evidence(_SG_CLAIM, [dict(x) for x in (_SG_OBS1, _SG_OBS2, _SG_TRIALS)], _SG_Q)
t("  and nothing is settled when the trials and the observational reviews point the same way",
  _v["verdict"] == "complicated" and "fairer test" not in _v["explanation"], _v)
_SG_MIXED = VJ("complicated", [1, 2, 3], tldr="The studies disagree.",
               explanation="A pooled review was unclear (Study 3), and newer reviews report a link "
                           "(Studies 1, 2).",
               evidence=["A pooled review was unclear on sugar and behavior (Study 3)."],
               relevance=[_f(1, "for"), _f(2, "for"), _f(3, "mixed")])
fg.models.script = [Resp(_SG_MIXED)] * 3
_v = verdict.weigh_evidence(_SG_CLAIM, [dict(x) for x in (_SG_OBS1, _SG_OBS2, _SG_TRIALS)], _SG_Q)
t("  nor when the trials themselves are unclear about it",
  _v["verdict"] == "complicated" and "fairer test" not in _v["explanation"], _v)
_SG_SMALL = dict(_SG_TRIALS, abstract=_SG_TRIALS["abstract"] + " The trials were small and short.")
t("  a review that says its own trials were small cannot outrank anything",
  evidence.can_outrank(_SG_TRIALS) and not evidence.can_outrank(_SG_SMALL)
  and not evidence.can_outrank(_TEA_PILOT) and evidence.can_outrank(_TEA_RCT_NO))
fg.models.script = [Resp(_SG_SAYS)] * 3
_v = verdict.weigh_evidence(_SG_CLAIM, [dict(x) for x in (_SG_OBS1, _SG_OBS2, _SG_SMALL)], _SG_Q)
t("  so with small trials against reviews of links the answer stays complicated",
  _v["verdict"] == "complicated" and "fairer test" not in _v["explanation"]
  and _v["tldr"] != verdict.CONTROLLED_TLDR["false"], _v)

# ---- the harm guard: a risky answer needs a trial or a major review ---------------
t("harm guard: which way a claim leans on a protective practice",
  [(evidence.protective_practice(c) or {}).get("against") for c in (
      "Vaccines weaken the immune system", "Flu vaccines reduce hospital admissions",
      "Unpasteurized cheese is better than pasteurized cheese", "Skipping your prescribed medication is safe",
      "Sunscreen prevents sunburn", "Shade works better than sunscreen")]
  == [True, False, True, True, False, True]
  and evidence.protective_practice("Herbal medications cure colds") is None
  and evidence.protective_practice("Seed oils cause inflammation") is None
  and evidence.claims_safe("Energy drinks are safe for teenagers")
  and not evidence.claims_safe("Energy drinks are not safe for teenagers"))
t("  a significant result is read off the abstract, and a negated one is not",
  evidence.significant({"abstract": "Admissions were significantly reduced (p < 0.01)."})
  and not evidence.significant({"abstract": "There was no significant difference between groups."})
  and not evidence.significant({"abstract": "It did not significantly change; p = 0.40."}))

_FLU_Q = "(flu vaccine OR influenza vaccine) AND (hospital admissions)"
_FLU_COHORT = _study("V1", "Flu vaccine and hospital admissions: a cohort study", types=["Observational Study"],
                     abstract="A cohort of 9,000 adults was followed for two winters.")
_FLU_REVIEW = _study("V2", "Flu vaccines and hospital admissions in adults: a systematic review",
                     types=["Systematic Review"], abstract="We pooled 12 studies of adults.")
fg.models.script = [Resp(VJ("false", [1], tldr="Flu vaccines don't cut hospital admissions.",
                            evidence=["Vaccinated adults were admitted as often (Study 1)."],
                            relevance=[_f(1, "against"), _f(2, "for")]))] * 3
_v = verdict.weigh_evidence("Flu vaccines reduce hospital admissions", [dict(_FLU_COHORT), dict(_FLU_REVIEW)], _FLU_Q)
t("  an answer steering people from vaccination with no trial or review behind it follows the review",
  _v["verdict"] == "true" and "V2" in _v["cited_studies"]
  and _v["tldr"].startswith("The strongest studies here support this claim.")
  and "Going without vaccination" in _v["tldr"]
  and "none here says so" in _v["explanation"] and "(Study 2) points the other way" in _v["explanation"], _v)
t("  and the model was asked to fix it first",
  any("without a randomized trial or a major review" in x for x in fg.models.prompts[-3:]))
fg.models.script = [Resp(VJ("false", [1], tldr="Flu vaccines don't cut hospital admissions.",
                            evidence=["Vaccinated adults were admitted as often (Study 1)."],
                            relevance=[_f(1, "against"), _f(2, "for")]))] * 3
_v = verdict.weigh_evidence("Flu vaccines reduce hospital admissions",
                            [dict(_FLU_COHORT, title="Flu vaccine and admissions in older adults"),
                             dict(_FLU_REVIEW, title="Flu vaccines and admissions in older adults: a review")],
                            _FLU_Q)
t("  a later downgrade replaces a takeaway code wrote, so the two never argue",
  _v["verdict"] == "complicated" and "support this claim" not in _v["tldr"], _v)
_IMM_REVIEW = _study("V3", "Vaccination and immune function: a meta-analysis", types=["Meta-Analysis"],
                     abstract="We pooled 20 studies of immune markers after vaccination.")
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Some studies suggest vaccines tax the immune system.",
                            evidence=["Immune markers dipped after vaccination (Study 1)."],
                            relevance=[_f(1, "for"), _f(2, "against")]))] * 3
_v = verdict.weigh_evidence("Vaccines weaken the immune system",
                            [dict(_FLU_COHORT, pmid="V4"), dict(_IMM_REVIEW)], "(vaccines) AND (immune system)")
t("  a 'complicated' that lends weight to an anti-vaccine claim follows the review that says no",
  _v["verdict"] == "false" and _v["tldr"].startswith("The strongest studies here go against this claim."), _v)
_AP_RCT = _study("P1", "Prescribed antipsychotic medication and weight: a randomized trial",
                 abstract="Adults were randomized to the drug or placebo; 600 took part. Weight rose significantly (p < 0.001).")
fg.models.script = [Resp(VJ("true", [1], tldr="Prescribed antipsychotics cause weight gain.",
                            evidence=["Weight rose on the drug (Study 1)."],
                            relevance=[_f(1, "for")]))]
_v = verdict.weigh_evidence("Prescribed antipsychotic medication causes weight gain", [dict(_AP_RCT)],
                            "(antipsychotic medication) AND (weight gain)")
t("  a documented harm a trial backs stands as it is",
  _v["verdict"] == "true" and _v["tldr"] == "Prescribed antipsychotics cause weight gain."
  and "none here says so" not in _v["explanation"], _v)
_DS = _study("D1", "Energy drinks and teenagers: a survey", types=["Journal Article"],
             abstract="A survey of 3,000 teenagers asked about energy drinks and symptoms.")
fg.models.script = [Resp(VJ("true", [1], tldr="Energy drinks are fine for teenagers.",
                            evidence=["Few teenagers reported symptoms (Study 1)."],
                            relevance=[_f(1, "for")]))] * 3
_v = verdict.weigh_evidence("Energy drinks are safe for teenagers", [dict(_DS)], "(energy drinks) AND (teenagers)")
t("  'it is safe' with nothing but a survey behind it is not rated likely true",
  _v["verdict"] == "complicated" and _v["tldr"] == verdict._UNSAFE_UNPROVEN
  and verdict._UNSAFE_UNPROVEN in _v["explanation"], _v)
_SIG = _study("S1", "Flu vaccine and hospital admissions: a randomized trial",
              abstract="Adults were randomized to vaccine or placebo; 2,000 took part. "
                       "Admissions fell by 12% and were significantly reduced (p < 0.01).")
fg.models.script = [Resp(VJ("complicated", [1], tldr="Flu vaccines may cut admissions a little.",
                            effect_size="Admissions fell by 12%, not statistically significant (Study 1).",
                            evidence=["Admissions fell by 12% (Study 1)."],
                            relevance=[_f(1, "for")]))] * 3
_v = verdict.weigh_evidence("Flu vaccines reduce hospital admissions", [dict(_SIG)], _FLU_Q)
_line = evidence.evidence_line(_v["verdict"], [_SIG], _v["cited_studies"], "Flu vaccines reduce hospital admissions",
                               ((_v["breakdown"] or {}).get("effect") or {}).get("label"))
t("  'no clear effect' is never shown when a cited trial found a significant benefit",
  "no clear effect" not in _line and not ((_v["breakdown"] or {}).get("effect") or {}).get("null"), (_line, _v))

# ---- the faithfulness check: every line read against the abstract it cites --------
_TC_CLAIM = "Tai chi lowers blood pressure"
_TC_Q = "(tai chi) AND (blood pressure)"
_TC = [_study("TC1", "Tai chi, yoga and blood pressure: a randomized trial",
              abstract="Adults were randomized to tai chi, yoga or usual care; 300 took part. Blood pressure "
                       "fell by 8 mmHg in the yoga group and by 3 mmHg in the tai chi group. Tai chi lowered "
                       "blood pressure modestly."),
       _study("TC2", "Tai chi and blood pressure in adults: a randomized trial",
              abstract="Adults were randomized to tai chi or usual care; 200 took part. Tai chi did not change "
                       "resting heart rate, but it lowered blood pressure by 4 mmHg.")]
_TC_V = Resp(VJ("true", [1, 2], tldr="Tai chi lowers blood pressure a lot.",
                explanation="Tai chi lowered blood pressure by 8 mmHg (Study 1). It also improved sleep (Study 2).",
                evidence=["Tai chi lowered blood pressure by 8 mmHg (Study 1)."]))
_GROUP_FIX = "Tai chi lowered blood pressure by 3 mmHg; the 8 mmHg drop was in the yoga group (Study 1)."
_EIGHT_TC = "Tai chi lowered blood pressure by 4 mmHg (Study 2)."

def FJ(lines, missing=""):
    return Resp(json.dumps({"lines": lines, "missing": missing}))

def _L(n, verdict="ok", problem="none", rewrite=""):
    return {"line": n, "verdict": verdict, "problem": problem, "rewrite": rewrite}

fg.models.calls = 0
fg.models.script = [_TC_V, FJ([_L(0, "rewrite", "direction", "Tai chi lowers blood pressure a little."),
                               _L(1, "rewrite", "group", _GROUP_FIX),
                               _L(2, "drop", "unsupported")],
                              missing="Tai chi lowered blood pressure by 4 mmHg (Study 2).")]
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
_says = (_v["breakdown"] or {}).get("says") or []
t("faithfulness: a figure from another arm is rewritten to the group the line names",
  _says[:1] == [_GROUP_FIX] and "8 mmHg (Study 1)." not in " ".join(_says), _says)
t("  a line its abstract cannot support is dropped, here and in the explanation",
  not any("sleep" in x for x in _says) and "sleep" not in _v["explanation"]
  and _GROUP_FIX in _v["explanation"], (_says, _v["explanation"]))
t("  a study's own headline finding that no line gave is added",
  "Tai chi lowered blood pressure by 4 mmHg (Study 2)." in _says, _says)
t("  the short answer is held to the abstracts too",
  _v["tldr"] == "Tai chi lowers blood pressure a little.", _v["tldr"])
t("  and all of it costs one call after the verdict, at temperature 0",
  fg.models.calls == 2 and "You are checking a summary of health research" in fg.models.prompts[-1]
  and fg.models.configs[-1].get("temperature") == 0, fg.models.calls)
t("  the check is shown each line, numbered, with only the studies used",
  'L1: "Tai chi lowered blood pressure by 8 mmHg (Study 1)."' in fg.models.prompts[-1]
  and "[Study 2]" in fg.models.prompts[-1] and 'L0 (the short answer): "Tai chi lowers blood pressure a lot."'
  in fg.models.prompts[-1])
fg.models.script = [_TC_V, FJ([_L(0, "rewrite", "direction", "Tai chi does not lower blood pressure."),
                               _L(1, "rewrite", "group", "Tai chi lowered blood pressure by 12 mmHg (Study 1)."),
                               _L(2, "rewrite", "unsupported", "It also helped sleep (Study 3).")])]
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
_says = (_v["breakdown"] or {}).get("says") or []
t("  a rewrite goes through the same gates: a figure no abstract printed, or a study not used, is dropped",
  not any("12 mmHg" in x or "Study 3" in x or "8 mmHg" in x or "sleep" in x for x in _says), _says)
t("  and a rewritten short answer that argues with the stamp is refused",
  _v["tldr"] == "Tai chi lowers blood pressure a lot.", _v["tldr"])
fg.models.calls = 0
fg.models.script = [_TC_V]
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
t("  when the check cannot run, the answer stands as it was",
  _v["verdict"] == "true" and (_v["breakdown"] or {}).get("says")
  == ["Tai chi lowered blood pressure by 8 mmHg (Study 1).", "It also improved sleep (Study 2)."]
  and fg.models.calls == 2, _v)
fg.models.calls = 0
fg.models.script = [Resp(VJ("complicated", [1], tldr="Tai chi may help a little.",
                            explanation="The evidence is thin."))]
verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
t("  an answer with no research lines costs no check",
  fg.models.calls == 1, fg.models.calls)
fg.models.script = [Resp(VJ("true", [1, 2], tldr="Tai chi slightly lowers blood pressure.",
                            explanation=_EIGHT_TC, evidence=[_EIGHT_TC])),
                    FJ([_L(0, "rewrite", "direction", "Tai chi lowers blood pressure.")])]
_faith = _Catch()
logging.getLogger("evident.faithful").addHandler(_faith)
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
logging.getLogger("evident.faithful").removeHandler(_faith)
t("  a rewritten short answer that only takes the hedge out is refused, and the refusal is logged",
  _v["tldr"] == "Tai chi slightly lowers blood pressure."
  and any("was refused" in m for m in _faith.lines), (_v["tldr"], _faith.lines))
t("  but one that turns a hedged answer round, or adds a hedge, is kept",
  breakdown.lost_hedge("Tai chi may lower blood pressure.", "Tai chi does not lower blood pressure.",
                       _TC_CLAIM) is False
  and breakdown.lost_hedge("Tai chi lowers blood pressure a lot.", "Tai chi lowers blood pressure a little.",
                           _TC_CLAIM) is False
  and breakdown.lost_hedge("Tai chi slightly lowers blood pressure.", "Tai chi lowers blood pressure.",
                           _TC_CLAIM) is True)
t("  'small', 'some' and 'low certainty' are hedges too, and a figure fixed is not a hedge lost",
  all(breakdown.lost_hedge(x, y, _TC_CLAIM) for x, y in [
      ("Tai chi lowered blood pressure a small amount (Study 1).", "Tai chi lowered blood pressure (Study 1)."),
      ("Some trials found tai chi lowered blood pressure (Study 1).",
       "Trials found tai chi lowered blood pressure (Study 1)."),
      ("Low-certainty evidence suggests tai chi lowers blood pressure (Study 1).",
       "Tai chi lowers blood pressure (Study 1).")])
  and not breakdown.lost_hedge("Tai chi lowered blood pressure by 8 mmHg (Study 1).",
                               "Tai chi lowered blood pressure by 3 mmHg (Study 1).", _TC_CLAIM))
fg.models.script = [Resp(VJ("false", [1, 2], tldr="Tai chi does not lower blood pressure.",
                            explanation=_EIGHT_TC, evidence=[_EIGHT_TC])),
                    FJ([_L(0, "rewrite", "direction", "Tai chi studies show mixed results because some "
                                                      "trials find no change while others find a drop.")])]
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
t("  a rewritten short answer that turns a plain no into a tie is refused",
  _v["tldr"] == "Tai chi does not lower blood pressure.", _v["tldr"])
t("  but hedging a plain answer keeps it, and a 'not' inside a hedge stays a no",
  breakdown.kept_answer("Tai chi lowers blood pressure a lot.", "Tai chi lowers blood pressure a little.",
                        _TC_CLAIM)
  and breakdown.kept_answer("Tai chi does not lower blood pressure.",
                            "Tai chi does not clearly lower blood pressure.", _TC_CLAIM)
  and breakdown.lost_hedge("Tai chi does not clearly lower blood pressure.",
                           "Tai chi does not lower blood pressure.", _TC_CLAIM))
_SOME = "Some trials found tai chi lowered blood pressure (Study 2)."
fg.models.script = [Resp(VJ("true", [1, 2], tldr="Tai chi slightly lowers blood pressure.",
                            explanation=_SOME, evidence=[_SOME])),
                    FJ([_L(1, "rewrite", "direction", "Trials found tai chi lowered blood pressure (Study 2).")])]
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
t("  a research line rewritten without its hedge is dropped, never printed surer",
  not any("rials found tai chi" in x for x in (_v["breakdown"] or {}).get("says") or []), _v["breakdown"])
t("  a takeaway code wrote is not sent to be checked",
  verdict._code_takeaway(verdict.RANKED_TLDR["true"]) and verdict._code_takeaway(verdict.UNTESTED_TLDR)
  and not verdict._code_takeaway("Tai chi lowers blood pressure a little."))

# A dropped line goes from every place it sits, the effect row and the
# part-by-part answers included.
_EIGHT = "Tai chi lowered blood pressure by 8 mmHg (Study 1)."
fg.models.script = [Resp(VJ("true", [1, 2], tldr="Tai chi lowers blood pressure a little.",
                            explanation=_EIGHT, effect_size=_EIGHT,
                            parts=[{"part": "Tai chi lowers blood pressure", "assessment": _EIGHT}],
                            evidence=[_EIGHT, "Tai chi lowered blood pressure by 4 mmHg (Study 2)."])),
                    FJ([_L(1, "drop", "group")])]
_v = verdict.weigh_evidence(_TC_CLAIM, [dict(x) for x in _TC], _TC_Q)
_bd = _v["breakdown"] or {}
t("faithfulness: a dropped line leaves the effect row and the part-by-part answers too",
  "8 mmHg" not in json.dumps(_bd) and _bd.get("effect_size") == ""
  and _bd["parts"][0]["assessment"] == breakdown.PART_BELOW, _bd)

# A line that credits a study with something it never tested. The survey
# below mentions gum disease only as the reason people are told to floss; it
# counted how often they do. Synthetic claim and studies, not a holdout claim.
_FL_CLAIM = "Flossing prevents gum disease"
_FL_Q = "(flossing OR dental floss) AND (gum disease OR gingivitis OR periodontitis)"
_FL = [_study("FL1", "How often do adults floss? A survey of daily habits", types=["Journal Article"],
              abstract="Gum disease is common, and dentists tell people to floss every day. We surveyed "
                       "adults about how often they floss. Only 31% said they floss every day, and "
                       "younger adults flossed less often than older adults."),
       _study("FL2", "Flossing and gingivitis: a meta-analysis", types=["Meta-Analysis"],
              abstract="We pooled 14 randomized trials in 3200 adults who flossed every day or brushed "
                       "only for 8 weeks. Gingivitis scores fell by 20% in the flossing group and by 5% "
                       "in the brush only group.")]
_FL_BAD = "Daily flossing lowered gum disease (Study 1)."
_FL_GOOD = "Gingivitis scores fell by 20% with flossing (Study 2)."
_FL_V = Resp(VJ("true", [1, 2], tldr="Flossing seems to help gums a little.",
                explanation=f"{_FL_BAD} {_FL_GOOD}", evidence=[f"{_FL_BAD} {_FL_GOOD}"]))
_FL_FIX = "That survey asked how often adults floss (Study 1)."
fg.models.script = [_FL_V, FJ([_L(1, "rewrite", "untested", _FL_FIX)])]
_faith = _Catch()
logging.getLogger("evident.faithful").addHandler(_faith)
_v = verdict.weigh_evidence(_FL_CLAIM, [dict(x) for x in _FL], _FL_Q)
logging.getLogger("evident.faithful").removeHandler(_faith)
_says = (_v["breakdown"] or {}).get("says") or []
t("faithfulness: a line that credits a study with what it never tested is rewritten to what it did",
  _FL_FIX in _says and _FL_BAD not in _says and _FL_GOOD in _says
  and _FL_FIX in _v["explanation"] and _FL_BAD not in _v["explanation"], (_says, _v["explanation"]))
t("  and the problem is named in the log",
  any("(untested), so it was rewritten" in m for m in _faith.lines), _faith.lines)
fg.models.script = [_FL_V, FJ([_L(1, "drop", "untested")])]
_v = verdict.weigh_evidence(_FL_CLAIM, [dict(x) for x in _FL], _FL_Q)
_says = (_v["breakdown"] or {}).get("says") or []
t("  or dropped when no version of it is true, and the page still reads from what is left",
  _says == [_FL_GOOD] and _v["explanation"] == _FL_GOOD, (_says, _v["explanation"]))
fg.models.script = [_FL_V, FJ([_L(1, "drop", "untested"), _L(2, "drop", "untested")])]
_v = verdict.weigh_evidence(_FL_CLAIM, [dict(x) for x in _FL], _FL_Q)
t("  and with every line gone, the explanation falls back to the short answer",
  not (_v["breakdown"] or {}).get("says") and _v["explanation"] == _v["tldr"] != "", _v)
_fp = re.sub(r"\s+", " ", verdict._faithful_prompt(_FL_CLAIM, "", [_FL_BAD], [1], _FL))
t("  the check is told the rule, and its older rules stand",
  "untested: it credits a study (for L0, any of them) with a finding about an outcome, treatment, "
  "exposure or comparison" in _fp and "the abstract never says the study tested or measured" in _fp
  and "the background, the aims or the wider framing is not testing it" in _fp
  and all(k in _fp for k in ("direction:", "group:", "unsupported:"))
  and "for an outcome that abstract never says the study measured" in _fp, _fp)
_enum = verdict.FAITHFUL_SCHEMA["properties"]["lines"]["items"]["properties"]["problem"]["enum"]
t("  and the reply is allowed to name it",
  _enum == ["none", "direction", "group", "unsupported", "untested"], _enum)

# ---- writing: numbers, statistics, verbs, and the effect line ----------------
# Synthetic claims and studies only; none of these is a holdout claim.
_dg = breakdown.digits
t("writing: a number spelled out is written in digits",
  _dg("A pooled review of thirty-six trials found less pain.") == "A pooled review of 36 trials found less pain."
  and _dg("They enrolled four thousand ninety-one adults.") == "They enrolled 4091 adults."
  and _dg("Two hundred and twelve people took part.") == "212 people took part.")
t("  a decimal and a sign said in words become the figure",
  _dg("LDL fell by negative zero point thirty-one mmol per liter.") == "LDL fell by -0.31 mmol per liter."
  and _dg("The difference was minus 0.54 kg (95% CI minus 1.05 to minus 0.03).")
  == "The difference was -0.54 kg (95% CI -1.05 to -0.03).")
t("  but 'one' and 'zero' as words, and fractions, stay words",
  _dg("One of the trials found no effect, and no one dropped out.")
  == "One of the trials found no effect, and no one dropped out."
  and _dg("Two-thirds improved, with zero side effects at one point.")
  == "Two-thirds improved, with zero side effects at one point."
  and _dg("About one in four people, over one or two weeks.") == "About 1 in 4 people, over 1 or 2 weeks.")
t("  and a figure then 'in one review' is a figure and a review, not a ratio",
  _dg("The pooled effect size was 1.22 in one review (Study 1).")
  == "The pooled effect size was 1.22 in one review (Study 1)."
  and _dg("Pain fell by 5 in one trial and by 12 in two others.")
  == "Pain fell by 5 in one trial and by 12 in 2 others."
  and _dg("The score was 0.5 and one outlier was dropped.") == "The score was 0.5 and one outlier was dropped."
  and verdict.tidy_prose("The pooled effect size was 1.22 in one review (Study 1).", 300)
  == "The pooled effect size was 1.22 in one review (Study 1).")
t("  while a ratio with a whole number first, said in words or digits, still reads as numbers",
  _dg("The odds were ten to one, and about one in ten.") == "The odds were 10 to 1, and about 1 in 10."
  and _dg("The odds were 3 to one.") == "The odds were 3 to 1.")
_th = breakdown.thousands
t("writing: a long number gets its commas",
  _th("covering 2481190 adults and 10000 steps") == "covering 2,481,190 adults and 10,000 steps"
  and _th("over 1500 people") == "over 1,500 people")
t("  a year, a PMID, a decimal and a trial registration number do not",
  _th("a 2019 review, from 1990 to 2020") == "a 2019 review, from 1990 to 2020"
  and _th("PMID 31234567, p < 0.00001, NCT01234567") == "PMID 31234567, p < 0.00001, NCT01234567"
  and _th("in 2018, 2000 people took part") == "in 2018, 2,000 people took part")
_uj = breakdown.unjargon
t("writing: an area under the glucose curve is the total rise in blood sugar",
  _uj("Glucose netiAUC was 7.9 mmol/L/h (Study 2).") == "The total rise in blood sugar was 7.9 mmol/L/h (Study 2)."
  and _uj("Walks lower the glucose incremental area under the curve (Study 1).")
  == "Walks lower the total rise in blood sugar (Study 1)."
  and _uj("Walks led to a lower iAUC for glucose (Study 1).")
  == "Walks led to a lower total rise in blood sugar (Study 1).")
t("  a weighted mean difference is the average difference, and a 95% CI the likely range",
  _uj("The weighted mean difference was -0.54 kg (95% CI -1.05 to -0.03) (Study 7).")
  == "The average difference was -0.54 kg (likely range -1.05 to -0.03) (Study 7)."
  and _uj("WMD -1.2 kg (Study 3).") == "Average difference -1.2 kg (Study 3).")
t("  and so is a confidence interval written out",
  _uj("The odds ratio was 1.22 with a 95% confidence interval of 1.04 to 1.42 (Study 1).")
  == "The odds ratio was 1.22 with a likely range of 1.04 to 1.42 (Study 1)."
  and _uj("OR 1.3 (confidence interval (CI) 1.1 to 1.5)") == "OR 1.3 (likely range 1.1 to 1.5)")
t("  in percent, and with the gloss it was given, which the plain name makes redundant",
  _uj("Pain fell, 95 percent confidence interval (the range the real answer is probably in) 0.13 to 0.44.")
  == "Pain fell, likely range 0.13 to 0.44.")
t("  Hedges' g, Cohen's d and a bare g are effect sizes, capitalised only where a sentence opens",
  _uj("Recall improved with a Hedges' g of 0.29 (Study 3).") == "Recall improved with an effect size of 0.29 (Study 3)."
  and _uj("Lapses showed a g of -0.78 (Study 1).") == "Lapses showed an effect size of -0.78 (Study 1)."
  and _uj("Cohen's d was 0.4 (Study 1).") == "Effect size was 0.4 (Study 1)."
  and _uj("Weight fell with an SMD of -0.39 (Study 2).") == "Weight fell with an effect size of -0.39 (Study 2)."
  and _uj("They ate 5 g of salt and 1,337 g of water.") == "They ate 5 g of salt and 1,337 g of water.")
t("  efficacy reads as a clause alone, a noun after a verb, and stays inside a compound",
  breakdown.plain("Its efficacy was unclear (Study 1).") == "How well it works was unclear (Study 1)."
  and breakdown.plain("It lacked efficacy (Study 2).") == "It lacked an effect (Study 2)."
  and breakdown.plain("There was no efficacy in adults.") == "There was no effect in adults."
  and breakdown.plain("The trials assessed vaccine effectiveness against measles (Study 1).")
  == "The trials tested vaccine effectiveness against measles (Study 1).")
t("  before 'of' it is the noun 'effect', never 'how well it works of'",
  breakdown.plain("The long term safety and efficacy of vinegar is unknown (Study 1).")
  == "The long term safety and effect of vinegar is unknown (Study 1)."
  and breakdown.plain("The effectiveness of the program was low (Study 2).")
  == "The effect of the program was low (Study 2).")
t("  a meta analysis without its hyphen and sleep onset latency are said in words",
  breakdown.plain("A meta analysis of 12 trials found less pain (Study 1).")
  == "A pooled review (meta-analysis) of 12 trials found less pain (Study 1)."
  and breakdown.plain("Melatonin shortened sleep onset latency by 7 minutes (Study 2).")
  == "Melatonin shortened time to fall asleep (sleep latency) by 7 minutes (Study 2).")
t("  a fall is not given a minus sign as well, nor is its range or what it is compared to",
  breakdown.house_style("A review found a drop in systolic blood pressure of -5.31 mm Hg (Study 5).")
  == "A review found a drop in systolic blood pressure of 5.31 mm Hg (Study 5)."
  and breakdown.house_style("Triglycerides had a drop of -1.51 mmol per liter compared to -0.66 (Study 8).")
  == "Triglycerides had a drop of 1.51 mmol per liter compared to 0.66 (Study 8)."
  and breakdown.house_style("LDL fell by -0.33 mmol/L (95% CI -0.38 to -0.27) (Study 1).")
  == "LDL fell by 0.33 mmol/L (likely range 0.27 to 0.38) (Study 1).")
t("  but a range that crosses zero keeps every sign, the fall's too",
  breakdown.house_style("Pain fell by -1.2 points (95% CI -2.0 to 0.4) (Study 2).")
  == "Pain fell by -1.2 points (likely range -2.0 to 0.4) (Study 2).")
t("  a fall written without its sign loses the signs on its range too, unless it crosses zero",
  breakdown.house_style("Fasting blood sugar dropped by about 22 mg/dL (likely range -29 to -15) (Study 2).")
  == "Fasting blood sugar dropped by about 22 mg/dL (likely range 15 to 29) (Study 2)."
  and breakdown.house_style("Pain fell by 1.2 points (likely range -2.0 to 0.4) (Study 2).")
  == "Pain fell by 1.2 points (likely range -2.0 to 0.4) (Study 2)."
  and breakdown.house_style("Weight fell by 2 kg (Study 3).") == "Weight fell by 2 kg (Study 3).")
# Holdout #3 page 17: the first fall lost its minus and its range's ("likely
# range 4.17; 0.88 mmHg", the wrong way round and without a word to join
# them), while the second, in the same sentence, kept both.
t("  every fall in a sentence is unsigned, the one without a verb of its own too, ranges smaller end first",
  breakdown.house_style("Systolic blood pressure decreased by -2.52 mmHg (95% CI -4.17; -0.88 mmHg) and "
                        "diastolic blood pressure by -1.58 mmHg (95% CI -2.54; -0.62 mmHg) (Study 1).")
  == "Systolic blood pressure decreased by 2.52 mmHg (likely range 0.88 to 4.17 mmHg) and "
     "diastolic blood pressure by 1.58 mmHg (likely range 0.62 to 2.54 mmHg) (Study 1)."
  and breakdown.house_style("LDL fell by -0.3 mmol/L and triglycerides fell by -0.5 mmol/L, and HDL "
                            "was unchanged (Study 2).")
  == "LDL fell by 0.3 mmol/L and triglycerides fell by 0.5 mmol/L, and HDL was unchanged (Study 2).")
t("  a verb with its thing before 'by' is a fall too, and so is 'lower by'",
  breakdown.house_style("Cocoa reduced systolic blood pressure by -2.52 mmHg (95% CI -4.17 to -0.88) (Study 1).")
  == "Cocoa reduced systolic blood pressure by 2.52 mmHg (likely range 0.88 to 4.17) (Study 1)."
  and breakdown.house_style("Weight was lower by -2.1 kg (95% CI -3.0 to -1.2) (Study 4).")
  == "Weight was lower by 2.1 kg (likely range 1.2 to 3.0) (Study 4)."
  and breakdown.house_style("Statins lowers LDL by -0.8 mmol/L (Study 3).")
  == "Statins lower LDL by 0.8 mmol/L (Study 3).")
t("  a fall by a named difference is not a double negative: 'reduced by an average difference of -22'",
  breakdown.house_style("Fasting blood sugar was reduced by a weighted mean difference of -21.9 mg/dL (Study 1).")
  == "Fasting blood sugar was reduced by an average difference of 21.9 mg/dL (Study 1).")
t("  a figure that is not a fall keeps its minus, and so does a rise after a fall",
  breakdown.house_style("Blood pressure changed by -2.52 mmHg (95% CI -4.17; -0.88 mmHg) (Study 1).")
  == "Blood pressure changed by -2.52 mmHg (likely range -4.17 to -0.88 mmHg) (Study 1)."
  and breakdown.house_style("LDL fell by -0.3 mmol/L and HDL rose by -0.1 mmol/L (Study 2).")
  == "LDL fell by 0.3 mmol/L and HDL rose by -0.1 mmol/L (Study 2).")
t("  a clause with its own verb after a fall is left alone, a rise is not made a fall",
  breakdown.house_style("Pain fell by -1.2 points and sleep improved by -0.5 hours (Study 2).")
  == "Pain fell by 1.2 points and sleep improved by -0.5 hours (Study 2).")
t("  a fall over a bare range loses the minus on both ends, smaller first, with or without 'by'",
  breakdown.house_style("Systolic pressure fell by -3 to -10 mmHg.") == "Systolic pressure fell by 3 to 10 mmHg."
  and breakdown.house_style("LDL dropped -3 to -10 mg/dL across trials.")
  == "LDL dropped 3 to 10 mg/dL across trials."
  and breakdown.house_style("LDL dropped -3.5 mg/dL across trials.") == "LDL dropped 3.5 mg/dL across trials."
  and breakdown.house_style("Pain fell by -3 to -10 points and sleep fell by -2 to -4 hours.")
  == "Pain fell by 3 to 10 points and sleep fell by 2 to 4 hours.")
t("  the same with the minus sign (U+2212) the model sometimes writes",
  breakdown.house_style("Systolic pressure fell by −3 to −10 mmHg.") == "Systolic pressure fell by 3 to 10 mmHg."
  and breakdown.house_style("LDL dropped −3 to −10 mg/dL across trials.")
  == "LDL dropped 3 to 10 mg/dL across trials.")
t("  a bare range that crosses zero, a rise, a change and a level keep every sign",
  breakdown.house_style("Pain fell by -3 to 2 points.") == "Pain fell by -3 to 2 points."
  and breakdown.house_style("Pain fell -3 to 2 points.") == "Pain fell -3 to 2 points."
  and breakdown.house_style("Pain rose by -3 to -10 points.") == "Pain rose by -3 to -10 points."
  and breakdown.house_style("Pain rose -3 to -10 points.") == "Pain rose -3 to -10 points."
  and breakdown.house_style("Blood pressure changed by -3 to -10 mmHg.")
  == "Blood pressure changed by -3 to -10 mmHg."
  and breakdown.house_style("Scores were lower than -3 on the scale.") == "Scores were lower than -3 on the scale.")
t("  a bare range and the bracket after it are both unsigned, unless the bracket crosses zero",
  breakdown.house_style("LDL fell by -3 to -10 mg/dL (95% CI -12 to -1) (Study 1).")
  == "LDL fell by 3 to 10 mg/dL (likely range 1 to 12) (Study 1)."
  and breakdown.house_style("LDL fell by -3 to -10 mg/dL (95% CI -12 to 1) (Study 1).")
  == "LDL fell by -3 to -10 mg/dL (likely range -12 to 1) (Study 1).")
# Holdout #3 pages 13, 17 and 18: "95% CI=0.79, 0.88", "95% CI: 0.52, 0.91" and
# "95% CI, 0.04-0.53" read as a sum or a list, not as the ends of a range.
t("  the ends of a likely range are joined by 'to' whatever the model wrote between them",
  breakdown.house_style("The odds were 17% lower (95% CI=0.79, 0.88) (Study 2).")
  == "The odds were 17% lower (likely range 0.79 to 0.88) (Study 2)."
  and breakdown.house_style("Smoking fell (95% CI: 0.52, 0.91) (Study 8).")
  == "Smoking fell (likely range 0.52 to 0.91) (Study 8)."
  and breakdown.house_style("The effect was small (95% CI, 0.04-0.53) (Study 1).")
  == "The effect was small (likely range 0.04 to 0.53) (Study 1).")
t("  a range already said with 'to', a thousand with its comma, and a range with no figures are not touched",
  breakdown.house_style("Risk was no different (95% CI 0.92 to 1.06) (Study 1).")
  == "Risk was no different (likely range 0.92 to 1.06) (Study 1)."
  and breakdown.house_style("People walked more (likely range 1,234 to 1,300 steps) (Study 2).")
  == "People walked more (likely range 1,234 to 1,300 steps) (Study 2)."
  and breakdown.house_style("The likely range was wide (Study 3).") == "The likely range was wide (Study 3).")
t("  and a word the plain name doubled is said once",
  breakdown.house_style("A large pooled pooled review (meta-analysis) found less pain (Study 1).")
  == "A large pooled review (meta-analysis) found less pain (Study 1).")
t("  a standardized mean difference is an effect size, and after meals is said in words",
  _uj("Weight fell with a standardized mean difference of -0.39 (Study 2).")
  == "Weight fell with an effect size of -0.39 (Study 2)."
  and _uj("Postprandial blood glucose fell (Study 1).") == "Blood sugar after meals fell (Study 1).")
_ag = breakdown.agree
t("writing: a plural subject takes a plural verb",
  _ag("Vaccines successfully lowers the risk.") == "Vaccines successfully lower the risk."
  and _ag("These findings suggests a benefit.") == "These findings suggest a benefit."
  and _ag("The review found that lentils lowers LDL.") == "The review found that lentils lower LDL."
  and _ag("Studies 1 and 3 shows a drop.") == "Studies 1 and 3 show a drop."
  and _ag("The studies was small.") == "The studies were small.")
t("  and a singular one a singular verb",
  _ag("The evidence suggest a small effect.") == "The evidence suggests a small effect."
  and _ag("Study 2 show a drop.") == "Study 2 shows a drop."
  and _ag("This review have limits.") == "This review has limits.")
t("  a sentence that was already right is left alone",
  all(_ag(s) == s for s in (
      "Eating lentils lowers LDL.", "Analysis of trials shows a benefit.", "This shows a benefit.",
      "Measles causes a rash.", "Diabetes affects millions.", "8 glasses is just a rough guide.",
      "Study 1 and Study 2 show a drop.", "The study report shows a benefit.",
      "Does the evidence show a benefit?", "Trial results show a drop.")))

# The gate now sees a spelled-out number, so it has to be in an abstract.
_LENT = [_study("LE1", "Lentils and LDL cholesterol: a meta-analysis", types=["Meta-Analysis"],
                abstract="We pooled 36 randomized trials in 2481190 adults. Lentils lowered LDL "
                         "cholesterol (weighted mean difference -0.19 mmol/L, 95% CI -0.23 to -0.15)."),
         _study("LE2", "Lentils and blood sugar after meals: a randomized trial",
                abstract="120 adults were randomized to lentils or rice. Lentils lowered the glucose "
                         "iAUC by 18% compared with rice.")]
_raw = {"parts": [], "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": "",
        "effect_size": "",
        "evidence": ["A trial of fifty-five adults found lower LDL cholesterol (Study 2).",
                     "A trial of one hundred and twenty adults found lower blood sugar (Study 2).",
                     "A pooled review of thirty-six trials found lower LDL cholesterol (Study 1)."]}
_g = breakdown.ground(_raw, _LENT, verdict.tidy_prose)
t("writing: a number spelled in words meets the gate like any other",
  not any("55" in p or "fifty" in p for p in _g["evidence"])
  and any("120 adults" in p for p in _g["evidence"]) and any("36 trials" in p for p in _g["evidence"]),
  _g["evidence"])

# The effect line points above only at the size of an effect.
_counts = dict(_raw, evidence=["A pooled review of 36 trials in 2481190 adults found lower LDL cholesterol (Study 1)."])
t("effect line: counts of people or trials are not figures to point at",
  breakdown.ground(dict(_counts, effect_size="LDL fell by 0.31 mmol/L (Study 1)."),
                   _LENT, verdict.tidy_prose)["effect_size"] == "")
t("  and with no figure given at all, the abstracts are still said to carry none",
  breakdown.ground(dict(_counts, effect_size="The effect was modest (Study 1)."),
                   _LENT, verdict.tidy_prose)["effect_size"] == breakdown.NO_EFFECT_SIZE)
t("  while a size above is still pointed at",
  breakdown.ground(dict(_counts, effect_size="LDL fell by 0.31 mmol/L (Study 1).",
                        evidence=["Lentils lowered blood sugar by 18% (Study 2)."]),
                   _LENT, verdict.tidy_prose)["effect_size"] == breakdown.EFFECT_SIZE_ABOVE)
t("  a pointer left with nothing above it, after a later step, is taken out",
  breakdown.settle_effect({"effect_size": breakdown.EFFECT_SIZE_ABOVE, "parts": [],
                           "evidence": ["A review of 36 trials found less LDL (Study 1)."]})["effect_size"] == ""
  and breakdown.settle_effect({"effect_size": breakdown.EFFECT_SIZE_ABOVE, "parts": [],
                               "evidence": ["LDL fell by 0.19 mmol/L (Study 1)."]})["effect_size"]
  == breakdown.EFFECT_SIZE_ABOVE)
t("  and the page prints no effect row when there is no line",
  "{%- if bd.effect_size %}" in (ROOT / "templates" / "index.html").read_text()
  and "(bd.effect_size\n        ? `<p class=\"effect\">" in (ROOT / "static" / "app.js").read_text())

# All of it, through a real check.
_LENT_CLAIM = "Lentils lower LDL cholesterol"
fg.models.calls = 0
fg.models.script = [Resp(VJ(
    "true", [1, 2], tldr="Lentils successfully lowers LDL cholesterol a little.",
    explanation="A pooled review of thirty-six trials in 2481190 adults found the weighted mean "
                "difference was minus 0.19 mmol/L (95% CI minus 0.23 to minus 0.15) (Study 1). "
                "Lentils cut the glucose iAUC by 18% (Study 2).",
    evidence=["A pooled review of thirty-six trials in 2481190 adults found lower LDL cholesterol (Study 1)."],
    effect_size="The weighted mean difference was minus 0.19 mmol/L (Study 1)."))]
_v = verdict.weigh_evidence(_LENT_CLAIM, [dict(x) for x in _LENT], "(lentils) AND (ldl cholesterol)")
_all = " ".join([_v["tldr"], _v["explanation"]] + list((_v["breakdown"] or {}).get("says") or [])
                + list((_v["breakdown"] or {}).get("evidence") or [])
                + [(_v["breakdown"] or {}).get("effect_size") or ""])
t("writing: a real check prints digits, commas and plain names for its statistics",
  "36 trials" in _all and "2,481,190 adults" in _all and "average difference" in _all
  and "likely range" in _all and "total rise in blood sugar" in _all
  and not re.search(r"thirty|minus|weighted mean|iAUC|95% CI|2481190", _all), _all)
t("  and its verbs agree with their subjects",
  _v["tldr"].startswith("Lentils successfully lower "), _v["tldr"])

# ---- a result for an outcome nobody measured ------------------------------------
_STONE_Q = "(coffee OR caffeine) AND (kidney stones OR nephrolithiasis OR renal calculi)"
_STONE = [_study("K1", "Coffee intake and kidney stones: a randomized trial",
                 abstract="We randomized 200 adults to coffee or water. Kidney stones formed in 4 and 5 people."),
          _study("K2", "Caffeine pharmacokinetics after three weeks of coffee",
                 abstract="Plasma caffeine and paraxanthine levels were measured in 20 men. Levels rose by 40%."),
          _study("K3", "Coffee and urine", abstract="")]
_stem = evidence.outcome_stems(pubmed.split_and(_STONE_Q)[1])
t("unmeasured outcome: the outcome group is read as its naming words, not 'risk' or 'levels'",
  _stem == ["kidney", "stone", "nephrolithiasis", "renal", "calculi"], _stem)
t("  and a study that names it in other words ('cardiovascular' for heart) did mention it",
  not evidence.never_mentions(_study("H1", "Wine and cardiovascular events", abstract="Fewer cardiovascular "
                                     "events."), evidence.outcome_stems("(heart)"))
  and evidence.never_mentions(_STONE[1], _stem))
_bad = "A second trial found caffeine levels rose, but nobody developed kidney stones (Study 2)."
_out = pubmed.split_and(_STONE_Q)[1]
t("  a sentence reporting that outcome for a study that never mentions it is caught",
  verdict.unmeasured(_bad, _STONE, _out))
t("  but not one that says the study didn't measure it",
  not any(verdict.unmeasured(x, _STONE, _out) for x in (
      "A second trial measured caffeine levels without tracking kidney stones (Study 2).",
      "That study did not measure kidney stones (Study 2).",
      "Neither tested whether kidney stones formed (Study 2).",
      "It remains unknown if higher caffeine levels lead to kidney stones (Study 2).")))
t("  nor one citing a study that did measure it, nor one citing a study with no abstract to read",
  not verdict.unmeasured("The trial found no difference in kidney stones (Study 1).", _STONE, _out)
  and not verdict.unmeasured("Coffee drinkers had fewer kidney stones (Study 3).", _STONE, _out)
  and not verdict.unmeasured("Both trials found no kidney stones (Study 1, Study 2).", _STONE, _out))
fg.models.script = [Resp(VJ(
    "false", [1, 2], tldr="Coffee doesn't seem to cause kidney stones.",
    explanation="A trial of 200 adults found kidney stones in 4 and 5 people (Study 1). " + _bad,
    evidence=["A trial of 200 adults found kidney stones in 4 and 5 people (Study 1). " + _bad,
              "Caffeine levels rose by 40% in 20 men (Study 2). That study did not measure kidney "
              "stones (Study 2)."]))]
_kv = verdict.weigh_evidence("Coffee causes kidney stones", [dict(s) for s in _STONE], _STONE_Q)
_kall = json.dumps(_kv)
t("  and through a real check it goes from every place it was written, while the honest line stays",
  "nobody developed" not in _kall and "did not measure kidney stones" in _kall
  and "4 and 5 people" in _kv["explanation"], _kv)
t("  and the model's own check is told the same rule",
  "for an outcome that abstract never" in verdict._faithful_prompt("c", "t", ["l"], [1], _STONE))

# ---- an effect row about some other outcome -------------------------------------
_FLU_Q = "(influenza vaccine OR flu vaccine) AND (hospitalization OR hospital admission)"
_FLU = [_study("F1", "Influenza vaccination and hospital admission in older adults",
               abstract="Vaccinated adults had fewer hospital admissions for influenza (relative risk 0.62)."),
        _study("F2", "Influenza vaccine and Guillain-Barre syndrome: a meta-analysis", types=["Meta-Analysis"],
               abstract="We pooled 12 cohorts. There was no link with Guillain-Barre syndrome (odds ratio 1.02).")]
_flu_out = pubmed.split_and(_FLU_Q)[1]
_gbs = "There was no link between the vaccine and Guillain-Barre syndrome, odds ratio 1.02 (Study 2)."
t("off-outcome effect: a figure for another outcome, from a study that never names this one, is caught",
  breakdown.off_outcome(_gbs, _FLU, _flu_out))
t("  but not a figure that names the outcome, or one from a study about it",
  not breakdown.off_outcome("Hospital admissions fell, relative risk 0.62 (Study 1).", _FLU, _flu_out)
  and not breakdown.off_outcome("The relative risk was 0.62 (Study 1).", _FLU, _flu_out))
t("  nor a figure for the stand-in the search named for this outcome",
  not breakdown.off_outcome(_gbs, _FLU, _flu_out, surrogate="(Guillain-Barre syndrome)"))
fg.models.script = [Resp(VJ(
    "true", [1, 2], tldr="Flu vaccines seem to keep people out of hospital.",
    explanation="Vaccinated adults had fewer hospital admissions, relative risk 0.62 (Study 1). " + _gbs,
    evidence=["Vaccinated adults had fewer hospital admissions, relative risk 0.62 (Study 1).",
              "A review of 12 cohorts found no link with Guillain-Barre syndrome, odds ratio 1.02 (Study 2)."],
    effect_size=_gbs))]
_fv = verdict.weigh_evidence("Flu vaccines reduce hospital admissions", [dict(s) for s in _FLU], _FLU_Q)
_fbd = _fv["breakdown"] or {}
t("  through a real check, that row is left out of 'How big is the effect', not pointed at",
  "Guillain" not in (_fbd.get("effect_size") or "")
  and _fbd.get("effect_size") not in (breakdown.EFFECT_SIZE_ABOVE, breakdown.NO_EFFECT_SIZE), _fbd)
t("  and the size the takeaway is held to comes from the claim's own outcome",
  "0.62" in (_fbd.get("effect") or {}).get("figure", "") and not (_fbd.get("effect") or {}).get("null"),
  _fbd.get("effect"))

# ---- "healthy" is who was studied, so it comes from the studies -------------------
_INS = [_study("I1", "Valerian for chronic insomnia: a meta-analysis", types=["Meta-Analysis"],
               abstract="We pooled 14 trials in adults with chronic insomnia. Valerian did not shorten sleep onset."),
        _study("I2", "Valerian in healthy volunteers", abstract="Twenty healthy adults took valerian for a week."),
        _study("I3", "Valerian and glucose", abstract="Non-diabetic adults took valerian."),
        _study("I4", "Valerian", abstract="")]
t("'healthy' that no cited study says is taken out, and a sentence that started with it still starts upper case",
  breakdown.for_reading("It did not help healthy adults (Study 1).", _INS) == "It did not help adults (Study 1)."
  and breakdown.for_reading("Healthy adults slept no better (Study 1).", _INS) == "Adults slept no better (Study 1).")
t("  but stays when the study says it, or says it another way, or has no abstract to read",
  all(breakdown.for_reading(x, _INS) == x for x in (
      "It helped healthy adults sleep (Study 2).", "It was tried in healthy adults (Study 3).",
      "It was tried in healthy adults (Study 4).")))
t("  and an uncited sentence keeps it when any study shown says it",
  breakdown.for_reading("It may help healthy adults.", _INS) == "It may help healthy adults."
  and breakdown.for_reading("It may help healthy adults.", _INS[:1]) == "It may help adults.")
t("an adjective before 'efficacy' takes the noun: 'lack clear effect', 'the long-term effect and safety'",
  breakdown.plain("Trials lack clear efficacy in adults.") == "Trials lack clear effect in adults."
  and breakdown.plain("Studies must establish the long-term efficacy and safety of it.")
  == "Studies must establish the long-term effect and safety of it.")
t("a number in a name is not a result: no 'not significant' note after 'type 1 diabetes'",
  breakdown._figures("Children and adults with type 1 diabetes (Studies 1, 2).") == []
  and breakdown._figures("COVID-19 patients in a phase 3 trial (Study 4).") == []
  and breakdown._figures("Omega-3 lowered triglycerides by 0.3 mmol/L (Study 2).") == ["0.3"]
  and breakdown.say_nulls("Children and adults with type 1 diabetes (Study 1).",
                          [_study("1", "T1D diet", abstract="HbA1c fell 1 point, which was not significant (p = 0.4).")])
  == "Children and adults with type 1 diabetes (Study 1).")
t("'prevalence' and 'incidence' are 'rate' wherever the sentence needs a noun",
  breakdown.plain("A pooled review estimated the global prevalence of alcohol use during pregnancy.")
  == "A pooled review estimated the global rate of alcohol use during pregnancy."
  and breakdown.plain("The prevalence was 10%.") == "The rate was 10%."
  and breakdown.plain("Melanoma incidence fell.") == "Melanoma rate fell."
  and breakdown.plain("The estimated incidence was low.") == "The estimated rate was low."
  and breakdown.plain("No study measured incidence.") == "No study measured how often it happened.")
t("  and 'safety and efficacy' is 'safety and effect'",
  breakdown.plain("HPV vaccines have durable safety and efficacy.") == "HPV vaccines have durable safety and effect."
  and breakdown.plain("Vaccine efficacy was high.") == "Vaccine efficacy was high.")
fg.models.script = [Resp(VJ(
    "complicated", [1, 2], tldr="Valerian may not help people with insomnia sleep.",
    explanation="A pooled review found valerian did not shorten sleep onset in adults with chronic insomnia "
                "(Study 1). A small trial gave it to 20 healthy adults (Study 2).",
    evidence=["A pooled review of 14 trials found valerian did not shorten sleep onset (Study 1)."],
    strength="Pooled trials show it lacks clear efficacy in healthy adults (Study 1)."))]
_hv = verdict.weigh_evidence("Valerian helps you fall asleep", [dict(s) for s in _INS], "(valerian) AND (sleep onset)")
t("  through a real check, both reach the page fixed",
  "lacks clear effect in adults (Study 1)" in (_hv["breakdown"] or {}).get("strength", "")
  and "20 healthy adults (Study 2)" in _hv["explanation"], _hv)

# ---- the guideline check ------------------------------------------------------
t("guideline check: runs for medicine, vaccines, infections, cancer, pregnancy, stopping "
  "treatment and protective practices",
  all(verdict.guideline_topic(x) for x in (
      "Antibiotics cure the common cold", "Daily sunscreen use lowers the risk of skin cancer",
      "Drinking a little alcohol during pregnancy is safe", "Raw milk is healthier than pasteurized milk",
      "The HPV vaccine is safe", "Ivermectin cures COVID-19", "Skipping doses of your treatment is harmless",
      "People who have had a heart attack can safely stop taking statins")))
t("  and not for everyday claims, where it would only cost a call",
  not any(verdict.guideline_topic(x) for x in (
      "Cold showers boost immunity", "Eggs raise heart disease risk", "Creatine improves cognitive performance",
      "Detox teas remove toxins from the body", "Magnesium supplements improve sleep quality",
      "Red light therapy reduces wrinkles")))

verdict.guideline_view = _real_guideline_view
_GL = lambda pos, says, bodies=("CDC", "WHO"): Resp(json.dumps(
    {"position": pos, "bodies": list(bodies), "says": says}))
fg.models.script = [_GL("rejects", "Antibiotics do not work on colds, which are caused by viruses — not bacteria.")]
_gv = verdict.guideline_view("Antibiotics cure the common cold")
t("  a plain guideline is read as a verdict and one printable sentence",
  _gv and _gv["verdict"] == "false" and _gv["bodies"] == ["CDC", "WHO"]
  and _gv["says"].startswith("antibiotics do not work on colds") and "—" not in _gv["says"]
  and _gv["says"].endswith("."), _gv)
fg.models.script = [_GL("mixed", "", ())]
_mixed = verdict.guideline_view("Vitamin D prevents infections")
fg.models.script = [_GL("none", "", ())]
_none = verdict.guideline_view("Vitamin D prevents infections")
fg.models.script = [_GL("rejects", "fewer than 2 in 10 colds respond to antibiotics.")]
_digits = verdict.guideline_view("Antibiotics cure the common cold")
fg.models.script = [_GL("rejects", "antibiotics do not work on colds.", ())]
_nobody = verdict.guideline_view("Antibiotics cure the common cold")
fg.models.script = []
_down = verdict.guideline_view("Antibiotics cure the common cold")
t("  and mixed, unknown, a figure nothing can check, no named body, or a failed call all mean "
  "no guidance", _mixed is None and _none is None and _digits is None and _nobody is None
  and _down is None)

_named = []
for _line in ("the WHO report on raw milk says it is unsafe.",
              "the Dietary Guidelines for Americans advise against it.",
              "the \u201cGlobal Strategy on Diet\u201d says it is unsafe.",
              "a CDC fact sheet says antibiotics do not work on colds.",
              "the NHS guidance titled Healthy Eating says no."):
    fg.models.script = [_GL("rejects", _line)]
    _named.append(verdict.guideline_view("Antibiotics cure the common cold"))
fg.models.script = [_GL("rejects", "the CDC reports that antibiotics do not work on colds.")]
_reports = verdict.guideline_view("Antibiotics cure the common cold")
fg.models.script = [_GL("rejects", "Guidelines advise against antibiotics for colds.")]
_opens = verdict.guideline_view("Antibiotics cure the common cold")
t("  a guideline line that names a document (a report, a title, a fact sheet, quotes) is not used",
  _named == [None] * 5, _named)
t("  but naming who advises is fine, and so is 'reports' as a verb or 'Guidelines' opening the line",
  _reports and _reports["says"].startswith("the CDC reports")
  and _opens and _opens["says"].startswith("guidelines advise"), (_reports, _opens))
t("  and the prompt itself bans named reports and guideline titles, not only numbers",
  all(w in verdict._guideline_prompt("x") for w in ("never a document", "by its title", "no quotation marks", "no years")))

_GUIDE_STUDIES = [_study("G1", "Antibiotics for the common cold", types=["Systematic Review"]),
                  _study("G2", "Antibiotic prescribing for colds in primary care", types=["Observational Study"])]
_said = []
def _guide(v, says):
    def view(claim):
        _said.append(claim)
        return {"verdict": v, "says": says, "bodies": ["CDC", "WHO"]}
    return view
_mixed_result = {"verdict": "complicated", "tldr": "The evidence on antibiotics for colds is mixed.",
                 "explanation": "A review found antibiotics did not shorten colds (Study 1). "
                                "The evidence supports the claim in some settings (Study 2).",
                 "still_open": "Whether some people with colds benefit.", "cited_studies": ["G1", "G2"],
                 "breakdown": {"says": ["A review found antibiotics did not shorten colds (Study 1).",
                                        "The evidence supports the claim in some settings (Study 2)."]}}
verdict.guideline_view = _guide("false", "antibiotics do not work on colds, which are caused by viruses.")
_fl = verdict.follow_guidelines(dict(_mixed_result), [dict(s) for s in _GUIDE_STUDIES],
                                "Antibiotics cure the common cold")
t("guideline check: a verdict that conflicts with plain guidance follows it, and the takeaway says so",
  _fl["verdict"] == "false" and _fl["tldr"] == "Major health guidelines say: antibiotics do not work on "
  "colds, which are caused by viruses." and _fl["cited_studies"] == ["G1", "G2"]
  and _fl["still_open"] == _mixed_result["still_open"], _fl)
t("  and a research line or explanation sentence that rules against the new stamp goes",
  "did not shorten colds" in _fl["explanation"] and "supports the claim" not in _fl["explanation"]
  and _fl["breakdown"]["says"] == ["A review found antibiotics did not shorten colds (Study 1)."], _fl)
t("  and it is a code-written takeaway, so the faithfulness check leaves it alone",
  verdict._code_takeaway(_fl["tldr"]))
_agree = dict(_mixed_result, verdict="false", tldr="No, antibiotics don't cure colds.")
t("  a verdict that already agrees is left exactly as it was",
  verdict.follow_guidelines(dict(_agree), _GUIDE_STUDIES, "Antibiotics cure the common cold") == _agree)
_said.clear()
t("  and a claim outside those topics never makes the call",
  verdict.follow_guidelines(dict(_mixed_result), _GUIDE_STUDIES, "Cold showers boost immunity")
  == _mixed_result and not _said)
verdict.guideline_view = _guide("true", "antibiotics do not cure colds.")
t("  a guideline sentence that argues with its own position is not used",
  verdict.follow_guidelines(dict(_mixed_result), _GUIDE_STUDIES, "Antibiotics cure the common cold")
  == _mixed_result)

_LAB = [_study("L1", "Antibacterial activity of colloidal silver against bacteria in vitro",
               abstract="Silver nanoparticles inhibited bacterial growth in cultured cells in vitro.",
               types=["Journal Article"])]
_lab_result = {"verdict": "insufficient", "tldr": "Nothing has tested this in people yet. The only papers "
               "are animal or lab studies.", "explanation": "Silver stopped bacteria in a dish (Study 1).",
               "still_open": "Whether it works in people.", "cited_studies": ["L1"], "breakdown": None}
verdict.guideline_view = _guide("false", "colloidal silver is not safe or effective for treating any disease.")
_kept = verdict.follow_guidelines(dict(_lab_result), [dict(s) for s in _LAB], "Colloidal silver cures infections")
t("guideline check: never overrules 'Not enough evidence' on papers that test nothing in people; "
  "it adds the guidance to the takeaway",
  _kept["verdict"] == "insufficient" and _kept["tldr"].startswith("Nothing has tested this in people yet.")
  and _kept["tldr"].endswith("Major health guidelines say: colloidal silver is not safe or effective "
                             "for treating any disease."), _kept)
_none_cited = dict(_lab_result, verdict="complicated", cited_studies=[],
                   tldr="Couldn't reach a verdict from the evidence found.")
t("  and never rates a verdict that used no study at all",
  verdict.follow_guidelines(dict(_none_cited), _LAB, "Colloidal silver cures infections") == _none_cited)
_none_found = verdict.weigh_evidence("Colloidal silver cures infections", [], "")
t("  and when PubMed finds nothing, the stamp stays 'Not enough evidence' with the guidance added",
  _none_found["verdict"] == "insufficient" and "Major health guidelines say: colloidal silver"
  in _none_found["tldr"], _none_found)

# Through a real check: the flipped verdict is what is shown and cached.
verdict.guideline_view = _guide("true", "daily sunscreen use lowers the risk of skin cancer.")
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in STUDIES]
fg.models.script = [Resp("sunscreen AND skin cancer"),
                    Resp(VJ("complicated", [1, 2], tldr="The evidence on sunscreen and skin cancer is mixed.",
                            explanation="One trial found fewer skin cancers (Study 1). Another found no "
                                        "difference (Study 2)."))]
_sun = P("Daily sunscreen lowers the risk of skin cancer", ip="7.7.7.31").get_json()
_sun_cached = db.get_cached_verdict("Daily sunscreen lowers the risk of skin cancer", 24)
t("guideline check: through a real check, the page and the cache both carry the guidance",
  _sun.get("verdict") == "true" and _sun.get("tldr", "").startswith("Major health guidelines say: ")
  and _sun_cached and _sun_cached["verdict"] == "true"
  and _sun_cached["tldr"].startswith("Major health guidelines say: "), _sun)
t("  with no dash anywhere in what the reader sees",
  not re.search("[–—]", json.dumps(_sun, ensure_ascii=False)))
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES
verdict.guideline_view = lambda claim: None

# The safety set (scripts/safety.py) is a merge gate, so its file is checked
# here: every verdict it names is a real one, and no claim lists the same
# verdict as both safe and harmful.
_SAFE = json.loads((ROOT / "tests" / "safety_claims.json").read_text())["claims"]
_VERDICTS = {"true", "false", "complicated", "insufficient"}
t("safety set: 15 claims, numbered in order, every verdict a real one",
  len(_SAFE) == 15 and [x["n"] for x in _SAFE] == list(range(1, 16))
  and all({x["expected"], *x.get("also_ok", []), *x["harmful"]} <= _VERDICTS for x in _SAFE))
t("  and no verdict is both safe and harmful for the same claim",
  all(not ({x["expected"], *x.get("also_ok", [])} & set(x["harmful"])) for x in _SAFE))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
