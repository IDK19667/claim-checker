"""Re-run the claims in tests/regression_claims.json through /api/check,
against the live provider in .env, and score each against its expected
verdict. Cache off, 30 seconds between claims (free-tier pacing).

    .venv/bin/python scripts/regression.py [out.jsonl] [--gap 30] [substring filters...]

For each claim it reports: the verdict shown, a match, whether the stamp
contradicts its own takeaway, and any study marked off topic that was
still counted as used. Writes one JSON line per claim to out.jsonl.
"""
import sys, os, time, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("RATE_LIMIT_GLOBAL_PER_MINUTE", "100")
os.environ.setdefault("RATE_LIMIT_PER_USER", "100")
os.environ.setdefault("RATE_LIMIT_GLOBAL_PER_DAY", "1000")
os.environ["VERDICT_CACHE_HOURS"] = "0"   # always a fresh check
import app as appmod
import breakdown

args = sys.argv[1:]
gap = 30.0
if "--gap" in args:
    i = args.index("--gap"); gap = float(args[i + 1]); del args[i:i + 2]
out = pathlib.Path(args.pop(0)) if args and args[0].endswith(".jsonl") else None
only = args

spec = json.loads((ROOT / "tests" / "regression_claims.json").read_text())["claims"]
spec = [c for c in spec if not only or any(o.lower() in c["claim"].lower() for o in only)]
c = appmod.app.test_client()
fh = out.open("w") if out else None
matches = conflicts = off_used = 0
for k, item in enumerate(spec):
    if k:
        time.sleep(gap)
    t0 = time.time()
    r = c.post("/api/check", json={"claim": item["claim"]})
    d = r.get_json() or {}
    shown = d.get("verdict") if r.status_code == 200 else f"HTTP {r.status_code}"
    studies = d.get("studies") or []
    conflict = (breakdown.stamp_conflict(shown, d.get("tldr") or "", d.get("explanation") or "", item["claim"])
                if r.status_code == 200 else None)
    used_off = [s["pmid"] for s in studies if s.get("cited_in_verdict") and s.get("off_topic")]
    ok = shown == item["expected"]
    matches += ok; conflicts += bool(conflict); off_used += bool(used_off)
    rec = {**item, "shown": shown, "match": ok, "conflict": conflict, "off_topic_used": used_off,
           "seconds": round(time.time() - t0, 1), "tldr": d.get("tldr"), "explanation": d.get("explanation"),
           "still_open": d.get("still_open"), "breakdown": d.get("breakdown"),
           "query": d.get("search_query_used"),
           "studies": [{"pmid": s.get("pmid"), "year": s.get("year"), "title": s.get("title"),
                        "type_label": s.get("type_label"), "cited": s.get("cited_in_verdict"),
                        "off_topic": s.get("off_topic")} for s in studies],
           "body": None if r.status_code == 200 else d}
    if fh:
        fh.write(json.dumps(rec) + "\n"); fh.flush()
    flag = "ok " if ok else ("~  " if shown in item.get("also_ok", []) else "NO ")
    print(f"{item['n']:2}. {flag} {shown:13} want {item['expected']:13} "
          f"{'CONFLICT ' if conflict else ''}{'OFF-TOPIC-USED ' if used_off else ''}:: {item['claim']}",
          flush=True)
print(f"\n{matches}/{len(spec)} match, {conflicts} stamp/text conflicts, {off_used} with off-topic studies used")
