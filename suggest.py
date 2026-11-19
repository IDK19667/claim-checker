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


# Words that carry no claim. Negations are deliberately not here: "is safe"
# and "is not safe" must never look alike.
_STOP = frozenset("""the and for are was were has have had that this these those with from into than
then its you your can may might will would should does did doing about over under
a an of to in on at by is it as be or if so we they their there which who what when""".split())
_NEGATIONS = frozenset({"no", "not", "never", "without", "non", "nor", "cannot", "cant", "dont", "doesnt"})


def _keys(text: str) -> frozenset:
    """The words that make up a claim: folded for case and punctuation, with
    function words dropped and a trailing plural 's' folded away."""
    out = set()
    for w in _norm((text or "").replace("'", "").replace("\u2019", "")).split():
        if w in _STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return frozenset(out)


def same_question(a: dict, b: dict) -> bool:
    """
    Two checked claims are one question worded twice when all of these hold:
      1. they got the same verdict (a different answer is never folded away);
      2. their key words are the same, or share at least two and each side
         has at most one word the other lacks ("Creatine builds strength" and
         "Creatine improves strength");
      3. none of the words that differ is a negation or contains a digit, so
         "safe" and "not safe", or "500 mg" and "5000 mg", stay apart.
    """
    if a.get("verdict") != b.get("verdict"):
        return False
    ka, kb = _keys(a.get("claim_text")), _keys(b.get("claim_text"))
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    da, db_ = ka - kb, kb - ka
    if len(ka & kb) < 2 or len(da) > 1 or len(db_) > 1:
        return False
    return not any(w in _NEGATIONS or any(ch.isdigit() for ch in w) for w in da | db_)


def group_similar(rows: list[dict]) -> list[dict]:
    """Fold each near-duplicate into the first row it matches, in rank order.
    The lead row keeps its place and gains `similar`: the other wordings."""
    leads: list[dict] = []
    for r in rows:
        for lead in leads:
            if same_question(lead, r):
                lead.setdefault("similar", []).append(r["claim_text"])
                break
        else:
            leads.append(dict(r))
    return leads


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
    ranked = group_similar([r for _, r in literal] + [r for _, r in near])
    return ranked[:limit]
