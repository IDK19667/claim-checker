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
import types
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
import og  # noqa: E402
import db  # noqa: E402
db.COUNT_FLUSH_SECONDS = 0  # no background writer in tests; they call db.flush_counts()
import pubmed  # noqa: E402
import ratelimit  # noqa: E402
import verdict  # noqa: E402
from google.genai import errors  # noqa: E402
import requests  # noqa: E402
import socket  # noqa: E402

# No test may use the network: the provider and PubMed are faked. Any test
# that opens a real connection, or looks a host up, fails here loudly and is
# counted at the end, whatever the code under test does with the error.
NET_TRIES = []


def _no_network(*a, **k):
    NET_TRIES.append(repr(a[1:] if a and isinstance(a[0], socket.socket) else a)[:120])
    raise OSError("tests/test_app.py: a test tried to use the network")


import _socket  # noqa: E402

for _name in ("connect", "connect_ex", "sendto", "sendmsg"):
    setattr(socket.socket, _name, _no_network)


class _GuardedRawSocket(_socket.socket):
    """`_socket.socket` itself cannot be patched, so its name is pointed at
    this subclass: code that builds a raw socket gets one that refuses."""
    connect = connect_ex = sendto = sendmsg = _no_network


_socket.socket = _GuardedRawSocket
for _mod in (socket, _socket):
    for _name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        setattr(_mod, _name, _no_network)
socket.create_connection = _no_network
# Child processes (node for the parity checks, python for the schema race)
# are separate interpreters: this guard is not in them. None of them is
# given network work, and they are not covered.

# Gemini calls are retried after a pause. No test waits for it: verdict's
# clock is a recorder, and the retry tests below read what it was asked for.
PAUSES = []
verdict.time = types.SimpleNamespace(sleep=PAUSES.append)

appmod.app.logger.setLevel(logging.ERROR)
c = appmod.app.test_client()
results = []

# Every study any response carries, from every route, is recorded here so
# the last test can say none was ever both relied on and off topic.
_PAYLOADS = []
_real_study_payload = appmod._study_payload
appmod._study_payload = lambda s_, cited_: _PAYLOADS.append(_real_study_payload(s_, cited_)) or _PAYLOADS[-1]


def t(label, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'} {label} {extra if not cond else ''}")


try:
    requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi", timeout=1)
    _net_refused = False
except Exception:  # noqa: BLE001 — any failure is the guard working
    _net_refused = True
t("network guard: a real request from a test is refused and recorded", _net_refused and NET_TRIES, NET_TRIES)
_net_kinds = []
for _lbl, _try in (("gethostbyname", lambda: socket.gethostbyname("localhost")),
                   ("gethostbyname_ex", lambda: socket.gethostbyname_ex("localhost")),
                   ("a raw lookup", lambda: _socket.gethostbyname("localhost")),
                   ("a UDP sendto", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 9))),
                   ("a raw socket connect", lambda: _socket.socket().connect(("127.0.0.1", 9)))):
    _before = len(NET_TRIES)
    try:
        _try(); _refused = False
    except OSError:
        _refused = True
    _net_kinds.append((_lbl, _refused and len(NET_TRIES) == _before + 1))
t("  and so are a host lookup, a UDP send and a raw socket's connect, each counted",
  all(ok for _, ok in _net_kinds), _net_kinds)
NET_TRIES.clear()


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
_hz = c.get("/healthz")
t("healthz", _hz.status_code == 200 and _hz.get_json()["ok"] is True)
t("healthz reports a short commit string",
  isinstance(_hz.get_json().get("commit"), str) and set(_hz.get_json()) == {"ok", "commit", "store"})
_old_env = os.environ.get("RENDER_GIT_COMMIT")
os.environ["RENDER_GIT_COMMIT"] = "d4a682d9f3c1b2a0e8e7d6c5b4a39281706f5e4d"
t("commit is the 7-character prefix of RENDER_GIT_COMMIT", appmod._deployed_commit() == "d4a682d")
os.environ.pop("RENDER_GIT_COMMIT")
_real_run = appmod.subprocess.run


def _git_fails(*a, **k):
    raise FileNotFoundError("git")


appmod.subprocess.run = _git_fails
t("commit is unknown with no env var and git failing", appmod._deployed_commit() == "unknown")
appmod.subprocess.run = _real_run
if _old_env is not None:
    os.environ["RENDER_GIT_COMMIT"] = _old_env
for icon in ("icon-192.png", "icon-512.png", "icon-512-maskable.png", "apple-touch-icon.png", "icon.svg"):
    t(f"icon {icon}", c.get(f"/static/icons/{icon}").status_code == 200)

# ---- validation -------------------------------------------------------------
t("empty claim 400", P("").status_code == 400)
t("500+ char claim 400", P("x" * 501).status_code == 400)
r = P("x")
t("no keys -> 500, with the operator's instructions kept out of the page",
  r.status_code == 500 and r.get_json()["error"] == appmod.UNAVAILABLE_MESSAGE
  and "aistudio" not in r.get_json()["error"] and ".env" not in r.get_json()["error"])

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
        # fail open (the faithfulness check, the direction read) does. It is
        # a dropped connection, which is not retried, so each call that finds
        # the script empty still counts once.
        if not self.script:
            raise ConnectionError("script empty")
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


def VJ(v, nums, tldr="Short take.", still_open="Longer trials are needed.", **extra):
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
fg.models.script = [errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})] * 3
r = P("err a"); t("gemini 429 -> 429 w/ retry_after", r.status_code == 429 and r.get_json()["retry_after"] == 20)
fg.models.script = [errors.ClientError(400, {"error": {"message": "API key not valid.", "status": "INVALID_ARGUMENT"}})]
r = P("err b"); t("gemini bad key -> 500, and the page says only that the checker is unavailable",
  r.status_code == 500 and r.get_json()["error"] == appmod.UNAVAILABLE_MESSAGE
  and "aistudio" not in r.get_json()["error"] and ".env" not in r.get_json()["error"])
fg.models.script = [errors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}})] * 3
t("gemini 503 -> 502", P("err c").status_code == 502)
# A reply that could not be read at all is a failed check, not a verdict:
# the app's 502 with what went wrong and a way out, on the plain check and
# the streamed one, and nothing saved, logged or listed. One per kind.
# The way out follows the kind of failure: a declined reply is declined again
# at temperature 0, so only other words can help ("reword": Edit claim, no
# Retry); a reply cut off may or may not come back whole ("either": both); a
# reply that could not be read is cleared by trying again (no way_out field).
_UNREAD_KINDS = (("not JSON", Resp("not json"), verdict.UNREADABLE,
                  "The AI model sent back a reply that could not be read, so this check did not finish. Try again in a moment.", None),
                 ("JSON that is not an object", Resp("[1, 2]"), verdict.UNREADABLE,
                  "The AI model sent back a reply that could not be read, so this check did not finish. Try again in a moment.", None),
                 ("cut off", Resp("", "FinishReason.MAX_TOKENS"), "ran out of room before finishing",
                  "The AI model ran out of room before finishing, so this check did not finish. Try again, or try the claim in other words.", "either"),
                 ("declined", Resp(""), "declined to answer this one",
                  "The AI model declined to answer this one, so this check did not finish. Try the claim in other words.", "reword"))
for _i, (_kind, _reply, _why, _want, _way) in enumerate(_UNREAD_KINDS):
    t(f"a reply that is {_kind}: the message names its own way out",
      verdict.unread_reply(_why) == _want and (verdict.way_out(_why) or None) == _way)
    _body = {"error": _want, **({"way_out": _way} if _way else {})}
    _logs0 = len(db.recent_checks(1000))
    fg.models.script = [Resp("q"), _reply]
    r = P(f"err d{_i}")
    t(f"a reply that is {_kind} is a failed check: 502, naming the problem and the way out",
      r.status_code == 502 and r.get_json() == _body, (r.status_code, r.get_json()))
    fg.models.script = [Resp("q"), _reply]
    _r, _evs = stream(f"err s{_i}", ip=f"8.8.6.{_i}")
    t(f"  and the streamed check ends in the same error event",
      _evs and _evs[-1] == {"stage": "error", "status": 502, **_body}, _evs[-1:])
    t(f"  and neither is saved or logged as a verdict",
      db.get_cached_by_key(db.normalize_claim(f"err d{_i}")) is None
      and db.get_cached_by_key(db.normalize_claim(f"err s{_i}")) is None
      and len(db.recent_checks(1000)) == _logs0)
    t(f"  and the message has no dash a reader would see", not any(ch in _want for ch in "\u2013\u2014"))
fg.models.script = [Resp("q"), Resp(VJ("complicated", [1]))]
_good = P("err good reply", ip="8.8.6.9")
t("a good reply is unaffected: 200, saved and logged as before",
  _good.status_code == 200 and _good.get_json()["tldr"] == "Short take."
  and db.get_cached_verdict("err good reply", 24) is not None
  and db.recent_checks(1)[0]["claim_text"] == "err good reply", _good.get_json())
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': (_ for _ in ()).throw(requests.ConnectionError("down"))
fg.models.script = [Resp("q")]
r = P("err f"); t("PubMed down -> 502", r.status_code == 502 and "PubMed" in r.get_json()["error"])
t("failed checks not cached", all(db.get_cached_verdict(x, 24) is None for x in ("err a", "err b", "err c", "err f")))
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

# A reply that leaves its still-open line empty is a failed reply: the app's
# error response, with a way out, and nothing saved or logged as an answer.
def _no_row(claim):
    with db.get_conn() as _conn:
        return _conn.execute("SELECT COUNT(*) n FROM verdict_cache WHERE claim_key = ?",
                             (db.normalize_claim(claim),)).fetchone()["n"] == 0


for _lbl, _so in (("empty", ""), ("blank", "   "), ("a zero-width space", "\u200b"),
                  ("a lone full stop", "."), ("a lone dash", "\u2014"), ("only marks", " - ,. ")):
    fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("complicated", [1], still_open=_so))]
    _n_logs = len(db.recent_checks(1000))
    r = P(f"empty line claim {_lbl}", ip="7.7.9.1")
    t(f"a reply whose still-open line is {_lbl} is a failed reply: 502 with the reason and a way out",
      r.status_code == 502 and r.get_json()["error"] == verdict.INCOMPLETE_REPLY
      and "Try again" in r.get_json()["error"], r.get_json())
    t(f"  and it offers both ways out: try again, or try the claim in other words ({_lbl})",
      r.get_json() == {"error": "The AI model sent back an answer with a line missing. "
                                "Try again, or try the claim in other words.", "way_out": "either"}, r.get_json())
    t(f"  and it is never saved, and not logged as a check ({_lbl})",
      _no_row(f"empty line claim {_lbl}") and len(db.recent_checks(1000)) == _n_logs)
fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("complicated", [1], still_open=""))]
_r, _evs = stream("empty line claim streamed", ip="7.7.9.2")
t("  the streamed check ends on the same error, never on a done stage",
  _evs[-1]["stage"] == "error" and _evs[-1]["status"] == 502 and _evs[-1]["error"] == verdict.INCOMPLETE_REPLY
  and not any(e["stage"] == "done" for e in _evs) and _no_row("empty line claim streamed"), _evs[-1])
t("  and the streamed error offers both ways out too", _evs[-1].get("way_out") == "either", _evs[-1])
t("  and the message has no dash", not any(ch in verdict.INCOMPLETE_REPLY for ch in "\u2013\u2014"))
for _lbl, _kw in (("still-open line is a lone full stop", {"still_open": "."}),
                  ("still-open line is a zero-width space", {"still_open": "\u200b"}),
                  ("short answer is a zero-width space", {"tldr": "\u200b"})):
    fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("complicated", [1], **_kw)), Resp(VJ("complicated", [1], **_kw))]
    _r, _evs = stream(f"wordless line claim streamed {_lbl}", ip="7.7.9.5")
    t(f"  a streamed reply whose {_lbl} ends on the error, with nothing saved",
      _evs[-1]["stage"] == "error" and _evs[-1]["status"] == 502
      and not any(e["stage"] == "done" for e in _evs) and _no_row(f"wordless line claim streamed {_lbl}"), _evs[-1])
t("  a line needs a letter or digit, in any script",
  verdict.has_lines({"tldr": "Ja.", "still_open": "长期研究"}) and verdict.has_lines({"tldr": "No.", "still_open": "2"})
  and not verdict.has_lines({"tldr": "No.", "still_open": "\u200b\u00a0"})
  and not verdict.has_lines({"tldr": "\u2014", "still_open": "Open."}))
for _lbl, _kw in (("a lone full stop", {"still_open": "."}), ("a zero-width space", {"tldr": "\u200b"}),
                  ("a lone dash", {"still_open": "\u2014"})):
    _c = f"Wordless saved row with {_lbl}"
    db.put_cached_verdict(_c, "(q)", "complicated", "Mixed (Study 1).", ["P1"], STUDIES,
                          **{"tldr": "Short.", "still_open": "Open.", **_kw})
    t(f"  a saved row whose line is {_lbl} is no saved answer: not served, not on a link, never matched",
      db.get_cached_verdict(_c, 24) is None and db.get_cached_by_key(db.normalize_claim(_c)) is None
      and db.find_equivalent("Is it true that " + _c.lower() + "?") is None)
fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("false", [], still_open=""))]
d = P("empty line claim resting on nothing", ip="7.7.9.3").get_json()
t("  a reply the code turns to 'Not enough evidence' gets its still-open line from code, and is an answer",
  d.get("verdict") == "insufficient" and d.get("still_open") == "Whether any study has tested this claim directly."
  and not _no_row("empty line claim resting on nothing"), d)
fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("complicated", [1], still_open="Doses vary."))]
d = P("empty line claim whole", ip="7.7.9.4").get_json()
t("  a whole reply is unaffected: shown, with its line, and saved",
  d.get("still_open") == "Doses vary." and db.get_cached_verdict("empty line claim whole", 24), d)
fg.models.script = [Resp(VJ("complicated", [1], still_open=""))]
try:
    verdict.weigh_evidence("empty line unit", [dict(x) for x in STUDIES], "(x) AND (y)"); _raised = None
except verdict.ProviderError as e:
    _raised = e
t("  weigh_evidence raises the provider's 'other' failure for it",
  _raised is not None and _raised.kind == "other" and _raised.detail == verdict.INCOMPLETE_REPLY)

# ---- gemini retries -----------------------------------------------------------
def _scripted(*items):
    seq, calls = list(items), []

    def go():
        calls.append(1)
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return go, calls


def _run_wrap(*items):
    go, calls = _scripted(*items)
    del PAUSES[:]
    try:
        return verdict._gemini_wrap(go), None, len(calls), list(PAUSES)
    except verdict.ProviderError as e:
        return None, e, len(calls), list(PAUSES)


def _e503(): return errors.ServerError(503, {"error": {"message": "busy", "status": "UNAVAILABLE"}})
def _e500(): return errors.ServerError(500, {"error": {"message": "oops", "status": "INTERNAL"}})
def _e429(): return errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})


_v, _e, _n, _p = _run_wrap(_e503(), _e503(), "ok")
t("gemini retry: 503, 503, then success returns the answer after 3 tries, pausing 1s then 3s",
  _v == "ok" and _e is None and _n == 3 and _p == [1.0, 3.0], (_v, _e, _n, _p))
_v, _e, _n, _p = _run_wrap(_e503(), _e503(), _e503())
t("  503 three times: the same 'unavailable' error and message as before, after 3 tries",
  _e is not None and _e.kind == "unavailable" and _e.detail == "The AI service is having trouble." and _e.code == 503
  and _n == 3 and _p == [1.0, 3.0], (_e, _n, _p))
# H9: no status number in a message a reader sees; the code stays on the error
# for the log. Every ProviderError message built in verdict.py, then the page
# and the stream for a provider 503.
import re  # noqa: E402
_h9_src = open(os.path.join(os.path.dirname(os.path.abspath(verdict.__file__)), "verdict.py")).read()
_h9_built = re.findall(r'ProviderError\(\s*"\w+",\s*(f?"[^"]*")', _h9_src)
t("H9: no ProviderError message built in verdict.py carries a status number or a {code}",
  _h9_built and not any(re.search(r"\d{3}|\{", m_) for m_ in _h9_built), _h9_built)
_h9_e = [_run_wrap(x(), x(), x())[1] for x in (_e503, _e500)] + [
    _run_wrap(errors.ClientError(404, {"error": {"message": "no model", "status": "NOT_FOUND"}}))[1]]
t("  and the errors a 5xx or a 4xx raises keep the code for the log, not in their words",
  all(e_ is not None and e_.code in (503, 500, 404) and not re.search(r"\d", e_.detail) for e_ in _h9_e),
  [(e_.code, e_.detail) for e_ in _h9_e if e_])
_v, _e, _n, _p = _run_wrap(_e429(), "ok")
t("  429 then success returns the answer, pausing 1s once", _v == "ok" and _n == 2 and _p == [1.0], (_v, _n, _p))
_v, _e, _n, _p = _run_wrap(_e429(), _e429(), _e429())
t("  429 three times: 'rate_limit' after 3 tries", _e is not None and _e.kind == "rate_limit" and _n == 3, (_e, _n))
_v, _e, _n, _p = _run_wrap(errors.ClientError(400, {"error": {"message": "API key not valid.", "status": "INVALID_ARGUMENT"}}), "ok")
t("  a rejected key is never retried: 'auth' after 1 try, no pause",
  _e is not None and _e.kind == "auth" and _n == 1 and _p == [], (_e, _n, _p))
_v, _e, _n, _p = _run_wrap(errors.ClientError(404, {"error": {"message": "no model", "status": "NOT_FOUND"}}), "ok")
t("  any other 4xx is never retried: 'other' after 1 try, no pause",
  _e is not None and _e.kind == "other" and _n == 1 and _p == [], (_e, _n, _p))
_v, _e, _n, _p = _run_wrap(_e500(), "ok")
t("  any 5xx is retried, not just 503", _v == "ok" and _n == 2 and _p == [1.0], (_v, _n, _p))
_v, _e, _n, _p = _run_wrap(ConnectionError("down"), "ok")
t("  a dropped connection is not retried", _e is not None and _e.kind == "unavailable" and _n == 1 and _p == [], (_e, _n, _p))
del PAUSES[:]


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
fg.models.script = [Resp("q"), Resp(VJ("false", [], still_open=""))]
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
_ci = _re.search(r'<textarea\s[^>]*id="claim-input"[^>]*>', c.get("/").get_data(as_text=True), _re.S).group(0)
t("the claim field is a plain textbox: no role the textarea may not have, no aria-expanded",
  _ci and "role=" not in _ci and "aria-expanded" not in _ci and 'aria-controls="suggest-list"' in _ci and 'aria-autocomplete="list"' in _ci, _ci)
t("uncached claim renders the ask view", '<section id="result" hidden>' in c.get("/?q=never%20checked").data.decode())

r = c.get("/robots.txt")
t("robots.txt allows crawlers and names the sitemap",
  r.status_code == 200 and "Disallow: /api/" in r.data.decode() and "Sitemap:" in r.data.decode()
  and "ClaudeBot" in r.data.decode())
r = c.get("/sitemap.xml")
t("sitemap is XML and lists the static pages", r.status_code == 200
  and r.mimetype == "application/xml" and "/privacy" in r.data.decode() and "/method" in r.data.decode())
r = c.get("/llms.txt")
t("llms.txt describes the rules and no longer lists a typed, non-curated claim",
  r.status_code == 200 and "ClaimReview" in r.data.decode() and "not medical advice" in r.data.decode().lower()
  and "streamed claim" not in r.data.decode())

# ---- privacy page ----------------------------------------------------------------
r = c.get("/privacy")
t("privacy page", r.status_code == 200 and b"No tracking" in r.data and b"Not medical advice" in r.data)
t("  it says what the AI provider is sent, that it gets nothing that identifies you, and that the verdict can be wrong",
  b"The AI provider is sent the claim and the abstracts" in r.data and b"does not get your IP address" in r.data
  and b"can still be wrong" in r.data)

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

# The chart's "By study design" counts follow the same kind() that colours the
# shelf, so an animal or lab paper is never described as a human study.
_MOUSE = {"pmid": "K1", "title": "Anti-inflammatory effect of celery leaf extract in murine colitis",
          "abstract": "Colitis was induced in C57BL/6 mice. Mice given the extract had lower "
                      "TNF-alpha and less colon damage than controls.",
          "publication_types": ["Journal Article", "Research Support, Non-U.S. Gov't"]}
_DISH = {"pmid": "K2", "title": "Apigenin and gastric cancer cells",
         "abstract": "In vitro, apigenin reduced growth of the AGS cell line and altered "
                     "expression of several pathway proteins.",
         "publication_types": ["Journal Article"]}
_VESICLES = {"pmid": "K3", "title": "Characterisation of plant-derived vesicles",
             "abstract": "This study aimed to characterise the physical and proteomic properties "
                         "of vesicles isolated from the root.",
             "publication_types": ["Journal Article"]}
_RCT = {"pmid": "K4", "title": "Nitrate intake and inflammatory markers in hypertension",
        "abstract": "We assessed in 60 adults with hypertension whether one week of beetroot "
                    "juice lowered inflammatory markers.",
        "publication_types": ["Randomized Controlled Trial", "Journal Article"]}
_COHORT = {"pmid": "K5", "title": "Fruit and vegetable intake and CRP",
           "abstract": "In a cohort of 4,000 women followed for ten years, higher intake was "
                       "linked to lower C-reactive protein.",
           "publication_types": ["Journal Article"]}
_BARE = {"pmid": "K6", "title": "Celery and inflammation", "abstract": "",
         "publication_types": ["Journal Article"]}
t("a mouse study tagged only Journal Article is an animal or lab study",
  evidence.kind(_MOUSE) == "lab" and evidence.kind(_DISH) == "lab")
t("  and so is a paper that never says it was run on people",
  evidence.kind(_VESICLES) == "lab")
t("  while a human randomized trial stays strong and an untagged human cohort stays human",
  evidence.kind(_RCT) == "strong" and evidence.kind(_COHORT) == "human")
t("  and a record with no abstract is not judged off its silence",
  evidence.kind(_BARE) == "human")
_cm = {m["kind"]: m for m in evidence.mix([_MOUSE, _DISH, _VESICLES, _RCT, _COHORT])}
t("the design mix counts animal and lab papers as such, not as human studies",
  _cm["lab"]["count"] == 3 and _cm["lab"]["label"] == "animal or lab studies"
  and _cm["human"]["count"] == 1 and _cm["human"]["label"] == "human study"
  and _cm["strong"]["count"] == 1, _cm)
t("  and the chart's sentence says so",
  "3 animal or lab studies" in evidence.snapshot([_MOUSE, _DISH, _VESICLES, _RCT, _COHORT])["described"]
  and "7 human studies" not in evidence.snapshot([_MOUSE, _DISH, _VESICLES, _RCT, _COHORT])["described"])
t("  and the mix still adds up to the studies read",
  sum(m["count"] for m in evidence.mix([_MOUSE, _DISH, _RCT, _COHORT])) == 4)

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
t("the front page shows no counts strip: the latest checks carry the work",
  'class="ledger"' not in _html and "Studies read" not in _html and "Pooled analyses" not in _html
  and "Claims checked" not in _html)
import inspect as _insp_led  # noqa: E402
_css_led = (ROOT / "static" / "style.css").read_text()
t("  its CSS is gone and the home page stops computing it, while db.ledger() stays",
  not re.search(r"^\.ledger[ ,{]", _css_led, re.M) and ".entry-no" not in _css_led
  and "db.ledger()" not in _insp_led.getsource(appmod._render_home) and callable(db.ledger))
t("the front page lists recent checks with their evidence",
  "Latest checks" in _html and 'class="entry-claim"' in _html and 'class="mini"' in _html)
_entry_css = re.search(r"^\.entry-link \{(.*?)\}", _css_led, re.M | re.S).group(1)
t("  without the number line, a list row's link still has a 44px tap height, with the layout unmoved",
  "padding: 10px 0" in _entry_css and "margin: -10px 0" in _entry_css, _entry_css)
_404_led = c.get("/no-such-page-led").get_data(as_text=True)
t("  with no issue numbers, on the home page or the 404 page",
  "entry-no" not in _html and "entry-no" not in _404_led and not re.search(r">No\. \d+<", _html + _404_led)
  and 'class="entry-claim"' in _404_led)
t("the front page never says '1 studies'", "1 studies read" not in _html)
# Sentence case is the template's: the stored text and the link keep the
# wording as typed, only the first letter shown is raised.
db.put_cached_verdict("green tea speeds up METABOLISM", "green tea AND metabolism", "complicated", "x",
                      ["SC1"], [{"pmid": "SC1", "title": "Green tea and energy use", "abstract": "a", "journal": "J", "year": "2020",
                        "authors": ["A B"], "data_banks": None, "url": "u", "publication_types": ["Randomized Controlled Trial"]}],
                      tldr="Maybe a little.", still_open="How much is unclear.")
_sc_html = c.get("/").data.decode()
_sc_row = db.get_cached_by_key(db.normalize_claim("green tea speeds up METABOLISM"))
t("the Latest list shows a claim in sentence case, and only the first letter changes",
  '<span class="entry-claim">Green tea speeds up METABOLISM</span>' in _sc_html
  and 'href="/?q=green%20tea%20speeds%20up%20METABOLISM"' in _sc_html
  and _sc_row["claim_text"] == "green tea speeds up METABOLISM",
  [x for x in _sc_html.split('class="entry-claim">')[1:3]])

# One rule for every list of claims: the first letter is raised only when the
# first word is all lower case letters. Mixed or upper case words, numbers and
# quotation marks at the start are left exactly as stored.
import display as _dsc  # noqa: E402
import shutil as _shutil, subprocess as _subprocess  # noqa: E402
from markupsafe import escape as _msc_escape  # noqa: E402
_SC_CASES = [("mRNA vaccines change DNA", "mRNA vaccines change DNA"),
             ("pH balanced water is healthier", "pH balanced water is healthier"),
             ("iPhone screens harm sleep", "iPhone screens harm sleep"),
             ("DNA tests predict disease", "DNA tests predict disease"),
             ("HIV can be cured", "HIV can be cured"),
             ("COVID-19 vaccines cause infertility", "COVID-19 vaccines cause infertility"),
             ("5G towers cause headaches", "5G towers cause headaches"),
             ("10,000 steps a day is needed", "10,000 steps a day is needed"),
             ("\u201cdetox\u201d teas work", "\u201cdetox\u201d teas work"),
             ('"natural" sugar is better', '"natural" sugar is better'),
             ("coffee causes cancer", "Coffee causes cancer"),
             ("coffee, daily, causes cancer", "Coffee, daily, causes cancer"),
             ("e-cigarettes help people quit", "E-cigarettes help people quit"),
             ("vitamin D prevents colds", "Vitamin D prevents colds"),
             ("Garlic lowers blood pressure", "Garlic lowers blood pressure"),
             ("\u00e9pinards are rich in iron", "\u00c9pinards are rich in iron"),
             (" leading space stays", " leading space stays"), ("", "")]
t("sentence case: the first letter is raised only when the first word is all lower case letters",
  [_dsc.sentence_case(x) for x, _ in _SC_CASES] == [w for _, w in _SC_CASES],
  [(x, _dsc.sentence_case(x)) for x, w in _SC_CASES if _dsc.sentence_case(x) != w])
_sc_js = (ROOT / "static" / "app.js").read_text()
if _shutil.which("node"):
    _sc_fn = _sc_js[_sc_js.index("function sentenceCase"):_sc_js.index("function claimListHtml")]
    _sc_out = json.loads(_subprocess.run(["node", "-e", _sc_fn + "console.log(JSON.stringify("
                                          + json.dumps([x for x, _ in _SC_CASES]) + ".map(sentenceCase)));"],
                                         capture_output=True, text=True, check=True).stdout)
    t("  and the script's sentenceCase gives the same on every case", _sc_out == [w for _, w in _SC_CASES], _sc_out)
t("  every list the script builds uses it: recent and trending, related claims, type-ahead",
  "${escapeHtml(sentenceCase(r.claim))}</span>" in _sc_js
  and '<span class="entry-claim">${escapeHtml(sentenceCase(r.claim_text))}</span>' in _sc_js
  and '<span class="suggest-claim">${escapeHtml(sentenceCase(r.claim_text))}' in _sc_js
  and 'data-claim="${escapeHtml(r.claim)}"' in _sc_js and "href=\"/?q=${encodeURIComponent(r.claim_text)}\"" in _sc_js)
_sc_tpls = {n: (ROOT / "templates" / n).read_text() for n in ("index.html", "404.html")}
t("  and both templates' lists use the one filter, with no first-letter upper of their own",
  all("{{ v.claim_text | sentence_case }}" in x and "[:1] | upper" not in x for x in _sc_tpls.values()))
db.put_cached_verdict("mRNA vaccines change DNA", "mrna AND dna", "false", "x",
                      ["SC2"], [{"pmid": "SC2", "title": "mRNA and the genome", "abstract": "a", "journal": "J", "year": "2021",
                        "authors": ["A B"], "data_banks": None, "url": "u", "publication_types": ["Review"]}],
                      tldr="No.", still_open="Long-term data are still coming.")
_sc_home = c.get("/").data.decode()
_sc_404 = c.get("/no-such-page-sc").get_data(as_text=True)
t("  the home page's Latest list shows 'mRNA vaccines change DNA' as stored, and the link is untouched",
  '<span class="entry-claim">mRNA vaccines change DNA</span>' in _sc_home and "MRNA" not in _sc_home
  and 'href="/?q=mRNA%20vaccines%20change%20DNA"' in _sc_home, _sc_home.count("entry-claim"))
t("  and the 404 list raises a lower case first word and leaves a mixed case one",
  '<span class="entry-claim">Green tea speeds up METABOLISM</span>' in _sc_404
  and '<span class="entry-claim">mRNA vaccines change DNA</span>' in _sc_404
  and 'href="/?q=green%20tea%20speeds%20up%20METABOLISM"' in _sc_404, _sc_404.count("entry-claim"))
with appmod.app.test_request_context("/"):
    _sc_r404 = appmod.render_template("404.html", labels=appmod.VERDICT_LABELS,
                                      latest=[{"claim_text": x, "verdict": "false"} for x, _ in _SC_CASES if x.strip()])
t("  and on each case the 404 list prints what the rule says",
  all(f'<span class="entry-claim">{_msc_escape(w)}</span>' in _sc_r404 for x, w in _SC_CASES if x.strip()),
  [w for x, w in _SC_CASES if x.strip() and f'<span class="entry-claim">{_msc_escape(w)}</span>' not in _sc_r404])

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

# An effect line that says there was no clear difference is no effect, even
# when no ratio can be read from it. Read as a missing size, a "cure" claim's
# evidence line fell back to an unsized effect "not a cure".
_NULL_ST = [{"pmid": "NL1", "title": "A mouthwash trial in mild infection", "journal": "J", "year": "2020",
             "authors": ["A B"], "data_banks": None, "url": "https://pubmed.ncbi.nlm.nih.gov/NL1/",
             "publication_types": ["Randomized Controlled Trial"],
             "abstract": "METHODS: 49 patients used the mouthwash or placebo. RESULTS: The mouthwash did not "
                         "reduce salivary viral titer at 10, 30, or 60 minutes compared to placebo, and the "
                         "difference was not statistically significant."}]
_null_bd = breakdown.ground(
    {"parts": [], "evidence": ["The mouthwash did not reduce viral titer compared to placebo (Study 1)."],
     "effect_size": "The trial found no drop in viral titer at 10, 30, or 60 minutes compared to placebo (Study 1). "
                    "That difference was not statistically significant (Study 1).",
     "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": ""},
    _NULL_ST, verdict.tidy_prose)
t("grounding: an effect line that says no clear difference is sized as none, with no ratio read from it",
  (_null_bd["effect"] or {}).get("label") == "none"
  and evidence.evidence_line("false", _NULL_ST, ["NL1"], "Gargling mouthwash cures the infection",
                             _null_bd["effect"]["label"], breakdown.absolute_claim("Gargling mouthwash cures the infection"))
  .endswith("evidence of no clear effect"), (_null_bd["effect"], _null_bd["effect_size"]))
# But only when every sentence in the line that carries a figure is a null. A
# real weight loss beside a waist result that was not significant is an effect
# with one null beside it, in either order.
_WT_ST = [{"pmid": "WT1", "title": "A diet and weight in 5000 adults: a cohort study", "journal": "J", "year": "2020",
           "authors": ["A B"], "data_banks": None, "url": "u", "publication_types": ["Journal Article", "Observational Study"],
           "abstract": "METHODS: 5000 adults were followed for 5 years. RESULTS: Adults on the diet lost 4.1 kg more over "
                       "5 years (p < 0.001). Waist circumference changed by 0.4 cm and did not differ significantly "
                       "between groups. "
                       "CONCLUSIONS: The diet was linked to weight loss."}]
_wt_lost = "Adults on the diet lost 4.1 kg more over 5 years (Study 1)."
_wt_waist = "The change in waist size was not statistically significant (Study 1)."
def _wt(effect):
    return breakdown.ground({"parts": [], "evidence": ["Adults on the diet lost more weight (Study 1)."],
                             "effect_size": effect, "strength": "", "applies_to": "", "not_applies_to": "",
                             "unknowns": ""}, _WT_ST, verdict.tidy_prose)
_wt_a, _wt_b = _wt(f"{_wt_lost} {_wt_waist}"), _wt(f"{_wt_waist} {_wt_lost}")
t("grounding: a real effect beside a null that was not significant is never sized as none, in either order",
  not ((_wt_a or {}).get("effect") or {}).get("null") and not ((_wt_b or {}).get("effect") or {}).get("null"),
  ((_wt_a or {}).get("effect"), (_wt_b or {}).get("effect")))
_wt_c = _wt("Waist size changed by 0.4 cm, which was not statistically significant (Study 1).")
t("  and a line whose every figure is a null still is",
  ((_wt_c or {}).get("effect") or {}).get("label") == "none", (_wt_c or {}).get("effect"))
# A sentence that carries a significant figure, a p value under 0.05 or an
# interval that excludes no effect, is not a null, whatever else it says, in
# any order, joined by any word, or split in two.
_wt_null = "there was no clear difference in waist size"
_wt_sig = "adults on the diet lost 4.1 kg more over 5 years (p < 0.001)"
_wt_one = [f"{_wt_null.capitalize()}, {j} {_wt_sig} (Study 1)." for j in ("but", "while", "whereas")] + [
    f"{_wt_sig.capitalize()}, {j} {_wt_null} (Study 1)." for j in ("but", "while", "whereas")] + [
    f"Although {_wt_null}, {_wt_sig} (Study 1).", f"Although {_wt_sig}, {_wt_null} (Study 1).",
    f"{_wt_null.capitalize()} (Study 1). {_wt_sig.capitalize()} (Study 1).",
    f"{_wt_sig.capitalize()} (Study 1). {_wt_null.capitalize()} (Study 1)."]
_wt_got = [((_wt(e) or {}).get("effect") or {}).get("null") for e in _wt_one]
t("grounding: a null and a significant figure in one sentence is not 'no clear effect', in both orders and with any join",
  not any(_wt_got) and not any(breakdown.all_null(e, _WT_ST) for e in _wt_one), list(zip(_wt_got, _wt_one)))
t("  an interval that excludes no effect is a significant figure too, and a p value of 0.05 or more is not",
  not breakdown.all_null("No clear difference in waist size, but the odds ratio was 0.6 (95% CI 0.4 to 0.8) (Study 1).")
  and breakdown.all_null("There was no clear difference in waist size (p = 0.40) (Study 1).")
  and breakdown.all_null("The odds ratio was 0.9 (95% CI 0.7 to 1.2), no clear difference (Study 1)."))
with appmod.app.test_request_context("/"):
    _wt_r = verdict._read_verdict(json.dumps({
        "verdict": "complicated", "tldr": "The diet is linked to weight loss, but only in an observational study.",
        "explanation": "Adults on the diet lost 4.1 kg more (Study 1).", "still_open": "Whether it causes it.",
        "cited_study_numbers": [1], "relevance": [{"study": 1, "subject": True, "outcome": True, "in_people": True,
                                                   "finding": "for"}],
        "parts": [], "evidence": ["Adults on the diet lost more weight (Study 1)."], "effect_size": _wt_one[0],
        "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": ""}), None, [dict(x) for x in _WT_ST],
        "This diet helps weight loss", subject="(diet)", outcome="(weight loss OR body weight)", surrogate="")
    _wt_page = appmod._check_response("This diet helps weight loss", "(diet) AND (weight loss OR body weight)",
                                      _wt_r["verdict"], _wt_r["tldr"], _wt_r["explanation"], _wt_r["cited_studies"],
                                      [dict(x) for x in _WT_ST], "http://x", still_open="Whether it causes it.",
                                      breakdown=_wt_r.get("breakdown"))
t("  and on the page the evidence line never says 'no clear effect' for it",
  "no clear effect" not in (_wt_page.get("evidence_line") or ""), _wt_page.get("evidence_line"))
t("  on the way out too: a saved size is not turned to none by one null beside a real effect",
  breakdown.settle_effect({"evidence": [], "parts": [], "effect_size": f"{_wt_lost} {_wt_waist}",
                           "effect": {"label": "large", "plain": "x", "figure": "odds ratio 80"}})["effect"] is None)
# A saved answer whose size was read from a misread ratio keeps no size on the
# way out; one whose ratio the page prints keeps it.
_mis = breakdown.settle_effect({"evidence": ["Trials enrolled 35 people or 80 adults (Studies 4, 6)."],
                                "parts": [], "effect_size": "Scores dropped by 3.9 points (Study 7).",
                                "effect": {"label": "large", "plain": "about 7900% higher odds", "figure": "odds ratio 80"}})
_mis_null = breakdown.settle_effect({"evidence": [], "parts": [],
                                     "effect_size": "No drop at 30, or 60 minutes (Study 5). That difference was not "
                                                    "statistically significant (Study 5).",
                                     "effect": {"label": "none", "plain": "x", "null": True, "figure": "odds ratio 60"}})
_real = {"evidence": [], "parts": [], "effect_size": "The matched odds ratio (how much more likely an outcome was) "
                                                    "was 41.3 (Study 1).",
         "effect": {"label": "large", "plain": "about 4030% higher odds", "figure": "odds ratio 41.3"}}
t("grounding: a saved size read from a ratio the page never prints is dropped on the way out",
  _mis["effect"] is None and (_mis_null["effect"] or {}).get("label") == "none"
  and breakdown.settle_effect(_real)["effect"] == _real["effect"], (_mis["effect"], _mis_null["effect"]))
# A figure that was not significant, under an answer that there is an effect,
# is said in words that do not contradict the answer. Under an answer of no
# effect, "no clear difference" is the point and stays.
_NS_ST = [dict(_NULL_ST[0], pmid="NS1", publication_types=["Meta-Analysis"],
               abstract="METHODS: Twelve trials in 50,000 adults were pooled. RESULTS: Melanoma was less common "
                        "with daily use (hazard ratio 0.50, 95% CI 0.24 to 1.02).")]
_NS_EFF = ("The hazard ratio for melanoma was 0.50 with a likely range of 0.24 to 1.02 (Study 1). "
           "That is no clear difference between the groups, because the result was not statistically significant (Study 1).")
_NS_BD = {"parts": [], "evidence": ["Melanoma was less common with daily use (Study 1)."], "effect_size": _NS_EFF,
          "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": "",
          "effect": dict(evidence.NULL_EFFECT, figure="hazard ratio 0.50")}
with appmod.app.test_request_context("/"):
    _ns_yes = appmod._check_response("Daily sunscreen lowers melanoma risk", "(sunscreen) AND (melanoma)", "true",
                                     "Daily sunscreen lowers melanoma risk.", "Less melanoma (Study 1).", ["NS1"],
                                     [dict(_NS_ST[0])], "http://x", still_open="Long term.", breakdown=dict(_NS_BD))
    _ns_no = appmod._check_response("Daily sunscreen does not lower melanoma risk", "(sunscreen) AND (melanoma)", "true",
                                    "It does not.", "No clear change (Study 1).", ["NS1"],
                                    [dict(_NS_ST[0])], "http://x", still_open="Long term.", breakdown=dict(_NS_BD))
t("wording: 'false' on a claim that denies an effect is an answer that there is one",
  breakdown.answers_effect("false", "Vaccines do not cause autism")
  and not breakdown.answers_effect("false", "Vaccines cause autism")
  and breakdown.answers_effect("true", "Vaccines cause autism")
  and not breakdown.answers_effect("complicated", "Vaccines do not cause autism"))
t("wording: under 'Likely true' on a claim of an effect, a figure that was not significant is never 'no clear difference'",
  _ns_yes["verdict"] == "true" and "no clear difference" not in " ".join(breakdown.prose_of(_ns_yes["breakdown"]))
  and "On its own, that result could be chance, because it was not statistically significant (Study 1)."
  in _ns_yes["breakdown"]["effect_size"], (_ns_yes["verdict"], _ns_yes["breakdown"]["effect_size"]))
t("  and under an answer of no effect it still says no clear difference",
  "That is no clear difference between the groups" in _ns_no["breakdown"]["effect_size"], _ns_no["breakdown"]["effect_size"])
t("  and no field of the response carries the words the page replaced: the effect's plain words say the same",
  "no clear difference" not in json.dumps({k: v for k, v in _ns_yes.items() if k != "studies"})
  and _ns_yes["breakdown"]["effect"]["plain"] == breakdown.NULL_PLAIN_ALONE
  == "a result that could be chance, because it was not statistically significant"
  and _ns_yes["breakdown"]["effect"]["label"] == "none"
  and _ns_yes["breakdown"]["effect"]["figure"] == "hazard ratio 0.50", _ns_yes["breakdown"]["effect"])
t("  and under an answer of no effect the effect's plain words stay",
  _ns_no["breakdown"]["effect"]["plain"] == evidence.NULL_EFFECT["plain"], _ns_no["breakdown"]["effect"])
t("grounding: softening only ever weakens a claim",
  breakdown.soften("It prevents flu and cures colds.")
  == "It lowers the risk of flu and helps with colds.")
t("  and it leaves 'cure' the noun alone where nothing else would: before a bracket, 'for' or 'rates'",
  breakdown.soften("Cure rates rose (Study 1).") == "Cure rates rose (Study 1)."
  and breakdown.soften("The cure rate was 80% (Study 2).") == "The cure rate was 80% (Study 2)."
  and breakdown.soften("A cure for colds was sought (Study 1).") == "A cure for colds was sought (Study 1)."
  and breakdown.soften("Effect sizes for cold cures (Study 5) were small.")
  == "Effect sizes for cold cures (Study 5) were small.")
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
t("  the word 'or' before a number is not an odds ratio: no '7900% higher odds' from '35 people or 80 adults'",
  evidence.effect_size("Trials enrolled small groups, such as 35 people or 80 adults (Studies 4, 6).") is None
  and evidence.effect_size("exposure up to 10.4 UK units or 83 grams per week (Study 8).") is None
  and evidence.significance("in 20 or 30 (10 to 40) people", "30") is None
  and evidence.effect_size("OR 0.88 (95% CI 0.80 to 0.95)")["figure"] == "odds ratio 0.88"
  and evidence.effect_size("aOR = 1.5")["figure"] == "odds ratio 1.5"
  and evidence.effect_size("RRs of 1.2")["figure"] == "risk ratio 1.2",
  (evidence.effect_size("such as 35 people or 80 adults"), evidence.effect_size("OR 0.88")))
t("  the adjusted ratios read in capitals too: AOR, AHR and aIRR",
  (evidence.effect_size("AOR 1.45 (95% CI 1.10 to 1.90)") or {}).get("figure") == "odds ratio 1.45"
  and (evidence.effect_size("The AHR for death was 0.8") or {}).get("figure") == "hazard ratio 0.8"
  and (evidence.effect_size("aIRR 1.3") or {}).get("figure") == "incidence rate ratio 1.3"
  and evidence.ratio_kinds("AOR 1.45 and AHR 0.8") == {"odds ratio", "hazard ratio"},
  (evidence.effect_size("AOR 1.45"), evidence.effect_size("The AHR for death was 0.8")))
t("  but ARR (an absolute risk reduction) and mixed case 'Hr' are no ratio",
  evidence.effect_size("ARR 0.05") is None and evidence.effect_size("Hr 0.8") is None
  and evidence.ratio_kinds("ARR 0.05, Hr 0.8") == set())
t("  capitals rule the ratio read across a gap too: 'patients or their carers were 80' is no odds ratio",
  evidence.effect_size("Patients or their carers were 80 (Study 1).") is None
  and (evidence.effect_size("The OR for patients was 1.4 (Study 1).") or {}).get("figure") == "odds ratio 1.4")
t("  and a figure after the word 'or' still needs its unit, while one after OR needs none",
  breakdown._unit_in("80", "Trials had 35 people or 80 in all.") is None
  and breakdown._unit_in("0.88", "The OR 0.88 (95% CI 0.80 to 0.95) was small.") == "")
t("  a number followed by a unit is a measure, not a ratio: no odds ratio of 45 from 'the OR was 45 minutes'",
  evidence.effect_size("Time in the OR was 45 minutes (Study 2).") is None
  and evidence.effect_size("OR 12 patients were excluded.") is None
  and evidence.effect_size("The hazard ratio for those on 1.45 mg was 2 days shorter.") is None
  and evidence.effect_size("OR 1.45 mg") is None
  and (evidence.effect_size("OR 1.45 (95% CI 1.10 to 1.90)") or {}).get("figure") == "odds ratio 1.45"
  and (evidence.effect_size("The hazard ratio for death was 0.8.") or {}).get("figure") == "hazard ratio 0.8",
  (evidence.effect_size("Time in the OR was 45 minutes (Study 2)."), evidence.effect_size("OR 1.45 mg")))
_es = lambda x_: (evidence.effect_size(x_) or {}).get("figure")
t("  a heart rate is no hazard ratio: not per minute, not in beats, not after 'mean' or 'resting', not above the bound",
  [_es(x_) for x_ in ("Mean HR was 72 per minute (Study 1).", "HR 72 bpm", "HR was 64 beats", "HR 88/min",
                      "Resting HR was 0.9 above baseline", "Peak HR 1.4", "Maximum HR was 1.2", "heart rate (HR) 1.2",
                      "HR 72 in the control group", "OR 60")] == [None] * 10,
  [_es(x_) for x_ in ("Mean HR was 72 per minute", "HR 72 in the control group", "OR 60")])
t("  and the same care for RR as a respiratory rate and OR as an operating room",
  [_es(x_) for x_ in ("RR was 18 breaths per minute", "RR 18/min", "respiratory rate (RR) 1.5",
                      "operating room (OR) 1.4", "mean RR 1.3")] == [None] * 5)
t("  while real ratios still read: per a dose, per day of use, in a number of patients, as times, up to the bound",
  [_es(x_) for x_ in ("OR 1.3 per 10 g", "HR 0.80 in 1,200 patients", "RR 2 times", "OR 1.3 per day of use",
                      "HR 1.05 per year of age", "The matched odds ratio was 41.3", "OR 50",
                      "Mean HR was 72 per minute, and the HR for death was 0.8")]
  == ["odds ratio 1.3", "hazard ratio 0.80", "risk ratio 2", "odds ratio 1.3", "hazard ratio 1.05",
      "odds ratio 41.3", "odds ratio 50", "hazard ratio 0.8"],
  [_es(x_) for x_ in ("OR 1.3 per 10 g", "HR 0.80 in 1,200 patients", "RR 2 times",
                      "Mean HR was 72 per minute, and the HR for death was 0.8")])
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
_sur_asked = []
_real_search_pubmed = pubmed.search_pubmed
pubmed.search_pubmed = lambda q, max_results=8: _sur_asked.append((q, max_results)) or ["S1"]
try:
    _sur = pubmed.search_surrogate("(creatine) AND (hair loss)", "DHT OR dihydrotestosterone",
                                   max_results=2)
finally:
    pubmed.search_pubmed = _real_search_pubmed
t("  the surrogate search asks for the subject plus the stand-in, never the outcome",
  _sur is not None and _sur == ["S1"]
  and _sur_asked == [("(creatine) AND (DHT OR dihydrotestosterone)", 2)], _sur_asked)

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
t("  and 'link' used as a thing is left alone, never 'a small are linked to'",
  breakdown.natural("Green tea has a small link to weight loss.") == "Green tea has a small link to weight loss."
  and breakdown.natural("Coffee has links to lower risk.") == "Coffee has links to lower risk."
  and breakdown.natural("Studies found no link to cancer.") == "Studies found no link to cancer."
  and breakdown.natural("Sugary drinks link to diabetes.") == "Sugary drinks are linked to diabetes.")
t("  nor after a describing word with the verb before it: never 'shows mixed is linked to'",
  breakdown.natural("Red wine shows mixed links to heart health.") == "Red wine shows mixed links to heart health."
  and breakdown.natural("Coffee has conflicting links to cancer.") == "Coffee has conflicting links to cancer.")

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
t("  the effect size is printed once: in 'What the research says', not again as its own line",
  "How big the effect is" not in _html.split('id="server-result"')[0]
  and _html.split('id="server-result"')[0].count("was 0.88, and 0.30") == 1)
t("  and every section of the layer is there",
  all(s in _html for s in ("What the evidence shows", "Why the verdict is what it is",
                           "Who and what this applies to", "And who it does not",
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
_js_deeper = _js.split("function renderBreakdown(")[1].split("\nfunction ")[0]
_js_labels = set(re.findall(r'sec\("([^"]+)"', _js_deeper)) | set(
    re.findall(r'class="section-label label-2">([^<]+)<', _js_deeper))
t("the server and the browser render the same breakdown headings",
  _tpl_labels == _js_labels, sorted(_tpl_labels ^ _js_labels))
t("the browser's renderer runs every sentence through the same ref linking",
  "renderBreakdown(data, " in _js and "linkStudyRefs(p.assessment" in _js)
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
t("the answer is flat on the page: no panel, no shadow, no lift onto the head",
  "box-shadow" not in _sheet and "background: transparent" in _sheet and "border" not in _sheet
  and "--shadow-near" not in _css)
t("on a wide screen the sources are a narrow rail beside one reading column",
  "grid-template-columns: minmax(0, 1fr) 340px" in _css)
_done = _js[_js.find("renderResult(final.result);"):][:900]
t("after a check the address carries the claim as checked, so a fixed spelling stays fixed on reload",
  'encodeURIComponent(final.result.claim || claim)' in _done
  and "encodeURIComponent(claim))" not in _done)
_ord = c.get("/?q=Vitamin+D+supplements+in+winter+cut+colds").data.decode()


def _at(needle):
    return _ord.index(needle)


t("the result reads claim, answer, assessment, research, still open, breakdown, evidence, then sources",
  _at("The claim you checked") < _at("The short answer") < _at('id="tldr"') < _at('id="stamp"')
  < _at("What the research says") < _at(">Still open<") < _at("Read the full breakdown")
  < _at('id="chart-sec"') < _at("Studies checked") < _at('id="share-btn"') < _at('id="report-open"'))
t("  the claim carries its label and is never cut short",
  "The claim you checked" in _ord
  and "Vitamin D supplements in winter cut colds</h1>" in _ord)
t("  the claim is the result view's h1 and the error title the error view's; the home h1 sits in the hidden ask section",
  '<h1 class="claim-echo" id="claim-echo">' in _ord and '<h1 class="error-title" id="error-title">' in _ord
  and '<section id="ask" aria-labelledby="ask-heading" hidden>' in _ord and "<h2 class=\"claim-echo\"" not in _ord)
t("  its quotation marks come from the stylesheet, so the text stays the text",
  ".claim-echo::before" in _css and "\\201C" in _css)
# Sizes are compared rather than eyeballed.
_claim_px = [float(n) for n in re.findall(
    r"\.claim-echo \{[^}]*?clamp\((\d+(?:\.\d+)?)px,[^,]+,\s*(\d+(?:\.\d+)?)px\)",
    _css, re.S)[0]]
# The answer sentence is the biggest text on the result, at every width: its
# smallest is above the claim's largest and the assessment's largest.
_tldr_px = [float(n) for n in re.findall(
    r"\.tldr \{[^}]*?clamp\((\d+(?:\.\d+)?)px,[^,]+,\s*(\d+(?:\.\d+)?)px\)", _css, re.S)[0]]
_stamp_px = float(re.search(r"\.stamp \{[^}]*?font-size: (\d+(?:\.\d+)?)px", _css, re.S).group(1))
t("  and the answer sentence is set larger than the claim and the assessment at every width",
  _claim_px[1] < _tldr_px[0] and _stamp_px < _tldr_px[0], f"claim {_claim_px} tldr {_tldr_px} stamp {_stamp_px}")
t("  the assessment is plain text: no oval, no ring, no pen drawn round it on the result",
  'class="ring"' not in _ord and 'class="vwrap"' not in _ord and ".ring" not in _css
  and ".vwrap" not in _css and "fitRing" not in (ROOT / "static" / "app.js").read_text())
t("  the assessment and its confidence are two labelled rows under the answer",
  '<span class="assess-label">Assessment</span><span class="stamp" id="stamp">' in _ord
  and '<span class="assess-label">Confidence</span>' in _ord
  and _at('id="tldr"') < _at('id="assess"') < _at('id="doctor-line"') < _at("What the research says"))
t("  the confidence is the sentence the check worded, once",
  "<mark>Low certainty evidence, so no plain yes or no.</mark>" in _ord.split('id="server-result"')[0]
  or "<mark>" in _ord.split('id="assess"')[1].split("</p>")[1], _ord.split('id="assess"')[1][:500])

# ---- no sentence is printed twice -----------------------------------------------
# "What the research says" is picked from the paragraphs the breakdown prints,
# so the same effect-size and who-it-applies-to sentences used to appear in both.
import display as _display  # noqa: E402
_DUP = {"claim": "Vitamin D prevents colds", "explanation": "Colds fell a little (Study 1).",
        "still_open": "Whether it holds for older adults.",
        "breakdown": {
            "says": ["Colds fell a little (Study 1).",
                     "The odds ratio for everyone was 0.88 (Study 1).",
                     "That is about 12% lower odds, a small effect (Study 1).",
                     "Who was studied: adults aged 18 to 67 (Study 2)."],
            "parts": [{"part": "Vitamin D prevents colds", "assessment": "Colds fell a little (Study 1)."}],
            "evidence": ["Colds fell a little (Study 1). Fewer people caught a cold (Study 2)."],
            "effect_size": "The odds ratio for everyone was 0.88 (Study 1). "
                           "That is about 12% lower odds, a small effect (Study 1).",
            "strength": "Pooled trials agree (Study 1).",
            "applies_to": "Adults aged 18 to 67 (Study 2).",
            "not_applies_to": "Children (Study 2).", "unknowns": "Whether it holds for older adults.",
            "concluded": [], "populations": []}}
_bv = _display.breakdown_view(_DUP)
t("the breakdown view drops what 'What the research says' already printed",
  _bv["effect_size"] == "" and _bv["parts"] == [] and _bv["evidence"] == ["Fewer people caught a cold (Study 2)."]
  and _bv["unknowns"] == "" and _bv["strength"] == "Pooled trials agree (Study 1).", _bv)
t("  the who-line is not said twice: the who block keeps the sentence, the research block does not",
  not any(ln.startswith("Who was studied") for ln in _bv["says"]) and _bv["applies_to"] == "Adults aged 18 to 67 (Study 2).")
t("  the effect-size pair, shown once in the research block, is not printed again as 'How big the effect is'",
  _bv["effect_size"] == "" and any("0.88" in ln for ln in _bv["says"]))
t("  a record with no breakdown has no view", _display.breakdown_view({"claim": "x"}) is None)

import html as _htmllib  # noqa: E402


def _printed(page):
    """Each sentence the reading column and the breakdown print, quotes of the authors left out."""
    body = page.split('id="says"', 1)[1].split('class="evidence"', 1)[0]
    body = body.split('<p class="fine">')[0] + (body.split('</details>', 1)[1] if "</details>" in body else "")
    paras = _re.findall(r'<p class="(?:says-p|deeper-p|part-take|effect)[^"]*"[^>]*>(.*?)</p>|<span>(.*?)</span></p>', body, _re.S)
    text = " ".join(a or b for a, b in paras)
    text = _htmllib.unescape(_re.sub(r"<[^>]+>", "", text))
    return [s.strip() for s in _display.split_sentences(text)]


_sents = _printed(_ord)
_keys = [_display._key(x) for x in _sents]
t(f"the result page prints no sentence twice ({len(_sents)} sentences)",
  len(_sents) >= 4 and len(_keys) == len(set(_keys)),
  [x for x in _sents if _keys.count(_display._key(x)) > 1])
t("  the effect-size sentence is on the page once, in the research block, and there is no second 'How big the effect is'",
  _ord.split('id="server-result"')[0].count("The odds ratio was 0.88, and 0.30 in the lowest") == 1
  and "How big the effect is" not in _ord.split('id="server-result"')[0])

import shutil as _shutil, subprocess as _subprocess  # noqa: E402
if _shutil.which("node"):
    _hsrc = (ROOT / "static" / "app.js").read_text().split("// ---- What the page prints from the breakdown, each sentence once")[1].split("// (d) on the result screen.")[0]
    _hsrc = _hsrc.split("\n", 1)[1]
    _recs = [_DUP, dict(_DUP, breakdown=dict(_DUP["breakdown"], says=[], evidence=[])),
             {"claim": "x", "explanation": "e", "breakdown": None}]
    _prog = _hsrc + "console.log(JSON.stringify(" + json.dumps(_recs) + ".map(breakdownView)));"
    _js_views = json.loads(_subprocess.run(["node", "-e", _prog], capture_output=True, text=True, check=True).stdout)
    _py_views = [_display.breakdown_view(r) for r in _recs]
    t("the script and the server pick the same sentences to print, on three records",
      _js_views == _py_views, (_js_views, _py_views))

# ---- the result page has no picture ---------------------------------------------
_res_head = _ord.split('id="server-result"')[0]
_appjs = (ROOT / "static" / "app.js").read_text()
_css = (ROOT / "static" / "style.css").read_text()
t("a result page carries no footage, and the checking screen has no banner: no band element, no clips, no film code",
  'class="film-shot"' not in _res_head and "<img" not in _res_head and "verdict.webp" not in _res_head
  and 'id="film"' not in _res_head and "film." not in _appjs and "filmBand" not in _appjs
  and ".film" not in _css and "/footage/" not in _appjs)
t("  checking reserves no tall block: the head is as tall as its content, so the claim stays where the verdict will put it",
  "#result.checking .report-head { min-height" not in _css)
_sheet = _tpl.split('id="study-sheet"')[1].split("</dialog>")[0]
t("the source dialog has one Close, in a bar that sticks to its top; the foot has none",
  _sheet.count("data-close") == 1 and 'class="sheet-bar"' in _sheet
  and _sheet.index("data-close") < _sheet.index('class="sheet-body"')
  and "position: sticky" in _css.split(".sheet-bar {")[1].split("}")[0])
t("  that Close is at least 44px tall and wide, and has a word, not just an icon",
  "min-height: 44px" in _css.split(".sheet-close {")[1].split("}")[0]
  and "min-width: 44px" in _css.split(".sheet-close {")[1].split("}")[0]
  and ">Close</button>" in _sheet)
t("  focus moves into the dialog on open, Escape closes it natively, and the opener gets focus back",
  "heading.focus(" in _appjs.split("function openSheet")[1].split("\n}")[0]
  and "showModal()" in _appjs and "cancel" not in _appjs.split("function openSheet")[1].split("\n}")[0])
_bar = _appjs.split("function armShareBar()")[1].split("\n}\n")[0]
t("the phone's share bar is shown only once the short answer has scrolled above the top edge, never on first view",
  'querySelector("#result .answer")' in _bar and "e.boundingClientRect.bottom <= 0" in _bar
  and "!e.isIntersecting" in _bar and '$("bar-sticky").hidden = false' not in _appjs
  and 'classList.add("has-bar")' not in _appjs)
t("  and it is put away with the result: leaving the result or starting a check disarms it",
  "if (section !== resultSection) disarmShareBar()" in _appjs and _appjs.count("disarmShareBar();") >= 2
  and "shareBarWatch.disconnect()" in _appjs)
t("the service worker no longer precaches the still",
  "verdict.webp" not in (ROOT / "static" / "sw.js").read_text() and not hasattr(appmod, "BAND_STILL"))

# ---- the confidence line: the same sentence from the server and the script -------
_CL = [{"studies": [], "evidence_line": "x"},
       {"studies": [{"cited_in_verdict": False, "tier": "strong"}], "evidence_line": "x"},
       {"studies": [{"cited_in_verdict": True, "tier": "strong"}], "evidence_line": "Moderate evidence of a small effect, not a cure"},
       {"studies": [{"cited_in_verdict": True, "tier": "strong"}, {"cited_in_verdict": True, "tier": "weak"}]},
       {"studies": [{"cited_in_verdict": True, "tier": "moderate"}]},
       {"studies": [{"cited_in_verdict": True, "tier": "weak"}]}]
_cl = [_display.confidence_line(r) for r in _CL]
t("the confidence line: nothing found, nothing used, the checked wording, or the designs of an old record",
  _cl == ["No direct evidence either way: nothing on PubMed matched well enough to weigh.",
          "No direct evidence either way: none of the studies tests this claim.",
          "Moderate evidence of a small effect, not a cure.",
          "Strong study designs.", "Moderate study designs.", "Weak study designs."], _cl)
if _shutil.which("node"):
    _csrc = "function confidenceLine" + (ROOT / "static" / "app.js").read_text().split("function confidenceLine")[1].split("\n}\n")[0] + "\n}"
    _jcl = json.loads(_subprocess.run(["node", "-e", _csrc + "console.log(JSON.stringify(" + json.dumps(_CL) + ".map(confidenceLine)));"],
                                      capture_output=True, text=True, check=True).stdout)
    t("  and the script words every one of them the same way", _jcl == _cl, _jcl)

# ---- edit the claim --------------------------------------------------------------
_edit_js = (ROOT / "static" / "app.js").read_text().split('$("edit-claim").addEventListener("click", () => {')[1].split("\n});")[0]
t("the result offers 'Edit claim' right under the claim, before anything else on the sheet",
  _at("The claim you checked") < _at('id="edit-claim"') < _at("The short answer")
  and '>Edit claim</button>' in _ord and 'id="claim-echo"' in _ord)
t("  it goes back to the claim box with the claim filled, cursor at the end, suggestions shut",
  "goHome()" in _edit_js and "input.value = claim" in _edit_js and "currentResult.claim" in _edit_js
  and "setSelectionRange(claim.length, claim.length)" in _edit_js and "closeSuggest()" in _edit_js
  and "check(" not in _edit_js)
t("  it is a 44px target and is not offered while a check runs",
  "min-height: 44px" in _css.split(".edit-claim {")[1].split("}")[0] and "#result.checking .edit-claim { display: none; }" in _css)

# ---- who and what it applies to, then the limitation -----------------------------
_deeper_html = _ord.split('<details class="deeper"')[1].split("</details>")[0]
t("who and what the answer applies to is its own block, between the research and what is still open",
  _at("What the research says") < _at("Who and what this applies to") < _at(">Still open<")
  and "Adults aged 18 to 67" in _ord.split(">Still open<")[0].split("Who and what this applies to")[1])
t("  it leaves the breakdown, so the breakdown does not say it a second time",
  "Who and what this applies to" not in _deeper_html and "And who it does not" not in _deeper_html
  and "Adults aged 18 to 67" not in _deeper_html)
t("  the main limitation is still the still_open field, and still printed",
  _re.search(r'<p class="still-open" id="still-open">.*?<span id="still-open-text">[^<]+</span>', _ord, _re.S) is not None)
t("  the script fills the same block from the same view",
  'function renderWho(data)' in (ROOT / "static" / "app.js").read_text()
  and "renderWho(data);" in (ROOT / "static" / "app.js").read_text())

# ---- sources say why they are listed ---------------------------------------------
_SW = [{"cited_in_verdict": True, "publication_types": ["Journal Article", "Meta-Analysis"]},
       {"cited_in_verdict": True, "publication_types": ["Randomized Controlled Trial"], "population": "children aged 2 to 5"},
       {"cited_in_verdict": True, "publication_types": ["Journal Article"]},
       {"cited_in_verdict": False, "off_topic": "tests something other than what this claim is about.", "publication_types": ["Review"]},
       {"cited_in_verdict": False, "background": True, "background_why": "animal or lab", "publication_types": []},
       {"cited_in_verdict": False, "background": True, "publication_types": ["Editorial"]},
       {"cited_in_verdict": False, "publication_types": ["Observational Study"]}]
_sw = [_display.study_why(x) for x in _SW]
t("a source says why it is listed: relied on with what its design does and where it stops, or why it was set aside",
  _sw[0].startswith("Relied on for the verdict. Combines the results of several studies")
  and "only as sound as the studies it pools" in _sw[0]
  and _sw[1].endswith("Studied in: children aged 2 to 5.")
  and _sw[2] == "Relied on for the verdict."
  and _sw[3] == "Read and set aside: tests something other than what this claim is about."
  and _sw[4] == "Background only, not counted: animal or lab."
  and _sw[5] == "Background only, not counted."
  and _sw[6] == "Read, not relied on.", _sw)
t("  the table of designs is the same in the script and on the server",
  _re.findall(r'\["([^"]+)", "([a-z]*)", "((?:[^"\\]|\\.)*)"\]',
              (ROOT / "static" / "app.js").read_text().split("const TYPES = [")[1].split("\n];")[0])
  == [tuple(x) for x in _display.DESIGN_NOTES])
t("  every source on the result page carries its line",
  _ord.split('id="server-result"')[0].count('<li class="study ') == _ord.split('id="server-result"')[0].count('<p class="why">')
  and _ord.count('<p class="why">') >= 1)
if _shutil.which("node"):
    _tsrc = (ROOT / "static" / "app.js").read_text()
    _tsrc = ("const TYPES = [" + _tsrc.split("const TYPES = [")[1].split("function escapeHtml")[0])
    _jsw = json.loads(_subprocess.run(["node", "-e", _tsrc + "console.log(JSON.stringify(" + json.dumps(_SW) + ".map(studyWhy)));"],
                                      capture_output=True, text=True, check=True).stdout)
    t("  and the script writes the same line for every kind of source", _jsw == _sw, _jsw)

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
  and _S(["Retracted Publication", "Meta-Analysis"]) == "Retracted")
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
  'aria-label="Meta-analysis 01, 2017, used for this verdict. '
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
  and "data-verdict" not in _css)
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

# The fly-through is out of the home page (its files are in _old/, with its
# test). /flight still redirects; the trust section's real example is read
# from the saved answers (see the trust tests below).

# ---- the checker comes first (queue 7, step 2) -----------------------------
_home = c.get("/").get_data(as_text=True)
_hi = _home.index('id="claim-input"')
t("home: the field has a visible label and a helper line, not just a placeholder",
  '<label class="field-title" for="claim-input">Health claim to check</label>' in _home
  and "Enter one claim. Add the group or situation if it matters." in _home
  and 'aria-describedby="claim-help suggest-hint"' in _home)
t("  the nav is How it works, About, Privacy, and the page loads no footage",
  all(x in _home for x in ('id="how-btn"', 'id="about-link"', 'href="/privacy">Privacy</a>'))
  and "flight" not in _home.replace("inFlight", "") and 'id="flight"' not in _home)
t("  the examples sit in the form, after the submit button; the latest checks come after the form",
  _home.index('id="submit-btn"') < _home.index('id="chips"') < _home.index("</form>")
  < _home.index('class="latest"'))
t("  the first three examples are the ones app.js renders",
  "EXAMPLES.slice(0, 3)" in _js)
_pl = _re.search(r'<p class="privacy-line"[^>]*>(.*?)</p>', _home, _re.S)
_pl_text = __import__("html").unescape(_re.sub(r"<[^>]+>", "", _pl.group(1))) if _pl else ""
_dbsrc = (ROOT / "db.py").read_text()
t("home: the privacy line sits right after the submit button and says what the code does",
  _pl and _home.index('id="submit-btn"') < _home.index('id="privacy-line"') < _home.index('id="status"')
  and "Don't include names or personal medical details." in _pl_text
  and "Claim text is stored and sent to our AI provider." in _pl_text
  and 'href="/privacy"' in _pl.group(1), _pl_text)
t("  it is true: the claim text is a stored column and is sent to the provider, with no dashes in the line",
  "claim_text TEXT NOT NULL" in _dbsrc and "def log_check(claim_text" in _dbsrc
  and "\u2014" not in _pl_text and "\u2013" not in _pl_text)
t("  /flight still redirects and its frames are gone from static/",
  c.get("/flight").status_code == 301 and not (ROOT / "static" / "flight.js").exists()
  and not (ROOT / "static" / "flight.css").exists())
t("  the service worker no longer precaches the intro's files",
  "flight" not in (ROOT / "static" / "sw.js").read_text().split("const SHELL")[1].split("];")[0])

_sw_src = (ROOT / "static" / "sw.js").read_text()
t("  the service worker never caches or serves /stats or /api/ (a tokened address must not become the offline shell)",
  '"/stats"' in _sw_src and _sw_src.index('"/stats"') < _sw_src.index('req.mode === "navigate"')
  and '"/api/"' in _sw_src.split('req.mode === "navigate"')[0])

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
# The retry the code already has: one with an empty line is not an answer,
# and one with both lines beats a first answer that left one empty.
fg.models.calls = 0
fg.models.script = [_SUGAR_NO, DIR("no"),
                    Resp(VJ("false", [1, 2], tldr=_NO_TLDR, still_open="",
                            evidence=["Sugar did not change behaviour (Study 1)."]))]
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("a retry that leaves its still-open line empty is not taken; the first answer, with its line, stands",
  _v["still_open"] == "Longer trials are needed." and _v["verdict"] == "false", _v)
fg.models.calls = 0
fg.models.script = [Resp(VJ("complicated", [1, 2], tldr=_NO_TLDR, still_open="",
                            evidence=["Sugar did not change behaviour (Study 1)."])), DIR("no"), _SUGAR_NO]
_v = verdict.weigh_evidence("Sugar makes children hyperactive", _SUGAR, "(sugar) AND (hyperactivity)")
t("  and a retry with both lines replaces a first answer that left one empty, even with as many faults",
  _v["still_open"] == "Longer trials are needed.", _v)
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
# A narrative review is never the strength, but its caveats count as any
# paper's do: only one with no caveat is in neither list.
_G3 = {
    "trial, caveats": dict(title="Turmeric in 20 adults: a pilot study", types=["Clinical Trial"],
                           abstract="A pilot study of 20 adults, uncontrolled. Pain fell."),
    "trial, none": dict(title="Turmeric and joint pain: a randomized trial", types=["Randomized Controlled Trial"],
                        abstract="We randomised 400 adults to turmeric or placebo. Pain fell more with turmeric."),
    "narrative, caveats": dict(title="Turmeric for joint health: a narrative review", types=["Review"],
                               abstract="This review summarises a pilot study of 12 patients without a control "
                                        "group and industry-funded reports. Turmeric may ease joint pain."),
    "narrative, none": dict(title="Turmeric and joints: a review", types=["Review"],
                            abstract="We review what is known about turmeric and joint pain."),
    "unlabelled": dict(title="Turmeric and joints", types=["Journal Article"], abstract="Turmeric and joints."),
}


def _g3(kind, n=0):
    k = _G3[kind]
    return _study(f"G{list(_G3).index(kind)}{n}", k["title"], types=k["types"], abstract=k["abstract"])


def _cap_231c774(studies, cited):
    """certainty_cap's trial loop as it stood at 231c774, the reference: no
    narrative review rule (none of these papers is a pooled review)."""
    pool = [s for s in studies if s["pmid"] in set(cited)]
    solid, limited = [], []
    for s in pool:
        labelled = any(not t.startswith(evidence.IGNORE_PREFIXES) for t in s.get("publication_types") or [])
        if evidence.trial_caveats(s):
            limited.append(s)
        elif evidence.sample_size(s) is not None or labelled:
            solid.append(s)
    return None if solid or not limited else "capped"


t("G3: a 'true' resting only on a narrative review with caveats is capped again, and its line says low certainty",
  evidence.trial_caveats(_g3("narrative, caveats")) and evidence.narrative_review(_g3("narrative, caveats"))
  and evidence.certainty_cap([_g3("narrative, caveats")], ["G20"]) is not None
  and evidence.line_certainty(evidence.evidence_line("true", [_g3("narrative, caveats")], ["G20"],
                                                     "Turmeric eases joint pain")) == "low",
  evidence.evidence_line("true", [_g3("narrative, caveats")], ["G20"], "Turmeric eases joint pain"))
t("  and a trial with caveats beside narrative reviews with none is still capped (L2)",
  evidence.certainty_cap([_g3("trial, caveats"), _g3("narrative, none", 1), _g3("narrative, none", 2)],
                         ["G00", "G31", "G32"]) is not None
  and evidence.certainty_cap([_g3("narrative, none")], ["G30"]) is None)
import itertools as _it  # noqa: E402
_g3_looser = []
for _size in (1, 2, 3):
    for _combo in _it.combinations_with_replacement(list(_G3), _size):
        _ps = [_g3(k, i) for i, k in enumerate(_combo)]
        _ids = [p_["pmid"] for p_ in _ps]
        if _cap_231c774(_ps, _ids) and not evidence.certainty_cap(_ps, _ids):
            _g3_looser.append(_combo)
t("  property: for every set of up to three of these papers, the cap is never looser than at 231c774",
  not _g3_looser, _g3_looser)
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
# A study that never measured the claim's outcome is not evidence for it, even
# when its background names that outcome. The case: a hair trial whose
# background says creatine "enhances muscle strength", read as a test of
# strength. Only a structured abstract can be told apart this way.
_HAIR = json.loads((ROOT / "tests" / "off_outcome_hair_trial.json").read_text())
_hair_out = _HAIR["outcome"]
_hair = dict(_HAIR["study"])
t("off outcome: a structured abstract that names the outcome only in its background did not measure it",
  evidence.measured_outcome(_hair, _hair_out) is False)
_RES_ONLY = _study("MO1", "Creatine supplementation in older adults: a randomised trial",
                   abstract="BACKGROUND: Ageing brings frailty.\nMETHODS: Sixty adults took creatine or placebo.\n"
                            "RESULTS: One repetition maximum rose by 8 kg with creatine.\nCONCLUSION: Creatine helped.")
t("  one that names the outcome only in its results did measure it",
  evidence.measured_outcome(_RES_ONLY, _hair_out) is True)
t("  one that names only the declared stand-in in its results is left for the stand-in label",
  evidence.measured_outcome(_study("MO2", "Creatine and hormones", abstract="BACKGROUND: Strength matters.\n"
                                   "METHODS: Men took creatine.\nRESULTS: Lean mass rose.\nCONCLUSION: Fine."),
                            _hair_out, "(lean mass)") is True)
_UNSTRUCT = _study("MO3", "Creatine and hair", abstract="Creatine is known to raise muscle strength. "
                   "We tested its effect on hair density in 40 men and found none.")
t("  an abstract with no headings, or none at all, is left as it is",
  evidence.measured_outcome(_UNSTRUCT, _hair_out) is None
  and evidence.measured_outcome(_study("MO4", "Creatine and hair", abstract=""), _hair_out) is None
  and evidence.measured_outcome(_study("MO5", "Creatine and hair", abstract="(No abstract available)"), _hair_out) is None
  and evidence.measured_outcome(_hair, "") is None)
# Three readings found by measuring the rule on saved answers, each a study
# that did measure the outcome and must stay in.
t("off outcome: an outcome named in general words too ('health', 'complications') is never ruled on",
  evidence.measured_outcome(_study("MO6", "Acute effects of coffee", abstract="BACKGROUND: Effects are uncertain.\n"
                                   "METHODS: 100 adults wore monitors.\nRESULTS: More ectopic beats.\nCONCLUSIONS: Some."),
                            "(health OR disease risk OR mortality OR health effects)") is None
  and evidence.measured_outcome(_hair, f"({_hair_out}) OR (complications)") is None)
t("  headings that are only a label and a registration are no headings",
  evidence.measured_outcome(_study("MO7", "A sit-stand desk trial", abstract="UNLABELLED: Desks reduced regional "
                                   "musculoskeletal discomfort.\nTRIAL REGISTRATION: OSF."),
                            "(back pain OR low back pain)") is None)
t("  a background sentence that states the study's aim counts as what it set out to measure",
  evidence.measured_outcome(_study("MO8", "Efficacy of a vaccine in males", abstract="BACKGROUND: Infection is common. "
                                   "We report on the safety of the vaccine and its efficacy.\nMETHODS: 4000 men.\n"
                                   "RESULTS: Fewer lesions; more injection-site pain.\nCONCLUSIONS: It prevents lesions."),
                            "(safety OR adverse events OR side effects)") is True
  and evidence.measured_outcome(_hair, _hair_out) is False)
t("off outcome: hyperactivity rated as behaviour, and stress rated as tension, were measured",
  evidence.measured_outcome(_study("MO9", "Diets high in sucrose and the behavior of children",
                                   abstract="BACKGROUND: Sugar is said to cause hyperactivity.\nMETHODS: 48 children, three "
                                            "diets.\nRESULTS: No differences in 39 behavioral measures.\nCONCLUSIONS: None."),
                            "(hyperactivity OR hyperactive OR ADHD)") is True
  and evidence.measured_outcome(_study("MO10", "Ashwagandha, cognition and mood",
                                       abstract="BACKGROUND: It is said to reduce stress.\nMETHODS: 59 adults, POMS.\n"
                                                "RESULTS: Less tension and fatigue.\nCONCLUSION: Some benefit."),
                                "(stress OR psychological stress OR cortisol)") is True)
# A missing word proves nothing: a study reports its outcome by an instrument
# (1RM, A1C, PSQI), an abbreviation the background defined, or other words
# ("died", "flu"). An independent grader wrote 54 such abstracts of studies
# that did measure the outcome. The rule sets a study aside only on a positive
# sign it is about another outcome. One case it still cannot read without the
# claim's subject: "Sleep loss and word learning", for a claim about memory,
# names sleep in its title and conclusions as the exposure.
_ATTACKS = json.loads((ROOT / "tests" / "off_outcome_attacks.json").read_text())["cases"]
_attacked = [a_["name"] for a_ in _ATTACKS if evidence.measured_outcome(a_["study"], a_["outcome"]) is False]
t("off outcome: of 54 abstracts that did measure the outcome in other words, only one is set aside",
  len(_ATTACKS) == 54 and _attacked == ["recall"], _attacked)
_recall = next(a_ for a_ in _ATTACKS if a_["name"] == "recall")
t("  and that one stays in once the claim's subject (sleep) is known, as it is in an answer",
  evidence.measured_outcome(_recall["study"], _recall["outcome"], "", "(sleep deprivation OR sleep loss)") is None)
t("  a background that never names the outcome sets nothing aside, whatever the title names",
  evidence.measured_outcome(_study("MO11", "Creatine and hair density: a randomized trial",
                                   abstract="BACKGROUND: Creatine is popular.\nMETHODS: 40 men.\n"
                                            "RESULTS: Hair density did not change.\nCONCLUSIONS: No hair loss."),
                            _hair_out) is None)
t("  a title naming another outcome is not enough when the findings never name it",
  evidence.measured_outcome(_study("MO12", "Creatine and hair: a randomized trial",
                                   abstract="BACKGROUND: Creatine raises muscle strength.\nMETHODS: 40 men.\n"
                                            "RESULTS: Leg press load rose by 12 kg.\nCONCLUSIONS: It helped."),
                            _hair_out) is None)
t("  the hair trial is still set aside: its background names strength, its title and results name hair",
  evidence.measured_outcome(_hair, _hair_out) is False)
t("  text before the first heading is no structure to read, so nothing is set aside on it",
  evidence.measured_outcome(_study("MO13", "Creatine and hair: a randomized trial",
                                   abstract="Muscle strength was measured by 1RM in every participant. "
                                            "BACKGROUND: Creatine raises muscle strength.\nMETHODS: 40 men.\n"
                                            "RESULTS: Hair density did not change.\nCONCLUSIONS: Hair was unchanged."),
                            _hair_out) is None)
# Setting a study aside removes its citation and nothing else. Two trials
# disagree and the answer is "complicated"; code sets one aside; the page must
# not then say "Randomized trials say yes". The mirror: the one set aside is
# the one that agreed.
def _flip_st(pmid, title, abstract, types):
    return _study(pmid, title, abstract=abstract, types=types)
_FL_FOR = _flip_st("FL1", "Creatine and muscle strength in 240 older adults: a randomized controlled trial",
                   "BACKGROUND: Ageing lowers strength.\nMETHODS: 240 adults were randomized to creatine or placebo.\n"
                   "RESULTS: Muscle strength rose more with creatine (mean difference 8 kg, p < 0.001).\n"
                   "CONCLUSIONS: Creatine raised strength.", ["Randomized Controlled Trial"])
_FL_AGAINST = _flip_st("FL2", "Creatine and muscle strength in 260 older women: a randomized controlled trial",
                       "BACKGROUND: Creatine is said to help.\nMETHODS: 260 women were randomized to creatine or placebo.\n"
                       "RESULTS: Leg press strength did not differ between groups (p = 0.6).\n"
                       "CONCLUSIONS: Creatine gave no strength benefit.", ["Randomized Controlled Trial"])
_FL_HAIR = _flip_st("FL3", "Creatine and hair in 250 older women: a randomized controlled trial",
                    "BACKGROUND: Creatine is said to raise muscle strength in older adults.\n"
                    "METHODS: 250 women were randomized to creatine or placebo.\n"
                    "RESULTS: Hair density did not differ between groups (p = 0.6).\nCONCLUSIONS: Hair was unchanged.",
                    ["Randomized Controlled Trial"])
_FL_SURVEY = _flip_st("FL4", "Creatine use and muscle strength in 5,000 gym members: a cross-sectional study",
                      "BACKGROUND: Many take creatine.\nMETHODS: A survey of 5,000 gym members.\n"
                      "RESULTS: Creatine users were {} than non-users.\nCONCLUSIONS: {}.",
                      ["Journal Article", "Observational Study"])
def _flip(trials_findings, survey_finding, survey_words):
    survey = dict(_FL_SURVEY, abstract=_FL_SURVEY["abstract"].format(*survey_words))
    sts = [dict(s_) for s_, _ in trials_findings] + [survey]
    finds = [f_ for _, f_ in trials_findings] + [survey_finding]
    parsed = {"verdict": "complicated", "tldr": "Trials disagree on whether creatine raises strength in older adults.",
              "explanation": "One trial found more strength with creatine (Study 1). Another found none (Study 2).",
              "still_open": "Which doses help, and for whom.", "cited_study_numbers": list(range(1, len(sts) + 1)),
              "relevance": [{"study": i + 1, "subject": True, "outcome": True, "in_people": True, "finding": f_}
                            for i, f_ in enumerate(finds)],
              "parts": [], "evidence": [], "effect_size": "", "strength": "", "applies_to": "",
              "not_applies_to": "", "unknowns": ""}
    return sts, verdict._read_verdict(json.dumps(parsed), None, sts, "Creatine increases muscle strength in older adults",
                                      subject="(creatine)", outcome="(muscle strength OR strength)", surrogate="")
_fs, _fr = _flip([(_FL_FOR, "for"), (_FL_HAIR, "against")], "against", ("no stronger", "No association"))
t("set aside: a trial code sets aside that disagreed still disagrees, so the trials settle nothing",
  "FL3" not in _fr["cited_studies"] and _fr["off_topic"].get("FL3") == verdict.OFF_OUTCOME
  and _fr["verdict"] == "complicated" and "Randomized trials say yes" not in _fr["tldr"], (_fr["verdict"], _fr["tldr"]))
_fs, _fr = _flip([(_FL_HAIR, "for"), (_FL_AGAINST, "against")], "for", ("stronger", "Users were stronger"))
t("  and the mirror: the one set aside agreed, and the answer still does not become 'trials say no'",
  "FL3" not in _fr["cited_studies"] and _fr["verdict"] == "complicated"
  and "Randomized trials say no" not in _fr["tldr"], (_fr["verdict"], _fr["tldr"]))
# The harm guard with a study set aside: it is worked out on the studies as
# they stand and with the set-aside ones put back, and the set-aside study
# counts exactly as it would have in view, never more.
def _hg_st(pmid, title, results, types, background="Flu vaccines are given to adults each winter."):
    return _study(pmid, title, types=types,
                  abstract=f"BACKGROUND: {background}\nMETHODS: Adults took part over two winters.\n"
                           f"RESULTS: {results}\nCONCLUSIONS: See results.")
_HG_FOR = _hg_st("HG1", "Flu vaccine and hospital admissions in 3,000 adults: a randomized controlled trial",
                 "Hospital admissions fell with the vaccine (p < 0.01).", ["Randomized Controlled Trial"])
_HG_HAIR_WEAK = _hg_st("HG2", "Flu vaccine and hair loss in 800 adults: a cohort study",
                       "Hair loss did not differ between groups.", ["Observational Study"],
                       background="Flu vaccines are said to reduce hospital admissions.")
_HG_HAIR_RCT = _hg_st("HG3", "Flu vaccine and hair loss in 2,000 adults: a randomized controlled trial",
                      "Hair loss did not differ between groups (p = 0.6).", ["Randomized Controlled Trial"],
                      background="Flu vaccines are said to reduce hospital admissions.")
def _hg(claim, outcome, model_verdict, studies_findings, cited, subject="(flu vaccine OR influenza vaccine)"):
    sts = [dict(s_) for s_, _ in studies_findings]
    parsed = {"verdict": model_verdict, "tldr": "The studies here lean one way.",
              "explanation": "The trials were read (Study 1).", "still_open": "Who it helps most.",
              "cited_study_numbers": cited,
              "relevance": [{"study": i + 1, "subject": True, "outcome": True, "in_people": True, "finding": f_}
                            for i, (_, f_) in enumerate(studies_findings)],
              "parts": [], "evidence": [], "effect_size": "", "strength": "", "applies_to": "",
              "not_applies_to": "", "unknowns": ""}
    return verdict._read_verdict(json.dumps(parsed), None, sts, claim, subject=subject,
                                 outcome=outcome, surrogate="")
_HG_C = "Flu vaccines reduce hospital admissions"
_HG_O = "(hospital admissions)"
_hr = _hg(_HG_C, _HG_O, "false", [(_HG_FOR, "for"), (_HG_HAIR_WEAK, "against")], [1, 2])
t("harm guard, set aside: strong trials point the safe way and a weak set-aside study disagrees, so the safe answer stands",
  _hr["off_topic"].get("HG2") == verdict.OFF_OUTCOME and _hr["verdict"] == "true" and "HG1" in _hr["cited_studies"],
  (_hr["verdict"], _hr["tldr"], _hr["off_topic"]))
_hr = _hg(_HG_C, _HG_O, "false", [(_HG_FOR, "for"), (_HG_HAIR_WEAK, "against")], [1])
t("  and the same with the set-aside study never cited",
  _hr["off_topic"].get("HG2") == verdict.OFF_OUTCOME and _hr["verdict"] == "true", (_hr["verdict"], _hr["tldr"]))
_hr = _hg(_HG_C, _HG_O, "false", [(_HG_FOR, "for"), (_HG_HAIR_RCT, "against")], [1, 2])
t("  a strong, cited set-aside trial that backs the risky answer stops it at 'complicated', with the risk said",
  _hr["off_topic"].get("HG3") == verdict.OFF_OUTCOME and _hr["verdict"] == "complicated"
  and "Going without vaccination" in _hr["tldr"] and "support this claim" not in _hr["tldr"],
  (_hr["verdict"], _hr["tldr"]))
_HG_SAFE_C = "Melatonin is safe for children"
_HG_SAFE_O = "(adverse events OR side effects)"
_HG_HARM = _hg_st("HG4", "Melatonin and adverse events in 400 children: a randomized controlled trial",
                  "Adverse events were more common with melatonin (p < 0.01).", ["Randomized Controlled Trial"],
                  background="Melatonin is given to children who sleep badly.")
_HG_SAFE_HAIR = _hg_st("HG5", "Melatonin and hair growth in 300 children: a randomized controlled trial",
                       "Hair growth did not differ between groups (p = 0.6).", ["Randomized Controlled Trial"],
                       background="Melatonin is said to cause few adverse events in children.")
_hr = _hg(_HG_SAFE_C, _HG_SAFE_O, "true", [(_HG_HARM, "against"), (_HG_SAFE_HAIR, "for")], [1, 2], subject="(melatonin)")
t("  and the mirror for a claim that something is safe: 'complicated', saying safety is not shown, never 'false' made firm",
  _hr["off_topic"].get("HG5") == verdict.OFF_OUTCOME and _hr["verdict"] == "complicated"
  and _hr["tldr"] == verdict._UNSAFE_UNPROVEN, (_hr["verdict"], _hr["tldr"]))
# Put back, a set-aside study counts as it would have in view: one the model
# cited is cited again, one it never cited is back in view, uncited.
_hr = _hg(_HG_C, _HG_O, "false", [(_HG_FOR, "for"), (_HG_HAIR_RCT, "against")], [1])
t("  a strong set-aside trial the model never cited does not weaken the safe answer it would not have weakened in view",
  _hr["off_topic"].get("HG3") == verdict.OFF_OUTCOME and _hr["verdict"] == "true"
  and _hr["tldr"].startswith("The strongest studies here support this claim."), (_hr["verdict"], _hr["tldr"]))
_hr = _hg(_HG_SAFE_C, _HG_SAFE_O, "true", [(_HG_HARM, "against"), (_HG_SAFE_HAIR, "for")], [1], subject="(melatonin)")
t("  and the mirror for a claim that something is safe: an uncited set-aside trial leaves 'false' as it stands",
  _hr["off_topic"].get("HG5") == verdict.OFF_OUTCOME and _hr["verdict"] == "false", (_hr["verdict"], _hr["tldr"]))
# Guards a change could remove unnoticed. An aim stated under its own heading,
# or in a background sentence by any of the aim wordings, names what a study
# set out to measure.
def _aim_st(pmid, background, objective=""):
    return _study(pmid, "Creatine in 250 older women: a randomized controlled trial", types=["Randomized Controlled Trial"],
                  abstract=f"BACKGROUND: Creatine is said to raise muscle strength in older adults. {background}\n"
                           + (f"OBJECTIVE: {objective}\n" if objective else "")
                           + "METHODS: 250 women were randomized to creatine or placebo.\n"
                             "RESULTS: Hair density did not differ between groups (p = 0.6).\nCONCLUSIONS: Hair was unchanged.")
_STR_OUT = "(muscle strength OR strength)"
t("set aside: an aim under its own heading names what the study set out to measure",
  evidence.measured_outcome(_aim_st("AM1", "", "To test whether creatine changes hair density."), _STR_OUT, "", "(creatine)")
  is False
  and evidence.measured_outcome(_aim_st("AM2", ""), _STR_OUT, "", "(creatine)") is None)
t("  and so does a background sentence in each of the aim wordings",
  [evidence.measured_outcome(_aim_st(f"AM{i}", b_), _STR_OUT, "", "(creatine)") for i, b_ in enumerate((
      "The trial was designed to measure hair density.", "Hair density was assessed in each woman.",
      "Hair density and shedding were examined at 12 weeks.", "To determine whether creatine changes hair density."), 3)]
  == [False] * 4)
# all_null asks every figure line to be a null: one real effect among nulls is an effect.
t("  'no clear effect' needs every line with a figure to be a null, not any one of them",
  not breakdown.all_null("Adults on the diet lost 4.1 kg more over 5 years (Study 1). Waist size changed by 0.4 cm, "
                         "which was not statistically significant (Study 1).", _WT_ST)
  and breakdown.all_null("Waist size changed by 0.4 cm, which was not statistically significant (Study 1).", _WT_ST))
# A set-aside trial still counts as read when the size is checked: one that
# found the effect and calls it significant keeps "no clear effect" off.
_NUL_STR = dict(_FL_AGAINST, abstract=_FL_AGAINST["abstract"].replace(
    "Leg press strength did not differ between groups (p = 0.6).",
    "Leg press strength rose by 1.2 kg more with creatine, which did not differ significantly between groups (p = 0.6)."))
_SIG_HAIR = dict(_FL_HAIR, pmid="FL5", abstract=_FL_HAIR["abstract"].replace(
    "Hair density did not differ between groups (p = 0.6)", "Hair density rose with creatine (p < 0.001)"))
_sp = {"verdict": "complicated", "tldr": "Trials disagree on whether creatine raises strength in older adults.",
       "explanation": "One trial found no strength gain (Study 1).", "still_open": "Which doses help.",
       "cited_study_numbers": [1, 2],
       "relevance": [{"study": 1, "subject": True, "outcome": True, "in_people": True, "finding": "against"},
                     {"study": 2, "subject": True, "outcome": True, "in_people": True, "finding": "for"}],
       "parts": [], "evidence": ["Leg press strength did not differ between groups (Study 1)."],
       "effect_size": "Leg press strength rose by 1.2 kg more with creatine, which was not statistically significant (Study 1).",
       "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": ""}
_sr = verdict._read_verdict(json.dumps(_sp), None, [dict(_NUL_STR), dict(_SIG_HAIR)],
                            "Creatine increases muscle strength in older adults",
                            subject="(creatine)", outcome=_STR_OUT, surrogate="")
t("  a significant trial for the claim that code set aside still keeps 'no clear effect' off the size",
  _sr["off_topic"].get("FL5") == verdict.OFF_OUTCOME and not ((_sr["breakdown"] or {}).get("effect") or {}).get("null"),
  ((_sr["breakdown"] or {}).get("effect"), _sr["off_topic"]))
# A saved answer that relied on a study set aside without the claim's stand-in
# is current when the stand-in it was saved with says the study measured it.
db.put_cached_verdict("creatine raises strength via hair", "(creatine) AND " + _STR_OUT, "true",
                      "Strength rose (Study 1).", ["FL3"], [dict(_FL_HAIR)], tldr="Creatine raises strength.",
                      still_open="Doses vary.")
db.put_cached_query("creatine raises strength via hair", verdict.QUERY_PROMPT_VERSION, "(creatine) AND " + _STR_OUT,
                    "hair density", "creatine raises strength")
db.put_cached_verdict("creatine raises strength no stand-in", "(creatine) AND " + _STR_OUT, "true",
                      "Strength rose (Study 1).", ["FL3"], [dict(_FL_HAIR)], tldr="Creatine raises strength.",
                      still_open="Doses vary.")
t("  a saved answer whose set-aside study its saved stand-in covers is current, and without the stand-in it is not",
  appmod._current(db.get_cached_verdict("creatine raises strength via hair", 24), "creatine raises strength via hair")
  is not None
  and appmod._current(db.get_cached_verdict("creatine raises strength no stand-in", 24),
                      "creatine raises strength no stand-in") is None)
t("  a cap either reading earns applies: the studies used, and those with the set-aside put back",
  verdict._with_aside(["A"], ["B"]) == [["A"], ["A", "B"]] and verdict._with_aside(["A"], []) == [["A"]])
_SA_TRIAL = _study("SA1", "Creatine and muscle strength in 900 older adults: a randomized controlled trial",
                   abstract="BACKGROUND: Ageing lowers strength.\nMETHODS: 900 adults were randomized.\n"
                            "RESULTS: Muscle strength rose more with creatine (p < 0.001).\nCONCLUSIONS: Creatine raised strength.",
                   types=["Randomized Controlled Trial"])
_SA_REVIEW = dict(_study("SA2", "Creatine and hair loss: a systematic review and meta-analysis",
                         abstract="BACKGROUND: Creatine raises muscle strength.\nMETHODS: We pooled 12 trials.\n"
                                  "RESULTS: Hair loss did not differ between groups.\nCONCLUSIONS: Hair was unchanged. "
                                  "The certainty of the evidence was very low.",
                         types=["Systematic Review", "Meta-Analysis"]), off_topic=verdict.OFF_OUTCOME)
with appmod.app.test_request_context("/"):
    _sa = appmod._check_response("Creatine increases muscle strength", "(creatine) AND (muscle strength OR strength)",
                                 "true", "Creatine raises strength.", "Strength rose (Study 1).", ["SA1"],
                                 [dict(_SA_TRIAL), dict(_SA_REVIEW)], "http://x", still_open="Doses.")
t("  on the page, a review code set aside that rates its evidence very low still caps the line and the stamp",
  _sa["evidence_line"].startswith("Very low certainty") and _sa["verdict"] == "complicated",
  (_sa["verdict"], _sa["evidence_line"]))
# A saved answer that relied on a study the current code sets aside was written
# with that study in view, and code cannot tell which of its sentences lean
# on it. It is not patched and served: it is out of date, and checked fresh.
db.put_cached_verdict("creatine raises strength in men", "(creatine) AND (" + _hair_out + ")", "true",
                      "Strength rose (Study 1), and hormones too (Study 2).", ["MO1", "40265319"],
                      [dict(_RES_ONLY), dict(_hair)], tldr="Creatine raises strength and hormones.",
                      still_open="Doses vary.")
db.put_cached_verdict("creatine raises strength in women", "(creatine) AND (" + _hair_out + ")", "true",
                      "Strength rose (Study 1).", ["MO1"], [dict(_RES_ONLY), dict(_hair)],
                      tldr="Creatine raises strength.", still_open="Doses vary.")
_calls0 = fg.models.calls
fg.models.script = [Resp("(creatine) AND (strength)"), Resp(VJ("complicated", [1]))]
_od = P("creatine raises strength in men", ip="7.7.7.71").get_json()
t("a saved answer that relied on a study code now sets aside is out of date and checked fresh",
  _od.get("cached") is False and fg.models.calls > _calls0, (_od.get("cached"), fg.models.calls - _calls0))
_calls0 = fg.models.calls
_ok = P("creatine raises strength in women", ip="7.7.7.72").get_json()
t("  and one that only listed that study, without relying on it, is served as saved",
  _ok.get("cached") is True and fg.models.calls == _calls0, (_ok.get("cached"), fg.models.calls - _calls0))
_MO = [dict(_RES_ONLY), dict(_hair), dict(_UNSTRUCT)]
fg.models.script = [Resp(VJ("true", [1, 2, 3], tldr="Creatine raises strength.",
                            explanation="Strength rose (Study 1). Hair was unchanged (Study 2). Hair again (Study 3).",
                            relevance=[{"study": i, "subject": True, "outcome": True} for i in range(1, 4)]))]
_v = verdict.weigh_evidence("Creatine increases muscle strength", _MO, f"(creatine) AND ({_hair_out})")
t("  in an answer the code sets the hair trial aside as off outcome, whatever the model judged",
  _MO[1].get("off_topic") == verdict.OFF_OUTCOME and "40265319" not in _v["cited_studies"], (_v["cited_studies"], _MO[1].get("off_topic")))
t("  and keeps the trial that measured it and the unstructured abstract",
  "MO1" in _v["cited_studies"] and not _MO[0].get("off_topic") and not _MO[2].get("off_topic"),
  (_v["cited_studies"], [m_.get("off_topic") for m_ in _MO]))
t("the prompt asks for subject and outcome for every study, and marks OFF TOPIC",
  '"relevance"' in verdict.weigh_prompt("c", "s") and "relevance" in verdict.VERDICT_SCHEMA["required"]
  and "OFF TOPIC" in verdict._format_study_for_prompt(2, _SMOKE[1], claim="Smoking causes lung cancer"))
db.put_cached_verdict("off topic render", "q", "true", "Because (Study 1).", ["L1"], _SMOKE,
                      tldr="Smoking causes lung cancer.", still_open="Doses vary.")
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
                      tldr="No study has tested this.", still_open="Whether any study has tested it.")
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

# ---- the same claim typed another way gets the same saved answer -------------
# db.claim_fold is an ordered key: exact equality, no synonyms. The 140 pairs
# in tests/equivalent_pairs.json are the approved rule, one test each.
_EQ_PAIRS = json.loads((ROOT / "tests" / "equivalent_pairs.json").read_text())
for _p in _EQ_PAIRS:
    _same = db.claim_fold(_p["a"]) == db.claim_fold(_p["b"]) != ()
    t(f"equivalent [{_p['cat']}] {_p['a']!r} vs {_p['b']!r}: {_p['expect']}",
      _same == (_p["expect"] == "match"), (db.claim_fold(_p["a"]), db.claim_fold(_p["b"])))
# Two claims match when they fold to the same key and that key is not empty
# (find_equivalent never matches an empty key).
_eqf = lambda a, b: db.claim_fold(a) == db.claim_fold(b) != ()
t("equivalent: question form, plural, punctuation and articles fold together",
  _eqf("Coffee causes cancer", "Does coffee cause cancer?")
  and _eqf("Eggs raise cholesterol", "Egg raises cholesterol")
  and _eqf("Omega-3 improves memory!", "omega 3 improves memory")
  and _eqf("The HPV vaccine is safe", "Is HPV vaccine safe"))
t("  negation, swapped order, dose, over and under, before and after, more and less never fold",
  not any(_eqf(a, b) for a, b in [
      ("Vaccines cause autism", "Vaccines don't cause autism"),
      ("Vaccines cause autism", "Vaccines do not cause autism"),
      ("Coffee causes cancer", "Cancer causes coffee"),
      ("Taking 4000 IU of vitamin D is safe", "Taking 400 IU of vitamin D is safe"),
      ("Taking 4000 IU of vitamin D is safe", "Taking 4,000 IU of vitamin D is safe"),
      ("Testosterone is safe for men over 50", "Testosterone is safe for men under 50"),
      ("Coffee before exercise helps", "Coffee after exercise helps"),
      ("More sleep improves memory", "Less sleep improves memory")]))
t("  a hedge is part of the claim: can, may, might, should and will never fold into each other or into none",
  not any(_eqf(a, b) for a, b in [
      ("Smoking causes lung cancer", "Smoking can cause lung cancer"),
      ("Coffee may cause cancer", "Coffee might cause cancer"),
      ("Coffee should be avoided", "Coffee will be avoided")])
  and _eqf("Can vitamin D prevent colds?", "Vitamin D can prevent colds"))
t("  cures, helps and causes are three claims",
  not any(_eqf(a, b) for a, b in [
      ("Apple cider vinegar cures diabetes", "Apple cider vinegar helps diabetes"),
      ("Apple cider vinegar helps diabetes", "Apple cider vinegar causes diabetes"),
      ("Apple cider vinegar cures diabetes", "Apple cider vinegar causes diabetes")]))
t("equivalent: a short form is spelled out as its own word, never glued into another word",
  not any(_eqf(a, b) for a, b in [
      ("Shell fish causes allergies", "She'll fish causes allergies"),
      ("Shell fish causes allergies", "She\u2019ll fish causes allergies"),
      ("Were healthier on keto", "We're healthier on keto"),
      ("Id causes anxiety", "I'd causes anxiety")])
  and _eqf("She'll get sick from vaccines", "She will get sick from vaccines")
  and _eqf("I'm tired after sugar", "I am tired after sugar"))
# The grader's 84 hostile pairs (tests/hostile_pairs.json): every one stays
# apart, except three the approved rule folds on purpose (each says why).
for _p in json.loads((ROOT / "tests" / "hostile_pairs.json").read_text()):
    t(f"hostile pair {_p['a'][:60]!r} vs {_p['b'][:60]!r}: {_p['expect']}",
      _eqf(_p["a"], _p["b"]) == (_p["expect"] == "match"), (db.claim_fold(_p["a"])[:12], db.claim_fold(_p["b"])[:12]))
t("equivalent: a number keeps its sign and its decimal point",
  not any(_eqf(a, b) for a, b in [
      ("Ice baths at -5 C help recovery", "Ice baths at 5 C help recovery"),
      ("Ice baths at \u22125 C help recovery", "Ice baths at 5 C help recovery"),
      ("Ice baths at \u20135 C help recovery", "Ice baths at 5 C help recovery"),
      ("Take .5 mg melatonin for sleep", "Take 5 mg melatonin for sleep"),
      ("Take.5 mg melatonin for sleep", "Take 5 mg melatonin for sleep"),
      ("Take -.5 mg", "Take .5 mg"), ("Take -0.5 mg", "Take 0.5 mg"),
      ("Salt -10% raises blood pressure", "Salt 10% raises blood pressure")])
  and _eqf("Ice baths at -5 C help recovery", "Ice baths at \u22125 C help recovery")
  and _eqf("Omega-3 improves memory", "Omega 3 improves memory")
  and db.claim_fold("Take 2-5 mg") == ("take", "2-5", "mg"))
t("  a dash standing free right before a number is kept, never dropped",
  not any(_eqf(a, b) for a, b in [
      ("Ice baths at - 5 C help recovery", "Ice baths at 5 C help recovery"),
      ("Ice baths at \u2212 5 C help recovery", "Ice baths at 5 C help recovery"),
      ("Lose 5 - 10 kg on keto", "Lose 5 10 kg on keto"),
      ("Take vitamin C - 500 mg", "Take vitamin C 500 mg"),
      ("Ice baths at - .5 C help", "Ice baths at .5 C help")])
  and _eqf("Ice baths at - 5 C help recovery", "Ice baths at \u2013 5 C help recovery")
  and _eqf("Coffee - the drink - causes cancer", "Coffee the drink causes cancer"))
db.put_cached_verdict("Ice baths at 5 C help recovery", "(ice) AND (recovery)", "complicated", "Mixed (Study 1).",
                      ["P1"], STUDIES, tldr="Short.", still_open="Open.")
db.put_cached_verdict("Take 5 mg melatonin for sleep", "(melatonin) AND (sleep)", "complicated", "Mixed (Study 1).",
                      ["P1"], STUDIES, tldr="Short.", still_open="Open.")
t("  and a saved claim with another sign or decimal point is never served for it",
  db.find_equivalent("Do ice baths at -5 C help recovery?") is None
  and db.find_equivalent("Take .5 mg melatonin for sleep") is None
  and db.find_equivalent("Does taking 5 mg melatonin for sleep?") is None
  and db.find_equivalent("Ice baths at 5 C helps recovery") == "Ice baths at 5 C help recovery")
# No letter is silently dropped: a claim whose folding would lose any part of
# a word (a combining mark, an invisible joiner) has no key and matches nothing.
_nokey = lambda c: db.claim_fold(c) == ()
t("equivalent: a vowel sign or other combining mark is never dropped, in any script",
  all(_nokey(x) for x in (
      "दाल से कैंसर होता है", "दिल से कैंसर होता है", "का", "की",    # Devanagari
      "ডাল খেলে ক্যান্সার হয়", "ডিল খেলে ক্যান্সার হয়",              # Bengali
      "กาแฟทำให้เป็นมะเร็ง",                                       # Thai
      "القَهوة تسبب السرطان",                                       # Arabic with a vowel mark
      "קָפֶה גורם לסרטן",                                           # Hebrew with points
      "காபி புற்றுநோயை உண்டாக்கும்",                                 # Tamil
      "Coq\u0301 au vin raises cholesterol"))                       # a Latin accent with no composed form
  and not _eqf("दाल से कैंसर होता है", "दिल से कैंसर होता है") and not _eqf("का", "की")
  and not _eqf("ডাল খেলে ক্যান্সার হয়", "ডিল খেলে ক্যান্সার হয়"))
t("  an invisible character inside a word is part of the word too",
  _nokey("Vaccines n\u200bot cause autism") and _nokey("Soft\u00adware causes stress")
  and not _eqf("Vaccines n\u200bot cause autism", "Vaccines n ot cause autism"))
t("equivalent: a raised or lowered digit or a fraction character never becomes a plain digit",
  all(_nokey(x) for x in ("10\u2079 CFU probiotics work", "10\u00b2 mg zinc", "H\u2082O cures colds",
                          "Take \u00bd tablet", "Take 1\u00bd tablets"))
  and not _eqf("Take 1\u20442 tablet", "Take 12 tablet")
  and not _eqf("10\u2079 CFU probiotics work", "109 CFU probiotics work")
  and not _eqf("10\u00b2 mg zinc", "102 mg zinc") and not _eqf("H\u2082O cures colds", "H2O cures colds")
  and not _eqf("Take \u00bd tablet", "Take 12 tablet")
  and _eqf("\u2460 vitamin C works", "1 vitamin C works"))
t("equivalent: no letter is folded into more than one letter",
  all(_nokey(x) for x in ("Die Ma\u00dfe des Fetts", "\ufb01sh oil helps", "Take 5 \u338e zinc",
                          "\u0130la\u00e7 yarar"))
  and not _eqf("Die Ma\u00dfe des Fetts", "Die Masse des Fetts")
  and not _eqf("\ufb01sh oil helps", "fish oil helps")
  and _eqf("Coffee causes cancer\u2026", "Coffee causes cancer...")
  and _eqf("\uff11\uff10 mg zinc helps", "10 mg zinc helps"))
t("  scripts written without combining marks still fold as before",
  _eqf("кофе вызывает рак", "Кофе вызывает рак?") and _eqf("咖啡致癌", "咖啡致癌？")
  and _eqf("القهوة تسبب السرطان", "القهوة تسبب السرطان؟") and not _eqf("咖啡致癌", "茶致癌"))
t("  café is never cafe; a composed and a decomposed é are the same letter",
  not _eqf("Café au lait raises cholesterol", "Cafe au lait raises cholesterol")
  and _eqf("Café au lait raises cholesterol", "Cafe\u0301 au lait raises cholesterol")
  and db.claim_fold("Café au lait raises cholesterol") != ())
db.put_cached_verdict("दाल से कैंसर होता है", "(lentils) AND (cancer)", "false", "No (Study 1).",
                      ["P1"], STUDIES, tldr="No.", still_open="Open.")
t("  and a claim with no key is never matched to a saved claim", db.find_equivalent("दिल से कैंसर होता है") is None
  and db.find_equivalent("दाल से कैंसर होता है?") is None)
t("  the small words dropped never include a negation, a preposition that sets direction or a hedge",
  not (db._FOLD_DROP & {"not", "no", "never", "without", "over", "under", "before", "after", "more",
                        "less", "to", "from", "and", "or", "can", "may", "might", "should", "will"}))

_EQ_SAVED = "Creatine causes hair loss in men"
db.put_cached_verdict(_EQ_SAVED, "(creatine) AND (hair loss)", "false", "No trial found it (Study 1).",
                      ["P1"], STUDIES, tldr="No, the trials found no hair loss.", still_open="Longer trials.")
fg.models.script = []; fg.models.calls = 0
r = P("Does creatine cause hair loss in men?", ip="7.7.8.1"); d = r.get_json()
t("equivalent: a reworded claim is answered from the saved check, with no model call",
  r.status_code == 200 and d["cached"] and fg.models.calls == 0 and d["verdict"] == "false", d)
t("  under the saved wording: the claim, the share link and the log",
  d["claim"] == _EQ_SAVED and d["share_url"].endswith("/?q=" + urllib.parse.quote(_EQ_SAVED))
  and db.recent_checks(1)[0]["claim_text"] == _EQ_SAVED, (d["claim"], d["share_url"]))
t("  and it says what was typed, as a reworded match, not a spelling fix",
  d["typed"] == "Does creatine cause hair loss in men?" and d["typed_reason"] == "wording", d)
_r, _evs = stream("Is it true that creatine causes hair loss in men?", ip="7.7.8.2")
t("  the streamed check gives the same saved answer and the same note",
  _evs[-1]["stage"] == "done" and _evs[-1]["result"]["claim"] == _EQ_SAVED
  and _evs[-1]["result"]["typed_reason"] == "wording" and fg.models.calls == 0, _evs[-1])
_r, _evs = stream("Creatine cause hair loss in men", ip="7.7.8.3")
t("  a dropped s is the same claim on the stream too",
  _evs[-1]["result"]["claim"] == _EQ_SAVED and _evs[-1]["result"]["typed"] == "Creatine cause hair loss in men"
  and _evs[-1]["result"]["typed_reason"] == "wording" and fg.models.calls == 0, _evs[-1])
fg.models.script = [Resp("(creatine) AND (hair loss)"), Resp(VJ("complicated", [1]))]
d = P("Creatine can cause hair loss in men", ip="7.7.8.4").get_json()
t("  a hedged wording is another claim: it is checked fresh under its own words",
  not d["cached"] and d["claim"] == "Creatine can cause hair loss in men" and d["typed"] is None, d)
t("  a claim checked under its own words carries no note reason", d["typed_reason"] is None, d)

# The second look for a reworded match, after the spelling switch: a
# misspelled rewording of a saved claim gets the saved answer, with one model
# call (the search) and none for the verdict.
_EQ_SP = "Creatine causes hair loss in women"
db.put_cached_verdict(_EQ_SP, "(creatine) AND (hair loss)", "false", "No trial found it (Study 1).",
                      ["P1"], STUDIES, tldr="No, the trials found no hair loss in women.", still_open="Longer trials.")
fg.models.script = [Resp("(creatine) AND (hair loss)\nClaim: Does creatine cause hair loss in women?")]
fg.models.calls = 0
d = P("Does creatin cause hair los in women?", ip="7.7.8.7").get_json()
t("equivalent: after the spelling fix, a reworded match is looked for again and answered from the saved check",
  d.get("cached") and d.get("claim") == _EQ_SP and d.get("typed") == "Does creatin cause hair los in women?"
  and d.get("typed_reason") == "spelling" and fg.models.calls == 1 and not fg.models.script, (d, fg.models.calls))

# Own key first: a stale row under the claim's own words is checked again
# under those words, even when a newer row of the same claim in other words
# is fresh. It is never swapped for the other wording.
_OWN = "Turmeric eases joint pain in adults"
db.put_cached_verdict(_OWN, "(turmeric) AND (joint pain)", "complicated", "Mixed (Study 1).",
                      ["P1"], STUDIES, tldr="Own old answer.", still_open="Own open line.")
with db.get_conn() as _conn:
    _conn.execute("UPDATE verdict_cache SET created_at = ? WHERE claim_key = ?",
                  ("2020-01-01T00:00:00+00:00", db.normalize_claim(_OWN)))
db.put_cached_verdict("Does turmeric ease joint pain in adults?", "(turmeric) AND (joint pain)", "complicated",
                      "Mixed (Study 1).", ["P1"], STUDIES, tldr="Other wording answer.", still_open="Open.")
t("equivalent: a row under the claim's own key comes before a newer row in other words",
  db.find_equivalent(_OWN) == _OWN)
fg.models.script = [Resp("(turmeric) AND (joint pain)"), Resp(VJ("complicated", [1], tldr="Own fresh answer."))]
d = P(_OWN, ip="7.7.8.8").get_json()
t("  so its stale row is checked again under its own words, with no note",
  not d.get("cached") and d.get("claim") == _OWN and d.get("tldr") == "Own fresh answer."
  and d.get("typed") is None, d)

# A saved check older than the window is checked again under its saved
# wording: its one row is refreshed, and no second row for it is made.
_EQ_OLD = "Green tea lowers blood pressure in adults"
db.put_cached_verdict(_EQ_OLD, "(green tea) AND (blood pressure)", "complicated", "Mixed (Study 1).",
                      ["P1"], STUDIES, tldr="Old short answer.", still_open="Old open line.")
with db.get_conn() as _conn:
    _conn.execute("UPDATE verdict_cache SET created_at = ? WHERE claim_key = ?",
                  ("2020-01-01T00:00:00+00:00", db.normalize_claim(_EQ_OLD)))
db.put_cached_query(_EQ_OLD, verdict.QUERY_PROMPT_VERSION, "(green tea) AND (blood pressure)", "", _EQ_OLD)
fg.models.script = [Resp(VJ("complicated", [1], tldr="Fresh short answer.", still_open="Fresh open line."))]
fg.models.calls = 0
d = P("Does green tea lower blood pressure in adults?", ip="7.7.8.5").get_json()
with db.get_conn() as _conn:
    _eq_rows = [r["claim_text"] for r in _conn.execute("SELECT claim_text FROM verdict_cache").fetchall()
                if db.claim_fold(r["claim_text"]) == db.claim_fold(_EQ_OLD)]
t("equivalent: a stale match is checked fresh under the saved wording, reusing its saved search",
  not d["cached"] and d["claim"] == _EQ_OLD and d["tldr"] == "Fresh short answer."
  and d["typed"] == "Does green tea lower blood pressure in adults?" and d["typed_reason"] == "wording"
  and d["search_query_used"] == "(green tea) AND (blood pressure)", d)
t("  and refreshes that one row: no second row for the same claim", _eq_rows == [_EQ_OLD], _eq_rows)

# An incomplete saved row (no still-open line) is never served through a match.
_EQ_BAD = "Garlic lowers cholesterol in adults"
db.put_cached_verdict(_EQ_BAD, "(garlic) AND (cholesterol)", "complicated", "Mixed (Study 1).",
                      ["P1"], STUDIES, tldr="Half a row.", still_open="")
t("equivalent: an incomplete saved row is never returned", db.find_equivalent("Does garlic lower cholesterol in adults?") is None)
fg.models.script = [Resp("(garlic) AND (cholesterol)"), Resp(VJ("complicated", [1], tldr="A whole answer."))]
d = P("Does garlic lower cholesterol in adults?", ip="7.7.8.6").get_json()
t("  and the check runs fresh under the reader's own words",
  not d["cached"] and d["tldr"] == "A whole answer." and d["claim"] == "Does garlic lower cholesterol in adults?"
  and d["typed"] is None, d)
# A saved record the page cannot repair (here: a claim that looks personal) is not served either.
_EQ_PERS = "My son Jake 7 gets sick from milk"
db.put_cached_verdict(_EQ_PERS, "(milk) AND (illness)", "complicated", "Mixed (Study 1).",
                      ["P1"], STUDIES, tldr="Short.", still_open="Open.")
t("  nor is a record with a problem the page cannot repair",
  bool(appmod.display.saved_record_problems(_EQ_PERS, "complicated", ["P1"], STUDIES, None, "q", "2026-01-01"))
  and db.find_equivalent("My son Jake 7 get sick from milk") is None)

# The search for a reworded match reads at most the newest EQUIVALENT_SCAN
# saved claims, newest first; the claim's own key is one lookup at any age.
t("equivalent: the search for a reworded match reads at most the newest 2,000 saved claims",
  db.EQUIVALENT_SCAN == 2000)
_scan_db = os.path.join(tempfile.mkdtemp(), "scan.db")
_real_db_path = db.DB_PATH
_real_scan = db.EQUIVALENT_SCAN
db.DB_PATH = _scan_db
db.EQUIVALENT_SCAN = 5
try:
    db.init_db()

    def _scan_put(claim, date):
        db.put_cached_verdict(claim, "(q)", "complicated", "Mixed (Study 1).", ["P1"], STUDIES,
                              tldr="Short.", still_open="Open.")
        with db.get_conn() as _conn:
            _conn.execute("UPDATE verdict_cache SET created_at = ? WHERE claim_key = ?",
                          (date, db.normalize_claim(claim)))

    _scan_put("Saffron lifts mood in adults", "2020-01-01T00:00:00+00:00")
    _scan_put("Beetroot juice lowers blood pressure", "2020-01-02T00:00:00+00:00")
    for _i in range(4):
        _scan_put(f"Filler claim number {_i} about sleep", f"2021-01-0{_i + 1}T00:00:00+00:00")
    t("  a match among the newest N is found", db.find_equivalent("Does beetroot juice lower blood pressure?")
      == "Beetroot juice lowers blood pressure")
    _folded = []
    _real_fold = db.claim_fold
    db.claim_fold = lambda c: _folded.append(c) or _real_fold(c)
    try:
        _beyond = db.find_equivalent("Does saffron lift mood in adults?")
    finally:
        db.claim_fold = _real_fold
    t("  a row beyond the newest N is not read, so it is not matched",
      _beyond is None and "Saffron lifts mood in adults" not in _folded
      and len(_folded) <= db.EQUIVALENT_SCAN + 1, (_beyond, _folded))
    with db.get_conn() as _conn:
        _plan = " ".join(str(tuple(r)) for r in _conn.execute(
            "EXPLAIN QUERY PLAN SELECT claim_key, claim_text, tldr, still_open FROM verdict_cache "
            "ORDER BY created_at DESC, rowid DESC LIMIT 5").fetchall())
    t("  the newest rows are read from an index on the date, not by sorting the whole table",
      "verdict_cache_time" in _plan and "TEMP B-TREE" not in _plan, _plan)
    t("  the claim's own saved row is found at any age, first",
      db.find_equivalent("Saffron lifts mood in adults") == "Saffron lifts mood in adults")
finally:
    db.DB_PATH = _real_db_path
    db.EQUIVALENT_SCAN = _real_scan

# Both renderers say the same line, from the same two fields.
_TN = [("x", None), ("Does coffee cause cancer?", "wording"), ("smokng causs cancr", "spelling"), (None, "wording"), ("", None)]
_tn_py = [appmod.display.typed_note(a, b) for a, b in _TN]
t("typed note: a spelling fix and a reworded match each say what happened",
  _tn_py[1] == "Showing the saved check for this claim. You typed “Does coffee cause cancer?”."
  and _tn_py[2] == "Spelling fixed. You typed “smokng causs cancr”." and _tn_py[3] == "" and _tn_py[4] == "", _tn_py)
_tn_js_src = (ROOT / "static" / "app.js").read_text()
t("  renderResult and the live stage both pass the reason to the note",
  "showTyped(data.typed, data.typed_reason)" in _tn_js_src and "showTyped(typed, ev.typed_reason)" in _tn_js_src)
_tn_tpl = (ROOT / "templates" / "index.html").read_text()
t("  the page renders the same line through display.typed_note_parts, the reader's words in <bdi>",
  "{% set tn = typed_note_parts(result.typed, result.typed_reason) %}{{ tn[0] }}<bdi>{{ tn[1] }}</bdi>{{ tn[2] }}" in _tn_tpl)
_tn_mark = _tn_tpl[_tn_tpl.index('<p class="typed-note"'):]
_tn_mark = _tn_mark[:_tn_mark.index("</p>") + 4]
with appmod.app.test_request_context("/"):
    _tn_env = appmod.app.jinja_env
    _tn_on = _tn_env.from_string(_tn_mark).render(result={"typed": "Does coffee cause cancer?", "typed_reason": "wording"})
    _tn_off = _tn_env.from_string(_tn_mark).render(result=None)
t("  the template's own markup shows the line with a result that carries it, and stays hidden without",
  _tn_on == '<p class="typed-note" id="typed-note">Showing the saved check for this claim. You typed \u201c<bdi>Does coffee cause cancer?</bdi>\u201d.</p>'
  and _tn_off == '<p class="typed-note" id="typed-note" hidden></p>', (_tn_on, _tn_off))
if _shutil.which("node"):
    _tn_fn = _tn_js_src[_tn_js_src.index("function typedNote"):_tn_js_src.index("function showTyped")]
    _tn_js = json.loads(_subprocess.run(
        ["node", "-e", _tn_fn + "console.log(JSON.stringify(" + json.dumps(_TN) + ".map(([a, b]) => typedNote(a, b))));"],
        capture_output=True, text=True, check=True).stdout)
    t("  the script and the server write the same note, on five inputs", _tn_js == _tn_py, (_tn_js, _tn_py))
t("  no note string carries a dash", not any(ch in s for s in _tn_py for ch in "\u2013\u2014"))

# ---- steady answers: a stale saved answer is kept when nothing has changed ------
# Past the cache window the search runs again. Same method stamp, same set of
# PubMed ids, a whole and sound row: the saved answer is kept, re-dated, and no
# model is asked. Anything else and the check runs as before.
_OLD_DATE = "2020-01-01T00:00:00+00:00"


def _steady_row(claim, studies=None, stamp="now", still="Longer trials."):
    q = "(steady) AND (claim)"
    db.put_cached_verdict(claim, q, "complicated", "Mixed (Study 1).", ["P1"], studies or STUDIES,
                          tldr="Saved short answer.", still_open=still,
                          method_stamp=verdict.METHOD_STAMP if stamp == "now" else stamp)
    with db.get_conn() as _conn:
        _conn.execute("UPDATE verdict_cache SET created_at = ? WHERE claim_key = ?",
                      (_OLD_DATE, db.normalize_claim(claim)))
    db.put_cached_query(claim, verdict.QUERY_PROMPT_VERSION, q, "", claim)


def _saved_row(claim):
    with db.get_conn() as _conn:
        return _conn.execute("SELECT created_at, method_stamp, tldr FROM verdict_cache WHERE claim_key = ?",
                             (db.normalize_claim(claim),)).fetchone()


t("steady: the method stamp is a short hash worked out at start-up from the files that decide an answer",
  len(verdict.METHOD_STAMP) == 12 and all(ch in "0123456789abcdef" for ch in verdict.METHOD_STAMP)
  and set(verdict.ANSWER_FILES) >= {"verdict.py", "evidence.py", "pubmed.py", "display.py"})
_real_files = verdict.ANSWER_FILES
verdict.ANSWER_FILES = _real_files[:-1]
_fewer = verdict._method_stamp()
verdict.ANSWER_FILES = _real_files
t("  and it changes when the files it is made from change", _fewer != verdict._method_stamp())
# A stamp that cannot be worked out is empty: the app still starts, a warning
# is logged, nothing is kept on an empty stamp and an empty stamp is not stored.
import builtins as _bi  # noqa: E402
import importlib.util as _ilu  # noqa: E402
_real_open = _bi.open


def _unreadable(path, *a, **k):
    if os.path.basename(str(path)) in verdict.ANSWER_FILES:
        raise PermissionError("unreadable")
    return _real_open(path, *a, **k)


class _Grab(logging.Handler):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, rec): self.lines.append(rec.getMessage())


_grab = _Grab()
logging.getLogger("evident.stamp").addHandler(_grab)
_bi.open = _unreadable
try:
    try:
        _empty = verdict._method_stamp()
    except Exception as _e:  # noqa: BLE001
        _empty = _e
    _spec = _ilu.spec_from_file_location("verdict_nostamp", ROOT / "verdict.py")
    _vcopy = _ilu.module_from_spec(_spec)
    try:
        _spec.loader.exec_module(_vcopy)
        _copy_stamp = _vcopy.METHOD_STAMP
    except Exception as _e:  # noqa: BLE001
        _copy_stamp = _e
finally:
    _bi.open = _real_open
    logging.getLogger("evident.stamp").removeHandler(_grab)
t("steady: a stamp that cannot be worked out is empty and logged, never an exception",
  _empty == "" and any("No method stamp" in x for x in _grab.lines), (_empty, _grab.lines))
t("  and verdict.py still imports with an empty stamp when a source file cannot be read",
  _copy_stamp == "", _copy_stamp)
_real_stamp = verdict.METHOD_STAMP
verdict.METHOD_STAMP = ""
try:
    for _lbl, _st in (("no stamp", None), ("an empty stamp", "")):
        _c = "Steady claim with an empty current stamp and " + _lbl
        _steady_row(_c, stamp=_st)
        t(f"steady: an empty current stamp keeps nothing, against a row with {_lbl}",
          appmod._steady(_c, STUDIES) is None and _saved_row(_c)["created_at"] == _OLD_DATE)
    _c = "Steady claim stored with an empty stamp straight in the table"
    _steady_row(_c)
    with db.get_conn() as _conn:
        _conn.execute("UPDATE verdict_cache SET method_stamp = '' WHERE claim_key = ?", (db.normalize_claim(_c),))
    _real_by_key = db.get_cached_by_key
    _as_read = dict(_real_by_key(db.normalize_claim(_c)), method_stamp="")
    db.get_cached_by_key = lambda key: dict(_as_read)
    try:
        _kept_empty = appmod._steady(_c, STUDIES)
    finally:
        db.get_cached_by_key = _real_by_key
    t("  a row whose stored stamp is an empty string is not kept while the stamp in force is empty, even read as empty",
      appmod._steady(_c, STUDIES) is None and _kept_empty is None and _saved_row(_c)["created_at"] == _OLD_DATE)
    _c = "Steady claim saved while the stamp is empty"
    fg.models.script = [Resp("(steady) AND (empty)"), Resp(VJ("complicated", [1], tldr="Made with no stamp."))]
    d = P(_c, ip="7.7.10.8").get_json()
    t("  and a check saved with an empty stamp stores no stamp",
      d.get("tldr") == "Made with no stamp." and _saved_row(_c)["method_stamp"] is None, (d, _saved_row(_c) and dict(_saved_row(_c))))
finally:
    verdict.METHOD_STAMP = _real_stamp
db.put_cached_verdict("Stamp stored as given", "(q)", "complicated", "E.", ["P1"], STUDIES,
                      tldr="T.", still_open="O.", method_stamp="")
t("  db stores an empty stamp as none", _saved_row("Stamp stored as given")["method_stamp"] is None)

_SK = "Steady claim kept as saved"
_steady_row(_SK)
fg.models.script = []; fg.models.calls = 0
d = P(_SK, ip="7.7.10.1").get_json()
t("steady: same stamp, same study ids and a whole row: the saved answer is kept, with no model call",
  d["cached"] and d["tldr"] == "Saved short answer." and fg.models.calls == 0, (d.get("tldr"), fg.models.calls))
t("  and its date is moved to now", _saved_row(_SK)["created_at"] > _OLD_DATE
  and d["cached_at"] == _saved_row(_SK)["created_at"], _saved_row(_SK)["created_at"])
_steady_row(_SK)
fg.models.calls = 0
_r, _evs = stream(_SK, ip="7.7.10.2")
t("  the streamed check keeps it the same way, after running the search",
  _evs[-1]["stage"] == "done" and _evs[-1]["result"]["cached"] and fg.models.calls == 0
  and any(e["stage"] == "found" for e in _evs), _evs[-1].get("stage"))

for _lbl, _kw in (("the study ids differ by one", {"studies": STUDIES[:2] + [dict(STUDIES[2], pmid="P9")]}),
                  ("the stamp differs", {"stamp": "0123456789ab"}),
                  ("the stamp is missing", {"stamp": None}),
                  ("the row is not whole", {"still": ""})):
    _c = "Steady claim rerun when " + _lbl
    _steady_row(_c, **_kw)
    fg.models.script = [Resp(VJ("complicated", [1], tldr="Made again.", still_open="New open line."))]
    fg.models.calls = 0
    d = P(_c, ip="7.7.10.3").get_json()
    t(f"steady: checked again when {_lbl}",
      not d["cached"] and d["tldr"] == "Made again." and fg.models.calls >= 1, (d.get("tldr"), fg.models.calls))
    t(f"  and the row is replaced by one with the current stamp ({_lbl})",
      _saved_row(_c)["method_stamp"] == verdict.METHOD_STAMP and _saved_row(_c)["tldr"] == "Made again.")

# The same ids are not enough: each study must be the same record as the one
# the saved answer read (id, title, abstract, publication types).
import copy as _copy  # noqa: E402


def _changed(field, value, i=0):
    out = _copy.deepcopy(STUDIES)
    if value is None:
        out[i].pop(field)
    else:
        out[i][field] = value
    return out


for _lbl, _fresh, _saved in (
        ("study 1 is now marked retracted", _changed("publication_types", ["Journal Article", "Retracted Publication"]), None),
        ("study 1 now carries an expression of concern", _changed("publication_types", ["Journal Article", "Expression of Concern"]), None),
        ("study 2 now has a correction", _changed("publication_types", ["Journal Article", "Published Erratum"], 1), None),
        ("study 1's abstract changed", _changed("abstract", "A new abstract that reverses the finding."), None),
        ("study 3's title changed", _changed("title", "A corrected title", 2), None),
        ("a saved study lacks its abstract", STUDIES, _changed("abstract", None)),
        ("a saved study lacks its publication types", STUDIES, _changed("publication_types", None, 1))):
    _c = "Steady record check when " + _lbl
    _steady_row(_c, studies=_saved)
    _real_saf = pubmed.search_and_fetch
    pubmed.search_and_fetch = lambda q, max_results=8, surrogate="", _f=_fresh: _copy.deepcopy(_f)
    # Two studies cited: a retracted study 1 is background now, and the
    # answer still rests on study 2.
    fg.models.script = [Resp(VJ("complicated", [1, 2], tldr="Made again.", still_open="New open line."))]
    fg.models.calls = 0
    try:
        d = P(_c, ip="7.7.10.6").get_json()
    finally:
        pubmed.search_and_fetch = _real_saf
    t(f"steady: checked again when {_lbl}",
      not d["cached"] and d["tldr"] == "Made again." and fg.models.calls >= 1, (d.get("tldr"), fg.models.calls))
_RS = _copy.deepcopy(STUDIES)
_RS[0]["publication_types"] = ["Review", "Journal Article"]
t("steady: the same records in another order, repeated, or with their types listed in another order are the same",
  appmod._same_records(STUDIES, list(reversed(_copy.deepcopy(STUDIES))) + [STUDIES[0]])
  and appmod._same_records(_changed("publication_types", ["Journal Article", "Review"]), _RS)
  and appmod._same_records(STUDIES + [STUDIES[1]], STUDIES)
  and not appmod._same_records(STUDIES, STUDIES[:2])
  and not appmod._same_records(STUDIES, _changed("abstract", None)))
_lack = lambda pmid, title: {"pmid": pmid, "title": title, "publication_types": ["Journal Article"]}
t("steady: two different studies that each lack a field are never the same record, nor two copies of one",
  not appmod._same_records([_lack("A1", "One study")], [_lack("B2", "Another study")])
  and not appmod._same_records([_lack("A1", "One study")], [_lack("A1", "One study")])
  and not appmod._same_records(STUDIES[:1] + [_lack("A1", "One")], STUDIES[:1] + [_lack("B2", "Two")]))
_steady_row("Steady record check unchanged")
fg.models.script = []; fg.models.calls = 0
d = P("Steady record check unchanged", ip="7.7.10.7").get_json()
t("  and unchanged records keep the saved answer, with no model call",
  d["cached"] and d["tldr"] == "Saved short answer." and fg.models.calls == 0, (d.get("tldr"), fg.models.calls))

# _steady refuses a row with a problem the page cannot repair, even with the
# current stamp and the same records, and keeps nothing while the cache is off.
_SP = "My son Jake 7 gets sick from oat milk"
_steady_row(_SP)
t("steady: a row with a problem the page cannot repair is not kept, even with the same stamp and records",
  bool(appmod.display.saved_record_problems(_SP, "complicated", ["P1"], STUDIES, None, "q", "2026-01-01"))
  and appmod._steady(_SP, STUDIES) is None and _saved_row(_SP)["created_at"] == _OLD_DATE)
_SH = "Steady claim while the cache is switched off"
_steady_row(_SH)
_real_hours = appmod.VERDICT_CACHE_HOURS
appmod.VERDICT_CACHE_HOURS = 0
try:
    _off = appmod._steady(_SH, STUDIES)
finally:
    appmod.VERDICT_CACHE_HOURS = _real_hours
t("  with the cache switched off nothing is kept and the saved date is left alone",
  _off is None and _saved_row(_SH)["created_at"] == _OLD_DATE)
t("  and with it on, the same row is kept", appmod._steady(_SH, STUDIES) is not None)

_SR = "Steady claim saved in its own words"
_steady_row(_SR)
fg.models.script = []; fg.models.calls = 0
d = P("Is it true that steady claim saved in its own words?", ip="7.7.10.4").get_json()
t("steady: a reworded match on a stale row follows the same rule: kept under the saved wording, no model call",
  d["cached"] and d["claim"] == _SR and d["typed_reason"] == "wording" and fg.models.calls == 0
  and _saved_row(_SR)["created_at"] > _OLD_DATE, d)

# A database from before the column opens, gains it, and still serves.
import sqlite3 as _sq3  # noqa: E402
_old_db = os.path.join(tempfile.mkdtemp(), "old.db")
_oc = _sq3.connect(_old_db)
_oc.executescript("""CREATE TABLE verdict_cache (claim_key TEXT PRIMARY KEY, claim_text TEXT NOT NULL,
  search_query TEXT, verdict TEXT NOT NULL, tldr TEXT, explanation TEXT, still_open TEXT,
  breakdown_json TEXT, cited_json TEXT, studies_json TEXT, created_at TEXT NOT NULL);""")
_oc.execute("INSERT INTO verdict_cache VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("old database claim", "Old database claim", "(old) AND (db)", "complicated", "Old short.",
             "Mixed (Study 1).", "Old open.", None, json.dumps(["P1"]), json.dumps(STUDIES),
             appmod.datetime.now(appmod.timezone.utc).isoformat()))
_oc.commit(); _oc.close()
_real_db_path = db.DB_PATH
db.DB_PATH = _old_db
try:
    db.init_db()
    fg.models.calls = 0
    d = P("Old database claim", ip="7.7.10.5").get_json()
    _cols = {r[1] for r in _sq3.connect(_old_db).execute("PRAGMA table_info(verdict_cache)")}
finally:
    db.DB_PATH = _real_db_path
t("steady: an old database without the column opens, gains it, and serves its saved answer",
  "method_stamp" in _cols and d.get("cached") and d.get("tldr") == "Old short." and fg.models.calls == 0, d)
t("  and a row read from it has no stamp, so past the window it would be made again",
  db.get_cached_by_key("x") is None and appmod._steady("never saved claim", STUDIES) is None)

# Two processes starting at once never crash on the schema change: each runs
# init_db on the same 100 old scratch files at the same moments.
_RACE_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
import db
paths, t0, gap = sys.argv[4].split("|"), float(sys.argv[2]), float(sys.argv[3])
bad = 0
for i, p in enumerate(paths):
    while time.time() < t0 + i * gap:
        pass
    db.DB_PATH = p
    try:
        db.init_db()
    except Exception as e:
        bad += 1
        print(type(e).__name__, e, file=sys.stderr)
print(bad)
"""
_race_dir = tempfile.mkdtemp()
_race_paths = []
for _i in range(100):
    _rp = os.path.join(_race_dir, f"old{_i}.db"); _race_paths.append(_rp)
    _rc = _sq3.connect(_rp)
    _rc.executescript("""CREATE TABLE verdict_cache (claim_key TEXT PRIMARY KEY, claim_text TEXT NOT NULL,
      search_query TEXT, verdict TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE checks (id INTEGER PRIMARY KEY, claim_text TEXT, verdict TEXT, checked_at TEXT);""")
    _rc.commit(); _rc.close()
_race_env = {k: v for k, v in os.environ.items() if not k.startswith("TURSO_")}
import time as _time  # noqa: E402
_race_t0 = _time.time() + 3
_race = [_subprocess.Popen([sys.executable, "-c", _RACE_CHILD, str(ROOT), str(_race_t0), "0.06", "|".join(_race_paths)],
                           stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True, env=_race_env)
         for _ in range(2)]
_race_out = [p.communicate(timeout=120) for p in _race]
_race_bad = sum(int(o[0].strip() or 99) for o in _race_out)
t("schema: two processes starting at once on the same 100 old files never crash on adding a column",
  _race_bad == 0 and all(p.returncode == 0 for p in _race), [o[1][:200] for o in _race_out])
t("  and every file ends with every column",
  all({"tldr", "still_open", "breakdown_json", "method_stamp"} <= {r[1] for r in _sq3.connect(_rp).execute(
      "PRAGMA table_info(verdict_cache)")} for _rp in _race_paths))


class _AlterConn:
    """A connection whose ALTER is refused; the column is or is not there after."""
    def __init__(self, err, there):
        self.err, self.there = err, there

    def execute(self, sql, params=()):
        if sql.startswith("ALTER"):
            raise self.err
        return _Rows([{"name": "x"}] + ([{"name": "tldr"}] if self.there else []))


_Rows = db._Rows
_alter = []
for _err in (_sq3.OperationalError("duplicate column name: tldr"), db.TursoError("statement rejected")):
    db._add_column(_AlterConn(_err, True), "verdict_cache", "tldr")
    try:
        db._add_column(_AlterConn(_err, False), "verdict_cache", "tldr"); _alter.append(False)
    except type(_err):
        _alter.append(True)
t("  either driver's refusal counts as done when the column is there, and is raised when it is not",
  _alter == [True, True], _alter)

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
# The proof is the saved answer itself, read from the database by its key,
# so it is seeded here the way a real check would have saved it.
_EXS = [_study("EX1", "Vinegar and fasting glucose: a meta-analysis", types=["Meta-Analysis"]),
        _study("EX2", "Vinegar with meals in type 2 diabetes", types=["Randomized Controlled Trial"]),
        _study("EX3", "Vinegar and insulin in rats", types=["Journal Article"])]
db.put_cached_verdict(appmod.EXAMPLE_CLAIM, "apple cider vinegar AND diabetes mellitus", "complicated",
                      "Studies 1 and 2 show lower blood sugar; none shows a cure.", ["EX1", "EX2"], _EXS,
                      tldr="It can lower blood sugar, but nothing shows it cures diabetes.",
                      still_open="Long trials of a cure are missing.")
_home = c.get("/").get_data(as_text=True)
_trust = _home.split('<section id="trust"')[1].split("</section>")[0] if '<section id="trust"' in _home else ""
_rows = _trust.split('<div class="trust-row">')[1:]
_qs = [_html.unescape(_re.sub(r"<[^>]+>", "", r.split("</h3>")[0])).strip() for r in _rows]
t("trust: the home page answers the five questions a stranger asks, and shows a real one",
  _qs[:5] == ["Where do the studies come from?", "Is an AI making this up?",
              "Is it biased? Who made it?", "Is it free?", "Is this medical advice?"]
  and "Can I see a real one?" in _qs, _qs)
t("  the answers carry no repeated links back to the claim box",
  len(_rows) >= 6 and not any("#claim-input" in r for r in _rows))
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
t("  the real check is the saved one, linked as a result page",
  _ex and f'href="{_ex["href"]}"' in _trust and _ex["href"] == "/?q=" + urllib.parse.quote(appmod.EXAMPLE_CLAIM)
  and "The footage at the top" not in _trust_text, _ex)
_ex_row = db.get_cached_by_key(db.normalize_claim(appmod.EXAMPLE_CLAIM))
t("  its claim, verdict, short answer, date and count are the saved row's, not a frozen file's",
  _ex and _ex["claim"] == _ex_row["claim_text"] and _ex["verdict"] == "complicated"
  and _ex["tldr"] == _ex_row["tldr"] and _ex["read"] == 3
  and _ex["checked"] == (lambda d: f"{d.day} {d.strftime('%B %Y')}")(appmod.datetime.fromisoformat(_ex_row["cached_at"]))
  and "check.json" not in _inspect.getsource(appmod._trust_example), _ex)
t("  the frozen file and the script that wrote it are gone from the repo",
  not (ROOT / "static" / "flight" / "check.json").exists() and not (ROOT / "scripts" / "export_flight_check.py").exists())
# It goes through the Latest list's own path: a stored "true" whose only cited
# study is off topic shows the same capped verdict in both places.
_EXO = [dict(_EXS[0], off_topic=verdict.OFF_OUTCOME), _EXS[1]]
db.put_cached_verdict(appmod.EXAMPLE_CLAIM, "apple cider vinegar AND diabetes mellitus", "true",
                      "Study 1 shows a cure.", ["EX1"], _EXO, tldr="It cures diabetes.", still_open="Doses vary.")
_ex2 = appmod._trust_example()
_ex2_home = c.get("/").get_data(as_text=True)
_ex2_latest = [x for x in _ex2_home.split('<ol class="ledger-list">')[1].split("</ol>")[0].split('<li class="entry">')
               if appmod.EXAMPLE_CLAIM.lower() in _html.unescape(x).lower()]
_ex2_proof = _re.search(r'<p class="proof-verdict">([^<]*)</p>', _ex2_home)
t("  the proof and the Latest list show the same verdict, through the same listing path",
  _ex2 and _ex2["verdict"] == "insufficient" and _ex2_proof and _ex2_latest
  and _ex2_proof.group(1) == appmod.VERDICT_LABELS["insufficient"]
  and f'<span class="entry-verdict">{appmod.VERDICT_LABELS["insufficient"]}</span>' in _ex2_latest[0]
  and _ex2["tldr"] == verdict.UNTESTED_TLDR, (_ex2, _ex2_proof and _ex2_proof.group(1), _ex2_latest))
# Missing or incomplete, the section renders without it.
db.put_cached_verdict(appmod.EXAMPLE_CLAIM, "apple cider vinegar AND diabetes mellitus", "complicated",
                      "x", [], [], tldr="A short answer.", still_open="Open.")
t("  a saved row with no studies is not offered as proof", appmod._trust_example() is None)
db.put_cached_verdict(appmod.EXAMPLE_CLAIM, "apple cider vinegar AND diabetes mellitus", "complicated",
                      "x", ["EX1"], _EXS, tldr="A short answer.", still_open="")
_ex_open = c.get("/").get_data(as_text=True)
t("  nor one with no still-open line", appmod._trust_example() is None
  and "Can I see a real one?" not in _ex_open)
t("  and the Latest list, on the same path, does not show that row as a finished answer either",
  appmod.EXAMPLE_CLAIM[1:] not in _ex_open.split('<ol class="ledger-list">')[1].split("</ol>")[0]
  if '<ol class="ledger-list">' in _ex_open else True)
with db.get_conn() as _exc:
    _exc.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(appmod.EXAMPLE_CLAIM),))
t("  nor a missing one", appmod._trust_example() is None and "Can I see a real one?" not in c.get("/").get_data(as_text=True))
_fx_src = (ROOT / "scripts" / "ui_fixture_server.py").read_text()
t("  the local fixture page seeds the example claim, so its screenshots show the proof",
  f'    "{appmod.EXAMPLE_CLAIM}",' in _fx_src.split("SEED = [")[1].split("]")[0])
db.put_cached_verdict(appmod.EXAMPLE_CLAIM, "apple cider vinegar AND diabetes mellitus", "complicated",
                      "Studies 1 and 2 show lower blood sugar; none shows a cure.", ["EX1", "EX2"], _EXS,
                      tldr="It can lower blood sugar, but nothing shows it cures diabetes.",
                      still_open="Long trials of a cure are missing.")
_checks_r = c.get("/checks")
t("  /checks is gone: it sends the reader to the home page, which points at no footage that is gone",
  _checks_r.status_code == 301 and _checks_r.headers["Location"].endswith("/")
  and '<section id="trust"' in _home and "The footage at the top" not in _home, _checks_r.headers.get("Location"))
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
t("  the code screen misreads 'just a rough guide' as a plain yes",
  breakdown.words_stamp("complicated", _GUIDE, _WATER) == "true")
fg.models.calls = 0
fg.models.script = [_GUIDE_R, DIR("no"), _GUIDE_R]
_v = verdict.weigh_evidence(_WATER, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  so the small call decides, and the stamp becomes 'Likely false', never 'Likely true'",
  _v["verdict"] == "false" and _v["tldr"] == _GUIDE and fg.models.calls == 3, _v)
fg.models.calls = 0
fg.models.script = [_GUIDE_R, DIR("mixed")]
_v = verdict.weigh_evidence(_WATER, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  the small call is told a fixed rule that 'varies' or is 'a rough guide' is answered no",
  "only a rough guide or rule of thumb" in verdict._direction_prompt(_WATER, _GUIDE)
  and "there is not one" in verdict._direction_prompt(_WATER, _GUIDE))
t("  a reading that agrees with the stamp means a false alarm: no retry, nothing changes",
  _v["verdict"] == "complicated" and fg.models.calls == 2, (_v, fg.models.calls))
fg.models.calls = 0
fg.models.script = [_GUIDE_R, Resp("not json")]
_v = verdict.weigh_evidence(_WATER, [dict(x) for x in _WATER_S], "(water) AND (hydration)")
t("  and a reading that fails changes nothing either",
  _v["verdict"] == "complicated" and _v["tldr"] == _GUIDE and fg.models.calls == 2, (_v, fg.models.calls))
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
  _el("false", _ACV, ["A1", "A2"], _ACV_CLAIM, None, "cure") == "Strong evidence, and none of it shows a cure"
  and _el("false", _ACV, ["A1", "A2"], _ACV_CLAIM, "small", "cure") == "Strong evidence of a small effect, not a cure"
  and _el("true", _ACV, ["A1", "A2"], "Vinegar lowers blood sugar", "moderate")
  == "Strong evidence for this claim, a moderate effect"
  and _el("false", _ACV, ["A1"], "Vinegar raises blood sugar") == "Strong evidence against this claim"
  and _el("complicated", _ACV, ["A1"], "Vinegar lowers blood sugar in diabetes", "small")
  == "Strong evidence of a small effect, but no plain yes or no")
# A false answer to a promise with no size: the line asserts no effect
# nobody measured, and keeps its level word.
_el_ns = [evidence.evidence_line(verdict_v, _ACV, ["A1", "A2"], c, lab, "cure") for verdict_v, c, lab in
          (("false", "Homeopathy cures cancer", None), ("false", "Garlic cures HIV", ""),
           ("false", "Ivermectin cures COVID-19", "unclear"))]
t("  a false answer to a promise with no size says none of the evidence shows the promise, never 'partial'",
  _el_ns == ["Strong evidence, and none of it shows a cure"] * 3
  and evidence.evidence_line("false", _ACV, ["A1", "A2"], _ACV_CLAIM, "moderate", "cure") == "Strong evidence of a moderate effect, not a cure"
  and evidence.evidence_line("false", _ACV, ["A1", "A2"], _ACV_CLAIM, None, "cure", withheld=True) == "Strong evidence, and none of it shows a cure"
  and evidence.evidence_line("false", _ACV, ["A1", "A2"], _ACV_CLAIM, "none", "cure") == "Strong evidence of no clear effect", _el_ns)
t("  it names the one group, says low certainty, and is empty with nothing used",
  _el("complicated", _MEASLES, ["M1", "M2"], "Vitamin D stops colds") == "Strong evidence, but only in children"
  and _el("complicated", [_study("L1", "Pilot trial of vinegar", abstract="A pilot trial in 12 adults.")],
          ["L1"], "Vinegar lowers blood sugar").startswith("Low certainty evidence")
  and _el("insufficient", _ACV, [], _ACV_CLAIM) == "" and _el("false", _ACV, [], _ACV_CLAIM) == "")
_rec = (ROOT / "static" / "app.js").read_text().split("function confidenceLine")[1].split("\nfunction ")[0]
t("  the confidence line prints it, and no longer calls the designs 'the evidence'",
  "data.evidence_line" in (ROOT / "static" / "app.js").read_text()
  and "the evidence is <mark>" not in _rec and "data.evidence_line" in _rec)
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in _ACV]
fg.models.script = [Resp("(vinegar) AND (diabetes)"),
                    Resp(VJ("complicated", [1, 2], tldr=_ACV_TLDR,
                            evidence=["Vinegar lowered fasting blood sugar (Study 1)."]))]
_d = P("Apple cider vinegar cures type 2 diabetes", ip="9.9.3.2").get_json()
t("  a real check carries it in the payload, under the 'Likely false' it explains",
  _d["verdict"] == "false" and _d["evidence_line"] == "Strong evidence, and none of it shows a cure", _d)
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
  == "Study 1 showed drops in fasting blood sugar with an average difference of about -22 mg/dL."
  and _fr("Fasting blood sugar was reduced by -21.929 mg/dL (95% CI: -29.19, -14.67) (Study 1). HbA1c "
          "was reduced by a weighted mean difference of -1.53 (95% CI: -2.65, -0.41) (Study 1).", _ACV_R)
  == "Fasting blood sugar was reduced by about 22 mg/dL (likely range: 29, 15) (Study 1)."
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
t("  weak or low certainty evidence is the reason for no plain answer, so the line says 'so', never 'but'",
  evidence.evidence_line("complicated", [_VLREV], ["L2"], _GIN_CLAIM, outcome="(vomiting)")
  == "Very low certainty evidence, so no plain yes or no"
  and evidence.evidence_line("complicated", [_LOWREV], ["L1"], _GIN_CLAIM, "small", outcome="(vomiting)")
  == "Low certainty evidence of a small effect, so no plain yes or no"
  and evidence.evidence_line("complicated", [_gin_review("L5", "Ten trials.", types=["Case Reports"])], ["L5"],
                             _GIN_CLAIM) == "Weak evidence, so no plain yes or no"
  and evidence.evidence_line("complicated", [_PLAIN], ["L4"], _GIN_CLAIM, outcome="(vomiting)")
  == "Strong evidence, but no plain yes or no",
  [evidence.evidence_line("complicated", [x_], [x_["pmid"]], _GIN_CLAIM, outcome="(vomiting)") for x_ in (_VLREV, _PLAIN)])
t("  the evidence line never says Strong when the lead review says low or very low",
  evidence.evidence_line("complicated", [_LOWREV], ["L1"], _GIN_CLAIM, outcome="(vomiting)")
  == "Low certainty evidence, so no plain yes or no"
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
                         effect_label="none") == "Moderate evidence of no clear effect"
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
  == "Low to moderate certainty evidence for this claim")
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
  == "LDL fell by 0.33 mmol/L (likely range 0.38 to 0.27) (Study 1).")
t("  but a range that crosses zero keeps every sign, the fall's too",
  breakdown.house_style("Pain fell by -1.2 points (95% CI -2.0 to 0.4) (Study 2).")
  == "Pain fell by -1.2 points (likely range -2.0 to 0.4) (Study 2).")
t("  a fall written without its sign loses the signs on its range too, unless it crosses zero",
  breakdown.house_style("Fasting blood sugar dropped by about 22 mg/dL (likely range -29 to -15) (Study 2).")
  == "Fasting blood sugar dropped by about 22 mg/dL (likely range 29 to 15) (Study 2)."
  and breakdown.house_style("Pain fell by 1.2 points (likely range -2.0 to 0.4) (Study 2).")
  == "Pain fell by 1.2 points (likely range -2.0 to 0.4) (Study 2)."
  and breakdown.house_style("Weight fell by 2 kg (Study 3).") == "Weight fell by 2 kg (Study 3).")
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
  "{%- if bv.effect_size %}" in (ROOT / "templates" / "index.html").read_text()
  and "(bv.effect_size\n        ? `<p class=\"effect\">" in (ROOT / "static" / "app.js").read_text())

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
  "colds, which are caused by viruses. Check treatment decisions with a doctor." and _fl["cited_studies"] == ["G1", "G2"]
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

# ---- G1: the guidance lead is code's alone ----
# A model takeaway that opens with "Major health guidelines say:" was read
# as code's own, so check_faithful let it pass and the guidance line came
# with it. verdict.without_lead takes it off in _read_verdict, which the
# plain and the streamed check both reach.
t("G1: without_lead takes the model's own lead off, in any spacing or case, and gives the capital back",
  verdict.without_lead(verdict.GUIDELINE_LEAD + "stop your arthritis medicine.") == "Stop your arthritis medicine."
  and verdict.without_lead("major  HEALTH guidelines say : that turmeric works.") == "Turmeric works."
  and verdict.without_lead("  Guidelines say: it is not safe.") == "It is not safe."
  and verdict.without_lead("Turmeric eases joint pain.") == "Turmeric eases joint pain.")
_G1B_KEEP = ["Guidelines say that adults should get 150 minutes of exercise.",
             "Guidelines say adults should get 150 minutes of exercise.",
             "Major health guidelines say that adults should get 150 minutes of exercise.",
             "health guidelines say adults need sleep."]
t("G1b: only the lead with its colon is taken off; a takeaway that says what guidelines say stays byte for byte",
  [verdict.without_lead(x) for x in _G1B_KEEP] == _G1B_KEEP, [verdict.without_lead(x) for x in _G1B_KEEP])
# H2: a lead after quotes, dashes, bold or italic marks or a zero-width
# character, or written with "state(s):", is taken off too, with no mark
# of its opening left dangling.
_H2_FORMS = ['"Major health guidelines say: stop your arthritis medicine."',
             "\u201cMajor health guidelines say: stop your arthritis medicine.\u201d",
             "- Major health guidelines say: stop your arthritis medicine.",
             "\u2014 Guidelines say: stop your arthritis medicine.",
             "**Major health guidelines say:** stop your arthritis medicine.",
             "**Major health guidelines say: stop your arthritis medicine.**",
             "*Guidelines say:* stop your arthritis medicine.",
             "\u200bMajor health guidelines say: stop your arthritis medicine.",
             "Major health guidelines state: stop your arthritis medicine.",
             "Health guidelines states: stop your arthritis medicine."]
t("H2: each of the reviewer's forms loses the lead and keeps the sentence, capital back",
  [verdict.without_lead(x) for x in _H2_FORMS] == ["Stop your arthritis medicine."] * len(_H2_FORMS),
  [verdict.without_lead(x) for x in _H2_FORMS])
t("  and a takeaway that only reports what guidelines say, or quotes after the lead, keeps its own marks",
  verdict.without_lead("Guidelines say that X helps.") == "Guidelines say that X helps."
  and verdict.without_lead('Major health guidelines say: "the drug" works.') == '"the drug" works.')
def _g1_run(claim, tldr, ip, v="true", way=P):
    with db.get_conn() as _conn:
        _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(claim),))
        _conn.execute("DELETE FROM query_cache")
    fg.models.script = [Resp("q"), Resp(VJ(v, [1], tldr=tldr))]
    if way is P:
        return P(claim, ip=ip).get_json()
    _evs = stream(claim, ip=ip)[1]
    return _evs[-1].get("result") or _evs[-1]
def _g1_same(a, b):
    return (a["verdict"], a["evidence_line"]) == (b["verdict"], b["evidence_line"])
_G1_CHERRY = "Tart cherry juice speeds muscle recovery"
_g1_lead = _g1_run(_G1_CHERRY, verdict.GUIDELINE_LEAD + "tart cherry juice speeds recovery.", "9.7.1.1")
_g1_bare = _g1_run(_G1_CHERRY, "Tart cherry juice speeds recovery.", "9.7.1.2")
t("G1: a model takeaway that opens with the lead, on a claim no guideline covers, loses the lead",
  "guidelines say" not in _g1_lead["tldr"].lower() and _g1_lead["evidence_line"] != verdict.GUIDANCE_LINE,
  _g1_lead)
t("  and its stamp and evidence line are what the same reply gives without those words",
  _g1_same(_g1_lead, _g1_bare), (_g1_lead["verdict"], _g1_lead["evidence_line"],
                                 _g1_bare["verdict"], _g1_bare["evidence_line"]))
_g1_slead = _g1_run(_G1_CHERRY, verdict.GUIDELINE_LEAD + "tart cherry juice speeds recovery.", "9.7.1.3", way=stream)
t("  and the streamed check reads it the same way",
  "guidelines say" not in _g1_slead["tldr"].lower() and _g1_same(_g1_slead, _g1_bare),
  (_g1_slead["tldr"], _g1_slead["verdict"], _g1_slead["evidence_line"]))
_G1_ARTH = "Stop your arthritis medicine and take turmeric"
_g1_arth = _g1_run(_G1_ARTH, verdict.GUIDELINE_LEAD + "stop your arthritis medicine and take turmeric.", "9.7.1.4")
_g1_arth_bare = _g1_run(_G1_ARTH, "Stop your arthritis medicine and take turmeric.", "9.7.1.5")
t("G1: the reviewer's arthritis and turmeric reply no longer keeps 'true' and the guidance line because of the lead",
  _g1_arth["evidence_line"] != verdict.GUIDANCE_LINE and "guidelines say" not in _g1_arth["tldr"].lower()
  and _g1_same(_g1_arth, _g1_arth_bare),
  (_g1_arth["verdict"], _g1_arth["evidence_line"], _g1_arth_bare["verdict"], _g1_arth_bare["evidence_line"]))
verdict.guideline_view = _guide("false", "antibiotics do not work on colds, which are caused by viruses.")
_g1_real = _g1_run("Antibiotics cure the common cold",
                   verdict.GUIDELINE_LEAD + "antibiotics cure colds.", "9.7.1.6", v="complicated")
verdict.guideline_view = lambda claim: None
t("G1: a real guidance answer still carries the lead once, never twice",
  _g1_real["verdict"] == "false" and _g1_real["tldr"].startswith(verdict.GUIDELINE_LEAD)
  and _g1_real["tldr"].lower().count("guidelines say") == 1, _g1_real["tldr"])

# ---- Under an answer the guidance rule set, nothing says the claim cannot be rated ----
# The live safety run: "type 1 diabetes and insulin" and "antibiotics and the
# cold" were False from guidance, with the model's "so there isn't enough
# evidence to rate it" still under the stamp.
_UNRATED = re.compile(r"enough evidence[^.]*\bto rate\b|\bcannot be rated\b|\bto rate (?:it|this claim)\b", re.I)
_GU_STUDIES = [_study("GU1", "Antibiotic prescribing for colds", types=["Editorial"],
                      abstract="Antibiotics should not be used for the common cold, which viruses cause."),
               _study("GU2", "Respiratory infections in general practice", types=["Review"],
                      abstract="Recovery from colds is usually spontaneous without antibiotics.")]
_GU_TLDR = verdict.GUIDELINE_LEAD + "antibiotics do not cure colds. Check treatment decisions with a doctor."
_GU_EXPL = ("Antibiotics should not be used for the common cold (Study 1). Recovery is usually spontaneous "
            "(Study 2). Nothing here tests this claim in people, only opinion pieces, so there isn't "
            "enough evidence to rate it.")
_GU_BD = {"parts": [], "evidence": ["Antibiotics should not be used for the common cold (Study 1).",
                                    "No trial here tests the claim, so there isn't enough evidence to rate it (Study 2)."],
          "says": ["Antibiotics should not be used for the common cold (Study 1)."],
          "effect_size": "", "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": "", "effect": None}
def _gu_seen(r):
    return " ".join([r["tldr"], r["explanation"], r["still_open"], r["evidence_line"] or ""]
                    + breakdown.prose_of(r["breakdown"]))
with appmod.app.test_request_context("/"):
    _gu = appmod._check_response("Antibiotics cure the common cold", "(antibiotics) AND (common cold)", "false",
                                 _GU_TLDR, _GU_EXPL, ["GU1", "GU2"], [dict(s) for s in _GU_STUDIES], "http://x",
                                 still_open="Whether a trial would settle it, since there isn't enough evidence to rate it.",
                                 breakdown=dict(_GU_BD))
    _gu_none = appmod._check_response("People with type 1 diabetes can replace insulin with a strict diet",
                                      "(diet) AND (type 1 diabetes)", "false",
                                      verdict.GUIDELINE_LEAD + "people with type one diabetes need insulin. "
                                      "Stopping a prescribed medicine without a doctor's advice can be risky.",
                                      "None of the studies found directly test this claim, so there isn't "
                                      "enough evidence to rate it.", [], [dict(s) for s in _GU_STUDIES], "http://x",
                                      still_open="Whether diet changes insulin needs.", breakdown=None)
    _gu_plain = appmod._check_response("Antibiotics cure the common cold", "(antibiotics) AND (common cold)",
                                       "insufficient", verdict.UNTESTED_TLDR, _GU_EXPL, ["GU1", "GU2"],
                                       [dict(s) for s in _GU_STUDIES], "http://x", still_open="Whether it works.",
                                       breakdown=dict(_GU_BD))
t("guidance prose: under an answer the guidance rule set, no line says the claim cannot be rated",
  _gu["verdict"] == "false" and not _UNRATED.search(_gu_seen(_gu))
  and _gu_none["verdict"] == "false" and not _UNRATED.search(_gu_seen(_gu_none)),
  (_gu_seen(_gu), _gu_seen(_gu_none)))
t("  what the studies did and did not test stays, and the fixed line says what the answer rests on",
  _gu["explanation"] == ("Antibiotics should not be used for the common cold (Study 1). Recovery is usually "
                         "spontaneous (Study 2). Nothing here tests this claim in people, only opinion pieces. "
                         + verdict.GUIDANCE_RESTS)
  and _gu_none["explanation"] == "None of the studies found directly test this claim. " + verdict.GUIDANCE_RESTS
  and verdict.GUIDANCE_RESTS == "This answer rests on what major health guidelines say, not on these studies."
  and "No trial here tests the claim (Study 2)." in " ".join(_gu["breakdown"]["evidence"])
  and _gu["still_open"] == "Whether a trial would settle it.", (_gu["explanation"], _gu["breakdown"], _gu["still_open"]))
t("  and an answer the guidance rule did not set keeps its own words",
  _gu_plain["verdict"] == "insufficient" and _gu_plain["explanation"].endswith("enough evidence to rate it.")
  and verdict.GUIDANCE_RESTS not in _gu_seen(_gu_plain), _gu_plain["explanation"])
db.put_cached_verdict("People with type 1 diabetes can replace insulin with a strict diet",
                      "(diet) AND (type 1 diabetes)", "false",
                      "None of the studies found directly test this claim, so there isn't enough evidence to rate it.",
                      [], [dict(s) for s in _GU_STUDIES],
                      tldr=verdict.GUIDELINE_LEAD + "people with type one diabetes need insulin.",
                      still_open="Whether diet changes insulin needs.")
_gu_page = c.get("/?q=" + urllib.parse.quote("People with type 1 diabetes can replace insulin with a strict diet")
                 ).get_data(as_text=True)
t("  and the saved answer's page, its markup and its short answer say the same",
  'id="server-result"' in _gu_page and verdict.GUIDANCE_RESTS in _gu_page and not _UNRATED.search(_gu_page),
  [m.group(0) for m in _UNRATED.finditer(_gu_page)])

# H6: a guidance answer that relies on nothing cites no paper for a finding.
# Live, "Antibiotics cure the common cold": "Studies show ... (Study 5)" sat
# four sentences before "Nothing here tests this claim in people".
_H6_ST = [_study("H6A", "Antibiotics for colds: a commentary", types=["Editorial"],
                 abstract="Antibiotics should not be used for the common cold, which viruses cause."),
          _study("H6B", "Why we should not prescribe antibiotics for colds", types=["Comment"],
                 abstract="Recovery from colds is usually spontaneous without antibiotics.")]
_H6_SAID = ("Studies show that antibiotics should not be used for the common cold (Study 1). "
            "Recovery is usually spontaneous without antibiotics (Study 2).")
_h6_search = pubmed.search_and_fetch
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in _H6_ST]
verdict.guideline_view = _guide("false", "antibiotics do not work on colds, which are caused by viruses.")
_h6_runs = []
for _h6_n, _h6_way in enumerate((P, stream)):
    with db.get_conn() as _conn:
        _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_GU_CLAIM_H6 := "Antibiotics cure the common cold"),))
        _conn.execute("DELETE FROM query_cache")
    fg.models.script = [Resp("(antibiotics) AND (common cold)"),
                        Resp(VJ("false", [1, 2], tldr=_GU_TLDR, explanation=_H6_SAID))]
    _h6_runs.append(P(_GU_CLAIM_H6, ip="9.7.6.1").get_json() if _h6_way is P
                    else (stream(_GU_CLAIM_H6, ip="9.7.6.2")[1][-1].get("result") or {}))
verdict.guideline_view = lambda claim: None
pubmed.search_and_fetch = _h6_search
t("H6: a guidance answer relying on no study, plain and streamed, keeps no sentence citing a paper",
  all(r_.get("verdict") == "false" and r_.get("evidence_line") == verdict.GUIDANCE_LINE
      and not any(s_["cited_in_verdict"] for s_ in r_["studies"])
      and not breakdown.refs(r_["explanation"], 2) and r_["explanation"].endswith(verdict.GUIDANCE_RESTS)
      for r_ in _h6_runs), [(r_.get("verdict"), r_.get("explanation")) for r_ in _h6_runs])
# The live shapes, each as the page gets it.
_H6_OFF = dict(_study("H6O", "Antibiotic resistance in farm animals", types=["Review"]),
               off_topic="tests something other than what this claim is about.")
_H6_LIVE = ("Studies show that antibiotics should not be used for the common cold since viruses cause these "
            "infections (Study 2). Recovery from these viral infections is usually spontaneous without antibiotics "
            "(Study 3). Nothing here tests this claim in people, only opinion pieces and papers that test nothing "
            "in people. " + verdict.GUIDANCE_RESTS)
_H6_MILK = ("Studies show pasteurization reduces certain vitamins like B1, B2, C, and folate, while raw milk retains "
            "them (Study 1). Other studies show raw milk consumption poses significant risks of bacterial zoonoses "
            "(Study 2).")
_H6_OILS = ("No studies test this exact claim in people to see if a cure occurs. The available papers are reviews "
            "or background tests that do not provide human clinical trial data (Studies 1, 2).")
_H6_MS = [_study("H6M1", "Effect of pasteurization on milk vitamins: a meta-analysis", types=["Meta-Analysis"],
                 abstract="Pasteurization reduces vitamins B1, B2, C, and folate in milk, while raw milk retains them."),
          _study("H6M2", "Raw milk and bacterial zoonoses: a systematic review", types=["Systematic Review"],
                 abstract="Raw milk consumption poses significant risks of bacterial zoonoses.")]
with appmod.app.test_request_context("/"):
    _h6_cold = appmod._check_response("Antibiotics cure the common cold", "(antibiotics) AND (common cold)", "false",
                                      _GU_TLDR, _H6_LIVE, [], [dict(_H6_OFF)] + [dict(s) for s in _H6_ST],
                                      "http://x", still_open="Whether a better treatment exists.")
    _h6_milk = appmod._check_response("Raw milk is healthier than pasteurized milk", "(raw milk) AND (health)", "false",
                                      verdict.GUIDELINE_LEAD + "raw milk can carry harmful germs.", _H6_MILK,
                                      ["H6M1", "H6M2"], [dict(s) for s in _H6_MS], "http://x",
                                      still_open="Whether any benefit outweighs the risk.")
    _h6_oils = appmod._check_response("Essential oils cure bacterial infections", "(essential oils) AND (infection)",
                                      "insufficient", verdict.UNTESTED_TLDR, _H6_OILS, [],
                                      [dict(s) for s in _H6_ST], "http://x", still_open="Whether oils help in people.")
t("H6: the live antibiotics shape keeps only code's own sentences about the papers",
  _h6_cold["explanation"] == ("Nothing here tests this claim in people, only opinion pieces and papers that test "
                              "nothing in people. " + verdict.GUIDANCE_RESTS), _h6_cold["explanation"])
t("  a guidance answer that relies on studies keeps every sentence",
  _h6_milk["evidence_line"] == verdict.GUIDANCE_LINE and _h6_milk["explanation"] == _H6_MILK, _h6_milk["explanation"])
t("  and a Not enough evidence answer keeps its sentence describing the background papers",
  _h6_oils["verdict"] == "insufficient" and _h6_oils["explanation"] == _H6_OILS, _h6_oils["explanation"])
# J2: the folded sections of that answer cite no study for a finding either.
_J2_BD = {"parts": [{"part": "Antibiotics cure colds", "assessment": "Antibiotics should not be used (Study 2).",
                     "studies": [2]},
                    {"part": "Colds are viral", "assessment": "Colds are caused by viruses.", "studies": []}],
          "evidence": ["Antibiotics should not be used (Study 2). Viruses cause colds (Study 2).",
                       "Recovery is usually spontaneous (Study 3).", "Guidelines advise against antibiotics for colds."],
          "effect_size": "", "strength": "Reviews agree antibiotics are ineffective (Study 2).",
          "applies_to": "Patients with colds (Study 2).", "not_applies_to": "", "unknowns": "Timing varies (Study 3)."}
_J2_MILK_BD = {"parts": [], "evidence": [_H6_MILK], "effect_size": "", "strength": "Two reviews (Studies 1, 2).",
               "applies_to": "", "not_applies_to": "", "unknowns": ""}
with appmod.app.test_request_context("/"):
    _j2 = appmod._check_response("Antibiotics cure the common cold", "(antibiotics) AND (common cold)", "false",
                                 _GU_TLDR, _H6_LIVE, [], [dict(_H6_OFF)] + [dict(s) for s in _H6_ST],
                                 "http://x", still_open="Whether a better treatment exists.", breakdown=json.loads(json.dumps(_J2_BD)))
    _j2_milk = appmod._check_response("Raw milk is healthier than pasteurized milk", "(raw milk) AND (health)", "false",
                                      verdict.GUIDELINE_LEAD + "raw milk can carry harmful germs.", _H6_MILK,
                                      ["H6M1", "H6M2"], [dict(s) for s in _H6_MS], "http://x",
                                      still_open="Whether any benefit outweighs the risk.", breakdown=json.loads(json.dumps(_J2_MILK_BD)))
_j2_b = _j2["breakdown"]
t("J2: under a guidance answer relying on no study, no part, paragraph or field cites a study",
  not any(breakdown.refs(x, 3) for x in breakdown.prose_of(_j2_b)), breakdown.prose_of(_j2_b))
t("  a part whose answer went is dropped, not pointed at paragraphs below; uncited prose stays; empty fields are blank",
  [p_["assessment"] for p_ in _j2_b["parts"]] == ["Colds are caused by viruses."]
  and _j2_b["evidence"] == ["Guidelines advise against antibiotics for colds."]
  and _j2_b["strength"] == "" and _j2_b["applies_to"] == "" and _j2_b["unknowns"] == "" and _j2_b["concluded"] == [],
  _j2_b)
t("  and a guidance answer that relies on studies keeps its folded sections citing them",
  _j2_milk["breakdown"]["evidence"] == [_H6_MILK] and _j2_milk["breakdown"]["strength"] == "Two reviews (Studies 1, 2).",
  _j2_milk["breakdown"])

# ---- final fix F2: the evidence line sizes only a figure about the outcome ----
# breakdown.line_label lends the line a size only when the figure's sentence
# is about the claim's outcome, not one that merely contains the word.
def _f2_bd(said, figure, plain, label="large"):
    return {"effect_size": said, "evidence": [], "effect": {"figure": figure, "label": label, "plain": plain}}
_F2_MP = ("Statins cause muscle pain", "(muscle pain OR myalgia)")
t("F2a: a sentence that sets the outcome aside ('not muscle') lends the line no size",
  breakdown.line_label(_f2_bd("Pain in the joints, not muscle, rose, RR 3.0.", "RR 3.0", "about 3 times higher risk"),
                       *_F2_MP) is None)
t("F2a: nor one that puts it 'rather than' or 'instead of' the thing measured",
  breakdown.line_label(_f2_bd("Joint pain rather than muscle pain rose, RR 3.0.", "RR 3.0", "about 3 times higher risk"),
                       *_F2_MP) is None
  and breakdown.line_label(_f2_bd("Headache instead of myalgia rose, RR 3.0.", "RR 3.0", "about 3 times higher risk"),
                           *_F2_MP) is None)
t("F2a: a sentence about the outcome itself still sizes it, a 'did not' before the verb included",
  breakdown.line_label(_f2_bd("Muscle pain rose, RR 3.0.", "RR 3.0", "about 3 times higher risk"), *_F2_MP) == "large"
  and breakdown.line_label(_f2_bd("Statins did not raise muscle pain, RR 1.02.", "RR 1.02", "", label="none"),
                           *_F2_MP) == "none")

_F2_TG = ("Fish oil lowers triglycerides", "(triglycerides)")
t("F2b: a sentence where the outcome only describes another noun ('triglyceride-lowering drugs') lends no size",
  breakdown.line_label(_f2_bd("Triglyceride-lowering drugs used 30% more.", "30%", "about 30% lower"), *_F2_TG) is None)
t("F2b: a sentence that also names the outcome itself still sizes it",
  breakdown.line_label(_f2_bd("Triglycerides fell by 30% more.", "30%", "about 30% lower"), *_F2_TG) == "large"
  and breakdown.line_label(_f2_bd("Triglycerides fell 30% more than with triglyceride-lowering drugs.", "30%",
                                  "about 30% lower"), *_F2_TG) == "large")

_F2_ST = ("Ashwagandha reduces stress", "(stress)")
_F2_SMD = ("SMD -1.75", "a difference of 1.75 standard deviations")
t("F2c: a sentence where the outcome word sits inside a longer term ('oxidative stress markers') lends no size",
  breakdown.line_label(_f2_bd("Oxidative stress markers fell, SMD -1.75.", *_F2_SMD), *_F2_ST) is None
  and breakdown.line_label(_f2_bd("Metabolic syndrome fell, SMD -1.75.", *_F2_SMD),
                           "Yoga reduces the risk of syndrome", "(syndrome)") is None)
t("F2c: a word that says only how bad or how felt, or one the claim uses, still lets it size",
  breakdown.line_label(_f2_bd("Perceived stress fell, SMD -1.75.", *_F2_SMD), *_F2_ST) == "large"
  and breakdown.line_label(_f2_bd("Chronic stress fell, SMD -1.75.", *_F2_SMD), *_F2_ST) == "large"
  and breakdown.line_label(_f2_bd("Oxidative stress fell, SMD -1.75.", *_F2_SMD),
                           "Ashwagandha reduces oxidative stress", "(stress)") == "large")

# ---- final fix F6: the plain-word swap leaves a quoted title as written -------
_f6_s = breakdown.plain('The trial "Effects of creatine in male participants" enrolled 40 male participants.')
_f6_c = breakdown.plain("The review \u201cMortality and hypertension in older subjects\u201d found it in subjects "
                        "with hypertension.")
t("F6: inside straight quotation marks the title keeps 'male participants', outside it becomes 'males'",
  _f6_s == 'The trial "Effects of creatine in male participants" enrolled 40 males.', _f6_s)
t("F6: inside curly quotation marks the title keeps its words, outside they are swapped",
  _f6_c == "The review \u201cMortality and hypertension in older subjects\u201d found it in people "
           "with high blood pressure (hypertension).", _f6_c)

# ---- final fix F3: what "cannot be rated" means under a guidance stamp --------
# verdict.unrated_said removes a sentence (or its closing clause) that says the
# claim, the evidence, "this" or "it" cannot be rated or judged, in any of the
# ways that is said; it keeps one about something else (an outcome, a symptom,
# a scale) and one where a study's researchers or participants did the rating.
_F3_GO = ["It can not be rated.", "We cannot rate this claim.", "This claim can't yet be judged.",
          "There isn't enough evidence to rate it.", "There is too little evidence to judge either way.",
          "It is not possible to rate the claim from these studies.", "It isn\u2019t possible to judge it.",
          "We are unable to judge it.", "The evidence is too thin to rate.", "So it could not be graded.",
          "We couldn\u2019t rate it.", "Without a direct trial, we were not able to rate this.",
          "The claim itself cannot be judged.", "There is no evidence for us to rate this.",
          "It's impossible to judge whether it works."]
_F3_STAY = ["Researchers were unable to rate pain intensity.", "Participants could not rate their symptoms reliably.",
            "Pain intensity could not be rated in two trials.", "The trial was unable to grade adverse events.",
            "Patients couldn't rate it on the scale.", "Fatigue scores cannot be judged against a placebo here.",
            "Clinicians were not able to judge the wound size.", "The studies used a scale to rate nausea.",
            "There was not enough evidence to grade the side effects separately.", "Sleep quality was hard to rate."]
for _g in _F3_GO:
    t(f"F3: goes under a guidance stamp: {_g}", verdict.unrated_said(_g) == "", verdict.unrated_said(_g))
for _k in _F3_STAY:
    t(f"F3: stays, it is about something else: {_k}", verdict.unrated_said(_k) == _k, verdict.unrated_said(_k))
# G6: the reviewer's leftovers. "from ..." after the verb names the
# evidence; a passive rated "by" a study's raters is about the study; a
# thing rated "as" something under "not enough evidence" is the claim.
# "No conclusion can be drawn" and "We cannot say" are said about side
# questions too, so they stay.
_G6_GO = ["It is impossible to judge from these abstracts.",
          "There is not enough evidence to rate raw milk as healthier.",
          "The claim is unratable.", "It is unrateable on these studies.",
          "The evidence does not allow us to rate it.", "The evidence doesn't let us judge this claim."]
_G6_STAY = ["The certainty of the evidence could not be graded by the review authors (Study 2).",
            "Pain could not be rated by the two independent assessors.",
            "Researchers were unable to judge from the photographs.",
            "No conclusion can be drawn about sleep.", "We cannot say how long it lasts."]
for _g in _G6_GO:
    t(f"G6: goes under a guidance stamp: {_g}", verdict.unrated_said(_g) == "", verdict.unrated_said(_g))
for _k in _G6_STAY:
    t(f"G6: stays: {_k}", verdict.unrated_said(_k) == _k, verdict.unrated_said(_k))
# H4: what a study or its people judged stays, whatever it says about
# rating; "no basis to rate it" and "Rating this claim is not possible" go.
_H4_GO = ["There is no basis to rate it.", "There is no basis for rating.", "Rating this claim is not possible.",
          "Rating this claim is impossible.", "Rating this claim cannot be done."]
_H4_STAY = ["Investigators judged that there was not enough evidence to rate the drug as first-line (Study 4).",
            "The review concluded that it could not be rated with confidence (Study 2).",
            "No conclusion can be drawn.", "We cannot say."]
for _g in _H4_GO:
    t(f"H4: goes under a guidance stamp: {_g}", verdict.unrated_said(_g) == "", verdict.unrated_said(_g))
for _k in _H4_STAY:
    t(f"H4: stays: {_k}", verdict.unrated_said(_k) == _k, verdict.unrated_said(_k))
t("F3: a closing 'meaning we can't rate it' goes and what came before it stays",
  verdict.unrated_said("No trial tests this, meaning we can't rate it.") == "No trial tests this.")
_f3_g = verdict.guidance_prose("No trial tests the claim. Researchers were unable to rate pain intensity.",
                               "Researchers were unable to rate pain intensity.", None)
t("F3: guidance_prose leaves prose about a rated outcome as written, with no rests-on line added",
  _f3_g[0] == "No trial tests the claim. Researchers were unable to rate pain intensity."
  and _f3_g[1] == "Researchers were unable to rate pain intensity.", _f3_g)

# ---- Under an answer the guidance rule set, the effect box headlines no study figure ----
# The live safety run: "alcohol in pregnancy" (False) headlined "found no
# consistently significant effects"; "raw milk" (False, set by the guidance
# rule) headlined the vitamin loss from pasteurising, sized large.
_GE_ST = [_study("GE1", "Low to moderate prenatal alcohol exposure: a systematic review", types=["Systematic Review"],
                 abstract="RESULTS: Exposure of up to 83 grams per week found no consistently significant effects. "
                          "CONCLUSIONS: Weaknesses in the evidence preclude the conclusion that drinking at these "
                          "levels is safe.")]
_GE_EFF = "The review on exposure of up to 83 grams per week found no consistently significant effects (Study 1)."
_GE_BD = {"parts": [], "evidence": ["The review found no consistently significant effects (Study 1)."],
          "says": ["The review found no consistently significant effects (Study 1)."], "effect_size": _GE_EFF,
          "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": "",
          "effect": {"figure": "standardised mean difference 1.77", "label": "large",
                     "plain": "a difference of 1.77 standard deviations"}}
_GE_CLAIM = "Drinking a little alcohol during pregnancy is safe"
_GE_TLDR = verdict.GUIDELINE_LEAD + "no amount of alcohol is safe during pregnancy. Ask a doctor or midwife about this."
with appmod.app.test_request_context("/"):
    _ge = appmod._check_response(_GE_CLAIM, "(alcohol) AND (pregnancy)", "false", _GE_TLDR,
                                 "The review found no consistent effects (Study 1).", ["GE1"], [dict(s) for s in _GE_ST],
                                 "http://x", still_open="Exact low dose limits.", breakdown=json.loads(json.dumps(_GE_BD)))
    _ge_plain = appmod._check_response(_GE_CLAIM, "(alcohol) AND (pregnancy)", "false",
                                       "Drinking during pregnancy is not shown to be safe.",
                                       "The review found no consistent effects (Study 1).", ["GE1"],
                                       [dict(s) for s in _GE_ST], "http://x", still_open="Exact low dose limits.",
                                       breakdown=json.loads(json.dumps(_GE_BD)))
t("guidance effect: under an answer the guidance rule set, the effect box says it rests on guidance, not a figure",
  _ge["breakdown"]["effect_size"] == verdict.GUIDANCE_EFFECT
  == "This answer rests on what major health guidelines say, not on a measured effect size."
  and not re.search(r"\d|\b(?:small|moderate|large)\b", _ge["breakdown"]["effect_size"]), _ge["breakdown"])
t("  and the study and its finding stay exactly as they were",
  _ge["studies"] == _ge_plain["studies"] and _ge["studies"][0]["abstract"] == _GE_ST[0]["abstract"]
  and _ge["breakdown"]["evidence"] == _ge_plain["breakdown"]["evidence"], _ge["breakdown"]["evidence"])
t("guidance line: under an answer the guidance rule set, the evidence line says what the answer rests on, "
  "with no strength word",
  _ge["evidence_line"] == verdict.GUIDANCE_LINE == "Rests on health guidance, not on these studies"
  and not re.search(r"(?i)\b(?:strong|moderate|weak|low|high|certainty|small|large|against|for this claim)\b",
                    _ge["evidence_line"])
  and _ge_plain["evidence_line"].endswith("evidence against this claim"),
  (_ge["evidence_line"], _ge_plain["evidence_line"]))
t("  and an answer the guidance rule did not set keeps its own figure",
  _ge_plain["breakdown"]["effect_size"] == _GE_EFF, _ge_plain["breakdown"]["effect_size"])
_ge_said = verdict.guidance_effect(dict(_GE_BD, says=["Experts advise avoiding it (Study 1).", _GE_EFF]))
t("  and a line of 'What the research says' picked from the effect box goes with it; the others stay",
  _ge_said["says"] == ["Experts advise avoiding it (Study 1)."], _ge_said["says"])
_ge_none = verdict.guidance_effect(dict(_GE_BD, effect_size=""))
t("  and a breakdown with no effect box is not given one",
  _ge_none["effect_size"] == "" and verdict.guidance_effect(None) is None, _ge_none)
db.put_cached_verdict(_GE_CLAIM, "(alcohol) AND (pregnancy)", "false",
                      "The review found no consistent effects (Study 1).", ["GE1"], [dict(s) for s in _GE_ST],
                      tldr=_GE_TLDR, still_open="Exact low dose limits.", breakdown=json.loads(json.dumps(_GE_BD)))
_ge_page = c.get("/?q=" + urllib.parse.quote(_GE_CLAIM)).get_data(as_text=True)
_ge_box = re.search(r'<p class="effect"><b>How big the effect is</b><span>(.*?)</span></p>', _ge_page)
_ge_conf = re.search(r'<span class="confidence" id="confidence"><mark>(.*?)</mark>', _ge_page)
t("  and the server-rendered page shows that line as its confidence, and the script renders the same field",
  _ge_conf and _ge_conf.group(1) == verdict.GUIDANCE_LINE + "."
  and "Strong evidence against this claim" not in _ge_page
  and 'String(data.evidence_line || "")' in (ROOT / "static" / "app.js").read_text(),
  _ge_conf and _ge_conf.group(1))
t("  and the server-rendered page prints the same words in its effect box",
  _ge_box and _ge_box.group(1) == verdict.GUIDANCE_EFFECT and _GE_EFF not in _ge_page.split('id="server-result"')[0],
  _ge_box and _ge_box.group(1))

# ---- Guidance that rejects a forgo-treatment or medicine-cures claim is a False ----
_FG_SAYS = "this is not safe, and the medicine is still advised."
_FG_LINE = "Major health guidelines say: " + _FG_SAYS
_FG_DOC = "Stopping a prescribed medicine without a doctor's advice can be risky."
_FG_CURE_DOC = "Check treatment decisions with a doctor."
_FG_CASES = [("People who have had a heart attack can safely stop taking statins", _FG_DOC),
             ("People with type 1 diabetes can replace insulin with a strict diet", _FG_DOC),
             ("Antibiotics cure the common cold", _FG_CURE_DOC)]
_fg_insuff = {"verdict": "insufficient", "tldr": "Nothing here tests this claim.",
              "explanation": "No study tests it.", "still_open": "Whether anyone has tested it.",
              "cited_studies": [], "breakdown": None}
verdict.guideline_view = _guide("false", _FG_SAYS)
for _claim, _doc in _FG_CASES:
    for _label, _res, _studs in (("no study cited", dict(_fg_insuff), []),
                                 ("an insufficient result citing lab papers",
                                  dict(_fg_insuff, cited_studies=["L1"]), [dict(s) for s in _LAB])):
        _o = verdict.follow_guidelines(_res, _studs, _claim)
        t(f"forgo guard: {_claim!r} with {_label} ends false, with the guidance and the doctor sentence",
          _o["verdict"] == "false" and _o["tldr"] == f"{_FG_LINE} {_doc}" and _o["still_open"].strip(), _o)
_o = verdict.follow_guidelines(dict(_fg_insuff, still_open=""), [],
                               "People can safely stop taking statins")
t("  and an empty still_open is filled, never left empty",
  _o["still_open"] == "Whether any study has tested this claim directly.", _o)
_o = verdict.follow_guidelines(dict(_fg_insuff, verdict="complicated"), [], FG := "Antibiotics cure the common cold")
t("  a 'complicated' stamp on a cure claim the guidance rejects also becomes false", _o["verdict"] == "false", _o)
_ord = verdict.follow_guidelines(dict(_fg_insuff), [], "Colloidal silver cures infections")
t("  control: an ordinary claim with insufficient and a rejecting view keeps insufficient, guidance appended",
  verdict.forgoes_treatment("Colloidal silver cures infections") == ""
  and _ord["verdict"] == "insufficient" and _ord["tldr"].endswith(_FG_LINE), _ord)
_ord2 = verdict.follow_guidelines(dict(_fg_insuff), [], "Cold showers boost immunity to infections")
t("  control: another ordinary claim, same", _ord2["verdict"] == "insufficient" and _ord2["tldr"].endswith(_FG_LINE), _ord2)
for _name, _gv in (("mixed", lambda claim: None),
                   ("supports", lambda claim: {"verdict": "true", "says": "this is advised.", "bodies": ["CDC"]})):
    verdict.guideline_view = _gv
    _o = verdict.follow_guidelines(dict(_fg_insuff), [], "People can safely stop taking statins")
    t(f"  control: a {_name} view changes nothing for a statins claim"
      if _name == "mixed" else "  control: a supports view leaves an insufficient forgo claim alone",
      _o["verdict"] == "insufficient" and _o["tldr"].startswith(_fg_insuff["tldr"])
      and (_name == "supports" or _o["tldr"] == _fg_insuff["tldr"]), _o)
t("  the predicate catches the plain wordings and not unrelated claims",
  all(verdict.forgoes_treatment(x) for x in ("You can skip chemotherapy", "Use honey instead of antibiotics",
                                             "Asthma patients don't need their inhaler", "Quit taking metformin"))
  and not verdict.forgoes_treatment("Vitamin D prevents infections"))
# Through the normal path: no-studies route and the API.
verdict.guideline_view = _guide("false", _FG_SAYS)
_nf = verdict.weigh_evidence("People who have had a heart attack can safely stop taking statins", [], "")
t("  with PubMed finding nothing, the stamp is false, not forced back to insufficient",
  _nf["verdict"] == "false" and _nf["tldr"] == f"{_FG_LINE} {_FG_DOC}" and _nf["still_open"], _nf)
fg.models.script = [Resp("statins AND heart attack"),
                    Resp(VJ("insufficient", [], tldr="Nothing here tests whether heart attack patients can "
                                                      "safely stop taking statins.",
                            explanation="No study here tests stopping statins."))]
_st = P("People who have had a heart attack can safely stop taking statins", ip="7.7.7.41").get_json()
_st_cached = db.get_cached_verdict("People who have had a heart attack can safely stop taking statins", 24)
t("  through /api/check the stamp is false, with the guidance and doctor sentence, and it is cached so",
  _st.get("verdict") == "false" and _st.get("tldr") == f"{_FG_LINE} {_FG_DOC}" and _st.get("still_open")
  and _st_cached and _st_cached["verdict"] == "false", _st)
t("  with no dash anywhere in what the reader sees", not re.search("[\u2013\u2014]", json.dumps(_st, ensure_ascii=False)))
verdict.guideline_view = lambda claim: None

# ---- Plain guidance decides pregnancy and child-safety claims -------------------
_PD, _CD = verdict.PREGNANCY_DOCTOR, verdict.CHILD_DOCTOR
_ALC = "Drinking a little alcohol during pregnancy is safe"
_ALC_SAYS = "no amount of alcohol is safe during pregnancy."
_ALC_LINE = "Major health guidelines say: " + _ALC_SAYS
t("pregnancy: the doctor lines are the ones asked for",
  _PD == "Ask a doctor or midwife about this." and _CD == "Ask your child's doctor about this.")
t("  vulnerable_group and says_safe read the wording",
  verdict.vulnerable_group(_ALC) == "pregnancy" and verdict.vulnerable_group("Honey is safe for babies") == "child"
  and verdict.vulnerable_group("Kids who eat breakfast do better in school") == ""
  and verdict.vulnerable_group("Antibiotics cure the common cold") == ""
  and verdict.says_safe(_ALC) and verdict.says_safe("Honey is okay for babies")
  and not any(verdict.says_safe(x) for x in ("Alcohol is not safe in pregnancy", "Smoking is never safe",
                                             "It isn't safe for babies", "Alcohol is unsafe in pregnancy")))
t("  a child claim with safety wording is a guideline topic; one without is not, so it costs no call",
  verdict.guideline_topic("Honey is safe for babies")
  and not verdict.guideline_topic("Kids who eat breakfast do better in school"))

_p_cited = dict(_mixed_result, cited_studies=["G1", "G2"])
_p_none = {"verdict": "insufficient", "tldr": "Nothing here tests this claim.", "explanation": "No study tests it.",
           "still_open": "Whether anyone has tested it.", "cited_studies": [], "breakdown": None}
verdict.guideline_view = _guide("false", _ALC_SAYS)
for _label, _res, _studs in (("complicated with a study cited", dict(_p_cited), [dict(s) for s in _GUIDE_STUDIES]),
                             ("insufficient with nothing cited", dict(_p_none), [])):
    _o = verdict.follow_guidelines(_res, _studs, _ALC)
    t(f"  alcohol, {_label}: false, the lead once, the doctor line, still_open set",
      _o["verdict"] == "false" and _o["tldr"] == f"{_ALC_LINE} {_PD}"
      and _o["tldr"].count("Major health guidelines say") == 1 and _o["still_open"].strip(), _o)
verdict.guideline_view = _guide("false", "smoking during pregnancy harms the baby and is never safe.")
_o = verdict.follow_guidelines(dict(_p_none), [], "Smoking a few cigarettes during pregnancy is safe")
t("  smoking: false with the pregnancy doctor line",
  _o["verdict"] == "false" and _o["tldr"].endswith(_PD) and "never safe" in _o["tldr"], _o)
verdict.guideline_view = _guide("true", "getting the whooping cough shot while pregnant helps protect "
                                        "newborn babies from getting sick.")
_o = verdict.follow_guidelines(dict(_p_none), [], "The whooping cough vaccine given during pregnancy protects newborns")
t("  whooping cough: insufficient with nothing cited becomes true, with the pregnancy doctor line",
  _o["verdict"] == "true" and _o["tldr"].endswith(" " + _PD) and "helps protect newborn babies" in _o["tldr"], _o)
verdict.guideline_view = _guide("false", "babies under one year old should not be given honey.")
_o = verdict.follow_guidelines(dict(_p_none), [], "Honey is safe for babies")
t("  honey for babies: false with the child doctor line",
  _o["verdict"] == "false" and _o["tldr"].endswith(" " + _CD), _o)
# Guards
verdict.guideline_view = _guide("true", "eating sushi during pregnancy is fine.")
_o = verdict.follow_guidelines(dict(_p_none), [], "Eating sushi during pregnancy is safe")
t("  guard: guidance alone never makes 'safe in pregnancy' true; the line is only appended",
  _o["verdict"] == "insufficient" and _o["tldr"].endswith("is fine."), _o)
verdict.guideline_view = _guide("false", "pregnant women can safely drink a little, but should avoid binge drinking.")
t("  guard: a line that argues with itself is not used, the verdict stays complicated",
  verdict.follow_guidelines(dict(_p_cited), [dict(s) for s in _GUIDE_STUDIES], _ALC) == _p_cited)
verdict.guideline_view = lambda claim: None
t("  guard: no guidance leaves a pregnancy claim unchanged",
  verdict.follow_guidelines(dict(_p_cited), _GUIDE_STUDIES, _ALC) == _p_cited)
_ord_ins = verdict.follow_guidelines(dict(_p_none), [], "Colloidal silver cures infections")
t("  guard: a claim outside both groups keeps its stamp", _ord_ins["verdict"] == "insufficient")

_PNS = [("no amount of alcohol is safe during pregnancy.", True),
        ("there is no known safe amount of alcohol to drink while pregnant.", True),
        ("no level of alcohol is known to be safe during pregnancy.", True),
        ("no alcohol is safe during pregnancy.", True),
        ("drinking alcohol while pregnant is not known to be safe.", True),
        ("it is not okay to drink alcohol while pregnant.", True),
        ("smoking during pregnancy harms the baby and is never safe.", True),
        ("pregnant women should not take any risks with alcohol.", True),
        ("no amount is safe, and even a little can harm the baby.", True),
        ("the vaccine does not cause harm to the baby.", False),
        ("there is no harm in a small amount.", False),
        ("a little alcohol does not hurt the baby.", False),
        ("pregnant women can safely drink a little, but should avoid binge drinking.", False),
        ("alcohol can cause lifelong problems for the baby.", None)]
_pns_bad = [(x, w, verdict._plain_not_safe(x)) for x, w in _PNS if verdict._plain_not_safe(x) is not w]
t("  _plain_not_safe: plain no, argues with itself, or no cue, by table", not _pns_bad, _pns_bad)
_SS = [("Drinking a little alcohol during pregnancy is safe", True), ("Drinking a little alcohol won't hurt the baby", True),
       ("A glass of wine doesn't harm your baby during pregnancy", True),
       ("There is no risk in eating sushi while pregnant", True),
       ("Alcohol is not harmful during pregnancy in small amounts", True), ("Honey is safe for babies", True),
       ("Honey is okay for babies", True), ("No amount of alcohol is safe during pregnancy", False),
       ("Smoking while pregnant is not safe", False), ("Smoking is never safe", False),
       ("It isn't safe for babies", False), ("Alcohol is unsafe in pregnancy", False),
       ("The whooping cough vaccine given during pregnancy protects newborns", False)]
_ss_bad = [(x, w) for x, w in _SS if verdict.says_safe(x) is not w]
t("  says_safe: asserts safety or not, by table", not _ss_bad, _ss_bad)
verdict.guideline_view = _guide("false", "no level of alcohol is known to be safe during pregnancy.")
_o = verdict.follow_guidelines(dict(_p_cited), [dict(s) for s in _GUIDE_STUDIES], _ALC)
t("  alcohol with 'no level of alcohol is known to be safe' becomes false, with the doctor line",
  _o["verdict"] == "false" and _o["tldr"].endswith(" " + _PD), _o)
verdict.guideline_view = _guide("false", "the vaccine does not cause harm to the baby.")
t("  a 'rejects' line that says no harm is not used",
  verdict.follow_guidelines(dict(_p_cited), [dict(s) for s in _GUIDE_STUDIES], _ALC) == _p_cited)
verdict.guideline_view = _guide("true", "this is fine.")
_o = verdict.follow_guidelines(dict(_p_none), [], "A glass of wine doesn't harm your baby during pregnancy")
t("  guidance alone never stamps 'won't harm' wording true", _o["verdict"] == "insufficient", _o)

# The lead, repeated by the model, is stripped
for _rep in ("Major health guidelines say that no amount of alcohol is safe during pregnancy.",
             "major health guidelines say: no amount of alcohol is safe during pregnancy.",
             "Guidelines say that no amount of alcohol is safe during pregnancy.",
             "Major health guidelines say: that no amount of alcohol is safe during pregnancy.",
             "that no amount of alcohol is safe during pregnancy.",
             "That no amount of alcohol is safe during pregnancy."):
    fg.models.script = [_GL("rejects", _rep)]
    verdict.guideline_view = _real_guideline_view
    _lv = verdict.guideline_view(_ALC)
    t(f"  guideline_view strips a repeated lead: {_rep[:28]!r}",
      _lv and _lv["says"] == _ALC_SAYS, _lv)
# A "that" that is a pronoun before a verb is part of the sentence, not a joiner
for _pron in ("that is not safe during pregnancy.", "that isn't safe during pregnancy.",
              "that doesn't harm the baby.", "that won't hurt the baby.",
              "that, the CDC says, is unsafe."):
    fg.models.script = [_GL("rejects", _pron)]
    verdict.guideline_view = _real_guideline_view
    _lv = verdict.guideline_view(_ALC)
    t(f"  guideline_view keeps a pronoun 'that' before a verb: {_pron[:24]!r}",
      _lv and _lv["says"] == _pron, _lv)

# Through a real check
verdict.guideline_view = _guide("false", _ALC_SAYS)
fg.models.script = [Resp("alcohol AND pregnancy"),
                    Resp(VJ("complicated", [1, 2], tldr="The evidence on alcohol in pregnancy is mixed.",
                            explanation="One study found no harm at low levels (Study 1). Another found "
                                        "some risk (Study 2)."))]
_alc = P(_ALC, ip="7.7.7.51").get_json()
t("  through a real check the alcohol claim is false, with the doctor line and a still_open",
  _alc.get("verdict") == "false" and _alc.get("tldr") == f"{_ALC_LINE} {_PD}" and _alc.get("still_open")
  and not re.search("[–—]", json.dumps(_alc, ensure_ascii=False)), _alc)
verdict.guideline_view = lambda claim: None

# ---- A Beta label beside the wordmark, and one line under every verdict --------
# Both asked for while accuracy is still being proven: "Beta" on every screen
# that has a masthead, and "Check important health decisions with a doctor."
# directly under the verdict, in the page's own HTML (so a shared link and a
# crawler get it with no script), in the shared text and on the link card.
_DOC = "Check important health decisions with a doctor."
_BETA = '<a class="wordmark" href="/" aria-label="Evident home">Evident</a><span class="beta">Beta</span>'
_mast = {p: c.get(p).get_data(as_text=True) for p in ("/", "/privacy", "/no-such-page")}
t("every masthead carries a Beta label right after the wordmark: home, privacy, 404",
  all(_BETA in h for h in _mast.values()), {p: _BETA in h for p, h in _mast.items()})
t("  it is plain text, not a link, a button or a tab stop",
  all(_re.search(r'<span class="beta">Beta</span>', h) and not _re.search(r'<(a|button)[^>]*>\s*Beta\s*<', h)
      for h in _mast.values()))
_beta_css = (ROOT / "static" / "style.css").read_text().split(".beta {")[1].split("}")[0]
t("  set in the mono, on the night's quiet ink, with no fill and no motion",
  "var(--mono)" in _beta_css and "var(--on-deep-2)" in _beta_css and "background" not in _beta_css
  and "animation" not in _beta_css and "transition" not in _beta_css, _beta_css)

pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in STUDIES]
fg.models.script = [Resp("sleep AND memory"),
                    Resp(VJ("complicated", [1, 2], tldr="The evidence on sleep and memory is mixed.",
                            explanation="One trial found a small gain (Study 1). Another found no "
                                        "difference (Study 2)."))]
_dl = P("Sleeping eight hours improves memory", ip="7.7.7.41").get_json()
_dl_page = c.get("/?q=" + urllib.parse.quote(_dl["claim"])).get_data(as_text=True)
_i_stamp = _dl_page.find('id="stamp"')
_i_doc = _dl_page.find('<p class="doctor-line" id="doctor-line">' + _DOC + "</p>")
t("a server-rendered result carries the doctor line, after the assessment and before what the research says",
  0 < _i_stamp < _i_doc < _dl_page.find('id="says-sec"'), (_i_stamp, _i_doc))
t("  exactly once, and visible: no hidden attribute on a page that has a verdict",
  _dl_page.count(_DOC) == 1 and 'id="doctor-line" hidden' not in _dl_page)
_cold = c.get("/?q=never%20checked%20for%20the%20line").get_data(as_text=True)
t("  an unchecked page keeps the line hidden until a verdict shows",
  '<p class="doctor-line" id="doctor-line" hidden>' + _DOC in _cold)
_js = (ROOT / "static" / "app.js").read_text()
_rr = _js.split("function renderResult(data) {")[1].split("\nfunction ")[0]
_rp = _js.split("function renderPending(claim) {")[1].split("\nfunction ")[0]
t("  a live check shows it with the verdict and hides it while a check runs",
  '$("doctor-line").hidden = false' in _rr and '$("doctor-line").hidden = true' in _rp)
t("  the page and the script say the same sentence, and it has no dash",
  f'const DOCTOR_LINE = "{_DOC}";' in _js and not _re.search("[–—]", _DOC))

# The shared text ends on the line. Run the real function under node when there
# is one (no browser is needed), so what is tested is what ships.
import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402
_share_src = _js.split("function shareText(r) {")[1].split("\n}\n")[0]
t("the shared text is built with the doctor line in the script", "DOCTOR_LINE" in _share_src)
if _shutil.which("node"):
    _labels = _js.split("const VERDICT_LABELS = ")[1].split("};")[0] + "}"
    _prog = (f"const VERDICT_LABELS = {_labels}; const DOCTOR_LINE = {json.dumps(_DOC)};"
             "function shareText(r) {" + _share_src + "\n}"
             "console.log(JSON.stringify(["
             "shareText({claim:'Sleep helps memory',verdict:'complicated',tldr:'It is mixed.',still_open:'Long term effects'}),"
             "shareText({claim:'Sleep helps memory',verdict:'insufficient',tldr:'Too little to say.'})]));")
    _out = json.loads(_subprocess.run(["node", "-e", _prog], capture_output=True, text=True, check=True).stdout)
    t("  with a still open line, the text ends on it, each sentence closed",
      _out[0] == "“Sleep helps memory”: It's complicated. It is mixed. Still open: Long term effects. " + _DOC, _out[0])
    t("  and without one, it still ends on it",
      _out[1].endswith("Too little to say. " + _DOC) and not _re.search("[–—]", _out[1]), _out[1])

# The link preview card: the line stands under the ring on the night, and it
# is drawn (light pixels where it sits) for a one-line claim.
from PIL import Image as _Image  # noqa: E402
import io as _io  # noqa: E402
t("the card module carries the same sentence", og.DOCTOR_LINE == _DOC)
_card_im = _Image.open(_io.BytesIO(og.render_card("Sleep helps memory", "true", "It is mixed.", 3))).convert("L")
_strip = _card_im.crop((94, 344, 520, 368))
t("  and it is drawn under the ringed verdict, on the night, at the card's left margin",
  max(_strip.getdata()) > 130 and _card_im.getpixel((8, 350)) < 30 and _card_im.size == (1200, 630))
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': STUDIES

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

# Guidance that tells people to keep a treatment going answers a claim to stop or
# replace it with a no, however much its words overlap the claim.
_STATIN = "People who have had a heart attack can safely stop taking statins"
_STATIN_SAYS = ("people who have had a heart attack should keep taking statins "
                "to prevent another one.")
_INSULIN = "People with type 1 diabetes can replace insulin with diet"
_INSULIN_SAYS = "people with type 1 diabetes need insulin every day and cannot replace it with diet."
t("keep-the-treatment guidance: 'should keep taking statins' is a no to a claim to stop them, not a conflict",
  breakdown.stance("Major health guidelines say: " + _STATIN_SAYS, _STATIN) == "no"
  and breakdown.stamp_conflict("false", "Major health guidelines say: " + _STATIN_SAYS, "", _STATIN) is None)
t("  and 'need insulin ... cannot replace it with diet' is a no to replacing insulin",
  breakdown.stance("Major health guidelines say: " + _INSULIN_SAYS, _INSULIN) == "no"
  and breakdown.stamp_conflict("false", "Major health guidelines say: " + _INSULIN_SAYS, "", _INSULIN) is None)
t("  and 'should not stop', 'do not stop' and 'never stop' all read as no",
  all(breakdown.stance(f"Guidelines say people {w} statins.", _STATIN) == "no"
      for w in ("should not stop", "do not stop", "don't stop", "must not stop", "never stop")))
t("  but guidance that really says 'safe to stop' is still a yes, so the conflict guard still drops it",
  breakdown.stance("Major health guidelines say: it is safe to stop statins after a heart attack.", _STATIN) == "yes"
  and breakdown.stamp_conflict("false", "Major health guidelines say: it is safe to stop statins "
                               "after a heart attack.", "", _STATIN))
t("  and a claim that is not about stopping a treatment is read as before",
  not breakdown._keeps_treatment("Major health guidelines say: people should keep taking statins.",
                                 "Statins lower cholesterol"))
_stop_fg = verdict.guideline_view
verdict.guideline_view = lambda claim: {"verdict": "false", "bodies": ["WHO"],
                                        "says": _STATIN_SAYS}
_stop_res = verdict.follow_guidelines(
    {"verdict": "insufficient", "tldr": "Not enough evidence.", "explanation": "", "still_open": "",
     "cited_studies": [], "breakdown": None}, [], _STATIN)
verdict.guideline_view = _stop_fg
t("  and end to end: the statins guidance turns an insufficient stamp into false",
  _stop_res["verdict"] == "false"
  and "keep taking statins" in _stop_res["tldr"], _stop_res)

# ---- Site audit: errors, retry, reports, sitemap, stats ---------------------------
# What a visitor can read when something fails, the way out, an anonymous report
# of a wrong answer, and the read-only stats report.
import re as _re2  # noqa: E402
import sqlite3 as _sq  # noqa: E402
import subprocess as _sp  # noqa: E402

_DASH = _re2.compile("[\u2013\u2014]")
_jsrc = (ROOT / "static" / "app.js").read_text()
_tpl = (ROOT / "templates" / "index.html").read_text()

# 1. No raw exception text, key name or .env hint in anything a visitor reads.
_real_swf = pubmed.search_with_fallback
def _explode(*a, **k):
    raise ValueError("secret internal detail 0xDEADBEEF")
pubmed.search_with_fallback = _explode
appmod.app.logger.setLevel(logging.CRITICAL)   # the traceback it logs is the point, not output
fg.models.script = [Resp("sleep AND memory")]
_r = P("an unexpected failure claim", ip="7.7.9.1")
_body = _r.get_json()
t("an unexpected failure is a plain 500 with a way out, not the exception text",
  _r.status_code == 500 and "0xDEADBEEF" not in json.dumps(_body) and "ValueError" not in json.dumps(_body)
  and "Try again" in _body["error"], _body)
fg.models.script = [Resp("sleep AND memory")]
_st, _evs = stream("an unexpected failure claim two", ip="7.7.9.2")
t("  the streamed check says the same",
  _evs[-1]["stage"] == "error" and "0xDEADBEEF" not in json.dumps(_evs[-1]) and _evs[-1]["status"] == 500, _evs[-1])
pubmed.search_with_fallback = _real_swf
appmod.app.logger.setLevel(logging.ERROR)
t("  and no visitor-facing error string has a dash",
  not _DASH.search(appmod.UNAVAILABLE_MESSAGE) and not _DASH.search(_body["error"]))

# 2. Wrong verbs and crashes on a page route are friendly, in HTML or JSON.
_r = c.get("/api/feedback")
t("a GET to the feedback address is a JSON 405, not the framework's page",
  _r.status_code == 405 and "error" in _r.get_json())
_r = c.post("/privacy")
t("a POST to a page is the friendly 404 page with a 405 status, home link and the notice",
  _r.status_code == 405 and 'href="/"' in _r.get_data(as_text=True)
  and "Not medical advice" in _r.get_data(as_text=True))
with appmod.app.test_request_context("/somewhere"):
    _r5 = appmod.server_error(None)
_b5 = _r5.get_data(as_text=True)
t("the 500 page is plain, says it was our side, links home and keeps the notice, with no dash",
  _r5.status_code == 500 and "Back to Evident" in _b5 and "Not medical advice" in _b5
  and "Traceback" not in _b5 and not _DASH.search(_b5))
with appmod.app.test_request_context("/api/check"):
    _r5 = appmod.server_error(None)
t("  and an API route's 500 is JSON the page can read", _r5[1] == 500 and "error" in _r5[0].get_json())
_r = c.get("/favicon.ico")
t("/favicon.ico answers with an image, not a 404", _r.status_code == 200 and _r.mimetype == "image/png")

# 3. The front end: a slow check says so, a failed one offers Retry, both are announced.
t("the page has a polite live region for 'still working', errors and the verdict",
  'id="announce" role="status" aria-live="polite"' in _tpl and 'function announce(' in _jsrc
  and "announce(SLOW_TEXT)" in _jsrc and "announce(`${k.title}. ${message}`)" in _jsrc and "announce(`Verdict:" in _jsrc)
t("  a silent check shows static 'still working' text after a delay and gives up later with Retry",
  "const SLOW_MS = 12000" in _jsrc and "const GIVE_UP_MS = 90000" in _jsrc
  and "taking longer than it should" in _jsrc and 'id="error-retry"' in _tpl and "Retry this claim" in _tpl
  and "li.slow" in _jsrc)
t("  Retry runs the same claim again, and a claim the server refused to read gets no Retry",
  'check(lastClaim)' in _jsrc and "retry: false" in _jsrc.split("const ERROR_KINDS")[1].split("};")[0])
# The five failures a reader can meet, told apart by what the server really sent.
_kinds = _jsrc.split("const ERROR_KINDS = {")[1].split("};")[0]
t("an error says which of four things failed, each with its own title: invalid claim, no connection, a limit, the checker",
  all(x in _kinds for x in ("That claim can't be checked", "Couldn't reach the server",
                            "Too many checks right now", "The checker couldn't finish this check")))
t("  the kind comes from the status the server sent: 400 invalid, 429 a limit, any other failure the checker; no status is guessed",
  'if (status === 400) return "invalid"' in _jsrc and 'if (status === 429) return "limit"' in _jsrc
  and 'return "provider"' in _jsrc and "errorKindOf(final.status)" in _jsrc)
t("  no response at all (offline, a dropped stream, a silent server) is the network kind, never a status",
  _jsrc.count('renderError("network"') == 3)
t("  an invalid claim offers Edit claim and no Retry; every other kind offers Retry",
  'back: "Edit claim"' in _kinds and _kinds.count("retry: true") == 3)
if _shutil.which("node"):
    _ea_fn = "const ERROR_KINDS = {" + _kinds + "};\n" + _jsrc[_jsrc.index("function errorActions"):_jsrc.index("// The draft stays in the field on every failure")]
    _ea_cases = [["provider", None], ["provider", "reword"], ["provider", "either"], ["invalid", None], ["limit", None], ["network", None]]
    _ea = json.loads(_subprocess.run(["node", "-e", _ea_fn + "console.log(JSON.stringify(" + json.dumps(_ea_cases)
                                      + ".map(([k, w]) => errorActions(k, w))));"], capture_output=True, text=True, check=True).stdout)
    t("  the ways out follow the server's way_out: none Retry and Check another claim, reword Edit claim only, either Retry and Edit claim",
      _ea == [{"retry": True, "back": "Check another claim"}, {"retry": False, "back": "Edit claim"},
              {"retry": True, "back": "Edit claim"}, {"retry": False, "back": "Edit claim"},
              {"retry": True, "back": "Check another claim"}, {"retry": True, "back": "Check another claim"}], _ea)
t("  both checks hand the server's way_out to the error screen, and it sets Retry and the back label from it",
  "way_out: data && data.way_out" in _jsrc and "final.retry_after, final.way_out)" in _jsrc
  and "const ways = errorActions(kind, wayOut)" in _jsrc and "back.hidden = !(ways.retry && lastClaim)" in _jsrc
  and '$("error-back").textContent = ways.back' in _jsrc)
t("  every failure keeps the draft in the field",
  "if (lastClaim && !input.value.trim()) input.value = lastClaim" in _jsrc)
t("  the error screen has a title and a line under it, and none of that copy has a dash",
  'id="error-title"' in _tpl and 'id="error-msg"' in _tpl
  and not _DASH.search(_kinds) and not any(_DASH.search(x) for x in _re2.findall(r'renderError\("[a-z]+", "([^"]+)"', _jsrc)))
# A check that found no studies is an answer, not an error: the result says what to try.
t("a result that rests on no studies says what to try next (server-rendered, hidden when studies exist, and shown by the script on a live check)",
  'id="no-evidence"{% if not result or result.studies %} hidden{% endif %}' in _tpl
  and 'id="no-evidence" hidden' in c.get("/?q=" + urllib.parse.quote(_dl["claim"])).get_data(as_text=True)
  and "Name the condition and the treatment" in _tpl and not _DASH.search(_tpl.split('id="no-evidence"')[1].split("</p>")[0])
  and '$("no-evidence").hidden = data.studies.length > 0' in _jsrc)
_strings = _re2.findall(r'"([^"\n]{12,})"', _jsrc.split("const SLOW_MS")[1].split("async function checkStreamed")[0])
t("  none of that copy has a dash", not any(_DASH.search(x) for x in _strings) and not _DASH.search(_tpl.split('id="error"')[1].split("</section>")[0]))

# 4. Report a wrong answer: on every result, anonymous, through /api/feedback.
_srv = c.get("/?q=" + urllib.parse.quote(_dl["claim"])).get_data(as_text=True)
t("a server-rendered result has the Report a wrong answer button and its form",
  "Report a wrong answer" in _srv and 'id="report-form"' in _srv and 'id="report-open"' in _srv)
t("  and the script posts it to /api/feedback and resets it for every result",
  "report: true" in _jsrc and "function resetReport()" in _jsrc and "resetReport();" in _jsrc.split("function renderResult(data) {")[1].split("\nfunction ")[0])
_before = _sq.connect(os.environ["DB_PATH"]).execute("select count(*) from feedback").fetchone()[0]
_r = c.post("/api/feedback", json={"claim": _dl["claim"], "verdict": "true", "report": True,
                                    "note": "  The   study says\nthe opposite.  "},
            environ_base={"REMOTE_ADDR": "9.9.9.9"})
_row = _sq.connect(os.environ["DB_PATH"]).execute(
    "select claim_key, verdict, helpful, note, created_at from feedback order by id desc limit 1").fetchone()
t("a report is stored as a thumbs down whose note starts 'wrong answer: ', whitespace tidied",
  _r.status_code == 200 and _row[1] == "true" and _row[2] == 0 and _row[3] == "wrong answer: The study says the opposite.", _row)
_cols = [r[1] for r in _sq.connect(os.environ["DB_PATH"]).execute("pragma table_info(feedback)")]
t("  and nothing that identifies the sender is stored: no address in any column or value",
  _cols == ["id", "claim_key", "verdict", "helpful", "note", "created_at"] and "9.9.9.9" not in " ".join(map(str, _row)))
_r = c.post("/api/feedback", json={"claim": "x" * 900, "verdict": "<script>", "report": True, "note": "n" * 900})
_row = _sq.connect(os.environ["DB_PATH"]).execute("select claim_key, verdict, note from feedback order by id desc limit 1").fetchone()
t("  an oversized claim and note are cut, and a verdict that is not one of ours is dropped",
  _r.status_code == 200 and len(_row[0]) <= 500 and _row[1] is None and len(_row[2]) <= 300, (len(_row[0]), _row[1], len(_row[2])))
t("  a report with no claim is a plain 400", c.post("/api/feedback", json={"report": True}).status_code == 400)
t("  a thumb still works as before",
  c.post("/api/feedback", json={"claim": "thumb claim", "verdict": "false", "helpful": True}).status_code == 200
  and _sq.connect(os.environ["DB_PATH"]).execute("select helpful, note from feedback order by id desc limit 1").fetchone() == (1, ""))
t("  and a body that is not JSON is a 400, not a 500",
  c.post("/api/feedback", data="nope", content_type="text/plain").status_code == 400)
_priv = c.get("/privacy").get_data(as_text=True)
t("the privacy page says a report's note is kept, anonymously", "report a wrong answer" in _priv)

# 5. The sitemap lists static pages only; a claim somebody typed never appears.
_sm = c.get("/sitemap.xml").get_data(as_text=True)
t("the sitemap has the static pages, absolute",
  all(f"<loc>http://localhost{x}</loc>" in _sm for x in ("/", "/privacy")))
t("  and /checks, now a redirect, is not in it", "/checks" not in _sm)
_rb = c.get("/robots.txt").get_data(as_text=True)
t("the sitemap and llms.txt list /about, /method and /corrections as absolute URLs",
  all(f"<loc>http://localhost{x}</loc>" in _sm for x in ("/about", "/method", "/corrections"))
  and all(f"](http://localhost{x})" in c.get("/llms.txt").get_data(as_text=True) for x in ("/about", "/method", "/corrections", "/privacy")))
t("robots.txt points at the sitemap with an absolute URL", "Sitemap: http://localhost/sitemap.xml" in _rb)

# 6. Page identity: /checks is a permanent redirect to /; every result has a card.
_ck_r = c.get("/checks")
_ckq_r = c.get("/checks?q=garlic%20shortens%20colds")
_ck_home = c.get("/").get_data(as_text=True)
t("/checks answers 301 to /, and keeps a query string an old shared link may carry",
  _ck_r.status_code == 301 and urllib.parse.urlsplit(_ck_r.headers["Location"]).path == "/"
  and not urllib.parse.urlsplit(_ck_r.headers["Location"]).query
  and _ckq_r.status_code == 301 and _ckq_r.headers["Location"].endswith("/?q=garlic%20shortens%20colds"),
  (_ck_r.headers.get("Location"), _ckq_r.headers.get("Location")))
t("  the home page is the one canonical tool page, with the card tags",
  'rel="canonical" href="http://localhost/"' in _ck_home and "/checks" not in _ck_home
  and 'name="twitter:card" content="summary_large_image"' in _ck_home and 'property="og:image" content="http://localhost/og/_.png?v=2"' in _ck_home)
_ck_sw = (ROOT / "static" / "sw.js").read_text()
t("  /checks is out of the service worker's SHELL, llms.txt and the templates",
  '"/checks"' not in _ck_sw.split("const SHELL")[1].split("];")[0]
  and "/checks" not in c.get("/llms.txt").get_data(as_text=True)
  and not any("checks_page" in (ROOT / "templates" / f).read_text() or "/checks" in (ROOT / "templates" / f).read_text()
              for f in ("index.html", "info.html", "privacy.html", "404.html"))
  and "checks_page" not in (ROOT / "app.py").read_text())
_png = c.get("/og/" + urllib.parse.quote(db.normalize_claim(_dl["claim"]), safe="") + ".png")
from PIL import Image as _Im2  # noqa: E402
import io as _io2  # noqa: E402
t("a result's og:image is absolute and /og/<key>.png is a 1200x630 PNG, with twitter:card too",
  f'property="og:image" content="http://localhost/og/' in _srv and 'name="twitter:card" content="summary_large_image"' in _srv
  and _png.status_code == 200 and _Im2.open(_io2.BytesIO(_png.data)).size == (1200, 630))

# 7. Beta and the doctor line are on the first screen of the ask, the 404 and the error screen.
_ask = c.get("/").get_data(as_text=True)
t("the ask screen carries Not medical advice and the doctor sentence right under its introduction",
  'class="ask-note">Not medical advice. Ask a doctor before you change a treatment.</p>' in _ask)
t("  so do the 404 page and the error screen",
  'class="ask-note">Not medical advice. Ask a doctor' in c.get("/nope-nope").get_data(as_text=True)
  and 'class="error-note">Not medical advice. Ask a doctor' in _tpl)
t("  and every result's head has the notice in the same row as the case line",
  'class="case case-nma">Not medical advice</p>' in _srv)

# 8. scripts/stats.py: read-only, from the table that already exists.
_sdb = os.path.join(tempfile.mkdtemp(), "s.db")
_con = _sq.connect(_sdb)
_con.executescript(db.SCHEMA)
_nowd = datetime.now(timezone.utc) if "datetime" in dir() else None
from datetime import datetime as _dt, timezone as _tz, timedelta as _td  # noqa: E402
_now2 = _dt.now(_tz.utc)
for _i, (_txt, _age) in enumerate([("Zinc cures colds", 0), ("Zinc cures colds", 0), ("zinc cures colds", 1),
                                    ("Garlic cures colds", 1), ("Old claim", 45)]):
    _con.execute("INSERT INTO checks (claim_key, claim_text, verdict, explanation, cached, studies_json, checked_at) "
                 "VALUES (?, ?, 'false', '', 0, '[]', ?)", (_txt.lower(), _txt, (_now2 - _td(days=_age)).isoformat()))
_con.commit(); _con.close()
_size = os.path.getsize(_sdb)
_out = _sp.run([sys.executable, str(ROOT / "scripts" / "stats.py"), "--db", _sdb], capture_output=True, text=True)
_lines = _out.stdout.splitlines()
t("stats.py prints checks per day for 30 days and the top claims, from the checks table",
  _out.returncode == 0 and "Checks per day, last 30 days" in _out.stdout
  and sum(1 for l in _lines if _re2.match(r"\d{4}-\d\d-\d\d  +\d+$", l)) == 30
  and _re2.search(r"\n\s+3  (Z|z)inc cures colds", _out.stdout) and "Garlic cures colds" in _out.stdout, _out.stdout[-300:])
t("  it counts today and yesterday separately, and leaves out what is older than 30 days from the daily list",
  any(l.startswith(_now2.strftime("%Y-%m-%d")) and l.strip().endswith("2") for l in _lines)
  and any(l.startswith((_now2 - _td(days=1)).strftime("%Y-%m-%d")) and l.strip().endswith("2") for l in _lines)
  and "All checks in this database: 5" in _out.stdout)
t("  it is read-only: the file is unchanged and no table is added",
  os.path.getsize(_sdb) == _size
  and {r[0] for r in _sq.connect(_sdb).execute("select name from sqlite_master where type='table'")}
  == {r[0] for r in _sq.connect(os.environ["DB_PATH"]).execute("select name from sqlite_master where type='table'")})
_miss = _sp.run([sys.executable, str(ROOT / "scripts" / "stats.py"), "--db", _sdb + ".nope"], capture_output=True, text=True)
t("  with no database it says so plainly and creates nothing",
  _miss.returncode == 1 and "No database" in _miss.stderr and not os.path.exists(_sdb + ".nope"))
_src = (ROOT / "scripts" / "stats.py").read_text()
t("  and it has no way to write: no INSERT, UPDATE, DELETE, CREATE or db.init_db",
  not _re2.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", _src.split('"""', 2)[2]) and "init_db" not in _src)

# ---- Text is sent gzipped to a browser that accepts it -------------------------------
import gzip as _gz  # noqa: E402
_plain = c.get("/static/app.js")
_zip = c.get("/static/app.js", headers={"Accept-Encoding": "gzip, br"})
t("a script is gzipped for a browser that accepts it, and decompresses to the same bytes",
  _zip.headers.get("Content-Encoding") == "gzip" and _gz.decompress(_zip.data) == _plain.data
  and len(_zip.data) < len(_plain.data) / 2 and "Accept-Encoding" in _zip.headers.get("Vary", ""))
t("  and is left alone for one that does not",
  "Content-Encoding" not in _plain.headers)
_hz = c.get("/", headers={"Accept-Encoding": "gzip"})
t("  the home page is gzipped too, and still HTML once decoded",
  _hz.headers.get("Content-Encoding") == "gzip" and b"<!doctype html>" in _gz.decompress(_hz.data))
_ss = c.post("/api/check/stream", json={"claim": ""}, headers={"Accept-Encoding": "gzip"})
t("  the event stream is never compressed, and images are not either",
  "Content-Encoding" not in _ss.headers
  and "Content-Encoding" not in c.get("/static/icons/icon-192.png", headers={"Accept-Encoding": "gzip"}).headers)
t("  a conditional request still answers 304 with no body",
  c.get("/static/app.js", headers={"Accept-Encoding": "gzip", "If-None-Match": _plain.headers["ETag"]}).status_code in (200, 304))

# ---- Turso storage: the same tables in a hosted database, with a fallback ----------------
import urllib.error  # noqa: E402
import sqlite3 as _sq3
import io as _io3

_SECRET = "tok-SECRET-abc123"
_URL = "libsql://evident-test.turso.io"


class FakeTurso:
    """Stands in for db._http_post: runs each statement on an in-memory SQLite
    and answers in Turso's pipeline format (integers as strings)."""

    def __init__(self):
        self.con = _sq3.connect(":memory:", check_same_thread=False)
        self.calls = []          # (url, token, body)
        self.fail = None         # an exception to raise instead of answering

    def __call__(self, url, token, body, timeout):
        self.calls.append((url, token, body))
        if self.fail:
            raise self.fail
        out = []
        for r in body["requests"]:
            if r["type"] == "close":
                out.append({"type": "ok", "response": {"type": "close"}})
                continue
            args = []
            for a in r["stmt"]["args"]:
                if a["type"] == "null":
                    args.append(None)
                elif a["type"] == "integer":
                    assert isinstance(a["value"], str)
                    args.append(int(a["value"]))
                elif a["type"] == "float":
                    args.append(float(a["value"]))
                else:
                    assert a["type"] == "text"
                    args.append(a["value"])
            cur = self.con.execute(r["stmt"]["sql"], args)
            self.con.commit()
            cols = [{"name": d[0], "decltype": None} for d in (cur.description or [])]
            rows = []
            for row in cur.fetchall():
                cells = []
                for v in row:
                    if v is None:
                        cells.append({"type": "null"})
                    elif isinstance(v, int):
                        cells.append({"type": "integer", "value": str(v)})
                    elif isinstance(v, float):
                        cells.append({"type": "float", "value": v})
                    else:
                        cells.append({"type": "text", "value": v})
                rows.append(cells)
            out.append({"type": "ok", "response": {"type": "execute", "result": {
                "cols": cols, "rows": rows, "affected_row_count": 0, "last_insert_rowid": None}}})
        return {"baton": None, "base_url": None, "results": out}


_real_post = db._http_post
_ft = FakeTurso()
_logbuf = _io3.StringIO()
_lh = logging.StreamHandler(_logbuf)
logging.getLogger().addHandler(_lh)
_oldlvl = logging.getLogger().level
logging.getLogger().setLevel(logging.DEBUG)


def _local_count(table):
    return _sq.connect(os.environ["DB_PATH"]).execute(f"select count(*) from {table}").fetchone()[0]


# 1. Nothing configured: the HTTP function is never called.
os.environ.pop("TURSO_DATABASE_URL", None); os.environ.pop("TURSO_AUTH_TOKEN", None)
db._http_post = _ft
db._reset_turso_state()
db.init_db(); db.add_feedback("no turso claim", "true", True, "x"); db.get_cached_verdict("no turso claim", 24); db.ledger()
t("turso: with no env vars nothing calls the HTTP function", _ft.calls == [])
os.environ["TURSO_DATABASE_URL"] = _URL
t("  nor with only the URL set", db._turso_config() is None)
os.environ.pop("TURSO_DATABASE_URL"); os.environ["TURSO_AUTH_TOKEN"] = _SECRET
db.add_feedback("no turso claim 2", None, False, "y")
t("  nor with only the token set", db._turso_config() is None and _ft.calls == [])

# 2. Configured: libsql:// becomes https://, and the token goes only in the header.
os.environ["TURSO_DATABASE_URL"] = _URL
db._reset_turso_state()
t("turso: libsql:// is turned into https://", db._turso_config() == ("https://evident-test.turso.io", _SECRET))
os.environ["TURSO_DATABASE_URL"] = "https://already.turso.io/"
t("  an https URL is kept, without a trailing slash", db._turso_config()[0] == "https://already.turso.io")
os.environ["TURSO_DATABASE_URL"] = _URL
_local_fb = _local_count("feedback")
db.init_db()
t("  init_db creates the tables in Turso too",
  {r[0] for r in _ft.con.execute("select name from sqlite_master where type='table'")}
  >= {"verdict_cache", "query_cache", "checks", "feedback"})
_ft.calls.clear()
db.add_feedback("Turso claim", "false", True, "a note")
t("  feedback is written to Turso, not the local file",
  _ft.con.execute("select claim_key, verdict, helpful, note from feedback").fetchall() == [("turso claim", "false", 1, "a note")]
  and _local_count("feedback") == _local_fb)
_url, _tok, _body = _ft.calls[-1]
t("  one request to /v2/pipeline with the bearer token, a close and typed args",
  len(_ft.calls) == 1 and _url == "https://evident-test.turso.io/v2/pipeline" and _tok == _SECRET
  and _body["requests"][-1] == {"type": "close"}
  and [a["type"] for a in _body["requests"][0]["stmt"]["args"]] == ["text", "text", "integer", "text", "text"]
  and _body["requests"][0]["stmt"]["args"][2] == {"type": "integer", "value": "1"})
db.add_feedback("Turso claim", None, False, "")
t("  None is sent as a null arg",
  _ft.calls[-1][2]["requests"][0]["stmt"]["args"][1] == {"type": "null"})
db.put_cached_verdict("Turso Claim.", "q AND r", "complicated", "Because.", ["P1"], STUDIES,
                      tldr="Short.", still_open="Open.", breakdown={"a": 1})
_got = db.get_cached_verdict("turso claim", 24)
t("  a saved answer round-trips through Turso, integers and JSON decoded",
  _got and _got["verdict"] == "complicated" and _got["still_open"] == "Open."
  and _got["breakdown"] == {"a": 1} and _got["cited_studies"] == ["P1"] and _got["studies"] == STUDIES
  and _ft.con.execute("select count(*) from verdict_cache").fetchone()[0] == 1)
db.log_check("Turso claim", "complicated", "Because.", STUDIES, cached=True)
t("  the check log goes to Turso with its integer and AUTOINCREMENT id",
  _ft.con.execute("select cached, claim_key from checks").fetchall() == [(1, "turso claim")]
  and db.recent_checks(5)[0]["cached"] == 1 and isinstance(db.recent_checks(5)[0]["id"], int))
db.put_cached_query("Turso claim", "v1", "q AND r", "sur", "understood")
t("  the saved search round-trips",
  db.get_cached_query("turso claim", "v1") == ("q AND r", "sur", "understood") and db.get_cached_query("turso claim", "v2") is None)
_ft.calls.clear()
_led = db.ledger()
t("  ledger sends its three reads in one request and decodes the counts",
  len(_ft.calls) == 1 and _led["claims"] == 1 and _led["checks"] == 1 and _led["studies_read"] == 3)
t("  recent_verdicts, all_claims, recent_cached, get_cached_by_key and trending read from Turso",
  db.recent_verdicts(3)[0]["claim_text"] == "Turso Claim."
  and db.all_claims()[0]["claim_text"] == "Turso Claim." and db.recent_cached(3)[0]["verdict"] == "complicated"
  and db.get_cached_by_key("turso claim")["claim_text"] == "Turso Claim." and db.trending(min_checks=1)[0]["checks"] == 1)

# 3. A failing Turso: the check still works, from the local file, and the token is never logged.
_logbuf.truncate(0); _logbuf.seek(0)
db._reset_turso_state(); _ft.fail = OSError("boom " + _SECRET)
_fb_local = _local_count("feedback")
db.init_db()
fg.models.script = [Resp("turso AND fallback"), Resp(VJ("complicated", [1, 3]))]
r = P("Turso fallback check claim", ip="7.7.7.7")
t("turso down: a check still returns 200", r.status_code == 200 and r.get_json()["verdict"] == "complicated", r.get_data(as_text=True)[:200])
t("  and was saved to the local file instead",
  _sq.connect(os.environ["DB_PATH"]).execute("select count(*) from verdict_cache where claim_key='turso fallback check claim'").fetchone()[0] == 1)
db.add_feedback("fallback fb", "true", True, "kept locally")
t("  feedback lands in the local file when Turso fails", _local_count("feedback") == _fb_local + 1)
t("  a bad HTTP status or a bad reply also falls back, never raises",
  (setattr(_ft, "fail", urllib.error.HTTPError("u", 500, "x", {}, None)) or db.get_cached_verdict("zzz", 24)) is None)
db._reset_turso_state()
_ft.fail = None
db._http_post = lambda *a, **k: {"nonsense": True}
t("  a reply with the wrong shape is a miss and a logged warning", db.get_cached_verdict("zzz", 24) is None)
db._http_post = lambda *a, **k: {"results": [{"type": "error", "error": {"message": "no such table: " + _SECRET}}]}
t("  a statement the server rejects is a miss too", db.get_cached_verdict("zzz", 24) is None)
db._http_post = _ft
_logs = _logbuf.getvalue()
t("  the token appears in no log line, and a warning was logged",
  _SECRET not in _logs and "Turso call failed" in _logs, _logs[-300:])

# 4. The cool-down: after repeated failures Turso is skipped for a while.
db._reset_turso_state(); _ft.fail = OSError("down"); _ft.calls.clear()
_clock = [1000.0]
_real_mono = db.time.monotonic
db.time.monotonic = lambda: _clock[0]
db._schema_ready = True
for _ in range(db.TURSO_FAIL_LIMIT):
    db.get_cached_verdict("cool", 24)
_n = len(_ft.calls)
t("turso cool-down: it is tried until the failure limit", _n == db.TURSO_FAIL_LIMIT)
for _ in range(5):
    db.get_cached_verdict("cool", 24)
t("  then skipped: further calls do not reach it", len(_ft.calls) == _n)
_clock[0] += db.TURSO_COOLDOWN + 1
db.get_cached_verdict("cool", 24)
t("  after the cool-down it is tried once, and one more failure skips it again",
  len(_ft.calls) == _n + 1 and (db.get_cached_verdict("cool", 24), len(_ft.calls) == _n + 1)[1])
_clock[0] += db.TURSO_COOLDOWN + 1
_ft.fail = None
db.get_cached_verdict("cool", 24)
t("  and a success clears the count", db._failures == 0 and len(_ft.calls) == _n + 2)
db.time.monotonic = _real_mono

# 5. Boot with Turso down, then up: the first healthy call creates the tables.
_ft2 = FakeTurso(); db._http_post = _ft2; db._reset_turso_state(); _ft2.fail = OSError("down")
db.init_db()
t("turso: init_db with Turso down does not raise", db._schema_ready is False)
_ft2.fail = None; db._reset_turso_state(); db._schema_ready = False
db.add_feedback("late tables", "true", True, "")
t("  once it is up the first call creates the tables and the write lands there",
  _ft2.con.execute("select count(*) from feedback").fetchone()[0] == 1)

# 7. /healthz "store": in-memory state only, four states, nothing leaked.
os.environ["TURSO_DATABASE_URL"] = _URL; os.environ["TURSO_AUTH_TOKEN"] = _SECRET
db._http_post = _ft; _ft.fail = None; _ft.calls.clear(); db._reset_turso_state()
_n0 = len(_ft.calls)
_hzr = c.get("/healthz"); _hzj = _hzr.get_json()
t("healthz store: configured but never called is turso-unused, and healthz made no call",
  _hzj["store"] == "turso-unused" and len(_ft.calls) == _n0 and set(_hzj) == {"ok", "commit", "store"})
db.get_cached_verdict("store status claim", 24)
t("healthz store: after a successful call it is turso", c.get("/healthz").get_json()["store"] == "turso")
_ft.fail = OSError("down " + _SECRET)
db.get_cached_verdict("store status claim", 24)
_hzr = c.get("/healthz"); _body = _hzr.get_data(as_text=True)
t("healthz store: after a failed call it is turso-failing, and leaks no token, URL or error",
  _hzr.get_json()["store"] == "turso-failing" and _SECRET not in _body and "turso.io" not in _body and "down" not in _body)
_ft.fail = None
db._schema_ready = True
db.get_cached_verdict("store status claim", 24)
t("healthz store: it recovers to turso after a good call", c.get("/healthz").get_json()["store"] == "turso")
db._reset_turso_state(); db._last_ok = True; db._skip_until = db.time.monotonic() + 30
t("healthz store: in cool-down it is turso-failing", c.get("/healthz").get_json()["store"] == "turso-failing")
os.environ.pop("TURSO_DATABASE_URL"); os.environ.pop("TURSO_AUTH_TOKEN"); db._reset_turso_state()
t("healthz store: with Turso not configured it is local", c.get("/healthz").get_json()["store"] == "local")
db._http_post = _real_post

# 6. Clean up: the rest of the suite sees plain local SQLite again.
db._http_post = _real_post
os.environ.pop("TURSO_DATABASE_URL", None); os.environ.pop("TURSO_AUTH_TOKEN", None)
db._reset_turso_state()
logging.getLogger().removeHandler(_lh); logging.getLogger().setLevel(_oldlvl)
t("turso: with the variables removed, storage is local again",
  db._turso_usable() is False and db.get_cached_verdict("turso fallback check claim", 24) is not None)

# The paragraph on why the verdict is what it is credits only the studies the
# verdict relied on: the melatonin check named a study it had not used.
t("relied_only takes an uncited study out of a list of citations",
  breakdown.relied_only("It rests on pooled reviews (Studies 1, 2, 4, 6).", {1, 2, 6}, 8)
  == "It rests on pooled reviews (Studies 1, 2, 6).")
t("  drops a sentence that cites only studies not relied on",
  breakdown.relied_only("Reviews agree (Studies 1, 2). One noted no effect (Study 4).", {1, 2}, 8)
  == "Reviews agree (Studies 1, 2).")
t("  says Study, not Studies, when one is left",
  breakdown.relied_only("It rests on reviews (Studies 1, 4).", {1}, 8)
  == "It rests on reviews (Study 1).")
t("  and leaves text that cites nothing alone",
  breakdown.relied_only("Nothing here cites a study.", {1}, 8) == "Nothing here cites a study.")
_RO = [dict(_BD_STUDIES[0]), dict(_BD_STUDIES[1])]
_ro_raw = json.dumps({
    "verdict": "true", "tldr": "Vitamin D lowers infections a little.",
    "explanation": "Pooled trials found a small drop (Study 1).",
    "cited_study_numbers": [1],
    "evidence": ["A meta-analysis of 25 trials with 11,321 people found an odds ratio of 0.88 (Study 1)."],
    "strength": "One pooled review found a small drop (Study 1). A trial found no change (Study 2).",
    "parts": [], "effect_size": "", "applies_to": "", "not_applies_to": "", "unknowns": ""})
_ro = verdict._read_verdict(_ro_raw, None, _RO, "Vitamin D lowers infections")
t("the verdict's 'why' paragraph never credits a study it did not rely on",
  _ro["cited_studies"] == ["D1"] and "Study 2" not in _ro["breakdown"]["strength"]
  and "Study 1" in _ro["breakdown"]["strength"], _ro["breakdown"]["strength"])
_ro2 = verdict._read_verdict(_ro_raw.replace('"cited_study_numbers": [1]', '"cited_study_numbers": []'),
                             None, _RO, "Vitamin D lowers infections")
t("  but a verdict that relied on nothing keeps its paragraph as written",
  "Study 2" in (_ro2["breakdown"] or {}).get("strength", "Study 2"), _ro2.get("breakdown"))
# The line's strength word reads only the relied-on studies.
_LINE = [{"pmid": "S1", "publication_types": ["Journal Article"]},
         {"pmid": "S2", "publication_types": ["Meta-Analysis"]}]
t("the evidence line never takes its strength word from a study the verdict did not use",
  evidence.evidence_line("true", _LINE, ["S1"]).startswith("Moderate evidence")
  and evidence.evidence_line("true", _LINE, ["S2"]).startswith("Strong evidence")
  and evidence.evidence_line("true", _LINE, ["S1", "S2"]).startswith("Strong evidence"),
  evidence.evidence_line("true", _LINE, ["S1"]))

# A saved result whose stored "rests_on" disagrees with its cited studies
# (the audit saw "Rests on 5" beside "3 used") shows the cited count in both
# renderers, and the stored record is left as it was.
_RS = [_study(f"R{i}", f"Trial {i}", abstract="Vitamin D and colds.", types=["Randomized Controlled Trial"])
       for i in range(1, 6)]
_rs_bd = {"strength": "x", "evidence": [], "parts": [], "effect_size": "", "applies_to": "",
          "not_applies_to": "", "unknowns": "", "rests_on": 5}
db.put_cached_verdict("count mismatch saved", "q AND r", "true", "Because (Study 1).",
                      ["R1", "R2", "R3"], _RS, tldr="Short.", still_open="Open.", breakdown=_rs_bd)
_rsh = c.get("/?q=count+mismatch+saved").data.decode()
t("a saved result's 'Rests on' count equals the studies marked used (server render)",
  "Rests on" in _rsh and '<span class="group-count">3 studies</span>' in _rsh
  and '5 studies</span>' not in _rsh, re.findall(r"Rests on.{0,80}", _rsh))
_rsj = P("count mismatch saved", ip="4.4.4.5").get_json()
t("  and in the JSON the browser draws from",
  _rsj["breakdown"]["rests_on"] == 3
  and sum(1 for s_ in _rsj["studies"] if s_["cited_in_verdict"]) == 3, _rsj["breakdown"].get("rests_on"))
with db.get_conn() as _cn:
    _raw_bd = _cn.execute("SELECT breakdown_json FROM verdict_cache WHERE claim_text=?",
                          ("count mismatch saved",)).fetchone()[0]
t("  while the stored record is not rewritten", json.loads(_raw_bd)["rests_on"] == 5)
_js_now = (ROOT / "static" / "app.js").read_text()
_rb = _js_now[_js_now.index("function renderBreakdown"):]
_rb = _rb[:_rb.index("\n}\n")]
t("renderBreakdown counts the used studies itself, not the stored number",
  "const restsOn = studies.filter((s) => s.cited_in_verdict).length" in _rb
  and "bd.rests_on" not in _js_now and _rb.index("const restsOn") < _rb.index("head.textContent = restsOn"))

# A saved record with no search query on file hides the "Searched PubMed for"
# line in the server render and in renderResult, and invents no query.
db.put_cached_verdict("empty query saved", "  ", "true", "Because (Study 1).",
                      ["R1"], _RS, tldr="Short.", still_open="Open.")
_eq = c.get("/?q=empty+query+saved").data.decode()
t("a saved result with an empty search query hides the 'Searched PubMed for' line",
  '<span id="query-what" hidden>' in _eq and "More on PubMed" in _eq, re.findall(r'id="query-what".{0,40}', _eq))
t("  and a result opened from a link carries the search that was stored, not an empty one",
  db.get_cached_by_key("count mismatch saved")["search_query"] == "q AND r"
  and '"search_query_used": "q AND r"' in _rsh.replace('"search_query_used":"', '"search_query_used": "'),
  re.findall(r"search_query_used.{0,40}", _rsh))
t("  and a saved result with a query still shows it",
  "Searched PubMed for <code id=\"query\">q AND r</code>" in _rsh and "id=\"query-what\" hidden" not in _rsh, re.findall(r"query-what.{0,160}", _rsh))
t("  and renderResult hides it the same way",
  '$("query-what").hidden = !usedQuery' in (ROOT / "static" / "app.js").read_text())

# The saved answer keeps only its latest date (a re-check replaces it). The
# first real check comes from the check log, shown only when it is on an
# earlier day, and never invented.
for _cl in ("two dates saved", "one date saved", "log after saved"):
    db.put_cached_verdict(_cl, "q", "true", "Because (Study 1).", ["R1"], _RS, tldr="Short.", still_open="Open.")
with db.get_conn() as _cn:
    _cn.execute("INSERT INTO checks (claim_key, claim_text, cached, checked_at) VALUES (?,?,0,?)",
                ("two dates saved", "two dates saved", "2026-09-01T10:00:00+00:00"))
    _cn.execute("INSERT INTO checks (claim_key, claim_text, cached, checked_at) VALUES (?,?,1,?)",
                ("one date saved", "one date saved", "2026-01-01T10:00:00+00:00"))
    _cn.execute("INSERT INTO checks (claim_key, claim_text, cached, checked_at) VALUES (?,?,0,?)",
                ("log after saved", "log after saved", "2999-01-01T10:00:00+00:00"))
_two = P("two dates saved", ip="4.4.4.6").get_json()
t("a re-checked saved answer carries its first check date beside the refresh date",
  _two["first_checked_at"] == "2026-09-01T10:00:00+00:00" and _two["cached_at"] > _two["first_checked_at"])
t("  a cache hit in the log is not an original check, so no first date is invented",
  P("one date saved", ip="4.4.4.7").get_json()["first_checked_at"] is None)
t("  a log date that is not earlier than the saved date is ignored",
  P("log after saved", ip="4.4.4.8").get_json()["first_checked_at"] is None)
_js_d = (ROOT / "static" / "app.js").read_text()
t("renderResult words the two dates separately and omits a date the record lacks",
  "First checked on ${fmtDate(data.first_checked_at)}. Last re-checked on ${fmtDate(data.cached_at)}" in _js_d
  and "Result from an earlier check." in _js_d and "function hasDate(" in _js_d)

# The saved-record audit script counts the kinds of inconsistency and renders
# every record, on copies, never touching the file it is given.
import subprocess as _sp
_ad = os.path.join(tempfile.mkdtemp(), "audit.db")
import shutil as _sh
_sh.copyfile(os.environ["DB_PATH"], _ad)
_before = open(_ad, "rb").read()
_out = _sp.run([sys.executable, str(ROOT / "scripts" / "saved_record_audit.py"), _ad],
               capture_output=True, text=True, env={**os.environ})
t("the saved-record audit counts the planted mismatches and renders every record",
  "count_mismatch:" in _out.stdout and "render check:" in _out.stdout and _out.returncode == 0
  and "empty_query" in _out.stdout, _out.stdout[-400:] + _out.stderr[-300:])
t("  and leaves the database it was given byte for byte unchanged", open(_ad, "rb").read() == _before)

# ---- input fixes (queue 7, step 5) -------------------------------------------
t("input: the empty-claim error has its own element, hidden until needed",
  '<p class="field-error" id="claim-error" role="alert" hidden></p>' in _home)
_appjs = open("static/app.js", encoding="utf-8").read()
t("  and the script links it with aria-describedby, sets aria-invalid, and clears both on valid text",
  'setAttribute("aria-invalid", "true")' in _appjs and 'removeAttribute("aria-invalid")' in _appjs
  and "clearClaimError()" in _appjs and 'showClaimError("Type or paste the claim first.")' in _appjs)

_cr = [{"claim_text": "Creatine builds strength", "verdict": "true", "issue": 5},
       {"claim_text": "Creatine improves strength.", "verdict": "true", "issue": 4},
       {"claim_text": "Creatine increases strength", "verdict": "true", "issue": 3},
       {"claim_text": "Creatine is not safe for the kidneys", "verdict": "complicated", "issue": 2}]
_g = suggest_mod.suggest("creatine", _cr)
t("suggest: near-duplicate wordings fold into one row that names the other wordings",
  [r["claim_text"] for r in _g][0] == "Creatine builds strength" and len(_g) == 2
  and _g[0]["similar"] == ["Creatine improves strength.", "Creatine increases strength"], _g)
t("  a different claim is its own row and has no 'similar'", "similar" not in _g[1])
import sqlite3 as _sqlite_b  # noqa: E402
_sq = suggest_mod.same_question
_c = lambda text, v="true": {"claim_text": text, "verdict": v}
t("  the same words in another case, order or punctuation are one question",
  _sq(_c("Vitamin D prevents colds"), _c("colds: vitamin d, PREVENTS!"))
  and _sq(_c("Vitamin D prevents colds"), _c("vitamin d PREVENTS colds!")))
t("  a negation never folds: 'is safe' and 'is not safe' stay apart",
  not _sq(_c("Creatine is safe for kidneys"), _c("Creatine is not safe for kidneys")))
t("  a differing number never folds: 500 mg and 5000 mg stay apart",
  not _sq(_c("Vitamin C 500 mg cures colds"), _c("Vitamin C 5000 mg cures colds")))
t("  a different verdict never folds, even for near-identical words",
  not _sq(_c("Creatine builds strength", "true"), _c("Creatine improves strength", "false")))
t("  two or more words must be shared and at most one may differ on each side",
  not _sq(_c("Creatine builds strength"), _c("Creatine builds muscle mass"))
  and not _sq(_c("Zinc helps"), _c("Iron helps")))
t("  the list is named 'Previously checked claims' (visible heading, accessible name) and says a pick opens the saved answer and says a pick opens the saved answer",
  'id="suggest-head">Previously checked claims</p>' in _home
  and 'role="listbox" aria-labelledby="suggest-head"' in _home
  and "Choose one to open its saved answer." in _home)
t("  and the script has no dash in the strings it writes for the list",
  not any(d in _appjs[_appjs.index("function suggestAlso"):_appjs.index("function moveSuggest")] for d in ("\u2014", "\u2013")))

# ---- study designs: what each does and where it stops ----------------------------
# A label says what was done, never how sure to be: design alone does not set
# certainty, so no line calls a design the best or the final word.
_types_src = _appjs.split("const TYPES = [")[1].split("\n];")[0]
_design_rows = _re.findall(r'\["([^"]+)", "[a-z]*", "((?:[^"\\]|\\.)*)"\]', _types_src)
t("study designs: all 17 labels have a line", len(_design_rows) == 17, len(_design_rows))
_overclaim = _re.compile(r"gold standard|as strong as|very strong|proves nothing|best evidence|settles|proof", _re.I)
t("  no line overstates a design: none says 'gold standard', 'very strong' or 'as strong as evidence gets'",
  not [l for l, w in _design_rows if _overclaim.search(w)], _design_rows)
_real = ("Meta-Analysis", "Network Meta-Analysis", "Systematic Review", "Randomized Controlled Trial",
         "Practice Guideline", "Case Reports", "Controlled Clinical Trial", "Clinical Trial",
         "Observational Study", "Multicenter Study", "Comparative Study", "Review")
t("  every study design says what it does and where it stops, in two short sentences",
  all(len(_re.findall(r"[.!?](?:\s|$)", w)) == 2 and len(w.split()) <= 30
      for l, w in _design_rows if l in _real), [(l, w) for l, w in _design_rows if l in _real])
t("  a pooled or randomized design names its limit", all(
  _re.search(r"only as sound|less certain|limits what|can mix", w)
  for l, w in _design_rows if l in ("Meta-Analysis", "Network Meta-Analysis", "Systematic Review", "Randomized Controlled Trial")))
t("  and the lines have no em or en dash", not any(d in w for _, w in _design_rows for d in ("\u2014", "\u2013")))

_js = (ROOT / "static" / "app.js").read_text()
t("errors: the claim is shown on the error screen and no copy points at a hidden box",
  'id="error-claim"' in c.get("/").get_data(as_text=True)
  and '$("error-claim").textContent' in _js and "still in the box" not in _js)

_home2 = c.get("/").get_data(as_text=True)
_foot = _home2.split('<footer class="foot')[1]
t("footer: About, Methodology, Corrections and contact, Privacy, and the medical-scope line",
  all(x in _foot for x in ('href="/about"', 'href="/method"', 'href="/corrections"', 'href="/privacy"',
                           "does not replace a clinician"))
  and 'id="about-link" href="/about"' in _home2)
for _slug in ("about", "method", "corrections", "privacy"):
  _r = c.get("/" + _slug); _h = _r.get_data(as_text=True)
  t(f"/{_slug} page: 200, footer links, Not medical advice, no dashes",
    _r.status_code == 200 and 'href="/corrections"' in _h and "Not medical advice" in _h
    and "\u2014" not in _h and "\u2013" not in _h)
_a = c.get("/about").get_data(as_text=True)
t("about: names Dhruva with the contact address and nothing else about the owner",
  "Dhruva" in _a and "hello@checkevident.com" in _a)

_trust = c.get("/").get_data(as_text=True).split('<section id="trust"')[1].split("</section>")[0]
t("faq: no repeated Check a claim links inside Before you trust a verdict", "to-claim" not in _trust)

_css9 = (ROOT / "static" / "style.css").read_text()
_hero_base = re.search(r"\.hero \{\s*margin: 0; font: \d+ (\d+)px/[\d.]+ var\(--display\); letter-spacing: (-?[\d.]+)em", _css9)
_hero_wide = [int(n) for n in re.findall(r"\n  \.hero \{ font-size: (\d+)px; \}", _css9)]
t("type: the main heading is 34 to 38px on a phone and 48 to 56px wide, tracked no tighter than -0.025em",
  bool(_hero_base) and 34 <= int(_hero_base.group(1)) <= 38 and float(_hero_base.group(2)) >= -0.025
  and len(_hero_wide) == 2 and all(48 <= n <= 56 for n in _hero_wide), (_hero_base and _hero_base.groups(), _hero_wide))
_small_px = [(m.group(0)) for m in re.finditer(r"(?:font-size: ?|font: \d+ )(\d+(?:\.\d+)?)px", _css9) if float(m.group(1)) < 13]
t("type: no font size under 13px in the stylesheet, except the lettering on a book spine (aria-hidden decoration)",
  len(_small_px) == 1 and "10px" in _small_px[0] and "barcol-spine" in _css9.split(_small_px[0])[0].rsplit("}", 1)[1], _small_px)
_body_css = re.search(r"body \{[^}]*font-size: (\d+)px; line-height: ([\d.]+)", _css9)
t("type: body text is 16 to 17px on a 1.6 line height",
  bool(_body_css) and 16 <= int(_body_css.group(1)) <= 17 and 1.55 <= float(_body_css.group(2)) <= 1.65)
_qa_src = (ROOT / "scripts" / "qa.mjs").read_text()
t("touch: qa.mjs fails any tap target under 44px, and the footer links are 44px rows on every page",
  "h < 43.5 || w < 43.5" in _qa_src and "tap target < 44px" in _qa_src
  and re.search(r"\.foot-links a \{[^}]*min-height: 44px", _css9) is not None
  and all('class="fine foot-links"' in c.get(u).get_data(as_text=True) for u in ("/", "/about", "/privacy")))
_fl = [re.search(r'<p class="fine foot-links">(.*?)</p>', c.get(u).get_data(as_text=True), re.S).group(1) for u in ("/", "/about", "/privacy")]
t("footer: the separators are CSS on the links, not dots in the text, so none can end or start a wrapped line",
  all("\u00b7" not in x and x.count("<a ") == 4 and x.startswith('<span class="foot-row">') for x in _fl)
  and re.search(r"\.foot-links a::before \{[^}]*content: \"\\00b7\"", _css9) is not None
  and re.search(r"\.foot-links \{[^}]*overflow: hidden", _css9) is not None, _fl[0])
_design9 = (ROOT / "DESIGN.md").read_text()
t("DESIGN.md: palette roles are written down and say green never signals safe or a good verdict",
  "Palette roles" in _design9 and "Green never signals" in _design9
  and all(h in _design9 for h in ("#f1f2ee", "#fafaf8", "#121514", "#434a46", "#5d6560", "#2c5848", "#d4e4da", "#0f1211")))
_tpl_who = (ROOT / "templates" / "index.html").read_text()
t("who: with only a 'does not apply' sentence, the heading says so and no empty 'applies to' heading is left",
  "Who this does not apply to" in _tpl_who and "{%- if who_yes %}<p class=\"section-label label-2\">" in _tpl_who
  and "Who this does not apply to" in _js and "(yes ? `<p class=\"section-label label-2\">" in (ROOT / "static" / "app.js").read_text())

# ---- B6 F1: one certainty per page --------------------------------------------
# The apple cider vinegar page said "low certainty" in the short answer,
# "Strong evidence of a small effect" in the evidence line and "moderate
# evidence" in the breakdown. Built from that page's shape.
_ACV = [
    {"pmid": "V1", "title": "Apple cider vinegar and body composition: a meta-analysis",
     "abstract": "Ten trials with 789 adults. Body weight fell (SMD -0.39).",
     "journal": "J", "year": "2025", "publication_types": ["Meta-Analysis"],
     "authors": ["A"], "data_banks": None, "url": "uV1"},
    {"pmid": "V2", "title": "Vinegar and glycaemic control in type 2 diabetes",
     "abstract": "Seven studies. Fasting blood sugar fell by 21.9 mg/dL.",
     "journal": "J", "year": "2024", "publication_types": ["Systematic Review", "Meta-Analysis"],
     "authors": ["B"], "data_banks": None, "url": "uV2"},
]
_ACV_BD = {"strength": "The reviews and trials provide moderate evidence for blood sugar "
                       "improvements (Studies 1, 2).",
           "says": ["Strong evidence shows fasting blood sugar fell by 21.9 mg/dL (Study 2)."],
           "evidence": [], "parts": [], "effect_size": "", "applies_to": "",
           "not_applies_to": "", "unknowns": "", "effect": {"label": "small"}}
_acv = appmod._check_response(
    "apple cider vinegar helps diabetes", "vinegar AND diabetes", "complicated",
    "It may lower blood sugar a little, but the studies are mixed and low certainty.",
    "Two reviews found small drops (Studies 1, 2).", ["V1", "V2"], _ACV, "http://x",
    breakdown=_ACV_BD)
_acv_words = " ".join([_acv["tldr"], _acv["evidence_line"], _acv["breakdown"]["strength"],
                       *_acv["breakdown"]["says"]]).lower()
t("F1: the evidence line is never surer than the short answer's stated certainty, in its own design word",
  _acv["evidence_line"].startswith("Weak evidence")
  and evidence.line_certainty(_acv["evidence_line"]) == "low", _acv["evidence_line"])
t("F1: the short answer, the evidence line and the breakdown state one certainty level",
  evidence.stated_certainty(_acv["tldr"]) == evidence.line_certainty(_acv["evidence_line"])
  == evidence.stated_certainty(_acv["breakdown"]["strength"])
  == evidence.stated_certainty(*_acv["breakdown"]["says"]) == "low"
  and "moderate evidence" not in _acv_words and "strong evidence" not in _acv_words,
  (_acv["evidence_line"], _acv["breakdown"]["strength"], _acv["breakdown"]["says"]))
t("F1: a capped phrase keeps its sentence and its capital letter",
  _acv["breakdown"]["says"][0].startswith("Weak evidence shows fasting blood sugar"),
  _acv["breakdown"]["says"])
t("F1: a negated certainty phrase is neither counted nor rewritten",
  evidence.stated_certainty("There is no high-quality evidence for this.") is None
  and evidence.cap_certainty("There is no strong evidence for this.", "low")
  == "There is no strong evidence for this.")
t("F1: nothing is ever rewritten upwards",
  evidence.cap_certainty("The studies are low certainty.", "high") == "The studies are low certainty.")
t("F1: a review's own lower rating caps the line even when it is not the first review",
  evidence.evidence_line("complicated", _ACV + [
      {"pmid": "V3", "title": "Vinegar review", "publication_types": ["Meta-Analysis"],
       "abstract": "The certainty of evidence was very low."}], ["V1", "V2", "V3"])
  .startswith("Very low certainty"))
db.put_cached_verdict("apple cider vinegar helps diabetes", "vinegar AND diabetes", "complicated",
                      "Two reviews found small drops (Studies 1, 2).", ["V1", "V2"], _ACV,
                      tldr="It may lower blood sugar a little, but the studies are mixed and low certainty.",
                      still_open="Doses vary.", breakdown=_ACV_BD)
_acv_html = c.get("/?q=apple%20cider%20vinegar%20helps%20diabetes").data.decode()
t("F1: the server-rendered page carries no surer certainty phrase than the short answer",
  "moderate evidence" not in _acv_html.lower() and "Strong evidence shows" not in _acv_html
  and "weak evidence for blood sugar" in _acv_html.lower())

# ---- B6 F2: the label never exceeds the main review's own rating ---------------
# "exercise helps depression" said "Moderate certainty evidence" while its main
# review (a network meta-analysis) rated confidence low to very low in CINeMA's
# words, which the certainty reader did not know; a smaller review's "moderate
# certainty" stood in for it.
_EXD = [
    {"pmid": "E1", "title": "Effect of exercise for depression: systematic review and network meta-analysis",
     "abstract": "218 unique studies with 14170 participants. Walking or jogging reduced depression "
                 "(g -0.62). Only one study met the Cochrane criteria for low risk of bias. As a "
                 "result, confidence in accordance with CINeMA was low for walking or jogging and "
                 "very low for other treatments.",
     "journal": "BMJ", "year": "2024", "publication_types": ["Systematic Review", "Network Meta-Analysis"],
     "authors": ["N"], "data_banks": None, "url": "uE1"},
    {"pmid": "E6", "title": "Impact of postpartum physical activity on maternal depression",
     "abstract": "35 studies. Moderate certainty evidence from RCTs showed that exercise reduced "
                 "the severity of postpartum depressive symptoms (SMD -0.52).",
     "journal": "BJSM", "year": "2025", "publication_types": ["Systematic Review", "Meta-Analysis"],
     "authors": ["D"], "data_banks": None, "url": "uE6"},
]
t("F2: CINeMA's 'confidence ... was low for X and very low for Y' is read as the review's own rating",
  evidence.review_certainty(_EXD[0]) == "very low"
  and evidence.certainty_range(_EXD[0]) == "very low to low", evidence.certainty_range(_EXD[0]))
t("F2: a confidence interval is never read as a rating",
  evidence.review_certainty({"publication_types": ["Meta-Analysis"],
                             "abstract": "The 95% confidence interval was 0.8 to 1.2 with high heterogeneity."})
  is None)
_exl = evidence.evidence_line("true", _EXD, ["E1", "E6"], "exercise helps depression")
t("F2: a main review rated low or very low never yields a Moderate or Strong label",
  not _exl.startswith(("Moderate", "Strong", "High")) and _exl.startswith("Very low"), _exl)
_exr = appmod._check_response("exercise helps depression", "exercise AND depression", "true",
                              "Exercise helps depression.", "Reviews found less depression (Studies 1, 2).",
                              ["E1", "E6"], _EXD, "http://x",
                              breakdown={"strength": "Moderate certainty evidence supports it (Studies 1, 2).",
                                         "says": [], "evidence": [], "parts": []})
t("F2: the breakdown's 'moderate certainty' is brought down to the main review's rating",
  "Moderate certainty" not in _exr["breakdown"]["strength"]
  and _exr["breakdown"]["strength"].startswith("Very low certainty"), _exr["breakdown"]["strength"])
_exv = verdict._read_verdict(json.dumps({
    "verdict": "true", "tldr": "Exercise helps depression.",
    "explanation": "Pooled studies found less depression (Studies 1, 2).", "still_open": "Best dose.",
    "cited_study_numbers": [1, 2], "evidence": [], "parts": [], "strength": "", "effect_size": "",
    "applies_to": "", "not_applies_to": "", "unknowns": ""}), None, _EXD, "exercise helps depression")
t("F2: the existing rule then holds: a main review rated low cannot carry 'Likely true'",
  _exv["verdict"] == "complicated", _exv["verdict"])

# ---- B6 F3: prose is held to the studies it cites ------------------------------
# D3: a postpartum-only study was rendered "pregnant women". D4: a who-line
# cited a study not relied on. D5: prose linked studies listed as read, not
# relied on. D6: "15 to 30 mL" credited to studies giving 20 and 30, and
# "thousands of people" of a review of 1,937.
t("F3: a postpartum-only study is labelled people after giving birth, never 'pregnant'",
  "pregnan" not in (evidence.population(_EXD[1]) or "").lower()
  and "birth" in (evidence.population(_EXD[1]) or ""), evidence.population(_EXD[1]))
_f3s = [
    {"pmid": "P1", "title": "Apple cider vinegar and glucose: a randomised trial",
     "abstract": "120 adults took 20 mL or 30 mL of vinegar daily for 12 weeks. Fasting glucose fell.",
     "journal": "J", "year": "2021", "publication_types": ["Randomized Controlled Trial"],
     "authors": ["A"], "data_banks": None, "url": "u1"},
    {"pmid": "P2", "title": "Vinegar intake and weight in adults",
     "abstract": "A review of 9 trials with 1,937 adults. Vinegar lowered weight slightly.",
     "journal": "J", "year": "2023", "publication_types": ["Systematic Review"],
     "authors": ["B"], "data_banks": None, "url": "u2"},
    {"pmid": "P3", "title": "Vinegar in a mouse model of obesity",
     "abstract": "Mice fed 15 mL/kg vinegar gained less fat. Postpartum mice were studied.",
     "journal": "J", "year": "2019", "publication_types": ["Journal Article"],
     "authors": ["C"], "data_banks": None, "url": "u3"},
]
_f3r = appmod._check_response(
    "apple cider vinegar helps weight loss", "vinegar AND weight", "complicated",
    "Small trials found small changes.",
    "A trial gave 20 mL or 30 mL a day (Study 1). People took 15 to 30 mL a day (Studies 1, 2). "
    "A review pooled thousands of people (Study 2). Mice gained less fat (Study 3). "
    "Pregnant women gained less weight (Study 1).",
    ["P1", "P2"], _f3s, "http://x", still_open="Whether it lasts (Study 3).",
    breakdown={"says": [], "evidence": [], "parts": [], "strength": "", "effect_size": "",
               "applies_to": "Adults (Study 1).",
               "not_applies_to": "Adults with untreated kidney failure (Study 3). Children were not in either trial (Study 1)."})
_f3e = _f3r["explanation"]
t("F3: a sentence whose figures are in the study it cites is kept",
  "20 mL or 30 mL a day (Study 1)" in _f3e, _f3e)
t("F3: '15 to 30 mL' credited to studies that give 20 and 30 is dropped",
  "15 to 30" not in _f3e, _f3e)
t("F3: 'thousands of people' of a review of 1,937 is dropped",
  "thousands" not in _f3e, _f3e)
t("F3: prose links only studies the verdict relied on",
  "Study 3" not in _f3e and "Mice" not in _f3e, _f3e)
t("F3: a group the cited study never names ('pregnant women') is dropped",
  "Pregnant" not in _f3e, _f3e)
t("F3: a who-line citing a study not relied on is removed, an honest absence is kept",
  "kidney" not in _f3r["breakdown"]["not_applies_to"]
  and "Children were not in either trial" in _f3r["breakdown"]["not_applies_to"],
  _f3r["breakdown"]["not_applies_to"])
t("F3: 'still open' is never touched",
  _f3r["still_open"] == "Whether it lasts (Study 3).", _f3r["still_open"])
t("F3: a warning no cited study supports is removed, like any other sentence",
  "injure" not in breakdown.held_to_sources(
      "Undiluted vinegar can injure the throat (Study 3).", _f3s, {1, 2}))
t("F3: with nothing relied on, the reasons studies were set aside stay",
  breakdown.held_to_sources("Mice gained less fat (Study 3).", _f3s, set())
  == "Mice gained less fat (Study 3).")

# ---- B6 F7: who runs Evident -----------------------------------------------------
# "Is it biased? Who made it?" was answered only "an independent project". The
# page says what is true now and shows owner details only once they are set.
import re as _re7
_ab = c.get("/about")
_abt = _ab.get_data(as_text=True)
_abv = _re7.sub(r"\{#.*?#\}|<!--.*?-->|<[^>]+>", " ", _abt, flags=_re7.S)
t("F7: /about exists, says who checks a result, how it is paid for, and carries the disclaimer",
  _ab.status_code == 200 and "No doctor or scientist reviews each result" in _abt
  and "shows no ads" in _abt and "Not medical advice" in _abt)
t("F7: /about ships no visible placeholder and no dashes",
  "[" not in _abv and "\u2014" not in _abv and "\u2013" not in _abv)
t("F7: home, a result and the privacy page all link to /about",
  'href="/about"' in c.get("/").get_data(as_text=True)
  and 'href="/about"' in c.get("/privacy").get_data(as_text=True)
  and 'href="/about"' in c.get("/?q=conclusions+render").get_data(as_text=True))
# main's rebuilt /about is a fixed page (no env-configured owner block): it
# names Dhruva as the person who runs Evident, and says so before the contact line.
t("F7: /about names Dhruva as the owner, and no owner-config block is promised",
  "made and run by Dhruva" in _abt and "It is based in" not in _abt)

t("contact: /about and /corrections give hello@checkevident.com as a plain mailto link, with no form",
  'href="mailto:hello@checkevident.com"' in _abt and 'href="mailto:hello@checkevident.com"' in c.get("/corrections").get_data(as_text=True)
  and "<form" not in c.get("/corrections").get_data(as_text=True) and "<form" not in _abt
  and "\u2014" not in c.get("/corrections").get_data(as_text=True))
_old_ce = appmod.CONTACT_EMAIL
appmod.CONTACT_EMAIL = None
_noce = c.get("/corrections").get_data(as_text=True)
appmod.CONTACT_EMAIL = _old_ce
t("contact: with no address set, no mailto is shown", "mailto:" not in _noce)

# ---- B6 F9: a linkable method page -----------------------------------------------
_me = c.get("/method")
_met = _me.get_data(as_text=True)
_mev = _re7.sub(r"\{#.*?#\}|<!--.*?-->|<[^>]+>", " ", _met, flags=_re7.S)
t("F9: /method exists with a version and a last-changed date",
  _me.status_code == 200 and f"Method version {appmod.METHOD_VERSION}, last changed {appmod.METHOD_CHANGED}" in _met)
t("F9: /method states the number of abstracts the code actually reads",
  f"up to {appmod.MAX_STUDIES} abstracts" in _met and appmod.MAX_STUDIES == 8)
t("F9: /method names the code's own rules: no citation means Not enough evidence; label capped by the review",
  "becomes \u201cNot enough evidence\u201d" in _met and "never higher than the lowest rating" in _met
  and "Not medical advice" in _met)
t("F9: /method has no placeholder and no dashes",
  "[" not in _mev and "\u2014" not in _mev and "\u2013" not in _mev)
t("F9: the How it works sheet and every result link to /method",
  'href="/method"' in c.get("/").get_data(as_text=True)
  and 'href="/method"' in c.get("/?q=conclusions+render").get_data(as_text=True))

# ---- B6 F10: review dates on results ---------------------------------------------
_rl = appmod._review_line("2026-11-14T09:00:00+00:00",
                          [{"pmid": "a", "year": "2019"}, {"pmid": "b", "year": "2025"},
                           {"pmid": "c", "year": "2001"}], {"a", "b"})
t("F10: the line gives the check date and the years of the studies relied on, not those read",
  _rl == "Checked 14 November 2026, from studies published 2019 to 2025.", _rl)
t("F10: one study, one year, and none are each worded plainly",
  appmod._review_line("2026-11-14T09:00:00+00:00", [{"pmid": "a", "year": "2024"}], {"a"})
  == "Checked 14 November 2026, from a study published in 2024."
  and appmod._review_line("2026-11-14T09:00:00+00:00", [{"pmid": "a", "year": "2024"}], set())
  == "Checked 14 November 2026.")
db.put_cached_verdict("review dates render", "q", "complicated", "Because (Study 1).",
                      ["C1", "C2"], _CON_STUDIES, tldr="Short take.", still_open="Open.")
_rlp = P("review dates render", ip="4.4.4.5").get_json()
_rlh = c.get("/?q=review+dates+render").get_data(as_text=True)
t("F10: a result carries the line, and the server-rendered page shows it before any script",
  str(_rlp.get("review_line")).startswith("Checked ") and str(_rlp.get("review_line")) in _rlh
  and 'id="review-line"' in _rlh, (_rlp.get("error"), sorted(_rlp)[:6]))
t("F10: renderResult fills the same line",
  'data.review_line' in pathlib.Path("static/app.js").read_text())

# ---- B6 F8: a corrections route --------------------------------------------------
_co = c.get("/corrections")
_cot = _co.get_data(as_text=True)
_cov = _re7.sub(r"\{#.*?#\}|<!--.*?-->|<[^>]+>", " ", _cot, flags=_re7.S)
t("F8: /corrections exists, points to the existing report button, and carries the disclaimer",
  _co.status_code == 200 and "Report a wrong answer" in _cot and "Not medical advice" in _cot)
t("F8: it promises no response time and no placeholder shows",
  not _re7.search(r"\bwithin\b|\bdays?\b(?! later)|\bhours?\b|\bweeks?\b", _cov)
  and "[" not in _cov and "\u2014" not in _cov and "\u2013" not in _cov, _cov[:200])
t("F8: the report form, /about and /method link to it",
  'href="/corrections"' in c.get("/").get_data(as_text=True).split('id="report-form"')[1].split("</form>")[0]
  and 'href="/corrections"' in _abt and 'href="/corrections"' in _met)
_fbr = c.post("/api/feedback", json={"claim": "review dates render", "report": True, "note": "wrong"},
              environ_base={"REMOTE_ADDR": "9.9.9.1"})
t("F8: reports still go through the one anonymous feedback route, and set no cookie",
  _fbr.status_code == 200 and _fbr.get_json() == {"ok": True} and "Set-Cookie" not in _fbr.headers
  and "Set-Cookie" not in _co.headers)

# ---- B6 F13: the count and the list numbers agree --------------------------------
_lg = db.ledger()["claims"]
_rv = db.recent_verdicts(500)
t("F13: the newest entry's number equals the Claims checked count, with no gaps",
  _rv and _rv[0]["issue"] == _lg and sorted(r["issue"] for r in _rv) == list(range(1, _lg + 1)),
  (_lg, [r["issue"] for r in _rv[:3]]))

# ---- Review fix 1: harm warnings are never dropped -------------------------------
# The first gate removed safety warnings (the review's f3_drops.json). Each of
# these is verbatim from that list, or from the review's brief.
_h8 = _f3s + [{"pmid": f"H{i}", "title": "Background paper", "abstract": "A narrative review.",
               "journal": "J", "year": "2020", "publication_types": ["Review"], "authors": ["x"],
               "data_banks": None, "url": "u"} for i in range(4, 9)]
_HARMS = {
    "Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.":
        "Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.",
    "Antibiotics should not be used for the common cold or influenza (Study 3).":
        "Antibiotics should not be used for the common cold or influenza.",
    "Alcohol is a well-established teratogen that can cause variable physical and behavioral effects on the fetus (Study 7).":
        "Alcohol is a well-established teratogen that can cause variable physical and behavioral effects on the fetus.",
    "Raw milk consumption is linked to outbreaks of campylobacteriosis and brucellosis (Study 6, Study 8).":
        "Raw milk consumption is linked to outbreaks of campylobacteriosis and brucellosis.",
    "Indirect evidence shows various functional and structural abnormalities in children with prenatal alcohol exposure (Study 3).":
        "Indirect evidence shows various functional and structural abnormalities in children with prenatal alcohol exposure.",
    # Study 1 never mentions pregnancy or diabetes, nor 99 mL or 12,000: the
    # warning stays, without crediting a study with it (second review fix 3).
    "It is unsafe in pregnancy (Study 1).": "It is unsafe in pregnancy.",
    "People with diabetes should not stop their medicine (Study 1).":
        "People with diabetes should not stop their medicine.",
    "Doses of 99 mL a day burned the throat in 12,000 people (Studies 1, 3).":
        "Doses of 99 mL a day burned the throat in 12,000 people.",
}
for _hin, _hout in _HARMS.items():
    _hgot = breakdown.held_to_sources(_hin, _h8, {1, 2})
    # Q8: an unsupported warning is removed too. The old expected text stays in
    # the table as the record of what the retired caution rule kept.
    _hok = "injure" not in _hgot and "Antibiotics" not in _hgot and "Alcohol" not in _hgot and "milk" not in _hgot and "abnormal" not in _hgot and "unsafe" not in _hgot and "stop their" not in _hgot and "burned" not in _hgot
    t(f"fix 1: a warning no cited study supports is removed: {_hin[:48]}", _hok, _hgot)
for _hin in ["Most acute nose and throat infections are caused by viruses, not bacteria (Study 3)."]:
    t(f"fix 1 (fifth review: softened or negated, so now dropped): {_hin[:48]}",
      breakdown.held_to_sources(_hin, _h8, {1, 2}) == "")
t("fix 1: the harm rule also holds inside the breakdown",
  breakdown.hold_breakdown({"says": ["Antibiotics should not be used for the common cold or influenza (Study 3)."],
                            "evidence": [], "parts": [], "strength": "", "effect_size": "",
                            "applies_to": "", "not_applies_to": "", "unknowns": ""},
                           _h8, {1, 2})["says"] == [])

# ---- Review fix 2: the two "who" lines, and untested claims ----------------------
_na = ("Heavy drinkers, people with contraindications to alcohol, and those outside the "
       "studied age ranges (Study 1).")
t("fix 2: a 'does not apply to' line naming groups its record never mentions is kept",
  breakdown.held_to_sources(_na, _h8, {1, 2}, who="not") == _na
  and breakdown.held_to_sources("Pregnant women (Study 1).", _h8, {1, 2}, who="not") == "Pregnant women (Study 1).")
t("fix 2: a 'does not apply to' line citing only a study not relied on still goes",
  breakdown.held_to_sources("Adults with untreated kidney failure (Study 3).", _h8, {1, 2}, who="not") == "")
_ap = breakdown.held_to_sources("Adults and smokers (Study 1).", _h8, {1, 2}, who="applies")
t("fix 2: an 'applies to' line loses only the clause its record does not support",
  _ap == "Adults (Study 1).", _ap)
t("fix 2: an absence stated as 'does not apply to' or 'not in' is never held to naming the group",
  breakdown.held_to_sources("The results do not apply to pregnant women (Study 1).", _h8, {1, 2})
  == "The results do not apply to pregnant women (Study 1)."
  and breakdown.held_to_sources("The trial does not cover smokers (Study 1).", _h8, {1, 2})
  == "The trial does not cover smokers (Study 1).")
t("fix 2: a statement that the claim was not tested is never dropped",
  breakdown.held_to_sources("Neither tested whether diabetes went away (Study 3).", _h8, {1, 2})
  == "Neither tested whether diabetes went away."
  and breakdown.held_to_sources("No trial tested whether smokers lost weight (Study 1).", _h8, {1, 2})
  == "No trial tested whether smokers lost weight (Study 1).")

# ---- Review fix 3: prose may only lower the line, in design words -------------
_ma = {"pmid": "M", "title": "X for pain: a meta-analysis",
       "abstract": "Meta-analysis of 30 randomised placebo-controlled trials with 9000 adults. Pain fell.",
       "journal": "J", "year": "2022", "publication_types": ["Meta-Analysis", "Systematic Review"],
       "authors": ["a"], "data_banks": None, "url": "u"}
t("fix 3: prose stating the same level keeps the design word",
  evidence.evidence_line("true", [_ma], ["M"], "x helps pain", stated="high") == "Strong evidence for this claim")
t("fix 3: prose stating a lower level lowers the line in a design word, never 'certainty'",
  evidence.evidence_line("true", [_ma], ["M"], "x helps pain", stated="low") == "Weak evidence for this claim")
t("fix 3: prose can never raise the line",
  evidence.evidence_line("true", [dict(_ma, publication_types=["Case Reports"])], ["M"], "x helps pain",
                         stated="high").startswith("Weak"))
_mar = dict(_ma, abstract="Meta-analysis of 30 RCTs. Pain fell (low certainty evidence).")
t("fix 3: 'certainty' wording comes only from a review's own rating",
  evidence.evidence_line("true", [_mar], ["M"], "x helps pain").startswith("Low certainty"))

# ---- Review fix 4: cap_certainty rewrites only the prose's own word on the body --
t("fix 4: 'More high-quality evidence is needed' is left alone, and states no level",
  evidence.cap_certainty("More high-quality evidence is needed.", "low") == "More high-quality evidence is needed."
  and evidence.stated_certainty("More high-quality evidence is needed.") is None)
t("fix 4: 'would' and 'larger ... needed' sentences are never rewritten",
  evidence.cap_certainty("A larger trial would give strong evidence.", "low") == "A larger trial would give strong evidence."
  and evidence.cap_certainty("Larger trials with moderate certainty are needed.", "low")
  == "Larger trials with moderate certainty are needed.")
t("fix 4: a phrase credited to a study or review is never rewritten",
  evidence.cap_certainty("Study 2 found moderate certainty evidence.", "low") == "Study 2 found moderate certainty evidence."
  and evidence.cap_certainty("The review rated the evidence high certainty.", "low")
  == "The review rated the evidence high certainty.")
t("fix 4: the prose's own statement about the evidence is still brought down, in a design word",
  evidence.cap_certainty("There is strong evidence it helps (Study 1).", "low") == "There is weak evidence it helps (Study 1).")

# ---- Review fix 5: a CINeMA "confidence" needs CINeMA, GRADE or the evidence -----
_cA = dict(_ma, pmid="A", abstract="Meta-analysis of 20 RCTs. Participants' confidence was high after treatment. Pain fell.")
_cB = dict(_ma, pmid="B", abstract="Meta-analysis of 12 RCTs. Pain fell (low certainty evidence).")
t("fix 5: an earlier review's 'confidence was high' cannot displace a later low-rated lead review",
  evidence.lead_certainty([_cA, _cB], ["A", "B"], "", strict=True) == "low"
  and evidence.certainty_cap([_cA, _cB], ["A", "B"]) is not None)
t("fix 5: self-confidence and someone's confidence are not ratings",
  all(evidence.review_certainty(dict(_ma, abstract=a)) is None for a in (
      "Meta-analysis. Self-confidence was high in the exercise group.",
      "Meta-analysis. Patient confidence in the results was high.",
      "Meta-analysis. Confidence was high after training.")))
t("fix 5: confidence in the evidence, or with CINeMA named, still counts",
  evidence.review_certainty(dict(_ma, abstract="Meta-analysis. Confidence in the evidence was low.")) == "low"
  and evidence.review_certainty(dict(_ma, abstract="Meta-analysis. Confidence according to CINeMA was moderate.")) == "moderate")

# ---- Review fix 6: the method and about pages say only what is true -------------
_met6 = c.get("/method").get_data(as_text=True)
_abt6 = c.get("/about").get_data(as_text=True)
t("fix 6: /method says the prose check covers the explanation and breakdown, not the short answer",
  "In the explanation and the breakdown, though not in the short answer" in _met6)
t("fix 6: /method says guidance can decide some claims with no study cited, and never says 'safe' alone",
  "even when no study is cited" in _met6 and "never on its own that something is safe" in _met6)
t("fix 6: the removal sentence on /method is backed by the gate",
  "even a warning" in _met6
  and breakdown.held_to_sources("It is unsafe in pregnancy (Study 3).", _h8, {1, 2}) == "")
t("fix 6: /about names the guidance exception instead of 'whatever the model wrote'",
  "whatever the model wrote" not in _abt6 and "without a study" in _abt6)
t("fix 6: guidance really can stamp a forgo-treatment claim with nothing cited (the exception /method states)",
  "Applies even with nothing cited" in pathlib.Path("verdict.py").read_text())

# ---- Review fix 7: the Money sentence on /about stays true -----------------------
_abt7 = c.get("/about").get_data(as_text=True)
t("fix 7: /about no longer promises a free tier it cannot know", "free tier" not in _abt7)
t("fix 7: the 'sells' promise is limited to this site, ads, accounts and data",
  "sells nothing" not in _abt7 and "This site shows no ads, has no accounts and sells no data." in _abt7
  and "sells nothing" not in c.get("/").get_data(as_text=True))

# ---- Review fix 8: list numbers cost one count, not one per row ------------------
import contextlib as _cl8
def _work8(n):
    """VM steps and statements for recent_verdicts(n) and all_claims() on a fresh n-row table."""
    _old_path, _old_conn = db.DB_PATH, db.get_conn
    db.DB_PATH = os.path.join(tempfile.mkdtemp(), "f8.db")
    _con = _sqlite_b.connect(db.DB_PATH); _con.executescript(db.SCHEMA)
    # Whole rows (a short answer and a still-open line), since a list shows only those.
    _con.executemany("INSERT INTO verdict_cache (claim_key, claim_text, verdict, explanation, tldr, still_open, created_at) "
                     "VALUES (?, ?, 'complicated', 'x', 'Mixed evidence.', 'Longer trials.', ?)",
                     [(f"k{i}", f"Claim {i}", f"2026-01-01T00:00:{i % 7:02d}") for i in range(n)])
    _con.commit(); _con.close()
    tally = {"steps": 0, "sql": []}
    @_cl8.contextmanager
    def _counting():
        conn = _sqlite_b.connect(db.DB_PATH); conn.row_factory = _sqlite_b.Row
        conn.set_trace_callback(tally["sql"].append)
        def _tick():
            tally["steps"] += 1
            return 0
        conn.set_progress_handler(_tick, 100)
        try:
            yield conn
        finally:
            conn.close()
    db.get_conn = _counting
    try:
        rv = db.recent_verdicts(n)
        rv_steps, rv_sql = tally["steps"], len(tally["sql"])
        tally["steps"], tally["sql"] = 0, []
        ac = db.all_claims(n)
        return rv, rv_steps, rv_sql, ac, tally["steps"], len(tally["sql"])
    finally:
        db.DB_PATH, db.get_conn = _old_path, _old_conn
_s8 = _work8(60)
_b8 = _work8(480)
t("fix 8: numbers still run newest = count down to 1 with ties broken by insertion",
  [r["issue"] for r in _b8[0]] == list(range(480, 0, -1)), [r["issue"] for r in _b8[0][:3]])
t("fix 8: recent_verdicts issues two statements whatever the table size",
  _s8[2] == 2 and _b8[2] == 2, (_s8[2], _b8[2]))
t("fix 8: work grows about linearly with rows (8x rows, well under 20x steps), not quadratically",
  _b8[1] < 20 * max(_s8[1], 1), (_s8[1], _b8[1]))
t("fix 8: all_claims (type-ahead) does no numbering at all: one statement, no issue field",
  _b8[5] == 1 and "issue" not in _b8[3][0] and _b8[4] < 20 * max(_s8[4], 1), (_b8[5], _b8[3][0]))

# ---- Review fix 9: a shared link scopes certainty to the outcome, like a check ---
_f9c = "Ginger reduces vomiting in adults"
db.put_cached_verdict(_f9c, _GIN_Q, "complicated", "Ginger may reduce vomiting (Study 1).",
                      ["L3"], [_MODREV], tldr="Ginger may reduce vomiting.", still_open="Doses vary.")
_f9k = db.get_cached_by_key(db.normalize_claim(_f9c))
_f9a = P(_f9c, "9.9.9.9")
_f9line = (_f9a.get_json() or {}).get("evidence_line", "")
_f9h = c.get("/", query_string={"q": _f9c}).get_data(as_text=True)
t("fix 9: get_cached_by_key returns the stored search query", _f9k and _f9k["search_query"] == _GIN_Q)
t("fix 9: the streamed check reads the review on the claim's outcome (moderate, not the lowest)",
  _f9line.startswith("Moderate"), _f9line)
t("fix 9: the shared link prints the same evidence line as the streamed check",
  _f9line and _f9line in _f9h and "Low certainty evidence" not in _f9h)

# ---- Review fix 10: a card never 500s when the gate fails -------------------------
_f10k = db.normalize_claim(_f9c)
_f10orig, _f10seen = appmod._check_response, {}
def _f10boom(*a, **k):
    raise ValueError("gate broke")
_f10card = appmod.og.render_card
def _f10spy(claim, verdict_value, tldr, *a, **k):
    _f10seen["tldr"] = tldr
    return _f10card(claim, verdict_value, tldr, *a, **k)
appmod._check_response, appmod.og.render_card = _f10boom, _f10spy
try:
    _f10r = c.get(f"/og/{_f10k}.png")
finally:
    appmod._check_response, appmod.og.render_card = _f10orig, _f10card
t("fix 10: a card whose gate raises still renders a PNG with the stored short answer",
  _f10r.status_code == 200 and _f10r.mimetype == "image/png" and _f10seen.get("tldr") == "Ginger may reduce vomiting.",
  (_f10r.status_code, _f10seen))

# ---- Review fix 11: number matching reads counts as the records give them --------
_n11 = [{"pmid": "N1", "title": "Coffee and cardiovascular disease: a dose-response meta-analysis",
         "abstract": "Thirty-six studies were included with 1 279 804 participants and 36 352 CVD cases. "
                     "In a trial, usual care (n = 25) was compared with an app (n = 29).",
         "journal": "J", "year": "2020", "publication_types": ["Meta-Analysis"], "authors": ["x"],
         "data_banks": None, "url": "u"},
        {"pmid": "N2", "title": "Walking in adults with diabetes",
         "abstract": "Adults with diabetes walked daily for a year; HbA1c fell.",
         "journal": "J", "year": "2021", "publication_types": ["Randomized Controlled Trial"],
         "authors": ["x"], "data_banks": None, "url": "u"}]
_H11 = lambda x, claim="": breakdown.held_to_sources(x, _n11, {1, 2}, claim=claim)
t("fix 11: space-grouped thousands in a record match the comma form",
  _H11("The review pooled 1,279,804 participants (Study 1).") != ""
  and _H11("There were 36,352 cases (Study 1).") != "")
t("fix 11: a rounded count the record supports is kept (1.3 million, 36,000)",
  _H11("The review pooled about 1.3 million people (Study 1).") != ""
  and _H11("About 36,000 cases were seen (Study 1).") != "")
t("fix 11: a trial's total from its group sizes is kept (25 + 29 = 54)",
  _H11("A trial of 54 adults tested the app (Study 1).") != "")
t("fix 11: 'type 2' is a name, not a figure the record must give",
  _H11("Adults with type 2 diabetes walked daily (Study 2).") != "")
t("fix 11: a number the claim itself gives is not held against the record",
  _H11("Drinking 8 glasses a day was not tested (Study 2).", "You need to drink 8 glasses of water a day") != ""
  and _H11("Walking 8 hours a day lowered HbA1c (Study 2).", "You need to drink 8 glasses of water a day") != "")
t("fix 11: a figure in no record still goes, and so does a wrong rounding",
  _H11("Risk fell 55% (Study 1).") == "" and _H11("The review pooled 2.5 million people (Study 1).") == ""
  and _H11("A trial of 60 adults tested the app (Study 1).") == "")
t("fix 11: the verbatim dropped sentence from the review now stays",
  breakdown.held_to_sources("Study 2 found a 13-week mobile intervention reduced HbA1c and blood pressure in 54 adults with type 2 diabetes.",
      [_n11[1], {**_n11[0], "abstract": "A randomized controlled trial compared usual care (n = 25) with a 13-week mHealth intervention (n = 29). HbA1c and blood pressure fell in adults with type 2 diabetes."}],
      {2}) != "")

# ---- Review extra: a cached "true" follows the same certainty cap as a fresh check
_xc = "Ginger stops vomiting for good in adults"
db.put_cached_verdict(_xc, _GIN_Q, "true", "Ginger reduces vomiting (Study 1).",
                      ["L1"], [_LOWREV], tldr="Ginger reduces vomiting.", still_open="Doses vary.")
_xa = P(_xc, "9.9.9.8").get_json() or {}
_xh = c.get("/", query_string={"q": _xc}).get_data(as_text=True)
_xseen = {}
_xcard = appmod.og.render_card
def _xspy(claim, verdict_value, *a, **k):
    _xseen["stamp"] = verdict_value
    return _xcard(claim, verdict_value, *a, **k)
appmod.og.render_card = _xspy
try:
    c.get(f"/og/{db.normalize_claim(_xc)}.png")
finally:
    appmod.og.render_card = _xcard
t("extra: a cached 'true' over a low-rated lead review is shown as complicated, with the low line",
  _xa.get("verdict") == "complicated" and (_xa.get("evidence_line") or "").startswith("Low certainty")
  and "certainty of that evidence is low" in (_xa.get("tldr") or ""), (_xa.get("verdict"), _xa.get("evidence_line"), _xa.get("tldr")))
t("extra: the shared page, its markup and its card never say Likely true over that line",
  not _re7.search(_re7.escape(_xc) + r"</span>\s*<span class=\"entry-verdict\">" + appmod.VERDICT_LABELS["true"], _xh)
  and _re7.search(_re7.escape(_xc) + r"</span>\s*<span class=\"entry-verdict\">", _xh)
  and f"<title>{appmod.VERDICT_LABELS['true']}" not in _xh
  and f"{appmod.VERDICT_LABELS['true']}: \u201c{_xc}" not in _xh
  and "<title>" + str(__import__('markupsafe').escape(appmod.display.result_meta(_xc, appmod.VERDICT_LABELS['complicated'], "")["title"])) in _xh
  and f'"alternateName": "{appmod.VERDICT_LABELS["true"]}"' not in _xh and _xseen.get("stamp") == "complicated",
  [_xh[max(0, m.start() - 120):m.end() + 40] for m in _re7.finditer(appmod.VERDICT_LABELS["true"], _xh)][:4])

_xs = c.get("/api/suggest", query_string={"q": "Ginger stops vomiting"}).get_json() or {}
t("extra: type-ahead shows the capped stamp too, not the stored 'true'",
  any(r["claim_text"] == _xc and r["verdict"] == "complicated" for r in _xs.get("suggestions", [])), _xs)

# ---- Review extra: an emptied explanation does not repeat the short answer --------
_ec = "Ginger cures every kind of vomiting in adults"
db.put_cached_verdict(_ec, _GIN_Q, "complicated", "Ginger helped 99% of people (Study 1).",
                      ["L3"], [_MODREV], tldr="Ginger may reduce vomiting a little.", still_open="Doses vary.")
_ea = P(_ec, "9.9.9.7").get_json() or {}
_eh = c.get("/", query_string={"q": _ec}).get_data(as_text=True)
_esays = _eh.split('id="says-sec"')[1].split("</section>")[0]
t("extra: an explanation the gate empties is empty, not the short answer again",
  _ea.get("explanation") == "" and _ea.get("tldr") == "Ginger may reduce vomiting a little.", _ea.get("explanation"))
t("extra: the server-rendered page then hides the research block instead of printing the short answer twice",
  _esays.lstrip().startswith("hidden") and "Ginger may reduce vomiting a little." not in _esays, _esays[:120])

# ---- Review extra: /corrections lists everything a report stores ----------------
_cor = c.get("/corrections").get_data(as_text=True)
_fbcols = {r[1] for r in _sqlite_b.connect(os.environ["DB_PATH"]).execute("pragma table_info(feedback)")}
t("extra: /corrections says the time of a report is stored, as the feedback table does",
  "created_at" in _fbcols and "the date and time of the report" in _cor
  and _fbcols <= {"id", "claim_key", "verdict", "helpful", "note", "created_at"}, _fbcols)

# ---- Second review fix 1: the stamp cap is verdict.py's strict cap, not the line --
_VAX_Q = "(vaccine OR vaccination) AND (severe measles OR measles complications)"
_vaxrev = _study("V1", "Vaccines for measles, mumps, rubella, and varicella in children", types=["Systematic Review", "Meta-Analysis"],
                 abstract="Certainty of evidence was rated using GRADE. We included 138 studies (23,480,668 participants). "
                          "One dose prevented measles (moderate certainty evidence) and hospital admission for measles "
                          "complications fell (low certainty evidence).")
_hpvrev = _study("V2", "HPV vaccines: a network meta-analysis", types=["Systematic Review", "Network Meta-Analysis"],
                 abstract="We assessed the certainty of evidence using the GRADE approach. We included 60 studies. "
                          "Serious adverse events were similar (high-certainty evidence); CIN2+ fell (moderate-certainty); "
                          "rare outcomes were uncertain (low-certainty evidence).")
_r1 = []
for _cl1, _q1, _rv1, _tl1 in [
        ("Vaccines reduce the risk of severe measles", _VAX_Q, _vaxrev, "Vaccines help reduce the risk of severe measles in children."),
        ("The HPV vaccine is safe", "(HPV vaccine) AND (safety OR toxicity)", _hpvrev,
         "The HPV vaccine is safe and provides strong protection against HPV related diseases.")]:
    _p1 = appmod._check_response(_cl1, _q1, "true", _tl1, "It works (Study 1).", [_rv1["pmid"]], [_rv1], "http://x",
                                 cached_at="2026-11-01T00:00:00+00:00")
    _r1.append((_cl1, _tl1, _p1, appmod._listed({"claim_text": _cl1, "verdict": "true", "search_query": _q1,
                                                 "studies": [_rv1], "cited": [_rv1["pmid"]]})))
t("second fix 1: vaccine-shaped rows rated over a range stay Likely true with the short answer unchanged",
  all(p["verdict"] == "true" and p["tldr"] == tl and lst["verdict"] == "true" for _cl, tl, p, lst in _r1),
  [(p["verdict"], p["evidence_line"], p["tldr"]) for _c, _t, p, _l in _r1])
t("second fix 1: their lines still read as ranges that start low, which is not a low rating",
  all(evidence.line_certainty(p["evidence_line"]) == "low" and " to " in p["evidence_line"] for _c, _t, p, _l in _r1),
  [p["evidence_line"] for _c, _t, p, _l in _r1])
_tur1 = appmod._check_response("Turmeric eases joint pain", "(turmeric) AND (joint pain)", "true", "Turmeric eases joint pain.",
                               "It helps (Study 1).", ["L1"], [_LOWREV], "http://x", cached_at="2026-11-01T00:00:00+00:00")
# H3: a saved takeaway is read as guidance only on a claim guidance is read
# for (verdict.guideline_topic, the gate follow_guidelines opens with).
_h3 = {}
for _h3_c in ("Turmeric eases joint pain", "Antibiotics cure joint infections"):
    for _h3_t in (verdict.GUIDELINE_LEAD + "turmeric eases joint pain.", "Turmeric eases joint pain."):
        _h3[_h3_c, _h3_t[:5]] = appmod._check_response(
            _h3_c, "(turmeric) AND (joint pain)", "true", _h3_t, "It helps (Study 1).", ["L1"], [_LOWREV], "http://x",
            cached_at="2026-11-01T00:00:00+00:00")
_h3_f, _h3_b = _h3["Turmeric eases joint pain", "Major"], _h3["Turmeric eases joint pain", "Turme"]
t("H3: a row with the lead on a claim no guideline covers shows no guidance line and is capped like the same answer without it",
  not verdict.guideline_topic("Turmeric eases joint pain") and _h3_f["evidence_line"] != verdict.GUIDANCE_LINE
  and (_h3_f["verdict"], _h3_f["evidence_line"]) == (_h3_b["verdict"], _h3_b["evidence_line"]) == ("complicated", _h3_b["evidence_line"])
  and not verdict.from_guidance(verdict.GUIDELINE_LEAD + "x.", "Turmeric eases joint pain"),
  (_h3_f["verdict"], _h3_f["evidence_line"], _h3_b["verdict"], _h3_b["evidence_line"]))
t("  and on a guideline topic the same lead still reads as guidance: the line, and no cap",
  verdict.guideline_topic("Antibiotics cure joint infections")
  and _h3["Antibiotics cure joint infections", "Major"]["evidence_line"] == verdict.GUIDANCE_LINE
  and _h3["Antibiotics cure joint infections", "Major"]["verdict"] == "true",
  _h3["Antibiotics cure joint infections", "Major"]["evidence_line"])
_exd1 = _gin_review("E1", "Network meta-analysis of 200 trials. Walking reduced depression. Confidence in accordance with CINeMA was low.")
_exp1 = appmod._check_response("Exercise reduces depression", "(exercise) AND (depression)", "true", "Exercise reduces depression.",
                               "It helps (Study 1).", ["E1"], [_exd1], "http://x", cached_at="2026-11-01T00:00:00+00:00")
t("second fix 1: a lead review rated low (turmeric shape) or CINeMA low (exercise shape) is still capped",
  _tur1["verdict"] == "complicated" and _exp1["verdict"] == "complicated"
  and "certainty of that evidence is low" in _tur1["tldr"], (_tur1["verdict"], _exp1["verdict"], _exp1["evidence_line"]))

# ---- Second review fix 2: a stamp set by health guidance is never capped ----------
_g2c = "Measles vaccine protects children from measles"
_g2t = verdict.GUIDELINE_LEAD + "The measles vaccine protects children from measles."
_g2p = appmod._check_response(_g2c, "(measles vaccine) AND (measles)", "true", _g2t, "It helps (Study 1).",
                              ["L1"], [_LOWREV], "http://x", cached_at="2026-11-01T00:00:00+00:00")
_g2l = appmod._listed({"claim_text": _g2c, "verdict": "true", "search_query": "(measles vaccine) AND (measles)",
                       "tldr": _g2t, "studies": [_LOWREV], "cited": ["L1"]})
db.put_cached_verdict(_g2c, "(measles vaccine) AND (measles)", "true", "It helps (Study 1).", ["L1"], [_LOWREV],
                      tldr=_g2t, still_open="Doses vary.")
_g2s = c.get("/api/suggest", query_string={"q": "Measles vaccine protects"}).get_json() or {}
t("second fix 2: a guidance 'true' over a low-rated review keeps its stamp and its guidance takeaway",
  _g2p["verdict"] == "true" and _g2p["tldr"] == _g2t and _g2l["verdict"] == "true"
  and any(r["claim_text"] == _g2c and r["verdict"] == "true" for r in _g2s.get("suggestions", [])),
  (_g2p["verdict"], _g2p["tldr"], _g2l["verdict"]))
t("second fix 2: the same rows without the guidance lead are still capped",
  appmod._check_response(_g2c, "(measles vaccine) AND (measles)", "true", "It protects.", "It helps (Study 1).",
                         ["L1"], [_LOWREV], "http://x", cached_at="2026-11-01T00:00:00+00:00")["verdict"] == "complicated")
t("second fix 2: /method says the verdict follows the guidance, and the code lets it",
  "the verdict follows it" in c.get("/method").get_data(as_text=True))

# ---- Second review fix 3: harm words are whole words in a warning, and warnings are still checked
_p3st = [_study("A", "Vinegar in adults", abstract="We randomised 1,937 adults aged 30 to 50 years to 15 mL vinegar or placebo "
                "for twelve weeks. Weight fell by 20% in 2019."),
         _study("C", "Oesophageal injury from vinegar", types=["Case Reports"],
                abstract="A 54-year-old woman had oesophageal injury after drinking undiluted vinegar.")]
_k3 = lambda x, who="": breakdown.held_to_sources(x, _p3st, {1}, who=who)
for _b3 in ["Pregnant women lost 12 kg on pharmacological doses (Study 1).",
            "Children with infections lost 99 kg (Study 1).",
            "It avoids weight gain in 40,000 children (Study 1)."]:
    t(f"second fix 3: not a warning, so held to its source and dropped: {_b3[:40]}",
      _k3(_b3) == "", _k3(_b3))
t("second fix 3: substrings are not harm words, and a hazard word needs a warning frame",
  not breakdown.is_warning("Pharmacological doses were used.") and not breakdown.is_warning("It is cytotoxic in cells.")
  and not breakdown.is_warning("Children with infections lost weight.")
  and breakdown.is_warning("Raw milk is linked to outbreaks.") and breakdown.is_warning("St John's wort can interact with warfarin.")
  and breakdown.is_warning("Bacterial infections such as sinusitis.", who="not"))
for _w3 in ["It is unsafe in pregnancy (Study 1).", "People with diabetes should not stop their medicine (Study 1)."]:
    t(f"second fix 3 (Q8: now removed): an unsupported warning goes: {_w3[:44]}", _k3(_w3) == "", _k3(_w3))
for _w3 in ["Undiluted vinegar can injure the throat in children (Study 2).",
            "Undiluted vinegar can injure the oesophagus (Study 2).",
            "Neither tested whether diabetes went away, and Study 2 is a reminder that undiluted vinegar can injure the oesophagus."]:
    _o3 = _k3(_w3)
    t(f"second fix 3 (Q8): cited to a study not relied on, so removed: {_w3[:44]}", _o3 == "", _o3)
t("second fix 3 (Q8): a warning its relied-on study supports is untouched",
  _k3("A woman had oesophageal injury after drinking undiluted vinegar (Study 1).")
  == "A woman had oesophageal injury after drinking undiluted vinegar (Study 1).",
  _k3("A woman had oesophageal injury after drinking undiluted vinegar (Study 1)."))
t("second fix 3: a warning with a figure its study lacks is removed (Q8)",
  _k3("Vinegar burned the throat of 300 adults (Study 1).") == "")
_ORIG17 = [
    ("Heavy drinkers, people with contraindications to alcohol, and those outside the studied age ranges (Study 1).", "not"),
    ("Raw milk consumption is linked to outbreaks of campylobacteriosis and brucellosis (Study 6, Study 8).", ""),
    ("Antibiotics should not be used for the common cold or influenza (Study 3).", ""),
    ("Evidence states that antibiotics should not be used for the common cold (Study 3).", ""),
    ("Bacterial infections such as acute bacterial sinusitis or group A streptococcal pharyngitis (Studies 2, 3).", "not"),
    ("Viruses cause most nose and throat infections and antibiotics should not be used for them (Study 3).", ""),
    ("Alcohol is a well-established teratogen that can cause variable physical and behavioral effects on the fetus (Study 7).", ""),
    ("Indirect evidence shows various functional and structural abnormalities in children with prenatal alcohol exposure (Study 3).", ""),
    ("Patients with other viral infections or those taking ivermectin strictly for parasitic conditions (Study 8).", "not"),
    ("People with chronic illnesses or those seeking a guaranteed way to avoid infections (Study 1).", "not"),
    ("Men, people without desk jobs, and people with severe spinal injuries (Studies 1, 2, 7).", "not"),
    ("Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.", ""),
]
_bare3 = lambda x: _re7.sub(r"\s*\((?:Study|Studies)[^)]*\)", "", x).replace("Study 6, Study 8", "").rstrip(". ")
_lost3 = [x for x, w in _ORIG17 if _bare3(breakdown.held_to_sources(x, _h8, {1, 2}, who=w)) != _bare3(x)]
t("second fix 3 (Q8): every warning from the review that no study supports is now removed",
  all(_bare3(breakdown.held_to_sources(x, _h8, {1, 2}, who=w)) == "" for x, w in _ORIG17
      if not x.startswith(("Heavy drinkers", "People with chronic", "Men, people", "Bacterial infections"))
      and "Patients with other" not in x), _lost3)

# ---- Second review, optional: free-tier wording only on Gemini; /about says what is sent
_og_prov = verdict.provider
try:
    verdict.provider = lambda: "anthropic"
    _hA, _pA = c.get("/").get_data(as_text=True), c.get("/privacy").get_data(as_text=True)
    verdict.provider = lambda: "gemini"
    _hG, _pG = c.get("/").get_data(as_text=True), c.get("/privacy").get_data(as_text=True)
finally:
    verdict.provider = _og_prov
t("optional: the free tier and Google's terms are mentioned only when checks go to Gemini",
  "free tier" not in _hA and "Google" not in _pA.split("Don't paste")[0][-200:]
  and "free tier" in _hG and "Google's free tier" in _pG and "usage limit" in _hA)

t("optional: /about says the claim and the studies' published details are sent",
  "the published details of the studies, such as their titles and abstracts" in c.get("/about").get_data(as_text=True))

# ---- Third review: reassurances and benefits are not warnings; inline naming comes out
_P4 = ["Study 1 found vinegar may avoid infections in 5000 pregnant women.",
       "In Study 1, the risk of infections fell by 75% in 9,000 children.",
       "Studies 1 and 2 show it could cut outbreaks by 90% in children.",
       "Study 1 shows it reduces side effects by 90% in 5000 children.",
       "Study 1 found no harm in 9,000 pregnant women.",
       "There were no adverse events in 12,000 children in Study 1."]
for _p4 in _P4:
    _o4 = breakdown.held_to_sources(_p4, _p3st, {1, 2})
    t(f"third review: a reassurance or benefit with figures its study lacks is dropped: {_p4[:44]}", _o4 == "", _o4)
t("third review: negated or reduced harm is not a warning; advice to avoid and plain hazards are",
  not breakdown.is_warning("There were no serious adverse events.") and not breakdown.is_warning("It was not harmful.")
  and not breakdown.is_warning("It reduces side effects.") and not breakdown.is_warning("The risk of infections fell.")
  and not breakdown.is_warning("It avoids weight gain.")
  and breakdown.is_warning("Pregnant women should avoid raw milk.") and breakdown.is_warning("Antibiotics should not be used.")
  and not breakdown.is_warning("It is not safe in pregnancy.") and breakdown.is_warning("It can cause side effects such as nausea.")
  and breakdown.is_warning("It caused side effects such as nausea."))
_w4 = {"Study 1 found vinegar can burn the throat in 5000 children.": "Vinegar can burn the throat in 5000 children.",
       "In Study 2, undiluted vinegar injured the oesophagus in 300 children.": "Undiluted vinegar injured the oesophagus in 300 children.",
       "Studies 1 and 2 warn that vinegar can injure the throat of 9,000 pregnant women.":
           "Vinegar can injure the throat of 9,000 pregnant women.",
       "Raw milk can carry harmful bacteria and caused 40 outbreaks in Study 1.":
           "Raw milk can carry harmful bacteria and caused 40 outbreaks."}
for _wi, _wo in _w4.items():
    _go = breakdown.held_to_sources(_wi, _p3st, {1, 2})
    t(f"third review (Q8): an inline warning its study does not carry is removed: {_wi[:40]}", _go == "", _go)
t("third review: a supported inline warning is left exactly as written",
  breakdown.held_to_sources("Study 2 is a reminder that undiluted vinegar can injure the oesophagus.", _p3st, {1, 2})
  == "Study 2 is a reminder that undiluted vinegar can injure the oesophagus.")

_ogp = verdict.provider
try:
    def _nokey():
        raise RuntimeError("no key")
    verdict.provider = _nokey
    _hN = c.get("/").get_data(as_text=True)
    verdict.provider = lambda: "gemini"
    _hG2 = c.get("/").get_data(as_text=True)
finally:
    verdict.provider = _ogp
t("third review: with no key the home page does not read 'An AI model (an AI model API)'",
  "An AI model (an AI model API)" not in _hN and "An AI model reads those abstracts" in _hN
  and "An AI model (Google&#39;s Gemini API) reads" in _hG2)

# ---- Fourth review: a failing sentence stays only if it is positively a caution
_p4st = _p3st + [_study("L", "Supplement and the liver",
                        abstract="In 200 adults taking the supplement, 12 developed liver injury and bleeding rose.")]
_k4 = lambda x: breakdown.held_to_sources(x, _p4st, {1, 2, 3})
# Fifth review: these ten were kept by the old rule; each is a double negative or softened
# ("can", "not free of", "no safe dose"), so the fail-safe drops them. The accepted trade.
for _c4 in ["It does not prevent bleeding and can cause it in pregnant women (Study 3).",
            "It did not reduce the risk of bleeding in older adults, which rose (Study 3).",
            "It is not free of side effects in children (Study 3).",
            "Stopping or reducing insulin can be fatal in children (Study 1).",
            "Cutting insulin can cause dangerous high blood sugar in children (Study 1).",
            "There is no safe dose for 700 pregnant women (Study 1).",
            "Stopping insulin is never safe for 700 children (Study 1).",
            "Not harmless: it can cause liver injury in older adults (Study 3).",
            "It should not be used to prevent infections in children (Study 1).",
            "No evidence it is safe in pregnancy (Study 1)."]:
    _o5 = _k4(_c4)
    t(f"fourth review (fifth review: accepted trade, now dropped): {_c4[:46]}", _o5 == "" and not breakdown.is_warning(_c4), _o5)
for _d4 in ["It was harmless in 9,000 pregnant women.", "Side effects were rare in 6,000 children.",
            "Serious harm was uncommon among 9,000 pregnant women.", "Deaths were no higher among 9,000 pregnant women.",
            "Adverse events were similar to placebo in 9,000 children.",
            "The risk of bleeding was no higher in 4,000 pregnant women."]:
    _o6 = _k4(_d4[:-1] + " (Study 1).")
    t(f"fourth review: a reassurance its study does not carry is dropped: {_d4[:46]}",
      _o6 == "" and not breakdown.is_warning(_d4), _o6)
_o7 = _k4("Study 1 found it harmless in 9,000 pregnant women.")
t("fourth review: an inline reassurance its study does not carry is dropped, never 'It harmless in'",
  _o7 == "", _o7)
_lost4 = [p for p in _P4 if _k4(p) != ""]
t("fourth review: the six probes of the third review are still dropped", not _lost4, _lost4)
t("fourth review (fifth review: a reassurance anywhere in the sentence drops it, even beside a caution)",
  _k4("It was safe in adults but should not be used in 9,000 pregnant women (Study 1).") == "")
t("fourth review: a negated caution word is not a caution",
  not any(breakdown.is_warning(x) for x in [
      "It is not dangerous.", "It is non-toxic.", "There is no evidence it can cause harm.",
      "It did not cause bleeding.", "Deaths fell.", "It lowered the risk of bleeding.",
      "It is linked to fewer infections.", "It cannot cause harm."]))
t("fourth review: a risk named with nothing to cancel it is a caution",
  all(breakdown.is_warning(x) for x in [
      "Bleeding rose.", "It is linked to outbreaks.", "It raised the risk of bleeding.",
      "It caused bleeding in 300 children.", "Tobacco smoking causes 30 to 40 percent of deaths from cancer."]))
t("fourth review: taking out the naming never leaves a clause without a verb",
  breakdown.unattributed("Study 1 found it harmless in 9,000 pregnant women.") == ""
  and breakdown.unattributed("Study 1 found it harmless in 9,000 pregnant women.", passes=lambda cl: True)
  == "A study found it harmless in 9,000 pregnant women."
  and breakdown.unattributed("Study 1 found it harmless in 9,000 pregnant women, but it can cause liver injury.")
  == "It can cause liver injury."
  and breakdown.unattributed("Study 1 found vinegar can burn the throat in 5000 children.")
  == "Vinegar can burn the throat in 5000 children.")
t("fourth review (Q8): /method says an unsupported sentence is removed, even a warning",
  "even a warning" in c.get("/method").get_data(as_text=True))

# ---- Fifth review: a failing sentence stays only if it names a harm (or forbids a use) and nothing softens it
# Every sentence below cites Study 1 and fails it (a group or figure the record lacks), so it is
# dropped unless the fail-safe keeps it, uncited.
_K5 = lambda x: breakdown.held_to_sources(x, _p4st, {1, 2, 3})
_KEEP5 = ["Deaths occurred in 3 children (Study 1).",
          "Liver injury was reported in children (Study 1).",
          "Side effects are common in children (Study 1).",
          "High doses harmed the liver in children (Study 1).",
          "Seizures followed the injection in 40 infants (Study 1).",
          "Poisoning was reported in 700 toddlers (Study 1).",
          "Women in 17 trials had miscarriages (Study 1).",
          "Overdose killed 15 teenagers (Study 1).",
          "Anaphylaxis occurred after the first dose in 20 adults over 80 (Study 1).",
          "It is unsafe in pregnancy (Study 1).",
          "Pregnant women must not take it with warfarin (Study 1).",
          "Children must not take it (Study 1).",
          "Pregnant women should avoid raw milk (Study 1).",
          "Never combine it with alcohol in 40 people (Study 1).",
          "It must be avoided in 9,000 pregnant women (Study 1).",
          "Children should stop taking it if they bleed (Study 1).",
          "Raw milk was linked to outbreaks in 40 farms (Study 1).",
          "It is contraindicated in infants (Study 1).",
          "Hospitalisation was needed for 90 children (Study 1)."]
for _x5 in _KEEP5:
    _o = _K5(_x5)
    t(f"fifth review (Q8: no longer kept): an unsupported warning is removed: {_x5[:50]}",
      _o == "" and breakdown.is_warning(_x5), _o)
_DROP5 = ["It caused no birth defects in 9,000 pregnant women (Study 1).",
          "It may cause fewer side effects in 9,000 children than placebo (Study 1).",
          "Bleeding was not increased in 9,000 pregnant women (Study 1).",
          "The risk of side effects is low in 9,000 children (Study 1).",
          "Claims it can harm 9,000 children were not supported (Study 1).",
          "It should not worry 9,000 pregnant women (Study 1).",
          "Deaths fell by half in 9,000 children (Study 1).",
          "It could cause bleeding in 40 children (Study 1).",
          "Some children had seizures (Study 1).",
          "Deaths were rare among 800 children (Study 1).",
          "Nobody died in 600 toddlers (Study 1).",
          "It was well tolerated, with no overdose in 90 teenagers (Study 1).",
          "Liver damage was mild in 20 children (Study 1).",
          "It is safe in pregnancy and caused no harm (Study 1).",
          "Women can take it without concern in 500 pregnancies (Study 1).",
          "There was no increase in miscarriage among 3,000 women (Study 1).",
          "Harm was unlikely in 9,000 children (Study 1).",
          "Do not take it, as it never caused harm in 90 children (Study 1).",
          "Adverse events were comparable to placebo in 700 infants (Study 1).",
          "It avoids bleeding in 9,000 pregnant women (Study 1).",
          "Overdoses were reversible in 30 children (Study 1).",
          "Poisoning is only a small risk in 400 children (Study 1)."]
for _x5 in _DROP5:
    _o = _K5(_x5)
    t(f"fifth review: dropped, a reassurance or softened or negated: {_x5[:50]}",
      _o == "" and not breakdown.is_warning(_x5), _o)
# Sixth round: "can" states a harm, benefit verbs soften, and the harm list covers common adverse effects.
for _x6 in ["It can cause liver injury in 9,000 children (Study 1).",
            "Undiluted vinegar can injure the throat in children (Study 2).",
            "It can burn the throat of 9,000 children (Study 1).",
            "Nausea and vomiting occurred in 40 children (Study 1).",
            "Diarrhoea was common in 90 infants (Study 1).",
            "Dizziness and headaches affected 77 adults (Study 1).",
            "A rash and itching appeared in 88 toddlers (Study 1).",
            "Hepatitis and jaundice occurred in 66 older adults on it (Study 1).",
            "Arrhythmia occurred in 60 children (Study 1).",
            "Kidney stones formed in 25 men aged 80 (Study 1).",
            "Addiction and cramps were seen in 500 teenagers (Study 1).",
            "The overdose killed two children (Study 1)."]:
    t(f"sixth round (Q8: no longer kept): an unsupported warning is removed: {_x6[:50]}", _K5(_x6) == "", _K5(_x6))
for _x6 in ["It can stop bleeding in 9,000 children (Study 1).",
            "It can help prevent heart attacks in 9,000 people (Study 1).",
            "It can be taken without harm by 9,000 children (Study 1).",
            "It can cure cancer in 9,000 people (Study 1).",
            "It can treat infections in 9,000 children (Study 1).",
            "It can relieve pain in 400 adults (Study 1).",
            "It can shrink tumours in 9,000 patients (Study 1).",
            "It can control hypertension in 9,000 adults (Study 1).",
            "It may cause nausea in 40 children (Study 1).",
            "It boosts the body against infection in 9,000 children (Study 1).",
            "It kills cancer cells in 9,000 patients (Study 1).",
            "It destroys bacteria in 4,000 children (Study 1).",
            "It attacks harmful germs in 4,000 children (Study 1).",
            "It clears infections in 4,000 children (Study 1).",
            "Children with infections lost 99 kg (Study 1)."]:
    t(f"sixth round: dropped, a benefit or hedge: {_x6[:50]}", _K5(_x6) == "", _K5(_x6))
t("fifth review: a prohibition is kept only if nothing else in it softens or reassures",
  breakdown.is_warning("Do not take it with warfarin.") and not breakdown.is_warning("It should not worry anyone.")
  and not breakdown.is_warning("You can take it without concern.")
  and not breakdown.is_warning("Do not worry, it is fine."))
t("fifth review: a sentence that passes its sources is untouched, even if softened",
  _K5("Vinegar may cause no harm in adults (Study 1).") == "Vinegar may cause no harm in adults (Study 1).")
t("fifth review: the 'does not apply to' line keeps a harm word but not a softened one",
  breakdown.is_warning("People who may have bleeding disorders.", who="not") is False
  and breakdown.is_warning("People with bleeding disorders.", who="not"))
t("fifth review (Q8): /method says a failing sentence is removed, even a warning, bar an untested statement",
  "even a warning of harm" in c.get("/method").get_data(as_text=True)
  and "nothing softens it" not in c.get("/method").get_data(as_text=True))

# Reassurances by absence, denial or dismissal: all dropped.
for _x7 in ["Kidney harm was absent in all 200 participants.", "Birth defects were ruled out.",
            "Seizures were absent.", "Deaths were absent from the trial.", "Poisoning is a myth.",
            "Fears of liver damage are exaggerated.", "Harm was excluded in the trial of 800 adults.",
            "There were no signs of toxicity.", "The danger of overdose is overblown.",
            "Reports of miscarriage were debunked.", "Claims of heart attacks were refuted.",
            "Bleeding is a misconception.", "Stroke risk was overstated.", "The harm is unproven.",
            "Side effects were undetected in 900 children.", "Allergic reactions were unobserved.",
            "Liver injury was nonexistent.", "Kidney function was unaffected, with no injury.",
            "Blood counts were normal and there was no bleeding.", "It is a false alarm that it causes seizures.",
            "The drug is clear of poison.", "Hospitalisation can be ruled out."]:
    t(f"reassurance by absence or denial is dropped: {_x7[:50]}", not breakdown.is_warning(_x7), _x7)

# Minimisers and rarity figures: all dropped.
for _x8 in ["Miscarriages were scarcely reported.", "Deaths were practically nonexistent in the trial.",
            "Seizures occurred in under 2% of children.", "Strokes were virtually nil.",
            "Liver damage was trivial.", "Bleeding episodes were isolated.", "Overdoses were sporadic.",
            "Allergic reactions were essentially unknown in 500 adults.", "Heart attacks were insignificant.",
            "Only a handful of seizures occurred.", "Poisoning was merely theoretical.",
            "Kidney injury occurred in 3 in 10,000 users.", "Fewer than 5 deaths occurred in 9,000 people.",
            "Birth defects were seen in at most 1 in 1,000 pregnancies.", "Adverse events were limited.",
            "Serious harm was modest.", "Fatalities were unheard of.", "Side effects were just nausea in a few people.",
            "Hospitalisation occurred in below 1% of patients.", "Toxic effects were minimal in 700 adults.",
            "Burns happened in up to 4 children."]:
    t(f"minimiser or rarity figure is dropped: {_x8[:50]}", not breakdown.is_warning(_x8), _x8)

# Blind sentences (round 8): 60 written by someone who never saw the rule, 30 warnings and
# 30 reassurances. The word-list rule kept 1 reassurance, so it no longer decides what stays:
# an unsupported sentence is removed, a warning included. Held to sources with no support,
# none of the 60 may stay, and in particular no reassurance.
_blind8 = json.loads((pathlib.Path(__file__).parent / "warning_blind_q8.json").read_text())
_b8_st = [{"pmid": "1", "title": "Background", "abstract": "A narrative review.", "journal": "J", "year": "2020",
           "publication_types": ["Review"], "authors": ["x"], "data_banks": None, "url": "u"}]
_b8_kept = [x for x in _blind8 if breakdown.held_to_sources(x["text"].rstrip(".") + " (Study 1).", _b8_st, {2}) != ""]
_b8_kept_r = [x["text"] for x in _b8_kept if x["kind"] == "reassurance"]
t("blind q8: 0 of 30 reassurances kept", not _b8_kept_r and sum(x["kind"] == "reassurance" for x in _blind8) == 30, _b8_kept_r)
t("blind q8: 0 of 60 unsupported sentences kept, warnings included", not _b8_kept, [x["text"] for x in _b8_kept])
t("blind q8: never_dropped keeps none of the 60", not any(breakdown.never_dropped(x["text"]) for x in _blind8))
print(f"blind sentences (q8): {len(_b8_kept)} of {len(_blind8)} kept, {len(_b8_kept_r)} reassurances kept")

# ---- Polish: each result page has its own title, description and card tags ---------
import display as _dsp  # noqa: E402
_mq = 'Taking 5\u201310 mg of "melatonin" & magnesium nightly for years cures chronic insomnia in every adult over fifty'
db.put_cached_verdict(_mq, _GIN_Q, "complicated", "Ginger reduces vomiting (Study 1).",
                      ["L1"], [_LOWREV], tldr="Studies point both ways on melatonin. Small trials found a short gain in sleep, and none ran longer than a few weeks, so what happens over years is unknown.", still_open="Doses vary.")
_mh = c.get("/", query_string={"q": _mq}).get_data(as_text=True)
_mt = _re.search(r"<title>(.*?)</title>", _mh, _re.S).group(1)
_md = _re.search(r'<meta name="description" content="(.*?)" />', _mh, _re.S).group(1)
import html as _html  # noqa: E402
_mt_raw, _md_raw = _html.unescape(_mt), _html.unescape(_md)
t("result meta: title is about 60 characters, starts with the verdict's label, and ends with the site name",
  len(_mt_raw) <= 62 and _mt_raw.startswith("It's complicated: \u201c") and _mt_raw.endswith("\u201d \u00b7 Evident") and "\u2026" in _mt_raw, _mt_raw)
t("result meta: description is the label and the short answer, 155 characters or fewer",
  len(_md_raw) <= 155 and _md_raw.startswith("It's complicated. Studies point both ways") and "years is unknown" not in _md_raw, _md_raw)
t("result meta: no em or en dash, and the page escapes what the claim carried",
  not _DASH.search(_mt_raw + _md_raw) and "&amp;" in _mh and "<title>" in _mh and '"melatonin"' not in _mt, _mt)
t("result meta: og and twitter tags carry the same title, description and card",
  f'property="og:title" content="{_mt.rsplit(" \u00b7 Evident", 1)[0]}"' in _mh
  and f'name="twitter:title" content="{_mt.rsplit(" \u00b7 Evident", 1)[0]}"' in _mh
  and f'property="og:description" content="{_md}"' in _mh and f'name="twitter:description" content="{_md}"' in _mh
  and 'name="twitter:image" content="http://localhost/og/' in _mh and 'property="og:image" content="http://localhost/og/' in _mh)
t("result meta: the home page keeps its own title, and no two saved results share one",
  "Evident: check a health claim" in c.get("/").get_data(as_text=True)
  and _mt != _re.search(r"<title>(.*?)</title>", c.get("/", query_string={"q": _xc}).get_data(as_text=True), _re.S).group(1))
_short = _dsp.result_meta("Garlic cures colds", "Likely false", "No.")
t("result meta: a short claim and answer are not padded or cut",
  _short == {"title": "Likely false: \u201cGarlic cures colds\u201d", "description": "Likely false. No."}, _short)
t("result meta: it never says more than the label: the label leads both lines",
  all(_dsp.result_meta("A claim " * 20, lb, "An answer. " * 30)[k].startswith(lb) for lb in appmod.VERDICT_LABELS.values() for k in ("title", "description")))

# ---- Polish: the share card is 1200x630 whatever it is given, and nothing runs off it ---
from PIL import Image as _CI, ImageDraw as _CD  # noqa: E402
import io as _cio  # noqa: E402
_cd = _CD.Draw(_CI.new("RGB", (10, 10)))
_cf = og._body(34)
_cases = {"long": ("word " * 200).strip(), "nospace": "x" * 500, "url": "see https://example.com/" + "a" * 120 + " for proof",
          "short": "Garlic cures colds"}
_okw, _okl, _ell = True, True, {}
for _n, _txt in _cases.items():
    _ls = og._wrap(_cd, _txt, _cf, 1000, 2)
    _okl &= 1 <= len(_ls) <= 2
    _okw &= all(_cd.textlength(x, font=_cf) <= 1000 for x in _ls)
    _ell[_n] = _ls[-1].endswith("\u2026")
t("card text: wrapped lines fit the width and the line limit, even for one unbroken word", _okw and _okl, _ell)
t("card text: text that does not fit ends in an ellipsis inside the width; text that fits is untouched",
  _ell["long"] and _ell["nospace"] and _ell["url"] and not _ell["short"]
  and og._wrap(_cd, "Garlic cures colds", _cf, 1000, 2) == ["Garlic cures colds"])
_shots = []
for _cl, _vd, _tl, _so in [("A very long claim " * 40, "complicated", "An answer that goes on and on. " * 12, "Something still open. " * 20),
                           ("x" * 500, "insufficient", "y" * 400, "z" * 300),
                           ("Short", "true", "Short answer.", ""), ("Medium length claim about sleep", "false", "", "Doses vary.")]:
    _im = _CI.open(_cio.BytesIO(og.render_card(_cl, _vd, _tl, 5, ("Meta-Analysis", "2020", "T " * 200), _so))).convert("RGB")
    _shots.append(_im)
def _runs_off(im):
    # The strip at the right edge, below the masthead, is only ever the night or the paper.
    return any(im.getpixel((x, y)) not in {(15, 18, 17), (241, 242, 238)}
               for y in range(100, 630, 2) for x in range(1150, 1200, 2))
t("share card: exactly 1200x630 for a very long claim, a run of letters, a long answer and a short one",
  all(i.size == (1200, 630) for i in _shots), [i.size for i in _shots])
t("share card: no text reaches the right edge, and the footer line is clear of the answer",
  not any(_runs_off(i) for i in _shots))
_sc = og.render_card("Sleep helps memory", "true", "It is mixed.", 3, None, "How long the effect lasts.")
_sc_none = og.render_card("Sleep helps memory", "true", "It is mixed.", 3, None, "")
t("share card: a check's still-open line is on the card, and a card without one is a different image", _sc != _sc_none)

_so3 = "The long-term effect beyond twelve weeks is unknown. " * 5
_i3 = _CI.open(_cio.BytesIO(og.render_card("Sleep helps memory", "true", "It is mixed.", 3, None, _so3))).convert("RGB")
_i1 = _CI.open(_cio.BytesIO(og.render_card("Sleep helps memory", "true", "It is mixed.", 3, None, "Short."))).convert("RGB")
_dark = lambda im, y0, y1: sum(1 for y in range(y0, y1) for x in range(100, 1150, 2) if sum(im.getpixel((x, y))) < 300)
_o3 = og._wrap(_cd, _so3, og._body(21), 1000, 3)
t("share card: a long still-open line wraps to up to three lines before it truncates",
  len(_o3) == 3 and og._wrap(_cd, _so3 * 3, og._body(21), 1000, 3)[-1].endswith("\u2026") and not _o3[-1].endswith("\u2026")
  and _i3.size == (1200, 630) and _dark(_i3, 330, 560) > _dark(_i1, 330, 560), _o3)
t("share card: three still-open lines leave the footer row clear",
  not any(_i3.getpixel((x, y)) != (241, 242, 238) for y in range(560, 590) for x in range(64, 1136, 3)))

# The card a link to the site itself unfurls with is a brand card: the
# tagline is never drawn as if it were a claim someone checked.
from PIL import ImageDraw as _OGDraw  # noqa: E402
_og_text = _OGDraw.ImageDraw.text
_og_drawn = []


def _og_spy(self, xy, text, *a, **k):
    _og_drawn.append(str(text))
    return _og_text(self, xy, text, *a, **k)


_OGDraw.ImageDraw.text = _og_spy
try:
    og.render_brand.cache_clear()
    _brand_png = c.get("/og/_.png")
    _brand_words = list(_og_drawn)
    _og_drawn.clear()
    og._render.cache_clear()
    og.render_card("Garlic shortens colds", "complicated", "It is mixed.", 4)
    _claim_words = list(_og_drawn)
finally:
    _OGDraw.ImageDraw.text = _og_text
_bw = " ".join(_brand_words)
t("og brand card: /og/_.png draws the wordmark, the line and the host, as a 1200x630 png",
  _brand_png.status_code == 200 and _brand_png.mimetype == "image/png"
  and _CI.open(_cio.BytesIO(_brand_png.data)).size == (1200, 630)
  and "Evident" in _brand_words and "checkevident.com" in _brand_words
  and "See what the research says about a health claim" in _bw, _brand_words)
t("  and draws no claim label, no verdict and no study count",
  not any(x in _bw for x in ("claim you checked", "Verdict", "No matching studies", "complicated")), _brand_words)
t("  a key with nothing saved gets the same brand card", c.get("/og/no-such-claim-saved.png").data == _brand_png.data)
_cw = " ".join(_claim_words)
t("og per-claim card: still draws its claim, its claim label and its verdict",
  "Garlic shortens colds" in _cw and "The claim you checked" in _cw and "Verdict" in _cw
  and "It's complicated" in _claim_words and "4 PubMed studies read" in _claim_words, _claim_words)
# The brand card's address carries a version, so a link service that cached
# the old card (the tagline drawn as a claim) fetches the new one.
_ogv = {p: _re2.findall(r'property="og:image" content="([^"]*)"', c.get(p).get_data(as_text=True))
        for p in ("/", "/privacy", "/about", "/method")}
t("og brand card: every page that is not a result points og:image at /og/_.png?v=2",
  all(v == ["http://localhost/og/_.png?v=2"] for v in _ogv.values()), _ogv)
t("  and no template still names the unversioned address",
  not any('/og/_.png"' in (ROOT / "templates" / f).read_text() for f in ("index.html", "info.html", "privacy.html")))

# ---- Polish: sitemap, robots, canonical, noindex ---------------------------------------
_gs = {"pmid": "S1", "title": "Garlic and colds", "abstract": "A randomized trial.", "journal": "J", "year": "2020",
       "publication_types": ["Randomized Controlled Trial"], "authors": ["x"], "data_banks": None, "url": "u"}
_good_c, _bad_c = "Garlic shortens a cold in adults", "Onion juice cures a sore throat in adults"
db.put_cached_verdict(_good_c, "garlic AND cold", "complicated", "Garlic may shorten colds (Study 1).",
                      ["S1"], [_gs], tldr="Studies are mixed.", still_open="Doses vary.")
db.put_cached_verdict(_bad_c, "onion AND throat", "complicated", "Onion juice may help (Study 1).",
                      ["GONE"], [_gs], tldr="Studies are mixed.", still_open="Doses vary.")
_sm = c.get("/sitemap.xml").get_data(as_text=True)
_loc_good = "<loc>http://localhost/?q=" + urllib.parse.quote(_good_c) + "</loc>"
_loc_bad = urllib.parse.quote(_bad_c)
_cur_c, _cur_bad = "Vaccines cause autism", "Cracking your knuckles causes arthritis"
db.put_cached_verdict(_cur_c, "vaccines AND autism", "false", "Large studies find no link (Study 1).",
                      ["S1"], [_gs], tldr="No link found.", still_open="Rare events are hard to study.")
db.put_cached_verdict(_cur_bad, "knuckles AND arthritis", "false", "No link (Study 1).",
                      ["GONE"], [_gs], tldr="No link found.", still_open="Few trials.")
_sm = c.get("/sitemap.xml").get_data(as_text=True)
_loc_cur = "<loc>http://localhost/?q=" + urllib.parse.quote(_cur_c) + "</loc>"
t("sitemap: a curated example claim whose saved result is sound is listed, absolute and with its save date",
  _loc_cur in _sm and "<lastmod>" in _sm, _sm[:300])
t("sitemap: a sound, non-personal saved result that is not a curated example is absent", _loc_good not in _sm)
t("sitemap: a curated example whose saved record is unsound (cited study missing) is absent",
  urllib.parse.quote(_cur_bad) not in _sm)
t("sitemap: a saved result that fails the check (a cited study that is not among the studies) is left out", _loc_bad not in _sm)
_sm_locs = _re.findall(r"<loc>([^<]*)</loc>", _sm)
t("sitemap: only the five static pages (/checks is a redirect now) and curated results, nothing else",
  all(u.startswith("http://localhost/?q=") is False or urllib.parse.unquote(u.split("?q=", 1)[1]) in _dsp.CURATED_CLAIMS
      for u in _sm_locs) and len(_sm_locs) == 6
  and [u for u in _sm_locs if "?q=" not in u] == ["http://localhost" + x for x in ("/", "/about", "/method", "/corrections", "/privacy")],
  _sm_locs)
t("sitemap: the five pages the brief names are all there, absolute",
  all(f"<loc>http://localhost{x}</loc>" in _sm for x in ("/", "/method", "/about", "/privacy", "/corrections")))
_gh = c.get("/", query_string={"q": _good_c}).get_data(as_text=True)
_bh = c.get("/", query_string={"q": _bad_c}).get_data(as_text=True)
_ch = c.get("/", query_string={"q": _cur_c}).get_data(as_text=True)
_cbh = c.get("/", query_string={"q": _cur_bad}).get_data(as_text=True)
t("noindex: a result that fails the record check says noindex and still renders its answer",
  '<meta name="robots" content="noindex" />' in _bh and 'id="server-result"' in _bh
  and '<meta name="robots" content="noindex" />' in _cbh and 'id="server-result"' in _cbh)
t("noindex: a sound, non-personal saved result that is not a curated example is noindex and still renders",
  '<meta name="robots" content="noindex" />' in _gh and 'id="server-result"' in _gh and 'content="index, follow' not in _gh)
t("noindex: a curated example with a sound record is indexable", 'content="noindex"' not in _ch and 'content="index, follow' in _ch)
t("noindex: a curated claim typed with other case, spacing and a trailing mark is still the same indexable page",
  'content="noindex"' not in c.get("/", query_string={"q": "  vaccines  CAUSE autism?? "}).get_data(as_text=True))
_js_cur = re.findall(r'"([^"]+)"', re.search(r"const EXAMPLES = \[(.*?)\n\];", (ROOT / "static" / "app.js").read_text(), re.S).group(1))
t("CURATED_CLAIMS in display.py is the EXAMPLES array in app.js, same claims in the same order",
  list(_dsp.CURATED_CLAIMS) == _js_cur, (_dsp.CURATED_CLAIMS, _js_cur))
t("curated match ignores case, whitespace and trailing punctuation, and nothing else",
  _dsp.is_curated_claim("  5g weakens   your IMMUNE system. ") and not _dsp.is_curated_claim("5G weakens your immune system in kids")
  and not _dsp.is_curated_claim("") and not _dsp.is_curated_claim(None))
t("the claim the trust section offers is a curated example", _dsp.is_curated_claim(appmod.EXAMPLE_CLAIM))
t("noindex: the home page and the info pages stay indexable", 'content="noindex"' not in c.get("/").get_data(as_text=True)
  and 'content="noindex"' not in c.get("/method").get_data(as_text=True))
_canon = lambda h: _re.search(r'<link rel="canonical" href="([^"]*)"', h).group(1)
_variants = [_good_c, _good_c.upper(), _good_c + "?", "  " + _good_c + "  "]
_cs = {_canon(c.get("/", query_string={"q": v}).get_data(as_text=True)) for v in _variants}
t("canonical: every phrasing of one claim has one stable canonical, the saved claim's own address",
  _cs == {"http://localhost/?q=" + urllib.parse.quote(_good_c)}, _cs)
t("canonical: every page that can be indexed has one, absolute",
  all(_re.search(r'<link rel="canonical" href="http://localhost[^"]*"', c.get(u).get_data(as_text=True))
      for u in ("/", "/about", "/method", "/corrections", "/privacy"))
  and _canon(c.get("/", query_string={"q": "never checked at all"}).get_data(as_text=True)) == "http://localhost/")
_rb = c.get("/robots.txt").get_data(as_text=True)
_groups = [g for g in _rb.split("\n\n") if g.startswith("User-agent")]
t("robots.txt: allows all, keeps /api/ and /stats out for every crawler group, and names the sitemap",
  _rb.count("User-agent:") == 5 and all("Disallow: /api/" in g and "Disallow: /stats" in g and "Allow: /" in g for g in _groups)
  and _rb.rstrip().endswith("Sitemap: http://localhost/sitemap.xml"), _rb)
_pr = _dsp.saved_record_problems
t("record check: names each kind of problem, and a sound record has none",
  _pr(_good_c, "true", ["S1"], [_gs], {"rests_on": 1}, "q", "2020-01-01T00:00:00+00:00") == []
  and "cited_not_in_studies" in _pr("c", "true", ["X"], [_gs], None, "q", "2020-01-01T00:00:00")
  and "count_mismatch" in _pr("c", "true", ["S1"], [_gs], {"rests_on": 3}, "q", "2020-01-01T00:00:00")
  and "empty_query" in _pr("c", "true", [], [], None, "  ", "2020-01-01T00:00:00")
  and "future_date" in _pr("c", "true", [], [], None, "q", "2999-01-01T00:00:00")
  and "unparseable_date" in _pr("c", "true", [], [], None, "q", "soon")
  and "missing_date" in _pr("c", "true", [], [], None, "q", "")
  and "unknown_verdict" in _pr("c", "maybe", [], [], None, "q", "2020-01-01T00:00:00")
  and _pr("c", "true", "oops", [], None, "q", "2020-01-01T00:00:00") == ["unreadable_json"])
import display as _disp_p
t("sitemap: a claim with an email, a phone number or first-person wording is never offered for indexing",
  all(_disp_p.looks_personal(x) for x in ("my mum's diabetes is cured by cinnamon", "Can I take ibuprofen with warfarin",
                                         "email jo@example.com about zinc", "call 020 7946 0958 for the cure"))
  and not any(_disp_p.looks_personal(x) for x in ("Vitamin D prevents colds", "Iron supplements cure fatigue",
                                                 "Omega 3 helps memory in adults over 65"))
  and "looks_personal" in _disp_p.saved_record_problems("my back pain is cured by yoga", "false", [], [], {}, "q", "2026-01-01"))

_PERSONAL_CLAIMS = {
    "contact": ["email jo@example.com about zinc", "call 020 7946 0958 for the cure", "text +1 (415) 555-0132 if zinc works",
                "I live at 12 Baker Street and garlic helps", "does it work, ask at www.cureblog.com", "follow @drjane for the cure",
                "instagram.com/jane_doe says honey cures acne", "garlic cures colds, PO Box 55", "turmeric cures pain, SW1A 1AA"],
    "age": ["I'm 34 and zinc shortens colds", "34 years old and low iron", "a 34yo with gout and cherries", "my 6-year-old and fish oil",
            "does a statin help someone aged 70", "age 45 and creatine", "a 6-year-old with eczema and oats"],
    "name": ["Dr. Smith says garlic cures colds", "Mrs Jones cured a cough with honey", "Ms Patel and turmeric",
             "my son Tom needs iron", "my friend Priya says keto cured her", "John Smith cured cancer with turmeric",
             "Does Sarah's diet work", "Is it true that Mark lost weight on fasting"],
    "medical": ["I have diabetes, is cinnamon good", "my blood pressure is high, does garlic help", "I take metformin with vitamin B12",
                "my diagnosis was lupus, does fish oil help", "I was diagnosed with lupus", "my test results show low iron",
                "my pregnancy and caffeine", "my child has eczema, does oatmeal help", "my doctor said to quit gluten",
                "I am pregnant and eat sushi", "She was diagnosed with cancer", "he tested positive, does zinc help"],
}
_ORDINARY_CLAIMS = ["Vitamin D prevents colds", "Intermittent fasting helps weight loss", "Creatine improves strength in older adults",
                    "Type 2 diabetes can be reversed with diet", "Omega 3 helps memory in adults over 65",
                    "Does Tylenol cause autism in children", "Can Vitamin C shorten a cold", "Cracking your knuckles causes arthritis",
                    "5G weakens your immune system", "Is the Mediterranean diet good for the heart",
                    "Caffeine during pregnancy raises miscarriage risk", "Statins cause memory loss",
                    "Coffee lowers the risk of Parkinson's disease", "Does HIV spread through kissing?",
                    "Walking 10,000 steps a day extends life", "Ozempic cures alcoholism"]
for _cat, _xs in _PERSONAL_CLAIMS.items():
    _miss = [x for x in _xs if not _disp_p.looks_personal(x)]
    t(f"personal screen: every {_cat} example is flagged ({len(_xs)})", not _miss, _miss)
_fp = [x for x in _ORDINARY_CLAIMS if _disp_p.looks_personal(x)]
t(f"personal screen: {len(_ORDINARY_CLAIMS)} ordinary health claims all pass", not _fp, _fp)
for _x in [x for xs in _PERSONAL_CLAIMS.values() for x in xs]:
    t(f"personal screen flags: {_x}", _disp_p.looks_personal(_x))
for _x in _ORDINARY_CLAIMS:
    t(f"personal screen passes: {_x}", not _disp_p.looks_personal(_x))
_ex_js = re.search(r"const EXAMPLES = \[(.*?)\n\];", (ROOT / "static" / "app.js").read_text(), re.S).group(1)
_examples = re.findall(r'"([^"]+)"', _ex_js)
t("personal screen: every EXAMPLES claim in app.js passes", len(_examples) >= 7 and not any(_disp_p.looks_personal(x) for x in _examples),
  [x for x in _examples if _disp_p.looks_personal(x)])
t("personal screen: curly apostrophes and case do not hide a first-person word",
  _disp_p.looks_personal("I\u2019M 34") and _disp_p.looks_personal("MY BLOOD PRESSURE") and not _disp_p.looks_personal(""))

_pv = c.get("/privacy").get_data(as_text=True)
_pv_txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", _pv))
t("privacy: it says typed-claim pages are not indexed, are kept out of the sitemap, and only example claims can be indexed",
  "not indexed by search engines" in _pv_txt and "out of the sitemap" in _pv_txt and "Only the site's own example claims can be indexed" in _pv_txt)
t("privacy: still no dash after the indexing paragraph", "\u2014" not in _pv_txt and "\u2013" not in _pv_txt)

_pers, _ordc = "My son Tom has asthma, does honey help", "Honey soothes a cough in adults"
for _cl, _q in ((_pers, "honey AND asthma"), (_ordc, "honey AND cough")):
    db.put_cached_verdict(_cl, _q, "complicated", "Honey may help (Study 1).", ["S1"], [_gs], tldr="Mixed.", still_open="Doses vary.")
_pages = {u: c.get(u).get_data(as_text=True) for u in ("/", "/no-such-page-xyz")}
_pages["/llms.txt"] = c.get("/llms.txt").get_data(as_text=True)
t("lists: a personal saved claim is on no public list (/, 404, /llms.txt)",
  all("Tom" not in v and "asthma" not in v for v in _pages.values()), [u for u, v in _pages.items() if "Tom" in v])
t("lists: an ordinary saved claim still shows on / and the 404 page",
  all(_ordc in _pages[u] for u in ("/", "/no-such-page-xyz")))
t("lists: /llms.txt lists only curated claims, so an ordinary typed one is left out", _ordc not in _pages["/llms.txt"]
  and "Vaccines cause autism" in _pages["/llms.txt"])
for _ in range(2):
    for _cl in (_pers, _ordc):
        db.log_check(_cl, "complicated", "x", [])
_tr = c.get("/api/trending").get_json()
_tp = appmod._public_claims([{"claim": _pers}, {"claim": _ordc}], 8, key="claim")
t("lists: the trending filter drops a personal claim and keeps an ordinary one", _tp == [{"claim": _ordc}])
t("lists: /api/trending carries the ordinary claim checked twice and not the personal one",
  any(x["claim"] == _ordc for x in _tr) and not any("Tom" in x["claim"] for x in _tr), _tr)
t("privacy: it says lists of recent checks leave out a claim that looks personal",
  "Lists of recent checks on the site leave out any claim that looks like it holds a name, an age, contact details or a personal medical detail"
  in _re.sub(r"\s+", " ", c.get("/privacy").get_data(as_text=True)))


# ---- Day counts: per-day totals, no identifiers ------------------------------------------
def _tot():
    db.flush_counts()
    out = {}
    for r in db.counts_since(30):
        out[r["name"]] = out.get(r["name"], 0) + r["count"]
    return out
def _delta(before, after):
    return {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after) if after.get(k, 0) != before.get(k, 0)}

_b = _tot()
c.get("/"); c.get("/checks"); c.get("/", query_string={"q": _good_c}); c.get("/about"); c.get("/method"); c.get("/corrections"); c.get("/privacy")
c.get("/", query_string={"q": "a claim nobody has checked yet"}); c.get("/no-such-page"); c.get("/healthz"); c.get("/sitemap.xml")
_d = _delta(_b, _tot())
t("counts: page views by kind: home (and an unchecked ?q=) 2, a saved result 1, info pages 4; the /checks redirect, 404s, the sitemap and healthz are not views",
  _d == {"view.home": 2, "view.result": 1, "view.info": 4}, _d)
_b = _tot()
P(_good_c, ip="9.9.9.1"); P("", ip="9.9.9.1"); P("x" * 600, ip="9.9.9.1")
_d = _delta(_b, _tot())
t("counts: a claim sent counts once as submitted and once as completed; an empty or too long one counts as neither",
  _d == {"check.submitted": 1, "check.completed": 1}, _d)
_b = _tot()
_r = c.post("/api/count", json={"name": "share.click"}); c.post("/api/count", json={"name": "client_error.network"})
_d = _delta(_b, _tot())
t("counts: POST /api/count with an allowed name adds one and answers 204", _r.status_code == 204 and _d == {"share.click": 1, "client_error.network": 1}, _d)
_b = _tot()
_bad = [c.post("/api/count", json={"name": "view.home"}), c.post("/api/count", json={"name": "x" * 50}), c.post("/api/count", json={"name": "share.click", "claim": "my back pain"}),
        c.post("/api/count", json={}), c.post("/api/count", data="share.click"), c.post("/api/count", json=["share.click"]),
        c.post("/api/count", json={"name": "share.click", "pad": "y" * 400}), c.get("/api/count")]
t("counts: any other name, extra fields, a big body, a non-JSON body or a GET is refused and counts nothing",
  [x.status_code for x in _bad] == [400, 400, 400, 400, 400, 400, 400, 405] and not _delta(_b, _tot()), [x.status_code for x in _bad])
_b = _tot()
_g = c.post("/api/count", json={"name": "share.click"}, headers={"Sec-GPC": "1"})
t("counts: a beacon from a browser sending Global Privacy Control is answered and not counted", _g.status_code == 204 and not _delta(_b, _tot()))
_ips = [c.post("/api/count", json={"name": "share.copy"}, environ_base={"REMOTE_ADDR": "9.9.9.77"}).status_code for _ in range(70)]
t("counts: /api/count is rate-limited per address like the other endpoints", _ips[:60] == [204] * 60 and 429 in _ips[60:], set(_ips))
with db.get_conn() as _cn:
    _cols = [r[1] for r in _cn.execute("PRAGMA table_info(counters)").fetchall()]
    _rows = _cn.execute("SELECT day, name, count FROM counters").fetchall()
_dump = json.dumps([list(r) for r in _rows])
t("counts: the table holds only day, name and count; every name is on the fixed list; no claim text or address is in it",
  _cols == ["day", "name", "count"] and all(r[1] in db.COUNTER_NAMES for r in _rows)
  and _good_c not in _dump and "9.9.9" not in _dump and "my back pain" not in _dump, _cols)
db.count_event("view.nonsense"); db.count_event(_good_c)
_nb = _tot()
t("counts: a name not on the list is ignored by the writer too", not _delta(_nb, _tot()) and "view.nonsense" not in _tot())
_jsapp = (ROOT / "static" / "app.js").read_text()
t("counts: the page skips its beacon when the browser sends Global Privacy Control, and sends only a name",
  "navigator.globalPrivacyControl" in _jsapp and "JSON.stringify({ name })" in _jsapp
  and all(n in _jsapp for n in ('countEvent("share.click")', 'countEvent("share.copy")', '"client_error."')))
_cfg = {n for n in db.CLIENT_COUNTERS}
t("counts: every error kind the page classifies has a client counter", {"client_error." + k for k in ("invalid", "network", "limit", "provider")} <= _cfg)

# The same counts go to Turso when it is configured.
_ftc = FakeTurso()
os.environ["TURSO_DATABASE_URL"] = _URL; os.environ["TURSO_AUTH_TOKEN"] = _SECRET
db._http_post = _ftc; db._reset_turso_state()
db.count_event("share.click"); db.count_event("share.click"); db.count_event("view.home"); db.flush_counts()
_tc = dict((n, k) for n, k in _ftc.con.execute("select name, count from counters").fetchall())
db.count_event("share.click"); db.flush_counts()
_tc2 = dict((n, k) for n, k in _ftc.con.execute("select name, count from counters").fetchall())
t("counts: with Turso configured the counters table lives there, and a second flush adds to the same row",
  _tc == {"share.click": 2, "view.home": 1} and _tc2["share.click"] == 3, (_tc, _tc2))
_ftc.fail = OSError("down")
db.count_event("share.click"); db.flush_counts()
t("counts: Turso failing never raises from a count", True)
os.environ.pop("TURSO_DATABASE_URL", None); os.environ.pop("TURSO_AUTH_TOKEN", None)
db._http_post = _real_post; db._reset_turso_state()


# ---- Error counts: server errors and provider failures, as per-day totals ----------------------
_b = _tot()
fg.models.script = [errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})] * 3
P("count err 429", ip="9.9.8.1")
fg.models.script = [errors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}})] * 3
P("count err 503", ip="9.9.8.2")
fg.models.script = [errors.ClientError(418, {"error": {"message": "teapot", "status": "TEAPOT"}})] * 3
P("count err other", ip="9.9.8.3")
fg.models.script = [errors.ClientError(400, {"error": {"message": "API key not valid.", "status": "INVALID_ARGUMENT"}})]
P("count err auth", ip="9.9.8.4")
_d = _delta(_b, _tot())
t("error counts: a provider 429, a 503, another provider error and a rejected key each add one to their own counter, and each is a submitted claim that did not complete",
  _d == {"provider.rate_limit": 1, "provider.unavailable": 1, "provider.other": 1, "provider.auth": 1, "check.submitted": 4}, _d)
_b = _tot()
_real_swf2 = pubmed.search_with_fallback
pubmed.search_with_fallback = lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down"))
fg.models.script = [Resp("q")]
P("count err pubmed", ip="9.9.8.5")
pubmed.search_with_fallback = lambda *a, **k: (_ for _ in ()).throw(ValueError("0xFEED"))
fg.models.script = [Resp("q")]
appmod.app.logger.setLevel(logging.CRITICAL)
P("count err crash", ip="9.9.8.6")
fg.models.script = [Resp("q")]
stream("count err crash two", ip="9.9.8.7")
pubmed.search_with_fallback = _real_swf2
_d = _delta(_b, _tot())
t("error counts: PubMed unreachable adds one pubmed.failure, an unhandled crash in a check adds one error.server (JSON and streamed alike)",
  _d == {"pubmed.failure": 1, "error.server": 2, "check.submitted": 3}, _d)
_b = _tot()
ratelimit.PER_USER = ratelimit.SlidingWindow(1, 600)
fg.models.script = [Resp("q"), Resp(VJ("true", [1]))]; P("count limit one", ip="9.9.8.8"); P("count limit two", ip="9.9.8.8")
ratelimit.PER_USER = ratelimit.SlidingWindow(100, 600)
_d = _delta(_b, _tot())
t("error counts: a limit we set ourselves is not a provider failure or a server error",
  _d == {"check.submitted": 2, "check.completed": 1}, _d)
_b = _tot()
_real_ss = db.store_status
db.store_status = lambda: (_ for _ in ()).throw(RuntimeError("0xBEEF"))
_r5 = c.get("/healthz"); _r6 = c.get("/api/trending") if False else None
db.store_status = _real_ss
appmod.app.logger.setLevel(logging.ERROR)
_d = _delta(_b, _tot())
t("error counts: an unhandled exception on a route is a 500 and adds one error.server", _r5.status_code == 500 and _d == {"error.server": 1}, _d)
_rawcounts = json.dumps([[r["day"], r["name"]] for r in db.counts_since(30)])
t("error counts: no claim text, exception text or address reaches the table",
  all(x not in _rawcounts for x in ("count err", "0xFEED", "0xBEEF", "9.9.8", "quota", "teapot")))


# ---- /stats: the day counts behind a token ------------------------------------------------
os.environ.pop("STATS_TOKEN", None)
_s0 = [c.get("/stats"), c.get("/stats", query_string={"token": "anything"})]
t("stats: with STATS_TOKEN unset the page does not exist (404, even with a token)", [x.status_code for x in _s0] == [404, 404])
os.environ["STATS_TOKEN"] = "s3cret-tok-7"
_s1 = [c.get("/stats"), c.get("/stats", query_string={"token": ""}), c.get("/stats", query_string={"token": "wrong"}),
       c.get("/stats", query_string={"token": "s3cret-tok-"}), c.get("/stats", query_string={"token": "caf\u00e9"})]
t("stats: no token, an empty one, a wrong one, a prefix of the right one and a non-ASCII one are all a 404",
  [x.status_code for x in _s1] == [404] * 5 and all(b"s3cret" not in x.data for x in _s1), [x.status_code for x in _s1])
_b = _tot()
db.count_event("share.click"); db.count_event("provider.rate_limit"); db.count_event("view.home")
_s2 = c.get("/stats", query_string={"token": "s3cret-tok-7"}, environ_base={"REMOTE_ADDR": "9.9.7.1"})
_sb = _s2.get_data(as_text=True)
t("stats: the right token is a 200 that is not cached, not indexed, and says so", _s2.status_code == 200
  and _s2.headers["Cache-Control"] == "no-store" and "noindex" in _s2.headers["X-Robots-Tag"]
  and '<meta name="robots" content="noindex, nofollow" />' in _sb, _s2.status_code)
t("stats: it is a plain table in the doc-page style: 30 day rows, the counters grouped, totals and today",
  'class="wrap doc"' in _sb and _sb.count('<th scope="row">20') == 30 and "Page views" in _sb and "Errors the server saw" in _sb
  and "AI provider: rate limited (429)" in _sb and "PubMed failures" in _sb and "Not medical advice" in _sb)
_totals = {r["name"]: r["count"] for r in map(lambda n: {"name": n[0], "count": n[1]}, [(k, v) for k, v in _tot().items()])}
import re as _re_s
_m = _re_s.search(r"Share button</th><td class=\"num\">(\d+)</td><td class=\"num\">(\d+)</td>", _sb)
t("stats: the numbers on the page are the saved totals", _m and int(_m.group(1)) == _totals["share.click"] == int(_m.group(2)), (_m and _m.groups(), _totals.get("share.click")))
t("stats: no claim text, address or token on the page; no dash in what it says",
  all(x not in _sb for x in (_good_c, "9.9.7.1", "s3cret-tok-7", "count err")) and not _DASH.search(_re_s.sub(r"<[^>]+>", " ", _sb)))
t("stats: it is not in the sitemap and robots disallows it, in every group",
  "/stats" not in c.get("/sitemap.xml").get_data(as_text=True) and c.get("/robots.txt").get_data(as_text=True).count("Disallow: /stats") >= 5)
import logging as _lg_s
_rec = _lg_s.LogRecord("werkzeug", 20, __file__, 1, '127.0.0.1 - - "GET /stats?token=s3cret-tok-7&x=1 HTTP/1.1" 200 -', (), None)
_flt = [f for f in _lg_s.getLogger("werkzeug").filters if type(f).__name__ == "_RedactToken"]
_ok = bool(_flt) and _flt[0].filter(_rec)
t("stats: the web server's request log does not carry the token", _ok and "s3cret" not in _rec.getMessage() and "token=REDACTED&x=1" in _rec.getMessage(), _rec.getMessage())
for _u in ('GET /stats?token%3Ds3cret-tok-7&x=1 HTTP', 'GET /stats?a=1&TOKEN=s3cret-tok-7 HTTP', 'GET /stats?x=1&token%3Ds3cret-tok-7 HTTP'):
    _r2 = _lg_s.LogRecord("werkzeug", 20, __file__, 1, _u, (), None)
    _flt[0].filter(_r2)
    t("stats: the log redaction also catches an encoded or upper-case token: " + _u, "s3cret" not in _r2.getMessage() and "REDACTED" in _r2.getMessage(), _r2.getMessage())
_bh = [c.get("/stats", headers={"Authorization": "Bearer s3cret-tok-7"}, environ_base={"REMOTE_ADDR": "9.9.7.20"}),
       c.get("/stats", headers={"Authorization": "bearer s3cret-tok-7"}, environ_base={"REMOTE_ADDR": "9.9.7.20"}),
       c.get("/stats", headers={"Authorization": "Bearer wrong"}, environ_base={"REMOTE_ADDR": "9.9.7.20"}),
       c.get("/stats", headers={"Authorization": "s3cret-tok-7"}, environ_base={"REMOTE_ADDR": "9.9.7.20"})]
t("stats: the token may come in an Authorization: Bearer header (right is 200, wrong or malformed is 404)",
  [x.status_code for x in _bh] == [200, 200, 404, 404] and _bh[0].headers["Cache-Control"] == "no-store", [x.status_code for x in _bh])
_fl = [c.get("/stats", query_string={"token": "nope"}, environ_base={"REMOTE_ADDR": "9.9.7.9"}).status_code for _ in range(25)]
_after = c.get("/stats", query_string={"token": "s3cret-tok-7"}, environ_base={"REMOTE_ADDR": "9.9.7.9"}).status_code
t("stats: wrong guesses are all a 404 and never lock out the right token (only failures count toward the limit)",
  set(_fl) == {404} and _after == 200, (set(_fl), _after))
_ok_many = [c.get("/stats", query_string={"token": "s3cret-tok-7"}, environ_base={"REMOTE_ADDR": "9.9.7.10"}).status_code for _ in range(30)]
t("stats: the right token is never limited, however often it is used", set(_ok_many) == {200}, set(_ok_many))
os.environ.pop("STATS_TOKEN", None)


# ---- A study classified off topic is never relied on ---------------------------------------
# The audit saw "Does creatine cause hair loss?" under "Relied on for the
# verdict" beside "3 off topic". A fresh check drops off-topic studies from
# its cited list; a saved record may still carry one in both, and the page
# must not trust it.
_OT = [_study("OT1", "Creatine and strength: a meta-analysis", types=["Meta-Analysis"]),
       _study("OT2", "Creatine loading and bench press", types=["Randomized Controlled Trial"]),
       _study("OT3", "Does creatine cause hair loss? A review", types=["Review"]),
       _study("OT4", "Creatine and kidney function", types=["Journal Article"])]
_OT[2]["off_topic"] = verdict.OFF_OUTCOME
_OT[3]["off_topic"] = verdict.OFF_OUTCOME
_ot_bd = {"strength": "x", "evidence": [], "parts": [], "effect_size": "", "applies_to": "",
          "not_applies_to": "", "unknowns": "", "rests_on": 3}
db.put_cached_verdict("creatine strength off topic saved", "creatine AND strength", "true",
                      "Studies 1 and 2 show a gain. Study 3 shows hair loss.", ["OT1", "OT2", "OT3"], _OT,
                      tldr="Creatine increases strength.", still_open="Doses vary.", breakdown=_ot_bd)
_oth = c.get("/?q=creatine+strength+off+topic+saved").data.decode()
_ot_src = _oth[_oth.index('id="sources"'):_oth.index('class="nosteps"')]
_ot_groups = [(m.group(2), m.group(1) is not None, int(m.group(3)), _re_s.findall(r">PMID (\w+)</a>", m.group(4)))
              for m in _re_s.finditer(r'<details class="source-group"( open)?>\s*<summary class="group-head">'
                                      r'<span class="group-name">([^<]*)</span><span class="group-count">(\d+)</span>'
                                      r'</summary>(.*?)</details>', _ot_src, _re_s.S)]
_otg = {name: (opened, n, pm) for name, opened, n, pm in _ot_groups}
t("off topic: a study both cited and off topic is never under 'Relied on' on the server-rendered page",
  "Relied on for the verdict" in _otg and "OT3" not in _otg["Relied on for the verdict"][2]
  and _otg["Relied on for the verdict"][2] == ["OT1", "OT2"], _ot_groups)
t("  it sits in a 'Set aside: off topic' group, folded, with the other off-topic study",
  _otg.get("Set aside: off topic") == (False, 2, ["OT3", "OT4"]) and _ot_src.count(">PMID OT3</a>") == 1, _ot_groups)
t("  and its line says it was set aside, not relied on",
  "Read and set aside: measures a different outcome" in _ot_src.split("PMID OT3")[1].split("</li>")[0])
_otj = P("creatine strength off topic saved", ip="4.4.4.6").get_json()
_ot_used = [s_["pmid"] for s_ in _otj["studies"] if s_["cited_in_verdict"]]
t("  the counts agree: Rests on 2, '2 used', a relied-on group of 2, and 2 marked used in the JSON",
  '<span class="group-count">2 studies</span>' in _oth and '3 studies</span>' not in _oth
  and "2 used for this verdict" in _oth and "3 used for this verdict" not in _oth
  and _otg["Relied on for the verdict"][1] == 2 and _ot_used == ["OT1", "OT2"]
  and _otj["breakdown"]["rests_on"] == 2 and _otj["evidence"]["relied_on"] == 2, (_ot_used, re.findall(r"Rests on.{0,60}", _oth)))
t("  the sentence resting only on the off-topic study is not printed, and the stamp stands on the two left",
  "hair loss" not in _otj["explanation"] and _otj["verdict"] in ("true", "complicated"), _otj["explanation"])
t("  the ClaimReview markup does not mark it as a source the verdict was based on",
  '"identifier": "PMID:OT3", "isBasedOn": false' in _oth or
  all(not x["isBasedOn"] for x in json.loads(_oth.split('type="application/ld+json"')[1].split(">", 1)[1].split("</script>")[0])["citation"]
      if x["identifier"] == "PMID:OT3"))
t("  the saved-record check names it, so a crawler is told to skip the record",
  "cited_off_topic" in _dsp.saved_record_problems("c", "true", ["OT1", "OT3"], _OT, None, "q", "2020-01-01T00:00:00")
  and "cited_off_topic" not in _dsp.saved_record_problems("c", "true", ["OT1"], _OT, None, "q", "2020-01-01T00:00:00"))
# A saved verdict whose only cited study is off topic rests on nothing, so the
# rule a fresh check obeys applies: "Not enough evidence".
db.put_cached_verdict("creatine only off topic saved", "creatine AND strength", "true",
                      "Study 3 shows it.", ["OT3"], _OT, tldr="Creatine increases strength.", still_open="Doses vary.")
_oto = P("creatine only off topic saved", ip="4.4.4.7").get_json()
t("off topic: a saved verdict whose only cited study is off topic is 'Not enough evidence', with none marked used",
  _oto["verdict"] == "insufficient" and _oto["tldr"] == verdict.UNTESTED_TLDR
  and not any(s_["cited_in_verdict"] for s_ in _oto["studies"]), (_oto["verdict"], _oto["tldr"]))
_otoh = c.get("/?q=creatine+only+off+topic+saved").data.decode()
t("  on the page too, with no relied-on group",
  "Not enough evidence" in _otoh.split('id="server-result"')[0] and "Relied on for the verdict</span>" not in _otoh)
# The front page's list shows the stamp the page would.
t("  and the latest-checks list does not stamp it 'Likely true'",
  appmod._listed({"claim_text": "creatine only off topic saved", "verdict": "true", "search_query": "q",
                  "tldr": "x", "studies": _OT, "cited": ["OT3"]})["verdict"] == "insufficient"
  and appmod._listed({"claim_text": "c", "verdict": "complicated", "search_query": "q",
                      "tldr": "x", "studies": _OT, "cited": ["OT1", "OT3"]})["cited"] == ["OT1"])
# A fresh check never cites an off-topic study in the first place.
# The model cites study 3 and, in the same answer, judges it off its subject.
fg.models.script = [Resp("creatine AND strength"), Resp(VJ("true", [1, 3], relevance=[
    {"study": 1, "subject": True, "outcome": True}, {"study": 2, "subject": True, "outcome": True},
    {"study": 3, "subject": False, "outcome": False}]))]
_FAKE_OT = [_study("FO1", "Creatine and strength trial", abstract="Creatine increased strength.",
                   types=["Randomized Controlled Trial"]),
            _study("FO2", "Creatine trial two", abstract="Creatine increased strength.", types=["Randomized Controlled Trial"]),
            _study("FO3", "Paracetamol and headache", abstract="Paracetamol eased headache.",
                   types=["Randomized Controlled Trial"])]
_ot_saved_search = pubmed.search_with_fallback
pubmed.search_with_fallback = lambda q, max_results=8, surrogate="": (_FAKE_OT, q, False)
try:
    _otf = P("creatine fresh off topic check", ip="4.4.4.8").get_json()
finally:
    pubmed.search_with_fallback = _ot_saved_search
t("off topic: in a fresh check the model's citation of an off-topic study is dropped, so none is both marked used and off topic",
  _otf.get("studies") and any(s_["pmid"] == "FO3" and s_["off_topic"] and not s_["cited_in_verdict"] for s_ in _otf["studies"])
  and not any(s_["cited_in_verdict"] and s_["off_topic"] for s_ in _otf["studies"]),
  [(s_["pmid"], s_["cited_in_verdict"], s_["off_topic"]) for s_ in _otf.get("studies") or []])
# Both renderers group and describe the same way, on raw records that still
# carry both flags (a result saved on the device) and on the server payload.
_ot_raw = [dict(_study("J1", "a"), cited_in_verdict=True), dict(_study("J2", "b"), cited_in_verdict=True, off_topic="x."),
           dict(_study("J3", "c"), background="lab", background_why="animal or lab"), _study("J4", "d"),
           dict(_study("J5", "e"), off_topic="y")]
_ot_recs = [_ot_raw, _otj["studies"], _oto["studies"], []]
_py_groups = [_dsp.source_groups(r_) for r_ in _ot_recs]
t("off topic: the server groups a both-flag study as excluded, and every study lands in exactly one group",
  _py_groups[0] == {"cited": [0], "back": [2], "others": [3], "excluded": [1, 4]}
  and all(sorted(sum(g_.values(), [])) == list(range(len(r_))) for g_, r_ in zip(_py_groups, _ot_recs)), _py_groups)
t("  and never describes it as relied on", _dsp.study_why(_ot_raw[1]) == "Read and set aside: x.")
if _shutil.which("node"):
    _ot_js = (ROOT / "static" / "app.js").read_text()
    _ot_fn = "const TYPES = [" + _ot_js.split("const TYPES = [")[1].split("function escapeHtml")[0]
    _ot_out = json.loads(_subprocess.run(
        ["node", "-e", _ot_fn + "const R = " + json.dumps(_ot_recs) + ";"
         "console.log(JSON.stringify({g: R.map(sourceGroups), w: R.map((r) => r.map(studyWhy)),"
         " d: R.map((r) => dropOffTopicCites({studies: JSON.parse(JSON.stringify(r))}).studies.map((s) => !!s.cited_in_verdict))}));"],
        capture_output=True, text=True, check=True).stdout)
    t("  the script and the server put every study in the same group, on four records",
      _ot_out["g"] == _py_groups, (_ot_out["g"], _py_groups))
    t("  and write the same line for each", _ot_out["w"] == [[_dsp.study_why(s_) for s_ in r_] for r_ in _ot_recs])
    t("  and a result saved on the device stops marking an off-topic study used before anything reads it",
      _ot_out["d"][0] == [True, False, False, False, False]
      and _ot_out["d"][1] == [s_["cited_in_verdict"] for s_ in _otj["studies"]])
_ot_rr = _ot_js[_ot_js.index("function renderResult"):] if _shutil.which("node") else (ROOT / "static" / "app.js").read_text()
t("  renderResult clears the stale flags first and builds the list from sourceGroups, with the excluded group folded",
  _ot_rr.index("dropOffTopicCites(data)") < _ot_rr.index("currentResult = data")
  and "sourceGroups(data.studies)" in _ot_rr and 'group("Set aside: off topic", rows(g.excluded), false)' in _ot_rr)
_ot_tpl = (ROOT / "templates" / "index.html").read_text()
t("  both renderers name the off-topic group in the same words, and the old label is gone from both",
  '<span class="group-name">Set aside: off topic</span>' in _ot_tpl and 'group("Set aside: off topic",' in _ot_rr
  and not any("Excluded (off-topic)" in x for x in (_ot_tpl, (ROOT / "static" / "app.js").read_text())))

# ---- /privacy says exactly what is counted -------------------------------------------------
_pv = c.get("/privacy").get_data(as_text=True)
_pt = " ".join(_re_s.sub(r"<[^>]+>", " ", _pv).split())
t("privacy: it names every kind of count (page views, claims submitted, results completed, shares, errors by kind)",
  all(x in _pt for x in ("What we count", "page views", "claims submitted", "results completed", "shares", "errors",
                         "server errors", "PubMed not answering", "the kind of error screen")), _pt[:200])
t("privacy: it says a count is per day with no IP address, user agent, cookie, visitor ID or claim text, where it is kept, and the GPC courtesy",
  all(x in _pt for x in ("daily totals", "IP address, a user agent, a cookie, a visitor ID or any claim text", "same database as the saved answers",
                         "Global Privacy Control")) and "No tracking" in _pt and "no cookies" in _pt)
t("privacy: it has no dash", not _DASH.search(_pt))
# The footer's privacy sentence is the one at the top of /privacy, from one
# source, and it says the claim text is kept: no stronger than the page.
_ft_home = c.get("/").get_data(as_text=True)
_ft_foot = " ".join(_re_s.sub(r"<[^>]+>", " ", _ft_home.split('<footer class="foot')[1].split("</footer>")[0]).split())
_ft_lede = " ".join(_re_s.sub(r"<[^>]+>", " ", _pv.split('<p class="lede">')[1].split("</p>")[0]).split())
import html as _ft_html  # noqa: E402
_ft_foot = _ft_html.unescape(_ft_foot)
_ft_lede = _ft_html.unescape(_ft_lede)
_ft_prov = verdict.provider_label()
_ft_line = appmod.privacy_line(_ft_prov)
t("footer privacy: the sentence is the owner's, naming the provider the claim is sent to",
  _ft_line == f"Claim text is stored without anything that identifies you and is sent to {_ft_prov} to check it.", _ft_line)
t("  the footer and the /privacy short version print the same sentence",
  _ft_line in _ft_foot and _ft_lede == "The short version: " + _ft_line[0].lower() + _ft_line[1:], (_ft_foot, _ft_lede))
t("  the lede runs on after its colon in lower case, with no stray capital",
  _ft_lede.startswith("The short version: claim text is stored"), _ft_lede)
_ft_p = _ft_home.split('<footer class="foot')[1].split("</footer>")[0]
_ft_p = [x for x in _ft_p.split("<p") if "identifies you" in x][0].split("</p>")[0]
t("  in the footer the sentence is followed by a link to /privacy",
  _re_s.search(r'identifies you and is sent to [^<]* to check it\.\s*<a href="/privacy">[^<]+</a>\s*$', _ft_p), _ft_p)
_ft_css = (ROOT / "static" / "style.css").read_text()
t("  the footer's Privacy details link has a tap area of 44px or more, grown around it, so the sentence does not move",
  re.search(r"\.foot \.fine:not\(\.foot-links\) a \{[^}]*position: relative", _ft_css)
  and re.search(r"\.foot \.fine:not\(\.foot-links\) a::after \{[^}]*inset: -26px -6px -2px", _ft_css))
t("  and it stays true: /privacy keeps the claim text with nothing that identifies the person, and sends it to the same provider",
  all(x in _pt for x in ("The text of each claim", "no name", "no account", "no IP address stored with a check",
                         "it does not get your IP address or anything else that identifies you"))
  and f"and {_ft_prov} to turn the claim into a search" in _ft_html.unescape(_pt))
t("  the stronger line is gone from both, and neither template hard-codes the sentence",
  "tied to you" not in _ft_foot and "tied to you" not in _ft_lede
  and all("identifies you and is sent" not in (ROOT / "templates" / f_).read_text()
          and "privacy_line(provider" in (ROOT / "templates" / f_).read_text()
          for f_ in ("index.html", "privacy.html"))
  and not _DASH.search(_ft_line))
_pc = (ROOT / "CLAUDE.md").read_text()
t("CLAUDE.md non-negotiable 7 allows only per-day aggregate counts with no identifiers", "no\n   third-party analytics and no per-person tracking" in _pc
  and "no analytics; no cookies" not in _pc and "per-day aggregate totals" in _pc)

# ---- Old saved answers with missing text ------------------------------------------
# A row saved before `tldr` and `still_open` existed (the migration adds them
# as NULL), or one damaged since, can have either one empty, blank or null.
# No reader-facing path may show it as a finished verdict: the check runs
# again, or the page has no verdict on it and runs the check itself.
ratelimit.GLOBAL_MINUTE = ratelimit.SlidingWindow(100, 60)
ratelimit.GLOBAL_DAY = ratelimit.SlidingWindow(1000, 86400)
_OR_STUDIES = [_study("OR1", "Honey and cough in children: a randomised trial")]
# (label, tldr as stored, still_open as stored); None is SQL NULL, the value a
# row from before the columns existed has after the migration.
_OR_CASES = [
    ("an empty short answer", "", "Doses vary."),
    ("an empty still-open line", "Mixed.", ""),
    ("both empty", "", ""),
    ("a null short answer", None, "Doses vary."),
    ("a null still-open line", "Mixed.", None),
    ("both null, as a row from before the columns", None, None),
    ("a blank short answer", "   ", "Doses vary."),
    ("a blank still-open line", "Mixed.", "  \n "),
]


def _or_put(claim, tldr, still):
    db.put_cached_verdict(claim, "honey AND cough", "complicated", "Honey may help (Study 1).",
                          ["OR1"], _OR_STUDIES, tldr="x", still_open="x")
    con = _sqlite_b.connect(os.environ["DB_PATH"])
    con.execute("UPDATE verdict_cache SET tldr = ?, still_open = ? WHERE claim_key = ?",
                (tldr, still, db.normalize_claim(claim)))
    con.commit(); con.close()


def _or_row(claim):
    con = _sqlite_b.connect(os.environ["DB_PATH"])
    r = con.execute("SELECT tldr, still_open FROM verdict_cache WHERE claim_key = ?",
                    (db.normalize_claim(claim),)).fetchone()
    con.close()
    return r


def _or_fresh():
    fg.models.script = [Resp("honey AND cough"),
                        Resp(VJ("complicated", [1], tldr="Fresh short answer.", still_open="Fresh open line."))]
    fg.models.calls = 0


# A whole row is still served from the cache, at no cost: the fix must not
# turn every saved answer into a re-check.
_cl = "old row honey cough whole"
_or_put(_cl, "Mixed.", "Doses vary.")
fg.models.calls = 0; fg.models.script = []
_d = P(_cl, ip="9.9.9.1").get_json()
t("old rows: a row with both lines is still a cache hit, with no model call",
  _d.get("cached") is True and fg.models.calls == 0 and _d["tldr"] == "Mixed." and _d["still_open"] == "Doses vary.", _d)
_pg = c.get("/?q=" + urllib.parse.quote(_cl)).get_data(as_text=True)
t("  and its shared link still renders the verdict with both lines",
  'id="server-result"' in _pg and "Mixed." in _pg and "Doses vary." in _pg)


_or_search = pubmed.search_and_fetch
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s) for s in _OR_STUDIES]
for _i, (_lbl, _tl, _so) in enumerate(_OR_CASES):
    # The check endpoint: a hit on such a row is a miss, so it runs again and
    # replaces the row with a whole one.
    _cl = f"old row honey cough check {_i}"
    _or_put(_cl, _tl, _so)
    _or_fresh()
    _d = P(_cl, ip=f"9.7.{_i}.1").get_json()
    t(f"old rows: /api/check with {_lbl} runs the check again, never serving the row",
      _d.get("cached") is False and fg.models.calls >= 2
      and _d.get("tldr") == "Fresh short answer." and _d.get("still_open") == "Fresh open line.", _d)
    t("  and the saved row is replaced by the whole answer",
      _or_row(_cl) == ("Fresh short answer.", "Fresh open line."), _or_row(_cl))
    # The streaming check, the path the page itself uses.
    _cl = f"old row honey cough stream {_i}"
    _or_put(_cl, _tl, _so)
    _or_fresh()
    _r, _evs = stream(_cl, ip=f"9.8.{_i}.1")
    _done = [e for e in _evs if e.get("stage") == "done"]
    t(f"old rows: the streaming check with {_lbl} runs again (it searches) and ends with both lines",
      _done and any(e.get("stage") == "search" for e in _evs) and _done[0]["result"]["cached"] is False
      and _done[0]["result"]["tldr"] == "Fresh short answer."
      and _done[0]["result"]["still_open"] == "Fresh open line.", _evs[-1:])
    # The shared link, GET /?q=: no verdict is rendered from the row; the page
    # arrives without one and the browser runs the check (app.js, deep links).
    _cl = f"old row honey cough page {_i}"
    _or_put(_cl, _tl, _so)
    _pg = c.get("/?q=" + urllib.parse.quote(_cl)).get_data(as_text=True)
    t(f"old rows: GET /?q= with {_lbl} renders no verdict from the row, so the page runs the check",
      'id="server-result"' not in _pg and "data-verdict=" not in _pg and "ClaimReview" not in _pg,
      [x for x in ('id="server-result"', "data-verdict=", "ClaimReview") if x in _pg])
    # The share card: the brand card, never a stamp over an empty answer.
    _png = c.get("/og/" + urllib.parse.quote(db.normalize_claim(_cl), safe="") + ".png")
    t(f"old rows: the share card for {_lbl} is the brand card, with no verdict on it",
      _png.status_code == 200 and _png.data == og.render_brand())
pubmed.search_and_fetch = _or_search

# The device's own list of recent checks (localStorage) keeps whole results
# so it works offline. One saved by an older version, without either line, is
# not replayed as a finished verdict: tapping it runs the check instead.
_rjs = (ROOT / "static" / "app.js").read_text()
_rclick = _rjs.split('$("recent-list").addEventListener("click"')[1].split("\n});")[0]
t("old rows: a recent check on this device is replayed only when it has its short answer and still-open line",
  re.search(r"saved\.result\.tldr", _rclick) and re.search(r"saved\.result\.still_open", _rclick)
  and "renderResult(" in _rclick and "check(btn.dataset.claim)" in _rclick, _rclick)

# ---- where the slices meet: the reworded-match note --------------------------
# Sentence case is for list rows in the template only. The note under a reworded
# match shows the reader's own words exactly as typed, in both renderers, and the
# stored claim keeps its saved wording.
_RW_SAVED, _RW_TYPED = "Zinc lozenges shorten colds", "do zinc lozenges shorten colds?"
db.put_cached_verdict(_RW_SAVED, "(zinc) AND (cold)", "complicated", "Study 1 found a shorter cold.", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="Zinc may shorten a cold a little.",
                      still_open="Which dose works.")
fg.models.script = []
_rw = P(_RW_TYPED, ip="9.6.1.1").get_json()
_rw_note = _dsp.typed_note(_rw.get("typed"), _rw.get("typed_reason"))
t("meet: a reworded match carries the reader's words unchanged, not sentence-cased",
  _rw.get("claim") == _RW_SAVED and _rw.get("typed") == _RW_TYPED and _rw.get("typed_reason") == "wording"
  and _rw_note == f"Showing the saved check for this claim. You typed \u201c{_RW_TYPED}\u201d.", (_rw.get("typed"), _rw_note))
_rw_tpl = (ROOT / "templates" / "index.html").read_text()
_rw_mark = _rw_tpl[_rw_tpl.index('<p class="typed-note"'):]
_rw_mark = _rw_mark[:_rw_mark.index("</p>") + 4]
with appmod.app.test_request_context("/"):
    _rw_on = appmod.app.jinja_env.from_string(_rw_mark).render(result=_rw)
t("  the template's note prints it unchanged", _rw_on == '<p class="typed-note" id="typed-note">Showing the saved check for this claim. You typed \u201c<bdi>' + _RW_TYPED + "</bdi>\u201d.</p>"
  and re.sub(r"</?bdi>", "", _rw_on) == '<p class="typed-note" id="typed-note">' + _rw_note + "</p>", _rw_on)
if _shutil.which("node"):
    _rw_js = (ROOT / "static" / "app.js").read_text()
    _rw_fn = _rw_js[_rw_js.index("function typedNote"):_rw_js.index("function showTyped")]
    _rw_out = json.loads(_subprocess.run(["node", "-e", _rw_fn + "console.log(JSON.stringify(typedNote("
                                          + json.dumps(_rw["typed"]) + ", " + json.dumps(_rw["typed_reason"]) + ")));"],
                                         capture_output=True, text=True, check=True).stdout)
    t("  and so does the script's", _rw_out == _rw_note, _rw_out)
with db.get_conn() as _conn:
    _rw_rows = [r_["claim_text"] for r_ in _conn.execute(
        "SELECT claim_text FROM verdict_cache WHERE claim_key IN (?, ?)",
        (db.normalize_claim(_RW_SAVED), db.normalize_claim(_RW_TYPED))).fetchall()]
    _rw_log = _conn.execute("SELECT claim_text FROM checks ORDER BY id DESC LIMIT 1").fetchone()["claim_text"]
t("  and the stored text is the saved wording, with no row for the typed one",
  _rw_rows == [_RW_SAVED] and _rw_log == _RW_SAVED, (_rw_rows, _rw_log))

# The reader's words in the note are bidi-isolated and still escaped, in both
# renderers: right-to-left text, markup, quotes and very long text.
_BD = [("\u0627\u0644\u0642\u0647\u0648\u0629 \u062a\u0633\u0628\u0628 \u0627\u0644\u0633\u0631\u0637\u0627\u0646", "wording"),
       ("\u05e7\u05e4\u05d4 \u05d2\u05d5\u05e8\u05de\u05ea \u05dc\u05e1\u05e8\u05d8\u05df", "spelling"),
       ("abc \u202e reversed", "wording"), ('<script>alert(1)</script> & "quoted" \'too\'', "wording"),
       ("very long claim " * 31, "spelling")]
with appmod.app.test_request_context("/"):
    for _bt, _br in _BD:
        _bd_on = appmod.app.jinja_env.from_string(_rw_mark).render(result={"typed": _bt, "typed_reason": _br})
        _bd_p = _dsp.typed_note_parts(_bt, _br)
        t(f"bidi: the template sets the typed text in <bdi>, escaped ({_bt[:12]!r})",
          _bd_on == '<p class="typed-note" id="typed-note">' + str(_msc_escape(_bd_p[0])) + "<bdi>" + str(_msc_escape(_bt))
          + "</bdi>" + str(_msc_escape(_bd_p[2])) + "</p>" and "<script>" not in _bd_on, _bd_on)
if _shutil.which("node"):
    _bd_fn = _rw_js[_rw_js.index("function typedNoteParts"):_rw_js.index("function showTyped")]
    _bd_js = json.loads(_subprocess.run(["node", "-e", _bd_fn + "console.log(JSON.stringify(" + json.dumps(_BD)
                                         + ".map(([a, b]) => typedNoteParts(a, b))));"], capture_output=True, text=True, check=True).stdout)
    t("  and the script splits the note into the same three parts", _bd_js == [list(_dsp.typed_note_parts(a, b)) for a, b in _BD], _bd_js)
_bd_show = _rw_js[_rw_js.index("function showTyped"):_rw_js.index("function logLine")]
t("  and the script puts the typed part in a <bdi> as text, never as markup",
  'document.createElement("bdi")' in _bd_show and "bdi.textContent = parts[1]" in _bd_show
  and "note.append(parts[0], bdi, parts[2])" in _bd_show and "innerHTML" not in _bd_show)

# A reworded match reached by a link (a shared or typed address) is complete
# in the HTML before any script runs: the saved answer, its verdict, and the
# note naming the reader's words.
_ln = c.get("/?q=" + urllib.parse.quote(_RW_TYPED)).get_data(as_text=True)
_ln_res = json.loads(_ln.split('id="server-result" type="application/json"')[1].split(">", 1)[1].split("</script>")[0])
t("link: a reworded match by address is server-rendered with its verdict and the note",
  '<h1 class="claim-echo" id="claim-echo">' + _RW_SAVED + "</h1>" in _ln
  and '<p class="typed-note" id="typed-note">Showing the saved check for this claim. You typed \u201c<bdi>'
      + str(_msc_escape(_RW_TYPED)) + "</bdi>\u201d.</p>" in _ln
  and "Zinc may shorten a cold a little." in _ln and _ln_res.get("typed") == _RW_TYPED
  and _ln_res.get("typed_reason") == "wording" and _ln_res.get("claim") == _RW_SAVED, _ln[:0])
t("  its link preview and canonical name the saved wording, and its card is the saved row's",
  '<link rel="canonical" href="http://localhost/?q=Zinc%20lozenges%20shorten%20colds" />' in _ln
  and "/og/" + urllib.parse.quote(db.normalize_claim(_RW_SAVED), safe="") + ".png" in _ln,
  re.findall(r'<link rel="canonical"[^>]*>', _ln))
_ln_exact = c.get("/?q=" + urllib.parse.quote(_RW_SAVED)).get_data(as_text=True)
_ln_exact_res = json.loads(_ln_exact.split('id="server-result" type="application/json"')[1].split(">", 1)[1].split("</script>")[0])
t("  and the saved wording's own address carries no note",
  'id="typed-note" hidden></p>' in _ln_exact and _ln_exact_res.get("typed") is None
  and _ln_exact_res.get("claim") == _RW_SAVED, _ln_exact_res.get("typed"))
_ln_none = c.get("/?q=" + urllib.parse.quote("a claim nobody has saved in any words")).get_data(as_text=True)
t("  and a claim with no saved answer in any words still renders the checker, for the script to run",
  'id="server-result"' not in _ln_none and 'id="typed-note" hidden></p>' in _ln_none)

# ---- where the slices meet: a study set aside as off outcome -------------------
# rc-reading's rule sets aside a structured abstract that names the claim's
# outcome only in its background. What a reader sees for it is the same in the
# Jinja page and in renderResult: the off-topic group, and the reason line.
_MO_CLAIM = "Creatine supplements increase muscle strength in adults"
_MO_STUDIES = [dict(_HAIR["study"]),
               _study("MOK", "Creatine and strength in adults: a randomised trial",
                      abstract="BACKGROUND: Ageing brings frailty.\nMETHODS: Sixty adults took creatine.\n"
                               "RESULTS: Muscle strength rose by 8 kg.\nCONCLUSION: Creatine helped.",
                      types=["Randomized Controlled Trial"])]
_mo_search = pubmed.search_with_fallback
pubmed.search_with_fallback = lambda q, max_results=8, surrogate="": ([dict(s_) for s_ in _MO_STUDIES], q, False)
fg.models.script = [Resp("(creatine) AND (" + _hair_out + ")"),
                    Resp(VJ("true", [1, 2], tldr="Creatine raises strength a little.",
                            explanation="A trial found more strength (Study 2)."))]
try:
    _mo = P(_MO_CLAIM, ip="9.6.2.1").get_json()
finally:
    pubmed.search_with_fallback = _mo_search
_mo_hair = [s_ for s_ in _mo.get("studies") or [] if s_["pmid"] == _HAIR["study"]["pmid"]]
_MO_WHY = "Read and set aside: " + verdict.OFF_OUTCOME + "."
t("meet: a study whose abstract names the outcome only in its background is set aside as off outcome",
  _mo_hair and _mo_hair[0]["off_topic"] == verdict.OFF_OUTCOME and not _mo_hair[0]["cited_in_verdict"],
  [(s_["pmid"], s_.get("off_topic")) for s_ in _mo.get("studies") or []])
_mo_pg = c.get("/?q=" + urllib.parse.quote(_MO_CLAIM)).get_data(as_text=True)
_mo_grp = _mo_pg.split('<span class="group-name">Set aside: off topic</span>')
t("  the page puts it in 'Set aside: off topic' with the line: " + _MO_WHY,
  len(_mo_grp) == 2 and _HAIR["study"]["title"][:30] in _mo_grp[1].split("</details>")[0]
  and f'<p class="why">{_MO_WHY}</p>' in _mo_grp[1].split("</details>")[0], _mo_grp[1][:400] if len(_mo_grp) > 1 else "")
t("  and that line is true of the rule: the study measured something else, and no dash",
  "different outcome" in _MO_WHY and not any(ch in _MO_WHY for ch in "\u2013\u2014"))
if _shutil.which("node"):
    _mo_js = (ROOT / "static" / "app.js").read_text()
    _mo_fn = "const TYPES = [" + _mo_js.split("const TYPES = [")[1].split("function escapeHtml")[0]
    _mo_out = json.loads(_subprocess.run(
        ["node", "-e", _mo_fn + "const S = " + json.dumps(_mo["studies"]) + ";"
         "const g = sourceGroups(S); console.log(JSON.stringify({g, w: g.excluded.map((i) => studyWhy(S[i]))}));"],
        capture_output=True, text=True, check=True).stdout)
    t("  and renderResult groups it the same and writes the same line",
      _mo_out["g"] == _dsp.source_groups(_mo["studies"]) and _mo_out["w"] == [_MO_WHY], _mo_out)

# ---- where the slices meet: the method stamp covers the reading rules ----------
# rc-reading changed evidence.py, verdict.py and breakdown.py (and, in app.py,
# only _check_response, which runs again on every render). A change to any of
# those three files must change the stamp, so a saved answer is made again.
import shutil as _sh_ms  # noqa: E402
_ms_dir = pathlib.Path(tempfile.mkdtemp())
for _f in verdict.ANSWER_FILES:
    _sh_ms.copy(ROOT / _f, _ms_dir / _f)
_ms_file = verdict.__file__
verdict.__file__ = str(_ms_dir / "verdict.py")
try:
    _ms_same = verdict._method_stamp()
    _ms_changed = {}
    for _f in ("evidence.py", "verdict.py", "breakdown.py"):
        _orig = (_ms_dir / _f).read_bytes()
        (_ms_dir / _f).write_bytes(_orig + b"\n# changed\n")
        _ms_changed[_f] = verdict._method_stamp() != _ms_same
        (_ms_dir / _f).write_bytes(_orig)
finally:
    verdict.__file__ = _ms_file
t("meet: the method stamp is made from every answer file the reading rules changed",
  {"evidence.py", "verdict.py", "breakdown.py"} <= set(verdict.ANSWER_FILES))
t("  and a change to any one of them changes the stamp",
  _ms_same == verdict._method_stamp() and all(_ms_changed.values()), _ms_changed)

# ---- the Latest list shows each question once ---------------------------------
# Rows whose db.claim_fold keys are equal are one question; the list shows the
# newest whole row for each, under its own saved wording. A row whose key is
# empty is never merged. The wordings are the real saved pairs find_equivalent
# merges, and the three vinegar claims that are three questions.
_OQ = ["Does creatine cause hair loss", "Creatine causes hair loss",
       "Red wine is good for your heart", "Apple cider vinegar cures diabetes",
       "Red wine is good for the heart", "Apple cider vinegar cure diabetes",
       "Apple cider vinegar helps diabetes", "Apple cider vinegar causes diabetes",
       "Is it?", "Does it?"]          # oldest first; the last two fold to an empty key
with db.get_conn() as _conn:
    _oq_had = {r_["claim_key"]: dict(r_) for r_ in _conn.execute(
        "SELECT * FROM verdict_cache WHERE claim_key IN (%s)" % ",".join("?" * len(_OQ)),
        [db.normalize_claim(x) for x in _OQ]).fetchall()}
for _x in _OQ:
    db.put_cached_verdict(_x, "(x) AND (y)", "complicated", "Study 1 found a little.", ["P1"],
                          [dict(s_) for s_ in STUDIES], tldr="A little, maybe.", still_open="For how long.")
_oq_list = [r_["claim_text"] for r_ in appmod._latest(500) if r_["claim_text"] in _OQ]
t("one per question: each pair find_equivalent merges shows once, as its newest row under its own wording",
  _oq_list == ["Does it?", "Is it?", "Apple cider vinegar causes diabetes", "Apple cider vinegar helps diabetes",
               "Apple cider vinegar cure diabetes", "Red wine is good for the heart", "Creatine causes hair loss"],
  _oq_list)
t("  the three vinegar claims (cures, helps, causes) stay three",
  sum(x.startswith("Apple cider vinegar") for x in _oq_list) == 3, _oq_list)
t("  and a row whose key is empty is never merged with another",
  "Is it?" in _oq_list and "Does it?" in _oq_list and db.claim_fold("Is it?") == db.claim_fold("Does it?") == (), _oq_list)
_oq_home = c.get("/").get_data(as_text=True)
_oq_items = _oq_home.split('<ol class="ledger-list">')[1].split("</ol>")[0]
t("  the home page's list shows each once too",
  _oq_items.count("pple cider vinegar cure diabetes") == 1 and _oq_items.count("pple cider vinegar causes diabetes") == 1,
  _oq_items[:300])
t("  and suggest.group_similar is not what folds it", "group_similar" not in _inspect.getsource(appmod._latest)
  + _inspect.getsource(appmod._one_per_question))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (%s)" % ",".join("?" * len(_OQ)),
                  [db.normalize_claim(x) for x in _OQ])
    for _r in _oq_had.values():
        _conn.execute("INSERT INTO verdict_cache (%s) VALUES (%s)" % (",".join(_r), ",".join("?" * len(_r))),
                      list(_r.values()))

# ---- rows saved from a reply that could not be read ---------------------------
# Before a failed reply was refused, its fallback was saved as a row. Such rows
# stay in the database, recognised by the fallback's own short answer, and are
# never served as an answer, whatever else the row holds.
import inspect as _insp_fr  # noqa: E402
t("failed rows: the marker is the fallback's own short answer",
  db.FAILED_REPLY_TLDR == _insp_fr.signature(verdict._fallback).parameters["tldr"].default)


def _fr_put(claim, still_open=""):
    db.put_cached_verdict(claim, "(honey) AND (cough)", "complicated",
                          "The evidence-weighing step returned an unexpected format. "
                          "Raw studies are still available below for manual review.",
                          [], [dict(s_) for s_ in STUDIES], tldr=db.FAILED_REPLY_TLDR, still_open=still_open)


_fr_search = pubmed.search_and_fetch
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(s_) for s_ in STUDIES]
for _i, (_lbl, _so) in enumerate((("no still-open line", ""), ("a still-open line", "Whether it lasts."))):
    _cl = f"failed row honey cough {_i}"
    _fr_put(_cl, _so)
    t(f"failed rows: one with {_lbl} is not served from the saved table",
      db.get_cached_verdict(_cl, 24) is None and db.get_cached_by_key(db.normalize_claim(_cl)) is None
      and db.find_equivalent(_cl) is None)
    _pg = c.get("/?q=" + urllib.parse.quote(_cl)).get_data(as_text=True)
    t(f"  its shared link renders no verdict from it", 'id="server-result"' not in _pg and "ClaimReview" not in _pg)
    fg.models.script = [Resp("(honey) AND (cough)"), Resp(VJ("complicated", [1], tldr="Fresh short answer."))]
    _fr = P(_cl, ip=f"9.7.{_i}.1")
    t(f"  and a check of the claim runs again and replaces it",
      _fr.status_code == 200 and _fr.get_json()["cached"] is False and _fr.get_json()["tldr"] == "Fresh short answer."
      and db.get_cached_verdict(_cl, 24)["tldr"] == "Fresh short answer.", _fr.get_json())
pubmed.search_and_fetch = _fr_search

# Nor is such a row in any public list, count, suggestion, feed or sitemap. An
# ordinary answer is. An old real answer that lacks a newer field (its
# still-open line) is in no public list either, since no page serves it, but
# it still counts as a claim checked.
import nextsteps as _ns_fr  # noqa: E402
_FR_BAD, _FR_GOOD, _FR_OLD = ("Zanzibar clove oil heals warts", "Zanzibar honey soothes sore throats",
                              "Zanzibar ginger eases nausea")
_FR_CUR = "Ozempic cures alcoholism"   # a curated claim, so the sitemap and llms.txt would list it
_ledger0 = db.ledger()["claims"]
db.put_cached_verdict(_FR_GOOD, "(honey) AND (throat)", "true", "Because (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="It helps a little.", still_open="For how long.")
db.put_cached_verdict(_FR_OLD, "(ginger) AND (nausea)", "complicated", "Because (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="Maybe, a little.", still_open=None)
_fr_put(_FR_BAD)
with db.get_conn() as _conn:
    _fr_cur_had = _conn.execute("SELECT COUNT(*) n FROM verdict_cache WHERE claim_key = ?",
                                (db.normalize_claim(_FR_CUR),)).fetchone()["n"]
_fr_put(_FR_CUR)
for _x in (_FR_BAD, _FR_GOOD, _FR_OLD, _FR_BAD, _FR_GOOD, _FR_OLD):
    db.log_check(_x, "complicated", "x", [])
_fr_home = c.get("/").get_data(as_text=True)
_fr_404 = c.get("/no-such-page-zanzibar").get_data(as_text=True)
_fr_trend = [r_["claim"] for r_ in c.get("/api/trending").get_json()]
_fr_sugg = [r_["claim_text"] for r_ in c.get("/api/suggest?q=zanzibar").get_json()["suggestions"]]
_fr_rel = [r_["claim"] if "claim" in r_ else r_.get("claim_text") for r_ in
           _ns_fr.related_checks("Zanzibar remedies", db.all_claims())]
appmod._sitemap_memo["stamp"] = None
_fr_map = c.get("/sitemap.xml").get_data(as_text=True)
_fr_llms = c.get("/llms.txt").get_data(as_text=True)
_fr_q = urllib.parse.quote(_FR_CUR)
t("failed rows: one is not on the home page's Latest list, and an ordinary answer is",
  _FR_BAD[1:] not in _fr_home and _FR_GOOD[1:] in _fr_home)
t("failed rows: nor on the 404 page's list", _FR_BAD[1:] not in _fr_404 and _FR_GOOD[1:] in _fr_404)
t("failed rows: nor in /api/trending, where the ordinary answer stays and an old one without its still-open line does not",
  _FR_BAD not in _fr_trend and _FR_GOOD in _fr_trend and _FR_OLD not in _fr_trend, _fr_trend)
t("failed rows: nor in type-ahead (/api/suggest), where the ordinary answer stays and an old one without its still-open line does not",
  _FR_BAD not in _fr_sugg and _FR_GOOD in _fr_sugg and _FR_OLD not in _fr_sugg, _fr_sugg)
t("failed rows: nor among the related claims a dead end offers (they read the same saved claims)",
  _FR_BAD not in [r_["claim_text"] for r_ in db.all_claims()]
  and _FR_GOOD in [r_["claim_text"] for r_ in db.all_claims()] and _FR_BAD not in str(_fr_rel), _fr_rel)
t("failed rows: nor in the sitemap or llms.txt, even under a curated claim",
  _fr_q not in _fr_map and f"?q={_fr_q})" not in _fr_llms
  and all(r_["claim_text"] != _FR_CUR for r_ in db.saved_records(5000))
  and all(r_["claim_text"] != _FR_CUR for r_ in db.recent_cached(5000)))
t("failed rows: nor in the count of claims checked, which counts the two answers",
  db.ledger()["claims"] == _ledger0 + 2 - _fr_cur_had, (db.ledger()["claims"], _ledger0, _fr_cur_had))
with db.get_conn() as _conn:
    _fr_kept = _conn.execute("SELECT COUNT(*) n FROM verdict_cache WHERE tldr = ?",
                             (db.FAILED_REPLY_TLDR,)).fetchone()["n"]
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?, ?, ?)",
                  tuple(db.normalize_claim(x) for x in (_FR_BAD, _FR_GOOD, _FR_OLD, _FR_CUR)))
t("failed rows: the rows themselves stay in the database", _fr_kept >= 2, _fr_kept)
appmod._sitemap_memo["stamp"] = None

# ---- where the fixes meet: a wordless line is no line, in every list ----------
# rc-saved-fix: a line needs a letter or digit (db._whole), so a saved row whose
# short answer or still-open line is only a dash, a full stop or an invisible
# character is no answer. The release branch's lists filter failed replies in
# SQL; a wordless row is one too, so it is listed nowhere. So is an old real
# answer that lacks the newer still-open field (above, and the test below).
_WL_ROWS = (("Quokka oil heals warts", "\u2014", "Doses vary."),
            ("Quokka tea soothes throats", "It helps a little.", "\u200b"),
            ("Quokka honey eases coughs", "It may help.", "."),
            ("Ozempic cures alcoholism", "\u2014", "Doses vary."))   # curated, for the sitemap and llms.txt
_WL_GOOD = "Quokka ginger eases nausea"
with db.get_conn() as _conn:
    _wl_cur_had = _conn.execute("SELECT COUNT(*) n FROM verdict_cache WHERE claim_key = ?",
                                (db.normalize_claim(_WL_ROWS[3][0]),)).fetchone()["n"]
for _wc, _wt, _wo in _WL_ROWS:
    db.put_cached_verdict(_wc, "(quokka) AND (warts)", "true", "Because (Study 1).", ["P1"],
                          [dict(s_) for s_ in STUDIES], tldr=_wt, still_open=_wo)
db.put_cached_verdict(_WL_GOOD, "(ginger) AND (nausea)", "true", "Because (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="It helps a little.", still_open="For how long.")
for _x in [r_[0] for r_ in _WL_ROWS] + [_WL_GOOD]:
    db.log_check(_x, "true", "x", [])
    db.log_check(_x, "true", "x", [])
_WL_BAD = [r_[0] for r_ in _WL_ROWS[:3]]
t("wordless rows: none is a whole saved answer (db.whole), and the ordinary one is",
  not any(db.whole({"tldr": _wt, "still_open": _wo}) for _wc, _wt, _wo in _WL_ROWS)
  and db.whole({"tldr": "It helps a little.", "still_open": "For how long."}))
_wl_home = c.get("/").get_data(as_text=True)
_wl_404 = c.get("/no-such-page-quokka").get_data(as_text=True)
_wl_trend = [r_["claim"] for r_ in c.get("/api/trending").get_json()]
_wl_sugg = [r_["claim_text"] for r_ in c.get("/api/suggest?q=quokka").get_json()["suggestions"]]
_wl_all = [r_["claim_text"] for r_ in db.all_claims()]
_wl_rel = str(_ns_fr.related_checks("Quokka remedies", db.all_claims()))
appmod._sitemap_memo["stamp"] = None
_wl_map = c.get("/sitemap.xml").get_data(as_text=True)
_wl_llms = c.get("/llms.txt").get_data(as_text=True)
_wl_q = urllib.parse.quote(_WL_ROWS[3][0])
t("wordless rows: none is on the home page's Latest list, and the ordinary answer is",
  not any(b_[1:] in _wl_home for b_ in _WL_BAD) and _WL_GOOD[1:] in _wl_home)
t("wordless rows: nor on the 404 page's list", not any(b_[1:] in _wl_404 for b_ in _WL_BAD) and _WL_GOOD[1:] in _wl_404)
t("wordless rows: nor in /api/trending", not set(_WL_BAD) & set(_wl_trend) and _WL_GOOD in _wl_trend, _wl_trend)
t("wordless rows: nor in type-ahead (/api/suggest)", not set(_WL_BAD) & set(_wl_sugg) and _WL_GOOD in _wl_sugg, _wl_sugg)
t("wordless rows: nor among the claims related claims are drawn from",
  not set(_WL_BAD) & set(_wl_all) and _WL_GOOD in _wl_all and not any(b_ in _wl_rel for b_ in _WL_BAD), _wl_rel)
t("wordless rows: nor in the sitemap or llms.txt, even under a curated claim",
  _wl_q not in _wl_map and f"?q={_wl_q})" not in _wl_llms
  and all(r_["claim_text"] != _WL_ROWS[3][0] for r_ in db.saved_records(5000))
  and all(r_["claim_text"] != _WL_ROWS[3][0] for r_ in db.recent_cached(5000)))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?, ?, ?, ?)",
                  tuple(db.normalize_claim(x) for x in [r_[0] for r_ in _WL_ROWS] + [_WL_GOOD]))
appmod._sitemap_memo["stamp"] = None

# ---- final fix F1a: public lists show whole rows only -------------------------
# No page serves a row without its short answer or its still-open line (every
# saved-answer lookup needs db._whole), so no public list offers one: not
# type-ahead, trending, related claims, the sitemap or llms.txt. An old row
# saved before those columns existed, NULL or blank, is left out everywhere.
_OL_ROWS = (("Wombat tea soothes throats", None, "Doses vary."),
            ("Wombat honey eases coughs", "It may help.", None),
            ("Wombat oil heals warts", "  ", "Doses vary."),
            ("Ozempic cures alcoholism", None, None))   # curated, for the sitemap and llms.txt
_OL_GOOD = "Wombat ginger eases nausea"
for _oc, _ot, _oo in _OL_ROWS:
    db.put_cached_verdict(_oc, "(wombat) AND (throat)", "true", "Because (Study 1).", ["P1"],
                          [dict(s_) for s_ in STUDIES], tldr=_ot, still_open=_oo)
db.put_cached_verdict(_OL_GOOD, "(ginger) AND (nausea)", "true", "Because (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="It helps a little.", still_open="For how long.")
for _x in [r_[0] for r_ in _OL_ROWS] + [_OL_GOOD]:
    db.log_check(_x, "true", "x", [])
    db.log_check(_x, "true", "x", [])
_OL_BAD = [r_[0] for r_ in _OL_ROWS[:3]]
t("F1a: _listable is the same test as _whole on old, wordless, failed and whole rows",
  all(db._listable(r_) == db._whole(r_) for r_ in (
      {"tldr": None, "still_open": "x"}, {"tldr": "x", "still_open": None}, {"tldr": " ", "still_open": "x"},
      {"tldr": "\u2014", "still_open": "x"}, {"tldr": db.FAILED_REPLY_TLDR, "still_open": "x"},
      {"tldr": "It helps.", "still_open": "For how long."})))
_ol_trend = [r_["claim"] for r_ in c.get("/api/trending").get_json()]
_ol_sugg = [r_["claim_text"] for r_ in c.get("/api/suggest?q=wombat").get_json()["suggestions"]]
_ol_all = [r_["claim_text"] for r_ in db.all_claims()]
_ol_rel = str(_ns_fr.related_checks("Wombat remedies", db.all_claims()))
appmod._sitemap_memo["stamp"] = None
_ol_map = c.get("/sitemap.xml").get_data(as_text=True)
_ol_llms = c.get("/llms.txt").get_data(as_text=True)
_ol_q = urllib.parse.quote(_OL_ROWS[3][0])
t("F1a: an old row without a short answer or a still-open line is not in type-ahead, and a whole one is",
  not set(_OL_BAD) & set(_ol_sugg) and _OL_GOOD in _ol_sugg, _ol_sugg)
t("F1a: nor in /api/trending", not set(_OL_BAD) & set(_ol_trend) and _OL_GOOD in _ol_trend, _ol_trend)
t("F1a: nor among the claims related claims are drawn from",
  not set(_OL_BAD) & set(_ol_all) and _OL_GOOD in _ol_all and not any(b_ in _ol_rel for b_ in _OL_BAD), _ol_rel)
t("F1a: nor in the sitemap or llms.txt, even under a curated claim",
  _ol_q not in _ol_map and f"?q={_ol_q})" not in _ol_llms
  and all(r_["claim_text"] != _OL_ROWS[3][0] for r_ in db.saved_records(5000))
  and all(r_["claim_text"] != _OL_ROWS[3][0] for r_ in db.recent_cached(5000)))
t("F1a: and no page serves any of them, which is why no list offers them",
  all(db.get_cached_by_key(db.normalize_claim(b_)) is None for b_ in _OL_BAD))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?, ?, ?, ?)",
                  tuple(db.normalize_claim(x) for x in [r_[0] for r_ in _OL_ROWS] + [_OL_GOOD]))
appmod._sitemap_memo["stamp"] = None

# ---- final fix F1b: a "true" suggestion that cannot be capped is left out --------
# app._suggested reads the row behind a "true" for the low-certainty cap. When
# that read gives nothing (a race with a replace, or a key that no longer
# matches), the suggestion is left out of /api/suggest, never shown uncapped.
_SB_TRUE, _SB_MIX = "Numbat honey soothes throats", "Numbat tea eases coughs"
db.put_cached_verdict(_SB_TRUE, "(honey) AND (throat)", "true", "Because (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="It helps a little.", still_open="For how long.")
db.put_cached_verdict(_SB_MIX, "(tea) AND (cough)", "complicated", "Because (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="Maybe.", still_open="For how long.")
_sb_seen = [r_["claim_text"] for r_ in c.get("/api/suggest?q=numbat").get_json()["suggestions"]]
_sb_get = db.get_cached_by_key
db.get_cached_by_key = lambda key, *a, **k: None
try:
    _sb_race = c.get("/api/suggest?q=numbat").get_json()["suggestions"]
    _sb_one = appmod._suggested({"claim_text": _SB_TRUE, "verdict": "true"})
finally:
    db.get_cached_by_key = _sb_get
t("F1b: both suggestions show while the rows can be read", _SB_TRUE in _sb_seen and _SB_MIX in _sb_seen, _sb_seen)
t("F1b: a 'true' whose row cannot be read for the cap is left out of /api/suggest, and the others stay",
  [r_["claim_text"] for r_ in _sb_race] == [_SB_MIX] and _sb_race[0]["verdict"] == "complicated", _sb_race)
t("F1b: _suggested gives None for it, never the raw 'true'", _sb_one is None, _sb_one)
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?)",
                  (db.normalize_claim(_SB_TRUE), db.normalize_claim(_SB_MIX)))

# ---- where the fixes meet: a reworded link reads the bounded lookup -----------
# rc-site-fix renders a reworded link as the saved answer (app._equivalent);
# rc-saved-fix bounds that lookup (db.find_equivalent, db.EQUIVALENT_SCAN). A
# reworded link still renders the saved answer with its note; one whose row is
# past the newest EQUIVALENT_SCAN is an unknown claim; and a claim folding
# gives no key renders as an unknown claim, with no error.
_BL_SAVED, _BL_TYPED = "Kelp lozenges shorten colds", "Do kelp lozenges shorten colds?"
db.put_cached_verdict(_BL_SAVED, "(kelp) AND (cold)", "complicated", "Mixed (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="Kelp may shorten a cold a little.", still_open="Doses vary.")
_bl = c.get("/?q=" + urllib.parse.quote(_BL_TYPED))
_bl_pg = _bl.get_data(as_text=True)
t("meet: a reworded link, through the bounded lookup, renders the saved answer with its note",
  _bl.status_code == 200 and "Kelp may shorten a cold a little." in _bl_pg
  and "Showing the saved check for this claim. You typed \u201c<bdi>" in _bl_pg
  and '<h1 class="claim-echo" id="claim-echo">' + _BL_SAVED + "</h1>" in _bl_pg)
_bl_scan = db.EQUIVALENT_SCAN
db.EQUIVALENT_SCAN = 1
db.put_cached_verdict("Kelp newer row pushes the scan", "(kelp) AND (x)", "complicated", "Mixed (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="Something.", still_open="Doses vary.")
try:
    _bl_far = c.get("/?q=" + urllib.parse.quote(_BL_TYPED)).get_data(as_text=True)
finally:
    db.EQUIVALENT_SCAN = _bl_scan
t("  and a link whose saved row is past the newest EQUIVALENT_SCAN claims is an unknown claim, as the check finds",
  'id="server-result"' not in _bl_far and 'id="typed-note" hidden></p>' in _bl_far)
_NK_SAVED, _NK_TYPED = "H2O cures colds", "H\u2082O cures colds"
db.put_cached_verdict(_NK_SAVED, "(water) AND (cold)", "insufficient", "None (Study 1).", ["P1"],
                      [dict(s_) for s_ in STUDIES], tldr="Not tested.", still_open="Everything.")
_nk = c.get("/?q=" + urllib.parse.quote(_NK_TYPED))
_nk_pg = _nk.get_data(as_text=True)
t("meet: a link whose claim has no fold key renders as an unknown claim, with no error",
  db.claim_fold(_NK_TYPED) == () and _nk.status_code == 200 and 'id="server-result"' not in _nk_pg
  and "Not tested." not in _nk_pg and 'id="typed-note" hidden></p>' in _nk_pg, _nk.status_code)
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?, ?)",
                  tuple(db.normalize_claim(x) for x in (_BL_SAVED, "Kelp newer row pushes the scan", _NK_SAVED)))

# ---- where the fixes meet: a wordless line ends on the "either" way out --------
# rc-saved-fix: a line with no letter or digit is an empty line. rc-site-fix: a
# reply with a line missing names both ways out. A wordless short answer or
# still-open line reaches the reader with that exact message and both actions,
# on the plain check and the streamed check.
_EW_BODY = {"error": "The AI model sent back an answer with a line missing. "
                     "Try again, or try the claim in other words.", "way_out": "either"}
for _i, (_lbl, _kw) in enumerate((("short answer is a lone dash", {"tldr": "\u2014"}),
                                  ("still-open line is a zero-width space", {"still_open": "\u200b"}))):
    fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("complicated", [1], **_kw)), Resp(VJ("complicated", [1], **_kw))]
    _ew = P(f"either way out claim {_i}", ip=f"7.7.8.{_i + 1}")
    t(f"meet: a reply whose {_lbl} gets the exact 'line missing' message and both ways out",
      _ew.status_code == 502 and _ew.get_json() == _EW_BODY and _no_row(f"either way out claim {_i}"), _ew.get_json())
    fg.models.script = [Resp("(x) AND (y)"), Resp(VJ("complicated", [1], **_kw)), Resp(VJ("complicated", [1], **_kw))]
    _ew_r, _ew_evs = stream(f"either way out streamed {_i}", ip=f"7.7.8.{_i + 11}")
    t(f"  and on the streamed check ({_lbl})",
      _ew_evs[-1]["stage"] == "error" and _ew_evs[-1]["status"] == 502
      and _ew_evs[-1]["error"] == _EW_BODY["error"] and _ew_evs[-1].get("way_out") == "either"
      and _no_row(f"either way out streamed {_i}"), _ew_evs[-1])
_ew_js = (ROOT / "static" / "app.js").read_text()
t("  and the page turns 'either' into both actions, Retry this claim and Edit claim",
  re.search(r'id="error-retry"[^>]*>\s*Retry this claim', (ROOT / "templates" / "index.html").read_text())
  and 'if (wayOut === "either") return { retry: true, back: "Edit claim" };' in _ew_js)

# ---- where the fixes meet: an out-of-date saved answer, reworded and on its page ----
# rc-reading: a saved answer that relied on a study code now sets aside is out
# of date. rc-site-fix and the equivalence lookup: the same claim in other
# words reaches that saved row. Out of date is out of date whichever wording
# reaches it; and the row's own page shows the saved answer as it stands.
_OD_SAVED = "Creatine raises strength in boys"
_OD_WORDED = "Does creatine raise strength in boys?"
_od_rows = (_OD_SAVED, _OD_WORDED)
db.put_cached_verdict(_OD_SAVED, "(creatine) AND (" + _hair_out + ")", "true",
                      "Strength rose (Study 1), and hormones too (Study 2).", ["MO1", _hair["pmid"]],
                      [dict(_RES_ONLY), dict(_hair)], tldr="Creatine raises strength and hormones.",
                      still_open="Doses vary.")
t("meet: the reworded claim reaches the saved row", appmod._equivalent(_OD_WORDED) == _OD_SAVED,
  appmod._equivalent(_OD_WORDED))
_od_pg = c.get("/?q=" + urllib.parse.quote(_OD_SAVED)).get_data(as_text=True)
_od_sr = json.loads(_od_pg.split('<script id="server-result" type="application/json"')[1].split(">", 1)[1]
                    .split("</script>")[0])
_od_h = [s_ for s_ in _od_sr["studies"] if s_["pmid"] == _hair["pmid"]]
t("meet: the out-of-date row's own page shows the saved answer, as any row past its window does",
  _od_pg.count('id="server-result"') == 1 and _od_sr["claim"] == _OD_SAVED
  and _od_sr["tldr"].startswith("Creatine raises strength and hormones"), (_od_sr.get("claim"), _od_sr.get("tldr")))
t("  and it never both relies on a study and sets it aside: the study is cited, with no 'Set aside' group",
  _od_h and _od_h[0]["cited_in_verdict"] and not _od_h[0].get("off_topic")
  and "Set aside: off topic" not in _od_pg, _od_h)
_calls0 = fg.models.calls
fg.models.script = [Resp("(creatine) AND (strength)"), Resp(VJ("complicated", [1]))]
_od_p = P(_OD_WORDED, ip="7.7.7.81").get_json()
t("meet: the same claim in other words does not get that out-of-date answer: it is checked fresh",
  _od_p.get("cached") is False and fg.models.calls > _calls0 and _od_p.get("claim") == _OD_SAVED,
  (_od_p.get("cached"), fg.models.calls - _calls0, _od_p.get("claim")))
db.put_cached_verdict(_OD_SAVED, "(creatine) AND (" + _hair_out + ")", "true",
                      "Strength rose (Study 1), and hormones too (Study 2).", ["MO1", _hair["pmid"]],
                      [dict(_RES_ONLY), dict(_hair)], tldr="Creatine raises strength and hormones.",
                      still_open="Doses vary.")
_calls0 = fg.models.calls
fg.models.script = [Resp(VJ("complicated", [1]))]
_od_r, _od_evs = stream(_OD_WORDED, ip="7.7.7.82")
_od_done = [e_ for e_ in _od_evs if e_.get("stage") == "done"]
t("  nor on the streamed check",
  _od_done and _od_done[0]["result"].get("cached") is False and fg.models.calls > _calls0,
  ([e_.get("stage") for e_ in _od_evs], fg.models.calls - _calls0))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?)",
                  tuple(db.normalize_claim(x) for x in _od_rows))

# ---- where the fixes meet: an out-of-date answer whose fresh check fails -----
# A saved answer in its window that _current calls out of date is checked
# fresh. When that fresh check cannot finish, for any defined reason, the
# reader gets the saved answer exactly as before this release, never an
# error, and the failure overwrites nothing. Exact, misspelled and reworded
# lookups, on the plain and the streamed check.
import requests as _rq_sf  # noqa: E402
import ratelimit as _rl_sf  # noqa: E402
_SF = "Creatine raises strength in swimmers"
_SF_TLDR = "Creatine raises strength and hormones in swimmers."


def _sf_row():
    db.put_cached_verdict(_SF, "(creatine) AND (" + _hair_out + ")", "true",
                          "Strength rose (Study 1), and hormones too (Study 2).", ["MO1", _hair["pmid"]],
                          [dict(_RES_ONLY), dict(_hair)], tldr=_SF_TLDR, still_open="Doses vary.")
    with db.get_conn() as _conn:
        return dict(_conn.execute("SELECT * FROM verdict_cache WHERE claim_key = ?",
                                  (db.normalize_claim(_SF),)).fetchone())


def _sf_raise(exc):
    def go(*a, **k):
        raise exc
    return go


_SF_FAILS = (
    ("the provider is busy", "weigh", verdict.ProviderError("unavailable", "busy")),
    ("the provider is out of quota", "weigh", verdict.ProviderError("rate_limit", "quota")),
    ("the reply is declined", "weigh", verdict.ProviderError("other", verdict.unread_reply(verdict.DECLINED), "reword")),
    ("the reply is cut off", "weigh", verdict.ProviderError("other", verdict.unread_reply(verdict.CUT_OFF), "either")),
    ("the reply cannot be read", "weigh", verdict.ProviderError("other", verdict.unread_reply(verdict.UNREADABLE))),
    ("the reply has a line missing", "weigh", verdict.ProviderError("other", verdict.INCOMPLETE_REPLY, "either")),
    ("the provider key is missing", "weigh", RuntimeError("No AI provider key is set.")),
    ("PubMed cannot be reached", "search", _rq_sf.ConnectionError("down")),
    ("something unforeseen breaks", "weigh", ValueError("boom")),
    ("the reader's rate limit is reached", "limit", None),
)
_sf_row0 = _sf_row()
# Many checks run here: fresh global windows, before and after, as elsewhere.
ratelimit.GLOBAL_MINUTE = ratelimit.SlidingWindow(100, 60)
ratelimit.GLOBAL_DAY = ratelimit.SlidingWindow(1000, 86400)
_sf_weigh, _sf_search, _sf_limit = verdict.weigh_evidence, pubmed.search_with_fallback, _rl_sf.check
pubmed.search_with_fallback = lambda q, max_results=8, surrogate="": ([dict(_RES_ONLY), dict(_hair)], q, False)
try:
    for _i, (_lbl, _where, _exc) in enumerate(_SF_FAILS):
        if _where == "weigh":
            verdict.weigh_evidence = _sf_raise(_exc)
        elif _where == "search":
            pubmed.search_with_fallback = _sf_raise(_exc)
        else:
            _rl_sf.check = lambda key: (False, "Slow down a little.", 30)
        for _how, _typed, _script in (
                ("exact", _SF, []),
                ("misspelled", "Creatin raises strenght in swimmers",
                 [Resp("(creatine) AND (" + _hair_out + ")\nClaim: " + _SF)]),
                ("reworded", "Does creatine raise strength in swimmers?", []),
                ("misspelled and reworded", "Does creatin raise strenght in swimmers?",
                 [Resp("(creatine) AND (" + _hair_out + ")\nClaim: Does creatine raise strength in swimmers?")])):
            if _where == "limit" and _how.startswith("misspelled"):
                continue    # the limit is met before the spelling is known: nothing saved is found yet
            fg.models.script = list(_script)
            _sf = P(_typed, ip=f"7.6.{_i}.1")
            _sfj = _sf.get_json() or {}
            t(f"stale on failure: when {_lbl}, a {_how} check gets the saved answer, not an error",
              _sf.status_code == 200 and _sfj.get("cached") is True and _sfj.get("claim") == _SF
              and (_sfj.get("tldr") or "").startswith(_SF_TLDR[:-1])
              and (_sfj.get("typed") is None) == (_how == "exact"), (_sf.status_code, _sfj.get("error"), _sfj.get("typed")))
            fg.models.script = list(_script)
            _sf_r, _sf_evs = stream(_typed, ip=f"7.6.{_i}.2")
            t(f"  and the streamed {_how} check too ({_lbl})",
              _sf_evs and _sf_evs[-1]["stage"] == "done" and _sf_evs[-1]["result"]["cached"] is True
              and _sf_evs[-1]["result"]["claim"] == _SF and not any(e_["stage"] == "error" for e_ in _sf_evs),
              _sf_evs[-1] if _sf_evs else None)
        with db.get_conn() as _conn:
            _sf_now = dict(_conn.execute("SELECT * FROM verdict_cache WHERE claim_key = ?",
                                         (db.normalize_claim(_SF),)).fetchone())
        t(f"  and the failure overwrites nothing ({_lbl})",
          {k: _sf_now[k] for k in _sf_now if k != "created_at"} == {k: _sf_row0[k] for k in _sf_row0 if k != "created_at"}
          and _sf_now["created_at"] == _sf_row0["created_at"])
        verdict.weigh_evidence, pubmed.search_with_fallback, _rl_sf.check = (
            _sf_weigh, lambda q, max_results=8, surrogate="": ([dict(_RES_ONLY), dict(_hair)], q, False), _sf_limit)
    # The misspelling call site: with the model at hand, the saved wording an
    # out-of-date answer stands under is checked fresh, as on the other paths.
    _calls0 = fg.models.calls
    # (Its search was saved by the checks above, so only the verdict is asked for.)
    fg.models.script = [Resp(VJ("complicated", [1]))]
    _sf_ok = P("Creatin raises strenght in swimmers", ip="7.6.99.1").get_json()
    t("meet: a misspelled claim whose saved wording is out of date is checked fresh",
      _sf_ok.get("cached") is False and _sf_ok.get("claim") == _SF and _sf_ok.get("typed_reason") == "spelling"
      and fg.models.calls - _calls0 == 1, (_sf_ok.get("cached"), _sf_ok.get("claim"), fg.models.calls - _calls0))
    # And the second look after the spelling switch, for a reworded match.
    _sf_row()
    _calls0 = fg.models.calls
    fg.models.script = [Resp(VJ("complicated", [1]))]
    _sf_ok = P("Does creatin raise strenght in swimmers?", ip="7.6.99.2").get_json()
    t("meet: a misspelled rewording of an out-of-date saved answer is checked fresh",
      _sf_ok.get("cached") is False and _sf_ok.get("claim") == _SF and fg.models.calls - _calls0 == 1,
      (_sf_ok.get("cached"), _sf_ok.get("claim"), fg.models.calls - _calls0))
finally:
    verdict.weigh_evidence, pubmed.search_with_fallback, _rl_sf.check = _sf_weigh, _sf_search, _sf_limit
    with db.get_conn() as _conn:
        _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_SF),))
    ratelimit.GLOBAL_MINUTE = ratelimit.SlidingWindow(100, 60)
    ratelimit.GLOBAL_DAY = ratelimit.SlidingWindow(1000, 86400)
# H9: a provider 503 with no saved answer: the page and the stream say the
# service is having trouble, with the Retry, and no status number.
_h9_503 = _run_wrap(_e503(), _e503(), _e503())[1]
verdict.weigh_evidence = _sf_raise(_h9_503)
pubmed.search_with_fallback = lambda q, max_results=8, surrogate="": ([dict(_RES_ONLY), dict(_hair)], q, False)
try:
    fg.models.script = [Resp("(creatine) AND (rowing)")]
    _h9_p = P("Creatine helps rowers H9 never saved", ip="7.6.99.1")
    fg.models.script = [Resp("(creatine) AND (rowing)")]
    _h9_ev = stream("Creatine helps rowers H9 never saved", ip="7.6.99.2")[1][-1]
finally:
    verdict.weigh_evidence, pubmed.search_with_fallback = _sf_weigh, _sf_search
_h9_pj = _h9_p.get_json() or {}
t("H9: a provider 503 shows the trouble sentence and no status number, on the page and in the stream",
  _h9_503.code == 503
  and all("The AI service is having trouble." in str(x_.get("error")) and not re.search(r"\b[45]\d\d\b", str(x_.get("error")))
          for x_ in (_h9_pj, _h9_ev)) and _h9_ev.get("stage") == "error", (_h9_pj, _h9_ev))
# A GET never calls the model: the page, the card and the lists show the saved
# row as stored until a fresh check replaces it.
_sf_row()
_calls0 = fg.models.calls
_sf_pg = c.get("/?q=" + urllib.parse.quote(_SF))
_sf_og = c.get("/og/" + urllib.parse.quote(db.normalize_claim(_SF), safe="") + ".png")
c.get("/"); c.get("/api/suggest?q=creatine+swimmers"); c.get("/api/trending")
t("stale on GET: the page and the card show the saved row, and no GET calls the model",
  _sf_pg.status_code == 200 and _SF_TLDR[:-1] in _sf_pg.get_data(as_text=True) and _sf_og.status_code == 200
  and _sf_og.mimetype == "image/png" and fg.models.calls == _calls0, (_sf_pg.status_code, _sf_og.status_code))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_SF),))

# ---- where the fixes meet: a kept answer is never an out-of-date one ----------
# rc-saved-fix keeps a saved answer past its window when the stamp and every
# record are the same; rc-reading calls one that relied on a study code now
# sets aside out of date. Both hold: same records and stamp, but relied on a
# set-aside study, and it is checked fresh; listed only, and it is kept.
_KS_Q = "(creatine) AND (" + _hair_out + ")"
_KS_STUDIES = [dict(_RES_ONLY), dict(_hair)]


def _ks_row(claim, cited):
    db.put_cached_verdict(claim, _KS_Q, "true", "Strength rose (Study 1).", cited, [dict(s_) for s_ in _KS_STUDIES],
                          tldr="Kept short answer.", still_open="Doses vary.", method_stamp=verdict.METHOD_STAMP)
    with db.get_conn() as _conn:
        _conn.execute("UPDATE verdict_cache SET created_at = ? WHERE claim_key = ?",
                      (_OLD_DATE, db.normalize_claim(claim)))
    db.put_cached_query(claim, verdict.QUERY_PROMPT_VERSION, _KS_Q, "", claim)


_ks_search = pubmed.search_with_fallback
pubmed.search_with_fallback = lambda q, max_results=8, surrogate="": ([dict(s_) for s_ in _KS_STUDIES], q, False)
try:
    _KS_BAD, _KS_OK = "Creatine raises strength in rowers", "Creatine raises strength in cyclists"
    _ks_row(_KS_BAD, ["MO1", _hair["pmid"]])
    _ks_row(_KS_OK, ["MO1"])
    t("meet: both rows have the same records and stamp the search finds now",
      appmod._same_records(db.get_cached_by_key(db.normalize_claim(_KS_BAD))["studies"], _KS_STUDIES))
    _calls0 = fg.models.calls
    fg.models.script = [Resp(VJ("complicated", [1], tldr="Made fresh.", still_open="Open."))]
    _ks_b = P(_KS_BAD, ip="7.7.7.91").get_json()
    t("meet: a saved answer that relied on a study code now sets aside is never kept, same records and stamp or not",
      _ks_b.get("cached") is False and fg.models.calls > _calls0 and not (_ks_b.get("tldr") or "").startswith("Kept short answer"),
      (_ks_b.get("cached"), fg.models.calls - _calls0, _ks_b.get("tldr")))
    _ks_row(_KS_BAD, ["MO1", _hair["pmid"]])
    _calls0 = fg.models.calls
    fg.models.script = [Resp(VJ("complicated", [1], tldr="Made fresh.", still_open="Open."))]
    _ks_r, _ks_evs = stream(_KS_BAD, ip="7.7.7.92")
    t("  nor on the streamed check",
      _ks_evs[-1]["stage"] == "done" and _ks_evs[-1]["result"]["cached"] is False and fg.models.calls > _calls0,
      (_ks_evs[-1].get("stage"), fg.models.calls - _calls0))
    _calls0 = fg.models.calls
    fg.models.script = []
    _ks_o = P(_KS_OK, ip="7.7.7.93").get_json()
    t("  while one that only listed that study is kept, with no model call",
      _ks_o.get("cached") is True and (_ks_o.get("tldr") or "").startswith("Kept short answer") and fg.models.calls == _calls0,
      (_ks_o.get("cached"), _ks_o.get("tldr"), fg.models.calls - _calls0))
finally:
    pubmed.search_with_fallback = _ks_search
    with db.get_conn() as _conn:
        _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?)",
                      tuple(db.normalize_claim(x) for x in (_KS_BAD, _KS_OK)))

# ---- The evidence line sizes only an effect on the claim's own outcome ----
# Live run: "Cold showers boost immunity" read "Strong evidence of a large
# effect", where the large effect was a rise in inflammation.
_EL_ST = [_study("EL1", "Cold water immersion and health: a systematic review", types=["Systematic Review"],
                 abstract="RESULTS: Inflammation increased with an effect size of 1.03. Immune function rose with an "
                          "effect size of 1.03. There was no drop in salivary viral titer. The autism hazard ratio "
                          "was 0.93 (95% CI 0.85 to 1.02). Deaths were lower with a relative risk of 0.83. "
                          "CONCLUSIONS: Effects on immunity, autism and health were mixed.")]


def _el_bd(effect_size, effect, evidence_=()):
    return {"parts": [], "evidence": list(evidence_) or ["The review pooled 11 trials (Study 1)."],
            "effect_size": effect_size, "strength": "", "applies_to": "", "not_applies_to": "",
            "unknowns": "", "effect": effect}


def _el(claim, query, verdict_, bd, tldr="The studies point both ways."):
    with appmod.app.test_request_context("/"):
        return appmod._check_response(claim, query, verdict_, tldr, "The review pooled 11 trials (Study 1).",
                                      ["EL1"], [dict(s) for s in _EL_ST], "http://x", still_open="Whether it lasts.",
                                      breakdown=json.loads(json.dumps(bd)))["evidence_line"]


_EL_LARGE = {"figure": "standardised mean difference 1.03", "label": "large",
             "plain": "a difference of 1.03 standard deviations"}
_EL_Q = "(cold shower OR cold water immersion) AND (immune system OR immunity OR immune function)"
_el_off = _el("Cold showers boost immunity", _EL_Q, "complicated",
              _el_bd("Inflammation increased with an effect size of 1.03 (Study 1). That is a difference of 1.03 "
                     "standard deviations, a large effect (Study 1).", _EL_LARGE))
_el_on = _el("Cold showers boost immunity", _EL_Q, "complicated",
             _el_bd("Immune function rose with an effect size of 1.03 (Study 1).", _EL_LARGE))
t("effect line: a figure whose sentence names another outcome gives the evidence line no size",
  _el_off == "Strong evidence, but no plain yes or no", _el_off)
t("  and a figure whose sentence names the claim's outcome keeps it",
  _el_on == "Strong evidence of a large effect, but no plain yes or no", _el_on)
# ---- final fix F8: the effect box follows the line's size gate ---------------
# Live: cold showers showed "a large effect" for a figure about inflammation.
# When line_label withholds the size from the line, code writes no size word
# anywhere in the answer; the figure stays with its own sentence.
def _f8(claim, query, verdict_, bd):
    with appmod.app.test_request_context("/"):
        return appmod._check_response(claim, query, verdict_, "The studies point both ways.",
                                      "The review pooled 11 trials (Study 1).", ["EL1"], [dict(s) for s in _EL_ST],
                                      "http://x", still_open="Whether it lasts.", breakdown=json.loads(json.dumps(bd)))
_F8_SAID = ("Inflammation increased with an effect size of 1.03 (Study 1). That is a difference of 1.03 "
            "standard deviations, a large effect (Study 1).")
_f8_bd = dict(_el_bd(_F8_SAID, _EL_LARGE), says=["Inflammation increased with an effect size of 1.03 (Study 1).",
                                                  "That is a difference of 1.03 standard deviations, a large effect (Study 1)."])
_f8_off = _f8("Cold showers boost immunity", _EL_Q, "complicated", _f8_bd)
_f8_text = json.dumps(_f8_off["breakdown"]).lower()
t("F8: a withheld size leaves the effect label blank in the response",
  _f8_off["breakdown"]["effect"]["label"] == "" and _f8_off["breakdown"]["effect"]["figure"], _f8_off["breakdown"]["effect"])
t("F8: and the code-written size ('a large effect') is in neither the box nor the research lines",
  "large effect" not in _f8_text and "a large" not in _f8_text, _f8_off["breakdown"])
t("F8: the figure stays, with the outcome its own sentence names and code's plain arithmetic, and the box does not "
  "say no size is reported",
  _f8_off["breakdown"]["effect_size"] == "Inflammation increased with an effect size of 1.03 (Study 1). "
                                         "That is a difference of 1.03 standard deviations (Study 1)."
  and breakdown.NO_EFFECT_SIZE not in json.dumps(_f8_off), _f8_off["breakdown"]["effect_size"])
_f8_on = _f8("Cold showers boost immunity", _EL_Q, "complicated",
             _el_bd("Immune function rose with an effect size of 1.03 (Study 1). That is a difference of 1.03 "
                    "standard deviations, a large effect (Study 1).", _EL_LARGE))
t("F8: a size the line carries keeps its label and its sentence",
  _f8_on["breakdown"]["effect"]["label"] == "large" and "a large effect" in _f8_on["breakdown"]["effect_size"])
# H8: a "That is ..." sentence travels with the figure it explains, and with
# the size withheld one that only repeats the figure goes.
_H8_REP = ("Inflammation increased with an effect size of 1.03 (Study 1). Immune function rose with an effect size "
           "of 1.03 (Study 1). That is an effect size of 1.03, a large effect (Study 1).")
_h8_rep = _f8("Cold showers boost immunity", _EL_Q, "complicated",
              dict(_el_bd(_H8_REP, _EL_LARGE), says=["Inflammation increased with an effect size of 1.03 (Study 1).",
                                                     "That is an effect size of 1.03, a large effect (Study 1)."]))
t("H8: with the size withheld, a 'That is' sentence that only repeats the figure goes, box and research lines",
  _h8_rep["breakdown"]["effect"]["label"] == ""
  and _h8_rep["breakdown"]["effect_size"] == ("Inflammation increased with an effect size of 1.03 (Study 1). "
                                              "Immune function rose with an effect size of 1.03 (Study 1).")
  and _h8_rep["breakdown"]["says"] == ["Inflammation increased with an effect size of 1.03 (Study 1)."],
  (_h8_rep["breakdown"]["effect_size"], _h8_rep["breakdown"]["says"]))
t("  one that converts the figure stays (the F8 case, and arithmetic in percent)",
  "That is a difference of 1.03 standard deviations (Study 1)." in _f8_off["breakdown"]["effect_size"]
  and breakdown.translations_follow({"says": ["Risk fell with a hazard ratio of 0.46 (Study 1).",
                                              "That is about 54% lower risk over time (Study 1)."],
                                     "effect_size": "The odds ratio was 0.88 (Study 2). That is, about 12% lower odds (Study 2)."},
                                    True)["says"][1] == "That is about 54% lower risk over time (Study 1).")
_H8_NULL = "That is no clear difference between the groups, because the result was not statistically significant (Study 1)."
_h8_iv = _f8("Cold showers boost immunity", _EL_Q, "complicated",
             dict(_el_bd("Hospital stays did not fall with a relative risk of 0.81 (Study 1). " + _H8_NULL, None),
                  says=["Later trials showed no significant improvement (Study 1).", _H8_NULL,
                        "Who was studied: adults (Study 1)."]))
t("H8: a 'That is' sentence whose figure sentence is not shown before it in the same block goes",
  _H8_NULL not in _h8_iv["breakdown"]["says"] and "Who was studied: adults (Study 1)." in _h8_iv["breakdown"]["says"],
  _h8_iv["breakdown"]["says"])
_h8_side = breakdown.translations_follow({"says": ["Hospital stays did not fall, relative risk 0.81 (Study 1).", _H8_NULL],
                                          "effect_size": "The relative risk was 0.81 (Study 1). " + _H8_NULL})
t("  and beside its figure it stays",
  _h8_side["says"][1] == _H8_NULL and _h8_side["effect_size"].endswith(_H8_NULL), _h8_side)
# J3: only the "That is ..." sentences code writes are held to their figure.
_J3_MODEL = ["That is because trials used different doses (Study 2).", "That is why the evidence is mixed (Study 1).",
             "That is not what the trials found (Study 1)."]
_j3 = [breakdown.translations_follow({"says": ["Studies disagree on dose (Study 1).", x],
                                      "effect_size": "Trials disagreed (Study 1). " + x}, w)
       for x in _J3_MODEL for w in (False, True)]
t("J3: a model sentence that only starts 'That is' stays after a sentence with no figure",
  all(r_["says"][1] in _J3_MODEL and r_["effect_size"].endswith(r_["says"][1]) for r_ in _j3), _j3)
_J3_CODE = [_H8_NULL, "That is about 54% lower risk over time (Study 1).", "That is, about 12% lower odds (Study 1).",
            "That is a difference of 0.4 standard deviations (Study 1).", "That is an effect size of 1.03 (Study 1).",
            "That is a large effect (Study 1).", "That is a risk drop of 26% (Study 1)."]
t("  and every form code writes (or the faithfulness rewrite leaves) still goes after a sentence with no figure",
  all(breakdown.code_translation(x) for x in _J3_CODE) and not any(breakdown.code_translation(x) for x in _J3_MODEL)
  and all(breakdown.translations_follow({"says": ["Studies disagree on dose (Study 1).", x]})["says"]
          == ["Studies disagree on dose (Study 1)."] for x in _J3_CODE))
_f8_db = "Cold showers boost immunity F8"
db.put_cached_verdict(_f8_db, _EL_Q, "complicated", "The review pooled 11 trials (Study 1).", ["EL1"],
                      [dict(s) for s in _EL_ST], tldr="The studies point both ways.", still_open="Whether it lasts.",
                      breakdown=_f8_bd)
_f8_pg = c.get("/?q=" + urllib.parse.quote(_f8_db)).get_data(as_text=True)
t("F8: the server-rendered page shows the figure and no code-written size word",
  "Inflammation increased with an effect size of 1.03" in _f8_pg and "large effect" not in _f8_pg)
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_f8_db),))

# ---- G5: unsized handles every shape its writers give a size ----
# Live: "Smoking causes lung cancer" kept "That is a large effect (Study 3)."
# in the box with the size withheld: a faithfulness rewrite had left only
# the size in code's sentence. The shapes come from the writers: ground's
# arithmetic with ", a {label} effect", ground's arithmetic alone (the model
# had said the size), and the lead with only the size.
_G5_ST = [_study("G5A", "Cold water immersion and health: a systematic review", types=["Systematic Review"],
                 abstract="RESULTS: Inflammation was higher with a relative risk of 2.5. Inflammation was higher "
                          "with a relative risk of 1.3 in one subgroup. Inflammation was higher with a relative "
                          "risk of 1.05 in another. Immune function was not measured.")]
_g5_bds = []
for _g5_x in ("2.5", "1.3", "1.05"):
    _g5_eff = "Inflammation was higher with a relative risk of %s (Study 1)" % _g5_x
    _g5_lab = evidence.effect_size(_g5_eff)["label"]
    for _g5_tail in ("", ", a %s effect" % _g5_lab):
        _g5_g = breakdown.ground({"evidence": ["Inflammation was higher after cold water immersion in the review (Study 1)."],
                                  "says": [], "effect_size": _g5_eff + _g5_tail + ".", "parts": []},
                                 _G5_ST, verdict.tidy_prose, "Cold showers boost immunity")
        _g5_bds.append(dict(_g5_g, says=breakdown.sentences(_g5_g["effect_size"])))
    _g5_only = (_g5_eff + ". " + breakdown.TRANSLATION_LEAD + "a %s effect (Study 1)." % _g5_lab)
    _g5_bds.append(dict(_g5_bds[-2], effect_size=_g5_only, says=breakdown.sentences(_g5_only)))
_G5_SIZE = re.compile(r"\b(?:small|moderate|large)\b", re.I)
def _g5_code_sized(r):
    bd = r["breakdown"] or {}
    said = breakdown.sentences(bd.get("effect_size") or "") + list(bd.get("says") or [])
    return [x for x in said if x.startswith(breakdown.TRANSLATION_LEAD) and _G5_SIZE.search(x)]
def _g5_check(bd):
    with appmod.app.test_request_context("/"):
        return appmod._check_response("Cold showers boost immunity", _EL_Q, "complicated", "The studies point both ways.",
                                      "Inflammation was higher (Study 1).", ["G5A"], [dict(s) for s in _G5_ST], "http://x",
                                      still_open="Whether it lasts.", breakdown=json.loads(json.dumps(bd)))
_g5_out = [_g5_check(b_) for b_ in _g5_bds]
t("G5: the shapes come from the writers, each with a code size word before the gate",
  len(_g5_bds) == 9 and all(any(x.startswith(breakdown.TRANSLATION_LEAD) and _G5_SIZE.search(x)
                                for x in b_["says"]) or _G5_SIZE.search(b_["effect_size"]) for b_ in _g5_bds),
  [b_["effect_size"] for b_ in _g5_bds])
t("G5: with the size withheld, no code-written size word is left in the box or the research lines, in any shape",
  all(o["breakdown"]["effect"]["label"] == "" and not _g5_code_sized(o) for o in _g5_out),
  [_g5_code_sized(o) for o in _g5_out])
t("  nor in the line, the short answer or what is still open",
  not any(_G5_SIZE.search(" ".join([o["evidence_line"] or "", o["tldr"], o["still_open"]])) for o in _g5_out),
  [o["evidence_line"] for o in _g5_out])
t("  where the size was the whole statement the sentence goes, with its citation, and the figure's own sentence stays",
  all(o["breakdown"]["effect_size"].startswith("Inflammation was higher with a relative risk")
      and breakdown.TRANSLATION_LEAD not in o["breakdown"]["effect_size"] for o in _g5_out[2::3]),
  [o["breakdown"]["effect_size"] for o in _g5_out[2::3]])
t("  and the arithmetic stays where code wrote it beside the size",
  all("That is about " in o["breakdown"]["effect_size"] for o in _g5_out[0::3]),
  [o["breakdown"]["effect_size"] for o in _g5_out[0::3]])
# G5b: the size comes out of a "That is" sentence only where what is left is
# still whole; where words that depend on it follow, the sentence goes.
_G5B_BROKEN = "That is a large increase in risk, about 3 times the odds (Study 3)."
_g5b = lambda x: breakdown.unsized({"says": [x], "effect_size": x, "effect": {"label": "large"}})
_g5b_all = [breakdown.unsized(dict(b_, says=list(b_["says"]) + [_G5B_BROKEN])) for b_ in _g5_bds]
_g5b_said = [x for o in _g5b_all for x in breakdown.sentences(o["effect_size"] or "") + list(o["says"])]
t("G5b: a size phrase that words depend on takes its whole sentence with it, citation and all",
  _g5b(_G5B_BROKEN)["says"] == [] and _g5b(_G5B_BROKEN)["effect_size"] == "", _g5b(_G5B_BROKEN))
t("  one that ends its clause comes out and the arithmetic stays; one that is the whole statement goes",
  _g5b("That is about 12% lower odds, a small effect (Study 2).")["says"] == ["That is about 12% lower odds (Study 2)."]
  and _g5b("That is a large effect (Study 3).")["says"] == [])
t("  and no output over the writers' shapes is broken or holds a size word",
  not any(x.startswith(("That is in", "That is of", "That is ,")) for x in _g5b_said)
  and not any(x.startswith(breakdown.TRANSLATION_LEAD) and _G5_SIZE.search(x) for x in _g5b_said), _g5b_said)
_g5_db = "Cold showers boost immunity G5"
db.put_cached_verdict(_g5_db, _EL_Q, "complicated", "Inflammation was higher (Study 1).", ["G5A"],
                      [dict(s) for s in _G5_ST], tldr="The studies point both ways.", still_open="Whether it lasts.",
                      breakdown=_g5_bds[2])
_g5_pg = c.get("/?q=" + urllib.parse.quote(_g5_db)).get_data(as_text=True)
_g5_ev = stream(_g5_db, ip="9.7.5.1")[1][-1].get("result") or {}
t("G5: the server page and the stream agree: the figure, and no code-written size",
  "Inflammation was higher with a relative risk" in _g5_pg
  and not re.search(r"(?:That is|,) an? (?:small|moderate|large) effect", _g5_pg)
  and _g5_ev.get("breakdown", {}).get("effect_size") == _g5_out[2]["breakdown"]["effect_size"]
  and not _g5_code_sized(_g5_ev), (_g5_ev.get("breakdown") or {}).get("effect_size"))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_g5_db),))

# ---- final fix F9: a guidance answer shows its line whenever a study is listed --
# Live: antibiotics for a cold was a guidance answer whose only cited study was
# set aside as an opinion piece, so the line was blank.
_F9_ST = [{"pmid": "OP9", "title": "Antibiotics and the common cold: a view", "url": "https://pubmed.ncbi.nlm.nih.gov/OP9/",
           "abstract": "Antibiotics do not help colds caused by viruses.", "publication_types": ["Editorial"],
           "background": True, "background_why": "opinion piece", "year": "2020", "authors": [], "journal": "J"}]
_F9_TLDR = verdict.GUIDELINE_LEAD + "antibiotics do not treat colds. Ask a doctor about this."
with appmod.app.test_request_context("/"):
    _f9_one = appmod._check_response("Antibiotics cure a cold", "(antibiotics) AND (common cold)", "false", _F9_TLDR,
                                     "Guidance says no.", [], [dict(s) for s in _F9_ST], "http://x",
                                     still_open="Whether new drugs help.")
    _f9_none = appmod._check_response("Antibiotics cure a cold", "(antibiotics) AND (common cold)", "false", _F9_TLDR,
                                      "Guidance says no.", [], [], "http://x", still_open="Whether new drugs help.")
t("F9: a guidance answer with a study listed but none relied on shows the guidance line, not a blank",
  _f9_one["evidence_line"] == verdict.GUIDANCE_LINE, _f9_one["evidence_line"])
t("F9: with no study listed at all, the line stays blank for the page's own wording",
  _f9_none["evidence_line"] == "", _f9_none["evidence_line"])
t("F9: the guidance line has the same words in verdict, display and the script",
  verdict.GUIDANCE_LINE == _display.GUIDANCE_LINE
  and '  const GUIDANCE_LINE = "' + verdict.GUIDANCE_LINE + '";' in (ROOT / "static" / "app.js").read_text())
t("F9: the server's confidence line shows it, not 'none of the studies tests this claim'",
  _display.confidence_line(_f9_one) == verdict.GUIDANCE_LINE + ".", _display.confidence_line(_f9_one))
if _shutil.which("node"):
    _f9src = (ROOT / "static" / "app.js").read_text()
    _f9fn = ("function confidenceLine(data) {"
             + _f9src.split("function confidenceLine(data) {")[1].split("\n}\n")[0] + "\n}\n")
    _f9js = _subprocess.run(["node", "-e", _f9fn + "console.log(confidenceLine(" + json.dumps(_f9_one) + "));"],
                            capture_output=True, text=True, check=True).stdout.strip()
    t("F9: and the script's confidence line says the same", _f9js == verdict.GUIDANCE_LINE + ".", _f9js)

_EL_NONE = {"figure": "", "label": "none", "null": True, "plain": "no clear difference between the groups"}
_el_cure = _el("Bleach cures COVID-19", "(bleach) AND (COVID-19 OR SARS-CoV-2)", "false",
               _el_bd("The trial found no drop in salivary viral titer (Study 1).", _EL_NONE),
               tldr="No, it does not cure it.")
t("  and under a promise of a cure, a withheld size is not turned into an unsized effect",
  _el_cure == "Strong evidence, and none of it shows a cure", _el_cure)
_el_asd = _el("Vaccines cause autism", "(vaccine) AND (autism OR autistic disorder OR ASD)", "false",
              _el_bd("The autism hazard ratio was 0.93 (likely range 0.85 to 1.02) (Study 1).",
                     dict(_EL_NONE, figure="hazard ratio 0.93")), tldr="No, vaccines do not cause autism.")
t("  and a short name in the outcome group (ASD) does not make the outcome unreadable",
  _el_asd == "Strong evidence of no clear effect", _el_asd)
_el_gen = _el("Coffee is healthy", "(coffee) AND (health OR mortality)", "true",
              _el_bd("Deaths were lower with a relative risk of 0.83 (Study 1).",
                     {"figure": "relative risk 0.83", "label": "small", "plain": "about 17% lower risk"}),
              tldr="Yes, coffee drinkers lived longer.")
t("  and an outcome group named in general words ('health') gives no size, since any sentence meets it",
  _el_gen == "Strong evidence for this claim", _el_gen)

# ---- The evidence line sizes only a figure that moves the way the claim says ----
# Live run: "Smoking causes lung cancer" (True) read "a moderate effect", from
# a relative risk of 0.72 for heavy smokers who cut down.
_ED_ST = [_study("EL1", "Smoking and lung cancer: a systematic review", types=["Systematic Review"],
                 abstract="RESULTS: Heavy smokers who cut down had a relative risk of 0.72 for lung cancer. "
                          "Current smokers had a relative risk of 4.21 for lung cancer. Depression odds ratio "
                          "was 0.83. CONCLUSIONS: Smoking raises lung cancer risk.")]


def _ed(claim, query, effect_size, effect, verdict_="true"):
    with appmod.app.test_request_context("/"):
        return appmod._check_response(claim, query, verdict_, "Yes.", "The review found it (Study 1).",
                                      ["EL1"], [dict(s) for s in _ED_ST], "http://x", still_open="Dose.",
                                      breakdown=json.loads(json.dumps(_el_bd(effect_size, effect))))["evidence_line"]


_ED_Q = "(smoking) AND (lung cancer)"
_ed_down = _ed("Smoking causes lung cancer", _ED_Q,
               "Heavy smokers who cut down had a relative risk of 0.72 for lung cancer (Study 1).",
               {"figure": "relative risk 0.72", "label": "moderate", "plain": "about 28% lower risk"})
_ed_up = _ed("Smoking causes lung cancer", _ED_Q,
             "Current smokers had a relative risk of 4.21 for lung cancer (Study 1).",
             {"figure": "relative risk 4.21", "label": "large", "plain": "about 321% higher risk"})
t("effect direction: a claim that something raises a risk, answered True, is not sized by a figure of lower risk",
  _ed_down == "Strong evidence for this claim", _ed_down)
t("  and a figure of higher risk under it keeps its size",
  _ed_up == "Strong evidence for this claim, a large effect", _ed_up)
_ed_mirror = _ed("Exercise lowers the risk of depression", "(exercise) AND (depression)",
                 "The depression odds ratio was 0.83 (Study 1).",
                 {"figure": "odds ratio 0.83", "label": "small", "plain": "about 17% lower odds"})
_ed_mirror_up = _ed("Exercise lowers the risk of depression", "(exercise) AND (depression)",
                    "The depression odds ratio was 0.83 (Study 1).",
                    {"figure": "odds ratio 0.83", "label": "small", "plain": "about 17% higher odds"})
t("  and the mirror case: a lowers claim keeps a figure of lower odds and loses one of higher odds",
  _ed_mirror == "Strong evidence for this claim, a small effect"
  and _ed_mirror_up == "Strong evidence for this claim", (_ed_mirror, _ed_mirror_up))
t("  and a claim that says no direction ('improves'), or a figure that says none, gives no size",
  breakdown.claim_direction("Creatine improves memory") is None
  and breakdown.claim_direction("Smoking does not cause lung cancer") is None
  and breakdown.figure_direction({"plain": "a difference of 0.3 standard deviations"},
                                     "The effect size for memory was 0.3 (Study 1).") is None
  and breakdown.figure_direction({"plain": "a difference of 1.03 standard deviations"},
                                     "Inflammation increased with an effect size of 1.03 (Study 1).") == "up")

# ---- A review that calls its own evidence thin lowers the evidence line ----
# Live run: the cold water review concluded "the current evidence base is
# constrained by few RCTs, small sample sizes", and the line said "Strong".
_TH_ABS = ("RESULTS: Eleven studies were included. CONCLUSIONS: Cold water has time-dependent effects. "
           "However, the current evidence base is constrained by few RCTs, small sample sizes, and a lack "
           "of diversity in study populations.")
_TH = _study("TH1", "Cold water immersion: a systematic review", types=["Systematic Review"], abstract=_TH_ABS)
_TH_OK = _study("TH2", "Cold water immersion: a systematic review", types=["Systematic Review"],
                abstract="RESULTS: Forty large trials were included. CONCLUSIONS: The evidence is consistent.")
t("thin evidence: a relied-on review that says its evidence is held back by few or small studies lowers the line",
  evidence.evidence_line("complicated", [_TH], ["TH1"]) == "Low certainty evidence, so no plain yes or no"
  and evidence.evidence_line("true", [_TH_OK], ["TH2"]) == "Strong evidence for this claim",
  (evidence.evidence_line("complicated", [_TH], ["TH1"]), evidence.evidence_line("true", [_TH_OK], ["TH2"])))
t("  and the family is general: 'limited by the small number of included trials' and 'small sample sizes limit'",
  evidence.says_thin(dict(_TH, abstract="CONCLUSIONS: The findings are limited by the small number of included trials."))
  and evidence.says_thin(dict(_TH, abstract="CONCLUSIONS: Small sample sizes limit these results."))
  and not evidence.says_thin(dict(_TH, abstract="METHODS: Searches were limited to English. Studies were small."))
  and not evidence.says_thin(dict(_TH, abstract="CONCLUSIONS: The evidence is limited by few trials.",
                                  publication_types=["Randomized Controlled Trial"])))
t("  and it never raises a line already at very low",
  evidence.evidence_line("complicated", [dict(_TH, abstract=_TH_ABS + " Certainty of evidence was very low.")],
                         ["TH1"]).startswith("Very low certainty"),
  evidence.evidence_line("complicated", [dict(_TH, abstract=_TH_ABS + " Certainty of evidence was very low.")], ["TH1"]))

# ---- Plain words: "including" is swapped only where it brings in examples ----
# Live run: "with likely ranges like zero" (sugar and hyperactivity).
t("plain including: the verb of a range or interval stays 'including'",
  breakdown.plain("The effect size ranged from -0.14 to 0.30, with confidence intervals including zero (Study 4).")
  .endswith("including zero (Study 4).")
  and "including 1" in breakdown.plain("The 95% CI including 1 (Study 2).")
  and "including the null" in breakdown.plain("The likely ranges all including the null (Study 2).")
  and "including no effect" in breakdown.plain("The range was wide, including no effect (Study 2)."),
  breakdown.plain("with confidence intervals including zero (Study 4)."))
t("  and where it brings in examples it still reads 'like'",
  breakdown.plain("Trials including adults and children (Study 1).") == "Trials like adults and children (Study 1).",
  breakdown.plain("Trials including adults and children (Study 1)."))

# ---- Plain words: "efficacy" becomes a clause only where a clause can stand ----
# Live log: "The pooled how well it works is about 69 percent".
t("plain efficacy: after a modifier the word stays, so no sentence reads 'the pooled how well it works'",
  breakdown.plain("The pooled efficacy is about 69 percent (Study 4).") == "The pooled efficacy is about 69 percent (Study 4).",
  breakdown.plain("The pooled efficacy is about 69 percent (Study 4)."))
t("  and after a verb that asks what was measured, or at the head of a sentence, the clause still reads",
  breakdown.plain("The trial tested efficacy in adults (Study 1).") == "The trial tested how well it works in adults (Study 1)."
  and breakdown.plain("Efficacy was 69 percent (Study 1).") == "How well it works was 69 percent (Study 1)."
  and breakdown.plain("It lacked efficacy (Study 1).") == "It lacked an effect (Study 1).",
  breakdown.plain("The trial tested efficacy in adults (Study 1)."))

# ---- Plain words keep the verb's number, and name men and women ----
# Live run: "these short term immune changes lowers the risk of", "72 male people".
t("plain agreement: a plural subject keeps a plural verb after 'protect against' and 'ward off' are swapped",
  breakdown.soften("It is unknown if these changes protect against illness (Study 1).")
  == "It is unknown if these changes lower the risk of illness (Study 1)."
  and breakdown.soften("These foods ward off colds (Study 1).") == "These foods lower the risk of colds (Study 1)."
  and breakdown.soften("Vitamin C protects against colds (Study 1).") == "Vitamin C lowers the risk of colds (Study 1).",
  breakdown.soften("It is unknown if these changes protect against illness (Study 1)."))
_sx = [breakdown.plain(x) for x in (
    "A trial of 72 male participants (Study 3).",
    "72 female subjects lost weight (Study 1).",
    "Male and female participants took part (Study 1).",
    "Male or female individuals were eligible (Study 1).",
    "Male, female participants were enrolled (Study 1).",
    "It enrolled female participants of childbearing age (Study 2).",
    "It followed female participants and their babies (Study 2).",
    "It enrolled female participants (university students) (Study 2).",
    "The trial had 40 male participants aged 8 (Study 2).")]
t("  and 'male participants' reads 'males', 'female subjects' 'females', with no age the sentence did not state",
  _sx == ["A trial of 72 males (Study 3).", "72 females lost weight (Study 1).",
          "Males and females took part (Study 1).", "Males or females were eligible (Study 1).",
          "Males and females were enrolled (Study 1).",
          "It enrolled females of childbearing age (Study 2).",
          "It followed females and their babies (Study 2).",
          "It enrolled females (university students) (Study 2).",
          "The trial had 40 males aged 8 (Study 2)."]
  and not any(re.search(r"\b(?:men|women|boys|girls)\b", x, re.I) for x in _sx)
  and not hasattr(breakdown, "_YOUNG"), _sx)

# ---- where the fixes meet: the evidence line over two readings, made the new way ----
# rc-reading builds the line over the studies used and with the set-aside ones
# put back, and shows the less sure. rc-guidance gives the line only a size
# about the claim itself (line_label, withheld), lowers it for a thin review
# (evidence.says_thin), and under a guidance-set answer shows GUIDANCE_LINE.
# Joined: each reading is built the new way, the less sure one shows, and the
# guidance line replaces it last.
_TR_REV = _study("TR1", "Creatine supplementation and muscle strength: a systematic review and meta-analysis",
                 types=["Systematic Review", "Meta-Analysis"],
                 abstract="BACKGROUND: Strength matters.\nMETHODS: We pooled 40 randomized trials with 3,000 adults.\n"
                          "RESULTS: Muscle strength rose with creatine (moderate certainty evidence).\n"
                          "CONCLUSIONS: Creatine raises muscle strength.")
_TR_THIN = _study("TR2", "Creatine and hair loss: a systematic review", types=["Systematic Review"],
                  abstract="BACKGROUND: Creatine is taken to raise muscle strength.\nMETHODS: We pooled trials of "
                           "creatine and hair.\nRESULTS: Hair density did not change. The evidence is limited by the "
                           "small number of included trials.\nCONCLUSIONS: No hair loss was seen.")
_TR_THIN["off_topic"] = verdict.OFF_OUTCOME
_TR_Q, _TR_C = "(creatine) AND (" + _hair_out + ")", "Creatine raises muscle strength"


def _tr_line(studies, tldr="Creatine raises strength.", verdict_value="true", bd=None, claim=_TR_C):
    with appmod.app.test_request_context("/"):
        return appmod._check_response(claim, _TR_Q, verdict_value, tldr, "Strength rose (Study 1).", ["TR1"],
                                      [dict(s_) for s_ in studies], "http://localhost",
                                      still_open="Doses vary.", breakdown=bd)["evidence_line"]


t("meet: the thin review is one code sets aside, and it says its evidence is thin",
  evidence.measured_outcome(_TR_THIN, _hair_out, "", "(creatine)") is False and evidence.says_thin(_TR_THIN))
t("meet: a thin review among the put-back studies lowers the line shown",
  _tr_line([_TR_REV]).startswith("Moderate certainty") and _tr_line([_TR_REV, _TR_THIN]).startswith("Low certainty"),
  (_tr_line([_TR_REV]), _tr_line([_TR_REV, _TR_THIN])))
_TR_BD = {"effect": {"label": "large", "effect_size": "Inflammation rose with an effect size of 1.03 (Study 1)."}}
t("meet: a size about another outcome is withheld in both readings, and the less sure line still shows",
  "large" not in _tr_line([_TR_REV], bd=_TR_BD) and "large" not in _tr_line([_TR_REV, _TR_THIN], bd=_TR_BD)
  and _tr_line([_TR_REV, _TR_THIN], bd=_TR_BD).startswith("Low certainty"),
  (_tr_line([_TR_REV], bd=_TR_BD), _tr_line([_TR_REV, _TR_THIN], bd=_TR_BD)))
_TR_G = verdict.GUIDELINE_LEAD + "creatine is fine for most adults."
# A guidance-set answer is one on a claim guidance is read for (H3): the
# lead on "Creatine raises muscle strength" is no guidance answer.
_TR_GC = "Creatine supplements are safe in pregnancy"
t("meet: a guidance-set answer shows the fixed line whatever either reading says",
  _tr_line([_TR_REV], tldr=_TR_G, verdict_value="false", claim=_TR_GC) == verdict.GUIDANCE_LINE
  and _tr_line([_TR_REV, _TR_THIN], tldr=_TR_G, verdict_value="false", claim=_TR_GC) == verdict.GUIDANCE_LINE
  and _tr_line([_TR_REV, _TR_THIN], tldr=_TR_G, verdict_value="false") != verdict.GUIDANCE_LINE,
  (_tr_line([_TR_REV, _TR_THIN], tldr=_TR_G, verdict_value="false", claim=_TR_GC),))

# ---- where the fixes meet: 'no clear effect' needs both rules ---------------
# rc-reading round 3: a sentence with a significant figure is never a null
# (breakdown.all_null). rc-guidance: the line carries a label, "none" included,
# only when the figure's own sentence names the claim's outcome (line_label).
# Each case below gets past one rule alone; the page says "no clear effect"
# only for the one that passes both.
def _nc_line(effect):
    with appmod.app.test_request_context("/"):
        r_ = verdict._read_verdict(json.dumps({
            "verdict": "complicated", "tldr": "The diet is linked to weight change, but only in an observational study.",
            "explanation": "Adults on the diet were followed (Study 1).", "still_open": "Whether it causes it.",
            "cited_study_numbers": [1], "relevance": [{"study": 1, "subject": True, "outcome": True, "in_people": True,
                                                       "finding": "for"}],
            "parts": [], "evidence": ["Adults on the diet were followed for 5 years (Study 1)."], "effect_size": effect,
            "strength": "", "applies_to": "", "not_applies_to": "", "unknowns": ""}), None, [dict(x) for x in _WT_ST],
            "This diet helps weight loss", subject="(diet)", outcome="(weight loss OR body weight)", surrogate="")
        return appmod._check_response("This diet helps weight loss", "(diet) AND (weight loss OR body weight)",
                                      r_["verdict"], r_["tldr"], r_["explanation"], r_["cited_studies"],
                                      [dict(x) for x in _WT_ST], "http://x", still_open="Whether it causes it.",
                                      breakdown=r_.get("breakdown")).get("evidence_line") or ""


_NC_SIG = "Body weight showed no clear difference, while adults lost 4.1 kg more (p < 0.001) (Study 1)."
_NC_OFF = "Waist size changed by 0.4 cm, which was not statistically significant (Study 1)."
_NC_BOTH = "Body weight changed by 0.4 kg, which was not statistically significant (Study 1)."
t("meet: a null beside a significant figure names the outcome (line_label alone would pass it), all_null refuses it",
  breakdown.names_outcome(_NC_SIG, "(weight loss OR body weight)") and not breakdown.all_null(_NC_SIG, _WT_ST))
t("  a null about another outcome is all null (all_null alone would pass it), line_label refuses it",
  breakdown.all_null(_NC_OFF, _WT_ST) and not breakdown.names_outcome(_NC_OFF, "(weight loss OR body weight)"))
t("  and on the page neither says 'no clear effect', while a null on the claim's own outcome does",
  "no clear effect" not in _nc_line(_NC_SIG) and "no clear effect" not in _nc_line(_NC_OFF)
  and "no clear effect" in _nc_line(_NC_BOTH), (_nc_line(_NC_SIG), _nc_line(_NC_OFF), _nc_line(_NC_BOTH)))
# Round 3 skips a number that cannot be a ratio when it reads a figure; the
# evidence line finds the figure's sentence through the same reader. They
# agree on which sentence the figure came from, a heart rate beside it or not.
_FS = {"effect_size": "Mean HR was 72 per minute (Study 1). The hazard ratio for death was 0.80 (Study 1).",
       "evidence": [], "parts": [], "effect": {"label": "small", "figure": "hazard ratio 0.80"}}
_fs_sent = breakdown.figure_sentence(_FS)
t("meet: figure_sentence and effect_size agree on the sentence a figure came from, past a number that is no ratio",
  evidence.effect_size("Mean HR was 72 per minute (Study 1).") is None
  and _fs_sent.startswith("The hazard ratio for death was 0.80")
  and (evidence.effect_size(_fs_sent) or {}).get("figure") == (evidence.effect_size(_FS["effect_size"]) or {}).get("figure"),
  (_fs_sent, evidence.effect_size(_FS["effect_size"])))

# ---- L1: a retracted paper, or a journal notice, is never relied on ----------
_RT_ST = [_study("RT1", "Vinegar and blood sugar in adults with diabetes: a randomized trial",
                 types=["Journal Article", "Randomized Controlled Trial", "Retracted Publication"],
                 abstract="Adults with type 2 diabetes were randomized to vinegar or placebo for 12 weeks."),
          _study("RT2", "Vinegar and fasting glucose: a randomized trial",
                 abstract="Adults with type 2 diabetes were randomized to vinegar or placebo for 8 weeks."),
          _study("RT3", "Retraction notice: vinegar and blood sugar", types=["Retraction of Publication"],
                 abstract="This article has been retracted at the request of the authors.")]
_rt_search = pubmed.search_and_fetch
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(x) for x in _RT_ST]
t("L1: a retracted paper and a retraction notice are background for any claim, with the reason in plain words",
  evidence.background_reasons(_RT_ST, "Vinegar lowers blood sugar")
  == {"RT1": evidence.BG_RETRACTED, "RT3": evidence.BG_NOTICE}
  and evidence.background_reasons(_RT_ST, "Vinegar lowers blood sugar in rats")["RT1"] == evidence.BG_RETRACTED)
fg.models.script = [Resp("(vinegar) AND (blood sugar)"), Resp(VJ("true", [1, 2, 3]))]
_rt = P("Retracted vinegar lowers blood sugar", ip="7.7.31.1").get_json()
_rt_by = {s["pmid"]: s for s in _rt.get("studies") or []}
t("  a fresh check never counts the retracted paper or the notice as used, whatever the model cites",
  [p for p, s in _rt_by.items() if s["cited_in_verdict"]] == ["RT2"]
  and _rt_by["RT1"]["background"] and _rt_by["RT1"]["background_why"] == "retracted by the journal"
  and _rt_by["RT3"]["background"], _rt_by)
t("  and the study list says so in plain words",
  _display.study_why(_rt_by["RT1"]) == "Background only, not counted: retracted by the journal.")
t("  and the list of what each study concluded leaves it out",
  "RT1" not in json.dumps(((_rt.get("breakdown") or {}).get("concluded")) or []), _rt.get("breakdown"))
fg.models.script = [Resp("(vinegar) AND (blood sugar)"), Resp(VJ("true", [1, 2]))]
_r, _evs = stream("Retracted vinegar lowers blood sugar streamed", ip="7.7.31.2")
_rts = [s for s in _evs[-1].get("result", {}).get("studies", []) if s["cited_in_verdict"]]
_rtb = {s["pmid"]: s for s in _evs[-1].get("result", {}).get("studies", [])}
t("  and a streamed check the same: its final event sets the cited retracted paper aside, with the reason",
  _evs[-1]["stage"] == "done" and [s["pmid"] for s in _rts] == ["RT2"]
  and _rtb["RT1"]["background"] and _rtb["RT1"]["background_why"] == "retracted by the journal"
  and not _rtb["RT1"]["cited_in_verdict"], _evs[-1])
# A saved row from before the rule, citing the retracted paper.
_rt_old = [dict(x) for x in _RT_ST]
db.put_cached_verdict("Retracted vinegar saved row", "(vinegar) AND (blood sugar)", "true",
                      "Both trials found it (Study 1) (Study 2).", ["RT1", "RT2"], _rt_old,
                      tldr="It lowers blood sugar.", still_open="Longer trials.")
_rt_row = db.get_cached_verdict("Retracted vinegar saved row", 24)
t("  a saved answer that cites it is out of date, as one citing any study now set aside",
  _rt_row and appmod._current(_rt_row, "Retracted vinegar saved row") is None)
_rt_pg = appmod._check_response("Retracted vinegar saved row", _rt_row["search_query"], _rt_row["verdict"],
                                _rt_row["tldr"], _rt_row["explanation"], _rt_row["cited_studies"],
                                _rt_row["studies"], "http://x", still_open="Longer trials.")
t("  and if it is ever served as saved, the page still sets the paper aside and no sentence leans on it alone",
  [s["pmid"] for s in _rt_pg["studies"] if s["cited_in_verdict"]] == ["RT2"]
  and next(s for s in _rt_pg["studies"] if s["pmid"] == "RT1")["background"]
  and "(Study 1)" not in _rt_pg["explanation"], _rt_pg["explanation"])
t("  the recent list and the card read the same set",
  appmod._listed({"claim_text": "x", "verdict": "true", "cited": ["RT1"], "studies": _rt_old,
                  "search_query": "(vinegar) AND (blood sugar)"})["verdict"] == "insufficient"
  and (og.strongest_cited(_rt_old, _display.relied_pmids(["RT1", "RT2"], _rt_old)) or ("",))[0] != ""
  and _display.relied_pmids(["RT1", "RT2"], _rt_old) == {"RT2"})
_rt_html = c.get("/?q=" + urllib.parse.quote("Retracted vinegar lowers blood sugar")).data.decode()
t("  the server-rendered page shows it set aside with the reason",
  "Background only, not counted: retracted by the journal." in _rt_html)
_rt_v = verdict._format_study_for_prompt(1, dict(_RT_ST[0]), claim="Vinegar lowers blood sugar")
t("  the model is told the journal retracted it and not to cite it",
  "BACKGROUND ONLY: the journal retracted this paper" in _rt_v and "Do not cite it" in _rt_v, _rt_v)
# H1: no sentence a reader sees as reasoning cites a study the page shows as
# set aside (withdrawn, a journal notice, or off topic), on any path.
_H1_A = {"retracted": _study("HA1", "Turmeric for knee pain: a randomized trial",
                             types=["Journal Article", "Randomized Controlled Trial", "Retracted Publication"],
                             abstract="Knee pain fell with turmeric over 8 weeks."),
         "notice": _study("HA1", "Retraction notice: turmeric for knee pain", types=["Retraction of Publication"],
                          abstract="Knee pain fell with turmeric over 8 weeks. This article has been retracted."),
         "off topic": _study("HA1", "Turmeric and knee pain in a trial", types=["Randomized Controlled Trial"],
                             abstract="Knee pain fell with turmeric over 8 weeks.")}
_H1_B = _study("HB2", "Turmeric and knee pain: a second randomized trial", types=["Randomized Controlled Trial"],
               abstract="Pain scores fell with turmeric in a second trial of adults with knee pain.")
_H1_SAID_A = "Knee pain fell with turmeric (Study 1)."
_H1_SAID_B = "Pain scores fell with turmeric in a second trial (Study 2)."
# J1: in every cell, a sentence citing the set-aside study and the other one
# goes whole: its words were written about both, so none is re-credited.
_H1_MIX = "Both trials enrolled adults with knee pain (Studies 1, 2)."
_H1_OFF = [{"study": 1, "subject": False, "outcome": True, "in_people": True, "finding": "unclear"},
           {"study": 2, "subject": True, "outcome": True, "in_people": True, "finding": "for"}]
def _h1_seen(r):
    return [r["explanation"] or "", r["tldr"] or ""] + breakdown.prose_of(r.get("breakdown"))
_h1_bad, _h1_lost, _h1_n = [], [], 0
for _h1_kind, _h1_a in _H1_A.items():
    for _h1_listed in (True, False):
        for _h1_cites in (True, False):
            for _h1_other in (True, False):
                _nums = ([1] if _h1_listed else []) + ([2] if _h1_other else [])
                _expl = " ".join(([_H1_SAID_A] if _h1_cites else []) + ([_H1_SAID_B] if _h1_other else []) + [_H1_MIX])
                _extra = {"relevance": _H1_OFF} if _h1_kind == "off topic" else {}
                for _h1_way in ("plain", "streamed", "saved"):
                    _h1_n += 1
                    _cl = "Turmeric eases knee pain H1 %d" % _h1_n
                    pubmed.search_and_fetch = lambda q, max_results=8, surrogate='', _a=_h1_a: [dict(_a), dict(_H1_B)]
                    if _h1_way == "saved":
                        _st = [dict(_h1_a, **({"off_topic": verdict.OFF_SUBJECT + "."} if _h1_kind == "off topic" else {})),
                               dict(_H1_B)]
                        with appmod.app.test_request_context("/"):
                            _h1_r = appmod._check_response(_cl, "(turmeric) AND (knee pain)", "true", "Turmeric eases knee pain.",
                                                           _expl, [x["pmid"] for x, n_ in zip(_st, (1, 2)) if n_ in _nums],
                                                           _st, "http://x", still_open="Longer trials.",
                                                           breakdown=dict(_el_bd(_expl, None, [_expl]), says=breakdown.sentences(_expl)))
                    else:
                        fg.models.script = [Resp("(turmeric) AND (knee pain)"),
                                            Resp(VJ("true", _nums, tldr="Turmeric eases knee pain.", explanation=_expl, **_extra))]
                        if _h1_way == "plain":
                            _h1_r = P(_cl, ip="7.9.%d.%d" % (_h1_n // 200, _h1_n % 200 + 1)).get_json()
                        else:
                            _h1_r = stream(_cl, ip="7.8.%d.%d" % (_h1_n // 200, _h1_n % 200 + 1))[1][-1].get("result") or {}
                    _cell = (_h1_kind, _h1_listed, _h1_cites, _h1_other, _h1_way)
                    if not _h1_r.get("studies"):
                        _h1_bad.append((_cell, "no result", _h1_r)); continue
                    _sa = next(s for s in _h1_r["studies"] if s["pmid"] == "HA1")
                    _num = [s["pmid"] for s in _h1_r["studies"]].index("HA1") + 1
                    if _sa["cited_in_verdict"] or any(_num in breakdown.refs(x, len(_h1_r["studies"])) for x in _h1_seen(_h1_r)):
                        _h1_bad.append((_cell, [x for x in _h1_seen(_h1_r) if _num in breakdown.refs(x, len(_h1_r["studies"]))]))
                    if any("Both trials enrolled adults" in x for x in _h1_seen(_h1_r)):
                        _h1_bad.append((_cell, "mixed sentence kept", [x for x in _h1_seen(_h1_r) if "Both trials" in x]))
                    if _h1_other and "Pain scores fell with turmeric in a second trial" not in _h1_r["explanation"]:
                        _h1_lost.append((_cell, _h1_r["explanation"]))
                    if not _h1_other and _h1_r["verdict"] != "insufficient":
                        _h1_bad.append((_cell, "stamp", _h1_r["verdict"]))
t("H1: in every cell no prose field cites the set-aside study, a sentence citing it with another goes whole, "
  "and with nothing else relied on the stamp is insufficient",
  _h1_n == 72 and not _h1_bad, _h1_bad[:6])
t("  and a sentence that cites only a relied-on study is still there", not _h1_lost, _h1_lost[:6])
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(_H1_A["retracted"])]
fg.models.script = [Resp("(turmeric) AND (knee pain)"), Resp(VJ("true", [1], tldr="Turmeric eases knee pain.",
                                                                explanation=_H1_SAID_A))]
_h1_rx = P("Turmeric eases knee pain, the reviewer's case", ip="7.9.9.1").get_json()
fg.models.script = [Resp("(turmeric) AND (knee pain)"), Resp(VJ("true", [1], tldr="Turmeric eases knee pain.",
                                                                explanation=_H1_SAID_A))]
_h1_rxs = stream("Turmeric eases knee pain, the reviewer's case streamed", ip="7.9.9.2")[1][-1].get("result") or {}
t("H1: the reviewer's case, plain and streamed: the sentence is gone, the paper set aside, the stamp insufficient",
  all(r_["verdict"] == "insufficient" and _H1_SAID_A[:-10] not in json.dumps(r_.get("explanation"))
      and r_["studies"][0]["background"] and not r_["studies"][0]["cited_in_verdict"] for r_ in (_h1_rx, _h1_rxs)),
  [(r_.get("verdict"), r_.get("explanation")) for r_ in (_h1_rx, _h1_rxs)])
# J1, the reviewer's case: a mixed citation in "Who was studied" under a
# saved answer. Off-topic Study 1's volunteers are not credited to Study 2.
_J1_WHO = "Who was studied: lab models and 48 volunteers with knee pain (Studies 1, 2)."
with appmod.app.test_request_context("/"):
    _j1 = appmod._check_response("Turmeric eases knee pain J1", "(turmeric) AND (knee pain)", "complicated",
                                 "The studies point both ways.", _H1_SAID_B + " Both were small (Studies 1, 2).",
                                 ["HB2"], [dict(_H1_A["off topic"], off_topic=verdict.OFF_SUBJECT + "."), dict(_H1_B)],
                                 "http://x", still_open="Longer trials.",
                                 breakdown=dict(_el_bd("", None, [_H1_SAID_B]), says=[_H1_SAID_B, _J1_WHO]))
t("J1: the reviewer's case: a sentence citing an off-topic and a relied-on study goes whole, not re-credited",
  _H1_SAID_B in _j1["breakdown"]["says"] and not any("48 volunteers" in x for x in breakdown.prose_of(_j1["breakdown"]))
  and "Both were small" not in _j1["explanation"] and _j1["explanation"].startswith("Pain scores fell"),
  (_j1["breakdown"]["says"], _j1["explanation"]))
t("J1: drop_any_citing returns text with no such sentence byte for byte, and drops a mixed one whole",
  breakdown.drop_any_citing("One.  Two\n(Study 2).", {1}, 3) == "One.  Two\n(Study 2)."
  and breakdown.drop_any_citing("Kept (Study 2). Gone (Studies 1, 2).", {1}, 3) == "Kept (Study 2)."
  and breakdown.drop_any_citing("Gone (Study 1).", {1}, 3) == "")
_H1_PLAIN = "Nothing here tests the claim in people. A second trial is under way."
with appmod.app.test_request_context("/"):
    _h1_same = appmod._check_response("Turmeric eases knee pain H1 same", "(turmeric) AND (knee pain)", "insufficient",
                                      "Unproven either way.", _H1_PLAIN, [], [dict(_H1_A["retracted"]), dict(_H1_B)],
                                      "http://x", still_open="Longer trials.")
t("H1: with nothing relied on and no set-aside study cited, the explanation comes back unchanged",
  _h1_same["explanation"] == _H1_PLAIN, _h1_same["explanation"])
# H7: a withdrawn paper also read as off topic is shown, and counted once,
# as withdrawn. Live: a "Retracted Publication" shown as "tests something
# other than what this claim is about".
def _h7_ok(r):
    s_ = next((x for x in r.get("studies") or [] if x["pmid"] == "HA1"), None)
    return (s_ is not None and not s_["off_topic"] and s_["background"] and not s_["cited_in_verdict"]
            and s_["background_why"] == evidence.BG_RETRACTED
            and _display.study_why(s_) == "Background only, not counted: retracted by the journal."
            and r["evidence"]["off_topic"] == 0 and r["evidence"]["background"] == 1
            and evidence.BG_RETRACTED in r["evidence"]["background_kinds"])
_h7 = []
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(_H1_A["retracted"]), dict(_H1_B)]
for _h7_i, _h7_way in enumerate(("plain", "streamed")):
    fg.models.script = [Resp("(turmeric) AND (knee pain)"),
                        Resp(VJ("true", [2], tldr="Turmeric eases knee pain.", explanation=_H1_SAID_B, relevance=_H1_OFF))]
    _cl = "Turmeric eases knee pain H7 %s" % _h7_way
    _h7.append(P(_cl, ip="7.9.8.%d" % (_h7_i + 1)).get_json() if _h7_way == "plain"
               else stream(_cl, ip="7.9.8.%d" % (_h7_i + 3))[1][-1].get("result") or {})
with appmod.app.test_request_context("/"):
    _h7.append(appmod._check_response("Turmeric eases knee pain H7 saved", "(turmeric) AND (knee pain)", "true",
                                      "Turmeric eases knee pain.", _H1_SAID_B, ["HB2"],
                                      [dict(_H1_A["retracted"], off_topic=verdict.OFF_SUBJECT + "."), dict(_H1_B)],
                                      "http://x", still_open="Longer trials."))
_h7_html = c.get("/?q=" + urllib.parse.quote("Turmeric eases knee pain H7 plain")).get_data(as_text=True)
pubmed.search_and_fetch = _rt_search
t("H7: a paper both off topic and retracted shows the retracted reason, plain, streamed and saved, counted once",
  len(_h7) == 3 and all(_h7_ok(r_) for r_ in _h7),
  [next((x for x in r_.get("studies") or [] if x["pmid"] == "HA1"), None) and
   (next(x for x in r_["studies"] if x["pmid"] == "HA1")["off_topic"], r_["evidence"]["off_topic"]) for r_ in _h7])
t("  and the server page says retracted, not off topic, for it",
  "Background only, not counted: retracted by the journal." in _h7_html
  and "Read and set aside: " + verdict.OFF_SUBJECT.rstrip(".") not in _h7_html)
pubmed.search_and_fetch = _rt_search

# ---- where the rounds meet: final fixes and rc-live-fix --------------------------
# F8 (a withheld size is said nowhere by code) meets L4 (a false answer to a
# cure claim with no size says the evidence does not show the promise).
_mc = _f8("Cold showers cure the common cold", "(cold shower) AND (common cold)", "false", _f8_bd)
_mc_text = json.dumps(_mc).lower()
t("meet F8 and L4: a false answer to a cure claim whose size is withheld shows the cure line and no code size word",
  "none of it shows a cure" in _mc["evidence_line"] and _mc["breakdown"]["effect"]["label"] == ""
  and not re.search(r"\b(?:small|moderate|large) effect", _mc_text), (_mc["evidence_line"], _mc["breakdown"]))
# F9 (the guidance line whenever a study is listed) meets L1 (a retracted
# paper is background): the only cited study is retracted.
with appmod.app.test_request_context("/"):
    _mg = appmod._check_response("Stop taking insulin and treat diabetes with vinegar", "(vinegar) AND (diabetes)",
                                 "false", _F9_TLDR,
                                 "Guidance says no (Study 1).", ["RT1"], [dict(_RT_ST[0])], "http://x",
                                 still_open="Whether it helps at all.")
_mg_s = _mg["studies"][0]
t("meet F9 and L1: a guidance answer whose only cited study is retracted shows the guidance line",
  _mg["evidence_line"] == verdict.GUIDANCE_LINE and _display.confidence_line(_mg) == verdict.GUIDANCE_LINE + ".",
  _mg["evidence_line"])
t("  and the paper is shown as retracted, not used",
  not _mg_s["cited_in_verdict"] and _mg_s["background"] and _mg_s["tier"] == "retracted"
  and _display.study_why(_mg_s) == "Background only, not counted: retracted by the journal.", _mg_s)
# G2: with every citation set aside, the saved answer follows the fresh
# check's rule (verdict.guidance_may_rate): guidance alone sets False on a
# forgo-treatment claim, and never "safe" for a pregnancy; a general
# guideline-topic claim stays "Not enough evidence" with the guidance
# sentence after the untested takeaway.
_G2_OT = dict(_study("OT1", "Sun exposure habits in office workers", types=["Observational Study"]),
              off_topic="tests something other than what this claim is about.")
_G2_CASES = [("Stop taking insulin and treat diabetes with vinegar", "false",
              "people with type one diabetes need insulin.", verdict.FORGO_DOCTOR, dict(_RT_ST[0])),
             ("Ibuprofen is safe during pregnancy", "true",
              "ibuprofen is safe to take in pregnancy.", verdict.PREGNANCY_DOCTOR, dict(_RT_ST[0])),
             ("Sunscreen prevents skin cancer", "true",
              "daily sunscreen use lowers the risk of skin cancer.", "", _G2_OT)]
_g2 = {}
for _g2_i, (_g2_c, _g2_v, _g2_says, _g2_doc, _g2_st) in enumerate(_G2_CASES):
    with appmod.app.test_request_context("/"):
        _g2_saved = appmod._check_response(
            _g2_c, "(x) AND (y)", _g2_v, (verdict.GUIDELINE_LEAD + _g2_says + " " + _g2_doc).strip(),
            "Guidance says so (Study 1).", [_g2_st["pmid"]], [dict(_g2_st)], "http://x",
            still_open="Whether a trial would settle it.")
    verdict.guideline_view = _guide(_g2_v, _g2_says)
    with db.get_conn() as _conn:
        _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_g2_c),))
        _conn.execute("DELETE FROM query_cache")
    fg.models.script = [Resp("q"), Resp(VJ(_g2_v, [], tldr="It works."))]
    _g2_fresh = P(_g2_c, ip="9.7.2.%d" % (_g2_i + 1)).get_json()
    verdict.guideline_view = lambda claim: None
    _g2[_g2_c] = (_g2_saved, _g2_fresh)
_g2_f = _g2[_G2_CASES[0][0]][0]
t("G2: a guidance False on a forgo-treatment claim whose only citation is retracted keeps False and the guidance line",
  _g2_f["verdict"] == "false" and _g2_f["evidence_line"] == verdict.GUIDANCE_LINE, (_g2_f["verdict"], _g2_f["evidence_line"]))
for _g2_c, _g2_label in ((_G2_CASES[1][0], "a pregnancy claim that says safe, set True by guidance, its only citation retracted"),
                         (_G2_CASES[2][0], "a protective-practice claim set True by guidance, its only citation set aside")):
    _g2_s = _g2[_g2_c][0]
    _g2_says = [x[2] for x in _G2_CASES if x[0] == _g2_c][0]
    t("G2: " + _g2_label + ": Not enough evidence, the guidance sentence after the untested takeaway, no guidance line",
      _g2_s["verdict"] == "insufficient" and _g2_s["evidence_line"] != verdict.GUIDANCE_LINE
      and _g2_s["tldr"] == verdict.UNTESTED_TLDR + " " + verdict.GUIDELINE_LEAD + _g2_says
      and not verdict.from_guidance(_g2_s["tldr"], _g2_c), (_g2_s["verdict"], _g2_s["tldr"], _g2_s["evidence_line"]))
t("G2: for each case a fresh check with nothing cited and the saved answer with everything set aside give the same stamp",
  all(_s["verdict"] == _f["verdict"] for _s, _f in _g2.values())
  and [_f["verdict"] for _s, _f in _g2.values()] == ["false", "insufficient", "insufficient"],
  {k: (v[0]["verdict"], v[1]["verdict"], v[1]["tldr"]) for k, v in _g2.items()})
t("  and the same takeaway where the stamp is Not enough evidence",
  all(_s["tldr"] == _f["tldr"] for _s, _f in list(_g2.values())[1:]),
  {k: (v[0]["tldr"], v[1]["tldr"]) for k, v in _g2.items()})
t("  and one rule says when guidance alone may rate, for both paths",
  "guidance_may_rate" in _inspect.getsource(verdict.follow_guidelines)
  and "guidance_may_rate" in _inspect.getsource(appmod._nothing_relied)
  and "_nothing_relied" in _inspect.getsource(appmod._check_response))
# G2b: every list shows the stamp the page shows, by the same decision
# (app._nothing_relied over verdict.guidance_may_rate).
_g2b = {}
for _g2_c, _g2_v, _g2_says, _g2_doc, _g2_st in _G2_CASES:
    _g2_t = (verdict.GUIDELINE_LEAD + _g2_says + " " + _g2_doc).strip()
    db.put_cached_verdict(_g2_c, "(x) AND (y)", _g2_v, "Guidance says so (Study 1).", [_g2_st["pmid"]],
                          [dict(_g2_st)], tldr=_g2_t, still_open="Whether a trial would settle it.")
    _g2_row = db.get_cached_by_key(db.normalize_claim(_g2_c))
    _g2_l = appmod._listed({"claim_text": _g2_row["claim_text"], "verdict": _g2_row["verdict"],
                            "search_query": _g2_row.get("search_query") or "", "tldr": _g2_row.get("tldr") or "",
                            "studies": _g2_row["studies"], "cited": _g2_row["cited_studies"]})
    _g2_s = appmod._suggested({"claim_text": _g2_c, "verdict": _g2_v})
    with appmod.app.test_request_context("/"):
        _g2_p = appmod._check_response(_g2_c, _g2_row.get("search_query") or "", _g2_row["verdict"], _g2_row["tldr"],
                                       _g2_row["explanation"], _g2_row["cited_studies"], _g2_row["studies"], "http://x",
                                       still_open=_g2_row.get("still_open") or "")
    _g2b[_g2_c] = (_g2_l["verdict"], _g2_s["verdict"], _g2_p["verdict"], _g2_l["tldr"] == _g2_p["tldr"])
    with db.get_conn() as _conn:
        _conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(_g2_c),))
t("G2b: for each case the stamp in the lists (recent, type-ahead) equals the stamp on the page",
  all(l_ == s_ == p_ and same for l_, s_, p_, same in _g2b.values())
  and [x[2] for x in _g2b.values()] == ["false", "insufficient", "insufficient"], _g2b)
t("  and the lists reach it through the page's own decision",
  "_nothing_relied" in _inspect.getsource(appmod._listed))
# F1a and F1b (type-ahead shows only whole rows, at the stamp its page would
# show) meet L1: a saved "true" that cites a retracted paper is out of date.
pubmed.search_and_fetch = lambda q, max_results=8, surrogate='': [dict(x) for x in _RT_ST]
for _mt_c, _mt_cited in (("Retracted kelp lowers blood sugar", ["RT1"]),
                         ("Retracted dulse lowers blood sugar", ["RT1", "RT2"])):
    db.put_cached_verdict(_mt_c, "(vinegar) AND (blood sugar)", "true", "Trials found it (Study 1).", _mt_cited,
                          [dict(x) for x in _RT_ST], tldr="It lowers blood sugar.", still_open="Longer trials.")
_mt_rows = {k: db.get_cached_verdict(k, 24) for k in ("Retracted kelp lowers blood sugar",
                                                      "Retracted dulse lowers blood sugar")}
_mt_sugg = {r_["claim_text"]: r_["verdict"] for r_ in c.get("/api/suggest?q=retracted").get_json()["suggestions"]}
def _mt_page(k):
    r_ = _mt_rows[k]
    with appmod.app.test_request_context("/"):
        return appmod._check_response(k, r_["search_query"], r_["verdict"], r_["tldr"], r_["explanation"],
                                      r_["cited_studies"], r_["studies"], "http://x", still_open=r_["still_open"])["verdict"]
t("meet F1 and L1: a saved 'true' citing a retracted paper is out of date",
  all(appmod._current(r_, k) is None for k, r_ in _mt_rows.items()))
t("  and type-ahead never shows it as 'true' when its page would not: it shows the page's stamp or nothing",
  _mt_sugg.get("Retracted kelp lowers blood sugar") in (None, _mt_page("Retracted kelp lowers blood sugar"))
  and _mt_sugg.get("Retracted kelp lowers blood sugar") != "true"
  and _mt_sugg.get("Retracted dulse lowers blood sugar") in (None, _mt_page("Retracted dulse lowers blood sugar")),
  (_mt_sugg, [_mt_page(k) for k in _mt_rows]))
with db.get_conn() as _conn:
    _conn.execute("DELETE FROM verdict_cache WHERE claim_key IN (?, ?)",
                  tuple(db.normalize_claim(k) for k in _mt_rows))
pubmed.search_and_fetch = _rt_search

# ---- final fix F12: a withdrawn paper is never a strong design -----------------
_F12_T = ["Journal Article", "Randomized Controlled Trial", "Retracted Publication"]
_f12_s = _study("F12", "A trial later withdrawn", types=_F12_T, abstract="Adults were randomized to it or placebo.")
t("F12: a paper typed both a randomized trial and retracted is retracted in classify, kind and its label",
  evidence.classify(_F12_T) == "retracted" and evidence.kind(_f12_s) == "retracted"
  and evidence.strongest_label(_F12_T) == "Retracted Publication",
  (evidence.classify(_F12_T), evidence.kind(_f12_s), evidence.strongest_label(_F12_T)))
t("F12: it is never counted as a strong design, a trial or a strong bar",
  evidence.snapshot([_f12_s], ["F12"])["strong"] == 0 and evidence.snapshot([_f12_s], ["F12"])["trials"] == 0
  and not any(b_.get("tier") == "strong" and b_.get("count") for b_ in (evidence.mix([_f12_s]) or [])
              if isinstance(b_, dict)))
t("F12: the study's own description and the card say retracted, not trial",
  _display.design_note(_F12_T)[1] == "retracted"
  and og.strongest_cited([_f12_s, _study("F13", "A plain observational study", types=["Observational Study"])],
                         ["F12", "F13"])[2] == "A plain observational study"
  and og.strongest_cited([_f12_s], ["F12"])[0] == "Retracted Publication")
if _shutil.which("node"):
    _f12src = (ROOT / "static" / "app.js").read_text()
    _f12fn = "const TYPES = [" + _f12src.split("const TYPES = [")[1].split("function escapeHtml")[0]
    _f12js = _subprocess.run(["node", "-e", _f12fn + "console.log(typeInfo(" + json.dumps(_F12_T) + ").cls);"],
                             capture_output=True, text=True, check=True).stdout.strip()
    t("F12: and the script's badge for it is retracted too", _f12js == "retracted", _f12js)

# ---- L2: narrative reviews do not lift small trials ---------------------------
_NR_TRIAL = _study("NR1", "Creatine and scalp hair in young men: a randomized trial",
                   abstract="In this 12-week randomized, placebo-controlled trial, 38 healthy men took creatine "
                            "or placebo. Scalp hair counts did not differ between groups.")
_NR_REV = [_study("NR2", "Creatine and hair: a narrative review", types=["Journal Article", "Review"],
                  abstract="We review what is known about creatine and hair."),
           _study("NR3", "Creatine safety: an overview", types=["Review"],
                  abstract="This review summarises creatine safety reports.")]
_NR_GL = _study("NR4", "Clinical practice guideline on creatine", types=["Practice Guideline"],
                abstract="The panel recommends creatine for strength.")
_NR_ALL = [_NR_TRIAL] + _NR_REV + [_NR_GL]
t("L2: a narrative review is neither solid nor limited, so two of them do not lift one small trial",
  evidence.trial_caveats(_NR_TRIAL) and evidence.narrative_review(_NR_REV[0]) and evidence.narrative_review(_NR_REV[1])
  and evidence.certainty_cap(_NR_ALL, ["NR1", "NR2", "NR3"])
  == evidence.certainty_cap(_NR_ALL, ["NR1"]) == "it rests on small, pilot or uncontrolled studies",
  (evidence.trial_caveats(_NR_TRIAL), evidence.certainty_cap(_NR_ALL, ["NR1", "NR2", "NR3"])))
t("  a guideline still counts, and a pooled review or a guideline is never a narrative review",
  evidence.certainty_cap(_NR_ALL, ["NR1", "NR4"]) is None and not evidence.narrative_review(_NR_GL)
  and not evidence.narrative_review(_study("NR5", "x", types=["Systematic Review", "Review"])))
t("  so the line over the trial and the reviews is no surer than over the trial alone",
  evidence.line_certainty(evidence.evidence_line("false", _NR_ALL, ["NR1", "NR2", "NR3"], "Creatine causes hair loss"))
  == evidence.line_certainty(evidence.evidence_line("false", _NR_ALL, ["NR1"], "Creatine causes hair loss")),
  (evidence.evidence_line("false", _NR_ALL, ["NR1", "NR2", "NR3"], "Creatine causes hair loss"),
   evidence.evidence_line("false", _NR_ALL, ["NR1"], "Creatine causes hair loss")))

# ---- L3: no empty reference ---------------------------------------------------
_ER = ("A review found no consistent harm (Study 5), but other reviews note that alcohol is linked "
       "to fetal harm (Study 6). The evidence is mixed (Study 5).")
t("L3: a citation whose every number is taken out disappears whole, with the space before it",
  breakdown.relied_only(_ER, {5}, 8) == "A review found no consistent harm (Study 5), but other reviews note "
  "that alcohol is linked to fetal harm. The evidence is mixed (Study 5).", breakdown.relied_only(_ER, {5}, 8))
t("  and no separator is left dangling in the same brackets, nor an empty pair of brackets anywhere",
  breakdown.relied_only("Harm (Study 6; Study 5).", {5}, 8) == "Harm (Study 5)."
  and breakdown.relied_only("Harm (Studies 6, 7) and help (Study 5).", {5}, 8) == "Harm and help (Study 5)."
  and breakdown.relied_only("Harm (Studies ). Plain ( ) words (Study 5).", {5}, 8) == "Harm. Plain words (Study 5).")
t("  a citation that was part of the sentence itself takes the sentence with it, and plain words stay",
  breakdown.relied_only("Studies 6 and 7 show harm (Study 5). Help (Study 5).", {5}, 8) == "Help (Study 5)."
  and breakdown.relied_only("As in other trials (see earlier studies).", {5}, 8)
  == "As in other trials (see earlier studies).")
_G4_PLAIN = ["The effect was significant (p = .03).", "Vitamin D (and calcium) improved bone density.",
             "A mean difference of .5 points was seen (Study 2).", "The ratio was 1 : 2 in both arms."]
t("G4: a sentence in which no reference was emptied comes back byte for byte",
  all(breakdown.without_empty_refs(x) == x and breakdown.relied_only(x, {2, 5}, 8) == x for x in _G4_PLAIN),
  [breakdown.relied_only(x, {2, 5}, 8) for x in _G4_PLAIN])
t("  an empty reference goes with the space before it, and only that bracket changes",
  [breakdown.without_empty_refs(x) for x in ("Harm (Studies ), (p = .03).", "Harm (Study ) here.", "Harm ( ) here.")]
  == ["Harm, (p = .03).", "Harm here.", "Harm here."])
t("  a dangling separator or an odd reference is rebuilt from the numbers it still has",
  breakdown.relied_only("Harm (Study 5; Study 6).", {5}, 8) == "Harm (Study 5)."
  and [breakdown.without_empty_refs(x) for x in ("Harm (Studies , 4).", "Harm (Study and 4).", "Harm (Studies 2, ).")]
  == ["Harm (Study 4).", "Harm (Study 4).", "Harm (Study 2)."])
t("  a sentence whose subject was the reference still goes whole",
  breakdown.relied_only("Study 6 and Study 7 show harm (Study 5). Help (Study 5).", {5}, 8) == "Help (Study 5).")
_H5_GONE = [("Harm (see Studies ) here (Study 5).", "Harm here (Study 5)."),
            ("Harm (see also Studies 6, 7) and help (Study 5).", "Harm and help (Study 5)."),
            ("Harm (e.g. Study 6), then (cf. Study 7) help (Study 5).", "Harm, then help (Study 5)."),
            ("Harm (as in Studies 6 and 7) and help (Study 5).", "Harm and help (Study 5).")]
_H5_LEFT = [("Harm (see Studies , 4).", "Harm (see Study 4)."), ("Harm (cf. Studies 6, 5).", "Harm (cf. Study 5)."),
            ("Harm (e.g. Studies 2, ).", "Harm (e.g. Study 2).")]
t("H5: an emptied reference with a lead-in goes whole, with the space before it",
  [breakdown.relied_only(x, {2, 4, 5}, 8) for x, _ in _H5_GONE] == [y for _, y in _H5_GONE],
  [breakdown.relied_only(x, {2, 4, 5}, 8) for x, _ in _H5_GONE])
t("  with numbers left it keeps its lead-in and the numbers it has",
  [breakdown.relied_only(x, {2, 4, 5}, 8) for x, _ in _H5_LEFT] == [y for _, y in _H5_LEFT],
  [breakdown.relied_only(x, {2, 4, 5}, 8) for x, _ in _H5_LEFT])
_H5_SAME = ["Harm (see Study 4).", "Harm (see below).", "Harm (e.g. pain or swelling).", "As in other trials (see earlier studies).",
            "That is, about 12% lower odds (Study 4).", "Harm (cf. Studies 2, 4, 5)."]
t("  and a lead-in bracket that lost nothing comes back byte for byte",
  all(breakdown.without_empty_refs(x) == x and breakdown.relied_only(x, {2, 4, 5}, 8) == x for x in _H5_SAME),
  [breakdown.relied_only(x, {2, 4, 5}, 8) for x in _H5_SAME])
_er_pg = appmod._check_response("Empty ref claim", "(alcohol) AND (pregnancy)", "complicated", "Mixed.", _ER,
                                ["P5"], [_study(f"P{i}", f"Study of alcohol {i}") for i in range(1, 9)],
                                "http://x", still_open="Whether small amounts harm.")
t("  and no prose field on a page carries an empty reference",
  not any(re.search(r"\(\s*(?:Stud(?:y|ies)\s*)?\)", str(v)) for v in
          (_er_pg.get("tldr"), _er_pg.get("explanation"), json.dumps(_er_pg.get("breakdown")))), _er_pg.get("explanation"))

# ---- L4: a promise's line claims no strength against what nobody tested -------
_L4_ST = [_study("Q1", "Vinegar and blood sugar: a meta-analysis", types=["Meta-Analysis"],
                 abstract="We pooled 12 randomized trials in 800 adults.")]
_l4 = {noun: (evidence.evidence_line("false", _L4_ST, ["Q1"], "x", None, word),
              evidence.evidence_line("false", _L4_ST, ["Q1"], "x", "small", word))
       for word, noun in (("cure", "a cure"), ("reverse", "a reversal"), ("prevent", "complete prevention"),
                          ("stop", "a complete stop"), ("eliminate", "complete removal"), ("heal", "a full fix"))}
t("L4: a false answer to a promise with no size says none of the evidence shows it, for every promise",
  all(a == f"Strong evidence, and none of it shows {n}" and b == f"Strong evidence of a small effect, not {n}"
      for n, (a, b) in _l4.items()), _l4)
t("  and line_certainty still reads the level off the new line, so the prose caps keep working",
  evidence.line_certainty("Strong evidence, and none of it shows a cure") == evidence.line_certainty("Strong evidence")
  and evidence.line_certainty("Very low to moderate certainty evidence, and none of it shows complete prevention")
  == "very low")
t("  a false answer to a claim with no promise still says 'against this claim'",
  evidence.evidence_line("false", _L4_ST, ["Q1"], "Vinegar raises blood sugar") == "Strong evidence against this claim")

# ---- L5: "cure" the noun is not softened as a verb ----------------------------
t("L5: the softening swap leaves 'cure' the noun alone and still softens the verb",
  breakdown.soften("The abstracts do not report numerical effect sizes for cold cures (Study 5).")
  == "The abstracts do not report numerical effect sizes for cold cures (Study 5)."
  and breakdown.soften("There is no cure for the common cold (Study 2).") == "There is no cure for the common cold (Study 2)."
  and breakdown.soften("Garlic cures colds (Study 1).") == "Garlic helps with colds (Study 1)."
  and breakdown.soften("Garlic cures and prevents colds.") == "Garlic helps with and lowers the risk of colds.")

t("no response in this run ever carried a study both cited in the answer and off topic",
  _PAYLOADS and not [p_["pmid"] for p_ in _PAYLOADS if p_["cited_in_verdict"] and p_["off_topic"]],
  (len(_PAYLOADS), [p_["pmid"] for p_ in _PAYLOADS if p_["cited_in_verdict"] and p_["off_topic"]][:10]))

t("no test opened a network connection or looked a host up", not NET_TRIES, NET_TRIES[:5])
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
