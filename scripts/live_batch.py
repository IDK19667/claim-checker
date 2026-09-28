"""Run varied real claims through /api/check end to end, against the live
provider in .env. Prints query, verdict, cited studies and per-call usage.

    RATE_LIMIT_GLOBAL_PER_MINUTE=100 RATE_LIMIT_PER_USER=100 VERDICT_CACHE_HOURS=0 \\
        .venv/bin/python scripts/live_batch.py [substring filters...]
"""
import sys, os, time, json
import pathlib; ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import app as appmod
import verdict

# Wrap whichever provider is live to capture usage + finish reason per call
PROV = verdict.provider()
CALLS = []
if PROV == "gemini":
    client = verdict._gemini()
    _orig = client.models.generate_content
    def _wrapped(**kw):
        t0 = time.time(); m = _orig(**kw); u = m.usage_metadata
        CALLS.append({"ms": int((time.time()-t0)*1000), "in": u.prompt_token_count or 0,
                      "out": (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0),
                      "stop": str(m.candidates[0].finish_reason).split(".")[-1] if m.candidates else "?"})
        return m
    client.models.generate_content = _wrapped
    PRICE_IN, PRICE_OUT = 0.0, 0.0  # free tier
else:
    client = verdict._anthropic()
    _orig = client.messages.create
    def _wrapped(**kw):
        t0 = time.time(); m = _orig(**kw)
        CALLS.append({"ms": int((time.time()-t0)*1000), "in": m.usage.input_tokens, "out": m.usage.output_tokens,
                      "stop": m.stop_reason})
        return m
    client.messages.create = _wrapped
    PRICE_IN, PRICE_OUT = 2.0, 10.0  # Sonnet 5 $/M
print(f"Provider: {PROV}  model: {verdict.model_name()}")

CLAIMS = [
    ("well-studied/false", "Vaccines cause autism"),
    ("well-studied/false", "Cracking your knuckles causes arthritis"),
    ("well-studied/true",  "Smoking causes lung cancer"),
    ("well-studied/true",  "Regular exercise lowers the risk of heart disease"),
    ("overclaim",          "Apple cider vinegar cures diabetes"),
    ("overclaim",          "Vitamin C prevents the common cold"),
    ("mixed",              "Intermittent fasting improves brain function"),
    ("fringe",             "5G networks weaken the immune system"),
    ("fringe",             "Detox teas flush toxins out of your body"),
    ("thin coverage",      "Drinking celery juice cures acne"),
    ("thin coverage",      "Sleeping with a fan on gives you a stiff neck"),
    ("nonsense",           "Eating moon rocks gives you superpowers"),
    ("very new",           "Ozempic cures alcoholism"),
    ("rambling",           "my aunt said if you put onions in your socks at night it draws out toxins and cures the flu is that real"),
]
only = sys.argv[1:]  # optional substring filters
c = appmod.app.test_client()
rows = []
for kind, claim in CLAIMS:
    if only and not any(o.lower() in claim.lower() for o in only):
        continue
    n0 = len(CALLS); t0 = time.time()
    r = c.post("/api/check", json={"claim": claim, })
    dt = time.time() - t0
    d = r.get_json()
    calls = CALLS[n0:]
    print("=" * 100)
    print(f"[{kind}] {claim}")
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {d}")
        rows.append((kind, claim, r.status_code, None, None, None, dt)); continue
    cited = [s for s in d["studies"] if s["cited_in_verdict"]]
    print(f"  query   : {d['search_query_used']!r}")
    print(f"  verdict : {d['verdict'].upper()}   studies={len(d['studies'])} cited={len(cited)}   {dt:.1f}s{'  (search broadened)' if d.get('search_broadened') else ''}")
    print(f"  tldr    : {d.get('tldr')}")
    print(f"  explain : {d['explanation']}")
    print(f"  open    : {d.get('still_open')}")
    for s in d["studies"]:
        tag = "*" if s["cited_in_verdict"] else " "
        print(f"   {tag} [{s['pmid']}] {s['year']} {', '.join(s['publication_types'][:2])} — {s['title'][:80]}")
    if d.get("cached"): print("  (served from cache)")
    for i, cl in enumerate(calls):
        print(f"  call{i+1}: {cl['ms']}ms in={cl['in']} out={cl['out']} stop={cl['stop']}")
    rows.append((kind, claim, d["verdict"], len(d["studies"]), len(cited), d["search_query_used"], dt))
    time.sleep(2.0)

print("\n" + "=" * 100 + "\nSUMMARY")
for kind, claim, v, ns, nc, q, dt in rows:
    print(f"  {str(v).upper():12} studies={ns!s:>4} cited={nc!s:>4} {dt:5.1f}s  [{kind}] {claim[:55]}")
tot_in = sum(c["in"] for c in CALLS); tot_out = sum(c["out"] for c in CALLS)
print(f"\nTotal Claude calls: {len(CALLS)}  input tokens: {tot_in}  output tokens: {tot_out}")
cost = tot_in*PRICE_IN/1e6 + tot_out*PRICE_OUT/1e6
print(f"Cost estimate ({PROV}): ${cost:.4f} for {len(rows)} checks (~${cost/max(len(rows),1):.4f}/check)")
