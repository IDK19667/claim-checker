"""
Type-ahead over claims that have already been checked.

Two jobs, in this order:

1. Save the reader a wait. A claim in the cache answers instantly and for
   free; a new one costs a model round trip and counts against the day's
   quota. Steering someone onto an existing answer is the cheapest good
   outcome this app has.
2. Survive a typo. Real claims arrive misspelled, because people retype
   what they half-remember from a video. "dose apple cider vinigaer cure
   diabaties" should still find the apple cider vinegar check.

Matching is deterministic and local: no model, no network, no index to
keep warm. `difflib` is in the standard library, so this adds nothing to
install.
"""

import difflib
import re

# Below this, a fuzzy match is noise rather than a near miss.
FUZZY_CUTOFF = 0.58
# Short fragments match everything, so type-ahead stays quiet until there
# is enough to go on.
MIN_QUERY = 3


def _norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) > 2}


def suggest(query: str, claims: list[dict], limit: int = 6) -> list[dict]:
    """
    Rank cached claims against what has been typed so far.

    Returns dicts of {claim_text, verdict, issue, kind}, where `kind` is
    "match" for a literal hit and "near" for a fuzzy one, so the page can
    label the two groups honestly instead of silently pretending a
    corrected spelling was what the reader typed.
    """
    q = _norm(query)
    if len(q) < MIN_QUERY or not claims:
        return []

    q_words = _words(query)
    literal, near = [], []
    seen = set()

    for c in claims:
        text = c.get("claim_text") or ""
        key = _norm(text)
        if not key or key in seen:
            continue
        row = {"claim_text": text, "verdict": c.get("verdict"),
               "issue": c.get("issue")}

        if q in key:
            seen.add(key)
            # Earlier hits are stronger: a prefix beats a mention.
            literal.append((key.index(q), row | {"kind": "match"}))
            continue

        # Shared words catch reordering ("vinegar apple cider").
        overlap = len(q_words & _words(text))
        ratio = difflib.SequenceMatcher(None, q, key).ratio()
        if overlap >= max(1, len(q_words) - 1) or ratio >= FUZZY_CUTOFF:
            seen.add(key)
            score = ratio + 0.1 * overlap
            near.append((-score, row | {"kind": "near"}))

    literal.sort(key=lambda p: p[0])
    near.sort(key=lambda p: p[0])
    return [r for _, r in literal][:limit] + \
           [r for _, r in near][: max(0, limit - len(literal))]
