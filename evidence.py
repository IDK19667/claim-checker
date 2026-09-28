"""
What kind of study is this, and how much does it weigh?

The same question is asked in two places: here, for anything the server
renders (the front page ledger, the result page snapshot, the share card),
and in `static/app.js` for anything the browser renders while a check is
streaming. The two tables must agree, so `tests/test_app.py` reads the
table out of `app.js` and fails if it ever drifts from this one.

Nothing here asks a model. Publication types come from PubMed's own
curated metadata, so the evidence mix on screen is a fact about the
record, not an opinion about it.
"""

# Ordered: the first match wins, so a paper tagged both "Meta-Analysis"
# and "Journal Article" reads as the meta-analysis it is.
TYPES = [
    ("Meta-Analysis", "strong"),
    ("Network Meta-Analysis", "strong"),
    ("Systematic Review", "strong"),
    ("Randomized Controlled Trial", "strong"),
    ("Practice Guideline", "strong"),
    ("Retracted Publication", "retracted"),
    ("Case Reports", "weak"),
    ("Editorial", "weak"),
    ("Comment", "weak"),
    ("Letter", "weak"),
    ("News", "weak"),
    ("Controlled Clinical Trial", "moderate"),
    ("Clinical Trial", "moderate"),
    ("Observational Study", "moderate"),
    ("Multicenter Study", "moderate"),
    ("Comparative Study", "moderate"),
    ("Review", "moderate"),
]

# Publication types that describe the paperwork rather than the study.
IGNORE_PREFIXES = ("Journal Article", "Research Support", "English Abstract",
                   "Introductory")

# Bar order, strongest first. "retracted" is deliberately last and is never
# folded into "weak": a withdrawn paper is a different fact from a thin one.
TIERS = ("strong", "moderate", "weak", "retracted")

# (singular, plural). A count always reads as English: "1 strong design",
# "4 strong designs".
TIER_LABELS = {
    "strong": ("strong design", "strong designs"),
    "moderate": ("human study", "human studies"),
    "weak": ("opinion or single case", "opinion or single cases"),
    "retracted": ("retracted paper", "retracted papers"),
}


def tier_label(tier: str, count: int) -> str:
    singular, plural = TIER_LABELS[tier]
    return singular if count == 1 else plural


def classify(publication_types) -> str:
    """The tier for one study. Unlabelled papers count as moderate."""
    kept = [t for t in (publication_types or [])
            if not t.startswith(IGNORE_PREFIXES)]
    for label, tier in TYPES:
        if any(t.startswith(label) for t in kept):
            return tier
    return "moderate"


def strongest_label(publication_types) -> str:
    """The most informative type name, for a badge. '' when there is none."""
    kept = [t for t in (publication_types or [])
            if not t.startswith(IGNORE_PREFIXES)]
    for label, _tier in TYPES:
        hit = next((t for t in kept if t.startswith(label)), None)
        if hit:
            return label
    return kept[0] if kept else ""


def mix(studies) -> list[dict]:
    """
    The evidence bar: one segment per tier that actually occurs, with the
    share it takes. Tiers with no studies are dropped rather than drawn as
    zero-width slivers.
    """
    studies = studies or []
    total = len(studies)
    if not total:
        return []
    counts = {tier: 0 for tier in TIERS}
    for s in studies:
        counts[classify(s.get("publication_types"))] += 1
    return [
        {"tier": tier, "label": tier_label(tier, counts[tier]),
         "count": counts[tier], "percent": round(counts[tier] * 100 / total)}
        for tier in TIERS if counts[tier]
    ]


def snapshot(studies, cited_pmids=()) -> dict:
    """
    The counts a reader can check us on, in the spirit of a research
    snapshot: how much was read, how much was actually used, what kind of
    designs were in it, and how recent they are.
    """
    studies = studies or []
    cited = {str(p) for p in (cited_pmids or ())}
    years = sorted(int(s["year"]) for s in studies
                   if str(s.get("year") or "").isdigit())
    pooled = sum(1 for s in studies
                 if strongest_label(s.get("publication_types"))
                 in ("Meta-Analysis", "Network Meta-Analysis", "Systematic Review"))
    trials = sum(1 for s in studies
                 if strongest_label(s.get("publication_types"))
                 in ("Randomized Controlled Trial", "Controlled Clinical Trial",
                     "Clinical Trial"))
    return {
        "read": len(studies),
        "relied_on": sum(1 for s in studies if str(s.get("pmid")) in cited),
        "pooled": pooled,
        "trials": trials,
        "registered": sum(1 for s in studies if s.get("data_banks")),
        "retracted": sum(1 for s in studies
                         if classify(s.get("publication_types")) == "retracted"),
        "year_from": years[0] if years else None,
        "year_to": years[-1] if years else None,
        "mix": mix(studies),
    }
