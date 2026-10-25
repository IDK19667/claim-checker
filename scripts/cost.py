"""What one check costs: AI calls, tokens, seconds and dollars, per model.

Runs five fixed claims from tests/regression_claims.json through /api/check
(Flask test client), against the live Gemini key in .env, with a scratch
database, the verdict cache off, and 30 seconds between checks. Prints each
check, then the average per check, then how many checks a day the free tier
allows with the models in use.

    .venv/bin/python scripts/cost.py [--gap 30] [--json out.json]

The models come from the environment, so the same script measures both
setups:

    GEMINI_VERDICT_MODEL=gemini-3.5-flash-lite .venv/bin/python scripts/cost.py   # before: all flash-lite
    .venv/bin/python scripts/cost.py                                               # after: the split

Only these five real checks are made. A 429 waits two minutes and tries the
check again; three 429s in a row stop the run (what finished is still
reported). The verdict model's fallback to the query model is turned off
here, so a busy verdict model shows up as a 429 and a wait, never as a
quietly mixed run. Gemini only: no other provider key is read.
"""
import json
import logging
import os
import pathlib
import sys
import tempfile
import time
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Five claims, fixed, so a before and an after are the same work: a supported
# claim, a refuted one, one with nothing to test, a mixed one with many
# studies, and a confounded one (claim numbers in tests/regression_claims.json).
CLAIM_NUMBERS = (2, 8, 10, 11, 14)

RATE_LIMIT_WAIT = 120      # seconds to wait after a 429
RATE_LIMITS_IN_A_ROW = 3   # this many 429s in a row stop the run

# ---------------------------------------------------------------------
# Paid prices, USD per 1M tokens, standard tier (not batch, flex or
# priority). Output includes thinking tokens: Google bills them as output.
# Source: https://ai.google.dev/gemini-api/docs/pricing  (read 2026-10-24)
# Each model lists (last day the price applies or None, input, output), in
# order. gemini-3.6-flash and gemini-3.8-flash double on 2027-01-01.
# gemini-3.5-flash and gemini-3.7-flash are not on that page, so they have
# no price here and their cost prints as n/a.
# ---------------------------------------------------------------------
PRICES = {
    "gemini-3.5-flash-lite": [(None, 0.30, 2.50)],
    "gemini-3.6-flash": [(date(2026, 12, 31), 0.75, 3.75), (None, 1.50, 7.50)],
    "gemini-3.8-flash": [(date(2026, 12, 31), 0.75, 3.75), (None, 1.50, 7.50)],
    "gemini-3-flash-preview": [(None, 0.50, 3.00)],
    "gemini-3.1-pro-preview": [(None, 2.00, 12.00)],  # prompts up to 200k tokens
}

# ---------------------------------------------------------------------
# Free-tier limits per project and per model: (requests per minute,
# requests per day). Google no longer publishes these numbers; its rate
# limit page (https://ai.google.dev/gemini-api/docs/rate-limits, read
# 2026-10-24) says to read them in AI Studio. The figures below were
# measured by a third party from the quota values inside a 429 answer on
# 2026-09-02 (https://dev.to/romeroyang/geminis-free-tier-measured-20-requests-a-day-and-google-no-longer-publishes-the-number-4gf2)
# and agree with a Google AI Developers Forum thread of 2026-09-03 that
# quotes 20 a day for gemini-3.8-flash and 500 for flash-lite. Pro models
# answered 429 with "limit: 0" to this project's key on 2026-10-24.
# ---------------------------------------------------------------------
FREE_TIER = {
    "gemini-3.5-flash-lite": (15, 500),
    "gemini-3.5-flash": (5, 20),
    "gemini-3.6-flash": (5, 20),
    "gemini-3.7-flash": (5, 20),
    "gemini-3.8-flash": (5, 20),
    "gemini-3-flash-preview": (5, 20),
    "gemini-3.1-pro-preview": (0, 0),
}


def price_for(model: str, today: date | None = None):
    """(input, output) USD per 1M tokens on `today`, or None when unlisted."""
    today = today or date.today()
    for last_day, p_in, p_out in PRICES.get(model, []):
        if last_day is None or today <= last_day:
            return p_in, p_out
    return None


def cost_usd(model: str, prompt: int, output: int, thinking: int, today: date | None = None):
    """Dollars for these tokens, or None when the model has no listed price."""
    p = price_for(model, today)
    if not p:
        return None
    return (prompt * p[0] + (output + thinking) * p[1]) / 1_000_000


# ---------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------

class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows, self.notes = [], []

    def emit(self, record):
        row = getattr(record, "usage", None)
        if row:
            self.rows.append(row)
        elif record.name == "evident.models":
            self.notes.append((record.levelno, record.getMessage()))


class StoppedByRateLimit(Exception):
    pass


def claims_from_file(numbers=CLAIM_NUMBERS):
    spec = json.loads((ROOT / "tests" / "regression_claims.json").read_text())["claims"]
    by_n = {c["n"]: c for c in spec}
    return [(n, by_n[n]["claim"], by_n[n]["expected"]) for n in numbers]


def summed(rows, today=None):
    """Per model: calls, prompt, output, thinking, call seconds, slowest call, dollars."""
    out = {}
    for r in rows:
        m = out.setdefault(r["model"], {"calls": 0, "prompt_tokens": 0, "output_tokens": 0,
                                        "thinking_tokens": 0, "seconds": 0.0, "slowest": 0.0})
        m["calls"] += 1
        for k in ("prompt_tokens", "output_tokens", "thinking_tokens", "seconds"):
            m[k] += r[k]
        m["slowest"] = max(m["slowest"], r["seconds"])
    for name, m in out.items():
        m["usd"] = cost_usd(name, m["prompt_tokens"], m["output_tokens"], m["thinking_tokens"], today)
    return out


def measure(client, claims, gap=30, sleep=time.sleep, clock=time.monotonic, today=None, say=print):
    """Run each (n, claim, expected) through /api/check; return one record per
    check. Raises StoppedByRateLimit (carrying the records so far) after three
    429s in a row. `sleep` and `clock` are arguments so a test can run it fast."""
    collect = _Collect()
    loggers = [logging.getLogger("evident.usage"), logging.getLogger("evident.models")]
    saved = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(collect)
        lg.setLevel(logging.INFO)
    records, in_a_row = [], 0
    try:
        for k, (n, claim, expected) in enumerate(claims):
            if k:
                sleep(gap)
            collect.rows, collect.notes = [], []   # calls from every attempt of this claim
            attempts = 0
            while True:
                attempts += 1
                started = clock()
                r = client.post("/api/check", json={"claim": claim})
                seconds = clock() - started
                if r.status_code != 429:
                    in_a_row = 0
                    break
                in_a_row += 1
                for _lvl, note in collect.notes:
                    say(f"      429 detail: {note}")
                if in_a_row >= RATE_LIMITS_IN_A_ROW:
                    err = StoppedByRateLimit(f"{in_a_row} rate limits in a row, stopping")
                    err.records = records
                    raise err
                say(f"  {n}: rate limited ({in_a_row} in a row), waiting {RATE_LIMIT_WAIT}s")
                sleep(RATE_LIMIT_WAIT)
            body = r.get_json() or {}
            fell_back = [msg for lvl, msg in collect.notes if lvl >= logging.WARNING]
            records.append({
                "n": n, "claim": claim, "expected": expected, "status": r.status_code,
                "verdict": body.get("verdict") if r.status_code == 200 else None,
                "error": None if r.status_code == 200 else body.get("error"),
                "seconds": round(seconds, 1), "attempts": attempts,
                "models": summed(collect.rows, today), "fell_back": fell_back,
            })
            say(format_check(records[-1]))
    finally:
        for lg, level in zip(loggers, saved):
            lg.removeHandler(collect)
            lg.setLevel(level)
    return records


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------

def _usd(v):
    return "n/a" if v is None else f"${v:.4f}"


def _line(name, m):
    return (f"      {name:24} {m['calls']:5.1f} calls {m['prompt_tokens']:9.0f} in {m['output_tokens']:7.0f} out "
            f"{m['thinking_tokens']:7.0f} thinking  {m['seconds']:6.1f}s in calls "
            f"(slowest {m['slowest']:5.1f}s)  {_usd(m['usd'])}")


def format_check(rec):
    head = (f"{rec['n']:2}. {rec['claim']}\n"
            f"      shown {rec['verdict'] or 'HTTP ' + str(rec['status'])}, wanted {rec['expected']}, "
            f"{rec['seconds']}s" + (f", {rec['attempts']} attempts" if rec["attempts"] > 1 else ""))
    lines = [head] + [_line(name, m) for name, m in rec["models"].items()]
    if rec["fell_back"]:
        lines.append("      MIXED MODELS: " + "; ".join(rec["fell_back"]))
    return "\n".join(lines)


def average(records, today=None):
    """Mean per check, over the checks that returned a verdict."""
    done = [r for r in records if r["status"] == 200]
    if not done:
        return {}, 0.0
    names = sorted({m for r in done for m in r["models"]})
    avg = {}
    for name in names:
        a = {k: 0.0 for k in ("calls", "prompt_tokens", "output_tokens", "thinking_tokens", "seconds", "slowest")}
        for r in done:
            m = r["models"].get(name)
            if m:
                for k in a:
                    a[k] += m[k] if k != "slowest" else 0
                a["slowest"] = max(a["slowest"], m["slowest"])
        for k in ("calls", "prompt_tokens", "output_tokens", "thinking_tokens", "seconds"):
            a[k] /= len(done)
        a["usd"] = cost_usd(name, a["prompt_tokens"], a["output_tokens"], a["thinking_tokens"], today)
        avg[name] = a
    return avg, sum(r["seconds"] for r in done) / len(done)


def checks_per_day(avg):
    """(checks a day, the model whose daily limit binds) on the free tier, or
    (None, why) when a model in use has no known limit."""
    best, binding = None, None
    for name, a in avg.items():
        if name not in FREE_TIER:
            return None, f"{name} has no known free-tier limit"
        rpm, rpd = FREE_TIER[name]
        n = int(rpd // a["calls"]) if a["calls"] else 0
        if best is None or n < best:
            best, binding = n, name
    return best, binding


def format_report(records, today=None):
    avg, secs = average(records, today)
    out = ["", "Average per check (" + str(sum(r["status"] == 200 for r in records)) + " of "
           + str(len(records)) + " returned a verdict)"]
    if not avg:
        return "\n".join(out + ["      nothing to average"])
    out += [_line(name, a) for name, a in avg.items()]
    total = [a["usd"] for a in avg.values()]
    out.append(f"      seconds per check (whole request): {secs:.1f}")
    out.append("      cost per check: " + ("n/a, a model in use has no listed price" if None in total
                                           else f"${sum(total):.4f}  (${sum(total) * 1000:.2f} per 1,000 checks)"))
    n, why = checks_per_day(avg)
    out.append("Free tier (limits per project and model, see FREE_TIER in this script):")
    for name, a in avg.items():
        rpm, rpd = FREE_TIER.get(name, (None, None))
        per_day = "unknown" if rpd is None else f"{int(rpd // a['calls']) if a['calls'] else 0} checks a day"
        out.append(f"      {name:24} {a['calls']:.1f} calls a check, {rpm} a minute and {rpd} a day: {per_day}")
    out.append("      checks a day, all models together: " + (f"{n}, bound by {why}" if n is not None else why))
    return "\n".join(out)


# ---------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------

def _load_app():
    # All of this must be in place before app.py and db.py are imported.
    os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "cost.db")
    os.environ["VERDICT_CACHE_HOURS"] = "0"
    os.environ["LLM_PROVIDER"] = "gemini"
    os.environ["MISTRAL_API_KEY"] = ""
    os.environ["GROQ_API_KEY"] = ""
    os.environ["VERDICT_MODEL_FALLBACK"] = "0"
    for name in ("RATE_LIMIT_PER_USER", "RATE_LIMIT_GLOBAL_PER_MINUTE", "RATE_LIMIT_GLOBAL_PER_DAY"):
        os.environ[name] = "0"   # our own limiter off, so every 429 is Google's
    os.chdir(ROOT)
    import app as appmod
    import verdict
    return appmod, verdict


def main(argv):
    gap, out = 30.0, None
    if "--gap" in argv:
        i = argv.index("--gap"); gap = float(argv[i + 1]); del argv[i:i + 2]
    if "--json" in argv:
        i = argv.index("--json"); out = pathlib.Path(argv[i + 1]); del argv[i:i + 2]
    appmod, verdict = _load_app()
    print(f"Models: {verdict.model_name()}   fallback off   {date.today()}")
    claims = claims_from_file()
    print(f"{len(claims)} claims, {gap:.0f}s apart, scratch database, cache off")
    stopped = None
    try:
        records = measure(appmod.app.test_client(), claims, gap=gap)
    except StoppedByRateLimit as e:
        records, stopped = e.records, e
    print(format_report(records))
    if out:
        out.write_text(json.dumps({"models": verdict.model_name(), "date": str(date.today()),
                                   "records": records}, indent=1))
    if stopped:
        print(f"STOPPED: {stopped}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
