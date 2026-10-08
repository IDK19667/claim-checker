#!/usr/bin/env python3
"""Before and after, on five real claims: the search query and the reading grade.

    .venv/bin/python scripts/plain_delta.py

For each claim this runs the old search prompt and the new one, then one
verdict call, and grades the same model output twice: once through the gate
as it was (no plain words, every word counted), once as it is now. Grading
one reply two ways is the only way the two numbers are comparable; running
the verdict twice would measure the model's variance instead.

Needs a key. Costs 3 calls per claim, 15 in all, plus free PubMed searches.
"""
from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

import breakdown  # noqa: E402
import evidence  # noqa: E402
import pubmed  # noqa: E402
import verdict  # noqa: E402

CLAIMS = [
    "Taking vitamin D supplements in winter cuts your risk of catching a cold "
    "or flu, but only if you're deficient to begin with",
    "Creatine causes hair loss",
    "Intermittent fasting is better than normal calorie counting for weight loss",
    "Eating eggs raises your risk of heart disease",
    "Blue light from screens damages your eyes",
]

# The query prompt as it stood before this change: terms joined with AND.
OLD_SEARCH = (
    "A student typed this health claim into a fact-checking "
    'tool: "{claim}"\n\n'
    "Write the best PubMed search query to find studies "
    "relevant to this claim. Use 2-4 key terms joined with AND. "
    "Keep the specific food, product, or substance named in the "
    "claim as one term, using its common name (e.g. \"celery\", "
    "\"apple cider vinegar\"), since that is how study titles "
    "refer to it. For the condition or outcome, prefer standard "
    "medical terminology (the kind used in MeSH headings) over "
    "slang or abbreviations that PubMed might map to something "
    "unrelated. Respond with ONLY the search query string, "
    "nothing else. No quotes, no explanation."
)

# Papers that ought to come back for the vitamin D claim, named in the brief.
LANDMARKS = {"28202713": "Martineau 2017 BMJ", "33798465": "Jolliffe 2021 Lancet"}


def old_grade(parsed, studies) -> float | None:
    """The grade the old gate would have reported for this same reply."""
    was = breakdown.plain
    breakdown.plain = lambda text, kept=None: text  # the step that did not exist
    try:
        bd = breakdown.ground(parsed, studies, verdict.tidy_prose, claim="")
    finally:
        breakdown.plain = was
    return None if not bd else breakdown.reading_grade(breakdown.flatten(bd))


def run(claim: str) -> dict:
    old_q = verdict._clean_query(
        verdict._complete_text(OLD_SEARCH.format(claim=claim), max_tokens=256), claim)
    new_q, surrogate, _ = verdict.extract_search_terms(claim)

    old_ids = pubmed.search_pubmed(old_q, max_results=8)
    studies, used, _ = pubmed.search_with_fallback(new_q, max_results=8,
                                                  surrogate=surrogate)

    groups = pubmed.split_and(used or "")
    outcome = groups[1] if len(groups) > 1 else ""
    block = "\n\n".join(verdict._format_study_for_prompt(i + 1, s, outcome, surrogate)
                        for i, s in enumerate(studies))
    raw, stop = verdict._complete_json(
        verdict.weigh_prompt(claim, block, verdict._subgroup_block(studies, claim)),
        verdict.VERDICT_SCHEMA, max_tokens=12000)
    import json
    parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())

    after = breakdown.ground(parsed, studies, verdict.tidy_prose, claim)
    return {
        "claim": claim,
        "old_q": old_q, "new_q": used,
        "old_ids": old_ids, "new_ids": [s["pmid"] for s in studies],
        "old_strong": sum(1 for s in pubmed.fetch_details(old_ids)
                          if evidence.classify(s["publication_types"]) == "strong"),
        "new_strong": sum(1 for s in studies
                          if evidence.classify(s["publication_types"]) == "strong"),
        "before": old_grade(parsed, studies),
        "after": None if not after else after["grade"],
        "tldr": verdict._read_verdict(raw, stop, studies, claim)["tldr"],
        "studies": studies,
    }


def main() -> None:
    rows = []
    for i, claim in enumerate(CLAIMS):
        print(f"[{i + 1}/{len(CLAIMS)}] {claim[:60]}...", file=sys.stderr)
        rows.append(run(claim))
        if i < len(CLAIMS) - 1:
            time.sleep(8)  # the free tier is rate limited per minute

    print("\nSEARCH QUERIES\n")
    for r in rows:
        print(f"  {r['claim'][:64]}")
        print(f"    before: {r['old_q']}")
        print(f"            {r['old_strong']}/8 strong designs")
        print(f"    after:  {r['new_q']}")
        print(f"            {r['new_strong']}/8 strong designs\n")

    print("READING GRADE\n")
    print(f"  {'claim':44}{'before':>9}{'after':>8}{'change':>9}")
    for r in rows:
        b, a = r["before"], r["after"]
        delta = f"{a - b:+.1f}" if (b is not None and a is not None) else "n/a"
        print(f"  {r['claim'][:42]:44}{b if b is not None else 'n/a':>9}"
              f"{a if a is not None else 'n/a':>8}{delta:>9}")

    print("\nTAKEAWAYS\n")
    for r in rows:
        print(f"  {r['tldr']}")

    vd = rows[0]
    print("\nLANDMARK PAPERS FOR THE VITAMIN D CLAIM\n")
    for pmid, name in LANDMARKS.items():
        was = "yes" if pmid in vd["old_ids"] else "no"
        now = "yes" if pmid in vd["new_ids"] else "no"
        print(f"  {name:24} before: {was:4} after: {now}")

    print("\nSTUDIES RETRIEVED FOR THE VITAMIN D CLAIM\n")
    for i, s in enumerate(vd["studies"], 1):
        kind = evidence.strongest_label(s["publication_types"]) or "unlabelled"
        who = evidence.population(s) or "general population"
        print(f"  {i}. PMID {s['pmid']} ({s['year']}) [{evidence.classify(s['publication_types'])}]")
        print(f"     {s['title'][:88]}")
        print(f"     type: {kind} | population: {who}")


if __name__ == "__main__":
    main()
