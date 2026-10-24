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
# Papers about a topic rather than about people they enrolled.
_OVERVIEW_TYPES = ("Review", "Practice Guideline", "Guideline", "Consensus Development")

# "Women and men with overweight" is everyone, not one sex. Read before the
# table above, because "men" is matched first and the audit found a mixed
# trial labelled "men only".
_BOTH_SEXES = re.compile(r"\bwomen\b.{0,40}\bmen\b(?!tal)|\bmen\b(?!tal).{0,40}\bwomen\b|"
                         r"\bmales? and females?\b|\bfemales? and males?\b|\bboth sexes\b",
                         re.IGNORECASE)
_SEX_LABELS = ("men only", "women only")


def population(study, claim: str = "") -> str | None:
    """
    The narrow group a study was run in, in plain words, or None when it
    looks like a general population.

    For a claim about the risk of a disease, that disease is the outcome:
    "physical activity and incident type 2 diabetes" is a study of who gets
    diabetes, run in people who don't have it yet, not a study of people
    with diabetes.

    The title counts wherever it matches. The abstract counts only for
    papers that enrolled people: a meta-analysis of 25 trials mentions
    children in a subgroup line without being a study of children, and
    mislabelling the broadest paper in the set as the narrowest would
    invert the whole point of the flag. A narrative review or a guideline
    enrolled nobody either. "Antimicrobial treatment of ENT infections"
    names children where it turns to ear infections, and was read as a
    study of children; that turned "antibiotics do not cure colds" into
    "it's complicated".
    """
    study = study or {}
    title = str(study.get("title") or "")
    pooled = (strongest_label(study.get("publication_types")) in _POOLED
              or any(t.startswith(_OVERVIEW_TYPES) for t in study.get("publication_types") or []))
    # Trials state who they enrolled early, in Background or Methods.
    body = "" if pooled else str(study.get("abstract") or "")[:700]
    mixed_sex = bool(_BOTH_SEXES.search(title) or (body and _BOTH_SEXES.search(body)))
    risk = bool(claim and RISK_CLAIM.search(claim))
    for pattern, label in POPULATIONS:
        if mixed_sex and label in _SEX_LABELS:
            continue
        if risk and label in DISEASE_GROUPS and re.search(pattern, claim, re.IGNORECASE):
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
    # A claim about treating a disease is about the people who have it:
    # "apple cider vinegar cures diabetes" can only be tested in people with
    # diabetes, though it never says "type 2". Not for a risk claim, whose
    # answer is in the people who do not have the disease yet.
    if not risk:
        found.update(label for pattern, label in _TREATED if pattern.search(claim))
    return found


_TREATED = [(re.compile(p, re.IGNORECASE), label) for p, label in (
    (r"\bdiabet", "people with diabetes"),
    (r"\bcancers?\b|\btumou?rs?\b", "people treated for cancer"),
    (r"\bkidney (?:disease|failure)", "people with kidney failure"),
)]


# What a study's own authors concluded. A structured abstract keeps the
# authors' heading, so the conclusion turns up under several names, at the
# start of a line or numbered inside one ("(4) Conclusions: ..."). An
# abstract with headings but no conclusion heading has nothing to quote.
_CONCLUSION_HEAD = re.compile(
    r"(?:^|(?<=\s))(?:\(\d\)\s*)?(?i:authors'?\s+)?(?i:main\s+)?(?i:conclusions?|interpretation"
    r"|implications?|significance|clinical relevance)(?:\s+(?:and|&)\s+[a-z]+)?\s*:\s*"
    # The BMJ's style: a capitalised heading with no colon, straight after
    # the full stop that ends the section before it.
    r"|(?:^|(?<=[.]))\s*(?:Conclusions?|CONCLUSIONS?|Interpretation|INTERPRETATION)\s+(?=[A-Z])")
_ANY_HEAD = re.compile(r"(?:^|\s)(?:\(\d\)\s*)?[A-Z][A-Za-z &/'-]{2,40}:\s")
_NEXT_HEAD = re.compile(r"\s(?:\(\d\)\s*)?[A-Z][A-Z &/'-]{2,40}:\s|\s\(\d\)\s*[A-Z][A-Za-z ]{2,40}:\s")
# With no headings at all, only a sentence that says it is the conclusion is
# one. The last sentence of an abstract is as often a plan or a summary of
# what the paper covers, so it is never taken on position alone.
_CONCLUDING = re.compile(
    r"^(?:in conclusion|in summary|to conclude|overall|taken together|together"
    r"|(?:these|our|the(?: present)?|this study'?s?) (?:results|findings|data|study|trial|review"
    r"|analysis|meta-analysis) (?:suggest|indicate|show|shows|demonstrate|demonstrates|support"
    r"|provide|provides|found|confirm|confirms)|we conclude|this (?:study|trial|review)"
    r" (?:suggests|indicates|shows|demonstrates|found|provides))\b", re.I)
# Housekeeping that ends some abstracts and is not a finding.
_TRAILER = re.compile(
    r"(?i)(regist(?:ration|ered)|clinicaltrials\.gov|\bNCT\d|PROSPERO|ISRCTN|copyright"
    r"|\u00a9|all rights reserved|level of evidence|funded by|conflicts? of interest)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
CONCLUSION_MAX = 420


def _undash(text: str) -> str:
    """The page carries no dashes: a range reads "to", any other a comma."""
    text = re.sub(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)", " to ", text)
    return re.sub(r"\s*[\u2013\u2014]\s*", ", ", text)


def _first_sentences(text: str) -> str:
    """Whole sentences from the start of text, up to CONCLUSION_MAX."""
    picked = []
    for s in (s for s in _SENTENCE.split(text.strip()) if not _TRAILER.search(s)):
        if picked and len(" ".join(picked + [s])) > CONCLUSION_MAX:
            break
        picked.append(s)
    return " ".join(picked)


def conclusion(study) -> str:
    """
    What a study's authors concluded, in their own words: the conclusion
    section of a structured abstract, or, in one with no headings, the
    sentences from the one that says it is concluding ("In conclusion",
    "These results suggest"). Quoted, never rewritten, except that a dash
    becomes the words or the comma the rest of the page uses. Housekeeping
    such as a trial registration is never quoted. "" when the abstract
    says no conclusion in so many words: a guess would be put in the
    authors' mouths.
    """
    text = " ".join(ln.strip() for ln in (study.get("abstract") or "").split("\n") if ln.strip())
    if not text:
        return ""
    # A heading run into the sentence before it ("quality.Conclusions") is
    # given back its space, so sentences split where they end.
    text = re.sub(r"(?<=[a-z0-9)][.])(?=[A-Z][a-z])", " ", text)
    m = _CONCLUSION_HEAD.search(text)
    if m:
        body = text[m.end():]
        nxt = _NEXT_HEAD.search(body)
        said = _first_sentences(body[:nxt.start()] if nxt else body)
    elif _ANY_HEAD.match(text):
        return ""
    else:
        sents = _SENTENCE.split(text)
        at = next((i for i in range(len(sents) - 1, -1, -1) if _CONCLUDING.match(sents[i])), None)
        if at is None:
            return ""
        said = _first_sentences(" ".join(sents[at:]))
    said = _undash(said).strip()
    if said and said[-1] not in ".!?":
        said += "."
    return said if len(said.split()) >= 4 else ""


def conclusions(studies, cited_pmids=()) -> list[dict]:
    """
    {"n": study number, "said": conclusion} for every study the verdict
    leaned on that has one, in study order. Read off the records, so it
    costs no model call and carries nothing a model wrote.
    """
    cited = {str(p) for p in (cited_pmids or ())}
    out = []
    for i, s in enumerate(studies or []):
        if str(s.get("pmid")) in cited:
            said = conclusion(s)
            if said:
                out.append({"n": i + 1, "said": said})
    return out


# A group that already is one sex. Listed beside it, "women only" adds
# nothing: "only in pregnant women, women only" says women twice.
_IMPLIES_SEX = {"pregnant women": "women only", "women after menopause": "women only"}


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
    # A broader label that a more specific one in the list already covers
    # is dropped, so the same group is never named twice.
    covered = {_IMPLIES_SEX[label] for label in seen if label in _IMPLIES_SEX}
    return [label for label in seen if label not in covered]


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
    return bool(used) and all(population(s, claim) and population(s, claim) not in own
                              for s in used)


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


# ---------------------------------------------------------------------------
# Whether a figure is a difference at all
#
# "An odds ratio of 0.99" is not "1% lower odds, a small effect" when its 95%
# interval runs from 0.92 to 1.06: the interval includes no effect at all, and
# the honest words are "no clear difference". The audit found exactly that
# sentence under "vaccines cause autism". So before a figure is sized, its
# interval is read, from the prose if the prose kept it and otherwise from the
# abstract the figure was copied from.
# ---------------------------------------------------------------------------

_NUM = r"[-\u2212]?\d+(?:\.\d+)?"
_SEP = r"\s*(?:to|\u2013|-|,|;)\s*"
_CI_PAIR = re.compile(r"(?:C\.?I\.?|confidence interval|credible interval)\]?"
                      r"[\s,:=]*(?:of\s+|was\s+|from\s+)?[\(\[]?\s*(" + _NUM + ")" + _SEP
                      + "(" + _NUM + ")", re.IGNORECASE)
_BARE_PAIR = re.compile(r"^\s*[\(\[]\s*(" + _NUM + ")" + _SEP + "(" + _NUM + r")\s*[\)\]]")
_P_VALUE = re.compile(r"\bP\s*(=|>|\u2265|<|\u2264)\s*(0?\.\d+|1(?:\.0+)?)\b", re.IGNORECASE)
NOT_SIGNIFICANT = re.compile(
    r"\bnot (?:statistically )?significant(?:ly)?\b|\bnon-?significant\b|"
    r"\bno (?:statistically )?significant\b|\bdid not (?:significantly )?differ\b|"
    r"\bno (?:clear |significant |real )?difference\b|\bcould be chance\b", re.IGNORECASE)
_RATIO_BEFORE = re.compile(r"\b(?:a?ORs?|a?HRs?|a?RRs?|IRRs?|odds ratios?|hazard ratios?|"
                           r"risk ratios?|relative risks?|rate ratios?)\b", re.IGNORECASE)
_DIFF_BEFORE = re.compile(r"\b(?:W?MDs?|SMDs?|mean difference|difference|net|"
                          r"standardi[sz]ed mean difference|Hedges'? g|Cohen'?s d)\b",
                          re.IGNORECASE)


def _num(text: str) -> float:
    return float(text.replace("\u2212", "-"))


def _interval_after(text: str, start: int):
    """The (low, high) interval printed straight after a figure, or None."""
    window = text[start:start + 90]
    m = _BARE_PAIR.match(window) or _CI_PAIR.search(window)
    if not m:
        return None
    # The interval has to belong to this figure: another number in between
    # ("OR 0.88 and RR 1.2 (95% CI ...)") means it belongs to that one.
    between = re.sub(r"\b9[059]\s?%", "", window[:m.start(1)])
    if re.search(r"\d", re.sub(r"(?:C\.?I\.?|confidence interval).*", "", between,
                                flags=re.IGNORECASE | re.DOTALL)):
        return None
    return _num(m.group(1)), _num(m.group(2))


def _crosses(pair, null: float) -> bool:
    lo, hi = min(pair), max(pair)
    return lo <= null <= hi


def significance(text: str, figure: str) -> str | None:
    """
    "null" when this text reports the figure as no clear difference (its
    interval includes no effect, its p value is 0.05 or more, or its sentence
    says it was not significant), "clear" when its interval or p value rules
    that out, None when the text does not say or the figure is not a
    comparison. Only ratios and differences are judged: a group's own average
    has an interval too, and it says nothing about whether two groups differ.
    """
    t = str(text or "")
    bare = str(figure or "").lstrip("-\u2212")
    if not bare:
        return None
    for m in re.finditer(r"(?<![\d.])[-\u2212]?" + re.escape(bare) + r"(?![\d])", t):
        before = t[max(0, m.start() - 60):m.start()]
        if _RATIO_BEFORE.search(before):
            null = 1.0
        elif _DIFF_BEFORE.search(before):
            null = 0.0
        else:
            continue
        pair = _interval_after(t, m.end())
        if pair:
            return "null" if _crosses(pair, null) else "clear"
        pv = _P_VALUE.search(t[m.end():m.end() + 80])
        if pv:
            op, val = pv.group(1), float(pv.group(2))
            if op in ("=", ">", "\u2265") and val >= 0.05:
                return "null"
            if (op in ("<", "\u2264") and val <= 0.05) or (op == "=" and val < 0.05):
                return "clear"
        start = max(t.rfind(".", 0, m.start()), t.rfind("\n", 0, m.start())) + 1
        end = t.find(". ", m.end())
        if NOT_SIGNIFICANT.search(t[start:end if end > 0 else len(t)]):
            return "null"
    return None


NULL_EFFECT = {"label": "none", "plain": "no clear difference between the groups",
               "null": True}


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
        pair = _interval_after(t, m.end())
        if value > 0 and pair and _crosses(pair, 1.0):
            return dict(NULL_EFFECT, figure=f"{_ratio_name(name)} {m.group(2)}")
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
        pair = _interval_after(t, m.end())
        if pair and _crosses(pair, 0.0):
            return dict(NULL_EFFECT, figure=f"standardised mean difference {m.group(1)}")
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


# British spellings, read as American on both sides of a match. The main
# review on a question can be written in either: "Efficacy of homoeopathic
# treatment" did not count as about "homeopathic treatment", fell to the
# band of papers about neither half of the claim, and was dropped.
_US_SPELLING = [
    (r"\b(hom)oeo", r"\1eo"), (r"\boe(soph|str|dem)", r"e\1"), (r"(diarrh|dyspn|apn)oe", r"\1e"),
    (r"\bfoet", "fet"), (r"\bcoeliac", "celiac"), (r"(an|isch|leuk|sept)aem", r"\1em"),
    (r"\bhaem", "hem"), (r"\b(ortho)?paed", r"\1ped"), (r"\b(tum|behavi|col|lab)our", r"\1or"),
    (r"\b(fib|cent|lit)re(s?)\b", r"\1er\2"),
    (r"\b(randomi|organi|hospitali)s(ation|ed|e|ing)\b", r"\1z\2"),
]


def _us(text: str) -> str:
    for pattern, plain in _US_SPELLING:
        text = re.sub(pattern, plain, text, flags=re.IGNORECASE)
    return text


def _query_term(term: str) -> str:
    """A query term as a whole-word pattern that also takes its plural or
    singular: "abdominal exercise" finds "abdominal exercises", and
    "probiotics" finds "probiotic"."""
    word = re.escape(_us(term.strip()))
    if term.endswith("s") and not term.endswith("ss") and len(term) > 4:
        word = word[:-1]
    return r"\b" + word + r"(?:s|es)?\b"


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
    body = _us(f"{study.get('title') or ''}  "
               f"{str(study.get('abstract') or '')[:SUBJECT_WINDOW]}")
    return any(re.search(_query_term(t), body, re.IGNORECASE) for t in terms)


# Words in an outcome group that name no outcome on their own: "loss" in
# "hair loss", "risk" in "cancer risk", "blood" in "blood pressure".
_NOT_AN_OUTCOME = frozenset("""
    risk risks loss losses level levels health effect effects rate rates change changes
    symptom symptoms function functions quality disease diseases disorder disorders outcome
    outcomes score scores status severity incidence prevalence reduction increase decrease
    improvement performance total mean body human people adult adults child children acute
    chronic severe mild high higher lower daily general overall condition conditions problem
    problems event events common upper lower blood test tests measure measures marker markers
    complicated complication complications related associated induced from with patients
    """.split())
_STEM_END = re.compile(r"(?:ness|ings?|ed|(?<![siu])s)$")
# An abstract can name the outcome in other words than the query did:
# "heart" as "cardiovascular", "blood sugar" as "glucose". A study that
# uses any word of the family has said something about that outcome.
_OUTCOME_KIN = [
    ("heart", "cardi", "coronary", "myocard", "cvd", "chd"),
    ("sugar", "glucose", "glyc", "insulin", "hba1c", "diabet"),
    ("cancer", "tumor", "neoplas", "carcino", "oncolog", "malignan", "lymphoma", "leuk",
     "melanoma", "sarcoma"),
    ("kidney", "renal", "nephr", "stone", "calculi", "lithiasis"),
    ("liver", "hepat"),
    ("stroke", "cerebrovascular"),
    ("weight", "obes", "adipos", "bmi", "body mass", "fat"),
    ("sleep", "insomnia"),
    ("depress", "mood"),
    ("memory", "cognit", "dementia"),
    ("skin", "derma", "cutaneous"),
    ("hair", "alopecia", "bald"),
    ("bone", "fracture", "osteopor"),
    ("teeth", "tooth", "dental", "caries", "cavit"),
    ("pressure", "hypertens"),
    ("cholesterol", "lipid", "ldl", "hdl", "triglycer"),
    ("cold", "respiratory", "urti"),
    ("death", "mortality", "surviv"),
    ("autism", "asd"),
    ("inflammat", "crp", "c-reactive"),
]


def outcome_kin(stems: list[str]) -> list[str]:
    out = list(stems)
    for family in _OUTCOME_KIN:
        if any(s.startswith(f) or f.startswith(s) for s in stems for f in family):
            out += [f for f in family if f not in out]
    return out


def outcome_stems(group: str) -> list[str]:
    """
    The words of a query's outcome group that name the outcome, cut to a
    stem: "(hair loss OR alopecia OR baldness)" gives hair, alopecia, bald.
    """
    stems = []
    for word in re.findall(r"[a-z]+", _us(group or "").lower()):
        if len(word) < 4 or word in _NOT_AN_OUTCOME:
            continue
        stem = _STEM_END.sub("", word) if len(word) > 5 else word
        if len(stem) >= 4 and stem not in stems:
            stems.append(stem)
    return stems


def names_any(text: str, stems: list[str]) -> bool:
    """True when the text uses one of these stems as the start of a word."""
    text = _us(text or "")
    return any(re.search(r"\b" + re.escape(s), text, re.IGNORECASE) for s in stems)


def never_mentions(study, stems: list[str]) -> bool:
    """
    True when a study's title and whole abstract use none of these stems,
    so it cannot have measured that outcome. False when there is no
    abstract to read: absence there proves nothing.
    """
    abstract = str((study or {}).get("abstract") or "").strip()
    if not stems or not abstract or abstract.lower().startswith("(no abstract"):
        return False
    return not names_any(f"{study.get('title') or ''} {abstract}", outcome_kin(stems))


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
    years = sorted(int(y) for y in (re.search(r"\b(?:1[89]|20)\d\d\b", str(s.get("year") or ""))
                                    for s in studies) if y for y in [y.group(0)])
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
        # A strong design about something else, or one that tests nothing
        # in people, is not strong evidence here.
        "strong": sum(1 for s in studies
                      if classify(s.get("publication_types")) == "strong"
                      and not s.get("off_topic") and not s.get("background")),
        "off_topic": sum(1 for s in studies if s.get("off_topic")),
        "background": sum(1 for s in studies
                          if s.get("background") and not s.get("off_topic")),
        # Why each background record is one, once each, in a fixed order:
        # the chart and the study list name the kinds that are there.
        "background_kinds": [k for k in (BG_LAB, BG_OPINION, BG_GUIDE, BG_NOT_TESTED)
                             if any(background_why(s) == k for s in studies
                                    if not s.get("off_topic"))],
        "retracted": sum(1 for s in studies
                         if classify(s.get("publication_types")) == "retracted"),
        "year_from": years[0] if years else None,
        "year_to": years[-1] if years else None,
        "mix": bars,
        # The shelf's key: only the cloths this set actually has, in legend
        # order, so the key never explains a colour that is not on the shelf.
        "kinds": [{"kind": k, "label": KIND_LABELS[k]} for k in KINDS
                  if any(kind(s) == k for s in studies)],
    }
    snap.update(_chart_words(snap))
    # The name of the study list's background group, worded here so the
    # server page and a streamed check say the same thing.
    snap["background_label"] = ("Background only: animal and lab studies"
                                if snap["background_kinds"] in ([], [BG_LAB])
                                else "Background only: not tests of this claim")
    return snap


# ---------------------------------------------------------------------------
# What the evidence shows about the claim
#
# The line under the chart used to end "so the evidence is strong". That was
# about the designs, not the claim: two meta-analyses showing that apple
# cider vinegar lowers blood sugar a little are strong evidence that it does
# not cure diabetes, and "strong" printed beside "Likely false" read as
# strong evidence for the cure. The level word still comes from the designs
# that were used (the meter's legend explains it), but the line now says
# what that evidence is evidence of: "Strong evidence of a partial effect,
# not a cure". Built here from facts the page already has (the stamp, the
# size of the effect, the claim's own words), so it costs no model call and
# a streamed check and a shared link cannot word it differently.
# ---------------------------------------------------------------------------

# What an absolute claim promises, as a noun for "not ...".
_PROMISES = [
    (re.compile(r"cur", re.IGNORECASE), "a cure"),
    (re.compile(r"revers", re.IGNORECASE), "a reversal"),
    (re.compile(r"prevent|protect|block", re.IGNORECASE), "complete prevention"),
    (re.compile(r"stop", re.IGNORECASE), "a complete stop"),
    (re.compile(r"eliminat|eradicat|rid of|wipe", re.IGNORECASE), "getting rid of it"),
    (re.compile(r"heal|fix", re.IGNORECASE), "a full fix"),
]


OBSERVATIONAL_TAIL = "observational studies (can't show cause)"
OBSERVATIONAL_HEAD = f"Evidence from {OBSERVATIONAL_TAIL}"


def level_word(studies, cited_pmids=()) -> str:
    """
    How much the studies used can be called evidence of, in one word: Strong
    (randomized trials, or reviews of them, that are not small), Limited
    (the trials or pilots used are small), Observational (cohort,
    case-control or survey studies, which can show a link and not a cause),
    Moderate (human studies or reviews of no one design, non-randomized
    trials) or Weak (case reports, letters, opinion). "" when none were
    used. The meter's legend says the same.

    The tier of the designs sets the ceiling, as it always did, and what the
    studies actually did can only lower it: a meta-analysis of cohort studies
    is a strong design on paper and observational evidence in fact.
    """
    cited = [s for s in studies or [] if s.get("pmid") in set(cited_pmids or ())]
    if not cited:
        return ""
    tiers = {classify(s.get("publication_types")) for s in cited}
    if "strong" not in tiers and "moderate" not in tiers:
        return "Weak"
    basis = evidence_basis(studies, cited_pmids)
    if basis == "small":
        return "Limited"
    if basis == "observational":
        return "Observational"
    if basis == "mixed" or "strong" not in tiers:
        return "Moderate"
    return "Strong"


def observational_only(studies, cited_pmids=()) -> bool:
    """True when the studies used are observational and no trial is among
    them, so "strong" would describe the paperwork, not the evidence."""
    return evidence_basis(studies, cited_pmids) == "observational"


def line_head(verdict: str, studies, cited_pmids=(), comparative: bool = False,
              outcome: str = "") -> str:
    """
    How the evidence line opens: "Strong evidence", "Moderate evidence",
    "Low certainty evidence", "Evidence from observational studies (can't
    show cause)" and the rest, or "" when nothing was used. The line and the
    prose gate (verdict._read_verdict) both read it from here, so the page
    can never say strong in the takeaway and moderate under the chart.
    `comparative` and `outcome` are the same inputs the verdict's own cap
    reads: whether the claim compares two things, and the query's outcome
    group. `verdict` only matters to the cap that blames other factors,
    which weakens a "true" and not a "false".
    """
    level = level_word(studies, cited_pmids)
    if not level:
        return ""
    # What the line opens with. Observational evidence says what it is and
    # what it cannot show; everything else is "<word> evidence".
    head = OBSERVATIONAL_HEAD if level == "Observational" else f"{level} evidence"
    # A certainty word replaces the opening, but observational evidence keeps
    # saying what it is: "Low certainty evidence from observational studies
    # (can't show cause)". A rating never hides the design.
    via = f" from {OBSERVATIONAL_TAIL}" if level == "Observational" else ""
    # A lead review that rates its own evidence says how sure it is in its
    # own words, and those words replace the one read off the designs: a
    # meta-analysis of low-certainty evidence is not "strong evidence". But a
    # review's "moderate" or "high" is a word about its trials, so it stands
    # only over trial evidence that is not small: it never lifts cohort
    # studies or pilots above what they are. A low rating always shows.
    lead = lead_certainty(studies, cited_pmids, outcome)
    if lead and (lead in ("low", "very low") or evidence_basis(studies, cited_pmids) == "trial"):
        span = certainty_range(lead_review(studies, cited_pmids, outcome), outcome) or lead
        head = f"{span.capitalize()} certainty evidence{via}"
    cap = certainty_cap(studies, cited_pmids, comparative, outcome)
    # A review that puts the link down to other factors weakens the case for
    # the claim, not the case against it. Small trials are already named by
    # "Limited" (and cohorts by their own words), so the cap does not say
    # the same thing twice in a weaker voice.
    capped = (cap and lead != "very low"
              and not (verdict == "false" and cap == CONFOUNDED_REASON)
              and not (cap == SMALL_REASON and level in ("Limited", "Observational")))
    if capped:
        head = f"Low certainty evidence{via}"
    # Any review that rates the claim's own outcome low says so too, not just
    # the lead one.
    elif lead not in ("low", "very low") and rated_low_on_outcome(studies, cited_pmids, outcome):
        head = f"Low certainty evidence{via}"
    return head


def evidence_line(verdict: str, studies, cited_pmids=(), claim: str = "",
                  effect_label: str | None = None, absolute: str = "",
                  comparative: bool = False, outcome: str = "") -> str:
    """
    What the evidence used shows about the claim, as one short line with no
    full stop, or "" when nothing was used (the page says "no direct
    evidence" there). `absolute` is the claim's promise ("cure"), from
    breakdown.absolute_claim; `effect_label` is the breakdown's size label;
    `outcome` is the query's outcome group. The opening is line_head's.
    """
    head = line_head(verdict, studies, cited_pmids, comparative, outcome)
    if not head or verdict not in ("true", "false", "complicated"):
        return ""
    size = effect_label if effect_label in ("small", "moderate", "large") else ""
    promise = next((noun for pattern, noun in _PROMISES if pattern.search(absolute or "")),
                   "what the claim promises") if absolute else ""
    if verdict == "true":
        return f"{head} for this claim" + (f", a {size} effect" if size else "")
    if verdict == "false":
        if effect_label == "none":
            return f"{head} of no clear effect"
        if absolute:
            return f"{head} of a {size or 'partial'} effect, not {promise}"
        return f"{head} against this claim"
    narrow = narrow_populations(studies, cited_pmids)
    if narrow and narrow_only(studies, cited_pmids, claim):
        return f"{head}, but only in {', '.join(narrow)}"
    if effect_label == "none":
        return f"{head} of no clear effect, but no plain yes or no"
    if size:
        return f"{head} of a {size} effect, but no plain yes or no"
    return f"{head}, but no plain yes or no"


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


# How the chart's description names each kind of background record.
_BG_PLURAL = {"animal or lab": "animal or lab studies", "opinion piece": "opinion pieces",
              "product guide": "product guides", "not a test of this claim": "other papers"}


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
    lab_only = snap.get("background_kinds", [BG_LAB]) in ([], [BG_LAB])
    if snap.get("background"):
        facts.append(f"{snap['background']} animal or lab, background only" if lab_only
                     else f"{snap['background']} background only")
    if snap.get("off_topic"):
        facts.append(f"{snap['off_topic']} off topic")
    if snap["year_from"]:
        span = (str(snap["year_from"]) if snap["year_from"] == snap["year_to"]
                else f"{snap['year_from']} to {snap['year_to']}")
        facts.append(f"published {span}")

    described = [f"{_n_things(read, 'study', 'studies')} read, "
                 f"{used} used for this verdict."]
    kinds = [_BG_PLURAL[k] for k in snap.get("background_kinds") or [] if k in _BG_PLURAL]
    if snap.get("background") and (lab_only or not kinds):
        described.append(f"{snap['background']} of them "
                         + ("an animal or lab study" if snap["background"] == 1
                            else "animal or lab studies")
                         + ", background only, never used for a claim about people.")
    elif snap.get("background"):
        said = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
        described.append(f"{snap['background']} of them background only, never used for "
                         f"this verdict, because they do not test the claim in people: {said}.")
    if snap.get("off_topic"):
        described.append(f"{snap['off_topic']} of them off topic for this claim.")
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


# ---------------------------------------------------------------------------
# How sure the evidence is
#
# "Likely true" is a claim about the evidence as much as about the answer.
# The audit stamped it on ashwagandha from a review whose own abstract says
# "the certainty of the evidence was low", and on standing desks from trials
# of 27 to 56 office workers, two of them feasibility studies and one that
# compared two ways of using the desk with no group that went without. Each
# of those papers is real and each is cited correctly. None of them, or all
# of them together, can carry a confident yes. These read that off the
# records, so the cap does not depend on the model noticing it.
# ---------------------------------------------------------------------------

SMALL_TRIAL = 100

_PEOPLE = (r"(?:participants|adults|patients|subjects|individuals|people|persons|men|"
           r"women|children|adolescents|employees|workers|volunteers|students|athletes|"
           r"infants|smokers|residents|veterans|respondents|nurses|pairs|couples)")
_SKIP_BETWEEN = re.compile(r"\b(?:years?|months?|weeks?|days?|hours?|minutes?|mg|kg|g|ml|"
                           r"percent|times)\b", re.IGNORECASE)
_DIGIT_PEOPLE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)\s+((?:[A-Za-z-]+\s+){0,3}?)"
                           + _PEOPLE + r"\b", re.IGNORECASE)
_WORD_PEOPLE = re.compile(r"\b((?:(?:" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True))
                          + r"|hundred|thousand|and)[\s-]+)+)((?:[A-Za-z-]+\s+){0,3}?)"
                          + _PEOPLE + r"\b", re.IGNORECASE)
_N_EQUALS = re.compile(r"\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)")
REVIEW_TYPES = ("Meta-Analysis", "Network Meta-Analysis", "Systematic Review")
TRIAL_TYPES = ("Randomized Controlled Trial", "Controlled Clinical Trial", "Clinical Trial")


def _text(study) -> str:
    return f"{study.get('title') or ''}. {study.get('abstract') or ''}"


def is_review(study) -> bool:
    return any(t.startswith(REVIEW_TYPES) for t in study.get("publication_types") or [])


def is_trial(study) -> bool:
    if is_review(study):
        return False
    return (any(t.startswith(TRIAL_TYPES) for t in study.get("publication_types") or [])
            or bool(re.search(r"\brandomi[sz]ed\b|\btrial\b", _text(study), re.IGNORECASE)))


def sample_size(study) -> int | None:
    """The most people this record says it enrolled, or None if it never says."""
    text = _text(study)
    found = []
    for m in _DIGIT_PEOPLE.finditer(text):
        if not _SKIP_BETWEEN.search(m.group(2)):
            found.append(int(m.group(1).replace(",", "")))
    for m in _WORD_PEOPLE.finditer(text):
        if not _SKIP_BETWEEN.search(m.group(2)):
            # "two groups of adults" is not a sample; nobody spells a
            # sample of under ten in words, so a small one is a count of
            # something else.
            found += [int(v) for v in written_numbers(m.group(1)) if int(v) >= 10]
    found += [int(n.replace(",", "")) for n in _N_EQUALS.findall(text)]
    return max(found) if found else None


_PILOT = re.compile(r"\bpilot\b|\bfeasibility\b|\bproof[- ]of[- ]concept\b", re.IGNORECASE)
_NO_CONTROL_SAID = re.compile(
    r"\bsingle[- ](?:arm|group)\b|\bone[- ]group\b|\bpre[- ]?(?:and[- ])?post\b|"
    r"\bpre-?test[- /]post-?test\b|\bbefore[- ]and[- ]after\b|\buncontrolled\b|"
    r"\bwithout (?:a )?control group\b|\bno control group\b", re.IGNORECASE)
_CONTROL_SAID = re.compile(
    r"\bcontrol|\bplacebo|\bsham\b|\busual care\b|\bstandard (?:care|practice|working)|"
    r"\bwait[- ]?(?:ing[- ])?list|\bno[- ](?:intervention|treatment|supplement)|"
    r"\buntreated\b|\bdelayed\b|\bat the end of\b|\bcross-?over\b|\bcomparison group\b|"
    r"\bvehicle\b|\bdummy\b|\bnothing\b|\bnon-?users?\b", re.IGNORECASE)
_INDUSTRY = re.compile(
    r"\bindustry[- ](?:funded|sponsored)\b|\b(?:funded|sponsored|supported) by\b[^.]{0,80}?"
    r"\b(?:Ltd|Inc|Pvt|GmbH|LLC|Pharma\w*|Laborator(?:y|ies)|Corporation|Co\.)", re.IGNORECASE)


def uncontrolled(study) -> bool:
    """
    A trial in which nobody went without the thing being tested: one group
    measured before and after, or two versions of the treatment compared
    with each other. It can show the versions differ; it cannot show the
    treatment beats doing nothing, which is what a randomised trial is
    usually taken to mean.
    """
    if not is_trial(study):
        return False
    text = _text(study)
    if _NO_CONTROL_SAID.search(text):
        return True
    return not _CONTROL_SAID.search(text)


# A review's own rating of its evidence, GRADE style, in the words it uses.
# "Quality" counts only beside "evidence": "more high-quality trials are
# needed" rates nothing. Lowest first, so a range reads as its low end.
CERTAINTY_LEVELS = ("very low", "low", "moderate", "high")
_LVL = r"(?:very[- ]low|low|moderate|high)"
_RATED = re.compile(
    rf"\b({_LVL})(?:[- ]to[- ]({_LVL}))?[- ](?:certainty|(?:quality|grade)(?=[- ](?:of[- ](?:the[- ])?)?evidence))\b|"
    rf"\b(?:(?:certainty|quality) of (?:the )?evidence|(?:overall|evidence) certainty) "
    rf"(?:was|is|were|ranged from|ranging from) (?:rated |judged |graded |considered |assessed )?"
    rf"(?:as )?(?:to be )?(?:being )?({_LVL})(?: to ({_LVL}))?\b|"
    rf"\bevidence (?:was|is|were) of ({_LVL}) (?:certainty|quality)\b|"
    rf"\bGRADE\W{{1,3}}({_LVL})\b", re.IGNORECASE)
_CLAUSE = re.compile(r"[()\[\]]|(?<=[.;])\s+|,\s+(?:but|whereas|while)\s+")

# A review asking for better evidence rates nothing: "the need for high-quality
# evidence", "no high certainty evidence", "high quality evidence is needed".
# Read from the few words beside the match, never the whole clause, so
# "no difference in mortality, low certainty evidence" still rates mortality.
_WANTS_BEFORE = re.compile(
    r"\b(?:needs?|needed|requires?|required|warrants?|warranted|calls? for|lacks?|lacked|"
    r"lacking|absence of|more|further|future|additional|robust|await\w*|no|little|"
    r"without|insufficient|good|better|stronger|sufficient),?"
    r"(?:\s+(?:of|for|any|the|a|an|enough|such|robust|good|better|stronger|more|further|"
    r"additional),?)*\s+$", re.IGNORECASE)
_WANTS_AFTER = re.compile(
    r"^(?:\s+(?:of\s+)?(?:the\s+)?(?:evidence|studies|trials|research|data)"
    r"(?:\s+(?:from|of|in|on)\s+(?:\w+\s+){0,3}\w+)?)?"
    r"\s*(?:(?:is|are|was|were|will be|would be|remains?|remain)\s+)?"
    r"(?:needed|required|warranted|necessary|lacking|scarce|sparse|to\s+(?:support|inform|"
    r"guide|confirm|establish|draw|determine|justify))\b", re.IGNORECASE)


def _asked_for(clause: str, m) -> bool:
    """True when a rating-shaped phrase is a wish for better evidence."""
    return bool(_WANTS_BEFORE.search(clause[:m.start()])
                or _WANTS_AFTER.match(clause[m.end():m.end() + 60]))

# Words an outcome group shares with every other outcome, so they say
# nothing about which one a sentence rates.
_GENERIC_OUTCOME = {
    "severe", "mild", "risk", "rate", "rates", "level", "levels", "outcome", "outcomes",
    "effect", "effects", "people", "adults", "children", "patients", "from", "with",
    "health", "total", "overall", "incidence", "prevalence", "symptoms"}


def _clauses(text: str) -> list[str]:
    """
    The abstract cut where one finding ends and the next begins, but never
    inside brackets: "measles was 95% after one dose (RR 0.05; 7 cohort
    studies; moderate certainty evidence)" is one finding, and its rating
    belongs with the word measles.
    """
    out, depth, start = [], 0, 0
    for m in _CLAUSE.finditer(text):
        mark = m.group()
        if mark in ("(", "["):
            depth += 1
        elif mark in (")", "]"):
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(text[start:m.start()])
            start = m.end()
    out.append(text[start:])
    return [c for c in out if c.strip()]


def _outcome_patterns(outcome: str) -> list[list]:
    """
    The outcome group as patterns, closest first: each term whole, then the
    word most of the terms share ("measles" in "severe measles OR measles
    complications OR hospitalization from measles"), so a review that says
    "preventing measles" is still read on the claim's outcome.
    """
    terms = [t for t in re.split(r"\s+OR\s+", (outcome or "").strip().strip("()"))
             if len(t.strip(' "()')) > 2]
    whole = [p for p in (_term_pattern(t) for t in terms) if p]
    if len(terms) < 2:
        return [whole]
    count: dict[str, int] = {}
    for t in terms:
        for w in set(re.findall(r"[a-z]{4,}", t.lower())) - _GENERIC_OUTCOME:
            count[w] = count.get(w, 0) + 1
    shared = [p for p in (_term_pattern(w) for w, n in count.items() if n * 2 >= len(terms)) if p]
    return [whole, shared]


def _ratings(study, outcome: str = "") -> tuple[list[str], list[str]]:
    """Every rating the review gives, and those of them on the claim's own
    outcome (empty when no finding names it)."""
    rated = []
    for clause in _clauses(_text(study)):
        levels = [_level(g) for m in _RATED.finditer(clause) if not _asked_for(clause, m)
                  for g in m.groups() if g]
        if levels:
            rated.append((clause, levels))
    on_outcome = []
    for patterns in _outcome_patterns(outcome):
        on_outcome = [lvl for clause, levels in rated
                      if any(p.search(clause) for p in patterns) for lvl in levels]
        if on_outcome:
            break
    return [lvl for _, levels in rated for lvl in levels], on_outcome


def _level(word: str) -> str:
    return re.sub(r"[- ]", " ", word.lower())


def _rank(level: str) -> int:
    return CERTAINTY_LEVELS.index(level)


def review_certainty(study, outcome: str = "", strict: bool = False) -> str | None:
    """
    The certainty a review gives its own evidence ("very low", "low",
    "moderate" or "high"), or None when it states none. A review often rates
    each outcome on its own ("moderate certainty that zinc does not prevent
    colds; low certainty that it shortens them"), so when `outcome` (the
    query's outcome group) is given, the findings that name it decide, and
    every rating counts only when none do. The lowest one stands: a review is
    never read as surer than its least sure word on the question.

    A `strict` reading, the one that can lower a verdict, needs the review to
    rate it low and nowhere higher: a review that finds moderate certainty
    that vaccination prevents measles, and low certainty for the narrower
    question of a dose given after exposure, has not rated its evidence that
    vaccination prevents measles low.
    """
    if not is_review(study):
        return None
    every, on_outcome = _ratings(study, outcome)
    levels = on_outcome or every
    if not levels:
        return None
    if strict and max(levels, key=_rank) not in ("low", "very low"):
        return None
    return min(levels, key=_rank)


def certainty_range(study, outcome: str = "") -> str | None:
    """The review's ratings on the question, in its own words, lowest to
    highest: "low", or "low to moderate" when it gives both."""
    if not is_review(study):
        return None
    every, on_outcome = _ratings(study, outcome)
    levels = on_outcome or every
    if not levels:
        return None
    low, high = min(levels, key=_rank), max(levels, key=_rank)
    return low if low == high else f"{low} to {high}"


def certainty_on_outcome(study, outcome: str = "") -> bool:
    """True when the review rates the claim's own outcome by name."""
    return is_review(study) and bool(_ratings(study, outcome)[1])


def lead_review(studies, cited_pmids=(), outcome: str = ""):
    """The first review used for the verdict that rates its own evidence
    at all, in the order the studies were ranked, or None."""
    cited = set(cited_pmids or ())
    return next((s for s in studies or []
                 if s.get("pmid") in cited and review_certainty(s, outcome)), None)


def lead_certainty(studies, cited_pmids=(), outcome: str = "",
                   strict: bool = False) -> str | None:
    """
    The certainty the lead review states: the first review used for the
    verdict that rates its own evidence at all, in the order the studies
    were ranked. None when no review used states one.
    """
    # A strict reading the lead review cannot give is not handed to the next
    # review down.
    lead = lead_review(studies, cited_pmids, outcome)
    return review_certainty(lead, outcome, strict) if lead else None


def rated_low_on_outcome(studies, cited_pmids=(), outcome: str = "") -> bool:
    """
    True when any review used for the verdict rates the certainty of its own
    evidence low or very low on the claim's own outcome, and nowhere higher
    there. Not only the lead review: one review's "moderate" does not cancel
    another's "low", so the line cannot open with the stronger word.
    """
    cited = set(cited_pmids or ())
    return any(s.get("pmid") in cited and certainty_on_outcome(s, outcome)
               and review_certainty(s, outcome, strict=True) in ("low", "very low")
               for s in studies or [])


def trial_caveats(study) -> list[str]:
    """What keeps one primary study from carrying a confident answer alone."""
    if is_review(study):
        return []
    out = []
    n = sample_size(study)
    if _PILOT.search(_text(study)):
        out.append("a pilot or feasibility study")
    elif n is not None and n < SMALL_TRIAL:
        out.append(f"small, {n} people")
    if uncontrolled(study):
        out.append("no untreated comparison group")
    if _INDUSTRY.search(_text(study)):
        out.append("funded by the maker")
    return out


# A review that says its own link is likely not the thing itself: "many
# confounding factors, in particular lifestyle, genetic, and socioeconomic
# associations with wine drinking, which likely explain much of the
# association". Only a plain statement counts: "residual confounding cannot
# be excluded" is the caveat every cohort review carries, and says nothing
# about how much of the link it explains.
_CONFOUNDED = re.compile(
    r"\bconfound\w*[^.]{0,160}?\b(?:likely|largely|probably|mostly)\s+(?:explain|account)\w*|"
    r"\b(?:likely|largely|probably|mostly)\s+(?:explained|accounted for|due to|driven)\s+"
    r"(?:by\s+)?(?:residual\s+)?(?:confound\w*|lifestyle|other factors|socioeconomic)",
    re.IGNORECASE)


def confounded(study) -> bool:
    """A systematic review or meta-analysis that says its link is likely
    explained by lifestyle or other factors rather than the thing itself."""
    return is_review(study) and bool(_CONFOUNDED.search(_text(study)))


CONFOUNDED_REASON = ("the main review says lifestyle and other factors likely explain "
                     "much of the link")
SMALL_REASON = "it rests on small, pilot or uncontrolled studies"


def certainty_cap(studies, cited_pmids=(), comparative: bool = False,
                  outcome: str = "") -> str | None:
    """
    Why the evidence behind a "true" cannot carry it, in words for the
    reader, or None when it can. Two ways to fail: the lead review rates its
    own certainty on this outcome low or very low (`outcome` is the query's
    outcome group); or there is no cited review and every cited study is
    small, a pilot, funded by the maker, or (for a claim that something
    works, as against one that compares two things) has nobody who went
    without.
    """
    cited = [s for s in studies or [] if s.get("pmid") in set(cited_pmids or ())]
    if not cited:
        return None
    reviews = [s for s in cited if is_review(s)]
    if reviews:
        if any(confounded(s) for s in reviews):
            return CONFOUNDED_REASON
        lead = lead_certainty(studies, cited_pmids, outcome, strict=True)
        if lead in ("low", "very low"):
            return f"the review behind it rates the certainty of its own evidence as {lead}"
        return None
    solid, limited = [], []
    for s in cited:
        caveats = [c for c in trial_caveats(s)
                   if not (comparative and c == "no untreated comparison group")]
        # A study that says neither its size nor its design is not evidence
        # of weakness, and not evidence of strength either: it cannot carry
        # the answer, and it cannot be the reason to cap it.
        labelled = any(not t.startswith(IGNORE_PREFIXES)
                       for t in s.get("publication_types") or [])
        if caveats:
            limited.append(s)
        elif sample_size(s) is not None or labelled:
            solid.append(s)
    if solid or not limited:
        return None
    return SMALL_REASON


# ---------------------------------------------------------------------------
# Which design outranks which
#
# A randomized trial, or a review that pools only randomized trials, is the
# fair test of whether a thing causes an outcome: chance decides who gets
# it. An observational study, a survey, or a review of them can only show
# that the two go together, and an animal or lab study shows what happens
# in animals or a dish. When the two tiers point opposite ways the verdict
# follows the trials (verdict.py), and says the weaker studies disagree.
# ---------------------------------------------------------------------------

_RANDOMIZED = re.compile(r"(?i:\brandomi[sz]ed\b)|\bRCTs?\b")
_OBSERVED = re.compile(
    r"\bobservational\b|\bcohorts?\b|\bcase[- ]control\b|\bcross[- ]sectional\b|"
    r"\bsurveys?\b|\bquestionnaires?\b|\bregistry\b|\bregistries\b|\becological\b",
    re.IGNORECASE)
_SURVEYED = re.compile(r"\bsurveys?\b|\bquestionnaires?\b|\bsurveyed\b", re.IGNORECASE)


def design_rank(study) -> str:
    """
    "trial" for a randomized trial or a review of randomized trials only;
    "weaker" for an observational study, a survey, a review of those, or an
    animal or lab study; "" for anything that is neither or both (a review
    that mixes the designs, a narrative review, a record that never says).
    """
    if preclinical(study):
        return "weaker"
    text = _text(study)
    # "Non-randomized" and "quasi-randomized" say the opposite of randomized.
    rct = bool(_RANDOMIZED.search(_NON_RANDOM.sub(" ", text)))
    seen = bool(_OBSERVED.search(text))
    types = study.get("publication_types") or []
    if is_review(study):
        if rct != seen:
            return "trial" if rct else "weaker"
        # Neither design is named: a review of interventions given against a
        # placebo to people who were blind to it still pools trials.
        return "trial" if not rct and blinded_intervention(study) else ""
    if any(t.startswith("Review") for t in types):
        return ""
    if "Randomized Controlled Trial" in types or (is_trial(study) and rct):
        return "trial"
    if "Observational Study" in types or seen:
        return "weaker"
    # A study typed as a trial whose record says its people were blind to a
    # placebo is a trial in everything but the word "randomized".
    if any(t.startswith(TRIAL_TYPES) for t in types) and blinded_intervention(study):
        return "trial"
    return ""


def says_randomized(study) -> bool:
    """The record calls itself randomized: typed as such, or says so."""
    return ("Randomized Controlled Trial" in (study.get("publication_types") or [])
            or bool(_RANDOMIZED.search(_text(study))))


def weaker_kind(study) -> str:
    """A weaker study in a reader's words, plural, for "the ... that disagree"."""
    if preclinical(study):
        return "animal or lab studies"
    if is_review(study):
        return "reviews of observational studies"
    if _SURVEYED.search(_text(study)):
        return "surveys"
    return "observational studies"


# ---------------------------------------------------------------------------
# What the evidence line may call strong
#
# `design_rank` above is loose on purpose: it only has to say which side of
# a disagreement outranks the other when the verdict is stamped, and one
# mention of "randomized" is enough. The words on the line under the chart
# are a claim of their own, and "Strong evidence" printed over observational
# cohorts, over a handful of pilots, or over a review that says its own
# causality "could not be inferred" is the line outrunning the studies. So
# the line reads the designs again, more strictly, and never changes the
# stamp (that stays with design_rank and certainty_cap).
#
# What it does differently from design_rank:
#   - "non-randomised", "trial registries" and sentences that only call for
#     more work ("warrant further investigation in randomized trials") are
#     not evidence of a design;
#   - a record typed as a trial that says it studied associations in a
#     trial's patients, with nobody going without, is an observational study;
#   - a review that names both designs is "mixed" and counts for neither.
# ---------------------------------------------------------------------------

_NON_RANDOM = re.compile(
    r"\b(?:non|quasi|pseudo|un)[- ]?randomi[sz]ed\b|\bnot randomi[sz]ed\b|\bnon-?RCTs?\b",
    re.IGNORECASE)
_TRIAL_REGISTER = re.compile(
    r"\b(?:clinical ?)?trials?(?:\.gov)? regist\w+|\bregist\w+ (?:of|for) (?:clinical )?trials\b|"
    r"\bPROSPERO\b", re.IGNORECASE)
# A sentence that wishes for, recommends or rules out a design says what
# the authors want, not what they did.
_WISHED = re.compile(
    r"\b(?:further|future|warrant\w*|needs?|needed|should|recommend\w*|call(?:s|ed)? for)\b",
    re.IGNORECASE)
_RULED_OUT = re.compile(r"\b(?:exclu\w+|ineligible|not eligible)\b", re.IGNORECASE)
_WISHED_OR_RULED_OUT = re.compile(_WISHED.pattern + "|" + _RULED_OUT.pattern, re.IGNORECASE)
# Observational designs, by name. "Questionnaire" is left out: a trial of
# anxiety measures it as an outcome, and that makes it no survey.
_SEEN = re.compile(
    r"\bobservational\b|\bcohorts?\b|\bcase[- ]control\b|\bcross[- ]sectional\b|"
    r"\becological\b|\bregistry\b|\bregistries\b|\bcase[- ]crossover\b|"
    r"\bprospective(?:ly)?\s+(?:cohort\s+|observational\s+)?(?:stud\w+|analys\w+)|"
    r"\bretrospective\b|\blongitudinal\s+(?:cohort\s+|observational\s+)?stud\w+|"
    r"\bepidemiolog\w+\s+(?:stud\w+|evidence|data)|\bpopulation[- ]based\s+(?:stud\w+|cohort)",
    re.IGNORECASE)
# An intervention given against a placebo, to people who did not know which,
# is a trial in everything but the word "randomized". A review that describes
# its studies this way never says "randomized", so it reads as design unknown;
# it still cannot be called observational.
_BLINDED = re.compile(r"\bplacebo\b|\b(?:double|single|triple)[- ]blind\w*|\bblinded\b",
                      re.IGNORECASE)
_ASSOCIATION_WORDS = re.compile(r"\bassociat\w+|\bancillary\b|\bprospective\b|\bcohort\b",
                                re.IGNORECASE)


def blinded_intervention(study) -> bool:
    """
    The record describes an intervention given against a placebo, or to
    people kept blind to what they got, and does not call itself
    non-randomized. A sentence that only wishes for such a design ("future
    placebo-controlled trials are needed") describes nothing that was done.
    """
    if _NON_RANDOM.search(_text(study)):
        return False
    return bool(_BLINDED.search(_sentences_of(study, _WISHED)))


def _sentences_of(study, skip) -> str:
    """The record with the sentences set aside that match `skip`."""
    text = _TRIAL_REGISTER.sub(" ", _NON_RANDOM.sub(" ", _text(study)))
    return " ".join(s for s in _SENTENCE.split(text) if not skip.search(s))


def line_design(study) -> str:
    """
    The design of one study as the evidence line reads it: "trial" for a
    randomized trial or a review of randomized trials only; "observational"
    for a cohort, case-control, cross-sectional or survey study, or a review
    of only those; "" for everything else (a review that mixes the designs
    or never says, a narrative review, a non-randomized trial, an animal
    study, a record that never says).
    """
    if preclinical(study):
        return ""
    types = study.get("publication_types") or []
    rct = bool(_RANDOMIZED.search(_sentences_of(study, _WISHED)))
    seen = bool(_SEEN.search(_sentences_of(study, _WISHED_OR_RULED_OUT)))
    if is_review(study):
        return "trial" if rct and not seen else "observational" if seen and not rct else ""
    if any(t.startswith("Review") for t in types):
        return ""
    if "Randomized Controlled Trial" in types or (is_trial(study) and rct):
        # "A prospective study ancillary to a trial to evaluate associations
        # between supplement use and survival" is a cohort study run inside
        # a trial's patients, whatever PubMed typed it as. It never says its
        # own people were randomized, and nobody in it went without.
        if not rct and uncontrolled(study) and _ASSOCIATION_WORDS.search(_text(study)):
            return "observational"
        return "trial"
    if "Observational Study" in types or seen or _SURVEYED.search(_text(study)):
        return "observational"
    return ""


# A review saying that the trials it pooled were small, few or short. Only
# its own plain words count: a pooled total of people says how many were
# counted, not how big each trial was. "Small-study effects" is a bias test,
# not a statement about the trials, and is not matched.
_SMALL_SAID = re.compile(
    r"\b(?:small|limited|modest)\s+(?:sample|number|quantity)s?\b|"
    r"\bsmall(?:\s+\w+){0,2}\s+(?:trials?|studies|RCTs?|samples?|sample sizes?)\b|"
    # "The few therapeutic trials" is a subset the review is describing, and
    # "few studies have looked at" is background; neither is its own trials.
    r"(?<!the\s)\b(?:only\s+)?(?:a\s+)?few\s+(?:\w+\s+){0,2}(?:trials?|studies|RCTs?)\b(?!\s+have\b)|"
    r"\bshort(?:[- ]term)?\s+(?:follow[- ]up|duration)\b|\bshort[- ]term\s+(?:trials?|RCTs?)\b|"
    r"\b(?:trials?|studies|RCTs?)\s+(?:were|was)\s+(?:generally\s+|mostly\s+)?(?:small|short)\b",
    re.IGNORECASE)

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_WORDNUM = "(?:" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + ")"
# "10 randomized controlled trials comprising 297 individuals": the count of
# trials and the people in them, in one stretch of one sentence. Pairing
# them is what makes an average per trial possible; a count of trials found
# anywhere else in an abstract is as likely to be a count of something else.
_POOL = re.compile(
    rf"\b({_NUMBER}|{_WORDNUM}(?:[- ]{_WORDNUM})?)\s+(?:[A-Za-z-]+\s+){{0,3}}?"
    rf"(?:trials?|RCTs?|studies|publications|articles|reports)\b"
    rf"[^.;]{{0,80}}?(?<![\d.,])({_NUMBER})\s+(?:[A-Za-z-]+\s+){{0,2}}?{_PEOPLE}\b",
    re.IGNORECASE)


def average_trial_size(study) -> float | None:
    """People per trial in a review that states both counts together, or
    None when it does not."""
    best = None
    for m in _POOL.finditer(_text(study)):
        token = m.group(1)
        trials = int(token.replace(",", "")) if token[0].isdigit() else max(
            (int(v) for v in written_numbers(token)), default=0)
        people = int(m.group(2).replace(",", ""))
        if trials and people >= trials:
            best = people / trials if best is None else min(best, people / trials)
    return best


def _small_trials(study) -> bool:
    """A study that is itself small or a pilot, or a review that says its
    trials were."""
    if is_review(study):
        return bool(_SMALL_SAID.search(_text(study)))
    return any(c.startswith(("a pilot", "small,")) for c in trial_caveats(study))


def _sizeable_trials(study) -> bool:
    """A trial of at least SMALL_TRIAL people, or a review whose trials
    averaged that. Unknown is not sizeable: only a stated size vouches."""
    if _small_trials(study):
        return False
    if is_review(study):
        size = average_trial_size(study)
        return size is not None and size >= SMALL_TRIAL
    size = sample_size(study)
    return size is not None and size >= SMALL_TRIAL


def evidence_basis(studies, cited_pmids=()) -> str:
    """
    What kind of evidence the studies used amount to, for the words on the
    line: "trial" (randomized trials, or reviews of them, that are not
    small), "small" (the trials or pilots used are small and none is
    sizeable), "observational" (no trial among them, and at least one
    cohort, case-control, cross-sectional or survey study), "mixed" (the
    rest: non-randomized trials, narrative reviews, designs that are never
    said), or "" when none was used.

    The studies that count are the ones of the strongest tier used, as in
    `level_word`: an editorial beside a cohort study does not turn it into
    something else.
    """
    cited = [s for s in studies or [] if s.get("pmid") in set(cited_pmids or ())]
    if not cited:
        return ""
    order = [classify(s.get("publication_types")) for s in cited]
    top = next((t for t in ("strong", "moderate", "weak") if t in order), "")
    carry = [s for s, t in zip(cited, order) if t == top] if top else cited
    designs = [line_design(s) for s in carry]
    trials = [s for s, d in zip(carry, designs) if d == "trial"]
    others = [s for s, d in zip(carry, designs) if d == ""]
    small = [s for s in trials + others if _small_trials(s)]
    # Small trials or pilots are the evidence when no sizeable trial vouches
    # for them and nothing else sizeable stands beside them. A large review
    # of mixed or unsaid design beside one small trial is not "limited": it
    # is just not randomized-only, which is "mixed" below.
    if small and (trials or "observational" not in designs):
        if not any(_sizeable_trials(s) for s in trials):
            return "mixed" if any(_sizeable_trials(s) for s in others) else "small"
    if trials:
        return "trial"
    # A study whose design is unsaid sits quietly beside observational ones
    # (an umbrella review of cohort meta-analyses never names a design), unless
    # it describes a blinded or placebo-controlled intervention.
    if "observational" in designs and not any(_BLINDED.search(_text(s)) for s in others):
        return "observational"
    return "mixed"


# ---------------------------------------------------------------------------
# Protective practices and safety
#
# Some answers can hurt someone who acts on them. A verdict that steers a
# reader away from sunscreen, vaccination, pasteurization or a prescribed
# medicine, or that calls something safe, has to rest on a randomized trial
# or a major review that says so (verdict.py). These read the claim for
# which way it leans; the risk sentence is what the answer says plainly
# when the guard has to act.
# ---------------------------------------------------------------------------

_PRACTICES = (
    ("sunscreen", re.compile(r"\bsun ?screens?\b|\bsun ?blocks?\b|\bSPF\b", re.IGNORECASE), None,
     "Sun exposure without protection is a documented cause of skin cancer."),
    ("vaccination", re.compile(r"\bvaccin\w*|\bimmuni[sz]ations?\b|\bflu shots?\b", re.IGNORECASE),
     None, "Going without vaccination leaves people open to the diseases vaccines prevent."),
    ("pasteurization", re.compile(r"\bpasteuri[sz]\w*", re.IGNORECASE),
     re.compile(r"\braw milk\b|\bunpasteuri[sz]ed\b", re.IGNORECASE),
     "Raw milk can carry bacteria that pasteurization kills."),
    ("prescribed medicine", re.compile(r"\bprescri(?:bed|ptions?)\b|\bmedications?\b", re.IGNORECASE),
     None, "Stopping a prescribed medicine without a doctor's advice can be risky."),
)
_NOT_PRESCRIBED = re.compile(
    r"\b(?:herbal|natural|homeopathic|ayurvedic|traditional|alternative|folk)\b", re.IGNORECASE)
_PRACTICE_HARM = re.compile(
    r"\b(?:causes?|caused|causing|leads? to|linked to|triggers?|toxic|poison\w*|dangerous|"
    r"harmful|harms?|unsafe|damages?|weakens?|useless|unnecessary|pointless|overrated|scam|"
    r"ineffective|no need|not needed|(?:do(?:es)?n[\u2019']?t|do(?:es)? not|never|won[\u2019']?t) "
    r"(?:work|help|need|protect|prevent))\b", re.IGNORECASE)
_PROTECTIVE_VERB = re.compile(
    r"\b(?:prevents?|protects?|reduces?|lowers?|cuts?|decreases?|stops?|blocks?|saves?)\b",
    re.IGNORECASE)
_THAN = re.compile(r"\b(?:than|instead of|rather than)\b", re.IGNORECASE)
# "Stopping your medication is safe": the subject is going without it.
_GOING_WITHOUT = re.compile(
    r"\b(?:stop(?:ping|s)?|quit(?:ting|s)?|skip(?:ping|s)?|giv(?:e|ing) up|com(?:e|ing) off|"
    r"go(?:ing)? off|avoid(?:ing|s)?|without|refus\w*|declin\w*|ditch\w*|no)\b", re.IGNORECASE)
_SAFE_CLAIM = re.compile(
    r"\b(?:is|are|it[\u2019']?s)\s+(?:completely |totally |perfectly |100% )?safe\b|\bharmless\b|"
    r"\bno side[- ]effects?\b|\brisk[- ]free\b|\bwon[\u2019']?t hurt\b|\bsafe to\b", re.IGNORECASE)


def protective_practice(claim: str) -> dict | None:
    """
    {"practice", "against", "risk"} when the claim is about one of the
    protective practices, else None. `against` is True when the claim leans
    away from the practice: "Vaccines cause autism", "Raw milk is healthier
    than pasteurized milk", "You don't need sunscreen in winter". The claim's
    subject is whatever it names first: "Raw milk causes illness" is a claim
    for pasteurization, not against it, and "Stopping your medication is
    safe" is about going without it.
    """
    claim = str(claim or "")
    for name, pattern, opposite, risk in _PRACTICES:
        m = pattern.search(claim)
        o = opposite.search(claim) if opposite else None
        if not (m or o):
            continue
        if name == "prescribed medicine" and _NOT_PRESCRIBED.search(claim):
            continue
        first = min(x.start() for x in (m, o) if x)
        than = _THAN.search(claim)
        on_practice = (bool(m) and m.start() == first and not (than and than.start() < m.start())
                       and not _GOING_WITHOUT.search(claim[:m.start()]))
        harm = _PRACTICE_HARM.search(claim)
        protect = _PROTECTIVE_VERB.search(claim)
        negative = bool(harm) and not (protect and protect.start() < harm.start())
        return {"practice": name, "against": negative if on_practice else not negative,
                "risk": risk}
    return None


def claims_safe(claim: str) -> bool:
    """A claim that something is safe ("Melatonin is safe for children")."""
    return bool(_SAFE_CLAIM.search(str(claim or "")))


_P_SIGNIFICANT = re.compile(
    r"\b[pP]\s*(?:<|\u2264|<=)\s*0?\.0[0-5]\d*\b|\b[pP]\s*=\s*0?\.0[0-4]\d*\b")
_NOT_SIGNIFICANT_BEFORE = re.compile(
    r"\b(?:no|not|non|nor|neither|without|failed to (?:show|find|reach)|lack of)\b[\w\s-]{0,20}$",
    re.IGNORECASE)


def significant(study) -> bool:
    """Whether the abstract reports a statistically significant result."""
    text = str(study.get("abstract") or "")
    if _P_SIGNIFICANT.search(text):
        return True
    return any(not _NOT_SIGNIFICANT_BEFORE.search(text[max(0, m.start() - 30):m.start()])
               for m in re.finditer(r"\bsignificant(?:ly)?\b", text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Off topic
#
# A study is evidence for a claim when it tests the claim's subject and
# measures the claim's outcome. PubMed matches words, so a search for
# "smoking AND lung cancer" returns a drug-dose trial in smokers who already
# have lung cancer, and "red wine AND heart" returns heart surgery that
# injects ethanol into a vein. The model is asked to judge every study on
# both counts; these are the cases that need no judgement, caught in code so
# they are never cited whatever the model decides.
# ---------------------------------------------------------------------------

CAUSE_CLAIM = re.compile(
    r"\b(?:causes?|caused|causing|leads? to|gives? you|triggers?|"
    r"(?:raises?|increases?) (?:the |your )?risk of)\s+(?:the\s+)?(?:risk of\s+)?"
    r"(?P<outcome>[a-z0-9' -]{3,40}?)\s*(?:risk)?\s*$", re.IGNORECASE)
_SICK_WITH = (r"\b(?:patients|people|persons|adults|individuals|survivors|cases|men|women|"
              r"children)\s+(?:\w+\s+){{0,2}}?(?:with|having|diagnosed with|treated for)\s+"
              r"(?:[\w-]+\s+){{0,3}}?{core}\b")
# "Exercise and insulin resistance in type 2 diabetes": a title that puts the
# whole study inside the disease. Read from the title only.
_SICK_IN = r"\b(?:in|among)\s+(?:adults\s+|older\s+adults\s+|children\s+)?{core}\b"
_SICK_NOUN = r"\b{core}(?:\s*\([^)]{{1,12}}\))?\s+(?:patients|survivors|cases)\b"
_ABBREV_PATIENTS = {"lung cancer": r"\b(?:NSCLC|SCLC)\b"}
_DRINK_CLAIM = re.compile(r"\b(?:wine|alcohol|beer|spirits|liquor|drinking|ethanol)\b",
                          re.IGNORECASE)
_PROCEDURE = re.compile(
    r"\bethanol (?:infusion|injection|ablation)\b|\balcohol septal ablation\b|"
    r"\b(?:infusion|injection) of (?:dehydrated )?(?:ethanol|alcohol)\b|\bablation\b",
    re.IGNORECASE)
_SURVEY = re.compile(
    r"\bperceived (?:health )?risks?\b|\bperceptions? of\b|\battitudes? (?:to|towards|about)\b|"
    r"\bbeliefs? about\b|\bawareness of\b|\bknowledge,? attitudes?\b", re.IGNORECASE)


# A review of whether something is safe is not a test of whether it works.
# A claim that red light smooths wrinkles was answered in part from a review
# of whether red light causes cancer. Read from the title, and only for a
# claim that is not itself about safety, harm or risk.
_SAFETY_TITLE = re.compile(
    r"\b(?:safety|adverse (?:events?|effects?|reactions?)|side[- ]effects?|toxicit(?:y|ies)|"
    r"harms?|dangers?|carcinogenic\w*|oncologic(?:al)? risks?)\b", re.IGNORECASE)
_WORKS_TITLE = re.compile(
    r"\b(?:efficacy|effectiveness|effective|efficacious|benefits?|improv\w*|reduc\w*|"
    r"outcomes?|effects? (?:of|on))\b", re.IGNORECASE)
_SAFETY_CLAIM = re.compile(
    r"\b(?:safe|safety|unsafe|harm\w*|danger\w*|risks?|toxic\w*|side[- ]effects?|"
    r"health(?:y|ier)?|unhealthy|causes?|caused|causing|cancers?|poison\w*|kills?|deadly)\b",
    re.IGNORECASE)
SAFETY_ONLY = "about whether it is safe, not about whether it does what this claim says"


def caused_outcome(claim: str) -> str:
    """'lung cancer' for 'Smoking causes lung cancer'; '' for other claims."""
    m = CAUSE_CLAIM.search(str(claim or "").strip().rstrip(".!?"))
    return m.group("outcome").strip().lower() if m else ""


# "Exercise lowers the risk of depression" is about who gets depressed, and a
# trial of exercise as a treatment for people already depressed cannot answer
# it, however good the trial. Same for "prevents".
PREVENT_CLAIM = re.compile(
    r"\b(?:prevents?|prevented|preventing|protects? (?:you )?(?:against|from)|wards? off|"
    r"(?:reduces?|lowers?|cuts?|decreases?) (?:the |your )?(?:risk|chance|odds) of)\s+"
    r"(?:the\s+)?(?:risk of\s+)?(?:getting\s+|developing\s+)?"
    r"(?P<outcome>[a-z0-9' -]{3,40}?)\s*(?:risk)?\s*$", re.IGNORECASE)


def prevented_outcome(claim: str) -> str:
    """'depression' for 'Exercise lowers the risk of depression'; '' otherwise."""
    m = PREVENT_CLAIM.search(str(claim or "").strip().rstrip(".!?"))
    return m.group("outcome").strip().lower() if m else ""


def off_topic(study, claim: str) -> str | None:
    """Why this record cannot be evidence for this claim, or None."""
    claim = str(claim or "")
    title = str(study.get("title") or "")
    opening = f"{title}. {str(study.get('abstract') or '')[:SUBJECT_WINDOW]}"
    outcome = caused_outcome(claim)
    prevented = "" if outcome else prevented_outcome(claim)
    for named, does in ((outcome, "causes"), (prevented, "prevents")):
        if not named:
            continue
        core = re.escape(named).replace(r"\ ", r"[\s-]+")
        sick = [_SICK_WITH.format(core=core), _SICK_NOUN.format(core=core)]
        if named in _ABBREV_PATIENTS:
            sick.append(_ABBREV_PATIENTS[named])
        if any(re.search(p, opening, re.IGNORECASE) for p in sick) \
                or re.search(_SICK_IN.format(core=core), title, re.IGNORECASE):
            return f"run in people who already have {named}, so it cannot show what {does} it"
    if (outcome or RISK_CLAIM.search(claim)) and re.search(r"\bscreening\b", title, re.IGNORECASE) \
            and not re.search(r"\bscreening\b", claim, re.IGNORECASE):
        return "about finding the disease early, not about what causes it"
    if _SURVEY.search(title) and not _SURVEY.search(claim):
        return "measures what people believe, not what happens to them"
    if (_SAFETY_TITLE.search(title) and not _WORKS_TITLE.search(title)
            and not _SAFETY_CLAIM.search(claim)):
        return SAFETY_ONLY
    if _DRINK_CLAIM.search(claim) and _PROCEDURE.search(opening):
        return "uses alcohol as a medical procedure, not as a drink"
    return None


# ---------------------------------------------------------------------------
# Not tested in people
#
# "Not enough evidence" is its own verdict, and the commonest way to reach it
# is a search that finds papers, just not tests. Detox teas return three case
# reports of liver and salt-balance emergencies; cold showers return a lab
# measurement of lymphocytes. Each is a real paper. None of them asks whether
# the claim is true, and a report of one person harmed is no evidence that a
# thing does not work.
# ---------------------------------------------------------------------------

_PRECLINICAL = re.compile(
    r"\b(?:mice|mouse|murine|rats?|rodents?|zebrafish|rabbits?|piglets?|canine|"
    r"in vitro|cell lines?|cultured cells|animal models?|ex vivo|"
    # Farm animals. A feeding trial in 36 Holstein cows is an animal study
    # however it is designed, and a claim about hormones in people was
    # answered with one.
    r"cows?|cattle|heifers?|calves|steers?|Holsteins?|dairy herds?|livestock|swine|sows|"
    r"ewes|lambs|broilers?|poultry|hens|pigs?|sheep|goats?|chickens?|horses?|equine|"
    r"(?:non-?human )?primates?|monkeys?|macaques?|"
    r"(?:experimental|laboratory) animals?|animal (?:stud(?:y|ies)|experiments?|bioassays?))\b",
    re.IGNORECASE)
# The animals a title can name without being about people at all. Narrower
# than the list above on purpose: "goat milk", "chicken" and "dog ownership"
# are things people eat and keep, and a title naming them is often a study
# of people.
_PRECLINICAL_TITLE = re.compile(
    r"\b(?:mice|mouse|murine|rats?|rodents?|zebrafish|rabbits?|piglets?|in vitro|"
    r"cell lines?|cultured cells|animal models?|ex vivo|cows|cattle|heifers|calves|"
    r"Holsteins?|dairy herds?|livestock|swine|sows|ewes|broilers|"
    r"(?:experimental|laboratory) animals?|animal (?:stud(?:y|ies)|experiments?|bioassays?))\b",
    re.IGNORECASE)
_HUMANS = re.compile(r"\b(?:participants|patients|volunteers|adults|children|women|men|"
                     r"humans?|people|subjects|individuals)\b", re.IGNORECASE)
_TITLE_HUMANS = re.compile(r"\b(?:humans?|people|patients|participants|adults|children|"
                           r"women|men|volunteers|clinical|epidemiolog\w*)\b", re.IGNORECASE)


def single_case(study) -> bool:
    return any(t.startswith("Case Reports") for t in study.get("publication_types") or [])


def preclinical(study) -> bool:
    """
    An animal or lab study: it says so, and never mentions people. Or a
    review of that work (`mechanism_review`), which can say no more about
    people than the studies it reviews.
    """
    # A title that says it is about animals is, whatever the abstract goes
    # on to say about people: a systematic review of cancer in laboratory
    # animals mentions human exposure in its background, and is still a
    # review of animals.
    title = str(study.get("title") or "")
    if _PRECLINICAL_TITLE.search(title) and not _TITLE_HUMANS.search(title):
        return True
    text = _text(study)
    return (bool(_PRECLINICAL.search(text)) and not _HUMANS.search(text)) \
        or mechanism_review(study)


# A narrative review about how something might work: its compounds, its
# pathways, what it does to cells. "Resveratrol and cardiovascular
# diseases" reviews antioxidant action, enzymes in bovine aortic cells and
# molecular targets, and says itself that human trials are limited. It is
# background for a claim about people, like the lab work it reviews. A
# systematic review or meta-analysis is never one: those pool outcomes.
_MECHANISM = re.compile(
    r"\b(mechanis(?:ms?|tic)|molecular|pathways?|signal(?:l)?ing|antioxidants?|"
    r"anti-inflammatory|oxidative stress|in vitro|in vivo|preclinical|cells?|enzymes?|"
    r"(?:up|down)-?regulat\w*|receptors?|gene expression|compounds?|polyphenol\w*|"
    r"bioactive|chemistry|chemical|oxidation)\b", re.IGNORECASE)
# What a review says about people. A mention of their absence ("there are
# limited human clinical trials") is not evidence in people.
_IN_PEOPLE = re.compile(
    r"\b(?:participants|patients|volunteers|adults|children|women|men|people|subjects|"
    r"individuals|cohorts?|humans?|trials?|clinical stud(?:y|ies))\b", re.IGNORECASE)
_ABSENT_BEFORE = re.compile(r"\b(?:limited|few|lack(?:ing)? of|no|scarce|absence of|"
                            r"without)\b(?:\s+[\w-]+){0,2}\s*$", re.IGNORECASE)


def mechanism_review(study) -> bool:
    """A narrative review about mechanisms, with little or nothing on people."""
    types = study.get("publication_types") or []
    if "Review" not in types or is_review(study) or sample_size(study) is not None:
        return False
    text = _text(study)
    marks = {m.lower().rstrip("s") for m in _MECHANISM.findall(text)}
    people = [m for m in _IN_PEOPLE.finditer(text)
              if not _ABSENT_BEFORE.search(text[max(0, m.start() - 40):m.start()])]
    return len(marks) >= 3 and len(marks) >= 3 * len(people)


# A claim names animals, or a dish, when it is about them and not about the
# reader. Everything else is a claim about people: "apple cider vinegar
# cures diabetes" means in people, whatever a rat study found.
_NOT_PEOPLE_CLAIM = re.compile(
    r"\b(?:dogs?|puppies|cats?|kittens?|pets?|horses?|cattle|cows?|pigs?|sheep|"
    r"goats?|chickens?|poultry|livestock|rats?|mice|mouse|rodents?|animals?|"
    r"in vitro|petri|test tubes?|in a dish|in the lab|cell cultures?)\b", re.IGNORECASE)


def about_people(claim: str) -> bool:
    """True unless the claim itself is about animals or the lab."""
    return not _NOT_PEOPLE_CLAIM.search(claim or "")


# ---------------------------------------------------------------------------
# Not a test at all
#
# Some records are about the claim and test nothing: an essay arguing that
# children should keep their phones away from their heads, a commentary on
# someone else's rat study, a guide to buying a red light mask. They are
# worth showing, because they are what PubMed has, and never worth counting,
# because nothing in them was measured.
# ---------------------------------------------------------------------------

_OPINION_TYPES = ("Editorial", "Comment", "Letter", "News", "Newspaper Article", "Interview",
                  "Personal Narrative", "Lecture", "Address")
_OPINION_TITLE = re.compile(
    r"^(?:commentary|editorial|viewpoint|opinion|letter)\b|\bcommentary (?:on|to)\b|"
    r"[:.]\s*(?:an?\s+)?(?:commentary|editorial|viewpoint|opinion(?: piece)?|personal view|"
    r"call to action)\s*\.?$|\b(?:reply|response) to\b|^in reply\b|\bletter to the editor\b",
    re.IGNORECASE)
_GUIDE_TITLE = re.compile(
    r"\b(?:product|buyer'?s|buying|consumer'?s?|shopping|purchasing) guide\b|"
    r"\bwhat to look for (?:in|when)\b|\bhow to (?:choose|buy|pick|select)\b", re.IGNORECASE)
# Any sign that a record reports something measured or pooled. Words an
# essay uses as freely as a study ("results", "patients", "trials") are not
# signs: an essay about phones says "the results of any study" too.
_STUDY_SIGNAL = re.compile(
    r"\b(?:we|our)\b|\bthis (?:study|trial|review|analysis|meta-analysis)\b|"
    r"\b(?:participants|subjects|respondents|cohorts?|randomi[sz]ed|enrolled|recruited|"
    r"analy[sz]ed|measured|questionnaires?|surveyed|methods?|meta-analys[ie]s|"
    r"systematic review)\b|\b[nN]\s*=\s*\d", re.IGNORECASE)
# A structured abstract's own section label: "METHODS: ", "RESULTS: ".
_SECTION_LABEL = re.compile(r"(?:^|\s)[A-Z]{3,}(?: [A-Z]{2,})*:\s")


_ESSAY_MIN_ABSTRACT = 400


def opinion(study) -> bool:
    """
    An opinion piece: PubMed tags it as one (editorial, comment, letter), its
    title says it is one ("Commentary on..."), or it is an essay, a record
    with no design of its own whose abstract never mentions anything it
    measured, anyone it studied or any data at all.
    """
    types = study.get("publication_types") or []
    if any(t.startswith(_OPINION_TYPES) for t in types):
        return True
    if _OPINION_TITLE.search(str(study.get("title") or "")):
        return True
    if any(not t.startswith(IGNORE_PREFIXES) for t in types):
        return False
    # An essay is a long abstract that never once reports a study. A stub
    # record, or one that names the animals it was done in, is not one.
    abstract = str(study.get("abstract") or "")
    return (len(abstract.strip()) >= _ESSAY_MIN_ABSTRACT
            and not _STUDY_SIGNAL.search(abstract)
            and not _SECTION_LABEL.search(abstract)
            and not _PRECLINICAL.search(_text(study))
            and sample_size(study) is None)


def product_guide(study) -> bool:
    """A buying guide: "What to Look for in Red Light Therapy"."""
    return bool(_GUIDE_TITLE.search(str(study.get("title") or "")))


# Why a record is background only, as the study list and the chart say it.
# A cached record from before the reasons existed carries plain True, which
# was only ever set for an animal or lab study.
BG_LAB = "animal or lab"
BG_OPINION = "opinion piece"
BG_GUIDE = "product guide"
BG_NOT_TESTED = "not a test of this claim"


def background_reasons(studies, claim: str) -> dict:
    """
    {pmid: why} for the records that are background for this claim and are
    never counted as used. Opinion pieces and buying guides, for any claim:
    nothing in them was measured. Animal and lab studies, for a claim about
    people: they can say why a thing might work, never whether it does.
    """
    people = about_people(claim)
    out = {}
    for s in studies or []:
        if product_guide(s):
            out[s["pmid"]] = BG_GUIDE
        elif people and preclinical(s):
            out[s["pmid"]] = BG_LAB
        elif opinion(s):
            out[s["pmid"]] = BG_OPINION
    return out


def background(studies, claim: str) -> list[str]:
    """The PMIDs of background_reasons, in study order."""
    return list(background_reasons(studies, claim))


def human_trial(study) -> bool:
    """A trial PubMed tags as one, whose record names the people in it."""
    return (any(t.startswith(TRIAL_TYPES) for t in study.get("publication_types") or [])
            and bool(_HUMANS.search(_text(study))) and not preclinical(study))


def background_why(study) -> str:
    """The reason a record is background only, or ""."""
    why = study.get("background")
    if not why:
        return ""
    return why if isinstance(why, str) else BG_LAB


# ---------------------------------------------------------------------------
# Names the claim's subject
#
# A study is evidence for a claim only if it tests the thing the claim is
# about. PubMed's own matching is loose (a search for seed cycling returns
# trials of single seed extracts, which are not seed cycling), and when
# fewer than three papers name the subject the ranking keeps the rest, so
# the gate has to be here as well.
# ---------------------------------------------------------------------------

_SUFFIX = re.compile(r"(?:ing|ers|er|es|ed|s)$", re.IGNORECASE)


def _stem(word: str) -> str:
    """'skipping' -> 'skipp', 'saunas' -> 'sauna'. Never shorter than four letters."""
    cut = _SUFFIX.sub("", word)
    return cut if len(cut) >= 4 else word


def _term_pattern(term: str):
    """
    A term as a pattern that allows the ordinary variations: plurals and
    other endings, a hyphen for a space, and two words either way round
    with up to two words between ("skipping breakfast", "breakfast
    skippers").
    """
    clean = re.sub(r"\[[^\]]*\]", "", term).strip(' "()')
    words = [w for w in re.split(r"[\s-]+", clean) if w]
    if not words:
        return None

    def one(w):
        if w.endswith("*"):
            return r"\b" + re.escape(w.rstrip("*")) + r"\w*"
        return r"\b" + re.escape(_stem(w)) + r"\w*"

    if len(words) == 1:
        return re.compile(one(words[0]), re.IGNORECASE)
    gap = r"\W+(?:\w+\W+){0,2}?"
    forward = gap.join(one(w) for w in words)
    backward = gap.join(one(w) for w in reversed(words))
    return re.compile(f"(?:{forward})|(?:{backward})", re.IGNORECASE)


_SUBJECT_MIN_ABSTRACT = 200


def names_subject(study, terms_group: str) -> bool:
    """
    True when this record names the thing `terms_group` (the subject's
    OR-group from the query) anywhere in its title or abstract. Read in the
    whole abstract, not just its opening: this decides only whether a paper
    can be about the subject at all, and a paper that never names it, in
    any of the words the search step gave it, cannot be.
    """
    terms = [t for t in re.split(r"\s+OR\s+", (terms_group or "").strip().strip("()"))
             if len(t.strip(' "()')) > 2]
    if not terms:
        return True
    study = study or {}
    text = _text(study)
    # A record with next to no abstract is not ruled out on what it never
    # had the room to say: PubMed matched it on something.
    if len(str(study.get("abstract") or "").strip()) < _SUBJECT_MIN_ABSTRACT:
        return True
    return any(p.search(text) for p in (_term_pattern(t) for t in terms) if p)


def untested_in_people(studies, cited_pmids=(), reasons=None) -> str:
    """
    What the cited evidence is, in words, when none of it tests anything in
    people: "reports of single patients", "animal or lab studies", or both.
    "" when at least one cited study is a test in people. `reasons` is the
    {pmid: why} of the records already judged background, so a paper the
    model read as no test at all counts as one here too.
    """
    cited = [s for s in studies or [] if s.get("pmid") in set(cited_pmids or ())]
    if not cited:
        return ""
    reasons = reasons or {}
    kinds = set()
    for s in cited:
        why = reasons.get(s.get("pmid"))
        if single_case(s) or classify(s.get("publication_types")) == "weak":
            kinds.add("reports of single patients" if single_case(s) else "opinion pieces")
        elif preclinical(s) or why == BG_LAB:
            kinds.add("animal or lab studies")
        elif opinion(s) or product_guide(s) or why in (BG_OPINION, BG_GUIDE):
            kinds.add("opinion pieces")
        elif why == BG_NOT_TESTED:
            kinds.add("papers that test nothing in people")
        else:
            return ""
    order = ["reports of single patients", "animal or lab studies", "opinion pieces",
             "papers that test nothing in people"]
    said = [k for k in order if k in kinds]
    return " and ".join(said)


# ---------------------------------------------------------------------------
# The shelf
#
# Every study is drawn as a book. Its height is its tier, the strength of the
# design. Its cloth is its kind: who or what the study was run on, and whether
# it counts at all. Both are read off the record, never off the verdict, so a
# shelf can say what the evidence is and never what the answer was.
# ---------------------------------------------------------------------------

# Legend order, strongest first, and the order a mini shelf is stood in.
KINDS = ("strong", "human", "lab", "weak", "retracted")
KIND_LABELS = {
    "strong": "Strong design",
    "human": "Human study",
    "lab": "Animal or lab",
    "weak": "Opinion or single case",
    "retracted": "Retracted",
}


def kind(study) -> str:
    """The cloth for one study's spine."""
    tier = classify(study.get("publication_types"))
    if tier in ("weak", "retracted"):
        return tier
    if preclinical(study):
        return "lab"
    return "strong" if tier == "strong" else "human"


# The word lettered on a spine: the design, in a reader's words, short
# enough to stand on the book. No year: the tip and the list carry that.
SPINES = {
    "Meta-Analysis": "Meta-analysis",
    "Network Meta-Analysis": "Network meta-analysis",
    "Systematic Review": "Systematic review",
    "Randomized Controlled Trial": "Randomized trial",
    "Practice Guideline": "Guideline",
    "Retracted Publication": "Retracted",
    "Case Reports": "One case",
    "Editorial": "Opinion",
    "Comment": "Comment",
    "Letter": "Letter",
    "News": "News",
    "Controlled Clinical Trial": "Trial",
    "Clinical Trial": "Trial",
    "Observational Study": "Observational",
    "Multicenter Study": "Multicenter",
    "Comparative Study": "Comparative",
    "Review": "Review",
}


def spine(study) -> str:
    """
    The word on one study's spine. A lab study that is not a pooled design
    says what it was run on, because that is the fact a reader needs first.
    """
    label = strongest_label(study.get("publication_types"))
    if kind(study) == "lab" and classify(study.get("publication_types")) != "strong":
        return "Animal or lab"
    return SPINES.get(label, "Study")


def shelf(studies, cited_pmids=()) -> list[dict]:
    """
    A mini shelf for a check in a list: one spine per study, stood in kind
    order so two shelves of the same set are always the same drawing.
    """
    cited = {str(p) for p in (cited_pmids or ())}
    books = [{"kind": kind(s), "tier": classify(s.get("publication_types")),
              "used": str(s.get("pmid")) in cited} for s in studies or []]
    return sorted(books, key=lambda b: KINDS.index(b["kind"]))
