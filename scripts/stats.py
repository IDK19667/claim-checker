"""A read-only report from the anonymous `checks` table that already exists:
checks per day for the last 30 days, and the 20 most checked claims.

    .venv/bin/python scripts/stats.py [--db path/to/file.db]

It reads whatever DB_PATH points at (the same variable the app uses, from the
environment or .env), or the file named by --db. It opens the database
read-only, creates nothing and writes nothing, and there is nothing to run on
the site for it: no counter, no page-view table, no script in any page. It
reports only what the check log already holds (the claim text, the verdict and
the time), which is anonymous by design. See DECISIONS.md, "Counting checks
without collecting more".

The free Render plan wipes its disk on every restart (render.yaml), so on the
live site the counts cover only the time since the last restart.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DAYS = 30
TOP = 20


def read_report(db_path: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=DAYS - 1)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        per_day = dict(conn.execute(
            "SELECT substr(checked_at, 1, 10) AS d, COUNT(*) FROM checks "
            "WHERE substr(checked_at, 1, 10) >= ? GROUP BY d", (since,)).fetchall())
        top = conn.execute(
            "SELECT claim_key, MIN(claim_text), COUNT(*) AS n FROM checks "
            "GROUP BY claim_key ORDER BY n DESC, claim_key LIMIT ?", (TOP,)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
    finally:
        conn.close()
    lines = [f"Checks per day, last {DAYS} days (UTC)", ""]
    for i in range(DAYS - 1, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        lines.append(f"{d}  {per_day.get(d, 0):>5}")
    lines += ["", f"All checks in this database: {total}", "", f"Top {TOP} claims", ""]
    if not top:
        lines.append("(no checks yet)")
    for _key, text, n in top:
        lines.append(f"{n:>5}  {text}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    path = None
    if "--db" in argv:
        i = argv.index("--db")
        path = argv[i + 1] if i + 1 < len(argv) else None
        if not path:
            print("--db needs a path", file=sys.stderr)
            return 2
    if path is None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        path = os.environ.get("DB_PATH", "health_claim_checker.db")
    if not os.path.exists(path):
        print(f"No database at {path}. Nothing has been checked here yet.", file=sys.stderr)
        return 1
    try:
        print(read_report(path))
    except sqlite3.Error as e:
        print(f"Could not read {path}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
