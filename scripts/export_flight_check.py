"""
Export one real, already-cached check for the fly-through to replay.

The animation over the footage is not a mock-up: it shows a check this app
actually ran, with the query it actually sent to PubMed, the studies it
actually read, the grades `evidence.py` actually assigned, and the verdict
and still-open line the model actually returned. Nothing here is written by
hand, which is the point. If the numbers on screen could drift from the
numbers the product produces, the animation would be an advert rather than
a demonstration.

The export exists because the local database is not in the repo, so without
it the page could not be rebuilt on another machine or on Render.

    .venv/bin/python scripts/export_flight_check.py "apple cider vinegar cures diabetes"
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import evidence  # noqa: E402

OUT = ROOT / "static" / "flight" / "check.json"
DB = ROOT / "health_claim_checker.db"


def main() -> None:
    key = (sys.argv[1] if len(sys.argv) > 1
           else "apple cider vinegar cures diabetes").lower().strip()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "select * from verdict_cache where claim_key = ?", (key,)).fetchone()
    if row is None:
        sys.exit(f"no cached check for {key!r}. Run it on the site first.")

    studies = json.loads(row["studies_json"])
    cited = set(json.loads(row["cited_json"] or "[]"))

    out = {
        "claim": row["claim_text"],
        "checkedAt": row["created_at"][:10],
        "query": row["search_query"],
        "verdict": row["verdict"],
        "tldr": row["tldr"],
        "stillOpen": row["still_open"],
        "snapshot": evidence.snapshot(studies, cited),
        "studies": [
            {
                "pmid": s["pmid"],
                "title": s["title"],
                "year": s.get("year"),
                "journal": s.get("journal"),
                "tier": evidence.classify(s.get("publication_types") or []),
                "label": evidence.strongest_label(s.get("publication_types") or []),
                "cited": s["pmid"] in cited,
            }
            for s in studies
        ],
    }
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{OUT.relative_to(ROOT)}: {len(out['studies'])} studies, "
          f"{len(cited)} relied on, verdict {out['verdict']}, "
          f"checked {out['checkedAt']}")


if __name__ == "__main__":
    main()
