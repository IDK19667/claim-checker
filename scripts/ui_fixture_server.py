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
  BUSY   "Provider busy claim"          the provider answers rate limited (429, retry_after 20)
  PUBMED "PubMed down claim"            PubMed cannot be reached (502)
  NONE   "No evidence claim"            PubMed returns no studies (an insufficient verdict)
  DECLINED "Declined claim"             the model declines the reply (502, way_out "reword")
  CUT    "Cut off claim"                the model's reply is cut off (502, way_out "either")
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

# The per-study lines a saved fixture study may carry (contribution, caution): laid over what the engine works out
# (app._study_extra), so a row can be measured with them. Never indirect: the engine sends indirect false and an empty
# reason for every study in this release, and the fixture keeps to that.
_PER_STUDY = ("contribution", "caution")
_extra = appmod._study_extra
appmod._study_extra = lambda s, cited: dict(_extra(s, cited), **{k: s[k] for k in _PER_STUDY if k in s})
import db  # noqa: E402
import pubmed  # noqa: E402
import ratelimit  # noqa: E402
import verdict  # noqa: E402

GATED = "Gated claim for stage test"
SLOW = "Slow claim for cancel test"
DOWN = "Provider down claim"
BUSY = "Provider busy claim"
PUBMED = "PubMed down claim"
NONE = "No evidence claim"
DECLINED = "Declined claim"
CUT = "Cut off claim"
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
    # app.EXAMPLE_CLAIM: the home page's real example is read from the saved answers.
    "Apple cider vinegar cures diabetes",
]
REPLAY_FROM = "Taking vitamin D supplements in winter cuts your risk of catching a cold or flu, but only if you're deficient to begin with"


def _rows(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {r["claim_text"]: r for r in con.execute("SELECT * FROM verdict_cache")}
    return out


def _fallback_row(claim, verdict_word):
    # Real studies name what they test, and a typed claim is listed publicly only when the studies its check read
    # vouch for its words (display.public_claim), so a fallback row's abstracts name the claim.
    studies = [{"pmid": str(1000 + i), "title": f"A trial of the claim, part {i}",
                "abstract": f"Background. {claim}. Methods. Results. Conclusions.",
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

# The desk front's saved results: the sample's own 16-study check ("Creatine
# improves cognitive performance"), results with 8, 5, 3 and no studies, and
# one with markup in every field (scripts/fixtures/desk_saved.json).
DESK_SAVED = json.loads((ROOT / "scripts" / "fixtures" / "desk_saved.json").read_text())["rows"]
for r in DESK_SAVED:
    db.put_cached_verdict(r["claim"], r["search_query"], r["verdict"], r["explanation"], r["cited"], r["studies"],
                          tldr=r["tldr"], still_open=r["still_open"], breakdown=r["breakdown"])

# The recorded vinegar answer (tests/vinegar_recorded.json), as a saved answer: study 1 is a review whose reference
# list names a retracted trial (flagged as a live check flags it, with no lookup), so its row carries the engine's
# own caution; the claim is absolute and rated false, so the page has both statements under How sure.
import copy  # noqa: E402
import evidence  # noqa: E402
VINEGAR = json.loads((ROOT / "tests" / "vinegar_recorded.json").read_text())
_vin_st = copy.deepcopy(VINEGAR["studies"])
_vin_st[0]["references"] = ["38966098"]
evidence.flag_retracted_citations(_vin_st, lambda ids: set())
db.put_cached_verdict(VINEGAR["claim"], VINEGAR["search_query_used"], VINEGAR["verdict"], VINEGAR["explanation"],
                      [s["pmid"] for s in _vin_st if s.get("cited_in_verdict")], _vin_st, tldr=VINEGAR["tldr"],
                      still_open=VINEGAR["still_open"], breakdown=VINEGAR["breakdown"])

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
    if claim == BUSY:
        raise verdict.ProviderError("rate_limit", "The AI service is busy.")
    if claim == PUBMED:
        raise appmod.requests.RequestException("fixture: PubMed unreachable")
    if claim == DOWN:
        raise verdict.ProviderError("unavailable", "The AI service is not answering right now. Try again in a minute.")
    _wait("query", claim)
    # The query is the claim itself: the app does not save that as a search
    # worth keeping, so every run asks again and the gates always apply.
    return claim, "", claim


def _search(query, max_results=8, surrogate=""):
    _wait("found", query)
    if query == NONE:
        return [], query, False
    return replay["studies"], replay["search_query"] or query, False


def _weigh(claim, studies, query="", surrogate=""):
    _wait("done", claim)
    # The two replies weigh_evidence cannot read, raised as it raises them.
    for which, why in ((DECLINED, verdict.DECLINED), (CUT, verdict.CUT_OFF)):
        if claim == which:
            raise verdict.ProviderError("other", verdict.unread_reply(why), verdict.way_out(why))
    if not studies:
        return {"verdict": "insufficient", "tldr": "No published studies on this yet. Unproven, not disproven.",
                "explanation": "No relevant studies were found on PubMed for this claim. That doesn't mean the claim is false, it may just not be well studied yet, or the search terms need refining.",
                "still_open": "Whether anyone has tested this claim directly. PubMed found nothing that does.",
                "cited_studies": [], "breakdown": None}
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


# A slow or sleeping server, for the service worker's time limit on the home
# page: /__fixture/hold/10 makes every request for "/" wait 10 s until reset.
hold = {"s": 0.0, "status": 0}


@appmod.app.route("/__fixture/hold/<secs>")
def _hold(secs):
    hold["s"] = float(secs)
    return "ok"


# A server that answers the home page badly: /__fixture/status/503 makes "/"
# answer 503 (or 429, ...) until reset; /__fixture/status/0 ends it.
@appmod.app.route("/__fixture/status/<int:code>")
def _status(code):
    hold["status"] = code
    return "ok"


# The live site's service worker, for the update path: /__fixture/oldsw/1
# makes /sw.js answer the worker of commit f10ef54 (cache "evident-v118"),
# kept as scripts/fixtures/sw-f10ef54.js (an export has no git), until reset
# or /__fixture/oldsw/0.
OLD_SW = ROOT / "scripts" / "fixtures" / "sw-f10ef54.js"


@appmod.app.route("/__fixture/oldsw/<int:on>")
def _oldsw(on):
    if on and "old_sw" not in hold:
        hold["old_sw"] = OLD_SW.read_text()
    hold["oldsw"] = on
    return "ok"


@appmod.app.before_request
def _held():
    if hold.get("oldsw") and appmod.request.path == "/sw.js":
        return appmod.Response(hold["old_sw"], mimetype="text/javascript", headers={"Cache-Control": "no-cache"})
    if hold["s"] and appmod.request.path == "/":
        time.sleep(hold["s"])
    if hold["status"] and appmod.request.path == "/":
        # a 403 stands for a Cloudflare challenge: a page the reader has to see
        body = "Challenge page: pass this check to go on" if hold["status"] == 403 else "Waking up"
        return appmod.Response(body, status=hold["status"], mimetype="text/plain")


@appmod.app.route("/__fixture/reset")
def _reset():
    for g in gates.values():
        g.clear()
    # The replayed claims are cached once they finish; forget them so the
    # next run streams again. The seeded claims stay.
    with db.get_conn() as conn:
        for claim in (GATED, SLOW, DOWN, BUSY, PUBMED, NONE, LIVE, DECLINED, CUT):
            conn.execute("DELETE FROM verdict_cache WHERE claim_key = ?", (db.normalize_claim(claim),))
    hold["s"] = 0.0
    hold["status"] = 0
    hold["oldsw"] = 0
    appmod._drop_home_lists()  # those rows went without a save, so the home page's copy goes too
    return "ok"


if __name__ == "__main__":
    appmod.app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")), threaded=True, debug=False)
