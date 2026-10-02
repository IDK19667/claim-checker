#!/usr/bin/env python3
"""What the deeper layer costs, in tokens, on one real claim.

    .venv/bin/python scripts/token_delta.py "the claim"

Runs the verdict call twice against the same studies: once with the schema
and prompt as they were before breakdown.py existed, once as they are now.
Both numbers come from the provider's own usage metadata, not an estimate.
The old prompt is derived from the new one by removing exactly the two
blocks that were added to it, so the comparison cannot drift.

Needs a key, and spends free-tier quota: 1 search call plus 2 verdict calls.
"""
from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

import breakdown  # noqa: E402
import pubmed  # noqa: E402
import verdict  # noqa: E402

CLAIM = (sys.argv[1] if len(sys.argv) > 1 else
         "Taking vitamin D supplements in winter cuts your risk of catching a "
         "cold or flu, but only if you're deficient to begin with")


def prompts(claim: str, studies: list[dict]) -> tuple[str, str]:
    """(before, after). The source of both is the one prompt in verdict.py."""
    block = "\n\n".join(verdict._format_study_for_prompt(i + 1, s)
                        for i, s in enumerate(studies))
    after = verdict.weigh_prompt(claim, block)
    before = after.replace(breakdown.PROMPT + "\n\n", "")
    before = before.replace("\n" + breakdown.PROMPT_SHAPE, "")
    before = before.replace('"cited_study_numbers": [1, 2],',
                            '"cited_study_numbers": [1, 2]')
    assert before != after, "the prompt blocks did not come back out"
    return before, after


OLD_SCHEMA = {
    "type": "object",
    "properties": {k: v for k, v in verdict.VERDICT_SCHEMA["properties"].items()
                   if k not in breakdown.SCHEMA_PROPERTIES},
    "required": [k for k in verdict.VERDICT_SCHEMA["required"]
                 if k not in breakdown.SCHEMA_REQUIRED],
    "additionalProperties": False,
}


def run(prompt: str, schema: dict) -> dict:
    client = verdict._gemini()
    r = client.models.generate_content(
        model=verdict.GEMINI_MODEL,
        contents=prompt,
        config={"max_output_tokens": 12000,
                "response_mime_type": "application/json",
                "response_json_schema": schema,
                "automatic_function_calling": {"disable": True}},
    )
    u = r.usage_metadata
    return {"in": u.prompt_token_count, "out": u.candidates_token_count,
            "total": u.total_token_count, "text": r.text}


def main() -> None:
    query, surrogate = verdict.extract_search_terms(CLAIM)
    studies, used, broadened = pubmed.search_with_fallback(
        query, max_results=8, surrogate=surrogate)
    print(f"claim:   {CLAIM}")
    print(f"search:  {used}{' (broadened)' if broadened else ''}")
    print(f"studies: {len(studies)}\n")

    before, after = prompts(CLAIM, studies)
    old = run(before, OLD_SCHEMA)
    new = run(after, verdict.VERDICT_SCHEMA)

    # Call 1 is unchanged, but a check is two calls, so it belongs in the total.
    search_in = verdict._gemini().models.count_tokens(
        model=verdict.GEMINI_MODEL, contents=CLAIM).total_tokens

    print(f"{'':26}{'before':>10}{'after':>10}{'change':>10}")
    for label, key in (("verdict call, prompt", "in"),
                       ("verdict call, output", "out"),
                       ("verdict call, total", "total")):
        d = new[key] - old[key]
        print(f"{label:26}{old[key]:>10}{new[key]:>10}{d:>+10}")
    print(f"\nsearch call prompt is about {search_in} tokens either way "
          f"and did not change.")
    print(f"per check, both calls: {old['total']} -> {new['total']} "
          f"({new['total'] - old['total']:+d})")

    parsed = json.loads(new["text"])
    bd = breakdown.ground(parsed, studies, verdict.tidy_prose)
    print(f"\nafter the gate: {len(bd['evidence'])} paragraphs, "
          f"{len(bd['parts'])} parts, reading grade {bd['grade']}, "
          f"resting on {bd['rests_on']} of {len(studies)} studies.")


if __name__ == "__main__":
    main()
