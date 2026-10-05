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

# "Women and men with overweight" is everyone, not one sex. Read before the
# table above, because "men" is matched first and the audit found a mixed
# trial labelled "men only".
_BOTH_SEXES = re.compile(r"\bwomen\b.{0,40}\bmen\b(?!tal)|\bmen\b(?!tal).{0,40}\bwomen\b|"
                         r"\bmales? and females?\b|\bfemales? and males?\b|\bboth sexes\b",
                         re.IGNORECASE)
_SEX_LABELS = ("men only", "women only")


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
    mixed_sex = bool(_BOTH_SEXES.search(title) or (body and _BOTH_SEXES.search(body)))
    for pattern, label in POPULATIONS:
        if mixed_sex and label in _SEX_LABELS:
            continue
        if re.search(pattern, title, re.IGNORECASE):
            return label
        if body and re.search(pattern, body, re.IGNORECASE):
            return label
    return None


# ---------------------------------------------------------------------------
# Whose claim it is
#
# The downgrade below exists for a general claim answered from one group's
# studies. A claim about one group is not that: "vaccines reduce the risk of
# severe measles" is a claim about children, because children are who get
# measles vaccines, and the Cochrane review in children is its best answer.
# The audit stamped it "It's complicated" for being answered in children.
# ---------------------------------------------------------------------------

# (pattern on the claim, groups whose studies answer it). The groups a claim is
# about without saying so.
NATURAL_GROUPS = [
    (r"\bmeasles|\bmumps|\brubella|\bchicken ?pox|\bvaricella|\bwhooping cough|"
     r"\bpertussis|\bpolio|\brotavirus|\bchildhood|\bADHD\b|\bhyperactiv|\bcolic\b|"
     r"\bteething|\bschool", ("children", "newborn babies")),
    (r"\bautis[mt]", ("children",)),
    (r"\bbreast.?fe|\bbab(?:y|ies)\b|\binfants?\b|\bnewborns?\b", ("newborn babies",)),
    (r"\bpregnan|\bmorning sickness|\bpre.?eclampsia|\bgestational", ("pregnant women",)),
    (r"\bmenopaus|\bhot (?:flash|flush)", ("women after menopause",)),
    (r"\bosteoporosis", ("women after menopause", "older adults")),
    (r"\bperiod pain|\bmenstrua|\bPCOS\b|\bpolycystic ovar", ("women only",)),
    (r"\bprostate|\berectile", ("men only",)),
    (r"\bdementia|\balzheimer|\bsarcopenia|\bfrailty", ("older adults",)),
    (r"\bsports? performance|\bathletic|\bendurance\b|\bsprint", ("athletes",)),
    # Trials of preventing type 2 diabetes enrol the people at risk of it.
    (r"\btype (?:2|II) diabet", ("people with prediabetes",)),
]
_NATURAL_GROUPS = [(re.compile(p, re.IGNORECASE), groups) for p, groups in NATURAL_GROUPS]

# Groups defined by having a disease. A claim about the risk of that disease
# is not answered by studies of people who already have it, so naming the
# disease in a risk claim does not make those people the claim's group.
DISEASE_GROUPS = ("people with prediabetes", "people with diabetes",
                  "people with kidney failure", "people with HIV",
                  "people with tuberculosis", "people with cystic fibrosis",
                  "people treated for cancer", "people with asthma or COPD")

RISK_CLAIM = re.compile(
    r"\b(?:risk|risks|chance|odds|prevents?|prevention|causes?|caused|causing|"
    r"gives? you|leads? to|protects? against|wards? off|incidence)\b", re.IGNORECASE)


def claim_groups(claim: str) -> set:
    """
    The groups whose studies answer this claim as asked: the ones it names
    ("children" in "sugar makes children hyperactive") and the ones it is
    about without naming (children for measles vaccines).
    """
    claim = claim or ""
    risk = bool(RISK_CLAIM.search(claim))
    found = set()
    for pattern, label in POPULATIONS:
        if re.search(pattern, claim, re.IGNORECASE):
            if risk and label in DISEASE_GROUPS:
                continue
            found.add(label)
    for pattern, groups in _NATURAL_GROUPS:
        if pattern.search(claim):
            found.update(groups)
    return found


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


def narrow_only(studies, cited_pmids=(), claim: str = "") -> bool:
    """
    True when every study a verdict leaned on was run in a narrow group
    other than the claim's own. A general claim answered only from these is
    not answered: the honest verdict is that it depends who you are. A study
    in the group the claim is about is not narrow for that claim.
    """
    cited = {str(p) for p in (cited_pmids or ())}
    own = claim_groups(claim)
    used = [s for s in (studies or [])
            if not cited or str(s.get("pmid")) in cited]
    return bool(used) and all(population(s) and population(s) not in own for s in used)


# ---------------------------------------------------------------------------
# The claim's condition, and whether the abstracts answer it
#
# A health claim often carries a condition: "but only if you're deficient",
# "if you have diabetes", "in children". The condition is the part a reader
# most wants settled, and it is also the part a model is quickest to wave
# away as untested, because answering it means reading the subgroup lines
# rather than the headline result.
#
# So the subgroup lines are found here, in code, and handed to the prompt as
# quoted sentences with their study numbers. A condition that an abstract
# reports on can then never be called untested: the sentence that reports on
# it is in the prompt, and `breakdown.contradictions` checks the answer
# against the same finding afterwards.
# ---------------------------------------------------------------------------

# (pattern on the claim, plain label, pattern on the abstract). The abstract
# side is wider than the claim side: a claim says "deficient" where a paper
# says "baseline 25(OH)D concentration".
CONDITIONS = [
    (r"\bdeficien\w*|\binsufficien\w*|\blow (?:levels?|status|in)\b",
     "people who start with low levels",
     r"baseline|25\(OH\)D|25-hydroxy|serum concentration|deficien|insufficien|"
     r"\bnmol/[lL]\b|\bng/m[lL]\b|low status"),
    (r"\bprediabet\w*", "people with prediabetes", r"prediabet"),
    (r"\btype [12] diabet|\bdiabet\w*", "people with diabetes", r"diabet|glycaem|glycem|HbA1c"),
    (r"\bobes\w*|\boverweight\b", "people who are overweight", r"obes|overweight|\bBMI\b"),
    (r"\bolder (?:adults?|people)\b|\belderly\b|\bover (?:the age of )?\d\d\b",
     "older adults", r"older adults|elderly|\baged \d\d|age subgroup|by age"),
    (r"\bpregnan\w*", "pregnant women", r"pregnan"),
    (r"\bchildren\b|\bkids\b|\bteenagers?\b",
     "children", r"children|paediatric|pediatric|adolescen|\bage\b"),
    (r"\bathletes?\b|\bresistance training\b|\bweight training\b|\bgym\b",
     "people who train", r"athlet|resistance training|trained|exercis"),
    (r"\bsmok\w*", "smokers", r"smok"),
    (r"\bwinter\b|\bseasonal\b", "winter", r"winter|season|latitude|sunlight"),
]

_CONDITIONS = [(re.compile(c, re.IGNORECASE), label, re.compile(a, re.IGNORECASE))
               for c, label, a in CONDITIONS]

# A sentence that reports on a subgroup rather than on everyone. "baseline"
# counts because that is how a trial names the status someone started in,
# which is exactly the shape of "only if you're deficient".
_SUBGROUP = re.compile(
    r"subgroup|stratifi|interaction|effect modif|\bbaseline\b|\bamong (?:those|"
    r"participants|people|patients)\b|\bin (?:those|participants|patients|people) "
    r"with\b|restricted to|confined to|greatest in|strongest in|larger in|"
    r"\bonly in\b|did not differ|no difference (?:by|between)|\bby age\b|"
    r"\bwhereas\b.*\bthose\b", re.IGNORECASE)


def conditions(claim: str) -> list[str]:
    """The conditions this claim carries, in plain words. Often empty."""
    found = []
    for pattern, label, _ in _CONDITIONS:
        if pattern.search(claim or "") and label not in found:
            found.append(label)
    return found


def _sentences(text: str) -> list[str]:
    """Abstract sentences. Rough on purpose: this is a search, not a render."""
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z0-9])", text or "")
            if s.strip()]


def subgroup_findings(studies, claim: str, limit: int = 6) -> list[dict]:
    """
    The sentences in these abstracts that report on a condition the claim
    carries. Each is {"study": n, "condition": label, "sentence": text}.

    Both halves have to be present in the same sentence: a subgroup marker
    and one of the condition's own words. "Protective effects were stronger
    in those with baseline 25(OH)D below 25 nmol/L" is a finding on the
    claim's condition; "25 trials were included" is not.
    """
    wanted = conditions(claim)
    if not wanted:
        return []
    out = []
    for i, study in enumerate(studies or [], 1):
        for sentence in _sentences(str(study.get("abstract") or "")):
            if not _SUBGROUP.search(sentence):
                continue
            for pattern, label, abstract_side in _CONDITIONS:
                if label not in wanted or not abstract_side.search(sentence):
                    continue
                out.append({"study": i, "condition": label,
                            "sentence": re.sub(r"\s+", " ", sentence)[:320]})
                break
            if len(out) >= limit:
                return out
    return out


def conditions_reported(studies, claim: str) -> list[str]:
    """Which of the claim's conditions the abstracts actually report on."""
    seen = []
    for hit in subgroup_findings(studies, claim, limit=40):
        if hit["condition"] not in seen:
            seen.append(hit["condition"])
    return seen


# ---------------------------------------------------------------------------
# How big is the effect, in words
#
# A verdict that prints "odds ratio 0.88" has told a reader who already knew
# what an odds ratio is. The number is the evidence, so it stays; this turns
# it into the sentence a person would say, and gives it a size label.
#
# The thresholds are here, in code, and they are relative change: how much
# the ratio moves away from 1. A ratio of 0.88 moves 12%, which is small; a
# ratio of 0.5 or 2.0 moves 50%, which is large. Epidemiology often calls a
# risk ratio under 1.5 "weak", which is the same judgement in other words.
# Fixed numbers rather than the model's adjective, so "small" means the same
# thing on every claim.
# ---------------------------------------------------------------------------

SMALL_BELOW = 0.20     # under 20% change either way
MODERATE_BELOW = 0.50  # 20% to 50%; at or above 50% is large

# Standardised mean differences are not ratios and have their own convention
# (Cohen): 0.2 small, 0.5 moderate, 0.8 large.
SMD_SMALL_BELOW = 0.5
SMD_MODERATE_BELOW = 0.8

# What the ratio is a ratio of, and the word a reader uses for it.
_RATIO_WORDS = [
    (r"odds ratios?|\bORs?\b|\baORs?\b", "odds"),
    (r"hazard ratios?|\bHRs?\b|\baHRs?\b", "risk over time"),
    (r"risk ratios?|relative risks?|\bRRs?\b|\baRRs?\b", "risk"),
    (r"incidence rate ratios?|\bIRRs?\b", "rate"),
    (r"rate ratios?", "rate"),
]

# "adjusted odds ratio 0.88", "OR, 0.88", "aOR = 0.88 (95% CI ...)".
_RATIO = re.compile(
    r"\b(?:adjusted\s+|pooled\s+|summary\s+)?"
    r"(odds ratios?|aORs?|ORs?|hazard ratios?|aHRs?|HRs?|risk ratios?|"
    r"relative risks?|aRRs?|RRs?|incidence rate ratios?|IRRs?|rate ratios?)"
    r"[\s,:=]*(?:of\s+|was\s+)?(\d+(?:\.\d+)?)\b", re.IGNORECASE)

# The same figure with the outcome named in between: "the hazard ratio for
# heart and blood vessel deaths for each more 50 g of egg was 1.09". The gap
# may not carry a negation, a percent sign or a second ratio name, so "the
# hazard ratio was not given, but mortality was 1.09" does not match and the
# figure is always read against the nearest ratio it belongs to.
_RATIO_WAS = re.compile(
    r"\b(?:adjusted\s+|pooled\s+|summary\s+)?"
    r"(odds ratios?|aORs?|ORs?|hazard ratios?|aHRs?|HRs?|risk ratios?|"
    r"relative risks?|aRRs?|RRs?|incidence rate ratios?|IRRs?|rate ratios?)"
    r"(?:(?!\b(?:not|no|ratios?|unknown|unclear|unreported)\b)[^.;:=%]){1,90}?"
    r"\b(?:was|were)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)

_SMD = re.compile(
    r"\b(?:standardi[sz]ed mean difference|SMD|Cohen's d)\s*"
    r"(?:was\s+|of\s+)?[,:=]?\s*(-?\d+(?:\.\d+)?)\b", re.IGNORECASE)

# "reduced infections by 12%", "a 47% reduction", "12% lower".
_PERCENT_CHANGE = re.compile(
    r"\b(?:reduc\w+|lower\w*|decreas\w+|increas\w+|rais\w+|higher|greater|fell|fall\w*|"
    r"drop\w*|rose|rise\w*|cut)\b[^.%]{0,30}?"
    r"(\d+(?:\.\d+)?)\s?%|\b(\d+(?:\.\d+)?)\s?%\s+(?:reduction|increase|lower|higher|"
    r"decrease|fewer|more)\b", re.IGNORECASE)

# "95%" in "95% confidence interval" is the interval's width, not an effect.
_INTERVAL = re.compile(r"\bC\.?I\.?\b|confidence interval|credible interval", re.IGNORECASE)


def size_label(change: float) -> str:
    """small / moderate / large, from a relative change like 0.12."""
    change = abs(change)
    if change < SMALL_BELOW:
        return "small"
    if change < MODERATE_BELOW:
        return "moderate"
    return "large"


def _smd_label(d: float) -> str:
    d = abs(d)
    if d < SMD_SMALL_BELOW:
        return "small"
    if d < SMD_MODERATE_BELOW:
        return "moderate"
    return "large"


_RATIO_NAMES = [(r"a?ORs?", "odds ratio"), (r"a?HRs?", "hazard ratio"),
                (r"a?RRs?", "risk ratio"), (r"IRRs?", "incidence rate ratio")]


def _ratio_name(name: str) -> str:
    """"aOR" reads back as "odds ratio"; a spelled-out name is left as it is."""
    for pattern, full in _RATIO_NAMES:
        if re.fullmatch(pattern, name, re.IGNORECASE):
            return full
    return name.lower()


def _ratio_word(name: str) -> str:
    for pattern, word in _RATIO_WORDS:
        if re.fullmatch(pattern, name, re.IGNORECASE):
            return word
    return "risk"


def effect_size(text: str) -> dict | None:
    """
    The first effect this text reports, read as a size rather than a number.

    Returns {"label": "small"|"moderate"|"large", "plain": "about 12% lower
    odds", "figure": "odds ratio 0.88"} or None when there is no figure to
    read. Ratios first, because they are what these abstracts report; a bare
    percentage only counts when it is attached to a word like "reduced", so
    "95% confidence interval" and "25% of participants" are not read as
    effects.
    """
    t = str(text or "")

    m = _RATIO.search(t) or _RATIO_WAS.search(t)
    if m:
        name, value = m.group(1), float(m.group(2))
        # A ratio of exactly 1 is no effect; 0 is a parse accident.
        if value > 0 and abs(value - 1.0) > 1e-9:
            word = _ratio_word(name)
            change = (1 - value) if value < 1 else (value - 1)
            direction = "lower" if value < 1 else "higher"
            pct = round(change * 100)
            return {"label": size_label(change),
                    "plain": f"about {pct}% {direction} {word}",
                    "figure": f"{_ratio_name(name)} {m.group(2)}"}

    m = _SMD.search(t)
    if m:
        d = float(m.group(1))
        if abs(d) > 1e-9:
            return {"label": _smd_label(d),
                    "plain": f"a difference of {abs(d):g} standard deviations",
                    "figure": f"standardised mean difference {m.group(1)}"}

    m = _PERCENT_CHANGE.search(t)
    if m and not _INTERVAL.search(t[m.end():m.end() + 24]):
        pct = float(m.group(1) or m.group(2))
        if 0 < pct < 100:
            direction = ("lower" if re.search(
                r"reduc|lower|decreas|fewer|fell|fall|drop|\bcut\b",
                m.group(0), re.IGNORECASE) else "higher")
            return {"label": size_label(pct / 100),
                    "plain": f"about {round(pct)}% {direction}",
                    "figure": f"{m.group(1) or m.group(2)}%"}
    return None


# ---------------------------------------------------------------------------
# Indirect evidence
#
# Some claims have no study that measures the thing claimed. "Creatine causes
# hair loss" is one: the nearest evidence is a trial that measured DHT, a
# hormone linked to pattern baldness, in 20 rugby players. That is worth
# showing and worth labelling, because a reader who is told "creatine raises
# DHT" has not been told that creatine causes hair loss.
# ---------------------------------------------------------------------------

# How much of an abstract counts as "what this paper is about". A study states
# what it gave people and what it measured in its title, background, objective
# and methods. Further down are results and safety tables, where a word can
# appear for an unrelated reason: a trial of baricitinib for alopecia reports
# creatine phosphokinase among its lab values, and reading that as a study of
# creatine is how a paper about something else ends up in an answer.
SUBJECT_WINDOW = 700


def measures(study, terms_group: str) -> bool:
    """
    True when this paper is about the thing `terms_group` names: an OR-group
    from the query, matched against the title and the opening of the abstract.
    """
    terms = [t for t in re.split(r"\s+OR\s+", (terms_group or "").strip("() "))
             if len(t.strip()) > 2]
    if not terms:
        return True
    study = study or {}
    body = (f"{study.get('title') or ''}  "
            f"{str(study.get('abstract') or '')[:SUBJECT_WINDOW]}")
    return any(re.search(r"\b" + re.escape(t.strip()) + r"\b", body, re.IGNORECASE)
               for t in terms)


def indirect(study, outcome: str, surrogate: str) -> str | None:
    """
    The surrogate this study measured instead of the claim's outcome, or None
    when it measured the outcome itself. `surrogate` is the OR-group the
    query step named as the indirect route, so the label is the term the
    paper actually uses rather than a guess.
    """
    if not surrogate or measures(study, outcome):
        return None
    body = f"{study.get('title') or ''} {study.get('abstract') or ''}"
    for term in re.split(r"\s+OR\s+", surrogate.strip("() ")):
        term = term.strip()
        if len(term) > 2 and re.search(re.escape(term), body, re.IGNORECASE):
            return term
    return None


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
    bars = mix(studies)
    snap = {
        "read": len(studies),
        "relied_on": sum(1 for s in studies if str(s.get("pmid")) in cited),
        "pooled": pooled,
        "trials": trials,
        "registered": sum(1 for s in studies if s.get("data_banks")),
        "strong": sum(1 for s in studies
                      if classify(s.get("publication_types")) == "strong"),
        "retracted": sum(1 for s in studies
                         if classify(s.get("publication_types")) == "retracted"),
        "year_from": years[0] if years else None,
        "year_to": years[-1] if years else None,
        "mix": bars,
    }
    snap.update(_chart_words(snap))
    return snap


# ---------------------------------------------------------------------------
# The chart's own words
#
# The evidence chart prints three sentences the reader can check: what it is
# based on, what else is true of the set, and, for a screen reader, the counts
# the bars are drawn from. All three are built here, once, so the server
# rendered page and a streamed check cannot word them differently.
# ---------------------------------------------------------------------------

def _n_things(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _chart_words(snap: dict) -> dict:
    read = snap["read"]
    if not read:
        return {"summary": "", "facts": "", "described": ""}

    used = snap["relied_on"]
    summary = [f"Based on {_n_things(read, 'study', 'studies')}",
               f"{used} used for this verdict" if used
               else "none used for this verdict"]
    if snap["strong"]:
        summary.append(f"{snap['strong']} strong")

    facts = []
    if snap["pooled"]:
        facts.append(_n_things(snap["pooled"], "pooled analysis", "pooled analyses"))
    if snap["trials"]:
        facts.append(_n_things(snap["trials"], "trial", "trials"))
    if snap["registered"]:
        facts.append(f"{snap['registered']} registered in advance")
    if snap["retracted"]:
        facts.append(_n_things(snap["retracted"], "retracted paper", "retracted papers"))
    if snap["year_from"]:
        span = (str(snap["year_from"]) if snap["year_from"] == snap["year_to"]
                else f"{snap['year_from']} to {snap['year_to']}")
        facts.append(f"published {span}")

    described = [f"{_n_things(read, 'study', 'studies')} read, "
                 f"{used} used for this verdict."]
    if snap["mix"]:
        described.append("By study design: "
                         + ", ".join(f"{m['count']} {m['label']}"
                                     for m in snap["mix"]) + ".")
    described.append("One bar per study, in the order of the list of studies, "
                     "taller for a stronger kind of study.")
    return {"summary": " \u00b7 ".join(summary),
            "facts": " \u00b7 ".join(facts),
            "described": " ".join(described)}


# ---------------------------------------------------------------------------
# Numbers an abstract spells out
#
# "Forty-five resistance-trained males (ages 18-40 years)" is where the
# figure 45 comes from, and a verdict that writes "45 people" is quoting
# that abstract rather than inventing a number. The grounding rule compares
# digits to digits, so without this it deleted the true sentence and left
# the breakdown's "who was studied" line blank. Methods sections spell the
# count that opens a sentence, which is exactly the count worth printing.
# ---------------------------------------------------------------------------

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000}


def _fold(words: list[str]) -> int:
    """"forty", "five" -> 45. "two", "hundred", "and", "ten" -> 210."""
    total = part = 0
    for word in words:
        if word == "and":
            continue
        if word == "hundred":
            part = (part or 1) * 100
        elif word == "thousand":
            total += (part or 1) * 1000
            part = 0
        else:
            part += _NUM_WORDS[word]
    return total + part


def written_numbers(text) -> set[str]:
    """
    Every number this text spells in words, as digits.

    Only the whole run counts: "forty-five" yields 45 and not 40 or 5, so
    reading this set as permission never widens into permission to write a
    number the abstract does not claim. "and" continues a run only after a
    scale word, where it is part of the number rather than a conjunction.
    """
    found, run = set(), []

    def close():
        if run:
            value = _fold(run)
            if value:
                found.add(str(value))
        run.clear()

    for word in re.findall(r"[A-Za-z]+", str(text or "")):
        word = word.lower()
        if word in _NUM_WORDS or word in _SCALES:
            run.append(word)
        elif word == "and" and run and run[-1] in _SCALES:
            run.append(word)
        else:
            close()
    close()
    return found
