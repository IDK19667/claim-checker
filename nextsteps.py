"""
What to offer when PubMed has no answer.

A claim with no matching research is the hardest screen in this product.
The honest thing to say is short: nothing found, and that is not the same
as false. The unhelpful thing is to stop there, which is what the page did
until now. It is also the tempting moment to guess, and guessing is the
one thing this app must never do, so nothing here proposes an answer.

What it offers instead is all true and all checkable:

  - the exact search that was run, and a link to run it yourself
  - a wider search, the same terms without requiring every one to match
  - claims we have already checked that share ground with this one
  - the reason a search can come back empty, in plain words

Everything is derived from the claim and the local cache. No model call,
so an unanswerable claim costs nothing beyond the search that failed.
"""

import re
from urllib.parse import quote

import suggest

# Words that carry no search meaning. Kept small on purpose: over-trimming
# a claim produces a wider search that matches everything and helps nobody.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "cause",
    "causes", "cure", "cures", "do", "does", "for", "from", "has", "have",
    "help", "helps", "how", "if", "in", "into", "is", "it", "its", "make",
    "makes", "may", "more", "my", "of", "on", "or", "really", "so", "that",
    "the", "their", "them", "then", "there", "they", "this", "to", "up",
    "was", "were", "what", "when", "which", "why", "will", "with", "you",
    "your",
}

PUBMED_SEARCH = "https://pubmed.ncbi.nlm.nih.gov/?term="


def key_terms(claim: str, limit: int = 6) -> list[str]:
    """The words worth searching on, longest first, order preserved."""
    words = re.findall(r"[a-z][a-z-]{2,}", (claim or "").lower())
    seen, kept = set(), []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.add(w)
        kept.append(w)
    kept.sort(key=len, reverse=True)
    return kept[:limit]


def wider_search_url(claim: str) -> str | None:
    """
    The same terms joined with OR instead of AND. A search fails most often
    because every term had to match at once; this asks for any of them.
    """
    terms = key_terms(claim)
    if len(terms) < 2:
        return None
    return PUBMED_SEARCH + quote(" OR ".join(terms))


def exact_search_url(query: str) -> str | None:
    return PUBMED_SEARCH + quote(query) if query else None


def related_checks(claim: str, claims: list[dict], limit: int = 4) -> list[dict]:
    """
    Claims already checked that share ground with this one. These are real
    answers with real evidence behind them, which is the most useful thing
    on a page that has none of its own.
    """
    rows = suggest.suggest(claim, claims or [], limit=limit + 2)
    here = suggest._norm(claim)
    return [r for r in rows if suggest._norm(r["claim_text"]) != here][:limit]


def build(claim: str, query_used: str, studies, cited, claims) -> dict | None:
    """
    The block to show when a check comes back without usable evidence.
    Returns None when the verdict does rest on something, so the caller can
    include it unconditionally.

    Two different emptinesses, told apart because they are different facts:
    `nothing_found` means PubMed matched no papers at all, `none_relevant`
    means papers came back but none of them actually tests the claim.
    """
    if studies and cited:
        return None
    return {
        "kind": "nothing_found" if not studies else "none_relevant",
        "searched": query_used or "",
        "exact_url": exact_search_url(query_used),
        "wider_url": wider_search_url(claim),
        "terms": key_terms(claim),
        "related": related_checks(claim, claims),
    }
