"""
Count how many saved answers (verdict_cache rows) are inconsistent, and check
that every one still renders after the saved-record fixes.

Run it on a COPY of the database, never on the live file. The copy is opened
read-only for the counts; the render check works on a second temporary copy
so the copy you pass in is never written to.

    cp health_claim_checker.db /path/to/audit_copy.db
    GEMINI_API_KEY= GOOGLE_API_KEY= MISTRAL_API_KEY= ANTHROPIC_API_KEY= \
    TURSO_DATABASE_URL= TURSO_AUTH_TOKEN= \
    .venv/bin/python scripts/saved_record_audit.py /path/to/audit_copy.db

Kinds counted (a record can have more than one):
  unreadable_json   cited, studies or breakdown JSON that does not parse
  count_mismatch    stored breakdown "rests_on" differs from the number of
                    cited studies that are really in the record
  cited_not_in_studies  a cited PMID that is not among the stored studies
  empty_query       no search query stored
  missing_date      no created_at
  unparseable_date  created_at is not an ISO date
  future_date       created_at is later than now
  first_check_after_saved  the check log's first real check is more than an hour
                    later than the saved date (the log is written just after the
                    save, so a little later is normal; much later contradicts it)
"""
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
from collections import Counter
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent


def audit(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM verdict_cache").fetchall()
    first = {r[0]: r[1] for r in con.execute(
        "SELECT claim_key, MIN(checked_at) FROM checks WHERE cached = 0 GROUP BY claim_key")}
    kinds, bad = Counter(), {}
    now = datetime.now(timezone.utc)

    def flag(row, kind):
        kinds[kind] += 1
        bad.setdefault(row["claim_key"], []).append(kind)

    def when(iso):
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    for r in rows:
        try:
            cited = set(json.loads(r["cited_json"] or "[]"))
            studies = json.loads(r["studies_json"] or "[]")
            bd = json.loads(r["breakdown_json"]) if r["breakdown_json"] else None
        except (ValueError, TypeError):
            flag(r, "unreadable_json")
            continue
        pmids = {s.get("pmid") for s in studies}
        if cited - pmids:
            flag(r, "cited_not_in_studies")
        if isinstance(bd, dict) and bd.get("rests_on") is not None \
                and bd["rests_on"] != len(cited & pmids):
            flag(r, "count_mismatch")
        if not (r["search_query"] or "").strip():
            flag(r, "empty_query")
        if not r["created_at"]:
            flag(r, "missing_date")
            continue
        try:
            saved = when(r["created_at"])
        except ValueError:
            flag(r, "unparseable_date")
            continue
        if saved > now:
            flag(r, "future_date")
        f = first.get(r["claim_key"])
        if f:
            try:
                if (when(f) - saved).total_seconds() > 3600:
                    flag(r, "first_check_after_saved")
            except ValueError:
                flag(r, "unparseable_date")
    return rows, kinds, bad


def render_all(path, rows):
    """Render every saved answer through the real app, on a throwaway copy."""
    tmp = tempfile.mkdtemp()
    copy = os.path.join(tmp, "render.db")
    shutil.copyfile(path, copy)
    os.environ["DB_PATH"] = copy
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "MISTRAL_API_KEY", "ANTHROPIC_API_KEY",
              "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        os.environ[k] = ""
    sys.path.insert(0, str(ROOT))
    import app as appmod
    client = appmod.app.test_client()
    failures = []
    for r in rows:
        q = urllib.parse.quote(r["claim_text"], safe="")
        try:
            resp = client.get("/?q=" + q)
            html = resp.data.decode()
            ok = resp.status_code == 200 and 'id="server-result"' in html
            if ok:
                data = json.loads(html.split('id="server-result" type="application/json"', 1)[1]
                                  .split(">", 1)[1].split("</script>", 1)[0])
                used = sum(1 for s in data["studies"] if s["cited_in_verdict"])
                bd = data.get("breakdown")
                if bd and bd.get("rests_on") is not None and bd["rests_on"] != used:
                    ok = False
                if bd and used and f"{used} stud" not in html:
                    ok = False
                empty = not (data.get("search_query_used") or "").strip()
                if empty != ('id="query-what" hidden' in html):
                    ok = False
        except Exception as e:  # any error is a failure to report, not to hide
            ok = False
            print("  error:", r["claim_key"][:60], type(e).__name__, e)
        if not ok:
            failures.append(r["claim_key"][:60])
    shutil.rmtree(tmp, ignore_errors=True)
    return failures


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = os.path.abspath(sys.argv[1])
    rows, kinds, bad = audit(path)
    print(f"records: {len(rows)}")
    for k, n in sorted(kinds.items()):
        print(f"  {k}: {n}")
    print(f"records with at least one problem: {len(bad)} of {len(rows)}")
    failures = render_all(path, rows)
    print(f"render check: {len(rows) - len(failures)} of {len(rows)} render cleanly and consistently")
    for f in failures:
        print("  FAILED:", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
