"""Compare two gold_run.py result folders: are the answers the same, were
the same requests made, and how long did each side take.

    python scripts/replay_compare.py A_DIR B_DIR [--quiet]

Reads A_DIR/results.jsonl and B_DIR/results.jsonl (one line per claim, as
gold_run.py writes them) and prints:
  - how many claims are byte-identical apart from timing fields: the final
    response, the status, the verdict shown and every stream event with its
    seconds since the start ("t") and since the event before ("took") left
    out; and which keys differ where they are not;
  - the model and PubMed request counts per claim on each side (logical
    model calls, PubMed requests) and every claim where they differ, plus
    the model tries in total (a live recording counts the tries a 429 or a
    5xx cost, which a replay of the saved answers never repeats, so tries
    are shown and not compared); the replay misses on each side;
  - the wall time, median and range, overall and per stage: "wall" is the
    whole request as the runner measured it, "done at" when the answer
    reached the reader (the last event), and each stage's "took".

Exit code 0 only when every claim in A is in B, all are identical, and the
request counts all match.
"""
import argparse
import json
import pathlib
import statistics
import sys

TIMING = ("t", "took")
STAGES = ("search", "query", "found", "weigh", "done")


def load(folder) -> dict:
    path = pathlib.Path(folder) / "results.jsonl"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["id"]] = r
    return out


def comparable(r: dict) -> dict:
    """The parts of one result that must not change, timing left out."""
    return {"http_status": r.get("http_status"), "status": r.get("status"),
            "verdict": r.get("verdict"), "response": r.get("response"),
            "events": [{k: v for k, v in e.items() if k not in TIMING} for e in r.get("events") or []]}


def _dump(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def differing_keys(a: dict, b: dict) -> list:
    ca, cb = comparable(a), comparable(b)
    keys = []
    for k in ca:
        if _dump(ca[k]) == _dump(cb[k]):
            continue
        if k == "response" and isinstance(ca[k], dict) and isinstance(cb[k], dict):
            keys += [f"response.{f}" for f in sorted(set(ca[k]) | set(cb[k]))
                     if _dump(ca[k].get(f)) != _dump(cb[k].get(f))]
        else:
            keys.append(k)
    return keys


def counts(r: dict) -> tuple:
    """(logical model calls, PubMed requests): what must match."""
    h = r.get("harness") or {}
    return (h.get("model_calls"), h.get("pubmed_requests"))


def tries(r: dict) -> int:
    return (r.get("harness") or {}).get("model_tries") or 0


def spread(xs) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    return f"median {statistics.median(xs):.2f}s ({min(xs):.2f} to {max(xs):.2f}, n {len(xs)})"


def stage_took(r: dict, stage: str):
    return next((e.get("took") for e in r.get("events") or [] if e.get("stage") == stage), None)


def done_at(r: dict):
    ev = r.get("events") or []
    return ev[-1].get("t") if ev else None


def compare(a: dict, b: dict) -> dict:
    ids = list(a)
    missing = [k for k in ids if k not in b]
    both = [k for k in ids if k in b]
    same, diff, count_diff = [], {}, {}
    for k in both:
        keys = differing_keys(a[k], b[k])
        if keys:
            diff[k] = keys
        else:
            same.append(k)
        if counts(a[k]) != counts(b[k]):
            count_diff[k] = (counts(a[k]), counts(b[k]))
    misses = [sum(len((r.get("harness") or {}).get("misses") or []) for r in side.values())
              for side in (a, b)]
    return {"ids": ids, "missing": missing, "same": same, "diff": diff,
            "count_diff": count_diff, "misses": misses}


def timing_lines(name: str, rs: list) -> list:
    out = [f"{name}: wall {spread([r.get('wall') for r in rs])}",
           f"{name}: done at {spread([done_at(r) for r in rs])}"]
    for s in STAGES:
        out.append(f"{name}:   {s} took {spread([stage_took(r, s) for r in rs])}")
    return out


def report(a_dir, b_dir, quiet=False) -> tuple[int, str]:
    a, b = load(a_dir), load(b_dir)
    c = compare(a, b)
    gone = f", {len(c['missing'])} missing from B" if c["missing"] else ""
    lines = [f"A {a_dir}", f"B {b_dir}",
             f"identical apart from timing: {len(c['same'])} of {len(c['ids'])}{gone}"]
    keys = sorted({k for ks in c["diff"].values() for k in ks})
    lines.append(f"differing keys: {keys if keys else 'none'}")
    for k, ks in c["diff"].items():
        lines.append(f"  differs {k}: {ks}")
    tot = lambda side, i: sum((counts(side[k])[i] or 0) for k in c["ids"] if k in side)  # noqa: E731
    ntries = lambda side: sum(tries(side[k]) for k in c["ids"] if k in side)  # noqa: E731
    lines.append(f"requests, A: model calls {tot(a, 0)}, pubmed {tot(a, 1)} (model tries {ntries(a)}); "
                 f"B: model calls {tot(b, 0)}, pubmed {tot(b, 1)} (model tries {ntries(b)})")
    lines.append(f"claims whose request counts differ: {len(c['count_diff'])}")
    for k, (ca, cb) in c["count_diff"].items():
        lines.append(f"  counts {k}: A {ca} B {cb} (model calls, pubmed)")
    if not quiet:
        lines.append("per claim (model calls, pubmed) A | B:")
        for k in c["ids"]:
            if k in b:
                lines.append(f"  {k}: {counts(a[k])} | {counts(b[k])}")
    lines.append(f"misses: A {c['misses'][0]}, B {c['misses'][1]}")
    common = [k for k in c["ids"] if k in b]
    lines += timing_lines("A", [a[k] for k in common]) + timing_lines("B", [b[k] for k in common])
    ok = not c["missing"] and not c["diff"] and not c["count_diff"]
    return (0 if ok else 1), "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--quiet", action="store_true", help="leave out the per-claim count table")
    args = p.parse_args(argv)
    code, text = report(args.a, args.b, args.quiet)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
