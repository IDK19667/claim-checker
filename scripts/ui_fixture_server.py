"""
A local copy of the app for UI measurement and the keep tests: no model key,
no network, no real PubMed. A few verdicts are already cached (so the
type-ahead and a result screen have something real to show), and the live
pipeline is replaced by a replay whose stages wait on gates the test
releases, so a test can prove the page shows a stage only when the server
has sent it.

    GEMINI_API_KEY= GOOGLE_API_KEY= MISTRAL_API_KEY= ANTHROPIC_API_KEY= \
    TURSO_DATABASE_URL= TURSO_AUTH_TOKEN= PORT=5050 \
    .venv/bin/python scripts/ui_fixture_server.py [source.db]

The source db is only read (a copy of a real cache; gitignored, never
committed). Without one, small made-up rows are used.

Claims with a special path:
  GATED  "Gated claim for stage test"   each stage waits for /__fixture/release/<gate>
  SLOW   "Slow claim for cancel test"   hangs after the query stage
  DOWN   "Provider down claim"          the provider is unavailable (an error screen)
  LIVE   "Vitamin D supplements prevent colds"  streams with a short pause per stage
Gates: query, found, done. /__fixture/reset closes them and forgets the replayed claims.
"""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "MISTRAL_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(k, None)
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "fixture.db")

import dotenv  # noqa: E402
dotenv.load_dotenv = lambda *a, **k: None

import app as appmod  # noqa: E402
import db  # noqa: E402
import pubmed  # noqa: E402
import ratelimit  # noqa: E402
import verdict  # noqa: E402

GATED = "Gated claim for stage test"
SLOW = "Slow claim for cancel test"
DOWN = "Provider down claim"
LIVE = "Vitamin D supplements prevent colds"
PAUSE = float(os.environ.get("UI_FIXTURE_PAUSE", "0.9"))

ratelimit.GLOBAL_MINUTE = ratelimit.SlidingWindow(10000, 60)
ratelimit.GLOBAL_DAY = ratelimit.SlidingWindow(100000, 86400)
ratelimit.PER_USER = ratelimit.SlidingWindow(10000, 600)

# ---- rows to replay ---------------------------------------------------------
SEED = [
    "Antibiotics cure the common cold",
    "Vaccines reduce the risk of severe measles",
    "Sleeping with a fan on gives you a stiff neck",
    "Turmeric reduces joint inflammation",
    "Creatine builds strength",
    "Creatine improves strength",
    "Creatine is not safe for the kidneys",
]
REPLAY_FROM = "Taking vitamin D supplements in winter cuts your risk of catching a cold or flu, but only if you're deficient to begin with"


def _rows(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {r["claim_text"]: r for r in con.execute("SELECT * FROM verdict_cache")}
    return out


def _fallback_row(claim, verdict_word):
    studies = [{"pmid": str(1000 + i), "title": f"A trial of the claim, part {i}", "abstract": "Background. Methods. Results. Conclusions.",
                "journal": "Journal of Examples", "year": "2020", "publication_types": ["Randomized Controlled Trial"],
                "authors": ["Author A", "Author B"], "data_banks": None, "url": f"https://pubmed.ncbi.nlm.nih.gov/{1000 + i}/"}
               for i in range(1, 5)]
    return {"search_query": claim, "verdict": verdict_word, "tldr": "Short answer for the fixture.",
            "explanation": "The studies point one way. [1] [2]", "still_open": "Whether it holds over years.",
            "breakdown": None, "cited": ["1001", "1002"], "studies": studies}


def _as_row(r):
    return {"search_query": r["search_query"] or "", "verdict": r["verdict"], "tldr": r["tldr"] or "",
            "explanation": r["explanation"] or "", "still_open": r["still_open"] or "",
            "breakdown": json.loads(r["breakdown_json"]) if r["breakdown_json"] else None,
            "cited": json.loads(r["cited_json"] or "[]"), "studies": json.loads(r["studies_json"] or "[]")}


src = sys.argv[1] if len(sys.argv) > 1 else None
rows = _rows(src) if src and os.path.exists(src) else {}
seed_rows = {c: (_as_row(rows[c]) if c in rows else _fallback_row(c, "complicated")) for c in SEED}
replay = _as_row(rows[REPLAY_FROM]) if REPLAY_FROM in rows else _fallback_row(LIVE, "true")

for claim, r in seed_rows.items():
    db.put_cached_verdict(claim, r["search_query"], r["verdict"], r["explanation"], r["cited"], r["studies"],
                          tldr=r["tldr"], still_open=r["still_open"], breakdown=r["breakdown"])

# ---- gates ------------------------------------------------------------------
gates = {n: threading.Event() for n in ("query", "found", "done")}


def _wait(name, claim):
    if claim == GATED:
        gates[name].wait(120)
    elif claim == SLOW and name == "found":
        time.sleep(300)
    else:
        time.sleep(PAUSE)


def _extract(claim):
    if claim == DOWN:
        raise verdict.ProviderError("unavailable", "The AI service is not answering right now. Try again in a minute.")
    _wait("query", claim)
    # The query is the claim itself: the app does not save that as a search
    # worth keeping, so every run asks again and the gates always apply.
    return claim, "", claim


def _search(query, max_results=8, surrogate=""):
    _wait("found", query)
    return replay["studies"], replay["search_query"] or query, False


def _weigh(claim, studies, query="", surrogate=""):
    _wait("done", claim)
    return {"verdict": replay["verdict"], "tldr": replay["tldr"], "explanation": replay["explanation"],
            "still_open": replay["still_open"], "cited_studies": replay["cited"], "breakdown": replay["breakdown"]}


verdict.extract_search_terms = _extract
pubmed.search_with_fallback = _search
verdict.weigh_evidence = _weigh
verdict.guideline_view = lambda claim: None
pubmed.deep_dive = lambda pmid: {"pmid": pmid, "cited_by": 7, "full_text_url": "https://example.org/x",
                                 "related": []}


@appmod.app.route("/__fixture/release/<name>")
def _release(name):
    gates[name].set()
    return "ok"


@appmod.app.route("/__fixture/reset")
def _reset():
    for g in gates.values():
        g.clear()
    # The replayed claims are cached once they finish; forget them so the
    # next run streams again. The seeded claims stay.
    with db.get_conn() as conn:
        for claim in (GATED, SLOW, DOWN, LIVE):
            conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(claim),))
    return "ok"


if __name__ == "__main__":
    appmod.app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")), threaded=True, debug=False)
