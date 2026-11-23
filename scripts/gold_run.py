"""Run a claims file through /api/check/stream on the Flask test client,
under the record and replay harness (scripts/harness.py), one JSON line per
claim, and say where the time went.

    python scripts/gold_run.py CLAIMS.json --out DIR [--mode fill] [--store DIR]
        [--gap 30] [--latency] [--limit N] [--only ID ...] [--resume]
        [--quiet-claims] [--env-file PATH]

CLAIMS.json: a gold file (a list of items, or an object with `claims`), or
tests/regression_claims.json or tests/safety_claims.json.

Writes DIR/results.jsonl (id, claim, status, verdict shown, the whole final
response, every stream event with its seconds since the start, the harness
numbers, the wall time) and DIR/summary.json (median and range of the wall
time, of each stage, of model calls, tokens and PubMed requests; errors).

Modes (see harness.py): live, record, replay, fill (the default: replay
what is saved, call out only for what is not). --store defaults to
DIR/store. --gap defaults to 30 seconds after a claim that called out, 0 in
replay.

Each claim runs on a fresh scratch database (DB_PATH, a file under DIR,
emptied before every claim), so its answer does not depend on which claims
ran before it, and --only and --resume give the answers a full run gives.

Every 429 try is logged to DIR/quota.jsonl with what its body says about
the quota (see harness.quota_info), and summary.json lists each quota seen
(quota_seen). A 429 that names the daily limit stops at once with QUOTA.
Any other 429 waits 2 minutes and tries the claim again, twice. A third in
a row is taken as the daily quota: one line starting QUOTA, what is done is
kept, exit code 3; --resume carries on. A replay miss is printed (MISS, the
call kind and key) and the run exits with code 4.

Safety: before the engine is imported the Turso settings and the Anthropic
and Mistral keys are set empty, LLM_PROVIDER=gemini, the verdict cache is off
and the local rate limits are lifted. --env-file is loaded with python-dotenv
without override, so those blanks win; no value from it is ever printed.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAGES = ("search", "query", "found", "weigh", "done")
QUOTA_WAIT = 120
QUOTA_RETRIES = 2


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("claims")
    p.add_argument("--mode", default="fill", choices=("live", "record", "replay", "fill"))
    p.add_argument("--store")
    p.add_argument("--out", required=True)
    p.add_argument("--gap", type=float)
    p.add_argument("--latency", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--only", nargs="+")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--quiet-claims", action="store_true")
    p.add_argument("--env-file")
    a = p.parse_args(argv)
    if a.gap is None:
        a.gap = 0.0 if a.mode == "replay" else 30.0
    return a


def safe_env(out: pathlib.Path, env_file=None) -> pathlib.Path:
    """Blank everything that could reach a live database or a paid model,
    before the engine is imported. Returns the scratch database path."""
    os.environ["TURSO_DATABASE_URL"] = ""
    os.environ["TURSO_AUTH_TOKEN"] = ""
    db_path = os.environ.get("DB_PATH")
    if not db_path:
        db_path = str(out / "scratch.db")
        os.environ["DB_PATH"] = db_path
    p = pathlib.Path(db_path).resolve()
    if out.resolve() not in p.parents or p.is_dir():
        raise SystemExit(f"DB_PATH must be a file under --out ({out}); it is {db_path}")
    for k in ("ANTHROPIC_API_KEY", "MISTRAL_API_KEY"):
        os.environ[k] = ""
    os.environ["LLM_PROVIDER"] = "gemini"
    os.environ["VERDICT_CACHE_HOURS"] = "0"
    os.environ["RATE_LIMIT_GLOBAL_PER_MINUTE"] = "100000"
    os.environ["RATE_LIMIT_PER_USER"] = "100000"
    os.environ["RATE_LIMIT_GLOBAL_PER_DAY"] = "100000"
    if env_file:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    return pathlib.Path(db_path)


def fresh_db(db, db_path: pathlib.Path):
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.unlink(str(db_path) + suffix)
        except FileNotFoundError:
            pass
    db.init_db()


def read_stream(resp, t0):
    """Each SSE event, stamped with the seconds since t0 on arrival."""
    events, buf = [], ""
    for chunk in resp.response:
        buf += chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            for line in block.split("\n"):
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    ev["t"] = round(time.perf_counter() - t0, 3)
                    events.append(ev)
    resp.close()
    return events


def quota_words(seen: list) -> str:
    """'per day, limit 20' from the 429s of one try, or '' when they say nothing."""
    if not seen:
        return ""
    last = seen[-1]
    qs = last.get("quotas") or []
    pick = next((q for q in qs if q.get("kind") == last.get("kind")), qs[0] if qs else {})
    bits = []
    if last.get("kind") in ("day", "minute"):
        bits.append(f"per {last['kind']}")
    if pick.get("quotaValue") is not None:
        bits.append(f"limit {pick['quotaValue']}")
    if last.get("retry_delay"):
        bits.append(f"retry after {last['retry_delay']}")
    return ", ".join(bits)


def run_one(client, db, db_path, h, item, mode):
    """One claim, with the 429 waits. Returns (record, quota_hit, what the
    last 429 said). A 429 that names the daily limit stops at once."""
    limited = 0
    while True:
        fresh_db(db, db_path)
        h.reset()
        t0 = time.perf_counter()
        resp = client.post("/api/check/stream", json={"claim": item["claim"]}, buffered=False)
        events = read_stream(resp, t0)
        wall = round(time.perf_counter() - t0, 3)
        final = events[-1] if events else {"stage": "error", "status": None, "error": "no events"}
        if final.get("stage") == "error" and final.get("status") == 429 and mode != "replay":
            seen = h.snapshot()["quota"]
            words = quota_words(seen)
            daily = any(q.get("kind") == "day" for q in seen)
            if limited < QUOTA_RETRIES and not daily:
                limited += 1
                print(f"    429{' (' + words + ')' if words else ''}, waiting {QUOTA_WAIT // 60} minutes "
                      f"({limited})", flush=True)
                time.sleep(QUOTA_WAIT)
                continue
            return None, True, words
        break
    done = final.get("stage") == "done"
    result = final.get("result") if done else {k: v for k, v in final.items() if k != "t"}
    shown = (result or {}).get("verdict") if done else None
    timeline, prev = [], 0.0
    for ev in events:
        e = {k: v for k, v in ev.items() if k != "result"}
        e["took"] = round(ev["t"] - prev, 3)
        prev = ev["t"]
        timeline.append(e)
    return {"id": item["id"], "claim": item["claim"], "http_status": resp.status_code,
            "status": 200 if done else final.get("status"), "verdict": shown,
            "response": result, "events": timeline, "harness": h.snapshot(),
            "wall": wall, "tries_429": limited, "mode": mode}, False, ""


def _spread(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"median": round(statistics.median(xs), 3), "min": round(min(xs), 3),
            "max": round(max(xs), 3), "n": len(xs)}


def quota_seen(path: pathlib.Path) -> list:
    """Each distinct quota the run's 429s named, with how often."""
    out = {}
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        info = json.loads(line)
        qs = info.get("quotas") or [{}]
        for q in qs:
            k = (q.get("kind") or info.get("kind"), q.get("quotaId"), q.get("quotaValue"))
            row = out.setdefault(k, {"kind": k[0], "quota_id": k[1], "limit": k[2], "count": 0,
                                     "model": info.get("model"), "last": None})
            row["count"] += 1
            row["last"] = info.get("time")
    return list(out.values())


def summarize(lines: list[dict]) -> dict:
    stages = {}
    for s in STAGES:
        at = [next((e["t"] for e in r["events"] if e.get("stage") == s), None) for r in lines]
        took = [next((e["took"] for e in r["events"] if e.get("stage") == s), None) for r in lines]
        stages[s] = {"at": _spread(at), "took": _spread(took)}
    H = lambda k: [r["harness"].get(k) for r in lines]  # noqa: E731
    return {
        "claims": len(lines),
        "answered": sum(1 for r in lines if r["verdict"]),
        "errors": sum(1 for r in lines if not r["verdict"] or r["harness"].get("misses")),
        "misses": sum(len(r["harness"].get("misses") or []) for r in lines),
        "wall": _spread([r["wall"] for r in lines]),
        "stages": stages,
        "model_calls": _spread(H("model_calls")), "model_tries": _spread(H("model_tries")),
        "model_live": _spread(H("model_live")),
        "tokens_in": _spread(H("tokens_in")), "tokens_out": _spread(H("tokens_out")),
        "model_ms": _spread(H("model_ms")),
        "pubmed_requests": _spread(H("pubmed_requests")), "pubmed_live": _spread(H("pubmed_live")),
        "pubmed_ms": _spread(H("pubmed_ms")),
    }


def main(argv=None):
    a = parse(sys.argv[1:] if argv is None else argv)
    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    db_path = safe_env(out, a.env_file)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    os.chdir(ROOT)

    import db
    assert db._turso_config() is None
    assert pathlib.Path(db.DB_PATH).resolve() == db_path.resolve()
    db.COUNT_FLUSH_SECONDS = 0          # no background writer on a file emptied per claim

    import harness
    items = harness.load_claims(a.claims)
    if a.only:
        want = set(a.only)
        items = [c for c in items if c["id"] in want]
    results = out / "results.jsonl"
    done_ids = set()
    if results.exists() and results.stat().st_size:
        if not a.resume:
            raise SystemExit(f"{results} exists: pass --resume, or use another --out")
        done_ids = {json.loads(l)["id"] for l in results.read_text().splitlines() if l.strip()}
    items = [c for c in items if c["id"] not in done_ids]
    if a.limit is not None:
        items = items[:a.limit]

    import logging
    import app as appmod
    import verdict
    appmod.app.logger.setLevel(logging.ERROR)
    h = harness.install(a.mode, a.store or str(out / "store"), latency=a.latency,
                        quota_log=str(out / "quota.jsonl"))
    print(f"model {verdict.model_name()}, mode {a.mode}, {len(items)} claims"
          f"{f', {len(done_ids)} already done' if done_ids else ''}", flush=True)

    client = appmod.app.test_client()
    quota = False
    with results.open("a", encoding="utf-8") as fh:
        for k, item in enumerate(items):
            rec, quota, words = run_one(client, db, db_path, h, item, a.mode)
            if quota:
                why = "the daily limit" if "per day" in words else "three rate limits in a row"
                print(f"QUOTA: {why} at {item['id']}{' (' + words + ')' if words else ''}; stopping. "
                      f"{k} claims written this run; carry on with --resume.", flush=True)
                break
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            n = rec["harness"]
            text = "" if a.quiet_claims else f" :: {item['claim']}"
            print(f"{item['id']} {rec['verdict'] or 'HTTP ' + str(rec['status'])} {rec['wall']}s "
                  f"model {n['model_calls']} ({n['model_live']} live) pubmed {n['pubmed_requests']} "
                  f"({n['pubmed_live']} live){text}", flush=True)
            for m in n["misses"]:
                print(f"MISS {item['id']} {m['kind']} key {m['key']} ({m['detail']})", flush=True)
            called_out = n["model_live"] or n["pubmed_live"]
            if k < len(items) - 1 and a.gap and called_out:
                time.sleep(a.gap)

    lines = [json.loads(l) for l in results.read_text().splitlines() if l.strip()]
    summary = summarize(lines)
    summary.update({"mode": a.mode, "model": verdict.model_name(), "quota_stop": quota,
                    "quota_seen": quota_seen(out / "quota.jsonl")})
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"{summary['answered']}/{summary['claims']} answered, {summary['errors']} errors, "
          f"wall median {summary['wall']['median'] if summary['wall'] else '-'}s", flush=True)
    h.uninstall()
    if quota:
        return 3
    if h.misses:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
