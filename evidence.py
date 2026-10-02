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

import re

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


# ---------------------------------------------------------------------------
# Who was actually studied
#
# A trial in 511 people with prediabetes is real evidence about people with
# prediabetes and thin evidence about everyone else. PubMed does not tag this,
# so it is read off the title and the opening of the abstract, where trials
# state who they enrolled. Ordered: the first match wins, and the label is the
# plain words a reader would use, because it is printed on the page.
# ---------------------------------------------------------------------------

POPULATIONS = [
    (r"\bcritically ill|\bintensive care|\bICU\b", "people in intensive care"),
    (r"\bpreterm|\bneonat|\binfants?\b", "newborn babies"),
    (r"\bpregnan|\bpostpartum", "pregnant women"),
    (r"\bprediabet", "people with prediabetes"),
    (r"\btype [12] diabet|\bdiabetic", "people with diabetes"),
    (r"\bdialysis|\bchronic kidney|\brenal failure", "people with kidney failure"),
    (r"\bHIV\b|\bAIDS\b", "people with HIV"),
    (r"\btuberculosis\b", "people with tuberculosis"),
    (r"\bcystic fibrosis\b", "people with cystic fibrosis"),
    (r"\bchemotherapy|\bcancer patients|\boncolog", "people treated for cancer"),
    (r"\basthma\b|\bCOPD\b|\bchronic obstructive", "people with asthma or COPD"),
    (r"\bpostmenopausal", "women after menopause"),
    (r"\bnursing home|\bcare home|\blong.term care", "care home residents"),
    (r"\bolder adults?\b|\belderly\b|\baged 6[5-9]|\baged 7[0-9]", "older adults"),
    (r"\bathletes?\b|\belite sport|\bmarathon", "athletes"),
    (r"\bchildren\b|\bpaediatric|\bpediatric|\badolescen|\bschoolchild",
     "children"),
    (r"\bmen\b(?!tal)|\bmales? only", "men only"),
    (r"\bwomen\b|\bfemales? only", "women only"),
]

_POOLED = ("Meta-Analysis", "Network Meta-Analysis", "Systematic Review")


def population(study) -> str | None:
    """
    The narrow group a study was run in, in plain words, or None when it
    looks like a general population.

    The title counts wherever it matches. The abstract counts only for
    papers that are not pooled evidence: a meta-analysis of 25 trials
    mentions children in a subgroup line without being a study of
    children, and mislabelling the broadest paper in the set as the
    narrowest would invert the whole point of the flag.
    """
    study = study or {}
    title = str(study.get("title") or "")
    pooled = strongest_label(study.get("publication_types")) in _POOLED
    # Trials state who they enrolled early, in Background or Methods.
    body = "" if pooled else str(study.get("abstract") or "")[:700]
    for pattern, label in POPULATIONS:
        if re.search(pattern, title, re.IGNORECASE):
            return label
        if body and re.search(pattern, body, re.IGNORECASE):
            return label
    return None


def narrow_populations(studies, cited_pmids=()) -> list[str]:
    """The distinct narrow groups among the studies a verdict leaned on."""
    cited = {str(p) for p in (cited_pmids or ())}
    seen = []
    for s in studies or []:
        if cited and str(s.get("pmid")) not in cited:
            continue
        label = population(s)
        if label and label not in seen:
            seen.append(label)
    return seen


def narrow_only(studies, cited_pmids=()) -> bool:
    """
    True when every study a verdict leaned on was run in a narrow group.
    A general claim answered only from these is not answered: the honest
    verdict is that it depends who you are.
    """
    cited = {str(p) for p in (cited_pmids or ())}
    used = [s for s in (studies or [])
            if not cited or str(s.get("pmid")) in cited]
    return bool(used) and all(population(s) for s in used)


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
