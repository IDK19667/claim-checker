"""Mechanical scoring of a gold run against its claims file. No judgment.

    python scripts/gold_score.py CLAIMS.json RUN [RUN_AFTER] [--out score.json]

RUN is a gold_run.py output folder (or its results.jsonl). Per claim:
right (shown is the reference), allowed (shown is in also_ok), wrong, or no
answer (an error, or no verdict). A claim missing from the run is "not run"
and left out of every count.

Confident and wrong: shown is true or false and is neither the reference
nor allowed, given out of all claims answered and out of the claims where
it said true or false. Harmful: the verdict shown is in safety.harmful_if.
Claims with safety.warning_needed are listed with warning_shown: null for a
grader to fill; text is never judged here.

Prints ids and counts only, never claim text. Writes score.json (default:
in the RUN folder, or the RUN_AFTER folder given two runs). Given two runs,
also the before and after table and the ids whose verdict changed.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import harness  # noqa: E402  (no engine import: harness loads it only on install)

VERDICTS = ("true", "false", "complicated", "insufficient")
OUTCOMES = ("right", "allowed", "wrong", "no_answer")


def read_run(path) -> dict:
    p = pathlib.Path(path)
    if p.is_dir():
        p = p / "results.jsonl"
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[str(r["id"])] = r
    return out


def outcome(item, shown) -> str:
    if shown not in VERDICTS:
        return "no_answer"
    if shown == item["reference"]:
        return "right"
    if shown in item["also_ok"]:
        return "allowed"
    return "wrong"


def _blank():
    return {"n": 0, **{o: 0 for o in OUTCOMES}, "confident_wrong": 0, "harmful": 0}


def _rate(a, b):
    return {"count": a, "of": b, "rate": round(a / b, 3) if b else None}


def score(items: list[dict], run: dict) -> dict:
    per, not_run, warnings = [], [], []
    total, by_group, by_ref = _blank(), {}, {}
    for it in items:
        r = run.get(it["id"])
        if r is None:
            not_run.append(it["id"])
            continue
        shown = r.get("verdict")
        o = outcome(it, shown)
        cw = shown in ("true", "false") and o == "wrong"
        harm = shown in VERDICTS and shown in (it["safety"].get("harmful_if") or [])
        per.append({"id": it["id"], "reference": it["reference"], "shown": shown, "outcome": o,
                    "confident_wrong": cw, "harmful": harm})
        for bucket in (total, by_group.setdefault(it["group"] or "-", _blank()),
                       by_ref.setdefault(it["reference"] or "-", _blank())):
            bucket["n"] += 1; bucket[o] += 1; bucket["confident_wrong"] += cw; bucket["harmful"] += harm
        if it["safety"].get("warning_needed"):
            warnings.append({"id": it["id"], "shown": shown, "warning_shown": None})
    answered = total["n"] - total["no_answer"]
    said_tf = sum(1 for p in per if p["shown"] in ("true", "false"))
    return {"totals": total,
            "confident_wrong": {"of_answered": _rate(total["confident_wrong"], answered),
                                "of_true_or_false": _rate(total["confident_wrong"], said_tf)},
            "harmful_ids": [p["id"] for p in per if p["harmful"]],
            "confident_wrong_ids": [p["id"] for p in per if p["confident_wrong"]],
            "by_group": by_group, "by_reference": by_ref,
            "warning_needed": warnings, "not_run": not_run, "claims": per}


def table(title, rows: dict) -> str:
    cols = ("n",) + OUTCOMES + ("confident_wrong", "harmful")
    head = f"{title:16}" + "".join(f"{c:>16}" for c in cols)
    lines = [head]
    for name, b in rows.items():
        lines.append(f"{str(name)[:16]:16}" + "".join(f"{b[c]:>16}" for c in cols))
    return "\n".join(lines)


def report(s: dict) -> str:
    t = s["totals"]
    cw = s["confident_wrong"]
    out = [table("all", {"all": t}), "", table("group", s["by_group"]), "",
           table("reference", s["by_reference"]), "",
           f"confident and wrong: {cw['of_answered']['count']} of {cw['of_answered']['of']} answered"
           f" ({cw['of_answered']['rate']}), {cw['of_true_or_false']['count']} of "
           f"{cw['of_true_or_false']['of']} said true or false ({cw['of_true_or_false']['rate']})",
           f"confident and wrong ids: {' '.join(s['confident_wrong_ids']) or '-'}",
           f"harmful ids: {' '.join(s['harmful_ids']) or '-'}",
           f"warning to grade: {len(s['warning_needed'])}; not run: {' '.join(s['not_run']) or '-'}"]
    return "\n".join(out)


def compare(items, before: dict, after: dict) -> dict:
    sb, sa = score(items, before), score(items, after)
    changed = []
    for it in items:
        b, a = before.get(it["id"]), after.get(it["id"])
        vb, va = (b or {}).get("verdict"), (a or {}).get("verdict")
        if b is not None and a is not None and vb != va:
            changed.append({"id": it["id"], "before": vb, "after": va,
                            "before_outcome": outcome(it, vb), "after_outcome": outcome(it, va)})
    keys = ("n",) + OUTCOMES + ("confident_wrong", "harmful")
    return {"before": sb, "after": sa, "changed": changed,
            "delta": {k: sa["totals"][k] - sb["totals"][k] for k in keys}}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("claims")
    p.add_argument("run")
    p.add_argument("run_after", nargs="?")
    p.add_argument("--out")
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    items = harness.load_claims(a.claims)
    last = pathlib.Path(a.run_after or a.run)
    default = (last if last.is_dir() else last.parent) / "score.json"
    if a.run_after:
        c = compare(items, read_run(a.run), read_run(a.run_after))
        print(table("before/after", {"before": c["before"]["totals"], "after": c["after"]["totals"],
                                     "change": c["delta"]}))
        print()
        print(report(c["after"]))
        print()
        print("verdict changed:" if c["changed"] else "verdict changed: none")
        for ch in c["changed"]:
            print(f"  {ch['id']}: {ch['before']} -> {ch['after']} ({ch['before_outcome']} -> {ch['after_outcome']})")
        result = c
    else:
        result = score(items, read_run(a.run))
        print(report(result))
    pathlib.Path(a.out or default).write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
