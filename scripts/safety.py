"""Run the high-stakes claims in tests/safety_claims.json through /api/check,
against the live provider in .env. Every claim must come out safe (its
expected verdict, or a pre-declared 'also_ok') before a merge. Cache off,
30 seconds between claims (free-tier pacing).

    .venv/bin/python scripts/safety.py [out.jsonl] [--gap 30] [substring filters...]

A rate limit (429) waits 2 minutes and retries, twice; a third in a row is
the daily quota, and the run stops there. Exits 0 only when every claim run
was safe.
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

spec = json.loads((ROOT / "tests" / "safety_claims.json").read_text())["claims"]
spec = [c for c in spec if not only or any(o.lower() in c["claim"].lower() for o in only)]
c = appmod.app.test_client()
fh = out.open("w") if out else None
exact = alt = harmful = ran = 0
for k, item in enumerate(spec):
    if k:
        time.sleep(gap)
    t0 = time.time()
    limited = 0
    while True:
        r = c.post("/api/check", json={"claim": item["claim"]})
        d = r.get_json() or {}
        if r.status_code == 429 and limited < 2:
            limited += 1; print(f"    429, waiting 2 minutes ({limited})", flush=True); time.sleep(120); continue
        break
    if r.status_code == 429:
        print("QUOTA: three rate limits in a row; stopping here.", flush=True)
        break
    ran += 1
    shown = d.get("verdict") if r.status_code == 200 else f"HTTP {r.status_code}"
    conflict = (breakdown.stamp_conflict(shown, d.get("tldr") or "", d.get("explanation") or "", item["claim"])
                if r.status_code == 200 else None)
    is_exact = shown == item["expected"]
    is_alt = shown in item.get("also_ok", [])
    is_harm = shown in item.get("harmful", [])
    exact += is_exact; alt += is_alt; harmful += is_harm
    rec = {**item, "shown": shown, "safe": is_exact or is_alt, "conflict": conflict,
           "seconds": round(time.time() - t0, 1), "tldr": d.get("tldr"), "explanation": d.get("explanation"),
           "still_open": d.get("still_open"), "breakdown": d.get("breakdown"),
           "query": d.get("search_query_used"),
           "studies": [{"pmid": s.get("pmid"), "year": s.get("year"), "title": s.get("title"),
                        "type_label": s.get("type_label"), "cited": s.get("cited_in_verdict"),
                        "off_topic": s.get("off_topic"), "background": s.get("background")}
                       for s in d.get("studies") or []],
           "body": None if r.status_code == 200 else d}
    if fh:
        fh.write(json.dumps(rec) + "\n"); fh.flush()
    flag = "ok " if is_exact else ("~  " if is_alt else ("HARM" if is_harm else "NO "))
    print(f"{item['n']:2}. {flag} {shown:13} want {item['expected']:13} "
          f"{'CONFLICT ' if conflict else ''}:: {item['claim']}", flush=True)
safe = exact + alt
print(f"\n{safe}/{len(spec)} safe ({exact} exact, {alt} pre-declared safe alternative), "
      f"{harmful} harmful{'' if ran == len(spec) else f', {len(spec) - ran} not run'}")
sys.exit(0 if safe == len(spec) else 1)
