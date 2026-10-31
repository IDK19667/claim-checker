"""
Extractive evidence: the model picks fields, code writes the sentences.

Round 4 failed on free prose. The model credited a figure to the wrong group,
credited a study with a finding it never tested, wrote an evidence line that
argued with the stamp, and stamped a causal claim true on observational
studies alone. A better prompt does not fix prose that drifts; not asking for
prose does.

For each study it relies on, the model fills one entry of fixed fields and
nothing else: which study, who was studied, what was measured, which way it
went (a fixed list), one figure copied from the abstract, and the sentence of
the abstract all of that came from. This module then:

  1. checks every field against that study's own abstract (`verify`): the
     quote must be in the abstract, the figure must be in the quote, the
     words naming the group and the outcome must be in the quote or the
     title, and the direction may not argue with the quote. An entry that
     fails any check is dropped, never reworded;
  2. writes every sentence of "What the research says" and of the deeper
     fold from fixed templates over the fields that passed (`sentence`,
     `build`), with the verb fixed by the design of the study, so an
     observational study can only be "linked to" and a trial can "find";
  3. writes the short answer from a template keyed on the stamp and the
     strongest study's fields (`tldr`), and the open question from gaps code
     can see in the studies plus one pick from a fixed list (`still_open`).

Nothing here calls a model. The wording rules live here too (`CAUSAL`,
`wording_ok`): "cure", "prove", "prevent" and their forms may stand only over
randomized trials, meta-analyses or systematic reviews, and a hedge in the
source ("may", "suggests") is kept as the verb "suggested".
"""
from __future__ import annotations

import math
import re

import evidence

# ---------------------------------------------------------------------
# What the model is asked for
# ---------------------------------------------------------------------

DIRECTIONS = ("improved", "worsened", "no clear difference", "mixed")
STUDY_TYPES = ("randomized trial", "systematic review or meta-analysis", "non-randomized trial",
               "cohort study", "case-control study", "cross-sectional study", "survey",
               "case report", "animal or lab study", "narrative review", "other")

# The one pick the model makes for the open question. Everything else in the
# "still open" line is a gap code found in the studies.
OPEN_QUESTIONS = (
    "Whether the results hold for people outside the groups studied.",
    "How long any effect lasts.",
    "Whether the amount or the timing matters.",
    "Whether it works the same for everyone or better for some people.",
    "Whether it stays safe when used for many years.",
    "Whether larger or better-controlled studies would give the same answer.",
    "Whether the link is a cause or only a pattern.",
)

MAX_FINDINGS = 8
PER_STUDY = 2

SCHEMA_PROPERTIES = {
    "findings": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "study": {"type": "integer"},
                "population": {"type": "string"},
                "outcome": {"type": "string"},
                "direction": {"type": "string", "enum": list(DIRECTIONS)},
                # Empty when the abstract prints no figure for this outcome.
                "size": {"type": "string"},
                "study_type": {"type": "string", "enum": list(STUDY_TYPES)},
                "quote": {"type": "string"},
            },
            "required": ["study", "population", "outcome", "direction", "size",
                         "study_type", "quote"],
            "additionalProperties": False,
        },
    },
    "open_question": {"type": "string", "enum": list(OPEN_QUESTIONS)},
}
SCHEMA_REQUIRED = list(SCHEMA_PROPERTIES)

PROMPT = """
The sentences on the page are written by the tool, not by you. For "findings",
fill one entry for each study you cite, from that study's abstract, and
nothing else. At most two entries per study and eight in all. Never fill one
for a study marked OFF TOPIC or BACKGROUND ONLY.

  * "study": the study number.
  * "population": who or what was studied, 2 to 8 words from the abstract or
    title, in plain words: "adults with insomnia".
  * "outcome": what was measured, 2 to 8 words from the abstract or title:
    "sleep quality", "risk of falls".
  * "direction": how that outcome went in the group that got the treatment or
    exposure, against the group that did not: "improved", "worsened", "no
    clear difference" or "mixed". When the abstract says the difference was
    not statistically significant, or its interval includes no effect, the
    direction is "no clear difference".
  * "size": one figure for that outcome, with its unit or its interval,
    copied character for character from the abstract ("odds ratio 0.88 (95% CI
    0.81 to 0.96)", "12 mmHg"). Empty if the abstract prints none. Never
    round, convert or work one out.
  * "study_type": the design, as the abstract describes it.
  * "quote": the one sentence of that abstract the entry comes from, copied
    word for word. It must contain the "size" figure, if you gave one.

An entry that does not match its abstract is deleted, not corrected, so only
an exact copy survives.

"open_question": pick the one line from the list that is the biggest thing
these studies leave open.
""".strip()

PROMPT_SHAPE = """  "findings": [{"study": 1, "population": "adults with insomnia", "outcome": "sleep quality", "direction": "improved", "size": "odds ratio 0.88 (95% CI 0.81 to 0.96)", "study_type": "randomized trial", "quote": "one sentence copied from that abstract"}],
  "open_question": "one line from the list: """ + " | ".join(OPEN_QUESTIONS) + '",'

# ---------------------------------------------------------------------
# Wording rules
# ---------------------------------------------------------------------

# Words that say one thing makes another happen, or that something is settled.
# They may stand only over randomized trials, meta-analyses or systematic
# reviews. Everywhere else the templates use the fixed weaker verbs below.
CAUSAL = re.compile(
    r"\b(?:cur(?:e|es|ed|ing)|prov(?:e|es|ed|en|ing)|prevent(?:s|ed|ing|ion)?|"
    r"caus(?:e|es|ed|ing)|eliminat(?:e|es|ed|ing)|revers(?:e|es|ed|ing)|"
    r"eradicat(?:e|es|ed|ing)|heal(?:s|ed|ing)?)\b", re.IGNORECASE)
LINKED = "was linked to"
FOLLOWED = "was followed by"

# A hedge in the source is kept. The templates carry it as "suggested".
HEDGE = re.compile(
    r"\b(?:may|might|could|possibly|possible|perhaps|suggest(?:s|ed|ing)?|"
    r"associated|association|appears?|seems?|likely|potentially)\b", re.IGNORECASE)

_DASHES = re.compile(r"[‒-―−]")


def causal_words(text: str) -> list[str]:
    return [m.group(0).lower() for m in CAUSAL.finditer(text or "")]


def wording_ok(text: str, may_cause: bool) -> bool:
    """False when `text` uses a causal word it has no right to."""
    return may_cause or not CAUSAL.search(text or "")


def hedged(quote: str) -> bool:
    return bool(HEDGE.search(quote or ""))


# ---------------------------------------------------------------------
# What a study is, read from its record
# ---------------------------------------------------------------------

_COHORT = re.compile(r"\bcohort\b", re.IGNORECASE)
_CASE_CONTROL = re.compile(r"\bcase[- ]control\b", re.IGNORECASE)
_CROSS = re.compile(r"\bcross[- ]sectional\b", re.IGNORECASE)
_SURVEY = re.compile(r"\bsurveys?\b|\bquestionnaires?\b", re.IGNORECASE)
_OBSERVED_WORD = re.compile(r"\bobservational\b|\bregistry\b|\bregistries\b|\becological\b",
                            re.IGNORECASE)

# Order the page lists studies in: strongest design first.
RANK = {"pooled": 0, "randomized": 1, "trial": 2, "observational": 3, "other": 4,
        "case": 5, "background": 6}


def design(study: dict) -> dict:
    """
    {"kind", "noun", "may_cause"} for one study, read from PubMed's own
    publication types and the record's words, never from the model.

    kind: pooled (meta-analysis or systematic review of trials, or of no
    design that is named), randomized, trial (controlled, not randomized),
    observational, other, case, background (animal, lab, in vitro).
    `may_cause` is True for the kinds the wording rules let use a causal word.
    """
    types = study.get("publication_types") or []
    text = f"{study.get('title') or ''}. {study.get('abstract') or ''}"
    if evidence.preclinical(study):
        return {"kind": "background", "noun": "animal or lab study", "may_cause": False}
    if "Case Reports" in types:
        return {"kind": "case", "noun": "case report", "may_cause": False}
    if evidence.is_review(study):
        meta = any(t.startswith(("Meta-Analysis", "Network Meta-Analysis")) for t in types)
        sysrev = any(t.startswith("Systematic Review") for t in types)
        noun = ("systematic review and meta-analysis" if meta and sysrev
                else "meta-analysis" if meta else "systematic review")
        if evidence.design_rank(study) == "weaker":
            return {"kind": "observational", "noun": f"{noun} of observational studies",
                    "may_cause": False}
        return {"kind": "pooled", "noun": noun, "may_cause": True}
    rank = evidence.design_rank(study)
    if rank == "trial":
        if "Randomized Controlled Trial" in types or evidence.says_randomized(study):
            return {"kind": "randomized", "noun": "randomized trial", "may_cause": True}
        return {"kind": "trial", "noun": "controlled trial", "may_cause": False}
    if any(t.startswith(evidence.TRIAL_TYPES) for t in types):
        return {"kind": "trial", "noun": "trial", "may_cause": False}
    if _CASE_CONTROL.search(text):
        return {"kind": "observational", "noun": "case-control study", "may_cause": False}
    if _CROSS.search(text):
        return {"kind": "observational", "noun": "cross-sectional study", "may_cause": False}
    if _COHORT.search(text):
        return {"kind": "observational", "noun": "cohort study", "may_cause": False}
    if _SURVEY.search(text):
        return {"kind": "observational", "noun": "survey", "may_cause": False}
    if rank == "weaker" or "Observational Study" in types or _OBSERVED_WORD.search(text):
        return {"kind": "observational", "noun": "observational study", "may_cause": False}
    return {"kind": "other", "noun": "study", "may_cause": False}


# ---------------------------------------------------------------------
# Checking an entry against its own abstract
# ---------------------------------------------------------------------

def _squash(text: str) -> str:
    """Lower case, dashes and quotes unified, everything but letters and
    digits gone: the form the quote test reads."""
    text = _DASHES.sub("-", str(text or "")).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace(" ", " ")).strip()


def in_abstract(quote: str, abstract: str) -> bool:
    """The quote is in the abstract once whitespace and punctuation are
    ignored, and is long enough to be a sentence and not a word."""
    words = len(str(quote or "").split())
    if words < 5 or words > 90:
        return False
    q = _squash(quote)
    return bool(q) and q in _squash(abstract)


_STOP = frozenset("""the and with for who are had that from all any were was has have this
their them than into over under not but its our out per via among between about""".split())
_BAD_FIELD = re.compile(r"[.;:!?\"“”()\[\]{}<>–—]|\bstudy \d", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{3,}", str(text or "").lower())}


def _words_found(field: str, haystack: str) -> bool:
    """At least half the content words of `field` (by their first five
    letters, so plurals and endings do not matter) are in the haystack."""
    words = [w for w in re.findall(r"[a-z]{3,}", field.lower()) if w not in _STOP]
    if not words:
        return False
    have = _stems(haystack)
    hit = sum(1 for w in words if w[:5] in have)
    return hit >= math.ceil(len(words) / 2)


def _field_ok(value: str, source: str, limit: int = 80) -> str:
    """A clean phrase for a template, or "" when it cannot be used: too long,
    punctuated like a sentence, a number the source never printed, or words
    the quote and the title do not contain."""
    value = _collapse(value)
    if not (2 <= len(value) <= limit) or _BAD_FIELD.search(value):
        return ""
    known = set(_NUMBER.findall(source))
    if any(n not in known for n in _NUMBER.findall(value)):
        return ""
    return value if _words_found(value, source) else ""


_NULL = re.compile(
    r"\bno (?:statistically )?(?:significant|clear|meaningful|real|consistent)\b|"
    r"\bnot (?:statistically )?significant(?:ly)?\b|\bn\.?s\.?\b(?=[\s.,;)])|"
    r"\bdid not (?:differ|change|improve|reduce|lower|increase|affect|alter)\b|"
    r"\bno (?:difference|effect|change|association|benefit|improvement|reduction)\b|"
    r"\b(?:similar|comparable|equivalent)\b|\bwithout (?:a )?(?:significant|difference)\b|"
    r"\bfailed to\b|\bdid not show\b|\bwas not associated\b|\bnot associated\b",
    re.IGNORECASE)
_POSITIVE_SIG = re.compile(r"\bsignificant(?:ly)?\b|\bP\s*<\s*0?\.\d+", re.IGNORECASE)


def _direction_fits(direction: str, quote: str) -> bool:
    """A quote that says there was no clear difference cannot be filed as
    improved or worsened, and a quote that calls its result significant
    cannot be filed as no clear difference."""
    null = bool(_NULL.search(quote))
    if direction in ("improved", "worsened"):
        return not null
    if direction == "no clear difference":
        return null or not _POSITIVE_SIG.search(quote)
    return True


def verify(entry: dict, studies: list[dict]) -> dict | None:
    """
    One entry of the model's `findings`, checked against the abstract of the
    study it names. Returns the cleaned entry, or None when any field fails.
    Nothing is repaired: an entry that is nearly right is wrong.
    """
    if not isinstance(entry, dict):
        return None
    try:
        n = int(entry.get("study"))
    except (TypeError, ValueError):
        return None
    if not 0 < n <= len(studies):
        return None
    study = studies[n - 1]
    abstract = str(study.get("abstract") or "")
    direction = str(entry.get("direction") or "").strip().lower()
    if direction not in DIRECTIONS:
        return None
    quote = _collapse(entry.get("quote"))
    if not in_abstract(quote, abstract):
        return None
    size = _collapse(entry.get("size"))
    if size:
        # The figure and its units, exactly as printed, inside the quote.
        if (len(size) > 70 or not re.search(r"\d", size) or _DASHES.search(size)
                or not re.search(r"(?<![\w.])" + re.escape(size) + r"(?![\w]|\.\d)",
                                 _collapse(quote))):
            return None
    source = f"{quote} {study.get('title') or ''}"
    population = _field_ok(entry.get("population"), source)
    outcome = _field_ok(entry.get("outcome"), source)
    if not population or not outcome:
        return None
    if not _direction_fits(direction, quote):
        return None
    info = design(study)
    # Over anything but a trial or a pooled review, the fields may not carry
    # a causal word of their own.
    if not wording_ok(f"{population} {outcome}", info["may_cause"]):
        return None
    return {"study": n, "population": population, "outcome": outcome, "direction": direction,
            "size": size, "quote": quote, "kind": info["kind"], "noun": info["noun"],
            "hedged": hedged(quote)}


# ---------------------------------------------------------------------
# The sentences
# ---------------------------------------------------------------------

def _article(noun: str) -> str:
    return "an" if noun[:1].lower() in "aeiou" else "a"


def _undash(text: str) -> str:
    return evidence._undash(text)


def clause(f: dict, with_population: bool = True) -> str:
    """One finding as a clause with no capital, no citation and no full
    stop. The verb is fixed by the design of the study."""
    noun, kind = f["noun"], f["kind"]
    out, d = f["outcome"], f["direction"]
    where = f" in {f['population']}" if with_population else ""
    head = f"{_article(noun)} {noun}{where}"
    say = "suggested" if f.get("hedged") else "found"
    if d == "mixed":
        return f"{head} {say} mixed results for {out}"
    if kind in ("pooled", "randomized"):
        if d == "improved":
            return f"{head} {say} {out} improved"
        if d == "worsened":
            return f"{head} {say} {out} got worse"
        return f"{head} {say} no clear difference in {out}"
    if kind == "observational":
        if d == "improved":
            return f"{head} {say} {out} {LINKED} improvement"
        if d == "worsened":
            return f"{head} {say} {out} {LINKED} worsening"
        return f"{head} {say} no clear link with {out}"
    # A trial that was not randomized, a case report, a study of no named design.
    if d == "improved":
        return f"{head} {say} {out} {FOLLOWED} improvement"
    if d == "worsened":
        return f"{head} {say} {out} {FOLLOWED} worsening"
    return f"{head} {say} no clear difference in {out}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def sentence(f: dict) -> str:
    """A finding as a full sentence ending in its citation."""
    size = f", reporting {_undash(f['size'])}" if f.get("size") else ""
    return f"{_cap(clause(f))}{size} (Study {f['study']})."


def background_sentence(n: int, noun: str = "animal or lab study") -> str:
    return (f"Background only: Study {n} is {_article(noun)} {noun}. It can say why something "
            f"might work, not whether it works in people.")


def _list_numbers(numbers: list[int]) -> str:
    nums = [str(n) for n in numbers]
    body = nums[0] if len(nums) == 1 else ", ".join(nums[:-1]) + " and " + nums[-1]
    return ("Study " if len(nums) == 1 else "Studies ") + body


DIRECTION_WORD = {"improved": "improvement", "worsened": "worsening",
                  "no clear difference": "no clear difference", "mixed": "mixed results"}


def agreement(found: list[dict]) -> str:
    """One sentence on whether the studies point the same way, from their
    directions, or "" with fewer than two studies."""
    by_study: dict[int, str] = {}
    for f in found:
        by_study.setdefault(f["study"], f["direction"])
    if len(by_study) < 2:
        return ""
    groups: dict[str, list[int]] = {}
    for n, d in by_study.items():
        groups.setdefault(d, []).append(n)
    if len(groups) == 1:
        return f"All {len(by_study)} studies found the same: {DIRECTION_WORD[next(iter(groups))]}."
    parts = [f"{_list_numbers(sorted(ns))} found {DIRECTION_WORD[d]}"
             for d, ns in sorted(groups.items(), key=lambda kv: kv[1][0])]
    return "The studies do not all agree. " + "; ".join(parts) + "."


def disagree(found: list[dict]) -> bool:
    """Relied-on studies pointing different ways: improved against worsened,
    or one that is itself mixed."""
    dirs = {f["direction"] for f in found}
    return "mixed" in dirs or ("improved" in dirs and "worsened" in dirs)


def ordered(found: list[dict]) -> list[dict]:
    return sorted(found, key=lambda f: (RANK.get(f["kind"], 4), f["study"]))


def build(raw, studies: list[dict], cited_pmids, skip=(), background=()) -> dict:
    """
    Everything the page says about the studies, from the model's entries.

    `cited_pmids` are the studies the verdict relies on, `skip` the study
    numbers that are off topic, `background` the numbers that are background
    only (animal, lab, opinion). Returns {"found": verified entries, in page
    order, "says": [...], "evidence": [...], "effect_size", "strength",
    "applies_to", "unknowns_from" ... , "dropped": how many failed}.
    """
    cited = set(cited_pmids or ())
    found, dropped, per = [], 0, {}
    for entry in raw if isinstance(raw, list) else []:
        ok = verify(entry, studies)
        if ok is None or ok["study"] in skip or ok["study"] in background \
                or studies[ok["study"] - 1]["pmid"] not in cited \
                or per.get(ok["study"], 0) >= PER_STUDY or len(found) >= MAX_FINDINGS:
            dropped += 1
            continue
        per[ok["study"]] = per.get(ok["study"], 0) + 1
        found.append(ok)
    found = ordered(found)
    back = sorted(n for n in background if 0 < n <= len(studies))
    return {"found": found, "dropped": dropped, "background": back}


def lines(built: dict, studies: list[dict]) -> dict:
    """The page's evidence fields, written from `build`'s output."""
    found = built["found"]
    strong = [f for f in found if f["kind"] in ("pooled", "randomized")]
    rest = [f for f in found if f["kind"] not in ("pooled", "randomized")]
    agree = agreement(found)
    back = [background_sentence(n, design(studies[n - 1])["noun"]) for n in built["background"]]
    paragraphs = []
    for group in (strong, rest):
        if group:
            paragraphs.append(" ".join(sentence(f) for f in group))
    if agree:
        paragraphs.append(agree)
    if back:
        paragraphs.append(" ".join(back[:2]))
    says = [sentence(f) for f in found[:3]]
    if agree:
        says.append(agree)
    if back and len(says) < 5:
        says.append(back[0])
    sized = [f for f in found if f.get("size")]
    effect = " ".join(f"Study {f['study']} reported {_undash(f['size'])}." for f in sized[:3])
    kinds: dict[str, list[int]] = {}
    for f in found:
        kinds.setdefault(f["noun"], [])
        if f["study"] not in kinds[f["noun"]]:
            kinds[f["noun"]].append(f["study"])
    strength = ""
    if kinds:
        what = [f"{len(ns)} {_plural(noun, len(ns))}" for noun, ns in kinds.items()]
        every = sorted({n for ns in kinds.values() for n in ns})
        strength = (f"This rests on {_and(what)} ({_list_numbers(every)}). "
                    + (agree if agree else ""))
    who: dict[int, str] = {}
    for f in found:
        who.setdefault(f["study"], f["population"])
    applies = " ".join(f"Study {n} was in {p}." for n, p in list(who.items())[:4])
    return {"says": says, "evidence": paragraphs, "effect_size": effect,
            "strength": strength.strip(), "applies_to": applies}


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _plural(noun: str, count: int) -> str:
    if count == 1:
        return noun
    for single, many in (("meta-analysis", "meta-analyses"), ("study", "studies"),
                         ("survey", "surveys"), ("review", "reviews"), ("trial", "trials")):
        if noun.endswith(single):
            return noun[: -len(single)] + many
    return noun + "s"


# ---------------------------------------------------------------------
# The short answer
# ---------------------------------------------------------------------

TLDR_LEADS = {
    "true": "Studies point towards yes",
    "false": "Studies point towards no",
    "complicated": "The studies found are mixed or limited",
    "insufficient": "The studies found do not settle this",
}
TLDR_MAX = 160


def is_template_tldr(text: str) -> bool:
    return any(text.startswith(lead) for lead in TLDR_LEADS.values())


def _strongest(found: list[dict], verdict: str, stance: dict) -> dict | None:
    """The strongest design among the entries that lean the way the stamp
    does ("for" under true, "against" under false), else the strongest of all."""
    if not found:
        return None
    want = {"true": "for", "false": "against"}.get(verdict)
    leaning = [f for f in found if want and stance.get(f["study"]) == want]
    return ordered(leaning or found)[0]


def tldr(verdict: str, found: list[dict], stance: dict | None = None, limit: int = TLDR_MAX) -> str:
    """The short answer, from the stamp and the strongest study's fields. A
    finding that does not fit the room loses its group, then is left out."""
    lead = TLDR_LEADS.get(verdict, TLDR_LEADS["insufficient"])
    pick = _strongest(found, verdict, stance or {})
    if pick and verdict in ("true", "false", "complicated"):
        for with_population in (True, False):
            text = f"{lead}: {clause(pick, with_population)}."
            if len(text) <= limit:
                return text
    return f"{lead}."


# ---------------------------------------------------------------------
# What is still open
# ---------------------------------------------------------------------

STILL_OPEN_MAX = 200
_DURATION = re.compile(
    r"\b(?:for|over|during|lasting|follow-up of|followed for|up to|of)\s+"
    r"(\d+(?:\.\d+)?)[- ](days?|weeks?|months?|years?)\b", re.IGNORECASE)
_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
SHORT_DAYS = 84


def longest_follow_up(studies: list[dict], cited_pmids) -> tuple[float, str] | None:
    """(days, as printed) for the longest period the cited trials say they
    ran or followed people for, or None when none says."""
    best = None
    for s in studies:
        if s.get("pmid") not in set(cited_pmids or ()) or evidence.is_review(s):
            continue
        for m in _DURATION.finditer(str(s.get("abstract") or "")):
            days = float(m.group(1)) * _DAYS[m.group(2).lower().rstrip("s")]
            if best is None or days > best[0]:
                best = (days, f"{m.group(1)} {m.group(2).lower()}")
    return best


def gaps(studies: list[dict], cited_pmids, claim: str = "", found: list[dict] | None = None) -> list[str]:
    """Open questions code can see in the studies themselves, most important
    first."""
    cited = [s for s in studies if s.get("pmid") in set(cited_pmids or ())]
    if not cited:
        return []
    out = []
    basis = evidence.evidence_basis(studies, cited_pmids)
    trial = any(evidence.design_rank(s) == "trial" for s in cited)
    if basis == "observational":
        out.append("Whether the link is a cause, since only observational studies were found.")
    elif not trial:
        out.append("No randomized trial among these studies tested this directly.")
    narrow = evidence.narrow_populations(studies, cited_pmids)
    if narrow:
        out.append(f"Whether the results hold outside {', '.join(narrow)}.")
    if basis == "small":
        out.append("The trials found were small, so larger ones could change the answer.")
    longest = longest_follow_up(studies, cited_pmids)
    if longest and longest[0] < SHORT_DAYS and trial:
        out.append(f"Follow-up was short: the longest study here ran {longest[1]}.")
    if found and disagree(found):
        out.append("Why the studies do not agree.")
    return out


def still_open(studies: list[dict], cited_pmids, claim: str = "", pick: str = "",
               found: list[dict] | None = None, fallback: str = "") -> str:
    """The open question: the first gap code found, then the model's pick
    from the fixed list when it is a different question and fits."""
    found_gaps = gaps(studies, cited_pmids, claim, found)
    pick = pick if pick in OPEN_QUESTIONS else ""
    parts = found_gaps[:1]
    if pick and len(" ".join(parts + [pick])) <= STILL_OPEN_MAX and not (
            parts and pick.startswith("Whether the link is a cause")
            and "cause" in parts[0]):
        parts.append(pick)
    return " ".join(parts) if parts else (fallback or "")


# ---------------------------------------------------------------------
# The reading of causal claims
# ---------------------------------------------------------------------

_ASSOCIATION = re.compile(
    r"\b(?:linked to|associated with|correlat\w+|tied to|connected to|risk factor|"
    r"more likely|less likely|goes with|go with)\b", re.IGNORECASE)
_CAUSAL_CLAIM = re.compile(
    r"\b(?:cur(?:e|es|ed|ing)|prevent(?:s|ed|ing)?|caus(?:e|es|ed|ing)|"
    r"leads? to|results? in|triggers?|reduc(?:e|es|ed|ing)|lower(?:s|ed|ing)?|"
    r"rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boosts?|improv(?:e|es|ed|ing)|"
    r"helps?|protects?|stops?|fix(?:es)?|heals?|reverses?|treats?|kills?|"
    r"makes? (?:you|your|people)|slows?|speeds? up|(?:good|bad|healthy|harmful|"
    r"dangerous|safe) for|promotes?|damages?|harms?)\b", re.IGNORECASE)


_PLAIN_CAUSAL = re.compile(
    r"\b(?:cur(?:e|es|ed|ing)|prevent(?:s|ed|ing)?|caus(?:e|es|ed|ing)|leads? to|"
    r"results? in|triggers?)\b", re.IGNORECASE)


def causal_claim(claim: str) -> bool:
    """The claim says one thing makes another happen (or is good or bad for
    it), as against saying two things go together. A claim worded as a link
    ("linked to a lower risk") is a claim about a link, whatever else it says,
    unless it also names a cause outright."""
    claim = str(claim or "")
    if not _CAUSAL_CLAIM.search(claim):
        return False
    if _ASSOCIATION.search(claim):
        return bool(_PLAIN_CAUSAL.search(_ASSOCIATION.sub(" ", claim)))
    return True


OBSERVATIONAL_TLDR = ("The studies found a link, but observational studies can't show "
                      "that one thing causes the other.")
OBSERVATIONAL_NOTE = ("These studies only observed people, so they can show a link but not "
                      "that it is the cause.")
NO_FINDINGS = ("No finding in these studies could be matched word for word to its abstract, "
               "so no summary is shown. The studies used are listed below.")
