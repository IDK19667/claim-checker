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
  3. Plain language, measured. PLAIN swaps the medical words that have an
     everyday twin, sentences over 28 words are dropped, and the terms in
     GLOSS that are left are explained in brackets the first time they
     appear anywhere in the breakdown. `reading_grade()` is the check on
     the result, and it is reported with the breakdown rather than
     asserted: see TARGET_GRADE for what it is aimed at and why real
     prose about trials does not always reach it.
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
from decimal import Decimal, ROUND_HALF_UP

import evidence

# Caps, in characters, applied through verdict.tidy_prose. Longer than the
# ticket's, because this layer is the long read; still short enough that no
# single field can run away with the page.
ASSESSMENT_MAX = 260
# The most paragraphs "What the studies found" may run to, matching the five
# things the prompt asks each one to cover, plus one.
PARAGRAPHS_MAX = 6
PARAGRAPH_MAX = 520
FIELD_MAX = 460

# The longest a sentence may be before it is dropped, and the most glosses
# one sentence may carry. Sentence length is the biggest single driver of
# reading grade, and these two are the only real control over it that does
# not involve rewriting the model's words. "baseline (the measurement taken
# before anything started) serum (the liquid part of blood, which is what
# gets measured) levels" is what one gloss per term, unlimited, produces.
MAX_SENTENCE_WORDS = 28
MAX_GLOSSES_PER_SENTENCE = 1

# The grade this layer is written to. Measured on the prose after the plain
# words go in and with the claim's own subject excluded, because a reader who
# typed "creatine" has already met the word "creatine".
#
# Aimed at, not guaranteed, and the breakdown carries its own measured grade
# so the number is never asserted. Flesch-Kincaid is
# 0.39*(words/sentence) + 11.8*(syllables/word) - 15.59, and real prose about
# trials runs near 1.95 syllables a word even after PLAIN has taken out every
# long word with a short twin. That term alone is 23.0 - 15.59 = 7.4 grades,
# so 10 needs sentences of about 6 words: too short to say "an odds ratio of
# 0.88 across 10933 people". The honest trade is to keep the figure and
# report the grade it costs, not to drop the figure to hit the number.
TARGET_GRADE = 10.0

# Said in code, not by the model: by the time either of these is used, the
# gate has established it is true of the surviving text.
NO_EFFECT_SIZE = "The abstracts don't report the size of the effect."
# When the effect-size sentence itself does not survive the gate but a figure
# did survive elsewhere, NO_EFFECT_SIZE would be a false statement sitting
# inches under the number it denies. This points at the figures instead.
EFFECT_SIZE_ABOVE = "The figures these studies report are in the paragraphs above."
NO_STUDY_FOR_PART = "No study in this set tests this part of the claim."
# The same distinction one step along. When the model did write a judgement
# on the only part there is and the gate dropped it, "no study tests this"
# is a false sentence printed above paragraphs of studies that do. This says
# where the answer went instead.
PART_BELOW = "What these studies found about this is in the paragraphs below."

# How the translated figure opens, written in ground() and looked for in
# says(). One string, so the two cannot drift apart.
TRANSLATION_LEAD = "That is "

# A figure a reader can picture. Used to tell an effect-size line that
# already translates its ratio from one that only quotes it.
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?(?:%|per ?cent)")

# ---------------------------------------------------------------------
# Plain words
#
# The glossary below explains a term in brackets and leaves it standing.
# This table does the opposite: it takes the terms that have an ordinary
# English equivalent and uses the ordinary English, because an explained
# hard word is still a hard word to read past.
#
# `keep` marks the handful where the medical term carries information the
# plain words lose, so the original follows in brackets the first time. The
# rest are swapped silently: nobody needs to be told that "chest and throat
# infections" was printed as "respiratory tract infections".
#
# Longest first, so "respiratory tract infection" is matched before
# "infection" and "vitamin D deficiency" before "deficiency".
# ---------------------------------------------------------------------

# "Vaccine effectiveness" is a compound: swapping the noun for a clause
# printed "assess vaccine how well it works". After a word that makes it
# one, the term stays as written.
_COMPOUND_BEFORE = {
    "vaccine", "vaccines", "drug", "drugs", "treatment", "treatments", "therapy",
    "intervention", "interventions", "clinical", "real-world", "comparative", "relative",
    "program", "programme", "screening", "mask", "medication", "booster"}


# Where the sentence needs a noun, the plain one is "effect": "it lacked
# efficacy" is "it lacked an effect", "no efficacy" is "no effect".
_NOUN_AFTER = {"no", "any", "little", "some", "much", "less", "more", "greater", "similar",
               # "safety and efficacy" pairs two nouns: "safety and effect"
               "and", "or",
               # and after an adjective: "lack clear efficacy" is "lack clear effect"
               "clear", "proven", "significant", "established", "limited", "modest", "real",
               "overall", "long-term", "short-term", "lasting", "sustained", "meaningful",
               "definite", "definitive", "strong", "weak", "high", "low", "superior",
               "inferior", "equal", "good", "poor", "better", "worse", "consistent", "added",
               "additional", "potential", "possible", "uncertain"}
_ARTICLE_AFTER = {"lacked", "lack", "lacks", "showed", "show", "shows", "demonstrated",
                  "demonstrate", "demonstrates", "had", "has", "have", "proved", "found",
                  "confirmed", "confirm", "confirms", "without", "with"}


_CLAUSE_SLOT = {"measured", "measure", "measures", "measuring", "estimated", "estimate", "assessed",
                "assess", "assesses", "examined", "examine", "examines", "studied", "tested", "test",
                "tests", "compared", "compare", "compares", "evaluated", "evaluate", "evaluates",
                "about", "on", "for", "in", "regarding", "whether", "to", "into"}


def _how_well(m: re.Match) -> str:
    before = re.search(r"([\w-]+)\s+$", m.string[:m.start()])
    word = before.group(1).lower() if before else ""
    if word in _COMPOUND_BEFORE:
        return m.group(0)
    if word in _NOUN_AFTER:
        return "effect"
    if word in _ARTICLE_AFTER:
        return "an effect"
    # "Safety and efficacy of X" needs a noun too: "safety and effect of X".
    if re.match(r"\s+of\b", m.string[m.end():]):
        return "effect"
    # A clause reads only where a clause can stand: at the head of the
    # sentence, or after a verb or preposition asking what was measured
    # ("tested how well it works"). After any other word, a modifier, the
    # word stays: "the pooled efficacy" is English, "the pooled how well it
    # works" is not.
    if not before or word in _CLAUSE_SLOT or re.search(r"[.;:,(]\s*$", m.string[:m.start()]):
        return "how well it works"
    return m.group(0)


# "Prevalence" and "incidence" are nouns in nearly every slot they fill:
# "the global prevalence of alcohol use", "the incidence was low",
# "melanoma incidence fell". A clause ("how common it is") only reads where
# a verb or preposition is asking what was measured; everywhere else the
# plain noun is "rate".
_CLAUSE_FITS = {"measured", "measure", "measures", "measuring", "estimated", "estimate",
                "estimates", "estimating", "assessed", "assess", "assesses", "examined",
                "examine", "examines", "studied", "tracked", "recorded"}


def _rate(clause: str):
    def swap(m: re.Match) -> str:
        before = re.search(r"(?:([\w-]+)\s+)?([\w-]+)\s+$", m.string[:m.start()])
        word = before.group(2).lower() if before else ""
        # "the estimated incidence": a participle after an article is an adjective
        det = (before.group(1) or "").lower() in {"the", "a", "an", "its", "their", "this"} if before else False
        if word in _CLAUSE_FITS and not det and not re.match(r"\s+of\b", m.string[m.end():]):
            return clause
        return "rate"
    return swap


def _sexed(m: re.Match) -> str:
    """The plain nouns for "male participants", "male and female subjects":
    "males", "males and females". No age: "men" or "girls" would state one
    the sentence did not."""
    names = {"male": "males", "female": "females"}
    if m.group(3):
        return names[m.group(3).lower()]
    joint = re.search(r" or | and |, ", m.group(0)).group(0)
    return f"{names[m.group(1).lower()]}{' and ' if joint == ', ' else joint}{names[m.group(2).lower()]}"


# A range or interval just before "including": "intervals including",
# "a likely range including". There "including" is a verb, not "like".
_RANGE_BEFORE = re.compile(r"\b(?:intervals?|ranges?|CIs?|C\.I\.)\s*(?:\([^)]*\)\s*)?(?:all\s+)?$",
                           re.IGNORECASE)


# Each row is (pattern, replacement, term-to-keep-in-brackets-or-None).
#
# Patterns, not bare words, because English does not let a noun be swapped
# for a phrase wherever it appears. "Supplementation" alone would turn
# "vitamin D supplementation lowers" into "vitamin D taking supplements
# lowers", and "the efficacy of X" into "the how well it works of X". So a
# term that reads differently inside a compound or after "the ... of" gets
# its own pattern for that position, and the general one is written to be
# inert: same part of speech, same number, drops straight in.
#
# Order is the order applied: the most specific pattern first.
PLAIN = [
    # Study designs. These are the longest words on the page and they turn
    # up in nearly every "strength" paragraph, which is why they are worth
    # a rule: "meta-analysis" alone is five syllables. The term follows in
    # brackets once, because a reader weighing two findings needs to know
    # which one was a pooled review of trials.
    # Singular and plural are separate rows: "meta-analyses show" has to
    # come out as "pooled reviews show", not "pooled review show".
    (r"\bsystematic reviews and meta[- ]?analyses\b", "pooled reviews",
     "systematic reviews and meta-analyses"),
    (r"\bsystematic review and meta[- ]?analysis\b", "pooled review",
     "systematic review and meta-analysis"),
    (r"\bmeta[- ]?analyses\b", "pooled reviews", "meta-analyses"),
    (r"\bmeta[- ]?analysis\b", "pooled review", "meta-analysis"),
    (r"\bdietary interventions?\b", "diet change", None),
    (r"\bintervention(s?)\b", r"treatment\1", None),
    (r"\bheterogeneity\b", "differences between the studies", None),

    # Outcomes.
    (r"\bacute respiratory (?:tract )?infections?\b",
     "sudden chest and throat infections", "acute respiratory infection"),
    (r"\bupper respiratory (?:tract )?infections?\b",
     "nose and throat infections", None),
    (r"\brespiratory (?:tract )?infection(s?)\b",
     r"chest and throat infection\1", "respiratory tract infection"),
    (r"\brespiratory illness(es)?\b", r"chest and throat illness\1", None),
    (r"\bmyocardial infarctions?\b", "heart attacks", "myocardial infarction"),
    (r"\bcerebrovascular accidents?\b", "strokes", "cerebrovascular accident"),
    (r"\bhypertension\b", "high blood pressure", "hypertension"),
    (r"\bhypercholesterolaemia\b|\bhypercholesterolemia\b",
     "high cholesterol", None),
    (r"\bhyperlipidaemia\b|\bhyperlipidemia\b", "high blood fats", None),
    (r"\bandrogenetic alopecia\b", "pattern hair loss", "androgenetic alopecia"),
    (r"\balopecia\b", "hair loss", "alopecia"),
    (r"\bmacular degeneration\b", "damage to the back of the eye",
     "macular degeneration"),
    (r"\basthenopia\b", "eye strain", "asthenopia"),
    (r"\bvisual fatigue\b", "eye strain", None),
    (r"\bmortality\b", "deaths", None),
    (r"\bcomorbidit(?:y|ies)\b", "other illnesses", None),
    (r"\badverse (?:events|effects|reactions)\b", "side effects", None),

    # Taking something. The compound comes first, so "vitamin D
    # supplementation" reads as a thing you do rather than a thing you are.
    (r"\bsupplementation with\b", "taking", None),
    (r"\bsupplementation\b", "supplement use", None),
    (r"\bsupplemented\b", "given supplements", None),
    (r"\bcaloric restriction\b|\bcalorie restriction\b",
     "eating fewer calories", None),

    # Too little of something. "Vitamin D deficiency" is "low vitamin D",
    # which needs the words the other way round, so it gets its own rule.
    # A callable, so the nutrient keeps its own spelling but not a capital
    # it only had because "Deficiency" happened to open the sentence.
    (r"\b(vitamin [A-K]\d?|iron|zinc|magnesium|calcium|folate) deficiency\b",
     lambda m: "low " + m.group(1)[0].lower() + m.group(1)[1:], "deficiency"),
    (r"\bdeficiency\b", "low levels", "deficiency"),
    # Attributive position needs the whole phrase rebuilt: "deficient" is a
    # predicate in "people who were deficient" and an adjective in "deficient
    # people", and only the second one breaks when the word becomes "low".
    (r"\b(?:vitamin [A-K]\d? )?(non-)?deficient (people|adults|children|men|women"
     r"|patients|participants|individuals|subjects|groups|populations)\b",
     lambda m: f"{m.group(2)} with {'normal' if m.group(1) else 'low'} levels", None),
    (r"\bnon-deficient\b", "not low", None),
    (r"\bmost deficient\b", "lowest", None),
    (r"\bdeficient\b", "low", None),
    (r"\binsufficiency\b", "slightly low levels", None),

    # The "the X of Y" trap: a noun here, a clause where it stands alone.
    (r"\b(?:the |its )effica(?:cy|ciousness) of\b", "the effect of", None),
    (r"\b(?:the|its|their|this)\s+(?:efficacy|effectiveness)\b(?!\s+of\b)", "how well it works", None),
    (r"\befficacy\b", lambda m: _how_well(m), None),
    (r"\b(?:the |its )effectiveness of\b", "the effect of", None),
    (r"\beffectiveness\b", lambda m: _how_well(m), None),
    (r"\bsleep(?:[- ]onset)? latency(?: time)?\b", "time to fall asleep", "sleep latency"),
    (r"\b(?:the |its )incidence of\b", "the rate of", None),
    (r"\bincidence\b", _rate("how often it happened"), None),
    (r"\b(?:the |its )prevalence of\b", "the rate of", None),
    (r"\bprevalence\b", _rate("how common it is"), None),
    (r"\b(?:the |its )duration of\b", "the length of", None),
    # "duration" sits in too many grammatical slots for a clause to replace
    # it. "The studies were of short duration" became "of short how long it
    # lasted", and "the duration was 12 weeks" became "the how long it
    # lasted was". One plain noun fits every slot, and reads shorter.
    (r"\bof (short|long|longer|shorter) duration\b", r"\1", None),
    (r"\bduration\b", "length", None),
    (r"\b(?:the |its )administration of\b", "the use of", None),
    (r"\b(?:the |its )initiation of\b", "the start of", None),
    (r"\b(?:the |its )pathogenesis of\b", "the development of", None),

    # Plain verbs and connectives.
    (r"\bdemonstrated\b|\bindicated\b", "showed", None),
    (r"\bdemonstrates\b|\bindicates\b", "shows", None),
    (r"\bevaluated\b|\bassessed\b", "tested", None),
    (r"\binvestigated\b|\bexamined\b", "looked at", None),
    (r"\butilized\b|\butilised\b", "used", None),
    (r"\butilize\b|\butilise\b", "use", None),
    (r"\bprior to\b", "before", None),
    (r"\bin order to\b", "to", None),
    (r"\bapproximately\b", "about", None),
    (r"\b(?:additionally|furthermore|moreover)\b", "also", None),
    (r"\bhowever,\s+", "but ", None),
    (r"\bhowever\b", "but", None),
    (r"\btherefore\b", "so", None),
    # "72 male participants" is "72 males", never "72 male people", and
    # never "72 men": the sentence may not say how old they were.
    (r"\b(?:(male|female)(?: or | and |, )(male|female) |(male|female) )(?:participants|subjects|individuals|people)\b",
     lambda m: _sexed(m), None),
    (r"\bparticipants\b|\bsubjects\b|\bindividuals\b", "people", None),
    (r"\belevated\b", "raised", None),
    (r"\bglycaemic control\b|\bglycemic control\b", "blood sugar control", None),
    # "Memory and cognitive function" is not "memory and thinking and
    # memory": where the sentence already names memory, thinking is the
    # half that is left to say.
    (r"\bcognitive function\b", lambda m: "thinking" if re.search(
        r"\bmemory\b", _sentence_around(m)) else "thinking and memory", None),
    (r"\bimmunomodulatory\b", "immune system", None),
    (r"\bcardiovascular\b", "heart and blood vessel", None),
    (r"\bgastrointestinal\b", "stomach and gut", None),
    (r"\bophthalmic\b", "eye", None),

    # Long words with a short twin. None of these carries information the
    # short one loses, which is the whole test for being on this list: a
    # "reduction" is a drop and an "association" is a link. Where a word
    # does carry something extra ("significant", "randomized") it is left
    # standing and the glossary explains it instead.
    (r"\bnutritional supplements?\b", "supplements", None),
    (r"\bassociated with\b", "linked to", None),
    (r"\bassociation(s?)\b", r"link\1", None),
    (r"\bconcentration(s?)\b", r"level\1", None),
    (r"\bpopulation(s?)\b", r"group\1", None),
    (r"\breduction(s?) in\b", r"drop\1 in", None),
    (r"\breduction(s?)\b", r"drop\1", None),
    (r"\bmagnitude\b", "size", None),
    (r"\bconsiderable\b|\bsubstantial\b", "large", None),
    (r"\bpreliminary\b", "early", None),
    (r"\bsubsequent\b", "later", None),
    (r"\badditional\b", "more", None),
    (r"\bnumerous\b", "many", None),
    (r"\bbeneficial\b", "helpful", None),
    (r"\bdetrimental\b", "harmful", None),
    (r"\bexhibited\b", "showed", None),
    (r"\bnevertheless\b", "even so", None),
    # "Including" introduces examples ("studies including adults") and is
    # also the verb of a range: "confidence intervals including zero" is not
    # "likely ranges like zero". Before a number, zero, one, the null or no
    # effect, or after a range or interval, it stays.
    (r"\bincluding\b(?!\s+(?:[-\u2212+]?\d|zero\b|one\b|unity\b|the null\b|null\b|no\s+"
     r"(?:effect|difference|change)\b))", lambda m: m.group(0) if _RANGE_BEFORE.search(
        m.string[max(0, m.start() - 40):m.start()]) else "like", None),
]

_PLAIN = [(re.compile(p, re.IGNORECASE), r, keep) for p, r, keep in PLAIN]

# Every term plain() can park in brackets beside its translation. Handing
# this in as the already-printed set turns the parking off, which is how a
# sentence the brackets alone pushed past the length rule gets a second try.
PARKED_TERMS = {keep for _, _, keep in PLAIN if keep}

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
    words. Keep every sentence under 18 words. The tool rewrites medical
    vocabulary into everyday words and sometimes keeps the original in
    brackets, which makes your sentence longer than you wrote it, and a
    sentence over 28 words after that is deleted, brackets and all. One
    fact per sentence. Two facts are two sentences.
  * Say "people with low levels", never "deficient people". Name a group
    by what is true of it in ordinary words.
  * Match the words to the size of the effect. Never "prevents",
    "cures", "wards off", "stops" or "proves" for an effect that is
    small, inconsistent, or measured in one trial. Say what changed and
    by how much.
  * A figure for one group is half a result. Give the other group's
    figure beside it: "1.7 episodes a year with extra water against 3.2
    without (Study 4)", never "1.7 episodes compared to controls".
  * When an abstract says a difference was not statistically
    significant, or its confidence interval includes no effect at all (a
    ratio whose interval runs past 1, a difference whose interval runs
    past 0), the finding is "no clear difference", and the sentence says
    so. Never call it a small effect.

"parts": split the claim into the things it actually asserts, in the
order it asserts them, and judge each separately. A claim with a
condition in it ("but only if you are deficient") has that condition as
its own part. Quote the part in the reader's own words, shortened, with
their verb exactly as they wrote it: "Taking vitamin D supplements in
winter", never "Takes vitamin D supplements in winter". Put your
judgement in "assessment". One to four parts. A claim that asserts one
thing gets one part.

When a block headed SUBGROUP FINDINGS appears above, those sentences are
the abstracts' own answer to the claim's condition, and the part that
quotes that condition must be answered from them. Never write that a
condition is untested when a sentence in that block tests it. If two of
those sentences disagree, say both and say which paper is which: "the
2017 pooled review found the effect was larger in people starting below
25 nmol/L (Study 1), while the 2021 update found no clear effect in any
baseline group (Study 4)" is the answer, not "this was not tested".

"evidence": 4 to 6 short paragraphs, each 2 or 3 sentences. Give one
paragraph to each of these, in this order, and leave out any that these
abstracts cannot support rather than padding it:

  1. What the single biggest or strongest study found.
  2. Whether the other studies agree with it, and where they disagree.
     Name the disagreement; two studies that found opposite things is
     the most useful thing a reader can be told.
  3. How big the effect was, in the abstracts' own figures. Copy the
     ratio or percentage they print. Do not work out a percentage of
     your own: a number that is not in an abstract deletes the sentence
     it is in, and the tool adds the plain-words translation itself.
  4. Who was studied: how many people, their ages, whether they were
     healthy or ill, the dose, and how long it ran.
  5. Any indirect evidence, said to be indirect, with what it does and
     does not show.

"effect_size": one or two sentences giving the size of the effect in the
abstracts' own figures, copied exactly, with the confidence interval
when the abstract prints one: "the adjusted odds ratio was 0.88 (95% CI
0.81 to 0.96) (Study 1)". You may call the effect small, moderate or
large, unless the interval includes no effect, which is no clear
difference and not a small effect. Do
not convert the ratio into a percentage: the tool does that arithmetic
itself, to fixed thresholds, and adds it to this line. If no abstract
reports how large the effect was, write exactly: "The abstracts don't
report the size of the effect."

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

PROMPT_SHAPE = """  "parts": [{"part": "a piece of the claim, in the reader's words", "assessment": "what the studies say about that piece, under 18 words, ending in (Study N)"}],
  "evidence": ["paragraph of 2 or 3 sentences, each under 18 words, each ending in (Study N)", "..."],
  "effect_size": "the size of the effect in the abstracts' own numbers, under 18 words a sentence, ending in (Study N)",
  "strength": "why this verdict, under 18 words a sentence, ending in (Study N)",
  "applies_to": "who was studied, under 18 words a sentence, ending in (Study N)",
  "not_applies_to": "who was not, under 18 words a sentence, ending in (Study N)",
  "unknowns": "what these studies leave open, under 18 words a sentence, ending in (Study N)","""

# ---------------------------------------------------------------------
# Rule 4: the wording may not outrun the evidence.
#
# Ordered, longest phrase first, so "protects against" is rewritten as a
# phrase rather than leaving a stray "against" behind. Every right-hand
# side is a weaker claim than its left-hand side.
# ---------------------------------------------------------------------

SOFTEN = [
    # The verb keeps its own number: "these changes protect against" is
    # "these changes lower the risk of", never "lowers".
    (r"\bwards\s+off\b", "lowers the risk of"),
    (r"\bward\s+off\b", "lower the risk of"),
    (r"\bwarded\s+off\b", "lowered the risk of"),
    (r"\bprotects\s+against\b", "lowers the risk of"),
    (r"\bprotect\s+against\b", "lower the risk of"),
    (r"\bprotected\s+against\b", "lowered the risk of"),
    (r"\bimmune\s+to\b", "at lower risk of"),
    (r"\bimmunity\s+to\b", "lower risk of"),
    (r"\bproof\s+that\b", "evidence that"),
    (r"\bprevents\b", "lowers the risk of"),
    (r"\bprevent\b", "lower the risk of"),
    (r"\bprevented\b", "lowered the risk of"),
    # Not "cure" the noun: "effect sizes for cold cures (Study 5)" became
    # "effect sizes for cold helps with". A verb has its object after it; a
    # noun is followed by a bracket, a stop, or "for", "of", "is" and kin,
    # or "rate" ("cure rates rose").
    (r"\bcures\b(?!\s*(?:[(.,;:!?]|$|(?:for|of|is|are|was|were|exist|exists|rates?)\b))", "helps with"),
    (r"\bcure\b(?!\s*(?:[(.,;:!?]|$|(?:for|of|is|are|was|were|exist|exists|rates?)\b))", "help with"),
    (r"\bcured\b", "helped with"),
    (r"\beliminates\b", "reduces"),
    (r"\beliminate\b", "reduce"),
    (r"\bguarantees\b", "may help"),
    (r"\bguarantee\b", "may help"),
    (r"\bproves\b", "suggests"),
    (r"\bprove\b", "suggest"),
    (r"\bproven\b", "supported"),
    # Not "blocks" the noun: "no evidence that blue blocks stop eye strain"
    # became "no evidence that reduces stop eye strain". A verb straight
    # after it is the tell that the word was a thing, not an action.
    (r"\bblocks\b(?!\s+(?:stop|prevent|reduce|lower|help|work|do|does|did|"
     r"are|is|was|were)\b)", "reduces"),
    # Not "stops X-ing": "where it stops working" is a question about where
    # the effect ends, not a claim that it prevents anything, and "reduces
    # providing protection" is not a sentence.
    (r"\bstops\b(?!\s+\w+ing\b)", "reduces"),
]

_SOFTEN = [(re.compile(p, re.IGNORECASE), r) for p, r in SOFTEN]

# ---------------------------------------------------------------------
# Rule 5: the words match the size of the effect.
#
# `evidence.effect_size` reads the figure out of the prose and labels it
# small, moderate or large against thresholds fixed in code. This is what
# the label is then allowed to buy: a small effect may not be announced
# with a verb that sounds like a cure. Like soften(), every substitution
# here is weaker than what it replaces, so it can only ever pull a
# sentence back towards the evidence, never push it past it. A large
# effect is left exactly as the model wrote it: nothing here strengthens.
# ---------------------------------------------------------------------

HEDGE = {
    "small": "slightly ",
    "moderate": "",      # the plain verb is already the right strength
    "large": "",
}

# Verbs that assert a change, and which of them already carry their own
# hedge, so "slightly" is never doubled or stacked on "may".
_CHANGE_VERB = re.compile(
    r"\b(cuts?|lowers?|reduces?|raises?|increases?|improves?|worsens?|boosts?|"
    r"helps?|protects?|speeds?|slows?)\b", re.IGNORECASE)
_ALREADY_HEDGED = re.compile(
    r"\b(may|might|could|slightly|a little|somewhat|marginally|probably|"
    r"barely|hardly|no |not |never)\b", re.IGNORECASE)


def match_effect(text: str, label: str | None) -> str:
    """
    Pull a sentence back to the size of the effect behind it.

    "Vitamin D reduces colds and flu" is the wrong sentence for an odds
    ratio of 0.88: 12% lower odds is a small effect, and the reader is
    owed "slightly lowers". Only the first change verb in a sentence is
    hedged, and only when the sentence does not hedge itself already.
    """
    hedge = HEDGE.get(label or "", "")
    if not hedge or not text:
        return text or ""
    out = []
    for sentence in sentences(text):
        if not _ALREADY_HEDGED.search(sentence):
            sentence = _CHANGE_VERB.sub(
                lambda m: hedge + _weaker(m.group(1)), sentence, count=1)
        out.append(sentence)
    return " ".join(out)


# A hedged verb reads better in its plainest form: "slightly cuts" is
# clumsy where "slightly lowers" is not, and the two say the same thing.
_PLAINER_VERB = {"cuts": "lowers", "cut": "lower", "reduces": "lowers",
                 "reduce": "lower", "boosts": "raises", "boost": "raise",
                 "protects": "helps", "protect": "help"}


def _weaker(verb: str) -> str:
    plainer = _PLAINER_VERB.get(verb.lower(), verb.lower())
    return plainer.capitalize() if verb[:1].isupper() else plainer


# ---------------------------------------------------------------------
# Rule 6: the four strings must not contradict each other.
#
# The takeaway, the explanation, the still-open line and the breakdown are
# written in one call but are four separate pieces of prose, and a model
# will happily say a condition is untested in one and report the test in
# another. A reader who notices that cannot trust either.
#
# Each row is two patterns that cannot both be true of the same check.
# `contradictions` returns the ones it finds, in words a prompt can use,
# and verdict.py spends one more call to ask for a consistent answer. It
# fixes nothing itself: a contradiction means the answer is wrong, and
# rewriting half of it in code would just hide which half.
# ---------------------------------------------------------------------

UNTESTED = (r"\b(?:not|never|n't|no study|none of these|nothing here)\b[^.]{0,40}"
            r"\b(?:test(?:ed|s)?|measur(?:ed|es)|address(?:ed|es)?|examin(?:ed|es)|"
            r"isolat(?:ed|es)|report(?:ed|s)?)\b")

CONTRADICTIONS = [
    (UNTESTED, r"\b(?:stronger|larger|greater|weaker|smaller|no clear effect|"
               r"no significant effect|only in|varied|depend(?:ed|s)?)\b[^.]{0,60}"
               r"\b(?:baseline|low levels|subgroup|those with|deficien)\b",
     "one line says the claim's condition was not tested and another reports "
     "what the studies found about it"),
    (r"\bno (?:clear |consistent )?(?:effect|benefit|link|association)\b",
     r"\b(?:cuts?|lowers?|reduces?|raises?|increases?) (?:the )?(?:risk|odds|rate)\b",
     "one line says there is no effect and another says there is one"),
    (r"\bonly (?:if|in|for|when)\b",
     r"\b(?:even|also) (?:for|in|among) (?:those|people) (?:who are )?not\b",
     "one line says the effect happens only under a condition and another "
     "says it happens without it"),
    # "the studies found" is also how English says "the studies we found",
    # so this half only counts when a finding follows the verb.
    (r"\bunproven\b|\bno published stud\w+\b",
     r"\b(?:trials?|studies|reviews?) (?:show(?:ed)?|found|report(?:ed)?)\s+"
     r"(?:a |an |that |some |significant|clear|benefit|lower|higher|reduc|improv)",
     "one line says the claim is unstudied and another cites what studies found"),
]

_CONTRADICTIONS = [(re.compile(a, re.IGNORECASE), re.compile(b, re.IGNORECASE), why)
                   for a, b, why in CONTRADICTIONS]


def contradictions(*texts, reported: list[str] | None = None) -> list[str]:
    """
    What this answer says twice, both ways. Empty when it is consistent.

    `reported` is the list of the claim's conditions that the abstracts do
    report on, from `evidence.conditions_reported`. Calling one of those
    untested is a contradiction with the record rather than with another
    sentence, and it is the one this whole check exists for.
    """
    blob = " ".join(t for t in texts if t)
    found = []
    for first, second, why in _CONTRADICTIONS:
        if first.search(blob) and second.search(blob) and why not in found:
            found.append(why)
    if reported and re.search(UNTESTED, blob, re.IGNORECASE):
        found.append(
            "the answer calls something untested, but these abstracts do report "
            f"on {', '.join(reported)}")
    return found


# ---------------------------------------------------------------------
# Rule 8: the stamp and the words agree.
#
# The audit's most visible failure: "It's complicated" stamped over a
# takeaway that says "controlled tests show sugar does not make children
# hyperactive". Either half can be the wrong one, and code cannot tell
# which, so this only reads what the words commit to and verdict.py asks
# again when the stamp says something else.
#
# A takeaway often holds two clauses. The one that carries its answer is
# the one after "but" (or before "though"), and it only counts as a plain
# yes or no when it is about the claim's own words and carries no hedge.
# "Some reviews link eggs to risk, but other studies find no link" answers
# nothing plainly: its "no" is about a link the sentence itself disputes.
# ---------------------------------------------------------------------

STANCES = ("yes", "no", "untested", "mixed")

# Words that make a clause a qualified answer rather than a plain one.
_HEDGED = re.compile(
    r"\b(?:may|might|could|can|some|slightly|a little|a bit|somewhat|mixed|"
    r"vary|varies|varied|unclear|not clear|uncertain|depends?|partly|limited|"
    r"weak|small|modest|possibly|probably|unknown|inconsistent|only|unless|"
    r"low.certainty|lean|leans|suggests?|appears?|seems?|if you|in people "
    r"(?:who|with)|for people (?:who|with)|for some|in some|not clearly|"
    r"(?:for|in) most)\b", re.IGNORECASE)

# The claim was never put to a test. Checked before the plain "no", because
# "no study tests this" is a negative sentence that is not a "no".
_UNTESTED_SAID = re.compile(
    r"\b(?:no (?:study|studies|trials?|good evidence|evidence|published|human)|"
    r"nothing (?:here |found )?(?:tests?|shows?|measures?)|not (?:been )?(?:tested|"
    r"studied)|untested|unproven|(?:do|does|did)(?:n't| not) (?:actually )?test)\b",
    re.IGNORECASE)

_NEGATED = re.compile(
    r"\b(?:does not|do not|did not|doesn't|don't|didn't|is not|isn't|are not|"
    r"aren't|was not|were not|cannot|can't|won't|will not|should not|shouldn't|"
    r"must not|mustn't|need not|never|no (?:more|"
    r"better)|not (?:better|more effective|any better)|(?:about|just|roughly) as "
    r"(?:well|good|effective) as|no (?:\w+ ){0,2}(?:benefits?|effect|link|"
    r"difference|advantage|change)|myth)\b", re.IGNORECASE)

# A claim that compares two things is answered by the clause that compares.
_COMPARATIVE_CLAIM = re.compile(
    r"\b(?:better|worse|more|less|faster|slower|healthier|stronger|superior|"
    r"inferior)\b[^.]{0,60}\bthan\b", re.IGNORECASE)
_COMPARES_SAME = re.compile(
    r"\b(?:about|just|roughly|nearly|much) as (?:well|good|effective|much) "
    r"(?:(?:for|at|in) [\w -]{1,30}? )?as\b|"
    r"\bno (?:better|worse|more|less)\b|(?:\bnot|n't) (?:any )?(?:better|worse|more|"
    r"less|healthier|safer)\b|\b(?:similar|same|equal(?:ly)?|comparable|no difference)\b",
    re.IGNORECASE)
_CLAUSE_EDGE = re.compile(r",|;|\b(?:but|yet|however|though|although|while|whereas)\b",
                          re.IGNORECASE)
_COMPARES_MORE = re.compile(r"\b(?:better|more|faster|superior|outperform\w*)\b"
                            r"[^.]{0,40}\bthan\b", re.IGNORECASE)

# Where a sentence turns. After "but" the answer follows the turn; after
# "though" the answer came before it.
_TURN_AFTER = re.compile(r",?\s+\b(?:but|yet|however)\b,?\s+", re.IGNORECASE)
_TURN_BEFORE = re.compile(r",?\s+\b(?:though|although|even though|while|whereas)\b\s+|"
                          r",\s+(?:as|since|because)\s+|\s+because\s+", re.IGNORECASE)
# Where a sentence concludes. "Needs vary by person, so eight glasses is not
# a hard rule": the answer is the conclusion, not the reason given for it.
_CONCLUDES = re.compile(r",\s+(?:so|which means|meaning)\s+(?!far\b|much\b|many\b|long\b)",
                        re.IGNORECASE)
# A second clause starts at ", and", ", so", ";", or a bare "and" that
# opens a new verb ("don't remove toxins and can hurt your liver"), whose
# hedge belongs to it and not to the answer before it.
_AND_CLAUSE = re.compile(r",\s+(?:and|so)\s+|;\s+|\s+and\s+(?=(?:can|could|may|might|"
                         r"will|may|is|are|was|were|has|have|cause|causes)\b)", re.IGNORECASE)

# Words in a "but" clause that weigh on the answer itself: how big, how sure,
# for whom. Anything else after the turn is a side note.
_QUALIFIES = re.compile(
    r"\b(?:effects?|evidence|results?|stud(?:y|ies)|trials?|benefits?|certainty|"
    r"data|findings|proof|research|small|modest|limited|unclear|not clear|mixed|"
    r"inconsistent|var(?:y|ies|ied)|only|some|few|weak|uncertain|unknown|"
    r"not (?:all|everyone|always|proven)|people (?:who|with)|depends?|lifestyle|"
    r"confound\w*|other factors|explain(?:s|ed)?|account(?:s|ed)? for)\b",
    re.IGNORECASE)

# A takeaway that says the link is likely the people rather than the thing:
# "but lifestyle and other factors likely explain much of that link".
_OTHER_FACTORS = re.compile(r"\b(?:lifestyle|other (?:factors|habits|differences)|"
                            r"confound\w*|healthier habits|socioeconomic|wealth|income)\b",
                            re.IGNORECASE)
_EXPLAINS = re.compile(r"\b(?:explain\w*|account(?:s|ed)? for|due to|because of|behind|"
                       r"driv(?:e|es|ing|en)|caused by|rather than|not the)\b", re.IGNORECASE)


def says_confounded(text: str) -> bool:
    """True when a sentence of `text` puts the link down to other factors."""
    return any(_OTHER_FACTORS.search(t) and _EXPLAINS.search(t) for t in sentences(text or ""))


def _stem(word: str) -> str:
    w = word.lower()
    for end in ("ing", "ed", "es", "s"):
        if len(w) > len(end) + 2 and w.endswith(end):
            return w[:-len(end)]
    return w


# A number in a claim is one of its words, whichever way it is written:
# "eight glasses is not a hard rule" is about "8 glasses of water".
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}


def _words_of(text: str) -> set:
    """Stemmed words and numbers, with a hyphenated word counted as its parts."""
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'-]*|\d+", re.sub(r"(?<=\d),(?=\d{3})", "", text or "")):
        for part in [w] + (w.split("-") if "-" in w else []):
            if part:
                out.add(_NUMBER_WORDS.get(part.lower(), _stem(part)))
    return out


def _echoes(clause: str, claim: str) -> bool:
    """True when the clause is about the claim's own words, not a neighbour's."""
    terms = ({_stem(t) for t in claim_terms(claim)}
             | set(re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d{3})", "", claim or ""))))
    if not terms:
        return False
    words = _words_of(clause)
    hits = {t for t in terms
            if any(w == t or (len(w) >= 5 and len(t) >= 5 and w[:5] == t[:5])
                   for w in words)}
    return len(hits) >= min(2, len(terms))


# "It is not clear they boost immunity", "whether it prevents colds is
# unknown": the claim itself is called unsettled, which is the answer "this
# has not been shown", not a hedge on a finding. Only when what is unsettled
# is the claim: "it is not clear the effect matters" stays a hedge.
_UNSETTLED = re.compile(r"\b(?:not (?:yet )?clear|unclear|not (?:yet )?known|unknown|"
                        r"uncertain|unproven)\b(?:\s+(?:if|whether|that))?", re.IGNORECASE)
_WHETHER_UNSETTLED = re.compile(r"^\s*whether\b.*\b(?:is|remains)\s+(?:still\s+)?"
                                r"(?:not clear|unclear|unknown|uncertain|unproven)\b",
                                re.IGNORECASE)


def _polarity(clause: str, claim: str = "") -> str:
    if _UNTESTED_SAID.search(clause):
        return "untested"
    if claim and _WHETHER_UNSETTLED.search(clause) and _echoes(clause, claim):
        return "untested"
    unsettled = _UNSETTLED.search(clause)
    if claim and unsettled and _echoes(clause[unsettled.end():], claim):
        return "untested"
    if _HEDGED.search(clause):
        return "mixed"
    return "no" if _NEGATED.search(clause) else "yes"


# Words that open a clause about someone or something rather than the
# claim's own assertion: "people who don't sleep gain weight" asserts that
# they gain weight, and its "don't" is not the claim saying no.
_SUBORDINATE = frozenset("who whom whose that which if when whenever because "
                         "unless until while".split())


def claim_denies(claim: str) -> bool:
    """True when the claim itself is a "no", like "smoking does not cause
    lung cancer". A plain "smoking causes lung cancer" answers it with a
    no, so a takeaway's yes and no swap places."""
    for m in _NEGATED.finditer(claim or ""):
        head = [w.lower().strip(",'\"") for w in claim[:m.start()].split()[-3:]]
        if not _SUBORDINATE.intersection(head):
            return True
    return False


# A claim that promises the whole thing: a cure, an end, a reversal,
# complete prevention. It is tested as written, so studies showing a partial
# effect answer it with a no: "vinegar lowers blood sugar a little" is real,
# and it is not "vinegar cures diabetes". Plain "prevents" is not on the list:
# "vitamin D prevents winter infections" is a claim about risk.
_ABSOLUTE = re.compile(
    r"\b(?:cures?|cured|curing|eliminat(?:e|es|ed|ing)|eradicat(?:e|es|ed|ing)|"
    r"revers(?:e|es|ed|ing)|get(?:s|ting)? rid of|wipes? out|"
    r"(?:completely|fully|totally|entirely|always|permanently)\s+(?:prevents?|stops?|"
    r"protects?|blocks?|heals?|fix(?:es)?)|"
    r"(?:prevents?|stops?|blocks?|heals?|fix(?:es)?)\b[^.,;]{0,40}?\b(?:completely|"
    r"entirely|totally|for good|forever|permanently))\b", re.IGNORECASE)
# "Cured meats", "reverse dieting", "reverse osmosis": the word is a name.
_NOT_ABSOLUTE_AFTER = re.compile(r"\s+(?:meats?|ham|bacon|salmon|fish|pork|sausages?|"
                                 r"foods?|dieting|diets?|osmosis)\b", re.IGNORECASE)
# A takeaway that says the whole thing does not happen: "but it does not
# cure it", "is not a cure", "rather than a cure", "won't prevent it
# completely". A few words may come between the "not" and the verb.
_ABSOLUTE_DENIED = re.compile(
    r"(?:\bnot|n't|\bnever|\bno|\bnor|\bcannot|\brather than|\bshort of|\bfar from|"
    r"\binstead of|\bwithout)\b(?:\s+[\w'-]+){0,4}?\s+(?:cur(?:e|es|ed|ing)\b|"
    r"eliminat\w*|eradicat\w*|revers\w*|rid of\b|wipes? out\b|prevent\w*|stops?\b|"
    r"blocks?\b|protects?\b|heals?\b|fix(?:es)?\b)", re.IGNORECASE)


def absolute_claim(claim: str) -> str:
    """
    The words that make a claim absolute ("cure", "reverses", "completely
    prevents"), or "" for any other claim. A claim that is itself a "no"
    ("vinegar does not cure diabetes") is answered the ordinary way, and a
    word that opens the claim is its subject ("Eliminating sugar clears
    skin"), not its promise.
    """
    text = claim or ""
    if claim_denies(text):
        return ""
    for m in _ABSOLUTE.finditer(text):
        if not text[:m.start()].strip() or _NOT_ABSOLUTE_AFTER.match(text[m.end():]):
            continue
        return " ".join(m.group(0).split()).lower()
    return ""


def denies_absolute(text: str, claim: str) -> bool:
    """True when the first sentence of `text` says the whole thing an
    absolute claim promises does not happen."""
    if not absolute_claim(claim):
        return False
    first = (sentences(text or "") or [""])[0]
    return bool(_ABSOLUTE_DENIED.search(first))


# A sentence that tells people to keep a treatment going: "should keep taking",
# "continue", "stay on", "need insulin", "must not stop", "cannot replace it".
# Its words overlap a claim to stop or replace that treatment, but it answers
# that claim with a no. "safe to stop", "no need to" and "needs to be
# stopped" are not in it.
_KEEPS_TREATMENT = re.compile(
    r"\b(?:keep(?:\s+on)?\s+(?:taking|using|up|it|them)|continu\w*|stay(?:ing)?\s+on|"
    r"remain(?:ing)?\s+on|should\s+(?:take|use))\b|"
    r"(?<!not )(?<!n't )(?<!no )(?<!longer )\bneeds?\b"
    r"(?!\s+to\s+be\s+(?:stopped|paused|held|withheld|discontinued|avoided|switched|changed))|"
    r"\b(?:(?:should|must|do|does|can|could|will)\s*(?:not|n't|never)|don't|never|cannot|can't|"
    r"shouldn't|mustn't)\s+(?:safely\s+)?(?:stop|quit|skip|replace|come\s+off|go\s+off|give\s+up|"
    r"ditch|swap|substitute)\b|"
    r"\bnot\s+(?:safe|advisable|recommended|wise)\s+to\s+(?:stop|quit|skip|replace|come\s+off|"
    r"go\s+off|give\s+up|ditch|swap)\b", re.IGNORECASE)


# What guidance says about stopping a treatment, for a claim to stop one.
# It says stopping is fine only in words (_STOP_FINE): can stop, may be
# stopped, safe to stop, no need to keep taking, it is not advised or not
# recommended, people are told to come off it, it should be stopped,
# paused or switched, it is usually paused, it has been withdrawn. A
# caution (_STOP_CAUTION, _KEEPS_TREATMENT) never counts as that: not to
# stop, to keep taking, to ask before stopping, what stopping risks. One
# kind alone is a no or a yes; both together are mixed.
_STOP_VERB = r"(?:stop|quit|skip|discontinue|come\s+off|go\s+off|give\s+up|withdraw)"
_NOT_BEFORE = r"(?<!not )(?<!n't )(?<!never )(?<!cannot )"
_STOP_FINE = re.compile(
    r"\b" + _NOT_BEFORE + r"(?:can|may|could)\s+(?:safely\s+|usually\s+|then\s+)?(?:be\s+)?"
    r"(?:stop(?:ped)?|quit|discontinued?|withdrawn|come\s+off|go\s+off)\b|"
    r"\b" + _NOT_BEFORE + r"(?:safe|fine|okay|ok|reasonable)\s+to\s+" + _STOP_VERB + r"\b|"
    r"\bno\s+(?:need|reason)\s+to\s+(?:continue|keep\s+(?:taking|using|on))\b|"
    r"\b(?:is|are)\s+(?:usually\s+|generally\s+|often\s+)?not\s+(?:advised|recommended|used|given|prescribed)\b|"
    r"\b(?:told|advised|recommended)\s+to\s+(?:stop|come\s+off|go\s+off)\b|"
    r"\b(?:should|must|(?<!no )needs?\s+to)\s+(?:be\s+(?:stopped|paused|held|withheld|switched|changed|"
    r"avoided|withdrawn)|stop|come\s+off|avoid|switch|move\s+to)\b|"
    r"\b(?:is|are)\s+(?:usually\s+|often\s+|generally\s+|commonly\s+|sometimes\s+)?"
    r"(?:paused|stopped|held|withheld|switched)\b|\bhas\s+been\s+withdrawn\b|"
    r"\bsome\s+(?:people\s+)?(?:stop|switch)\b|"
    r"(?:^|:\s*)(?:stop|quit|come\s+off)\s+(?:taking|using)?", re.IGNORECASE)
_STOP_CAUTION = re.compile(
    r"\b(?:talk|speak|check|consult|ask)\w*\s+(?:\w+\s+){0,5}?before\s+(?:stopping|quitting|skipping|"
    r"coming\s+off|going\s+off|giving\s+up)\b|"
    r"\bwithout\s+(?:first\s+)?(?:talking|speaking|checking|asking)\b|"
    r"\b(?:should|must|can)\s*(?:not|n't)\s+be\s+(?:stopped|discontinued|withdrawn)\b|"
    r"\bno\s+need\s+to\s+" + _STOP_VERB + r"\b|"
    r"\b(?:risks?|dangers?|harms?)\s+of\s+(?:stopping|coming\s+off|quitting)\b|"
    r"\b(?:stopping|coming\s+off|quitting|withdrawing)\b[^.;]{0,60}?\b(?:can|may|might|could|will|often|"
    r"usually)\s+(?:\w+\s+){0,2}?(?:raise|increase|cause|lead|bring|make|trigger|worsen|return)\w*\b|"
    r"\b(?:rise|rises|return|returns|come\s+back|comes\s+back|worsen|worsens|flare)\w*\s+(?:again\s+)?"
    r"(?:when|if|after|once)\s+(?:\w+\s+){0,3}?(?:stopped|stopping)\b", re.IGNORECASE)
_STOP_WORD_NEAR = re.compile(r"\b(?:stop\w*|quit\w*|skip\w*|discontinu\w*|coming\s+off|going\s+off|"
                             r"giving\s+up|withdraw\w*)\b(?:\W+\w+){0,4}\W*$", re.IGNORECASE)


def _stopping_guidance(text: str, claim: str) -> str | None:
    """For a claim to stop a treatment: "no", "yes" or "mixed" when the
    first sentence of `text` says whether stopping is fine (see above),
    otherwise None and the sentence is read as it plainly stands."""
    import verdict  # late: verdict imports this module
    if verdict.forgoes_treatment(claim) != "forgo":
        return None
    first = (sentences(text or "") or [""])[0]

    def blank(text, m):
        return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]

    fine = caution = False
    masked = first
    for m in _STOP_CAUTION.finditer(first):  # first, so "rises when it is stopped" stays a caution
        caution = True
        masked = blank(masked, m)
    for m in _STOP_FINE.finditer(masked):
        if "not" in m.group(0).lower().split() and _STOP_WORD_NEAR.search(masked[:m.start()]):
            caution = True  # "stopping it is not advised" is about stopping
        else:
            fine = True
        masked = blank(masked, m)
    if _KEEPS_TREATMENT.search(masked):
        caution = True
    if fine and caution:
        return "mixed"
    return "no" if caution else ("yes" if fine else None)


def _keeps_treatment(text: str, claim: str) -> bool:
    """True when `claim` says to stop, skip or replace a treatment or medicine
    and the first sentence of `text` says to keep it up."""
    import verdict  # late: verdict imports this module
    if verdict.forgoes_treatment(claim) != "forgo":
        return False
    first = (sentences(text or "") or [""])[0]
    return bool(_KEEPS_TREATMENT.search(first))


def stance(text: str, claim: str) -> str:
    """
    What a takeaway commits to about the claim: "yes", "no", "untested" or
    "mixed". Only the first sentence is read: the takeaway is one sentence,
    and an explanation's first sentence is where it states its answer.
    """
    guided = _stopping_guidance(text, claim)
    if guided:
        return guided
    said = _plain_stance(text, claim)
    if said in ("yes", "no") and claim_denies(claim):
        return "no" if said == "yes" else "yes"
    return said


def _plain_stance(text: str, claim: str) -> str:
    """What the takeaway says on its own, before a claim that says no
    turns it round."""
    first = (sentences(text or "") or [""])[0].strip().rstrip(".")
    if not first:
        return "mixed"
    if _UNTESTED_SAID.search(first) and not _TURN_AFTER.search(first):
        return "untested"
    # "It lowers blood sugar a little, but it does not cure it" is a plain
    # no to "it cures diabetes", however hedged its first half is.
    if denies_absolute(first, claim):
        return "no"
    if _COMPARATIVE_CLAIM.search(claim or ""):
        same = _COMPARES_SAME.search(first)
        if same:
            # Judge the hedge in the clause that compares, not in a trailing
            # one: "about as well as dieting, though some methods show equal
            # results" is still a plain "no better".
            start = max([0] + [m.end() for m in _CLAUSE_EDGE.finditer(first, 0, same.start())])
            end = next((m.start() for m in _CLAUSE_EDGE.finditer(first, same.end())), len(first))
            return "mixed" if _HEDGED.search(first[start:end]) else "no"
        if _COMPARES_MORE.search(first):
            return "mixed" if _HEDGED.search(first) else "yes"
        return "mixed"
    parts = _TURN_AFTER.split(first, maxsplit=1)
    if len(parts) == 2:
        answer = parts[1]
        # "Smoking causes lung cancer, but cutting down lowers the risk": the
        # turn is a side note about something else, so the answer is the
        # plain one before it. A turn that qualifies the answer ("but the
        # effect is small") still makes it mixed.
        if (not _echoes(answer, claim) and _echoes(parts[0], claim)
                and not _QUALIFIES.search(answer)):
            answer = parts[0]
    else:
        parts = _TURN_BEFORE.split(first, maxsplit=1)
        answer = parts[0] if len(parts) == 2 else None
    if answer is not None:
        return _polarity(answer, claim) if _echoes(answer, claim) else "mixed"
    concluded = _CONCLUDES.split(first, maxsplit=1)
    if len(concluded) == 2 and _echoes(concluded[1], claim):
        return _polarity(concluded[1], claim)
    for clause in _AND_CLAUSE.split(first):
        if _echoes(clause, claim):
            return _polarity(clause, claim)
    return "mixed"


# What an explanation says outright about the claim as a whole. Narrow on
# purpose: an explanation is full of findings about single studies, and only
# a sentence that rules on the claim itself can contradict a stamp.
_RULES_NO = re.compile(r"\b(?:contradicts?|refutes?) (?:the|this) claim\b|"
                       r"\bthe claim is (?:false|untrue|not true|a myth|wrong)\b",
                       re.IGNORECASE)
_RULES_YES = re.compile(r"\bthe claim is (?:true|correct|accurate|well supported)\b|"
                        r"\b(?<!not )(?<!n't )(?:clearly |strongly )?supports? the claim\b",
                        re.IGNORECASE)

# Which stances each stamp can stand beside. A plain answer pins one stamp:
# a plain yes is "true", a plain no is "false", and "nothing here tests it"
# is "insufficient". "complicated" says the studies point both ways, so any
# plain answer under it is the contradiction, an untested one included. A
# hedged answer ("may", "a little") commits to no stamp of its own and can
# sit beside any of them.
_AGREES = {
    "true": {"yes", "mixed"},
    "false": {"no", "mixed"},
    "complicated": {"mixed"},
    "insufficient": {"untested", "mixed"},
}

# The stamp each plain answer commits to.
STAMP_FOR = {"yes": "true", "no": "false", "untested": "insufficient"}

_SAYS = {"yes": "a plain yes", "no": "a plain no",
         "untested": "that nothing here tests the claim"}


def words_stamp(verdict: str, tldr: str, claim: str) -> str | None:
    """
    The stamp the takeaway commits to, when that is not `verdict`; None when
    the two agree or the takeaway is hedged and commits to none.
    """
    said = stance(tldr, claim)
    if said in _AGREES.get(verdict, set()):
        return None
    return STAMP_FOR.get(said)


def stamp_conflict(verdict: str, tldr: str, explanation: str, claim: str) -> str | None:
    """
    Why the stamp and the words disagree, in words a retry prompt can use,
    or None when they agree.
    """
    said = stance(tldr, claim)
    where = "the takeaway"
    if said in _AGREES.get(verdict, set()):
        said, where = None, ""
        exp = explanation or ""
        if _RULES_NO.search(exp):
            said, where = "no", "the explanation"
        elif _RULES_YES.search(exp):
            said, where = "yes", "the explanation"
        if said in _AGREES.get(verdict, set()):
            said = None
    if not said:
        return None
    return stamp_note(verdict, tldr, said, claim, where)


def stamp_note(verdict: str, tldr: str, said: str, claim: str,
               where: str = "the takeaway") -> str:
    """What a retry is told when the stamp and `said`, the plain answer the
    takeaway (or the explanation) gives, disagree."""
    turned = ("; the claim itself says no, so a takeaway agreeing that it does not "
              "happen is a yes to it, and the verdict for that is \"true\""
              if said in ("yes", "no") and claim_denies(claim) else "")
    # Both halves are shown, so the retry is answering this answer's own
    # words rather than a description of them.
    if where == "the takeaway":
        where = f'the takeaway, "{" ".join((tldr or "").split())}",'
    return (f"the verdict is \"{verdict}\" but {where} says {_SAYS[said]}. The verdict "
            f"and the takeaway must agree. If the studies really show {_SAYS[said]}, "
            f"the verdict must be \"{STAMP_FOR[said]}\"; if they do not, the takeaway "
            f"must say no more than \"{verdict}\" does{turned}")


# Words that make an answer smaller without turning it round.
_SIZE_HEDGE = re.compile(
    r"\b(?:may|might|could|slightly|a little|a bit|somewhat|marginally|modest(?:ly)?|"
    r"small|some|probably|possibly|likely|(?:very[- ])?low[- ]certainty|"
    r"certainty (?:of (?:the )?evidence )?(?:is|was) (?:very )?low)\b\s*", re.IGNORECASE)


def lost_hedge(before: str, after: str, claim: str) -> bool:
    """
    A rewrite that gives the same answer with the hedge taken out says more
    than the sentence it replaced. "Slightly lowers" is the size of the
    effect, put there on purpose by match_effect(); "lowers" is a bigger
    claim, and so is a line that loses "low certainty", "small" or "some".
    A rewrite that turns the answer round ("may lower" to "does not lower")
    is a correction, not this.
    """
    hedged = lambda t: bool(_SIZE_HEDGE.search(t or "") or _HEDGED.search(t or ""))
    if not hedged(before) or hedged(after):
        return False
    return stance(_bare(before), claim) == stance(after, claim)


def _bare(text: str) -> str:
    """The answer with its hedges set aside, and any "not" in them kept:
    "does not clearly lower" is still a no."""
    out = _HEDGED.sub(lambda m: " not " if m.group().lower().startswith("not") else " ",
                      _SIZE_HEDGE.sub("", text or ""))
    return re.sub(r"\s{2,}", " ", out)


def kept_answer(before: str, after: str, claim: str) -> bool:
    """
    A rewrite of a short answer that said a plain yes or no still says it,
    however much it hedges. "Lowers it a lot" to "lowers it a little" keeps
    the answer; "does not" to "some trials find no effect while others show
    a link" turns a verdict that followed the trials back into a tie.
    """
    was = stance(_bare(before), claim)
    return was not in ("yes", "no") or stance(_bare(after), claim) == was


# ---------------------------------------------------------------------
# Rule 7: the answer is about the claim that was typed.
#
# A search for screen light and eye damage returns trials of blue-light
# filtering spectacles, and an answer built from those tells a reader
# whether the glasses work rather than whether screens harm eyes. Both are
# real questions; only one of them was asked.
# ---------------------------------------------------------------------

# Nouns that name a product rather than the thing a claim is usually about.
# Introducing one the claim never mentioned is the signature of answering a
# neighbouring question.
PRODUCT_NOUNS = (r"glasses|spectacles|lenses|lens|goggles|filters?|screen "
                 r"protectors?|lotions?|shampoos?|creams?|serums?|gels?|"
                 r"ointments?|sprays?|patches|implants?")

_PRODUCT = re.compile(rf"\b({PRODUCT_NOUNS})\b", re.IGNORECASE)


def off_claim(tldr: str, claim: str) -> list[str]:
    """
    Product nouns the answer introduces that the claim never named.

    Mechanical and narrow on purpose: it catches the two failures actually
    seen, a blue-light claim answered about filtering glasses and a
    creatine claim answered about a hair lotion, without trying to judge
    relevance in general. The prompt carries the rest.
    """
    asked = {m.group(1).lower() for m in _PRODUCT.finditer(claim or "")}
    return sorted({m.group(1).lower() for m in _PRODUCT.finditer(tldr or "")}
                  - asked - {w.rstrip("es") for w in asked})


# Weakening a verb only weakens the sentence when the sentence asserts it.
# Under a "not" it does the opposite: "does not cure cancer" became "does
# not help with cancer", a stronger claim than the abstracts make, and one
# that contradicted "helps with some cancers" in the same takeaway. So a
# verb with a negation in the few words before it is left as written, the
# same way match_effect() leaves a sentence with "no" or "not" in it alone.
_NEGATION_BEFORE = re.compile(r"\b(?:not|never|no|none|nothing|neither|nor|cannot|without|"
                              r"fails?|failed|rather than|short of|far from|instead of)\b"
                              r"|n't\b", re.IGNORECASE)
# "Cure" the noun is not a promise. "Modest control rather than a full cure"
# came out as "rather than a full help with", because the rule meant for
# "vinegar cures diabetes" read the noun as the verb. After an article or
# an adjective, or before "for", the word is the thing, and stays.
_CURE_NOUN_BEFORE = re.compile(
    r"\b(?:a|an|the|no|any|full|complete|total|real|true|actual|miracle|magic|"
    r"permanent|definitive|outright|possible|potential)\s*$", re.IGNORECASE)


# A negation that governs what was shown, further back than four words:
# "none of these trials show that apple cider vinegar can cure diabetes"
# became "can help with diabetes", which says the opposite of the
# sentence before it. Inside "no evidence that", "did not find that" and
# their relatives, the strong verb is what was not found, and weakening it
# turns a denial into a claim.
_NEGATED_FINDING = re.compile(
    r"(?:\b(?:not|never|no|none|nothing|neither|nor|little|lack)\b|n't\b)[^.;:]*?"
    r"\b(?:show|shows|showed|shown|prove|proves|proved|proven|find|finds|found|"
    r"evidence|suggest|suggests|suggested|support|supports|supported|tested|test|tests)\b",
    re.IGNORECASE)


def _soften_unless_negated(weaker):
    def swap(m: re.Match) -> str:
        head = re.split(r"[.!?;:]", m.string[:m.start()])[-1]
        clause = re.split(r",\s+(?:and|but|so|while|yet)\s+", head)[-1]
        if (_NEGATION_BEFORE.search(" ".join(head.split()[-4:]))
                or _NEGATED_FINDING.search(clause)):
            return m.group(0)
        if (m.group(0).lower().startswith("cure")
                and (_CURE_NOUN_BEFORE.search(head)
                     or re.match(r"\s+for\b", m.string[m.end():]))):
            return m.group(0)
        return m.expand(weaker)
    return swap


def soften(text: str) -> str:
    """Rewrite certainty the evidence here can never support."""
    t = text or ""
    for pattern, weaker in _SOFTEN:
        t = pattern.sub(_soften_unless_negated(weaker), t)
    # A substitution at the head of a sentence must not leave it lowercase.
    return heads_up(t)


# Research-paper grammar that nobody says out loud. Each pair fixes the
# verb and leaves the finding alone: "links to" and "is linked to" are the
# same strength of claim, so this can no more raise certainty than soften()
# can. It exists because the takeaway is the one line meant to be repeated
# to another person, and "Vitamin D links to colds" is not a sentence
# anyone would say.
NATURAL = [
    (r"\bassociates with\b", "is linked to"),
    (r"\bassociate with\b", "are linked to"),
    (r"\bis associated with\b", "is linked to"),
    (r"\bare associated with\b", "are linked to"),
    (r"\bcorrelates with\b", "goes together with"),
    (r"\bshows? an? association with\b", "is linked to"),
    (r"\bdemonstrates? efficacy\b", "works"),
    (r"\bconfers? benefit\b", "helps"),
    (r"\bmay be of benefit\b", "may help"),
]

_NATURAL = [(re.compile(p), r) for p, r in NATURAL]

# "link" is a thing as often as something done. "Vitamin D links to colds"
# wants its verb fixed; "a small link to weight loss" and "has links to"
# are already English, and swapping the verb in there left "a small are
# linked to". After one of these words, "link" is the noun and stays.
_LINK = re.compile(r"\b([Ll]inks?) to\b")
_LINK_NOUN_AFTER = frozenset("""a an the no any some this that its their one
    small weak strong clear possible modest direct causal slight consistent
    plausible real significant definite firm limited tenuous potential likely
    known proven little inverse positive negative statistical close mixed
    conflicting inconsistent unclear uncertain contradictory variable
    has have had with of found find finds show shows showed suggest suggests
    suggested report reports reported""".split())


def _link_as_verb(m: re.Match) -> str:
    before = re.findall(r"[A-Za-z']+", m.string[:m.start()])
    # One word back or two: "shows mixed links to" has its verb already.
    if any(w.lower() in _LINK_NOUN_AFTER for w in before[-2:]):
        return m.group(0)
    return "is linked to" if m.group(1).lower() == "links" else "are linked to"


def natural(text: str) -> str:
    """
    Make the takeaway sound like a person saying it.

    "Vitamin D links to colds and flu" is a headline, not a sentence, and
    the takeaway is the line a reader is meant to be able to repeat. Only
    the verb is touched; nothing here changes what is being claimed or how
    strongly, so it composes safely with soften().
    """
    t = _LINK.sub(_link_as_verb, text or "")
    for pattern, fixed in _NATURAL:
        t = pattern.sub(fixed, t)
    # "Vitamin D is linked to" needs its capital back if it opened the line.
    return heads_up(t)


# "a" before a vowel sound, "an" before a consonant one. Swapping a word
# changes the sound after the article, so the article has to follow it.
# "u" is left alone in both directions: "a unique dose" and "an unusual
# one" are both right, and no rule above produces either.
def _fix_articles(text: str) -> str:
    text = re.sub(r"\b([Aa])n (?=[bcdfgjklmnpqrstvwxyz])", r"\1 ", text)
    return re.sub(r"\b([Aa]) (?=[aeio])", r"\1n ", text)


def _recase(original: str, replacement: str) -> str:
    """Keep the capital a sentence opening needs, drop the rest."""
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _sentence_around(m) -> str:
    """The sentence a match sits in, without the match itself."""
    text = m.string
    start = max(text.rfind(". ", 0, m.start()), -1) + 1
    end = text.find(". ", m.end())
    end = len(text) if end < 0 else end
    return text[start:m.start()] + " " + text[m.end():end]


# The same phrase twice across an "and" or "or", which the swaps above can
# produce from two different words ("memory and overall thinking and memory").
_REPEATED = re.compile(r"\b((?:\w+\s+){1,3}\w+)(\s+(?:and|or)\s+(?:overall\s+|general\s+)?)"
                       r"\1(?=\s*(?:[.,;:)]|$))", re.IGNORECASE)


def _no_repeats(text: str) -> str:
    return _REPEATED.sub(lambda m: m.group(1), text)


# Text between straight or curly double quotation marks: a quoted title.
_QUOTED = re.compile(r'"[^"\n]{1,300}"|\u201c[^\u201d\n]{1,300}\u201d')


def plain(text: str, kept: set | None = None) -> str:
    """
    Swap medical vocabulary for the everyday words that mean the same.

    Runs before the glossary and before the reading grade is measured, so
    a term that has plain English never reaches the point of needing an
    explanation in brackets. The few terms whose precision matters keep
    the original alongside, once per breakdown: `kept` is carried across
    fields so "chest and throat infections (respiratory tract infections)"
    is printed on first appearance and plainly thereafter.

    This only ever changes vocabulary. No sentence is added, removed or
    reordered here, and nothing it writes can make a finding sound more
    certain than the sentence already did.
    """
    kept = kept if kept is not None else set()
    out = text or ""
    # A term parked in brackets is finished text, not text still being
    # edited. Held out of the stream as a numbered token while the rest of
    # the rules run, or the next rule rewrites the very word it was put
    # there to preserve: "pattern hair loss (androgenetic hair loss)".
    parked: list[str] = []

    def park(term: str) -> str:
        parked.append(term)
        return f" (\x00{len(parked) - 1}\x00)"

    # A quoted title is the paper's own words: what sits between straight or
    # curly double quotation marks is held out, as written, until the end.
    quoted: list[str] = []

    def hold(m) -> str:
        quoted.append(m.group(0))
        return f"\x01{len(quoted) - 1}\x01"

    out = _QUOTED.sub(hold, out)
    for pattern, replacement, keep_term in _PLAIN:

        def swap(m, replacement=replacement, keep_term=keep_term):
            built = replacement(m) if callable(replacement) else m.expand(replacement)
            said = _recase(m.group(0), built)
            if keep_term and keep_term not in kept:
                kept.add(keep_term)
                return said + park(keep_term)
            return said

        out = pattern.sub(swap, out)

    out = re.sub(r"\x00(\d+)\x00", lambda m: parked[int(m.group(1))], out)
    out = _fix_articles(_no_repeats(out))
    return re.sub(r"\x01(\d+)\x01", lambda m: quoted[int(m.group(1))], out)


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
    # "meta-analysis" is not here: plain() turns it into "pooled review",
    # which says the same thing in the sentence instead of after it.
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
            # "Not statistically significant (unlikely to be chance alone)"
            # reads as the opposite of the finding. A negated term goes
            # unglossed, and stays open for a later, positive use.
            if re.search(r"\b(?:not|no|non)[\s-]+$", sentence[:m.start()]):
                continue
            # A term the model already explained in its own brackets is left alone.
            if sentence[m.end():m.end() + 2].startswith(" ("):
                done.add(key)
                continue
            # So is one already inside brackets, which is where plain()
            # parks a medical term it has just replaced with plain words.
            # Glossing it there would nest a second parenthesis inside the
            # first: "unlikely to be chance (statistically significant (...))".
            before = sentence[:m.start()]
            if before.count("(") > before.count(")"):
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


# A single capital letter and a stop: a genus abbreviation when a lowercase
# word follows ("E. coli", "S. aureus", "H. pylori"), never a sentence's end.
# "vitamin C. It ..." still splits, because a capital follows.
_INITIAL = re.compile(r"(?:^|(?<=[\s(\[\"\u201c]))[A-Z]\.$")


def _joins(before: str, after: str) -> bool:
    """Whether a split between these two pieces fell inside "E. coli"."""
    return bool(_INITIAL.search(before)) and after[:1].islower()


def sentences(text: str) -> list[str]:
    """Split prose into sentences without splitting "e.g.", "12.5%" or
    "E. coli"."""
    out, buf = [], ""
    pieces = re.split(r"(?<=[.!?])(\s+)", text or "")
    for i, piece in enumerate(pieces):
        if piece.strip() == "" and piece != "":
            buf += piece
            continue
        buf += piece
        after = pieces[i + 2] if i + 2 < len(pieces) else ""
        if buf.strip() and not _ABBREV.search(buf.strip()) and not _joins(buf.rstrip(), after):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def heads_up(text: str) -> str:
    """Each sentence of text starting with a capital. "coli" after "E." is
    not a sentence's head ("E. coli", the rule sentences() reads by)."""
    t = text or ""
    return re.sub(r"(?:^|(?<=[.!?]\s))[a-z]",
                  lambda m: m.group(0) if m.start() and _joins(t[:m.start()].rstrip(), t[m.start():])
                  else m.group(0).upper(), t)


def first_sentence(text: str) -> str:
    """The text up to its first ". ", read past "E. coli" (sentences)."""
    parts = str(text or "").split(". ")
    out = parts[0]
    for nxt in parts[1:]:
        if not _joins(out + ".", nxt):
            break
        out += ". " + nxt
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
            # A count an abstract spells out is still a figure the abstract
            # reports. Without this the gate deleted "the 45 people were
            # healthy young males" because the methods section opened with
            # "Forty-five resistance-trained males".
            bag |= evidence.written_numbers(v)
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


# Words too ordinary to be anyone's subject, so they are never excluded
# from the grade even when the claim is made of them.
_STOP = {
    "the", "and", "but", "for", "with", "your", "you", "are", "that", "this",
    "from", "have", "has", "was", "were", "will", "can", "does", "did", "not",
    "only", "more", "less", "than", "into", "out", "about", "when", "what",
    "how", "why", "who", "its", "it's", "their", "them", "they", "all", "any",
    "get", "gets", "getting", "take", "takes", "taking", "make", "makes",
    "cause", "causes", "help", "helps", "good", "bad", "better", "worse",
    "risk", "risks", "people", "every", "some", "most", "much", "many",
    "begin", "beginning", "catch", "catching", "cuts", "cut", "lower",
    "lowers", "raise", "raises", "stop", "stops", "prevent", "prevents",
    "may", "one", "two", "per", "new", "old", "use", "uses", "see", "say",
    "big", "too", "now", "yet", "off", "own", "day", "days", "week", "weeks",
    "year", "years", "than", "then", "also", "just", "both", "each", "been",
}


def claim_terms(claim: str) -> set:
    """
    The words a claim is actually about: its subject and its outcome.

    "Taking vitamin D supplements in winter cuts your risk of catching a
    cold" is about vitamin D, supplements, winter and colds. A reader who
    typed those words has already met them, and no rewrite can make them
    shorter without changing the subject, so counting their syllables
    against the prose measures the claim rather than the writing.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", claim or "")
    # Three letters, not four: "flu", "fat", "eye" and "gut" are exactly the
    # sort of word a claim is about. The stop list carries the short words
    # that are never anyone's subject.
    return {w.lower() for w in words if len(w) >= 3 and w.lower() not in _STOP}


def reading_grade(text: str, skip: set | None = None) -> float:
    """
    Flesch-Kincaid grade level. Study references are dropped first: "(Study
    3)" is a tap target, not a word the reader has to parse. `skip` drops
    the claim's own key terms, which are the subject rather than the prose.

    Sentence count is taken before the words are dropped, so removing a
    term never shortens a sentence that the reader still has to read.
    """
    clean = _REF.sub(" ", text or "")
    sents = [s for s in sentences(clean) if s.strip()]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", clean)
    if not words or not sents:
        return 0.0
    counted = [w for w in words if w.lower() not in (skip or set())]
    if not counted:
        return 0.0
    syl = sum(syllables(w) for w in counted)
    return round(0.39 * (len(words) / len(sents))
                 + 11.8 * (syl / len(counted)) - 15.59, 1)


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
        # A citation on its own is not a sentence. "(Studies 3, 8)." passes
        # every rule below it and prints a section heading with a bracket
        # under it, which reads as a bug and tells the reader nothing.
        if len(re.findall(r"\S+", _REF.sub(" ", s).strip(" ().,"))) < 4:
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


# ---------------------------------------------------------------------
# Rule 9: a null result is said as one, and a figure keeps its comparison.
#
# The audit printed "an odds ratio of 0.99. That is about 1% lower odds, a
# small effect" for a study whose interval ran from 0.92 to 1.06, which is
# no difference at all; "the net weight difference was -1.8 kg" for a trial
# whose abstract says, in the same sentence, that it was not significant;
# and "1.7 cystitis episodes compared to controls" where the controls had
# 3.2. Each of the three is copied from an abstract and passes the number
# gate. Each tells the reader something the abstract does not.
#
# All three are read back from the abstract the sentence cites, so nothing
# here can introduce a figure or a finding that is not on the page.
# ---------------------------------------------------------------------

NOT_SIGNIFICANT_SAID = "That difference was not statistically significant ({ref})."
NULL_SAID = ("no clear difference between the groups, because the result was "
             "not statistically significant")
# The same translation under an answer that there is an effect ("Likely
# true" on "sunscreen lowers skin cancer"). "No clear difference between the
# groups" under it reads as a contradiction; what the figure shows is that
# this one result could be chance.
NULL_SAID_ALONE = ("On its own, that result could be chance, because it was not "
                   "statistically significant")


def null_alone(text: str) -> str:
    """The null translation code wrote under a figure, reworded for a page
    whose answer is that there is an effect. Model prose is not touched."""
    return re.sub(re.escape(TRANSLATION_LEAD + NULL_SAID), NULL_SAID_ALONE, text or "")


# The effect's own plain words, under the same answer. The page no longer
# prints "no clear difference between the groups" there, so the response's
# effect field does not carry it either.
NULL_PLAIN_ALONE = "a result that could be chance, because it was not statistically significant"


def effect_alone(bd: dict | None) -> dict | None:
    """The breakdown with a null effect's plain words reworded as null_alone
    rewords the sentence, for a page whose answer is that there is an
    effect. Its label and figure stay."""
    effect = (bd or {}).get("effect") or {}
    if effect.get("null") and effect.get("plain") == evidence.NULL_EFFECT["plain"]:
        return {**bd, "effect": {**effect, "plain": NULL_PLAIN_ALONE}}
    return bd


# The size code appends to its translation of a figure: ", a large effect".
# A size said in a "That is ..." sentence, in every shape its writers give
# it: ground's ", a large effect" after the arithmetic, and a faithfulness
# rewrite (verdict.check_faithful swaps a line the model rewrote) that left
# only the size, "That is a large effect (Study 3)".
_SIZE_TAIL = re.compile(
    r"(?:,\s*|\s+)(?:which\s+is\s+)?(?:an?\s+)?(?:very\s+)?(?:small|slight|modest|moderate|large|big|tiny)"
    r"\s+(?:effect|benefit|reduction|increase|improvement|difference)\b", re.IGNORECASE)


def unsized(bd: dict | None) -> dict | None:
    """The breakdown with no size word code wrote, for an answer whose
    evidence line may not carry its figure's size (line_label refused it).
    The figure stays, in its own sentence naming its own outcome; the label
    is blank, and the "That is ..." sentence code wrote beside the figure
    keeps its plain arithmetic ("about 12% lower odds") without the size
    ("a small effect"), in the effect box and in "What the research says".
    One that only said no clear difference, or only the size, goes."""
    if not bd:
        return bd

    def bare(t: str) -> str:
        if not str(t).startswith(TRANSLATION_LEAD):
            return t
        if NULL_SAID in t:
            return ""
        # The size comes out only where what is left is still a whole
        # statement: the phrase ends its clause (a comma, a citation, a stop
        # or the end follows). Where words that depend on it follow ("a
        # large increase in risk"), the whole sentence goes, with its
        # citation: saying less is fine, a broken sentence is not.
        if any(not re.match(r"\s*(?:[,.;:!?(]|$)", t[m.end():]) for m in _SIZE_TAIL.finditer(t)):
            return ""
        left = _SIZE_TAIL.sub("", t)
        # Where the size was the whole statement, the sentence goes, with
        # its citation; the figure's own sentence stays.
        if not re.search(r"\w", _REF.sub(" ", left[len(TRANSLATION_LEAD):]).replace("(", "").replace(")", "")):
            return ""
        return left

    out = {**bd, "says": [x for x in (bare(t) for t in bd.get("says") or []) if x]}
    if bd.get("effect_size"):
        out["effect_size"] = " ".join(x for x in (bare(t) for t in sentences(bd["effect_size"])) if x)
    if bd.get("effect"):
        out["effect"] = {**bd["effect"], "label": ""}
    return out


# What a "That is ..." sentence code wrote can say, built from its writers:
# ground() writes TRANSLATION_LEAD + evidence.effect_size's plain words
# ("about 12% lower odds", "a difference of 0.4 standard deviations") or
# NULL_SAID, and the faithfulness rewrite keeps the lead but rewords the
# figure ("That is an effect size of 1.03", "That is, about 12% lower
# odds") or leaves only the size ("That is a large effect"). A sentence
# that merely starts "That is" ("That is because ...", "That is why ...")
# is the model's and is not one of these.
_CODE_TRANSLATION = re.compile(
    r"That is,?\s+(?:"
    + re.escape(evidence.NULL_EFFECT["plain"]) + "|" + re.escape(NULL_SAID)
    + r"|about\s+[-−]?\d[\d.,]*\s?(?:%|per ?cent)"
    + r"|a\s+difference\s+of\s+[-−]?\d"
    + r"|(?:an?\s+)?(?:very\s+)?(?:small|slight|modest|moderate|large|big|tiny)"
      r"\s+(?:effect|benefit|reduction|increase|improvement|difference)\b"
    + r"|an?\s+(?:[A-Za-z-]+\s+){0,3}?of\s+(?:about\s+)?[-−]?\d"
    + r")", re.IGNORECASE)


def code_translation(t: str) -> bool:
    """Whether t is a "That is ..." sentence code wrote beside a figure."""
    return bool(_CODE_TRANSLATION.match(str(t or "")))


# Words a restatement can add without saying anything new.
_FILLER = frozenset("a an the of is was were that".split())


def translations_follow(bd: dict | None, withheld: bool = False) -> dict | None:
    """
    The breakdown with each "That is ..." sentence code wrote shown only
    beside the figure it explains. In each block ("What the research says",
    each list item, each field), one whose sentence before it is missing or
    gives no figure goes: "That is no clear difference ... (Study 2)." under
    a sentence with no number explains nothing, and so does one under
    another study's figure: when it cites a study, the sentence before it
    has to cite that study too. One that only repeats what
    the block already says, short words aside ("That is a risk drop of 26%
    (Study 1)." after "The risk drop was 26% ... (Study 1)."), goes too,
    whether or not the size is withheld; one that converts the figure
    ("That is about 54% lower risk") or adds to it ("..., meaning a lower
    risk over time") stays. Everything else comes back as it was.
    """
    if not bd:
        return bd

    def figure(t: str) -> bool:
        return bool(_FIGURE.search(_REF.sub(" ", t)))

    def words(t: str) -> list[str]:
        return [w for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", _REF.sub(" ", t).lower())
                if w not in _FILLER]

    def cited(t: str) -> set:
        return {n for m in _REF.finditer(t) for n in re.findall(r"\d+", m.group(1))}

    def block(units: list[str]) -> list[str]:
        kept = []
        for i, t in enumerate(units):
            if code_translation(t):
                if not kept or not figure(kept[-1]):
                    continue
                if cited(t) and not cited(t) & cited(kept[-1]):
                    continue
                if figure(t):
                    said = {w for u in units[:i] + units[i + 1:] for w in words(u)}
                    if set(words(re.sub(r"^That is,?\s+", "", t))) <= said:
                        continue
            kept.append(t)
        return kept

    def field(t: str) -> str:
        parts = sentences(t or "")
        kept = block(parts)
        return t if len(kept) == len(parts) else " ".join(kept)

    out = dict(bd)
    out["says"] = block(list(bd.get("says") or []))
    if bd.get("evidence"):
        out["evidence"] = [x for x in (field(t) for t in bd["evidence"]) if x]
    for k in PROSE_FIELDS:
        if bd.get(k):
            out[k] = field(bd[k])
    return out if out != bd else bd


def answers_effect(verdict: str, claim: str) -> bool:
    """True when the answer says there is an effect: "true" on a claim of
    one, or "false" on a claim of none ("vaccines do not cause autism")."""
    return verdict in ("true", "false") and (verdict == "true") != claim_denies(claim)


_SIZE_SAID = re.compile(
    r"\b(?:an?\s+)?(?:very\s+)?(?:small|slight|modest|moderate|large|big|tiny)\s+"
    r"(?:effect|benefit|reduction|increase|improvement|difference)\b", re.IGNORECASE)
_FIGURE = re.compile(r"(?<![\w.])[-\u2212]?\d+(?:\.\d+)?(?![\w.]*\d)")
_SAID_NULL = re.compile(r"\bno clear difference\b|\bnot (?:statistically )?significant",
                        re.IGNORECASE)


def _cited_abstracts(sentence: str, studies: list[dict]) -> list[tuple[str, str]]:
    """(the citation as written, that study's abstract) for each study cited."""
    out = []
    for m in _REF.finditer(sentence):
        for n in re.findall(r"\d+", m.group(1)):
            i = int(n)
            if 0 < i <= len(studies or []):
                out.append((f"Study {i}", str(studies[i - 1].get("abstract") or "")))
    return out


# A number that is part of a name, not a result: "type 1 diabetes",
# "COVID-19", "omega-3", "a phase 3 trial", "stage 2 cancer".
_NAME_NUMBER = re.compile(r"(?:\b(?:type|phase|stage|grade|class|step|wave|tier)\s+|[A-Za-z]-)$",
                          re.IGNORECASE)


# An interval's confidence level is not a result: "95" in "(95 percent CI
# 0.71 to 0.89)" was read against the abstract's first "95% confidence
# interval", a null subgroup, and a significant risk ratio was called not
# significant.
_LEVEL_AFTER = re.compile(r"\s?(?:%|per ?cent\b)\s*(?:C\.?I\.?\b|confidence|credible)", re.IGNORECASE)


def _figures(sentence: str) -> list[str]:
    bare = _REF.sub(" ", sentence)
    return [m.group(0) for m in _FIGURE.finditer(bare)
            if not re.fullmatch(r"(?:19|20)\d\d", m.group(0).lstrip("-"))
            and not _NAME_NUMBER.search(bare[:m.start()])
            and not _LEVEL_AFTER.match(bare, m.end())]


def null_figure(sentence: str, studies: list[dict]) -> str | None:
    """
    The citation behind a figure in this sentence that its own abstract
    reports as no clear difference, or None. The sentence is read first, in
    case the model kept the interval; then the abstract it cites.
    """
    for fig in _figures(sentence):
        if evidence.significance(sentence, fig) == "null":
            ref = _REF.search(sentence)
            return ref.group(0) if ref else ""
        for ref, abstract in _cited_abstracts(sentence, studies):
            if evidence.significance(abstract, fig) == "null":
                return ref
    return None


# A sentence that reports a finding as none: "no drop in viral titer",
# "did not differ", "was not statistically significant".
_NULL_FINDING = re.compile(
    r"\bno (?:clear |significant |real )?(?:drop|fall|change|difference|effect|benefit|reduction|rise"
    r"|increase|improvement|association|link)\b|\b(?:did|does|do|was|were) not (?:differ|change|reduce"
    r"|improve|lower|raise|increase|affect)\b|\bnot (?:statistically )?significant|\bno clear difference\b",
    re.IGNORECASE)


def all_null(text: str, studies: list[dict] = ()) -> bool:
    """
    Whether an effect line reports nothing but nulls: every sentence in it
    that carries a figure is a null, by its own words or by its abstract.
    With no figure at all, every sentence has to be one. "Lost 4.1 kg
    (p < 0.001)" beside a waist result that was not significant is a real
    effect with one null beside it, not "no clear effect".
    """
    said = sentences(text or "")
    pool = [t for t in said if _figures(t)] or said
    return bool(pool) and all((_NULL_FINDING.search(t) or null_figure(t, list(studies)) is not None)
                              and not significant_figure(t) for t in pool)


def significant_figure(sentence: str) -> bool:
    """
    Whether this sentence carries a significant figure: a p value under
    0.05, or a figure whose interval or p value rules out no effect. Such a
    sentence is not a null, whatever else it says ("no clear difference in
    waist size, while adults lost 4.1 kg (p < 0.001)").
    """
    if evidence._P_SIGNIFICANT.search(sentence or ""):
        return True
    return any(evidence.significance(sentence, fig) == "clear" for fig in _figures(sentence))


def say_nulls(text: str, studies: list[dict]) -> str:
    """
    Every sentence whose figure its abstract calls no clear difference says
    so: a size word for it ("a small effect") becomes "no clear difference",
    and a sentence that does not already say it is followed by one that
    does, carrying the same citation. A sentence that carries a significant
    figure (its own p value or interval rules out no effect) is never called
    a null: "That difference was not statistically significant" after it
    would be read as about that figure.
    """
    out = []
    for sentence in sentences(text or ""):
        ref = null_figure(sentence, studies)
        if ref is None or _SAID_NULL.search(sentence) or significant_figure(sentence):
            out.append(sentence)
            continue
        if _SIZE_SAID.search(sentence):
            sentence = _SIZE_SAID.sub("no clear difference", sentence)
            out.append(sentence)
            continue
        out.append(sentence)
        if ref:
            out.append(NOT_SIGNIFICANT_SAID.format(ref=ref))
    return " ".join(out)


_ONE_SIDED = re.compile(
    r"(?P<x>(?<![\w.])\d+(?:\.\d+)?)(?P<between>(?:\s+[A-Za-z%][\w%-]*){0,4}?)\s+"
    r"(?P<verb>compared (?:to|with)|versus|vs\.?|relative to)\s+"
    r"(?P<grp>(?:the\s+|those\s+in\s+the\s+)?(?:controls?|control group|placebo(?: group)?|"
    r"usual care|comparison group|the other group|those who did not))\b",
    re.IGNORECASE)
_COMPARATOR = re.compile(
    r"^[^.\d]{0,80}?\b(?:compared (?:with|to)|versus|vs\.?|against)\s+"
    r"(?:\w+\s+){0,2}?(?P<y>\d+(?:\.\d+)?)\b", re.IGNORECASE)


def _other_side(abstract: str, x: str) -> str | None:
    """The comparison group's figure printed straight after x in the abstract."""
    for m in re.finditer(r"(?<![\d.])" + re.escape(x) + r"(?![\d])", abstract):
        after = abstract[m.end():m.end() + 200]
        after = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", after)   # intervals, p values
        found = _COMPARATOR.match(after)
        if found and found.group("y") != x:
            return found.group("y")
    return None


def keep_comparison(text: str, studies: list[dict]) -> str:
    """
    "1.7 episodes compared to controls" becomes "1.7 episodes compared with
    3.2 in controls" when the abstract it cites prints the controls' figure
    beside the 1.7. Only that one shape is rewritten, and only from the
    cited abstract: a sentence that already names both figures, or whose
    abstract does not pair them, is left as it was.
    """
    out = []
    for sentence in sentences(text or ""):
        m = _ONE_SIDED.search(sentence)
        if m:
            for _, abstract in _cited_abstracts(sentence, studies):
                y = _other_side(abstract, m.group("x"))
                if y:
                    grp = re.sub(r"^(?:the|those in the)\s+", "", m.group("grp"),
                                 flags=re.IGNORECASE)
                    sentence = (sentence[:m.start("verb")] + f"compared with {y} in "
                                f"{'the ' if 'group' in grp.lower() else ''}{grp}"
                                + sentence[m.end("grp"):])
                    break
        out.append(sentence)
    return " ".join(out)


_NULL_SAID_BY_CODE = re.compile(
    "^(?:" + re.escape(NOT_SIGNIFICANT_SAID.split("({ref})")[0]) + "|"
    + re.escape(TRANSLATION_LEAD + NULL_SAID) + r" )\((?P<ref>[^()]*)\)\.$")


def unsaid_nulls(text: str) -> str:
    """
    `text` without a null sentence code wrote ("That difference was not
    statistically significant (Study 1).") straight after a sentence of the
    same study that carries a significant figure: a check saved before
    say_nulls asked has it, under "0.79 (95 percent CI 0.71 to 0.89)".
    Text with no such pair comes back byte for byte.
    """
    parts = sentences(text or "")
    kept = []
    for s in parts:
        m = _NULL_SAID_BY_CODE.match(s)
        if (m and kept and significant_figure(kept[-1])
                and set(re.findall(r"\d+", m.group("ref"))) <= set(re.findall(r"\d+", " ".join(
                    x.group(1) for x in _REF.finditer(kept[-1]))) or [None])):
            continue
        kept.append(s)
    return (text or "") if len(kept) == len(parts) else " ".join(kept)


def tell_straight(text: str, studies: list[dict]) -> str:
    """Rule 9, both halves, for any gated prose."""
    return say_nulls(keep_comparison(text, studies), studies)


def drop_citing(text: str, skip, count: int) -> str:
    """Every sentence of text except those resting only on studies in skip."""
    if not skip:
        return text or ""
    return " ".join(sentence for sentence in sentences(text or "")
                    if not (refs(sentence, count) and set(refs(sentence, count)) <= set(skip)))


def drop_any_citing(text: str, skip, count: int) -> str:
    """
    Every sentence of text except those citing any study in skip, whatever
    else they cite. For studies the page shows as set aside (withdrawn, a
    journal notice, off topic): taking only their number out of "(Studies
    1, 2)" would credit words written about Study 1 to Study 2, so the
    sentence goes whole. Text with no such sentence comes back byte for byte.
    """
    skip = set(skip or ())
    parts = sentences(text or "")
    kept = [s for s in parts if not skip & set(refs(s, count))]
    return (text or "") if len(kept) == len(parts) else " ".join(kept)


def relied_only(text: str, relied, count: int) -> str:
    """
    `text` with every citation of a study the verdict did not rely on taken
    out: a sentence that cites only such studies goes, and one that cites a
    mix keeps the studies relied on, so "(Studies 1, 2, 4, 6)" becomes
    "(Studies 1, 2, 6)". Text that cites nothing is left as it was.
    """
    relied = set(relied or ())
    drop = {n for n in range(1, count + 1) if n not in relied}
    kept = []
    for sentence in sentences(text or ""):
        cited = refs(sentence, count)
        if cited and set(cited) <= drop:
            continue

        def fix(m: re.Match) -> str:
            nums = [int(n) for n in re.findall(r"\d+", m.group(1))
                    if int(n) not in drop or int(n) > count]
            if not nums:
                return _GONE
            return (f"Study {nums[0]}" if len(nums) == 1
                    else "Studies " + ", ".join(str(n) for n in nums))

        sentence = without_empty_refs(_REF.sub(fix, sentence) if cited else sentence)
        if sentence:
            kept.append(sentence)
    return " ".join(kept)


# Where a citation whose every number was taken out stood.
_GONE = "\x00"


def without_empty_refs(sentence: str) -> str:
    """
    `sentence` with every citation left with no number taken out whole:
    "(Studies )", "(Study )", "( )" and the space before it. A citation
    that still has numbers but lost some, or whose list begins or ends
    with a separator ("(Study 5; )", "(Studies , 4)"), is rebuilt from the
    numbers it has: "(Study 5)", "(Study 4)". A short lead-in in the
    bracket ("see", "see also", "e.g.", "cf.", "as in") is part of the
    citation: "(see Studies )" goes whole, "(see Studies , 4)" reads
    "(see Study 4)". A citation that was part of
    the sentence itself ("Studies and show") cannot be taken out without
    rewriting it, so the sentence goes: "" is returned. Everything else in
    the sentence, a sentence with no such citation included, comes back
    byte for byte.
    """
    def fix(m: re.Match) -> str:
        body = m.group("body")
        if not body.strip():
            return ""
        lead = _REF_LEAD.match(body)
        lead = lead.group(0) if lead and _REF_BODY.fullmatch(body[lead.end():]) else ""
        body = body[len(lead):]
        if not _REF_BODY.fullmatch(body) or not (_GONE in body or re.search(r"\bStud", body)):
            return m.group(0)
        nums = re.findall(r"\d+", body)
        if not nums:
            return ""
        if _GONE not in body and not _ODD_REF.search(body):
            return m.group(0)
        open_, close = m.group("open"), m.group("close")
        said = f"Study {nums[0]}" if len(nums) == 1 else "Studies " + ", ".join(nums)
        return f"{m.group('space')}{open_}{lead}{said}{close}"

    out = _BRACKET_REF.sub(fix, sentence)
    return "" if _GONE in out else out


# A bracket and the space before it; its body is judged in without_empty_refs.
_BRACKET_REF = re.compile(r"(?P<space>\s*)(?P<open>[(\[])(?P<body>[^()\[\]]*)(?P<close>[)\]])")
# A short lead-in a citation can carry inside its bracket: "(see Study 4)",
# "(e.g. Studies 2, 3)". Judged with the rest of the body after it.
_REF_LEAD = re.compile(r"\s*(?:see(?:\s+also)?|e\.g\.,?|cf\.|as\s+in)\s+", re.IGNORECASE)
# A body made only of the words, numbers and separators of a citation.
_REF_BODY = re.compile(r"(?:\s|Stud(?:y|ies)\b|\d+|[,;&]|\band\b|\x00)*")
# A list that begins or ends with a separator, or a "Study" with no number
# after it.
_ODD_REF = re.compile(r"^\s*(?:Stud(?:y|ies)\s*)?(?:[,;&]|and\b)|(?:[,;&]|\band)\s*$|"
                      r"Stud(?:y|ies)\s*(?:[,;&]|\band\b|$)")


# ---------------------------------------------------------------------
# Held to its sources
#
# A sentence that cites a study has to be a sentence that study can carry.
# The audit found four that were not: "pregnant women" for a study of people
# after birth, a "who it does not apply to" line its study never mentions,
# a study cited in the prose but listed as "Read, not relied on", and "15 to
# 30 mL" credited to studies that give 20 and 30. Each rule below looks only
# at the studies a sentence cites, not at every abstract fetched, and when a
# sentence fails, it goes: a sentence the reader never sees cannot mislead
# them, and the rest of the page still answers the question. Runs on the way
# out, so cached checks are held to it too. Never touches "still open".
# ---------------------------------------------------------------------

# A sentence that names studies in order to say they were set aside, or count
# for less, is honest about them, and keeps its citation.
_SETS_ASIDE = re.compile(
    r"\b(?:count(?:s|ed)? for less|point(?:s|ed)? the other way|set aside|not relied on|"
    r"(?:did|does|do) not (?:test|measure|study|look at)|don[\u2019']t (?:test|measure)|"
    r"off topic|not (?:a )?tests? of|retracted|"
    r"(?:animal|lab|laboratory|preclinical|in vitro|cell|rat|mouse|mice) (?:studies|study|work|"
    r"research|models?|experiments?))\b", re.IGNORECASE)

# A sentence that fails its sources is dropped, except a caution about harm
# and a statement that the claim went untested. The caution rule is a
# fail-safe: keeping a false reassurance is never acceptable, and losing a
# few awkwardly worded warnings is. A failing sentence stays only if it names
# a harm (or is an instruction not to use something) AND has no softening
# word, no reassuring word and no negation in it. A kept caution names no
# study, bracketed or inline, so none is credited with what its record does
# not give.
#
# The "does not apply to" line is a list of people, not a claim: a harm or
# hazard word there ("bacterial infections", "contraindications to alcohol")
# says who must not rely on the answer. It meets the same softening test but
# not the negation test ("people without desk jobs, and people with severe
# spinal injuries").
HARM = re.compile(
    r"\b(?:injur\w*|burn(?:s|ed|t|ing)?|toxic\w*|poison\w*|teratogen\w*|unsafe|not safe|"
    r"should not|shouldn[’']t|must not|do not stop|don[’']t stop|contraindicat\w*|"
    r"bleed\w*|deaths?|died|fatal\w*|overdos\w*|side[- ]effects?|adverse|harm\w*|dangers?|"
    r"dangerous\w*|warn\w*|caution\w*)\b", re.IGNORECASE)
_HAZARD = re.compile(r"\b(?:infections?|infectious|outbreaks?|interact\w*|abnormalit\w*)\b",
                     re.IGNORECASE)

# Words naming a harm, whole words with their inflections.
_HARM_WORDS = re.compile(
    r"\b(?:death\w*|die|dies|died|dying|dead|deadly|fatal\w*|kill\w*|lethal|mortality|"
    r"injur\w*|damag\w*|failures?|bleed\w*|ha?emorrhag\w*|birth defects?|malformation\w*|"
    r"miscarriages?|stillbirths?|overdos\w*|poison\w*|toxic\w*|seizures?|anaphyla\w*|"
    r"allergic reactions?|interact\w*|hospitali[sz]\w*|side[- ]effects?|adverse|"
    r"harm\w*|danger\w*|hazardous|unsafe|teratogen\w*|outbreaks?|burn(?:s|ed|t|ing)?|strokes?|heart attacks?|"
    r"complications?|abnormalit\w*|contraindicat\w*|"
    r"nausea\w*|vomit\w*|diarrh\w*|dizz\w*|headaches?|rash\w*|itch\w*|syndromes?|hepatitis|jaundice|"
    r"arrhythmi\w*|palpitations?|hypoglyc\w*|hyperkal\w*|hypotension|"
    r"coma|paralysis|blindness|deafness|addiction|cramps?|pain\w*|"
    r"swelling|ulcers?|kidney stones?)\b", re.IGNORECASE)

# Words that soften or reassure. Any one anywhere in the sentence drops it.
_SOFTENERS = re.compile(
    r"\b(?:may|might|could|possibl\w+|perhaps|sometimes|occasional\w*|rare|rarely|rarer|seldom|"
    r"uncommon|infrequent\w*|usually|generally|typically|mostly|mild\w*|minor|minimal|negligible|tiny|"
    r"slight\w*|some|few|fewer|less|lower\w*|low|little|small\w*|only|hardly|barely|unlikely|"
    r"safe|safely|safer|safety|harmless|gentle|gently|tolerat\w+|fine|ok|okay|relax\w*|worr\w+|concern\w*|"
    r"unfounded|unsupported|unchanged|similar|comparable|resolv\w+|reversible|transient|"
    r"benign|reassur\w+|placebo-like|"
    # a cut in the harm is good news, whatever the harm is
    r"reduc\w+|fell|fall\w*|fallen|drop\w*|declin\w+|decreas\w+|cut|cuts|cutting|prevent\w*|"
    r"halv\w+|lessen\w*|curb\w*|avoid\w*|protect\w*|improv\w*|"
    # a benefit claim is not a harm, and "can" must not carry one through
    r"treat\w*|cur(?:e|es|ed|ing)|heal\w*|reliev\w*|relief|eas(?:e|es|ed|ing)|stop|stops|stopped|stopping|"
    r"control\w*|manag\w*|restor\w*|boost\w*|support\w*|help\w*|benefit\w*|fight\w*|shrink\w*|"
    r"slow\w*|eliminat\w*|revers\w*|destroy\w*|clear\w*|eradicat\w*|target\w*|"
    # minimisers: the harm is said to be tiny, however it is worded
    r"scarc\w+|practically|virtually|essentially|insignificant|trivial|isolated|sporadic|nil|"
    r"unheard of|handful|modest|limited|just|merely)\b", re.IGNORECASE)
# A rarity figure: "under 1%", "fewer than 5 in 1,000", "3 in 10,000", "up to 2".
_RARITY = re.compile(
    r"\b(?:under|below|less than|fewer than|at most|up to)\s+[\d.,]+|\b\d[\d,.]*\s+in\s+[\d,]{3,}\b|"
    r"\b\d[\d,.]*\s+(?:per|out of)\s+[\d,]{3,}\b", re.IGNORECASE)
# Killing germs or cells is a benefit claim, not a harm.
_KILLS_GERMS = re.compile(
    r"\b(?:kill|destroy|attack)\w*\s+(?:\w+\s+){0,3}?(?:cells?|bacteri\w*|germs?|microbes?|viruses|virus|"
    r"parasites?|fungi|fungus|pathogens?|tumou?rs?|cancer\w*)\b", re.IGNORECASE)

# Negation turns a harm into a reassurance ("caused no birth defects").
_NEGATION = re.compile(
    r"\b(?:no|not|never|none|nothing|nobody|without|zero|neither|nor|cannot|non-?|"
    r"free of|clear of|lack of|lacks?|lacking|absen(?:t|ce)|rul(?:e|es|ed|ing) out|exclud\w*|myths?|"
    r"misconception\w*|exaggerat\w*|overblown|overstat\w*|unproven|disprov\w*|debunk\w*|refut\w*|"
    r"false alarms?|nonexistent|unaffected|normal\w*|undetect\w*|unobserved)\b|n[’']t\b", re.IGNORECASE)

# An instruction not to use something. It is a caution without a harm word,
# and its own "not" and "avoid" are not counted as negation or softening.
_PROHIBITION = re.compile(
    r"\b(?:(?:do not|don[’']t|must not|mustn[’']t|should not|shouldn[’']t|should never|never|"
    r"no one should|nobody should)\s+(?:(?:ever|simply|just)\s+)?"
    r"(?:take|give|use|combine|exceed|start|stop|mix|drink|eat|inject|apply|swallow|skip|delay|rely|replace)\b|"
    r"not be (?:used|given|taken)|"
    r"must be avoided|(?:is|are) (?:best )?avoided|(?:should|must|need to|needs to|advised to|best to|"
    r"told to|ought to)\s+(?:\w+\s+){0,2}?avoid\w*|^\W*avoid\b|"
    r"stop(?:s|ped)? taking|(?:is|are) contraindicated)", re.IGNORECASE)


def is_warning(sentence: str, who: str = "") -> bool:
    """A caution about harm that nothing in the sentence softens: it names a
    harm ("deaths occurred in 3 children") or tells people not to use
    something ("do not take it with warfarin"), and has no softening or
    reassuring word ("may", "rare", "safe", "fewer", "reduced") and no
    negation ("no", "not", "without"). A prohibition's own "not" does not
    count against it. On the "does not apply to" line a harm or hazard word
    is enough, if nothing softens it."""
    body = _REF.sub(" ", sentence or "")
    rest = _PROHIBITION.sub(" ", body)
    if _SOFTENERS.search(rest) or _KILLS_GERMS.search(rest) or _RARITY.search(rest):
        return False
    if who == "not":
        return bool(HARM.search(body) or _HAZARD.search(body))
    if _NEGATION.search(rest):
        return False
    return bool(_HARM_WORDS.search(rest) or _PROHIBITION.search(body))


# A sentence saying the studies did not test the claim is the honest answer to
# it, and is never dropped either: "Neither tested whether diabetes went away".
_UNTESTED = re.compile(
    r"\b(?:neither|none|no (?:study|trial|review)s?|nothing)\b[^.]{0,60}?\btest|"
    r"\b(?:not|never|n[’']t)\s+(?:\w+\s+){0,2}?test(?:ed|s)?\b", re.IGNORECASE)


def never_dropped(sentence: str, who: str = "") -> bool:
    """Whether a sentence that fails its sources is kept anyway. Only a
    statement that the claim went untested, with no harm named in it, is: it asserts an absence no study
    could support. A warning of harm is not. Five rounds of word lists could
    not tell every warning from every reassurance, so a sentence no cited
    study supports is removed, even a warning. is_warning stays as the old
    classifier, used by tests and no longer by the page."""
    body = _REF.sub(" ", sentence or "")
    return bool(_UNTESTED.search(body)
                and not (_HARM_WORDS.search(body) or HARM.search(body) or _PROHIBITION.search(body)))


def uncited(sentence: str) -> str:
    """`sentence` with its bracketed citations taken off."""
    out = _PAREN_REF.sub("", sentence)
    return re.sub(r"\s+([.,;:!?])", r"\1", out).strip()


_NUMS = r"\d+(?:\s*(?:,|,?\s*and\b|&)\s*\d+)*"
# A study named in the sentence itself, as the one saying it: "Study 1
# found that", "Studies 1 and 2 show", "In Study 1,", "in Study 1", "study 4
# is a reminder that". Taken out, the caution says the same and credits no
# study with what its record does not give.
_INLINE_ATTRIBUTION = [
    re.compile(rf"^\s*(?:in|according to|from)\s+stud(?:y|ies)\s+{_NUMS}\s*,\s*", re.IGNORECASE),
    re.compile(rf"\bstud(?:y|ies)\s+{_NUMS}\s+(?:\w+\s+){{0,2}}?(?:found|finds|find|shows?|showed|shown|"
               rf"reported|reports?|suggests?|suggested|notes?|noted|says|said|is a reminder|was a reminder|"
               rf"are a reminder|warns?|warned|saw|sees|observed|judged|considered|rated)\s+(?:that\s+)?",
               re.IGNORECASE),
    re.compile(rf"\s*,?\s*\b(?:in|from|by|according to)\s+stud(?:y|ies)\s+{_NUMS}\b", re.IGNORECASE),
]
# A finite verb, roughly: an auxiliary or modal, a past form, or a verb this
# prose uses. "It harmless in 9,000 pregnant women" has none.
_FINITE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|am|has|have|had|do|does|did|can|cannot|may|might|could|would|will|"
    r"shall|should|must|\w+ed|rose|fell|led|saw|took|gave|made|grew|ran|came|went|found|told|left|kept|"
    r"shows?|causes?|injures?|burns?|damages?|harms?|raises?|lowers?|reduces?|increases?|worsens?|kills?|"
    r"interacts?|carr(?:y|ies)|spreads?|leads?|helps?|works?|needs?|remains?|stays?|seems?|appears?|"
    r"puts?|gets?|makes?|takes?|gives?|lasts?|says?|means?|tests?|protects?|prevents?|affects?)\b",
    re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"(,\s*(?:and|but)\s+|;\s*|\s+but\s+)", re.IGNORECASE)


def _tidy(text: str) -> str:
    out = re.sub(r"\s{2,}", " ", re.sub(r"\s+([.,;:!?])", r"\1", text)).strip()
    return out[:1].upper() + out[1:]


def unattributed(sentence: str, passes=None) -> str:
    """`sentence` with no study named in it: bracketed citations off, inline
    naming taken out, and any study still named reworded to "a study". It
    never leaves a clause without a verb: where taking the naming out would
    ("Study 1 found it harmless in ..."), the clause reads "A study found
    ..." if `passes(clause)` says its sources carry it, and otherwise the
    clause goes. With nothing left, the result is empty."""
    parts = _CLAUSE_SPLIT.split(uncited(sentence))
    clauses, seps = parts[0::2], parts[1::2]
    out: list[str | None] = []
    for clause in clauses:
        if not _REF.search(clause):
            out.append(clause)
            continue
        bare = clause
        for pattern in _INLINE_ATTRIBUTION:
            bare = pattern.sub("", bare)
        if _FINITE.search(_REF.sub(" ", bare)):
            out.append(_REF.sub(lambda m: "studies" if re.search(r"\d\D+\d", m.group(1)) else "a study", bare))
        elif passes is not None and passes(clause):
            out.append(_REF.sub(lambda m: "studies" if re.search(r"\d\D+\d", m.group(1)) else "a study", clause))
        else:
            out.append(None)
    text = ""
    for i, clause in enumerate(out):
        if clause is None:
            continue
        if text:
            text += seps[i - 1]
        text += clause
    if not text.strip(" .,;:"):
        return ""
    if not re.search(r"[.!?]$", text.strip()):
        text = text.rstrip(" ,;:") + "."
    return _tidy(text)


_PAREN_REF = re.compile(
    r"\s*\(\s*((?:stud(?:y|ies)\s+\d+(?:\s*(?:,|,?\s*and\b|&)\s*\d+)*\s*[,;]?\s*)+)\)",
    re.IGNORECASE)


def strip_unrelied(sentence: str, relied, count: int) -> str:
    """`sentence` with the bracketed citations of studies not relied on taken
    off, keeping any relied-on study in the same brackets. A citation that is
    part of the sentence itself ("study 4 is a reminder") cannot be taken off
    without rewriting it, so the sentence is then left exactly as it was."""
    relied = set(relied or ())

    def fix(m: re.Match) -> str:
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        keep = [n for n in nums if n in relied or n > count]
        if not keep:
            return ""
        return " (Study %d)" % keep[0] if len(keep) == 1 else \
            " (Studies " + ", ".join(str(n) for n in keep) + ")"

    out = _PAREN_REF.sub(fix, sentence)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out).strip()
    if any(n not in relied for n in refs(out, count)):
        return sentence
    return out


# Who a study was run in. A sentence that names one of these groups must cite
# a study whose own record names it: the exercise page said "pregnant women"
# of a study of people after birth.
_GROUP_TERMS = [(re.compile(said, re.IGNORECASE), re.compile(source, re.IGNORECASE)) for said, source in (
    (r"\bpregnan\w*|\bexpectant mothers?\b|\bprenatal\b|\bantenatal\b",
     r"\bpregnan\w*|\bprenatal\b|\bantenatal\b|\bgestation\w*|\bexpectant\b"),
    (r"\bpostpartum\b|\bpostnatal\b|\bafter (?:giving )?birth\b|\bnew mothers?\b",
     r"\bpostpartum\b|\bpostnatal\b|\bafter (?:giving )?birth\b|\bnew mothers?\b|\bpuerper\w*"),
    (r"\bolder (?:adults?|people|women|men|groups?)\b|\belderly\b|\bseniors?\b",
     r"\bolder\b|\belderly\b|\bseniors?\b|\bag(?:e|ed|ing)\b|\bgeriatric\b|\b(?:6[0-9]|[7-9][0-9]) years\b"),
    (r"\bchildren\b|\bchild\b|\bkids?\b|\badolescen\w*|\bteenagers?\b|\binfants?\b|\bbabies\b",
     r"\bchild\w*|\bkids?\b|\badolescen\w*|\bteen\w*|\binfan\w*|\bbab(?:y|ies)\b|\bpa?ediatric\b|"
     r"\byouth\b|\bschool\w*|\bnewborns?\b|\bneonat\w*|\baged 0\b"),
    (r"\bcancers?\b|\boncolog\w*|\bchemotherapy\b|\btumou?rs?\b",
     r"\bcancers?\b|\boncolog\w*|\bchemotherapy\b|\btumou?r\w*|\bcarcino\w*|\bmalignan\w*|\bneoplas\w*"),
    (r"\bdiabet\w*", r"\bdiabet\w*|\bT2DM?\b|\bT1DM?\b"),
    (r"\bathletes?\b", r"\bathlet\w*|\bsports?\w*|\bplayers?\b|\btrained\b|\brunners?\b|\bmarathon\w*|"
     r"\bphysical stress\b|\bsoldiers?\b|\bexercis\w*"),
    (r"\bfo?etus(?:es)?\b|\bfo?etal\b|\bunborn\b",
     r"\bfo?etus(?:es)?\b|\bfo?etal\b|\bunborn\b|\bprenatal\b|\bpregnan\w*"),
    (r"\bpostmenopausal\b|\bafter menopause\b", r"\bmenopaus\w*|\bpostmenopaus\w*"),
    (r"\bobes\w*|\boverweight\b", r"\bobes\w*|\boverweight\b|\bBMI\b|\bbody mass\b"),
    (r"\bsmokers?\b", r"\bsmok\w*|\btobacco\b|\bcigarette\w*"),
    (r"\bstudents?\b", r"\bstudents?\b|\bundergraduates?\b|\buniversity\b|\bcollege\b"),
    (r"\bnursing homes?\b|\bcare homes?\b", r"\bnursing homes?\b|\bcare homes?\b|\blong.term care\b|\bresidential\b"),
    (r"\bheart (?:disease|failure)\b", r"\bheart\b|\bcardi\w*|\bcoronary\b"),
    (r"\bkidney\b|\brenal\b|\bdialysis\b", r"\bkidney\w*|\brenal\b|\bdialysis\b|\bnephr\w*"),
    (r"\bdementia\b|\balzheimer\w*", r"\bdementia\b|\balzheimer\w*|\bcognitive (?:decline|impairment)\b"),
)]
# A sentence that says a group was left out of the studies ("children were
# not in either trial") is true because the records do not name them, so it
# is not held to naming them.
_LEFT_OUT = re.compile(
    r"\b(?:not|never|no)\b(?:\s+[\w-]+){0,3}?\s+(?:included|include|includes|studied|study|"
    r"enrolled|enrol|enroll|tested|test|covered|cover|targeted|target|focus|represented|"
    r"part of|among|appear|apply|applies|in)\b|\bdoes(?:n[\u2019']t| not) cover\b|"
    r"\b(?:did|do|does|were|was|are|is)n[\u2019']t\b(?:\s+[\w-]+){0,3}?\s+(?:included|include|"
    r"studied|study|enrolled|tested|test|covered|cover|target|in|among)\b|\bexcluded\b",
    re.IGNORECASE)
# "Thousands of people" needs a count that size in a study it cites: the
# creatine page said "thousands" of a review of 1,937.
_VAGUE_COUNTS = [(re.compile(r"\btens of thousands\b", re.I), 20000),
                 (re.compile(r"\b(?<!tens of )thousands\b", re.I), 2000),
                 (re.compile(r"\bhundreds\b", re.I), 200),
                 (re.compile(r"\bmillions\b", re.I), 2000000)]
_PEOPLE_AFTER = re.compile(r"\s*(?:[A-Za-z-]+\s+){0,3}?(?:participants|adults|patients|subjects|"
                           r"individuals|people|persons|men|women|children|cases|volunteers)\b",
                           re.IGNORECASE)
# Words any "who it applies to" clause can use without the study saying them.
_GENERIC_WHO = frozenset("""
people person persons adult adults those these them they their individual individuals
group groups participant participants patient patients anyone everyone someone with
without who whom whose that which have having from under over more less than specific
certain other others some many most such also only especially mainly generally usually
likely may might studied study studies evidence research trial trials data apply
applies applied claim this being been were what when where while into onto about
general healthy population populations type types kind kinds form forms find found
taking take takes using used use doing does done among across between within based
finding findings result results like such including include includes generally broadly
""".split())


# "1 279 804" or "11 321" (a space, thin space or no-break space between
# groups of three) is one number, written 1,279,804.
_SPACED_THOUSANDS = re.compile(r"(?<=\d)[\u2009\u202f\xa0 ](?=\d{3}(?!\d))")


def _ungroup(text: str) -> str:
    return _SPACED_THOUSANDS.sub(",", text or "")


def _studies_text(studies, numbers) -> str:
    return _ungroup(" ".join(f"{studies[n - 1].get('title') or ''} {studies[n - 1].get('abstract') or ''}"
                             for n in numbers if 0 < n <= len(studies)))


def _counts_in(text: str) -> list[int]:
    """The whole numbers a record gives that can be counts of people: a bare
    year (1900 to 2099 with no people word after it) is not one."""
    out = []
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?![\d.])", text or ""):
        n = int(m.group(1).replace(",", ""))
        if 1900 <= n <= 2099 and "," not in m.group(1) and not _PEOPLE_AFTER.match(text, m.end()):
            continue
        out.append(n)
    return out + [int(x) for x in evidence.written_numbers(text) if str(x).isdigit()]


def _known_in(studies, numbers) -> set:
    """Every number the cited records give, and each as the page rounds it
    (for_reading): "21.929" is printed as "about 22", "7.44" can be "7"."""
    # "11 321" with a thin space is 11,321, in the title as in the abstract.
    picked = [studies[n - 1] for n in numbers if 0 < n <= len(studies)]
    bag = corpus([{**st, "title": _ungroup(str(st.get("title") or "")),
                   "abstract": _ungroup(str(st.get("abstract") or ""))} for st in picked])
    source = " ".join(f"{st.get('title') or ''} {st.get('abstract') or ''}" for st in picked)
    bag |= _supported_counts(_ungroup(source))
    for num in list(bag):
        if "." in num:
            try:
                bag.add(_canon(str(Decimal(num).quantize(Decimal(1), ROUND_HALF_UP))))
            except (ArithmeticError, ValueError):
                continue
            r = _rounded(num)
            if r:
                bag.add(_canon(r[0].lstrip("-−")))
    return bag


# Group sizes a record gives as "n = 25" or "(n=29)": their sum is the
# trial's total, which a sentence may give ("54 adults") though the record
# never prints it.
_GROUP_N = re.compile(r"\bn\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)", re.IGNORECASE)


def _supported_counts(source: str) -> set:
    """Counts a record supports without printing them: the sum of its group
    sizes, and each large count as a reader rounds it, to two significant
    figures ("1,279,804" is "1,300,000", and "1.3 million" is read as that
    before it is looked up; "36,352" is "36,000")."""
    out = set()
    groups = [int(g.replace(",", "")) for g in _GROUP_N.findall(source)]
    if 2 <= len(groups) <= 6:
        out.add(str(sum(groups)))
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\d.])", source):
        n = int(m.group(1).replace(",", ""))
        if n < 1000:
            continue
        digits = len(str(n))
        r = round(n, -(digits - 2))
        out.add(str(r))
    return out


_SCALED = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s+(thousand|million|billion)\b", re.IGNORECASE)
_SCALE = {"thousand": 1000, "million": 1000000, "billion": 1000000000}


def _unscaled(sentence: str) -> str:
    """ "1.3 million people" read as "1300000 people", so a count written in
    words is checked as the number it is, not as a bare 1.3."""
    def whole(m):
        v = Decimal(m.group(1)) * _SCALE[m.group(2).lower()]
        return str(int(v)) if v == v.to_integral_value() else m.group(0)
    return _SCALED.sub(whole, sentence)


def _unsupported_who(sentence: str, source: str) -> bool:
    """True when some clause of a "who it applies to" sentence shares no
    word with the records it cites: "those with specific untreated medical
    conditions (Study 7)", from a study that never mentions a condition."""
    body = re.sub(r"\([^)]*\)", " ", _REF.sub(" ", sentence))
    have = {_stem(w) for w in re.findall(r"[A-Za-z]{3,}", source)}
    for clause in re.split(r",|;|:|\bor\b|\band\b|\bbut\b|\bnor\b", body, flags=re.IGNORECASE):
        words = [w for w in re.findall(r"[A-Za-z]{4,}", clause) if w.lower() not in _GENERIC_WHO]
        if words and not any(_stem(w) in have for w in words):
            return True
    return False


def _clause_fails(clause: str, source: str, have: set) -> bool:
    """A "who it applies to" clause its cited records do not support. A clause
    that names a known group is judged by the group alone ("pregnant women"
    for a study of alcohol use during pregnancy is fine); any other clause
    needs one word that starts the same as a word in the records."""
    named = [(said, src) for said, src in _GROUP_TERMS if said.search(clause)]
    if named:
        return not all(src.search(source) for _said, src in named)
    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", clause) if w.lower() not in _GENERIC_WHO]
    return bool(words) and not any(w[:5] in have for w in words)


def _who_clauses(sentence: str, studies, numbers) -> str:
    """An "applies to" sentence with only its unsupported clauses taken out:
    "Adults, older women and smokers (Study 1)" keeps what Study 1 names.
    "" when no clause is left."""
    source = _studies_text(studies, numbers)
    m = re.search(r"\s*\([^)]*\)\s*\.?\s*$", sentence)
    body, tail = (sentence[:m.start()], sentence[m.start():]) if m else (sentence.rstrip(". "), ".")
    parts = [p.strip() for p in re.split(r",\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+", body) if p.strip()]
    have = {w.lower()[:5] for w in re.findall(r"[A-Za-z]{4,}", source)}
    keep = [p for p in parts if not _clause_fails(p, source, have)]
    if len(keep) == len(parts):
        return sentence
    if not keep:
        return ""
    joined = keep[0] if len(keep) == 1 else ", ".join(keep[:-1]) + " and " + keep[-1]
    return joined[:1].upper() + joined[1:] + (tail if tail.strip() else ".")


def _from_claim_or_name(sentence: str, figure_at: int, figure: str, claim: str) -> bool:
    """A number the gate does not ask a record for: one the claim itself
    gives ("8 glasses of water"), or one that is part of a name ("type 2
    diabetes", "COVID-19", "phase 3")."""
    if _NAME_NUMBER.search(sentence[:figure_at]):
        return True
    return _canon(figure) in {_canon(m.group(0)) for m in _NUMERAL.finditer(claim or "")}


def _fails_sources(sentence: str, studies, numbers, who: str = "", claim: str = "") -> bool:
    """Whether a sentence says something the studies it cites do not. `who`
    is "applies" or "not" for the two "who it applies to" lines: the groups
    a "does not apply to" line names are, as expected, groups its records do
    not mention, so neither the group rule nor the clause rule runs there,
    and an "applies to" line has its clauses checked one by one elsewhere."""
    source = _studies_text(studies, numbers)
    left_out = bool(_LEFT_OUT.search(sentence))
    if not who:
        for said, named in _GROUP_TERMS:
            if not left_out and said.search(sentence) and not named.search(source):
                return True
    if not sentence.startswith(TRANSLATION_LEAD):
        known = _known_in(studies, numbers)
        bare = _unscaled(_REF.sub(" ", sentence))
        if any(_canon(m.group(0)) not in known
               and not _from_claim_or_name(bare, m.start(), m.group(0), claim)
               for m in _NUMERAL.finditer(bare)):
            return True
    counts = _counts_in(source)
    for pattern, least in _VAGUE_COUNTS:
        if pattern.search(sentence) and not any(n >= least for n in counts):
            return True
    return False


def _gate(sentence: str, cited, studies, relied, who: str, claim: str) -> str:
    """The checks every cited sentence meets: `sentence` as it may be
    shown, or "" if it fails them."""
    count = len(studies or [])
    if not _SETS_ASIDE.search(sentence):
        using = [n for n in cited if n in relied]
        if not using:
            return ""
        if len(using) < len(cited):
            sentence = relied_only(sentence, relied, count)
            cited = using
    if _fails_sources(sentence, studies, cited, who, claim):
        return ""
    if who == "applies" and not _LEFT_OUT.search(sentence):
        sentence = _who_clauses(sentence, studies, cited)
    return sentence or ""


def held_to_sources(text: str, studies: list[dict], relied, who=False, claim: str = "") -> str:
    """
    `text` with every cited sentence held to the studies it cites:

    - a sentence citing only studies the verdict did not rely on goes;
    - one citing a mix keeps only the studies relied on, and then has to
      pass the checks below on those alone, or it goes;
    - a group of people it names ("pregnant women", "older adults with
      cancer") must be named in a cited record;
    - every figure must be in a cited record (rounded as the page rounds,
      or as a total or a rounded count the record supports), unless the
      claim itself gives it or it is part of a name ("type 2");
    - "thousands", "hundreds" of people need a count that size in one;
    - on the "applies to" line (`who="applies"`), a clause its records give
      no word for is taken out, not the whole line; on the "does not apply
      to" line (`who="not"`), the group and clause rules do not run.

    A sentence that fails these checks goes, even a warning of harm, unless it
    says the claim was not tested. That one stays, naming no study, bracketed or
    inline (unattributed), so none is credited with it. The one exception
    is a study not relied on that the sentence names inline, when what it
    is credited with passes: that cannot come off without a rewrite, so the
    sentence is left as written.

    `relied` is the set of study numbers the verdict relied on. With none
    relied on (not enough evidence), the text names what was read and why
    none of it counted, so it is left as it was. A sentence that cites
    nothing is left exactly as it was.
    """
    if who is True:
        who = "applies"
    count = len(studies or [])
    relied = set(relied or ())
    if not relied:
        return text or ""
    kept = []
    for sentence in sentences(text or ""):
        cited = refs(sentence, count)
        if not cited:
            kept.append(sentence)
            continue
        held = _gate(sentence, cited, studies, relied, who or "", claim)
        if held:
            kept.append(held)
            continue
        # It failed. Only a statement that the claim went untested stays,
        # and then it names no study.
        if not never_dropped(sentence, who or ""):
            continue
        def passes(clause: str) -> bool:
            return not _fails_sources(clause, studies, refs(clause, count), who or "", claim)

        sentence = uncited(sentence)
        # A study named in the sentence itself is credited only with its own
        # clause. If every such clause passes, the naming stays: "Neither
        # tested whether diabetes went away, and study 4 is a reminder that
        # undiluted vinegar can injure the oesophagus" credits study 4 with
        # the injury, which it gives, and not with diabetes.
        named = [cl for cl in _CLAUSE_SPLIT.split(sentence)[0::2] if refs(cl, count)]
        if not (named and all(passes(cl) for cl in named)):
            sentence = unattributed(sentence, passes=passes)
        if sentence:
            kept.append(sentence)
    return " ".join(kept)


def hold_breakdown(bd: dict | None, studies: list[dict], relied, claim: str = "") -> dict | None:
    """held_to_sources over every string of prose the breakdown prints, with
    the two "who it applies to" lines held to their own rules."""
    if not bd:
        return bd
    who = {k: bd.get(k) for k in ("applies_to", "not_applies_to")}
    out = map_prose(bd, lambda t: held_to_sources(t, studies, relied, claim=claim))
    for k, v in who.items():
        if v:
            out[k] = held_to_sources(v, studies, relied,
                                     who="applies" if k == "applies_to" else "not", claim=claim)
    return settle_effect(out)


def off_outcome(text: str, studies: list[dict], outcome: str, surrogate: str = "") -> bool:
    """
    True when a figure is about some other outcome than the claim's: it
    names neither the outcome nor its stand-in, and every study it cites
    never mentions either. A measles claim's effect row read "no link
    between vaccination and autism", from a review that never says measles.
    """
    stems = evidence.outcome_stems(outcome)
    if not text or not stems:
        return False
    stand_in = evidence.outcome_stems(surrogate)
    if evidence.names_any(text, evidence.outcome_kin(stems + stand_in)):
        return False
    numbers = refs(text, len(studies or []))
    return bool(numbers) and all(
        evidence.never_mentions(studies[n - 1], stems)
        and (not stand_in or evidence.never_mentions(studies[n - 1], stand_in))
        for n in numbers)


def figure_sentence(bd: dict | None) -> str:
    """
    The sentence the breakdown's sized figure (`effect`) was read from: the
    first sentence of the effect box, then of the evidence paragraphs, that
    gives the same figure, or failing that names its number. A figureless
    "no clear difference" is the effect box's own first sentence. The
    "That is ..." translation is code's arithmetic, never the source. ""
    when it cannot be found.
    """
    bd = bd or {}
    figure = str((bd.get("effect") or {}).get("figure") or "")
    pool = [t for text in [bd.get("effect_size")] + list(bd.get("evidence") or [])
            for t in sentences(str(text or "")) if not t.startswith(TRANSLATION_LEAD)]
    if not figure:
        return pool[0] if pool and bd.get("effect_size") else ""
    for t in pool:
        if (evidence.effect_size(t) or {}).get("figure") == figure:
            return t
    number = figure.split()[-1].rstrip("%")
    exact = re.compile(r"(?<![\d.])" + re.escape(number) + r"(?![\d])")
    return next((t for t in pool if exact.search(t)), "")


def _general_term(term: str) -> bool:
    """An outcome term made only of words that name no outcome ("health",
    "disease risk"). A short name such as "ASD" is not general: it is just
    too short to read a stem from."""
    words = re.findall(r"[a-z]+", term.lower())
    return bool(words) and all(w in evidence._NOT_AN_OUTCOME for w in words)


def names_outcome(text: str, outcome: str) -> bool:
    """True when `text` names the query's outcome group, or its kin, by the
    words evidence.measured_outcome reads a study by. False when that cannot
    be told: no outcome group, or one also named in general words ("health"),
    which any sentence would meet."""
    stems = evidence.outcome_stems(outcome)
    if not stems or any(_general_term(term)
                        for term in re.split(r"\s+OR\s+", outcome.strip("() "))):
        return False
    return evidence.names_any(text, evidence.outcome_kin(stems))


# Words that set what follows aside: "pain in the joints, not muscle, rose"
# is about the joints. Read on the text just before a mention of the outcome.
_SET_ASIDE = re.compile(r"\b(?:not|rather\s+than|instead\s+of|other\s+than|except(?:\s+for)?|excluding"
                        r"|apart\s+from|unlike|but\s+not)\s+(?:(?:the|in|of|on|for)\s+){0,2}$", re.IGNORECASE)


def _outcome_mentions(text: str, outcome: str):
    """(text, match) for each word in `text` that names the query's outcome
    group or its kin, read as names_outcome reads it."""
    t = evidence._us(text or "")
    for stem in evidence.outcome_kin(evidence.outcome_stems(outcome)):
        for m in re.finditer(r"\b" + re.escape(stem) + r"\w*", t, re.IGNORECASE):
            yield t, m


def sets_outcome_aside(text: str, outcome: str) -> bool:
    """True when the sentence names the outcome only to set it aside ("not
    muscle", "rather than pain"): its figure is about something else."""
    return any(_SET_ASIDE.search(t[:m.start()]) for t, m in _outcome_mentions(text, outcome))


def _modifies_another(t: str, m) -> bool:
    """The mention is the first half of a hyphened word that describes
    another noun: "triglyceride-lowering drugs" are drugs."""
    return bool(re.match(r"-[a-z]", t[m.end():], re.IGNORECASE))


# A classifying adjective right before the outcome word makes a longer term
# that names something else: "oxidative stress" is not stress, "metabolic
# syndrome" is not a syndrome in general. Read by its ending. The words that
# only say how bad, how long or how much are not such terms.
_CLASSIFIER = re.compile(r"\b([a-z]+(?:ive|ic|al|ar|ary|ous|ory))\s+$", re.IGNORECASE)
_DEGREE = frozenset("""chronic acute clinical total overall general usual normal actual initial final
    additional typical potential physical mental emotional psychological substantial several serious
    similar particular regular natural daily various previous""".split())


def _inside_longer_term(t: str, m, words: set) -> bool:
    """The mention follows a classifying adjective the claim does not use."""
    before = _CLASSIFIER.search(t[:m.start()])
    if not before:
        return False
    adj = before.group(1).lower()
    return adj not in _DEGREE and adj not in words


def names_outcome_itself(text: str, outcome: str, claim: str = "") -> bool:
    """True when at least one mention of the outcome in `text` is the
    outcome itself, not a word that only describes something else nor part
    of a longer term the claim does not use."""
    words = set(re.findall(r"[a-z]+", f"{claim} {outcome}".lower()))
    return any(not _modifies_another(t, m) and not _inside_longer_term(t, m, words)
               for t, m in _outcome_mentions(text, outcome))


def line_label(bd: dict | None, claim: str = "", outcome: str = "") -> str | None:
    """
    The effect label the evidence line may carry, or None. The line puts a
    size ("a large effect", "no clear effect") next to the claim, so it
    takes one only when the sentence the figure came from names the claim's
    own outcome. A large rise in inflammation is not a large effect on
    immunity, nor is one in a sentence that names the outcome only to set it
    aside ("not muscle") or only as a word describing another noun
    ("triglyceride-lowering drugs") or inside a longer term ("oxidative
    stress"). The effect box keeps its figure and its own
    sentence either way.
    """
    effect = (bd or {}).get("effect") or {}
    label = effect.get("label")
    if not label:
        return None
    said = figure_sentence(bd)
    if (not names_outcome(said, outcome) or sets_outcome_aside(said, outcome)
            or not names_outcome_itself(said, outcome, claim)):
        return None
    if label in ("small", "moderate", "large") and not agrees_direction(effect, said, claim):
        return None
    return label


# Which way a claim says its outcome moves. The verbs are the ones the code
# already reads a cause claim and a prevent claim by (evidence.CAUSE_CLAIM,
# evidence.PREVENT_CLAIM and the protective verbs). "Improves" and "is good
# for" say no direction in numbers: a better score can be a higher or a
# lower one.
_CLAIM_UP = re.compile(r"\b(?:causes?|caused|causing|leads? to|gives? you|triggers?|raises?|"
                       r"increases?|boosts?)\b", re.IGNORECASE)
# Which way a figure's own sentence says it moved, for a figure (a
# standardised difference) whose number alone does not say.
_MOVED_UP = re.compile(r"\b(?:increas\w*|rose|rise[sn]?|rising|higher|raised|elevat\w*|gain\w*)\b",
                       re.IGNORECASE)
_MOVED_DOWN = re.compile(r"\b(?:reduc\w*|decreas\w*|lower\w*|fell|fall\w*|drop\w*|declin\w*|"
                         r"less|fewer)\b", re.IGNORECASE)


def claim_direction(claim: str) -> str | None:
    """'up' for a claim that something raises its outcome ("smoking causes
    lung cancer"), 'down' for one that lowers it ("exercise lowers the risk
    of depression"), None when it says neither, both, or is itself a no."""
    claim = str(claim or "")
    if claim_denies(claim):
        return None
    up = bool(evidence.caused_outcome(claim) or _CLAIM_UP.search(claim))
    down = bool(evidence.prevented_outcome(claim) or evidence._PROTECTIVE_VERB.search(claim))
    return "up" if up and not down else "down" if down and not up else None


def figure_direction(effect: dict, said: str) -> str | None:
    """'up' or 'down' for the figure the line would size: a ratio or a
    percentage by the words code wrote beside it ("about 28% lower risk"),
    a standardised difference by its own sentence's change word. None when
    that cannot be told."""
    plain = str((effect or {}).get("plain") or "")
    if re.search(r"\blower\b", plain):
        return "down"
    if re.search(r"\bhigher\b", plain):
        return "up"
    up, down = bool(_MOVED_UP.search(said or "")), bool(_MOVED_DOWN.search(said or ""))
    return "up" if up and not down else "down" if down and not up else None


def agrees_direction(effect: dict, said: str, claim: str) -> bool:
    """True when the figure moves the way the claim says. A claim that
    something raises a risk is not sized by a figure of lower risk."""
    want = claim_direction(claim)
    return want is not None and figure_direction(effect, said) == want


def ground(raw, studies: list[dict], tidy, claim: str = "", skip=(), outcome: str = "",
           surrogate: str = "") -> dict | None:
    """
    Gate whatever the model returned into a breakdown the page can render,
    or None if too little survived to be worth opening.

    `tidy` is verdict.tidy_prose, passed in rather than imported, so this
    module stays free of the provider plumbing and the import stays one
    way round. `claim` is only ever read, never printed: its own words are
    excluded from the reading grade.

    Order matters. Plain words go in before the length rule is applied, so
    a sentence is measured and kept or dropped as the reader will meet it,
    not as the model wrote it.
    """
    if not isinstance(raw, dict):
        return None
    count = len(studies or [])
    if not count:
        return None
    known = corpus(studies)
    kept_terms = set()

    def clean(value, limit):
        said = soften(tidy(str(value or ""), limit))
        before = set(kept_terms)
        out = drop_citing(keep_sentences(plain(said, kept_terms), count, known), skip, count)
        if out:
            return tell_straight(out, studies)
        # Nothing survived. The commonest reason is the length rule, and the
        # commonest reason for that is plain() putting the original term in
        # brackets beside the plain words: "chest and throat infections
        # (acute respiratory infection)" costs four words a reader did not
        # need. The brackets are a courtesy; the sentence is the content. So
        # try once more without them rather than lose the field. The kept set
        # is rewound first, or the term would count as printed on a line the
        # reader never got.
        kept_terms.clear()
        kept_terms.update(before)
        return tell_straight(drop_citing(
            keep_sentences(plain(said, set(PARKED_TERMS)), count, known), skip, count), studies)

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
            # Whether the model judged this part and the gate took the
            # judgement away, as against never judging it at all. Read once,
            # below, then dropped: it is bookkeeping, not content.
            "_dropped": bool(str(item.get("assessment") or "").strip())
                        and not assessment,
        })

    # Up to six paragraphs that survive the gate, not the first six the
    # model wrote. Capping before the gate would let three dropped sentences
    # cost the reader three good ones. Six because the prompt asks for one
    # paragraph each on the biggest study, where the studies disagree, the
    # effect size in plain words, who was studied, and any indirect evidence.
    paragraphs = []
    for para in (raw.get("evidence") or [])[:12]:
        text = clean(para, PARAGRAPH_MAX)
        if text:
            paragraphs.append(text)
        if len(paragraphs) == PARAGRAPHS_MAX:
            break

    # Only now can the dropped judgement be answered honestly, and only when
    # the part is the whole claim: with one part, the paragraphs below are
    # about it by definition. With four, part three could be the one thing
    # nothing here touches, and pointing at the paragraphs would be a guess.
    for p in parts:
        if p.pop("_dropped") and len(parts) == 1 and paragraphs:
            p["assessment"] = PART_BELOW

    effect = clean(raw.get("effect_size"), FIELD_MAX)
    # The size of some other outcome's effect is not this claim's, so the
    # row is left out rather than pointed at a figure above.
    elsewhere = off_outcome(effect, studies, outcome, surrogate)
    if elsewhere:
        effect = ""
    # By here the gate has checked every figure in it against the abstracts.
    # If nothing survived, which line is honest depends on the rest of the
    # layer: "the abstracts don't report it" is only true when no figure
    # reached the page anywhere, and the gate drops sentences one at a time,
    # so the effect-size line can go while an odds ratio two paragraphs up
    # stays. Saying the abstracts carry no figure there would contradict it.
    #
    # Pointing above needs the size of an effect above, not any number: "33
    # reviews covering 17,155,277 people" is a count. And when the model did
    # give a figure here and the gate took it out, the abstracts may well
    # report one, so neither line is true and the row is left out.
    if elsewhere:
        pass
    elif not effect or not _NUMERAL.search(_REF.sub(" ", effect)):
        said = paragraphs + [p["assessment"] for p in parts]
        gave = _NUMERAL.search(_REF.sub(" ", tidy(str(raw.get("effect_size") or ""), FIELD_MAX)))
        effect = (EFFECT_SIZE_ABOVE if any(shows_effect(t) for t in said)
                  else "" if gave else NO_EFFECT_SIZE)

    # The figure, read as a size. The translation is only ever written beside
    # the figure it came from: a ratio in this line translated and parked
    # under a different ratio in that line is a wrong number, not a plainer
    # one. The label for the takeaway may come from a paragraph instead,
    # because a label only ever weakens wording and cannot mis-state a
    # figure the reader never sees.
    sized = evidence.effect_size(effect)
    # A figure its own abstract reports as no clear difference is sized as
    # none, whatever its distance from 1. The sentence say_nulls added under
    # it becomes the translation, so it is picked as one by says(). So is an
    # effect line that itself says there was no clear difference, whether or
    # not a ratio could be read from it: "no drop in viral titer at 30 or 60
    # minutes" is no effect, not a missing size for the line to guess at.
    null_ref = next((r for r in (null_figure(t, studies) for t in sentences(effect))
                     if r is not None), None)
    if (null_ref is not None or _SAID_NULL.search(effect)) and all_null(effect, studies):
        sized = dict(evidence.NULL_EFFECT, figure=(sized or {}).get("figure", ""))
        if null_ref:
            effect = effect.replace(NOT_SIGNIFICANT_SAID.format(ref=null_ref),
                                    f"{TRANSLATION_LEAD}{NULL_SAID} ({null_ref}).")
    labelled = bool(sized) and sized["label"] in effect.lower()
    if sized and sized.get("null"):
        pass    # said above, or by the model; never sized as small
    elif (sized and sized["plain"] not in effect
            and not (labelled and _PERCENT.search(effect))):
        # Arithmetic on a figure that is already cited, so it carries that
        # figure's citation rather than arriving unsourced. Whichever half
        # the model already wrote is not written twice.
        cite = _REF.search(effect)
        where = f" ({cite.group(0)})" if cite else ""
        said = (sized["plain"] if labelled
                else f"{sized['plain']}, a {sized['label']} effect")
        effect = (f"{effect.rstrip().rstrip('.')}. "
                  f"{TRANSLATION_LEAD}{said}{where}.")
    sized = sized or next(
        (e for e in (evidence.effect_size(t) for t in paragraphs
                     if not off_outcome(t, studies, outcome, surrogate)) if e), None)

    out = {
        "parts": parts,
        "evidence": paragraphs,
        "effect_size": effect,
        "strength": clean(raw.get("strength"), FIELD_MAX),
        "applies_to": clean(raw.get("applies_to"), FIELD_MAX),
        "not_applies_to": clean(raw.get("not_applies_to"), FIELD_MAX),
        "unknowns": clean(raw.get("unknowns"), FIELD_MAX),
        # Not rendered. The takeaway is held to this label upstream.
        "effect": sized,
    }

    # A breakdown with no paragraphs and no judged parts is a heading with
    # nothing under it. Better no disclosure than an empty one.
    if not paragraphs and not any(p["studies"] for p in parts):
        return None

    done = set()
    for key in FIELDS:
        if key == "parts":
            for p in out["parts"]:
                if p["assessment"] not in (NO_STUDY_FOR_PART, PART_BELOW):
                    p["assessment"] = gloss_once(p["assessment"], done)
        elif key == "evidence":
            out["evidence"] = [gloss_once(p, done) for p in out["evidence"]]
        elif out[key] and out[key] not in (NO_EFFECT_SIZE, EFFECT_SIZE_ABOVE):
            out[key] = gloss_once(out[key], done)

    # Two measurements, kept with the text they describe: how many distinct
    # records the whole layer rests on (the summary line shows it), and what
    # reading grade the surviving prose came out at.
    out["rests_on"] = len(cited_pmids(out, studies))
    out["grade"] = reading_grade(flatten(out), claim_terms(claim))
    return out


# ---------------------------------------------------------------------
# Rule 10: a figure is rounded for reading, and never loses its unit.
#
# The gate compares digits to digits, so the model is told to copy every
# figure exactly, and "fasting blood sugar fell by 21.929 mg/dL" reached
# the page: three decimals no reader can use. Rounding happens here, after
# the gate has passed the sentence, so the gate still checks the figure the
# abstract printed and the reader gets "about 22 mg/dL". The same pass puts
# back a unit the model dropped, copied from the abstract that printed the
# figure. When the abstract never gave one, the figure is left out rather
# than guessed or printed bare: a review of apple cider vinegar reported
# HbA1c as "-1.53" with no unit at all, and its own table called it mg/dL,
# which HbA1c never is. A reader cannot use a number with no unit, so the
# clause that carries it goes, or its sentence when that is all it says.
# ---------------------------------------------------------------------

# Measures whose figures mean nothing without a unit. Read in the few words
# before the figure, not anywhere in the sentence.
_MEASURE = re.compile(
    r"\b(?:HbA1c|A1c|h(?:a)?emoglobin A1c|glycated h(?:a)?emoglobin|"
    r"glycosylated h(?:a)?emoglobin|(?:fasting\s+)?(?:blood|plasma|serum)\s+"
    r"(?:sugar|glucose)|glucose|FBS|FPG|FBG|blood pressure|systolic|diastolic|"
    r"cholesterol|LDL(?:-C)?|HDL(?:-C)?|triglycerides?|weight|BMI|"
    r"body mass index|waist(?: circumference)?|insulin)\b", re.IGNORECASE)
_HBA1C = re.compile(r"A1c|glycated|glycosylated", re.IGNORECASE)
_UNIT = (r"(?:%|percentage points?|percent|mg/dl|mmol/l|mmol/mol|mmhg|mm hg|"
         r"milligrams? per decilit(?:er|re)|millimoles? per lit(?:er|re)|"
         r"kg/m2|kg/m\u00b2|kg|lbs?|pounds?|cm|inches|g/dl|\u03bcu/ml|\u00b5u/ml|"
         r"mu/l|miu/l|points?|ml|mg|g)(?![a-z])")
_FIGURE_UNIT = re.compile(r"(?<![\w.])([-\u2212]?\d+(?:\.\d+)?)(?![\d])(\s*" + _UNIT + ")?",
                          re.IGNORECASE)
# What may follow a figure that has lost its unit. Anything else after it
# ("4 trials", "319 people", "eight weeks") makes the figure a count.
_AFTER_BARE = re.compile(
    r"\s*(?:$|[).,;:]|\(|(?:in|and|or|among|after|over|for|with|compared|"
    r"versus|vs|than|from|to|at|on|when|while|but|across|between|overall)\b)",
    re.IGNORECASE)
# A figure is a change when it follows "by", "of" or a difference. Only a
# change is held to having a unit; "HbA1c under 7" is a level.
_CHANGE_BEFORE = re.compile(
    r"(?:\bby|\bof|\bW?MD:?|mean difference(?: of)?|difference(?: of)?)\s*(?:about\s+)?$",
    re.IGNORECASE)
# Ratios and standardized differences have no unit, and need none.
_UNITLESS_BEFORE = re.compile(
    r"\b(?:(?-i:[aA]?ORs?|a?RRs?|[aA]?HRs?|a?IRRs?|SMDs?)|ratio|relative risk|standardi[sz]ed|"
    r"Hedges|Cohen|effect size|g)\b[^.\d]{0,12}$", re.IGNORECASE)
_P_BEFORE = re.compile(r"\bp\s*(?:-?value)?\s*(?:=|<|>|\u2264|\u2265|of)\s*$", re.IGNORECASE)
_CI_BEFORE = re.compile(r"\bC\.?I\.?\b|confidence interval|credible interval|\b95%",
                        re.IGNORECASE)
_ABOUT_BEFORE = re.compile(r"(?:\b(?:about|around|roughly|approximately|nearly|almost|"
                           r"some|over|under|more than|less than)|~)\s*$", re.IGNORECASE)
_RANGE_JOIN = re.compile(r"\s*(?:to|and|or|,|-|\u2013)\s*")
# The two ends of one range: "27.19 to 7.44", or "-29.15, -14.71" inside a
# confidence interval. Both ends are printed to the same precision.
# "between 7.44 and 27.19" too, but a bare "and" joins two separate figures.
_PAIR_JOIN = re.compile(r"\s*(?:to|-|\u2013|(,)|(and))\s*")
_PAIR_NEXT = re.compile(r"\s*(?:to|-|\u2013|(,)|(and))\s*([-\u2212]?\d+(?:\.\d+)?)(?![\d])")
_BETWEEN_BEFORE = re.compile(r"\bbetween\s+(?:about\s+)?$", re.IGNORECASE)
_RANGE_UNIT = re.compile(r"\s*(?:to|and|or|-|\u2013)\s*[-\u2212]?\d+(?:\.\d+)?\s*" + _UNIT,
                         re.IGNORECASE)
_UNIT_CASE = {"mg/dl": "mg/dL", "mmol/l": "mmol/L", "mmhg": "mmHg", "mm hg": "mmHg",
              "g/dl": "g/dL", "kg/m2": "kg/m\u00b2", "mmol/mol": "mmol/mol",
              "\u03bcu/ml": "\u03bcU/mL", "\u00b5u/ml": "\u00b5U/mL", "ml": "mL"}
# Where for_reading found a measured change no abstract gave a unit for.
# Never printed: drop_unitless takes the figure out at each one.
_NO_UNIT = "\x00"
# What joins a clause to the one before it, and an interval that belongs to
# the figure just dropped.
_CLAUSE_JOIN = re.compile(r"(?:\s*,\s*and|\s*,|\s*;|\s+and|\s+while|\s+whereas|\s+but)\s+",
                          re.IGNORECASE)
# A figure and its range travel together: the interval goes with the figure
# whether it is in brackets, "(95% CI -2.65, -0.41)", or not: ", 95% CI
# -2.65, -0.41", "with a 95% confidence interval of -2.65 to -0.41".
_CI_END = r"[-−]?\d+(?:\.\d+)?(?:\s*%)?"
_TRAILING_CI = re.compile(
    r"\s*\((?:[^()]*?\b(?:CI|confidence interval)\b)[^()]*\)"
    r"|\s*,?\s*(?:(?:with|and)\s+(?:an?\s+)?)?"
    r"(?:95\s?(?:%|percent|per cent)\s*)?(?:CI|confidence interval|credible interval)s?"
    r"\s*(?:of|from|was|:|=|,)?\s*[\[(]?\s*" + _CI_END + r"\s*(?:,|to|-|–|and)\s*" + _CI_END
    + r"[\])]?",
    re.IGNORECASE)
_FIGURE_WITH_UNIT = re.compile(r"\d\s*" + _UNIT, re.IGNORECASE)
UNITLESS_LEFT_OUT = "The size of this change is given without a unit, so it is left out here."


def _rounded(figure: str):
    """(new figure, whether it is now approximate), or None to leave it."""
    sign = figure[:1] if figure[:1] in "-\u2212" else ""
    whole, _, frac = figure.lstrip("-\u2212").partition(".")
    if not frac:
        return None
    value = Decimal(whole + "." + frac)
    if value >= 10 and len(frac) >= 2:
        return sign + str(value.quantize(Decimal(1), ROUND_HALF_UP)), True
    if len(frac) < 3 or value == 0:
        return None
    if value >= 1:
        return sign + str(value.quantize(Decimal("0.01"), ROUND_HALF_UP)), False
    # Two significant figures: 0.0345 is 0.035, 0.123 is 0.12.
    place = Decimal(1).scaleb(value.adjusted() - 1)
    return sign + format(value.quantize(place, ROUND_HALF_UP), "f"), False


def _to_whole(figure: str) -> bool:
    """Whether _rounded makes this figure a whole number."""
    r = _rounded(figure)
    return bool(r and r[1])


def _model_unit(figure: str, abstracts: str) -> bool:
    """Whether a unit written beside this figure came from the model alone:
    the abstracts print the figure, and never with a unit or as a ratio."""
    bare = re.escape(figure.lstrip("-\u2212"))
    return (_unit_in(figure, abstracts) is None
            and re.search(r"(?<![\d.])" + bare + r"(?![\d])", abstracts) is not None)


def _unit_in(figure: str, abstracts: str) -> str | None:
    """
    The unit an abstract printed straight after this exact figure; "" when
    it printed the figure as a ratio or standardized difference, which has
    none; None when it printed the figure bare, or not at all.
    """
    bare = re.escape(figure.lstrip("-\u2212"))
    for m in re.finditer(r"(?<![\d.])[-\u2212]?\s?" + bare + r"(?![\d])(\s*" + _UNIT + ")?",
                         abstracts, re.IGNORECASE):
        if _UNITLESS_BEFORE.search(abstracts[max(0, m.start() - 20):m.start()]):
            return ""
        if m.group(1):
            return m.group(1).strip()
    return None


# "Healthy" is a description of who was studied, so it has to come from
# the studies: "healthy adults" said of a review of adults with chronic
# insomnia names the wrong people.
_HEALTHY = re.compile(
    r"\bhealthy\s+(?=(?:young\s+|older\s+|adult\s+)?(?:adults?|people|persons?|volunteers?|"
    r"individuals?|men|women|participants?|subjects?|children|kids|adolescents?|teens?|"
    r"teenagers?|infants?|babies|seniors?|elderly)\b)", re.IGNORECASE)
# What a study writes when it means the same thing.
_SAYS_HEALTHY = re.compile(
    r"\bhealthy\b|\bnon-?diabetic|\bwithout (?:any |known )*(?:disease|diabetes|illness|"
    r"comorbidit)|\bdisease-free\b|\bnormoglyc|\bnormotensive\b|\bnormal[- ]weight\b",
    re.IGNORECASE)


def unsaid_healthy(text: str, studies: list[dict] = ()) -> str:
    """
    "Healthy" before the people a sentence describes is taken out when no
    study it cites (or, uncited, no study at all) says "healthy". A study
    with no abstract to read keeps it: absence there proves nothing.
    """
    if not text or not studies or not _HEALTHY.search(text):
        return text or ""
    out, changed = [], False
    for sentence in sentences(text):
        if _HEALTHY.search(sentence):
            numbers = refs(sentence, len(studies)) or range(1, len(studies) + 1)
            cited = [studies[n - 1] for n in numbers]
            if not any(not str(st.get("abstract") or "").strip()
                       or _SAYS_HEALTHY.search(f"{st.get('title') or ''} {st.get('abstract')}")
                       for st in cited):
                sentence = _HEALTHY.sub("", sentence)
                sentence = sentence[:1].upper() + sentence[1:]
                changed = True
        out.append(sentence)
    return " ".join(out) if changed else text


def for_reading(text: str, studies: list[dict] = ()) -> str:
    """
    Round the figures in prose that has passed the gate, and give a measured
    change its unit. "Fasting blood sugar fell by 21.929 mg/dL and HbA1c by
    1.53" becomes "fell by about 22 mg/dL" when no abstract printed a unit
    for 1.53 (drop_unitless), and a change in HbA1c printed in % is said in
    percentage points, which is what it is. Study numbers, p-values, counts
    and years are never touched. "Healthy" no study said goes too.
    """
    text = unsaid_healthy(text, studies)
    if not text or not re.search(r"\d", text):
        return house_style(text or "")
    abstracts = " ".join(str(s.get("abstract") or "") for s in studies or [])
    refs_at = [m.span() for m in _REF.finditer(text)]
    prev_end = [None]
    prev_whole = [False]
    prev_between = [False]

    def fix(m: re.Match) -> str:
        a = m.start(1)
        figure, unit = m.group(1), m.group(2) or ""
        before = text[max(0, a - 40):a]
        gap = text[prev_end[0]:a] if prev_end[0] is not None else None
        joined = gap is not None and bool(_RANGE_JOIN.fullmatch(gap))
        prev_end[0] = m.end()
        was_whole, prev_whole[0] = prev_whole[0], False
        was_between = prev_between[0]
        prev_between[0] = bool(_BETWEEN_BEFORE.search(before))
        if any(x <= a < y for x, y in refs_at) or _P_BEFORE.search(before):
            return m.group(0)
        in_ci = bool(_CI_BEFORE.search(text[max(0, a - 30):a]))
        pair = gap is not None and _PAIR_JOIN.fullmatch(gap)
        nxt = _PAIR_NEXT.match(text, m.end())
        partner_whole = (
            (pair and (in_ci or not pair.group(1)) and (was_between or not pair.group(2))
             and was_whole)
            or (nxt and (in_ci or not nxt.group(1)) and (prev_between[0] or not nxt.group(2))
                and _to_whole(nxt.group(3))))
        change = _CHANGE_BEFORE.search(before)
        near = _MEASURE.search(" ".join(text[max(0, a - 90):a].split()[-9:]))
        note = ""
        if (near and (change or "." in figure) and not in_ci and not joined
                and not _UNITLESS_BEFORE.search(before)):
            hba1c = bool(_HBA1C.search(near.group(0)))
            if unit and _model_unit(figure, abstracts):
                # The abstract printed this figure bare and the model
                # supplied the unit: "HbA1c decreased by 1.53 percent".
                unit, note = "", _NO_UNIT
            elif unit:
                if hba1c and unit.strip() == "%" and change:
                    unit = " percentage points"
            elif _RANGE_UNIT.match(text[m.end():]):
                pass    # "between 12 and 31 mg/dL": the unit comes once, at the end
            elif _AFTER_BARE.match(text[m.end():]):
                found = _unit_in(figure, abstracts)
                if found == "%":
                    note = " percentage points" if hba1c else "%"
                elif found:
                    note = " " + _UNIT_CASE.get(found.lower(), found)
                elif found is None:
                    note = _NO_UNIT
        new = figure
        r = _rounded(figure)
        if (not (r and r[1]) and partner_whole and "." in figure
                and Decimal(figure.lstrip("-\u2212")) >= 1):
            # "-27 to -7.44" reads as two kinds of figure. One range, one precision.
            r = figure[:1].strip("0123456789") + str(
                Decimal(figure.lstrip("-\u2212")).quantize(Decimal(1), ROUND_HALF_UP)), True
        prev_whole[0] = bool(r and r[1])
        if r:
            new, approx = r
            if approx and not in_ci and not joined and not _ABOUT_BEFORE.search(before):
                new = "about " + new
        return new + unit + note

    return house_style(drop_unitless(_FIGURE_UNIT.sub(fix, text)))


def drop_unitless(text: str) -> str:
    """
    Take out each figure for_reading marked as having no unit. When a figure
    with its unit comes earlier in the same sentence, only the clause that
    carries the bare one goes: "fell by about 22 mg/dL and HbA1c by 1.53"
    keeps "fell by about 22 mg/dL". Otherwise the sentence goes. "" when
    nothing is left.
    """
    if _NO_UNIT not in (text or ""):
        return text or ""
    kept = []
    for sent in sentences(text):
        while _NO_UNIT in sent:
            mark = sent.index(_NO_UNIT)
            start = mark
            while start and (sent[start - 1].isdigit() or sent[start - 1] in ".-\u2212"):
                start -= 1
            joins = [m for m in _CLAUSE_JOIN.finditer(sent, 0, start)
                     if _FIGURE_WITH_UNIT.search(sent[:m.start()])]
            if not joins:
                sent = ""
                break
            end = mark + len(_NO_UNIT)
            ci = _TRAILING_CI.match(sent, end)
            if ci:
                end = ci.end()
            sent = sent[:joins[-1].start()] + sent[end:]
        sent = re.sub(r"\s+([.,;)])", r"\1", " ".join(sent.split()))
        if sent:
            kept.append(sent)
    return " ".join(kept)


# ---------------------------------------------------------------------
# Rule 11: house style for numbers, statistics and verbs.
#
# The holdout test printed "17155277 people", "thirteen thousand
# seventy-eight patients", "negative zero point twenty-seven mmol per
# liter", "glucose netiAUC" and "Vaccines successfully lowers". The prompt
# already asks for digits and plain words; these are what got past it.
#
# Two places. Number words become digits in tidy_prose, before the gate,
# so a number the model spelled out is checked against the abstracts like
# any other instead of slipping past the gate in words. The rest is the
# last step of for_reading, after every rule that reads a figure or the
# name of a statistic: calling a standardized mean difference an "effect
# size" any earlier would hide it from the code that sizes it.
# ---------------------------------------------------------------------

_NUMBER_WORD = "|".join(sorted(evidence._NUM_WORDS, key=len, reverse=True))
_SPELLED = re.compile(
    rf"\b(?P<sign>(?:negative|minus)\s+)?"
    rf"(?P<whole>(?:{_NUMBER_WORD})\b(?:(?:-|\s+)(?:{_NUMBER_WORD}|hundred|thousand)\b"
    rf"|(?:(?<=hundred)|(?<=thousand))\s+and\s+(?:{_NUMBER_WORD})\b)*)"
    rf"(?:\s+point\s+(?P<part>(?:{_NUMBER_WORD})\b(?:(?:-|\s+)(?:{_NUMBER_WORD})\b)*))?",
    re.IGNORECASE)
_FRACTION_AFTER = re.compile(r"(?:-|\s+)(?:halves|half|thirds?|quarters?|fourths?|fifths?|"
                             r"sixths?|sevenths?|eighths?|ninths?|tenths?)\b", re.IGNORECASE)
# "one" and "zero" are words more often than numbers: "one of the trials",
# "no one", "zero effect". They become digits only beside another number.
_BESIDE_NUMBER = re.compile(rf"\s+(?:in|to|or|and)\s+(?:\d|(?:{_NUMBER_WORD})\b)", re.IGNORECASE)
_TENS = {20, 30, 40, 50, 60, 70, 80, 90}


def _spelled_value(words: list[str]) -> int | None:
    """The run's value, or None when the words are two numbers side by side."""
    for prev, cur in zip(words, words[1:]):
        if prev in ("hundred", "thousand") or cur in ("hundred", "thousand"):
            continue
        if not (evidence._NUM_WORDS[prev] in _TENS and 0 < evidence._NUM_WORDS[cur] < 10):
            return None
    return evidence._fold(words)


def _spelled_digits(words: list[str]) -> str | None:
    """The digits after a decimal point: "twenty seven" is 27, "zero five" 05."""
    out, i = "", 0
    while i < len(words):
        v = evidence._NUM_WORDS[words[i]]
        if v in _TENS and i + 1 < len(words) and 0 < evidence._NUM_WORDS[words[i + 1]] < 10:
            v += evidence._NUM_WORDS[words[i + 1]]
            i += 1
        out += str(v)
        i += 1
    return out


def digits(text: str) -> str:
    """
    Numbers the model spelled out, written as digits: "forty-two articles"
    is "42 articles", "negative zero point twenty-seven" is "-0.27", "minus
    0.54 kg" is "-0.54 kg". Fractions ("two-thirds") and a lone "one" or
    "zero" are left as words.
    """
    def swap(m: re.Match) -> str:
        if _FRACTION_AFTER.match(m.string, m.end()):
            return m.group(0)
        words = [w for w in re.findall(r"[a-z]+", m.group("whole").lower()) if w != "and"]
        value = _spelled_value(words)
        if value is None:
            return m.group(0)
        part = m.group("part")
        if (len(words) == 1 and words[0] in ("one", "zero") and not part and not m.group("sign")
                and not _BESIDE_NUMBER.match(m.string, m.end())
                and not re.search(rf"(?:\d|\b(?:{_NUMBER_WORD}))\s+(?:in|to|or|and)\s+$",
                                  m.string[:m.start()], re.IGNORECASE)):
            return m.group(0)
        out = str(value)
        if part:
            out += "." + _spelled_digits(re.findall(r"[a-z]+", part.lower()))
        return ("-" if m.group("sign") else "") + out

    t = _SPELLED.sub(swap, text or "")
    return re.sub(r"\b(?:minus|negative)\s+(?=\d)", "-", t, flags=re.IGNORECASE)


# A year is not a count. Between 1800 and 2100 a four-digit number takes
# a comma only when what follows says it was counted.
_COUNTED_AFTER = re.compile(
    r"\s*(?:people|persons|participants|patients|adults|children|kids|infants|women|men|"
    r"subjects|individuals|volunteers|respondents|students|workers|athletes|smokers|users|"
    r"cases|deaths|births|pregnancies|steps|calories|kcal|IU|mcg|µg|mg|g|kg|ml)\b",
    re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<![\w.,])(\d{4,})(?!\d)")


def thousands(text: str) -> str:
    """17155277 is 17,155,277. Years, PMIDs and decimals are left alone."""
    def group(m: re.Match) -> str:
        n = int(m.group(1))
        if re.search(r"PMID:?\s*$", m.string[:m.start()], re.IGNORECASE):
            return m.group(0)
        if len(m.group(1)) == 4 and 1800 <= n <= 2100 and not _COUNTED_AFTER.match(m.string, m.end()):
            return m.group(0)
        return f"{n:,}"

    return _LONG_NUMBER.sub(group, text or "")


def _total_rise(m: re.Match) -> str:
    named = " ".join(g for g in m.groups() if g).lower()
    near = m.string[max(0, m.start() - 80):m.end() + 80].lower()
    what = ("insulin" if "insulin" in named
            else "blood sugar" if named or re.search(r"glucose|sugar|glyc", near)
            else None)
    said = f"total rise in {what}" if what else "total rise after eating"
    # "a lower iAUC" already has its article; "lowers iAUC" needs one.
    before = m.string[max(0, m.start() - 30):m.start()]
    if not re.search(r"\b(?:a|an|the|its|their)\s+(?:[\w-]+\s+)?$", before, re.IGNORECASE):
        said = "the " + said
    return said


# Statistics with a plain name. Each keeps its figure and its meaning: an
# incremental area under the glucose curve is the total rise in blood sugar
# after eating, and a weighted mean difference is the average difference
# between the groups, weighted only in how the review added the trials up.
JARGON = [
    (r"\b(?:the\s+)?(?:(?:postprandial\s+)?(?:blood\s+|plasma\s+)?(glucose|sugar|insulin)\s+)?"
     r"(?:net\s*)?(?:iAUC|incremental\s+(?:AUC|area\s+under\s+the(?:\s+(?:glucose|insulin))?\s+curve)"
     r"(?:\s*\(i?AUC\))?)(?:\s+(?:for|of)\s+(?:blood\s+|plasma\s+)?(glucose|sugar|insulin))?",
     _total_rise),
    (r"\bstandardi[sz]ed\s+mean\s+difference(s?)(?:\s*\(SMDs?\))?", r"effect size\1"),
    (r"\bSMD(s?)\b(?=:?\s*(?:of\s+|=\s*)?[-−]?\d)", r"effect size\1"),
    (r"\b(?:weighted\s+|pooled\s+)?mean\s+difference(s?)(?:\s*\(W?MDs?\))?", r"average difference\1"),
    (r"\bW?MD(s?)\b(?=:?\s*(?:of\s+|=\s*)?[-−]?\d)", r"average difference\1"),
    (r"\b95\s?%\s*CIs?\b", "likely range"),
    # The gloss gloss_once() gave "confidence interval" goes with it: the
    # plain name says the same thing.
    (r"\b(?:95\s?(?:%|percent|per cent)\s*)?confidence\s+interval(s?)"
     r"(?:\s*\((?:CIs?|the range the real answer is probably in)\))?", r"likely range\1"),
    (r"\bHedges'?\s*g\b|\bCohen'?s\s*d\b|(?<=\b[Aa]\s)g\b(?=\s*(?:of|=|:)\s*[-−]?\d)",
     "effect size"),
    (r"\bpostprandial\s+(?:blood\s+|plasma\s+)?(?:glucose|sugar)\b", "blood sugar after meals"),
    (r"\bpostprandial\b", "after-meal"),
    (r"\bfasting\s+(?:blood\s+|plasma\s+)?glucose\b", "fasting blood sugar"),
    (r"\b(?:blood|plasma)\s+glucose\b", "blood sugar"),
]
_JARGON = [(re.compile(p, re.IGNORECASE), r) for p, r in JARGON]


def unjargon(text: str) -> str:
    """Name each statistic in plain words, keeping its figure."""
    out = text or ""
    for pattern, plain_words in _JARGON:
        out = pattern.sub(lambda m, r=plain_words: _open_case(
            m, r(m) if callable(r) else m.expand(r)), out)
    return _fix_articles(out)


def _open_case(m: re.Match, said: str) -> str:
    """A capital only where the sentence opens: "an SMD of" and "a Hedges'
    g of" are capitals of their own, not of the sentence."""
    before = m.string[:m.start()].rstrip()
    return _recase(m.group(0), said) if not before or before[-1] in ".!?" else said


# Subject and verb. Only the patterns that cannot misfire are fixed: a
# plural subject that opens its clause and is followed straight by its
# verb, a singular study word the same way, and a study reference. "Eating
# eggs raises" is right (the subject is the eating), so a subject with an
# -ing word in it is never touched, and nor is one with a preposition in
# it, where the noun beside the verb is not the subject at all.
_AGREE_BASE = ("show suggest find indicate support report confirm link lower reduce raise "
               "increase improve help cause need seem appear remain lead make give offer "
               "affect decrease slow boost protect prevent work point tend shorten harm worsen").split()
_IRREGULAR = {"have": "has", "do": "does", "are": "is", "were": "was"}


def _s_form(verb: str) -> str:
    low = verb.lower()
    if low in _IRREGULAR:
        out = _IRREGULAR[low]
    elif re.search(r"[^aeiou]y$", low):
        out = low[:-1] + "ies"
    elif re.search(r"(?:s|x|z|ch|sh|o)$", low):
        out = low + "es"
    else:
        out = low + "s"
    return _recase(verb, out)


def _base_form(verb: str) -> str:
    low = verb.lower()
    back = {v: k for k, v in _IRREGULAR.items()}
    if low in back:
        out = back[low]
    elif low.endswith("ies"):
        out = low[:-3] + "y"
    elif re.search(r"(?:sses|xes|zes|ches|shes|oes)$", low):
        out = low[:-2]
    else:
        out = low[:-1]
    return _recase(verb, out)


# A singular subject is only corrected to verbs that cannot be nouns. "The
# study report shows" and "the research support for it" are compounds, and
# turning "report" into "reports" there would break a sentence that was right.
_AGREE_SURE = "show suggest find indicate confirm seem appear remain".split()
_S_VERBS = "|".join(sorted({_s_form(v) for v in _AGREE_BASE} | {"cuts", "hurts", "has", "does",
                                                                "is", "was"}, key=len, reverse=True))
_BASE_VERBS = "|".join(sorted(_AGREE_SURE + list(_IRREGULAR), key=len, reverse=True))
# Two verbs in a row means the first was a noun: "trial results show".
_VERB_NEXT = re.compile(rf"\s+(?:{_S_VERBS}|{_BASE_VERBS}|had|did|\w+ed)\b", re.IGNORECASE)
_CLAUSE_START = r"(?:^|(?<=[.!?]\s)|(?<=\bthat\s)|(?<=\bbecause\s))"
_ADVERB = r"(?P<adv>(?:(?:also|still|often|\w+ly)\s+)?)"
_NOT_SUBJECT = frozenset("""of in on at by for from with without among across about like than
    after before during between into over under within per versus vs and or but nor that which
    who whose where when while if whether because since although though as how why what to not
    never no does did do can could will would should may might must let found showed reported
    is are was were has have had""".split())
_PLURAL_HEAD = frozenset("""studies trials reviews analyses results findings researchers authors
    people patients participants adults children kids women men users vaccines supplements
    foods drinks they""".split())
_PLURAL_DET = frozenset("""these those both many most several multiple few all other some two
    three four five six seven eight nine ten""".split())
_NOT_PLURAL = frozenset("""this thus its his hers theirs ours yours whereas perhaps always
    sometimes besides as yes does goes has was is us diabetes herpes measles mumps rickets rabies
    scabies shingles news series species means lens gas bias atlas canvas molasses pilates aids
    sars mers covid""".split())
_SINGULAR_HEAD = ("study|trial|review|analysis|meta-analysis|evidence|research|survey|paper|"
                  "cohort|experiment")
_PLURAL_SUBJECT = re.compile(
    rf"{_CLAUSE_START}(?P<np>(?:[A-Za-z0-9][\w'-]*\s+){{0,3}}?)"
    rf"(?P<head>[A-Za-z][\w-]*s|people|children|women|men)\s+{_ADVERB}(?P<verb>{_S_VERBS})\b")
_SINGULAR_SUBJECT = re.compile(
    rf"{_CLAUSE_START}(?P<np>(?:[A-Za-z0-9][\w'-]*\s+){{0,3}}?)"
    rf"(?P<head>{_SINGULAR_HEAD})\s+{_ADVERB}(?P<verb>{_BASE_VERBS})\b", re.IGNORECASE)
_STUDIES_REF = re.compile(
    rf"(?<![(\w])(?P<np>Studies\s+\d+(?:\s*,\s*\d+)*(?:,?\s+and\s+\d+)?)\s+{_ADVERB}"
    rf"(?P<verb>{_S_VERBS})\b")
_STUDY_REF = re.compile(
    rf"(?<![(\w])(?P<np>Study\s+\d+)\s+{_ADVERB}(?P<verb>{_BASE_VERBS})\b")


def _np_words(np: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[\w'-]+", np)]


def agree(text: str) -> str:
    """Fix a verb that does not agree with its subject, where that is sure."""
    def to_base(m: re.Match) -> str:
        words = _np_words(m.group("np"))
        head = m.group("head").lower()
        if (head in _NOT_PLURAL or _VERB_NEXT.match(m.string, m.end())
                or any(w in _NOT_SUBJECT or w.endswith("ing") for w in words)):
            return m.group(0)
        # "8 glasses is just a rough guide" is one amount, and reads right.
        if any(re.fullmatch(r"[\d,.]+", w) for w in words) and head not in _PLURAL_HEAD:
            return m.group(0)
        if not (head in _PLURAL_HEAD or any(w in _PLURAL_DET for w in words)
                or not re.search(r"(?:ss|us|is|ics|ous|ness)$", head)):
            return m.group(0)
        return m.group(0)[:m.start("verb") - m.start()] + _base_form(m.group("verb"))

    def to_s(m: re.Match) -> str:
        words = _np_words(m.group("np"))
        if (_VERB_NEXT.match(m.string, m.end())
                or any(w in _NOT_SUBJECT or w in _PLURAL_DET for w in words)):
            return m.group(0)
        return m.group(0)[:m.start("verb") - m.start()] + _s_form(m.group("verb"))

    def ref_base(m: re.Match) -> str:
        return m.group(0)[:m.start("verb") - m.start()] + _base_form(m.group("verb"))

    def ref_s(m: re.Match) -> str:
        if re.search(r"(?:\band|\bor|,)\s*$", m.string[:m.start()]):
            return m.group(0)
        return m.group(0)[:m.start("verb") - m.start()] + _s_form(m.group("verb"))

    out = _PLURAL_SUBJECT.sub(to_base, text or "")
    out = _SINGULAR_SUBJECT.sub(to_s, out)
    out = _STUDIES_REF.sub(ref_base, out)
    return _STUDY_REF.sub(ref_s, out)


# A word said twice running, where a plain name met the word already
# beside it: "a large pooled meta-analysis" became "pooled pooled review".
_SAID_TWICE = re.compile(r"\b(\w{4,})\s+\1\b", re.IGNORECASE)


# "A drop of -5.31 mm Hg" says the fall twice, once in the word and once in
# the sign, and read literally it is a rise. After a word that already says
# down, the minus goes, and so does the minus on the figure it is compared to.
_FALL_BY = re.compile(
    r"\b(?:drop|fall|decrease|reduction|decline|loss|lowering|cut|"
    r"dropped|fell|decreased|reduced|lowered|declined)"
    r"(?:\s+(?:in|of)\s+[^,.;:()\d]{1,60}?)?\s+(?:of|by)\s+"
    r"(?:about\s+|around\s+|roughly\s+|nearly\s+|almost\s+)?([-\u2212])?(?=\d)", re.IGNORECASE)
_COMPARED_SIGN = re.compile(r"(\b(?:compared (?:to|with)|versus|vs\.?)\s+)[-\u2212](?=\d)",
                            re.IGNORECASE)


# The bracket straight after it, when every figure in it is negative: the
# range of the same fall.
_RANGE_AFTER = re.compile(r"^(\s*[^()\d]{0,30}?\()([^()]*)\)")
_SIGNED = re.compile(r"[-\u2212](?=\d)")


def _unsign_range(rest: str) -> str | None:
    """The text after the figure, its range unsigned; None when the range
    crosses zero, because then the signs carry the meaning and stay."""
    m = _RANGE_AFTER.match(rest)
    if not m or re.match(r"\s*Stud(?:y|ies)\b", m.group(2)):
        return rest
    inside = re.sub(r"\b9[059]\s?(?:%|percent\b|per cent\b)", "", m.group(2))
    numbers = re.findall(r"[-\u2212]?\d+(?:\.\d+)?", re.sub(r"\bp\s*[<=>]\s*[\d.]+", "", inside))
    if not numbers:
        return rest
    if not all(n[0] in "-\u2212" for n in numbers):
        return None
    return m.group(1) + _SIGNED.sub("", m.group(2)) + ")" + rest[m.end():]


def _unsign_falls(text: str) -> str:
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        m = _FALL_BY.search(sentence)
        if m:
            # "Dropped by about 22 mg/dL (likely range -29 to -15)": the
            # figure is already unsigned, and its range follows it.
            if m.group(1):
                head, rest = sentence[:m.start(1)], sentence[m.start(1) + 1:]
            else:
                head, rest = sentence[:m.end()], sentence[m.end():]
            figure = re.match(r"[\d.,]+\s*[^\s(]*", rest)
            cut = figure.end() if figure else 0
            after = _unsign_range(rest[cut:])
            if after is not None:
                sentence = _COMPARED_SIGN.sub(r"\1", head + rest[:cut] + after)
        out.append(sentence)
    return " ".join(out)


def house_style(text: str) -> str:
    """The last pass over prose a reader sees: commas, plain statistics, agreement."""
    return _SAID_TWICE.sub(r"\1", agree(unjargon(_unsign_falls(thousands(text or "")))))


# The effect line points at figures above it only when one is there. A
# count of people or trials is a number, not the size of an effect.
_BY_FIGURE = re.compile(r"\bby\s+(?:about\s+|around\s+|roughly\s+|nearly\s+|almost\s+|over\s+|"
                        r"more than\s+)?[-−]?\d", re.IGNORECASE)
_OUTCOME_FIGURE = re.compile(r"\d\s*(?:%|percent\b|per cent\b|percentage points?\b|mg/dl|"
                             r"mmol/l|mmol/mol|mm ?hg|kg/m2|kg\b|lbs?\b|pounds?\b|cm\b|"
                             r"inches\b|points?\b)", re.IGNORECASE)
_CHANGE_WORD = re.compile(r"\b(?:increas|decreas|reduc|lower|rais|rose|ris|fell|fall|drop|declin|"
                          r"improv|gain|los[ts]|differ|chang|higher|shorter|longer|less|more|"
                          r"cut|shrank|grew|slow)\w*", re.IGNORECASE)
_NAMED_SIZE = re.compile(r"\b(?:effect size|average difference)\b[^.]{0,24}?[-−]?\d",
                         re.IGNORECASE)


def shows_effect(text: str) -> bool:
    """Whether this prose gives the size of an effect, not just a count."""
    for s in sentences(text or ""):
        bare = _REF.sub(" ", s)
        if (evidence.effect_size(bare) or _BY_FIGURE.search(bare) or _NAMED_SIZE.search(bare)
                or (_OUTCOME_FIGURE.search(bare) and _CHANGE_WORD.search(bare))):
            return True
    return False


PROSE_FIELDS = ("effect_size", "strength", "applies_to", "not_applies_to", "unknowns")


def prose_of(bd: dict | None) -> list[str]:
    """Every string of prose the breakdown prints, the quoted conclusions
    excepted (those are the authors' own words)."""
    if not bd:
        return []
    out = list(bd.get("says") or []) + list(bd.get("evidence") or [])
    out += [bd.get(k) or "" for k in PROSE_FIELDS]
    out += [p.get("assessment") or "" for p in bd.get("parts") or []]
    return [t for t in out if t]


def map_prose(bd: dict | None, fn) -> dict | None:
    """
    The breakdown with `fn` applied to every string of prose it prints
    (prose_of). A list item or part that `fn` empties goes; a part whose
    answer went points at the paragraphs below when there are any, as the
    gate does; an effect row left pointing at nothing goes (settle_effect).
    """
    if not bd:
        return bd
    out = dict(bd)
    out["says"] = [x for x in (fn(t) for t in bd.get("says") or []) if x]
    out["evidence"] = [x for x in (fn(t) for t in bd.get("evidence") or []) if x]
    for k in PROSE_FIELDS:
        if bd.get(k):
            out[k] = fn(bd[k])
    parts = []
    for p in bd.get("parts") or []:
        p = {**p, "assessment": fn(p.get("assessment") or "")}
        if not p["assessment"] and out["evidence"]:
            p["assessment"] = PART_BELOW
        if p["assessment"]:
            parts.append(p)
    out["parts"] = parts
    return settle_effect(out)


def settle_effect(bd: dict | None) -> dict | None:
    """
    Take the effect line out when it points at figures that are no longer
    above it: rounding, the faithfulness check or the unit rule can remove
    the last one after the line was written. The effect's size goes too
    when the ratio it was read from is printed nowhere.
    """
    if not bd:
        return bd
    said = list(bd.get("evidence") or []) + [p.get("assessment") or "" for p in bd.get("parts") or []]
    # The size the evidence line words ("a large effect") stands on a ratio
    # only when the page prints a ratio of that kind. One saved from a misread
    # ("35 people or 80 adults" read as an odds ratio of 80) is no finding.
    # An effect line that says no clear difference still means none.
    kind = evidence.ratio_kind((bd.get("effect") or {}).get("figure") or "")
    if kind and not any(kind in evidence.ratio_kinds(t) for t in [bd.get("effect_size") or ""] + said):
        bd = {**bd, "effect": (dict(evidence.NULL_EFFECT, figure="")
                               if _SAID_NULL.search(bd.get("effect_size") or "")
                               and all_null(bd.get("effect_size") or "") else None)}
    if bd.get("effect_size") != EFFECT_SIZE_ABOVE:
        return bd
    return bd if any(shows_effect(t) for t in said) else {**bd, "effect_size": ""}


# ---------------------------------------------------------------------
# What the research says
#
# The block between the takeaway and the still-open line, always visible.
# Three to five sentences, and not one of them is new: every sentence here
# has already passed the gate, either as part of the explanation or as part
# of the breakdown below. They are picked, not written, so this costs no
# model call and cannot say anything the deeper layer does not.
#
# The order is the order a reader asks in: what the studies found, how big
# the effect was in plain words, and who it was found in.
# ---------------------------------------------------------------------

SAYS_MIN = 3
SAYS_MAX = 5


def contents(bd: dict) -> str:
    """
    The line under "Read the full breakdown": what is folded inside, so a
    reader can tell it is worth opening. Names only sections this breakdown
    has, at most four, the most particular first.
    """
    have = [name for name, there in (
        ("the claim part by part", bd.get("parts")),
        ("each study's own conclusion", bd.get("concluded")),
        ("how big the effect is", bd.get("effect_size")),
        ("who it applies to", bd.get("applies_to") or bd.get("not_applies_to")
         or bd.get("populations")),
        ("what is still unknown", bd.get("unknowns")),
        ("why the verdict is what it is", bd.get("strength")),
    ) if there][:4]
    if not have:
        return ""
    line = have[0] if len(have) == 1 else ", ".join(have[:-1]) + " and " + have[-1]
    return line[0].upper() + line[1:] + "."


WHO_LABEL = "Who was studied: "
# Words that open a population line and are not names, so they drop their
# capital after the label. Anything else ("HIV", "Japanese") keeps it.
_COMMON_OPENERS = set("""a adults all an athletes both children elderly healthy
individuals infants men middle-aged mostly obese older only overweight participants
patients people postmenopausal pregnant students studies subjects teenagers the
these those trials volunteers women young younger adolescents mainly most
general office desk-based sedentary community-dwelling hospitalized hospitalised
employees workers smokers nonsmokers boys girls newborns toddlers residents
outpatients inpatients runners mixed various diverse groups persons populations
""".split())

# An ending that says the label again: "Office workers were included in these
# studies" under "Who was studied:".
_WHO_TAIL = re.compile(
    r"(?:\s+(?:were|was)\s+(?:studied|included|tested|enrolled|examined|recruited|involved)"
    r"(?:\s+in\s+(?:these|this|the(?: cited| included)?)\s+(?:studies|study|trials|trial|reviews|review))?"
    r"|\s+(?:studied|tested|included)\s+in\s+(?:these|this|the(?: cited| included)?)\s+"
    r"(?:studies|study|trials|trial|reviews|review))"
    r"(?=\s*(?:\([^()]*\))?\s*\.?\s*$)", re.IGNORECASE)


# A lead-in that says again what the label says: "These findings apply to
# adults with..." under "Who was studied:" is the same words twice.
_WHO_LEAD = re.compile(
    r"^(?:(?:these|the|all|most) (?:findings|results|studies|trials|data|reviews)\s+"
    r"(?:apply to|were (?:done |run |carried out |conducted )?(?:in|on|among|with)|"
    r"included|involved|studied|enrolled|looked at|focused on|covered)|"
    r"(?:participants|subjects|the people studied|those studied) were|"
    r"(?:studies|trials) (?:were )?(?:in|on|among|included|involved|enrolled))\s+",
    re.IGNORECASE)


def who_label(sentence: str) -> str:
    """'Adults with type 2 diabetes (Study 1).' -> 'Who was studied: adults
    with type 2 diabetes (Study 1).'"""
    text = str(sentence or "").strip()
    if not text or text.lower().startswith(WHO_LABEL.strip().lower()):
        return text
    bare = _WHO_TAIL.sub("", _WHO_LEAD.sub("", text, count=1), count=1)
    if bare != text and bare:
        text = bare[0].upper() + bare[1:]
    first = text.split(None, 1)[0]
    if first.lower().strip(",.:;") in _COMMON_OPENERS:
        text = text[0].lower() + text[1:]
    return WHO_LABEL + text


def says(explanation: str, bd: dict, studies: list[dict], skip=()) -> list[str]:
    # `skip` is the study numbers that are background only (animal and lab
    # studies, for a claim about people). A sentence resting on nothing else
    # is not what the research says about the claim, so it is not picked.
    count = len(studies or [])
    if not count or not isinstance(bd, dict):
        return []
    known = corpus(studies)
    out, seen = [], set()

    def take(text, most, gated=True):
        # `gated` text has already been through keep_sentences on its way out
        # of ground(), so it is not put through again. That matters for one
        # sentence: the translated effect size is arithmetic this code did on
        # a figure that is in an abstract, and its own percentage is not, so
        # re-gating it would delete the plainest sentence in the block. What
        # is still required of it is a citation, which is also what keeps the
        # code-authored lines ("No study in this set tests this part") out.
        got = 0
        source = (str(text or "") if gated
                  else keep_sentences(str(text or ""), count, known))
        for sentence in sentences(source):
            if got >= most or len(out) >= SAYS_MAX:
                return
            if not refs(sentence, count) or not drop_citing(sentence, skip, count):
                continue
            # The same sentence can reach here twice, because a paragraph of
            # the breakdown and the explanation often make the same point.
            key = re.sub(r"[^a-z0-9]+", "", sentence.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            out.append(sentence)
            got += 1

    take(explanation, 2, gated=False)
    # The effect size takes two, because the figure and the plain words for it
    # are two sentences and the second one is the point. They are picked as a
    # pair rather than as the first two: the translation is written last in the
    # field, and the figure it was computed from is named in bd["effect"], so a
    # second unrelated figure in between must not come between them.
    said = sentences(str(bd.get("effect_size") or ""))
    figure = re.search(r"\d+(?:\.\d+)?",
                       ((bd.get("effect") or {}).get("figure") or ""))
    pair = [line for line in said if figure and figure.group(0) in line][:1]
    plain_at = next((i for i, line in enumerate(said) if line.startswith(TRANSLATION_LEAD)), None)
    # With no figure named (a null read from a mean difference), the
    # translation is taken with the sentence it follows, never alone: alone
    # it lands under another study's figure.
    if not pair and plain_at:
        pair = [said[plain_at - 1]]
    pair += [said[plain_at]] if plain_at is not None else []
    take(" ".join(pair) if pair else bd.get("effect_size"), 2)
    # Who was studied is often written as a bare noun phrase ("Adults with
    # type 2 diabetes (Study 1)."), which under a heading about what the
    # research says reads as a stray fragment, so it carries its label.
    before = len(out)
    take(bd.get("applies_to"), 1)
    if len(out) > before:
        out[-1] = who_label(out[-1])
    # Still thin: the explanation was mostly dropped, or there was no effect
    # size to report. The paragraphs underneath are the same material.
    for para in bd.get("evidence") or []:
        if len(out) >= SAYS_MIN:
            break
        take(para, 2)
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


# ---------------------------------------------------------------------
# One figure, once (E4).
#
# The audit found "about 22 mg per dL" five times on one answer. A later
# sentence goes only when it says nothing new: it cites the same study or
# studies as an earlier kept sentence, every number with its unit in it
# appears in that sentence, and so does every content word (stop words and
# the citation aside; fell, dropped, decreased, declined, lowered and
# reduced count as one word, rose and increased as one), in the same order
# (its words and numbers are a subsequence of that sentence's). Only the
# explanation and the figure lists ("says", "evidence", "effect_size") are
# read: never still_open, unknowns, safety lines, harms, assessments or
# titles. No item or field is ever left empty: its sentences then stay, and
# the earlier sentence may go instead (_drop_earlier). A "That is ..."
# sentence stays or goes with the sentence before it.
# ---------------------------------------------------------------------

_E4_FIGURE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(%|[A-Za-z]+)?")
# The figure lists, in the order the page prints them (display.breakdown_view).
FIGURE_ORDER = ("says", "evidence", "effect_size")
_SAME_WORD = {w: "fell" for w in ("fell", "dropped", "decreased", "declined", "lowered", "reduced",
                                  "fall", "drop", "decrease", "decline", "reduction")}
_SAME_WORD.update({w: "rose" for w in ("rose", "increased", "rise", "increase")})
_STOP_WORDS = frozenset(
    "a an the of in on at by to for with and or was were is are be been being that this these those it its "
    "as from than per into over after during their there which who whom".split())
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def figures_of(sentence: str, count: int) -> set:
    """(number, the word after it, the studies cited) for each figure in a
    sentence, citations themselves left out."""
    cites = frozenset(refs(sentence, count))
    text = _REF.sub(" ", sentence or "")
    out = set()
    for m in _E4_FIGURE.finditer(text):
        number = m.group(1).replace(",", "")
        unit = (m.group(2) or "").lower()
        unit = {"percent": "%", "per": ""}.get(unit, unit)
        out.add((number, unit, cites))
    return out


def content_words(sentence: str) -> set:
    """The words of a sentence that carry meaning, the citation left out and
    the verbs of a fall, or of a rise, read as one."""
    text = _REF.sub(" ", str(sentence or "")).lower()
    return {_SAME_WORD.get(w, w) for w in _WORD.findall(text) if w not in _STOP_WORDS}


_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+(?:'[a-z]+)?")


def word_order(sentence: str) -> list:
    """The content words and numbers of a sentence, in the order written
    (content_words' reading of each word), the citation left out."""
    text = _REF.sub(" ", str(sentence or "")).lower()
    out = []
    for tok in _TOKEN.findall(text):
        if tok[0].isdigit():
            out.append(tok.replace(",", ""))
        elif tok not in _STOP_WORDS:
            out.append(_SAME_WORD.get(tok, tok))
    return out


def _in_order(part: list, whole: list) -> bool:
    """Whether `part` is a subsequence of `whole`."""
    it = iter(whole)
    return all(any(x == y for y in it) for x in part)


# A sentence that says whether a result was statistically significant,
# clear or uncertain, or how certain the evidence is, is never dropped as a
# repeat (nor the figure it belongs to). The one recogniser for both rules
# (brief E4 D2); certainty, chance, "inconclusive" and "no difference" joined
# it in brief E5 F1, then any word with "significan", "did not differ",
# "similar between groups", intervals and CI, "certain", confidence and
# evidence quality. Every use only ever keeps more sentences.
_STATES_SIGNIFICANCE = re.compile(
    r"\b(?:statistically significant|not significant|\w*significan\w*|no clear|clear (?:difference|effect)|"
    r"confidence intervals?|intervals?|(?-i:CI)\b|cross(?:ed|es|ing)? (?:zero|one|the line of no effect)|"
    r"p[ -]?values?|p\s*[<=>]|uncertain\w*|imprecis\w*|certain\w*|confidence|"
    r"(?:very low|low|moderate|high)[ -]quality|quality of (?:the )?evidence|chance|inconclusive|"
    r"no differences?|did not differ|similar between (?:the )?groups)", re.IGNORECASE)


def _says_nothing_new(sentence: str, kept: list, count: int) -> bool:
    """Whether an earlier kept sentence already says all of this one."""
    cites = frozenset(refs(sentence, count))
    figs = {(n, u) for n, u, _ in figures_of(sentence, count)}
    if not cites or not figs:
        return False
    words = content_words(sentence)
    order = word_order(sentence)
    for earlier in kept:
        # Order counts too (brief E3 N11): "rose by 3% with the drug and fell
        # by 2% with placebo" is not repeated by its reverse.
        if (frozenset(refs(earlier, count)) == cites
                and figs <= {(n, u) for n, u, _ in figures_of(earlier, count)}
                and words <= content_words(earlier)
                and _in_order(order, word_order(earlier))):
            return True
    return False


def one_figure_once(explanation: str, bd, count: int):
    """(explanation, breakdown) with every sentence dropped that says nothing
    an earlier kept one did not, in the page's reading order. The
    explanation leads when the breakdown has no "says" (the page prints it
    there); beside a "says" it is read within itself only."""
    def keep(text, kept):
        # A sentence and the "That ..." translations after it are one unit:
        # they stay or go together, and a unit that says whether a result
        # was significant or clear always stays.
        units = []
        for s in sentences(text or ""):
            if s.startswith("That ") and units:
                units[-1].append(s)
            else:
                units.append([s])
        out = []
        for unit in units:
            if (any(_STATES_SIGNIFICANCE.search(s) for s in unit)
                    or not _says_nothing_new(unit[0], kept + out, count)):
                out.extend(unit)
        kept.extend(out)
        return " ".join(out)

    forced = []  # the lists and fields kept whole because the rule would empty them

    def keep_field(text, kept, key=None):
        # A field is never left empty: its sentences then stay.
        said = keep(text, kept)
        if said or not str(text or "").strip():
            return said
        if key:
            forced.append(key)
        return text

    has_says = isinstance(bd, dict) and any(str(x or "").strip() for x in bd.get("says") or [])
    seen = []
    explanation = keep_field(explanation, seen if not has_says else [])
    if not isinstance(bd, dict):
        return explanation, bd
    out = dict(bd)
    for key in FIGURE_ORDER:
        value = out.get(key)
        if key == "says" and isinstance(value, list):
            # The who-line is printed under "Who and what this applies to",
            # not here, so it is left as it is.
            items = [v if str(v or "").lower().startswith(WHO_LABEL.strip().lower()) else keep(v, seen)
                     for v in value]
        elif isinstance(value, list):
            items = [keep(v, seen) if isinstance(v, str) else v for v in value]
        else:
            if isinstance(value, str) and value:
                out[key] = keep_field(value, seen, key)
            continue
        # A list is never left empty: it then stays as it was.
        items = [v for v in items if v]
        if not items and any(value):
            forced.append(key)
        out[key] = items if items or not any(value) else value
    return explanation, _drop_earlier(out, forced, count)


# The earlier sentence goes instead (brief E3 follow-up). When a later
# sentence is a repeat by the rule above but removing it would empty its list
# or field, the earlier one goes, if: it is in a different figure list that
# keeps another sentence; it is not tied to a "That ..." translation right
# after it, and no sentence in its list about the same studies (or none)
# says whether a result was significant or how certain it is (brief E4 D2,
# E5 F1: then both stay); it
# cites the same studies; and all it carries is in
# the later sentence, in the same order, these generic words counting as
# nothing. No other word may join this list: no named population, outcome,
# dose, time, harm, safety or significance word.
GENERIC_WORDS = frozenset(
    "patients people participants subjects group groups about approximately around roughly average mean "
    "overall level levels".split())
_SAFETY = re.compile(r"\b(?:safe\w*|unsafe)\b", re.IGNORECASE)


def _units(text: str) -> list:
    """The sentences of a text, each with the "That ..." sentences after it."""
    units = []
    for x in sentences(text or ""):
        if x.startswith("That ") and units:
            units[-1].append(x)
        else:
            units.append([x])
    return units


def _carried_by(earlier: list, later: list, count: int) -> bool:
    """Whether everything the earlier unit says is in the later one, the
    generic words aside, in the same order."""
    e, l = " ".join(earlier), " ".join(later)
    cites = frozenset(refs(e, count))
    if not cites or cites != frozenset(refs(l, count)):
        return False
    if not {(n, u) for n, u, _ in figures_of(e, count)} <= {(n, u) for n, u, _ in figures_of(l, count)}:
        return False
    if not content_words(e) - GENERIC_WORDS <= content_words(l):
        return False
    return _in_order([w for w in word_order(e) if w not in GENERIC_WORDS], word_order(l))


def _next_sentence(items: list, i: int, j: int, units: list) -> str:
    """The sentence right after unit j of item i in the same list, or ""."""
    if j + 1 < len(units):
        return units[j + 1][0]
    for nxt in items[i + 1:]:
        if isinstance(nxt, str) and sentences(nxt):
            return sentences(nxt)[0]
    return ""


def _figure_bound(sentence: str, after: str, count: int) -> bool:
    """Whether a figure is tied to what it says about certainty (brief E4
    D2): the sentence itself says whether the result was significant or
    clear, or the next sentence in the same list, citing the same studies
    (or none), translates it ("That ...") or says so. Then both stay."""
    if _STATES_SIGNIFICANCE.search(sentence):
        return True
    if not after or frozenset(refs(after, count)) not in (frozenset(), frozenset(refs(sentence, count))):
        return False
    return after.startswith("That ") or bool(_STATES_SIGNIFICANCE.search(after))


def _figure_held(items: list, i: int, j: int, units: list, count: int) -> bool:
    """Whether the earlier unit j of item i must stay where it is (brief E5
    F1): it is tied by _figure_bound, or any other sentence in the same list,
    before or after it and at any distance, says whether a result was
    significant or how certain the evidence is, and cites at least one study
    the earlier sentence cites or no study at all."""
    sentence = units[j][0]
    if _figure_bound(sentence, _next_sentence(items, i, j, units), count):
        return True
    cites = frozenset(refs(sentence, count))
    for k, it in enumerate(items):
        if not isinstance(it, str):
            continue
        for m, unit in enumerate(_units(it)):
            if (k, m) == (i, j):
                continue
            for x in unit:
                if _STATES_SIGNIFICANCE.search(x):
                    other = frozenset(refs(x, count))
                    if not other or other & cites:
                        return True
    return False


def _same_words(a: str, b: str) -> bool:
    """Whether two sentences are the same letters and digits. The page prints
    such a copy once already (display.unseen), in the earlier block, so the
    earlier one is never the one removed."""
    return re.sub(r"[^a-z0-9]+", "", a.lower()) == re.sub(r"[^a-z0-9]+", "", b.lower())


def _drop_earlier(bd: dict, forced: list, count: int) -> dict:
    """The breakdown with, for each repeat kept only so its list or field is
    not emptied, the earlier sentence removed instead where the rule allows.
    Never across studies, never in the explanation, never a harm, safety or
    significance sentence, and never when the two are word for word the same."""
    if not forced:
        return bd
    out = dict(bd)

    def items_of(key):
        v = out.get(key)
        if isinstance(v, list):
            return v
        return [v] if isinstance(v, str) and v else []

    def put(key, items):
        if isinstance(out.get(key), list):
            out[key] = [i for i in items if not isinstance(i, str) or i.strip()]
        else:
            out[key] = items[0] if items else ""

    def sentence_count(key):
        return sum(len(sentences(i)) for i in items_of(key)
                   if isinstance(i, str) and not i.lower().startswith(WHO_LABEL.strip().lower()))

    for later_key in forced:
        for item in list(items_of(later_key)):
            if not isinstance(item, str):
                continue
            for later in _units(item):
                if any(_STATES_SIGNIFICANCE.search(x) for x in later):
                    continue
                done = False
                for key in FIGURE_ORDER[:FIGURE_ORDER.index(later_key)]:
                    items = list(items_of(key))
                    for i, it in enumerate(items):
                        if not isinstance(it, str) or (key == "says" and it.lower().startswith(WHO_LABEL.strip().lower())):
                            continue
                        units = _units(it)
                        for j, earlier in enumerate(units):
                            text = " ".join(earlier)
                            if (len(earlier) > 1 or _figure_held(items, i, j, units, count)
                                    or HARM.search(text) or _HARM_WORDS.search(text)
                                    or _SAFETY.search(text) or _same_words(earlier[0], later[0])
                                    or not _says_nothing_new(later[0], [earlier[0]], count)
                                    or not _carried_by(earlier, later, count)
                                    or sentence_count(key) - len(earlier) < 1):
                                continue
                            items[i] = " ".join(x for k, u in enumerate(units) if k != j for x in u)
                            put(key, items)
                            done = True
                            break
                        if done:
                            break
                    if done:
                        break
    return out
