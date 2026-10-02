"""
The deeper layer under a verdict, and the gate that keeps it honest.

The top of a result is three lines: the verdict, one takeaway, one open
question. Those are the answer. This module is what sits under "Read the
full breakdown": the claim split into its parts, what the strongest
studies actually found, how strong the evidence is, who it applies to,
and what is still unknown.

It is longer prose than anything else on the page, which makes it the
easiest place for a model to start inventing. So none of it is trusted.
`ground()` is a gate, not a formatter: it takes what the model returned
and throws away everything it cannot tie back to the studies that were
actually fetched.

Four rules, each enforced here rather than hoped for in the prompt:

  1. Every sentence cites at least one study number, and the number has
     to be in range. A sentence with no citation is dropped before the
     page is rendered, the same way `verdict.py` drops a citation the
     model was never shown.
  2. Every number in a sentence has to appear in the abstracts. The
     corpus is the titles, abstracts, journals and years of the studies
     that went into the prompt; a figure that is not in it is an invented
     figure, and the sentence carrying it goes. When nothing survives in
     the effect-size line, the page says the abstracts do not report the
     size of the effect, because by then that is a measured fact.
  3. Plain language, measured. Sentences over 40 words are dropped, and
     the medical terms in GLOSS are explained in brackets the first time
     they appear anywhere in the breakdown. `reading_grade()` is the
     check on the result; the tests hold it to about 8th grade.
  4. The wording may not outrun the evidence. SOFTEN rewrites "prevents",
     "cures", "wards off" and their relatives into weaker phrasings.
     Every substitution is strictly weaker than what it replaces, so the
     gate can only ever lower the certainty of a sentence, never raise
     it. The reader's own claim is never softened: a claim that says
     "cures" is quoted back as it was typed.

None of this asks a model anything. The whole layer rides on the verdict
call that already runs, so a check is still two model calls.
"""
from __future__ import annotations

import re

# Caps, in characters, applied through verdict.tidy_prose. Longer than the
# ticket's, because this layer is the long read; still short enough that no
# single field can run away with the page.
ASSESSMENT_MAX = 260
PARAGRAPH_MAX = 520
FIELD_MAX = 460

# The longest a sentence may be before it is dropped, and the most glosses
# one sentence may carry. Sentence length is the biggest single driver of
# reading grade, and these two are the only real control over it that does
# not involve rewriting the model's words. "baseline (the measurement taken
# before anything started) serum (the liquid part of blood, which is what
# gets measured) levels" is what one gloss per term, unlimited, produces.
MAX_SENTENCE_WORDS = 32
MAX_GLOSSES_PER_SENTENCE = 1

# Said in code, not by the model: by the time either of these is used, the
# gate has established it is true of the surviving text.
NO_EFFECT_SIZE = "The abstracts don't report the size of the effect."
NO_STUDY_FOR_PART = "No study in this set tests this part of the claim."

# ---------------------------------------------------------------------
# What the model is asked for, and the shape it must come back in.
# These are merged into the single verdict call in verdict.py.
# ---------------------------------------------------------------------

SCHEMA_PROPERTIES = {
    "parts": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "part": {"type": "string"},
                "assessment": {"type": "string"},
            },
            "required": ["part", "assessment"],
            "additionalProperties": False,
        },
    },
    "evidence": {"type": "array", "items": {"type": "string"}},
    "effect_size": {"type": "string"},
    "strength": {"type": "string"},
    "applies_to": {"type": "string"},
    "not_applies_to": {"type": "string"},
    "unknowns": {"type": "string"},
}

SCHEMA_REQUIRED = list(SCHEMA_PROPERTIES)

PROMPT = """
Then write the deeper layer, for the reader who opens "Read the full
breakdown" under that verdict. It is collapsed by default, so it is for
someone who has already read the verdict and wants to understand it.

Hard rules for everything in the breakdown:

  * End every sentence with the studies it rests on, in brackets, like
    "(Study 3)" or "(Studies 1, 4)". A sentence with no study number is
    deleted before the reader sees it, so an uncited sentence is wasted
    work.
  * Every number you write must appear in one of the abstracts above.
    Do not round, do not convert, do not average two numbers into a
    third. Copy the figure. A sentence containing a number that is not
    in the abstracts is deleted.
  * Write at about an 8th-grade reading level. Short sentences, common
    words. Keep sentences under 40 words; longer ones are deleted.
  * Match the words to the size of the effect. Never "prevents",
    "cures", "wards off", "stops" or "proves" for an effect that is
    small, inconsistent, or measured in one trial. Say what changed and
    by how much.

"parts": split the claim into the things it actually asserts, in the
order it asserts them, and judge each separately. A claim with a
condition in it ("but only if you are deficient") has that condition as
its own part. Quote the part in the reader's own words, shortened; put
your judgement in "assessment". One to four parts. A claim that asserts
one thing gets one part.

"evidence": 3 to 5 short paragraphs, each 2 or 3 sentences, on what the
strongest studies found. Say how big the effect was in the abstracts'
own numbers, and who was studied: how many people, their ages, whether
they were healthy or ill, the dose, and how long it ran.

"effect_size": one or two sentences giving the size of the effect in the
abstracts' own figures. If no abstract reports how large the effect was,
write exactly: "The abstracts don't report the size of the effect."

"strength": 2 to 4 sentences on why the verdict is what it is. Name the
study types, whether they agree with each other, how many people were in
them, and any result that points the other way.

"applies_to": who these studies were actually about, in 1 to 3 sentences.

"not_applies_to": who they were not about, and so who cannot read this
verdict as being about them, in 1 to 3 sentences.

"unknowns": 2 to 4 sentences on what these studies leave open. This is
the long version of "still_open", so it can name study numbers where
that one cannot.
""".strip()

PROMPT_SHAPE = """  "parts": [{"part": "a piece of the claim, in the reader's words", "assessment": "what the studies say about that piece, ending in (Study N)"}],
  "evidence": ["paragraph, every sentence ending in (Study N)", "..."],
  "effect_size": "the size of the effect in the abstracts' own numbers, ending in (Study N)",
  "strength": "why this verdict, ending in (Study N)",
  "applies_to": "who was studied, ending in (Study N)",
  "not_applies_to": "who was not, ending in (Study N)",
  "unknowns": "what these studies leave open, ending in (Study N)","""

# ---------------------------------------------------------------------
# Rule 4: the wording may not outrun the evidence.
#
# Ordered, longest phrase first, so "protects against" is rewritten as a
# phrase rather than leaving a stray "against" behind. Every right-hand
# side is a weaker claim than its left-hand side.
# ---------------------------------------------------------------------

SOFTEN = [
    (r"\bwards?\s+off\b", "lowers the risk of"),
    (r"\bwarded\s+off\b", "lowered the risk of"),
    (r"\bprotects?\s+against\b", "lowers the risk of"),
    (r"\bprotected\s+against\b", "lowered the risk of"),
    (r"\bimmune\s+to\b", "at lower risk of"),
    (r"\bimmunity\s+to\b", "lower risk of"),
    (r"\bproof\s+that\b", "evidence that"),
    (r"\bprevents\b", "lowers the risk of"),
    (r"\bprevent\b", "lower the risk of"),
    (r"\bprevented\b", "lowered the risk of"),
    (r"\bcures\b", "helps with"),
    (r"\bcure\b", "help with"),
    (r"\bcured\b", "helped with"),
    (r"\beliminates\b", "reduces"),
    (r"\beliminate\b", "reduce"),
    (r"\bguarantees\b", "may help"),
    (r"\bguarantee\b", "may help"),
    (r"\bproves\b", "suggests"),
    (r"\bprove\b", "suggest"),
    (r"\bproven\b", "supported"),
    (r"\bblocks\b", "reduces"),
    (r"\bstops\b", "reduces"),
]

_SOFTEN = [(re.compile(p, re.IGNORECASE), r) for p, r in SOFTEN]


def soften(text: str) -> str:
    """Rewrite certainty the evidence here can never support."""
    t = text or ""
    for pattern, weaker in _SOFTEN:
        t = pattern.sub(weaker, t)
    # A substitution at the head of a sentence must not leave it lowercase.
    return re.sub(r"(^|(?<=[.!?]\s))([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), t)


# ---------------------------------------------------------------------
# Rule 3: the terms a reader should not have to look up.
#
# Glosses are written here, not by the model, so they cannot drift and
# cannot be wrong about this claim. Longest key first when matching, so
# "systematic review" is not glossed as "review".
# ---------------------------------------------------------------------

GLOSS = {
    "randomized controlled trial": "people were put in groups at random, then compared",
    "randomised controlled trial": "people were put in groups at random, then compared",
    "systematic review": "a search of every study on a question, by a set method",
    "meta-analysis": "the results of many studies pooled into one",
    "confidence interval": "the range the real answer is probably in",
    "odds ratio": "how much more likely an outcome was",
    "relative risk": "the risk in one group divided by the risk in another",
    "hazard ratio": "how much sooner something happened in one group",
    "statistically significant": "unlikely to be chance alone",
    "placebo": "a dummy treatment with nothing active in it",
    "cohort": "a group followed over time",
    "incidence": "how many new cases there were",
    "prevalence": "how many people have it at one time",
    "subgroup": "a smaller group picked out of the whole",
    "baseline": "the measurement taken before anything started",
    "prophylaxis": "treatment given to stop something starting",
    "supplementation": "taking it as a supplement",
    "deficiency": "having too little of it in the blood",
    "serum": "the liquid part of blood, which is what gets measured",
    "adverse event": "something that went wrong during the study",
    "comorbidity": "another illness at the same time",
    "bolus": "one large dose at once",
    "observational": "people were watched, not assigned",
    "p value": "how likely a result this big would be by chance",
}

_GLOSS_KEYS = sorted(GLOSS, key=len, reverse=True)


def gloss_once(text: str, done: set) -> str:
    """
    Explain each term in brackets the first time it appears, once per
    breakdown and at most once per sentence. `done` is carried across fields
    in render order, so the bracket lands on the first occurrence the reader
    actually meets. A term that only ever appears in sentences that have
    already used their bracket goes unglossed: two parentheses in one noun
    phrase costs more reading than the term it explains.

    A gloss that would push its sentence past MAX_SENTENCE_WORDS is not
    inserted, and the term is left unmarked so a shorter sentence further
    down can carry the explanation instead. The length rule is about the
    sentence the reader is handed, so it has to count the brackets too.
    """
    out = []
    for sentence in sentences(text or ""):
        used = 0
        for key in _GLOSS_KEYS:
            if used >= MAX_GLOSSES_PER_SENTENCE:
                break
            if key in done:
                continue
            m = re.search(r"\b" + re.escape(key) + r"s?\b", sentence, re.IGNORECASE)
            if not m:
                continue
            # A term the model already explained in its own brackets is left alone.
            if sentence[m.end():m.end() + 2].startswith(" ("):
                done.add(key)
                continue
            glossed = f"{sentence[:m.end()]} ({GLOSS[key]}){sentence[m.end():]}"
            if len(re.findall(r"\S+", glossed)) > MAX_SENTENCE_WORDS:
                continue
            sentence = glossed
            done.add(key)
            used += 1
        out.append(sentence)
    return " ".join(out)


# ---------------------------------------------------------------------
# Rule 1: every sentence cites a study the model was shown.
# ---------------------------------------------------------------------

_REF = re.compile(r"\bstud(?:y|ies)\s+((?:\d+)(?:\s*(?:,|,?\s*and\b|&)\s*\d+)*)",
                  re.IGNORECASE)

# Abbreviations that end in a full stop without ending a sentence.
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|vs|approx|no|fig|cf|et al|dr|mr|ms|prof)\.$",
                     re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """Split prose into sentences without splitting "e.g." or "12.5%"."""
    out, buf = [], ""
    for piece in re.split(r"(?<=[.!?])(\s+)", text or ""):
        if piece.strip() == "" and piece != "":
            buf += piece
            continue
        buf += piece
        if buf.strip() and not _ABBREV.search(buf.strip()):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def refs(text: str, count: int) -> list[int]:
    """The in-range study numbers a sentence cites, in the order given."""
    found = []
    for m in _REF.finditer(text or ""):
        for n in re.findall(r"\d+", m.group(1)):
            n = int(n)
            if 0 < n <= count and n not in found:
                found.append(n)
    return found


# ---------------------------------------------------------------------
# Rule 2: every number came out of an abstract.
# ---------------------------------------------------------------------

_NUMERAL = re.compile(r"\d+(?:[.,]\d+)*")


def _canon(num: str) -> str:
    """1,200 and 1200 are the same number; so are 12.0 and 12."""
    n = num.replace(",", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n or "0"


def corpus(studies: list[dict]) -> set:
    """Every number that appears anywhere in the records that were fetched."""
    bag = set()
    for s in studies or []:
        for field in ("title", "abstract", "journal", "year", "pmid", "data_banks"):
            v = s.get(field)
            if not v:
                continue
            for m in _NUMERAL.finditer(str(v)):
                bag.add(_canon(m.group(0)))
    return bag


def ungrounded_numbers(text: str, known: set) -> list[str]:
    """
    Numbers in this sentence that are in no abstract. Study references are
    removed first: "Study 7" points at a record, it does not quote one.
    """
    stripped = _REF.sub(" ", text or "")
    return [m.group(0) for m in _NUMERAL.finditer(stripped)
            if _canon(m.group(0)) not in known]


# ---------------------------------------------------------------------
# Rule 3, measured: Flesch-Kincaid grade level.
# ---------------------------------------------------------------------

def syllables(word: str) -> int:
    """Vowel groups, with the usual silent-e correction. Close enough."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    return max(1, n)


def reading_grade(text: str) -> float:
    """
    Flesch-Kincaid grade level. Study references are dropped first: "(Study
    3)" is a tap target, not a word the reader has to parse.
    """
    clean = _REF.sub(" ", text or "")
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", clean)
    sents = [s for s in sentences(clean) if s.strip()]
    if not words or not sents:
        return 0.0
    syl = sum(syllables(w) for w in words)
    return round(0.39 * (len(words) / len(sents)) + 11.8 * (syl / len(words)) - 15.59, 1)


# ---------------------------------------------------------------------
# The gate itself.
# ---------------------------------------------------------------------

def keep_sentences(text: str, count: int, known: set) -> str:
    """
    The three sentence-level rules, applied in one pass. Returns only the
    sentences that cite a study in range, carry no number the abstracts do
    not have, and are short enough to read.
    """
    kept = []
    for s in sentences(text):
        if not refs(s, count):
            continue
        if ungrounded_numbers(s, known):
            continue
        if len(re.findall(r"\S+", s)) > MAX_SENTENCE_WORDS:
            continue
        kept.append(s)
    return " ".join(kept)


# Render order. Matters because the glossary brackets the first appearance
# of a term, and "first" has to mean first on the page.
FIELDS = ("parts", "evidence", "effect_size", "strength",
          "applies_to", "not_applies_to", "unknowns")


def label(text: str, limit: int = 120) -> str:
    """
    One part of the claim, quoted back. Not run through tidy_prose: that
    closes a sentence with a full stop, and a quoted fragment inside the
    reader's own words ("only if you are deficient") is not a sentence.
    Dashes still go, because no string a reader sees may carry one.
    """
    t = re.sub(r"\s*[—–]\s*", ", ", str(text or "")).strip()
    t = re.sub(r"\s{2,}", " ", t).strip(" \"“”'")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    return t


def ground(raw, studies: list[dict], tidy) -> dict | None:
    """
    Gate whatever the model returned into a breakdown the page can render,
    or None if too little survived to be worth opening.

    `tidy` is verdict.tidy_prose, passed in rather than imported, so this
    module stays free of the provider plumbing and the import stays one
    way round.
    """
    if not isinstance(raw, dict):
        return None
    count = len(studies or [])
    if not count:
        return None
    known = corpus(studies)

    def clean(value, limit):
        return keep_sentences(soften(tidy(str(value or ""), limit)), count, known)

    parts = []
    for item in (raw.get("parts") or [])[:4]:
        if not isinstance(item, dict):
            continue
        piece = label(item.get("part"))
        if not piece:
            continue
        # The part is the reader's own claim, quoted back. It is never
        # softened and never gated: it is not a finding about the evidence.
        assessment = clean(item.get("assessment"), ASSESSMENT_MAX)
        cites = refs(assessment, count)
        parts.append({
            "part": piece,
            "assessment": assessment or NO_STUDY_FOR_PART,
            "studies": cites,
        })

    # Up to five paragraphs that survive the gate, not the first five the
    # model wrote. Capping before the gate would let three dropped sentences
    # cost the reader three good ones.
    paragraphs = []
    for para in (raw.get("evidence") or [])[:12]:
        text = clean(para, PARAGRAPH_MAX)
        if text:
            paragraphs.append(text)
        if len(paragraphs) == 5:
            break

    effect = clean(raw.get("effect_size"), FIELD_MAX)
    # By here the gate has checked every figure in it against the abstracts.
    # If nothing survived, the honest line is that there is no figure to give.
    if not effect or not _NUMERAL.search(_REF.sub(" ", effect)):
        effect = NO_EFFECT_SIZE

    out = {
        "parts": parts,
        "evidence": paragraphs,
        "effect_size": effect,
        "strength": clean(raw.get("strength"), FIELD_MAX),
        "applies_to": clean(raw.get("applies_to"), FIELD_MAX),
        "not_applies_to": clean(raw.get("not_applies_to"), FIELD_MAX),
        "unknowns": clean(raw.get("unknowns"), FIELD_MAX),
    }

    # A breakdown with no paragraphs and no judged parts is a heading with
    # nothing under it. Better no disclosure than an empty one.
    if not paragraphs and not any(p["studies"] for p in parts):
        return None

    done = set()
    for key in FIELDS:
        if key == "parts":
            for p in out["parts"]:
                if p["assessment"] != NO_STUDY_FOR_PART:
                    p["assessment"] = gloss_once(p["assessment"], done)
        elif key == "evidence":
            out["evidence"] = [gloss_once(p, done) for p in out["evidence"]]
        elif out[key] and out[key] != NO_EFFECT_SIZE:
            out[key] = gloss_once(out[key], done)

    # Two measurements, kept with the text they describe: how many distinct
    # records the whole layer rests on (the summary line shows it), and what
    # reading grade the surviving prose came out at.
    out["rests_on"] = len(cited_pmids(out, studies))
    out["grade"] = reading_grade(flatten(out))
    return out


def flatten(bd: dict) -> str:
    """Every sentence of a breakdown, in render order. For measuring."""
    if not bd:
        return ""
    bits = []
    for p in bd.get("parts") or []:
        bits.append(p.get("assessment") or "")
    bits += list(bd.get("evidence") or [])
    for key in ("effect_size", "strength", "applies_to", "not_applies_to", "unknowns"):
        bits.append(bd.get(key) or "")
    return " ".join(b for b in bits if b)


def cited_pmids(bd: dict, studies: list[dict]) -> list[str]:
    """
    The PMIDs the breakdown leans on, mapped the same way the verdict's own
    citations are. Used to check the deeper layer never reaches past the
    records the shallow one did.
    """
    if not bd:
        return []
    out = []
    for n in refs(flatten(bd), len(studies or [])):
        pmid = studies[n - 1].get("pmid")
        if pmid and pmid not in out:
            out.append(pmid)
    return out
