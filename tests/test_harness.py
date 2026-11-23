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
real_get = pubmed.requests.get
pubmed.requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("replay reached PubMed"))
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
pubmed.requests.get = real_get
gone = saved_model[-1]
gone.unlink()
h = harness.install("fill", str(STORE))
fake.models.calls = 0; PUBMED_CALLS.clear(); h.reset()
evs = check()
fill_snap = h.snapshot()
t("fill: exactly the missing call goes out and is saved", fake.models.calls == 1 and fill_snap["model_live"] == 1
  and fill_snap["saved"] == 1 and gone.exists() and not PUBMED_CALLS, (fake.models.calls, fill_snap))
t("fill: the answer is still the recorded one", canon(evs[-1].get("result")) == canon(RECORDED))

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

shutil.rmtree(WORK, ignore_errors=True)
t("no test used the network", not NET_TRIES, NET_TRIES)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
