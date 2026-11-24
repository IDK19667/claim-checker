"""
Tests for the record and replay harness (scripts/harness.py), the gold runner
(scripts/gold_run.py) and the scorer (scripts/gold_score.py). A fake model
client and a fake PubMed sit under the harness; no network, no key. Run:

    .venv/bin/python tests/test_harness.py
"""
import json
import logging
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types as pytypes

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for k in ("ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(k, None)
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""
os.environ["GEMINI_API_KEY"] = "fake-key-for-tests"
os.environ["VERDICT_CACHE_HOURS"] = "0"
for k in ("RATE_LIMIT_GLOBAL_PER_MINUTE", "RATE_LIMIT_PER_USER", "RATE_LIMIT_GLOBAL_PER_DAY"):
    os.environ[k] = "100000"
WORK = pathlib.Path(tempfile.mkdtemp(prefix="harness-"))
DB_FILE = WORK / "t.db"
os.environ["DB_PATH"] = str(DB_FILE)

import dotenv  # noqa: E402
dotenv.load_dotenv = lambda *a, **k: None  # keep the developer's .env out of the tests

import db  # noqa: E402
assert db._turso_config() is None
db.COUNT_FLUSH_SECONDS = 0

# No test may use the network.
NET_TRIES = []


def _no_network(*a, **k):
    NET_TRIES.append(repr(a)[:80])
    raise OSError("tests/test_harness.py: a test tried to use the network")


for _name in ("connect", "connect_ex"):
    setattr(socket.socket, _name, _no_network)
socket.getaddrinfo = _no_network
socket.create_connection = _no_network

import app as appmod  # noqa: E402
import pubmed  # noqa: E402
import requests  # noqa: E402
import verdict  # noqa: E402
from google.genai import errors, types  # noqa: E402

import harness  # noqa: E402

appmod.app.logger.setLevel(logging.CRITICAL)
verdict.time = pytypes.SimpleNamespace(sleep=lambda s: None)   # no retry pauses
pubmed.MIN_INTERVAL = 0
results = []


def t(label, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'} {label} {extra if not cond else ''}")


# ---- fakes under the harness --------------------------------------------------
def resp(text, prompt=""):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]),
                                    finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=max(1, len(prompt) // 4), candidates_token_count=max(1, len(text) // 4)))


VERDICT = json.dumps({"verdict": "complicated", "tldr": "Coffee may cut sleep a little.",
                      "explanation": "Two trials found small effects [1][2].",
                      "cited_study_numbers": [1, 2], "still_open": "Longer trials are needed."})


class FakeModels:
    def __init__(self):
        self.calls = 0
        self.fail_next = []          # exceptions to raise before answering
        self.lock = threading.Lock()

    def generate_content(self, **kw):
        with self.lock:
            self.calls += 1
            if self.fail_next:
                raise self.fail_next.pop(0)
        prompt = str(kw.get("contents") or "")
        cfg = kw.get("config") or {}
        schema = cfg.get("response_json_schema") or {}
        props = set((schema.get("properties") or {}).keys())
        if not schema:
            return resp("coffee AND sleep", prompt)
        if "verdict" in props:
            return resp(VERDICT, prompt)
        if "answer" in props:
            return resp(json.dumps({"answer": "lower"}), prompt)
        return resp("{}", prompt)


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


fake = FakeClient()
verdict._gemini_client = fake


def article(pmid):
    return (f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
            f"<Journal><Title>Sleep Journal</Title><JournalIssue><PubDate><Year>2020</Year></PubDate>"
            f"</JournalIssue></Journal><ArticleTitle>Coffee and sleep quality in adults: trial {pmid}"
            f"</ArticleTitle><Abstract><AbstractText>In 120 adults, coffee in the evening lowered sleep "
            f"quality a little compared with placebo.</AbstractText></Abstract>"
            f"<AuthorList><Author><LastName>Lee</LastName><ForeName>A</ForeName></Author></AuthorList>"
            f"<PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType>"
            f"</PublicationTypeList></Article></MedlineCitation></PubmedArticle>")


PUBMED_CALLS = []


def fake_get(url, params=None, timeout=None):
    PUBMED_CALLS.append(url + " " + json.dumps({k: v for k, v in (params or {}).items()
                                               if k not in ("tool", "email")}, sort_keys=True))
    r = requests.Response()
    r.status_code = 200
    r.encoding = "utf-8"
    if url.endswith("esearch.fcgi"):
        r._content = json.dumps({"esearchresult": {"count": "3", "idlist": ["101", "102", "103"]}}).encode()
    elif url.endswith("efetch.fcgi"):
        ids = (params or {}).get("id", "").split(",")
        r._content = ("<PubmedArticleSet>" + "".join(article(i) for i in ids if i) + "</PubmedArticleSet>").encode()
    else:
        r._content = b"{}"
    return r


pubmed.requests = pytypes.SimpleNamespace(get=fake_get, RequestException=requests.RequestException)
pubmed._http = pytypes.SimpleNamespace(get=fake_get)   # the shared session pubmed sends through

client = appmod.app.test_client()
CLAIM = "Coffee lowers sleep quality"


def check(claim=CLAIM):
    """One check on a fresh database: its stream events, each stamped with
    the seconds since the start."""
    for suffix in ("", "-journal"):
        try:
            os.unlink(str(DB_FILE) + suffix)
        except FileNotFoundError:
            pass
    db.init_db()
    t0 = time.perf_counter()
    r = client.post("/api/check/stream", json={"claim": claim}, buffered=False)
    evs, buf = [], ""
    for chunk in r.response:
        buf += chunk.decode() if isinstance(chunk, bytes) else chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            if block.startswith("data: "):
                evs.append({**json.loads(block[6:]), "t": time.perf_counter() - t0})
    r.close()
    return evs


def canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


# ---- key -----------------------------------------------------------------------
k1 = harness.key_of(harness.pubmed_request("esearch.fcgi", {"term": "x", "tool": "a", "email": "b@c"}))
k2 = harness.key_of(harness.pubmed_request("esearch.fcgi", {"email": "", "term": "x", "tool": "other"}))
k3 = harness.key_of(harness.pubmed_request("esearch.fcgi", {"term": "y"}))
t("pubmed key: tool and email left out, order does not matter, the term counts", k1 == k2 != k3)
m1 = harness.key_of(harness.model_request({"model": "m", "contents": "p", "config": {"temperature": 0, "seed": 0}}))
m2 = harness.key_of(harness.model_request({"model": "m", "contents": "p", "config": {"seed": 0, "temperature": 0}}))
m3 = harness.key_of(harness.model_request({"model": "m", "contents": "p", "config": {"temperature": 0, "seed": 1}}))
m4 = harness.key_of(harness.model_request({"model": "m2", "contents": "p", "config": {"temperature": 0, "seed": 0}}))
m5 = harness.key_of(harness.model_request({"model": "m", "contents": "p", "config": {"temperature": 0, "seed": 0,
                                                                                    "max_output_tokens": 9}}))
t("model key: model, contents and every config field count", m1 == m2 and len({m1, m3, m4, m5}) == 4)

# ---- record, with known call counts ---------------------------------------------
STORE = WORK / "store"
h = harness.install("record", str(STORE))
fake.models.calls = 0; PUBMED_CALLS.clear(); h.reset()
fake.models.fail_next = [errors.ServerError(503, {"error": {"message": "busy", "status": "UNAVAILABLE"}})]
evs = check()
rec_snap = h.snapshot()
final = evs[-1]
t("record: the check answers", final.get("stage") == "done" and final["result"]["verdict"] in verdict.VALID_VERDICTS,
  final)
t("record: the stream events come in order with times", [e["stage"] for e in evs][:3] == ["search", "query", "found"]
  and all(evs[i]["t"] <= evs[i + 1]["t"] for i in range(len(evs) - 1)), [e["stage"] for e in evs])
t("counts: logical calls and tries match what reached the provider (one 503 retried)",
  rec_snap["model_tries"] == fake.models.calls and rec_snap["model_calls"] == fake.models.calls - 1
  and rec_snap["model_calls"] >= 2, (rec_snap, fake.models.calls))
t("counts: PubMed requests match what reached PubMed", rec_snap["pubmed_requests"] == len(PUBMED_CALLS) >= 2,
  (rec_snap, len(PUBMED_CALLS)))
t("counts: this check is the query call and the verdict call (3 tries), and 4 PubMed requests (3 searches, 1 fetch)",
  (rec_snap["model_calls"], rec_snap["model_tries"], rec_snap["pubmed_requests"]) == (2, 3, 4), rec_snap)
t("counts: tokens and times are summed", rec_snap["tokens_in"] > 0 and rec_snap["tokens_out"] > 0
  and rec_snap["model_ms"] >= 0 and rec_snap["model_live"] == fake.models.calls)
saved_model = sorted((STORE / "model").glob("*.json"))
saved_pubmed = sorted((STORE / "pubmed").glob("*.json"))
t("record: one file per distinct call", len(saved_model) == rec_snap["model_calls"]
  and len(saved_pubmed) == len(set(PUBMED_CALLS)), (len(saved_model), len(saved_pubmed), len(set(PUBMED_CALLS))))
retried = [json.loads(p.read_text()) for p in saved_model]
t("record: the try count is kept, and only successes are saved",
  sorted(r["tries"] for r in retried)[-1] == 2 and all(r["response"].get("candidates") for r in retried))
t("record: usage numbers are saved with the response",
  all(r["response"]["usage_metadata"]["prompt_token_count"] > 0 for r in retried))
t("record: no temp files left", not list(STORE.rglob("*.tmp")))
RECORDED = final["result"]


# ---- the same check, served from the store ------------------------------------------
class Refuse:
    """A provider that must never be reached."""
    def __getattr__(self, name):
        raise AssertionError("replay reached the real provider")


verdict._gemini_client = Refuse()
real_get = pubmed._http.get
pubmed._http.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("replay reached PubMed"))
h = harness.install("replay", str(STORE))
fake.models.calls = 0; PUBMED_CALLS.clear(); h.reset()
evs = check()
rep_snap = h.snapshot()
t("replay: the final response is the same, byte for byte",
  evs[-1].get("stage") == "done" and canon(evs[-1]["result"]) == canon(RECORDED))
t("replay: no live call of either kind", rep_snap["model_live"] == 0 and rep_snap["pubmed_live"] == 0
  and rep_snap["model_replayed"] == rec_snap["model_calls"] and not NET_TRIES, rep_snap)
t("replay: the same numbers are reported", rep_snap["model_calls"] == rec_snap["model_calls"]
  and rep_snap["pubmed_requests"] == rec_snap["pubmed_requests"]
  and rep_snap["tokens_in"] == rec_snap["tokens_in"] and rep_snap["tokens_out"] == rec_snap["tokens_out"])

# ---- a miss is loud -----------------------------------------------------------------
h.reset()
evs = check("Tea lowers sleep quality")
miss_snap = h.snapshot()
t("replay miss: the check fails with a defined error, never a silent answer",
  evs[-1].get("stage") == "error" and evs[-1].get("status") == 500, evs[-1])
t("replay miss: listed with its kind and key", miss_snap["misses"] and miss_snap["misses"][0]["kind"] == "model"
  and len(miss_snap["misses"][0]["key"]) == 64 and h.misses == miss_snap["misses"], miss_snap["misses"])
try:
    h._gemini().models.generate_content(model="m", contents="never saved", config={})
    raised = None
except harness.ReplayMiss as e:
    raised = str(e)
t("replay miss: raises ReplayMiss naming the call kind and key", raised and "model call" in raised and "key " in raised,
  raised)
try:
    pubmed._paced_get("esearch.fcgi", {"term": "never saved"})
    raised = None
except harness.ReplayMiss as e:
    raised = str(e)
t("replay miss: a PubMed miss names its kind and key too", raised and "pubmed call" in raised, raised)

# ---- latency ------------------------------------------------------------------------
one = json.loads(saved_model[0].read_text())
one["ms"] = 250
saved_model[0].write_text(json.dumps(one))
h = harness.install("replay", str(STORE), latency=True)
t0 = time.perf_counter()
h._gemini().models.generate_content(**{"model": one["request"]["model"], "contents": one["request"]["contents"],
                                       "config": one["request"]["config"]})
t("latency: a replay hit sleeps the recorded milliseconds", time.perf_counter() - t0 >= 0.24)

# ---- fill: only the missing call goes out -------------------------------------------
verdict._gemini_client = fake
pubmed._http.get = real_get
gone = saved_model[-1]
gone.unlink()
h = harness.install("fill", str(STORE))
fake.models.calls = 0; PUBMED_CALLS.clear(); h.reset()
evs = check()
fill_snap = h.snapshot()
t("fill: exactly the missing call goes out and is saved", fake.models.calls == 1 and fill_snap["model_live"] == 1
  and fill_snap["saved"] == 1 and gone.exists() and not PUBMED_CALLS, (fake.models.calls, fill_snap))
t("fill: the answer is still the recorded one", canon(evs[-1].get("result")) == canon(RECORDED))

# ---- a 429 says which quota it hit ---------------------------------------------------
QLOG = WORK / "quota.jsonl"
h = harness.install("record", str(WORK / "qstore"), quota_log=str(QLOG))
DAILY = {"error": {"code": 429, "message": "You exceeded your current quota.", "status": "RESOURCE_EXHAUSTED",
                   "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                       {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        "quotaDimensions": {"location": "global", "model": "m"}, "quotaValue": "20"}]},
                               {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41s"}]}}
fake.models.fail_next = [errors.ClientError(429, DAILY), errors.ClientError(429, {})]
for _ in range(2):
    try:
        verdict._gemini().models.generate_content(model="m", contents="SECRET PROMPT TEXT", config={})
    except errors.ClientError:
        pass
qlines = [json.loads(l) for l in QLOG.read_text().splitlines()] if QLOG.exists() else []
t("429: each try writes one quota line", len(qlines) == 2 and len(h.quota_seen) == 2, qlines)
if len(qlines) == 2:
    q0, q1 = qlines
    t("429: the line names the kind, the quota id, the limit and the retry delay",
      q0["kind"] == "day" and q0["status"] == 429 and q0["model"] == "m" and q0["retry_delay"] == "41s"
      and q0["quotas"][0]["quotaValue"] == "20" and "PerDay" in q0["quotas"][0]["quotaId"]
      and q0["message"].startswith("You exceeded"), q0)
    t("429: an error that carries nothing still writes what there is",
      q1["status"] == 429 and q1["kind"] is None and "quotas" not in q1 and q1["time"], q1)
    t("429: no prompt and no key in the log", "SECRET PROMPT" not in QLOG.read_text()
      and os.environ["GEMINI_API_KEY"] not in QLOG.read_text())
import gold_run  # noqa: E402
t("429: the runner says per day and the limit", gold_run.quota_words(qlines[:1]) == "per day, limit 20, retry after 41s"
  and gold_run.quota_words(qlines[1:]) == "" and gold_run.quota_words([]) == "", gold_run.quota_words(qlines[:1]))
seen = gold_run.quota_seen(QLOG)
t("429: summary lists each quota seen", {(r["kind"], r["limit"]) for r in seen} == {("day", "20"), (None, None)}, seen)
minute = harness.quota_info(errors.ClientError(429, {"error": {"details": [{"violations": [
    {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "15"}]}]}}))
t("429: a per-minute quota reads as minute", minute["kind"] == "minute", minute)

# ---- two threads at once lose nothing -----------------------------------------------
TSTORE = WORK / "tstore"
h = harness.install("record", str(TSTORE))
h.reset()
errs = []


def worker(n):
    try:
        for i in range(40):
            verdict._gemini().models.generate_content(model="m", contents=f"thread {n} call {i}", config={})
            pubmed._paced_get("esearch.fcgi", {"term": f"thread {n} q {i}"})
    except Exception as e:  # noqa: BLE001
        errs.append(repr(e))


ths = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
[th.start() for th in ths]
[th.join() for th in ths]
snap = h.snapshot()
t("threads: every call counted and saved, none lost", not errs and snap["model_calls"] == 80
  and snap["model_tries"] == 80 and snap["pubmed_requests"] == 80 and snap["saved"] == 160
  and len(list((TSTORE / "model").glob("*.json"))) == 80 and len(list((TSTORE / "pubmed").glob("*.json"))) == 80,
  (errs, snap))
h.uninstall()
t("uninstall puts the engine back", verdict._gemini.__module__ == "verdict"
  and pubmed._paced_get.__module__ == "pubmed")

# ---- the runner, end to end, in its own process, replaying the store --------------------
claims_file = WORK / "claims.json"
claims_file.write_text(json.dumps({"claims": [{"id": "x-01", "claim": CLAIM, "reference": "complicated",
                                               "also_ok": [], "group": "g"}]}))
run_out = WORK / "run"
env = {**os.environ, "GEMINI_API_KEY": "", "DB_PATH": "", "TMPDIR": str(WORK)}
p = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_run.py"), str(claims_file), "--mode", "replay",
                    "--store", str(STORE), "--out", str(run_out), "--quiet-claims"],
                   env=env, capture_output=True, text=True, timeout=300)
lines = [json.loads(l) for l in (run_out / "results.jsonl").read_text().splitlines()] if p.returncode == 0 else []
t("runner: replay of a recorded claim exits 0 and writes one line", p.returncode == 0 and len(lines) == 1,
  p.stdout[-500:] + p.stderr[-1500:])
if lines:
    L = lines[0]
    t("runner: the line carries status, verdict, response, events, numbers and wall time",
      L["status"] == 200 and L["verdict"] == RECORDED["verdict"] and canon(L["response"]) == canon(RECORDED)
      and [e["stage"] for e in L["events"]][-1] == "done" and L["harness"]["model_live"] == 0
      and L["wall"] > 0, {k: L[k] for k in ("status", "verdict", "wall")})
    t("runner: --quiet-claims prints no claim text", CLAIM not in p.stdout and "x-01" in p.stdout, p.stdout)
    summ = json.loads((run_out / "summary.json").read_text())
    t("runner: summary has wall time, each stage, calls, tokens and PubMed requests",
      summ["wall"]["n"] == 1 and summ["stages"]["done"]["at"] and summ["model_calls"]["median"] == rep_snap["model_calls"]
      and summ["pubmed_requests"]["median"] == rep_snap["pubmed_requests"] and summ["errors"] == 0, summ)
p2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_run.py"), str(claims_file), "--mode", "replay",
                     "--store", str(STORE), "--out", str(run_out), "--quiet-claims", "--resume"],
                    env=env, capture_output=True, text=True, timeout=300)
t("runner: --resume skips ids already written",
  p2.returncode == 0 and len((run_out / "results.jsonl").read_text().splitlines()) == 1, p2.stderr[-500:])
p3 = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_run.py"), str(claims_file), "--mode", "replay",
                     "--store", str(WORK / "empty"), "--out", str(WORK / "run3"), "--quiet-claims"],
                    env=env, capture_output=True, text=True, timeout=300)
t("runner: a replay miss prints MISS with the kind and key and exits 5",
  p3.returncode == 5 and "MISS x-01 model key " in p3.stdout, (p3.returncode, p3.stdout[-300:]))
p4 = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_run.py"), str(claims_file), "--mode", "replay",
                     "--store", str(STORE), "--out", str(WORK / "run4")],
                    env={**env, "DB_PATH": str(WORK / "elsewhere.db")}, capture_output=True, text=True, timeout=300)
t("runner: refuses a DB_PATH outside --out", p4.returncode != 0 and "DB_PATH must be a file under" in p4.stderr,
  p4.stderr[-300:])
reg = harness.load_claims(ROOT / "tests" / "regression_claims.json")
saf = harness.load_claims(ROOT / "tests" / "safety_claims.json")
t("claims loader reads the regression and safety shapes",
  reg[0]["id"] == "1" and reg[0]["reference"] and saf[0]["safety"]["harmful_if"] == ["false", "insufficient", "complicated"])

# ---- a budget of live model calls ----------------------------------------------------
import contextlib  # noqa: E402
import io  # noqa: E402
bclaims = WORK / "bclaims.json"
bclaims.write_text(json.dumps([{"id": "b-01", "claim": CLAIM, "reference": "true"},
                               {"id": "b-02", "claim": "Tea lowers sleep quality", "reference": "true"}]))
bout = WORK / "brun"
bout.mkdir()
os.environ["DB_PATH"] = str(bout / "b.db"); db.DB_PATH = os.environ["DB_PATH"]
verdict._gemini_client = fake; fake.models.calls = 0
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = gold_run.main([str(bclaims), "--mode", "record", "--store", str(WORK / "bstore"), "--out", str(bout),
                          "--gap", "0", "--max-calls", "1", "--quiet-claims"])
blines = (bout / "results.jsonl").read_text().splitlines()
bsum = json.loads((bout / "summary.json").read_text())
t("budget: stops before the next claim once live calls reach the limit, BUDGET line, exit 4",
  code == 4 and len(blines) == 1 and "\nBUDGET: 2 live model calls" in "\n" + buf.getvalue()
  and bsum["budget_stop"] is True and bsum["live_model_calls_this_run"] == 2, (code, buf.getvalue()[-300:]))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = gold_run.main([str(bclaims), "--mode", "fill", "--store", str(WORK / "bstore"), "--out", str(bout),
                          "--gap", "0", "--max-calls", "10", "--resume", "--quiet-claims"])
t("budget: --resume carries on from there", code == 0 and len((bout / "results.jsonl").read_text().splitlines()) == 2,
  (code, buf.getvalue()[-300:]))
os.environ["DB_PATH"] = str(DB_FILE); db.DB_PATH = str(DB_FILE)

# ---- the scorer's arithmetic -----------------------------------------------------------
import gold_score  # noqa: E402
items = [
    {"id": "a", "claim": "c", "reference": "true", "also_ok": [], "group": "g1",
     "safety": {"warning_needed": False, "harmful_if": []}},
    {"id": "b", "claim": "c", "reference": "false", "also_ok": ["complicated"], "group": "g1",
     "safety": {"warning_needed": True, "warning": "w", "harmful_if": ["true"]}},
    {"id": "c", "claim": "c", "reference": "complicated", "also_ok": [], "group": "g2",
     "safety": {"warning_needed": False, "harmful_if": ["false"]}},
    {"id": "d", "claim": "c", "reference": "insufficient", "also_ok": [], "group": "g2",
     "safety": {"warning_needed": False, "harmful_if": []}},
    {"id": "e", "claim": "c", "reference": "true", "also_ok": [], "group": "g2",
     "safety": {"warning_needed": False, "harmful_if": []}},
    {"id": "f", "claim": "c", "reference": "false", "also_ok": [], "group": "g1",
     "safety": {"warning_needed": False, "harmful_if": []}},
]
gold = WORK / "gold.json"
gold.write_text(json.dumps(items))
run1 = {"a": "true", "b": "true", "c": "false", "d": "complicated", "e": None}   # f not run
run2 = {"a": "true", "b": "complicated", "c": "complicated", "d": "complicated", "e": "true", "f": "false"}
for name, run in (("r1", run1), ("r2", run2)):
    (WORK / name).mkdir()
    (WORK / name / "results.jsonl").write_text("".join(json.dumps({"id": k, "verdict": v}) + "\n" for k, v in run.items()))
s = gold_score.score(harness.load_claims(gold), gold_score.read_run(WORK / "r1"))
T = s["totals"]
t("scorer: right, allowed, wrong, no answer, not run",
  (T["n"], T["right"], T["allowed"], T["wrong"], T["no_answer"]) == (5, 1, 0, 3, 1) and s["not_run"] == ["f"], T)
t("scorer: confident and wrong out of answered (2 of 4) and out of true-or-false (2 of 3)",
  s["confident_wrong"]["of_answered"] == {"count": 2, "of": 4, "rate": 0.5}
  and s["confident_wrong"]["of_true_or_false"] == {"count": 2, "of": 3, "rate": 0.667}
  and s["confident_wrong_ids"] == ["b", "c"], s["confident_wrong"])
t("scorer: harmful is the verdict shown in harmful_if", s["harmful_ids"] == ["b", "c"], s["harmful_ids"])
t("scorer: tables by group and by reference", s["by_group"]["g1"]["n"] == 2 and s["by_group"]["g2"]["no_answer"] == 1
  and s["by_reference"]["true"]["n"] == 2 and s["by_reference"]["complicated"]["harmful"] == 1, s["by_group"])
t("scorer: a warning claim is left for a grader", s["warning_needed"] == [{"id": "b", "shown": "true",
                                                                            "warning_shown": None}])
c = gold_score.compare(harness.load_claims(gold), gold_score.read_run(WORK / "r1"), gold_score.read_run(WORK / "r2"))
t("scorer: before and after, and the ids whose verdict changed",
  [x["id"] for x in c["changed"]] == ["b", "c", "e"] and c["delta"]["right"] == 3 and c["delta"]["harmful"] == -2
  and c["after"]["totals"]["allowed"] == 1, c["changed"])
p5 = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_score.py"), str(gold), str(WORK / "r1"),
                     str(WORK / "r2")], capture_output=True, text=True, timeout=60)
t("scorer: the command prints ids and counts and writes score.json",
  p5.returncode == 0 and "b: true -> complicated" in p5.stdout and (WORK / "r2" / "score.json").exists(),
  p5.stderr[-300:])

# ---- the retraction lookup under the harness (brief E5, third item) ----------
# pubmed.retracted_among passes `timeout=` to _paced_post; the harness wrapper
# must take it and hand it on, or every lookup under the harness raises
# TypeError and is reported as "could not be made".
_rl_posts = []


def _rl_post(url, data=None, timeout=None):
    _rl_posts.append((url, timeout))
    r = requests.Response()
    r.status_code = 200
    r.encoding = "utf-8"
    r._content = json.dumps({"esearchresult": {"idlist": ["102"]}}).encode()
    return r


_rl_http = pubmed._http
pubmed._http = pytypes.SimpleNamespace(get=fake_get, post=_rl_post)
_rl_res = {}
for _rl_mode, _rl_store in (("live", None), ("record", str(WORK / "rlstore"))):
    h = harness.install(_rl_mode, _rl_store)
    try:
        _rl_res[_rl_mode] = pubmed.retracted_among(["101", "102", "103"])
    except Exception as e:  # noqa: BLE001
        _rl_res[_rl_mode] = repr(e)
    h.uninstall()
h = harness.install("replay", str(WORK / "rlstore"))
try:
    _rl_res["replay"] = pubmed.retracted_among(["101", "102", "103"])
except Exception as e:  # noqa: BLE001
    _rl_res["replay"] = repr(e)
h.uninstall()
pubmed._http = _rl_http
t("harness: a retraction lookup through the wrapper returns its flags and raises nothing (live, record, replay)",
  _rl_res == {"live": {"102"}, "record": {"102"}, "replay": {"102"}}, _rl_res)
t("harness: the wrapper hands the lookup's timeout on to the real POST",
  len(_rl_posts) == 2 and all(isinstance(x[1], tuple) and len(x[1]) == 2 for x in _rl_posts), _rl_posts)

shutil.rmtree(WORK, ignore_errors=True)
t("no test used the network", not NET_TRIES, NET_TRIES)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
