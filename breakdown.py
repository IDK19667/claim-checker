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
    (r"\bsystematic reviews and meta-analyses\b", "pooled reviews",
     "systematic reviews and meta-analyses"),
    (r"\bsystematic review and meta-analysis\b", "pooled review",
     "systematic review and meta-analysis"),
    (r"\bmeta-analyses\b", "pooled reviews", "meta-analyses"),
    (r"\bmeta-analysis\b", "pooled review", "meta-analysis"),
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
    (r"\befficacy\b", "how well it works", None),
    (r"\b(?:the |its )effectiveness of\b", "the effect of", None),
    (r"\beffectiveness\b", "how well it works", None),
    (r"\b(?:the |its )incidence of\b", "the rate of", None),
    (r"\bincidence\b", "how often it happened", None),
    (r"\b(?:the |its )prevalence of\b", "the rate of", None),
    (r"\bprevalence\b", "how common it is", None),
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
    (r"\bincluding\b", "like", None),
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
    r"(?:who|with)|for people (?:who|with)|for some|in some)\b", re.IGNORECASE)

# The claim was never put to a test. Checked before the plain "no", because
# "no study tests this" is a negative sentence that is not a "no".
_UNTESTED_SAID = re.compile(
    r"\b(?:no (?:study|studies|trials?|good evidence|evidence|published|human)|"
    r"nothing (?:here |found )?(?:tests?|shows?|measures?)|not (?:been )?(?:tested|"
    r"studied)|untested|unproven|(?:do|does|did)(?:n't| not) (?:actually )?test)\b",
    re.IGNORECASE)

_NEGATED = re.compile(
    r"\b(?:does not|do not|did not|doesn't|don't|didn't|is not|isn't|are not|"
    r"aren't|was not|were not|cannot|can't|won't|will not|never|no (?:more|"
    r"better)|not (?:better|more effective|any better)|(?:about|just|roughly) as "
    r"(?:well|good|effective) as|no (?:\w+ ){0,2}(?:benefits?|effect|link|"
    r"difference|advantage|change)|myth)\b", re.IGNORECASE)

# A claim that compares two things is answered by the clause that compares.
_COMPARATIVE_CLAIM = re.compile(
    r"\b(?:better|worse|more|less|faster|slower|healthier|stronger|superior|"
    r"inferior)\b[^.]{0,60}\bthan\b", re.IGNORECASE)
_COMPARES_SAME = re.compile(
    r"\b(?:about|just|roughly|nearly|much) as (?:well|good|effective|much) as|"
    r"\bno (?:better|worse|more|less)\b|\bnot (?:any )?(?:better|worse|more "
    r"effective)\b|\b(?:similar|same|equal(?:ly)?|comparable|no difference)\b",
    re.IGNORECASE)
_COMPARES_MORE = re.compile(r"\b(?:better|more|faster|superior|outperform\w*)\b"
                            r"[^.]{0,40}\bthan\b", re.IGNORECASE)

# Where a sentence turns. After "but" the answer follows the turn; after
# "though" the answer came before it.
_TURN_AFTER = re.compile(r",?\s+\b(?:but|yet|however)\b,?\s+", re.IGNORECASE)
_TURN_BEFORE = re.compile(r",?\s+\b(?:though|although|even though|while|whereas)\b\s+",
                          re.IGNORECASE)
# A second clause starts at ", and", ", so", ";", or a bare "and" that
# opens a new verb ("don't remove toxins and can hurt your liver"), whose
# hedge belongs to it and not to the answer before it.
_AND_CLAUSE = re.compile(r",\s+(?:and|so)\s+|;\s+|\s+and\s+(?=(?:can|could|may|might|"
                         r"will|may|is|are|was|were|has|have|cause|causes)\b)", re.IGNORECASE)


def _stem(word: str) -> str:
    w = word.lower()
    for end in ("ing", "ed", "es", "s"):
        if len(w) > len(end) + 2 and w.endswith(end):
            return w[:-len(end)]
    return w


def _echoes(clause: str, claim: str) -> bool:
    """True when the clause is about the claim's own words, not a neighbour's."""
    terms = {_stem(t) for t in claim_terms(claim)}
    if not terms:
        return False
    words = {_stem(w) for w in re.findall(r"[A-Za-z][A-Za-z'-]*", clause or "")}
    hits = {t for t in terms
            if any(w == t or (len(w) >= 5 and len(t) >= 5 and w[:5] == t[:5])
                   for w in words)}
    return len(hits) >= min(2, len(terms))


def _polarity(clause: str) -> str:
    if _UNTESTED_SAID.search(clause):
        return "untested"
    if _HEDGED.search(clause):
        return "mixed"
    return "no" if _NEGATED.search(clause) else "yes"


def stance(text: str, claim: str) -> str:
    """
    What a takeaway commits to about the claim: "yes", "no", "untested" or
    "mixed". Only the first sentence is read: the takeaway is one sentence,
    and an explanation's first sentence is where it states its answer.
    """
    first = (sentences(text or "") or [""])[0].strip().rstrip(".")
    if not first:
        return "mixed"
    if _UNTESTED_SAID.search(first) and not _TURN_AFTER.search(first):
        return "untested"
    if _COMPARATIVE_CLAIM.search(claim or ""):
        if _COMPARES_SAME.search(first):
            return "mixed" if _HEDGED.search(first.split(",")[-1]) else "no"
        if _COMPARES_MORE.search(first):
            return "mixed" if _HEDGED.search(first) else "yes"
        return "mixed"
    parts = _TURN_AFTER.split(first, maxsplit=1)
    if len(parts) == 2:
        answer = parts[1]
    else:
        parts = _TURN_BEFORE.split(first, maxsplit=1)
        answer = parts[0] if len(parts) == 2 else None
    if answer is not None:
        return _polarity(answer) if _echoes(answer, claim) else "mixed"
    for clause in _AND_CLAUSE.split(first):
        if _echoes(clause, claim):
            return _polarity(clause)
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

# Which stances each stamp can stand beside. "complicated" holds both sides,
# so a plain yes or a plain no under it is the contradiction; "insufficient"
# means nothing tested the claim, so any plain answer under it is one too.
_AGREES = {
    "true": {"yes", "mixed"},
    "false": {"no", "mixed"},
    "complicated": {"mixed", "untested"},
    "insufficient": {"untested", "mixed"},
}

_SAYS = {"yes": "a plain yes", "no": "a plain no",
         "untested": "that nothing here tests the claim"}


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
    return (f"the verdict is \"{verdict}\" but {where} says {_SAYS[said]}. If the "
            f"studies really show {_SAYS[said]}, the verdict must say so; if they do "
            f"not, the takeaway must say no more than the verdict does")


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


def soften(text: str) -> str:
    """Rewrite certainty the evidence here can never support."""
    t = text or ""
    for pattern, weaker in _SOFTEN:
        t = pattern.sub(weaker, t)
    # A substitution at the head of a sentence must not leave it lowercase.
    return re.sub(r"(^|(?<=[.!?]\s))([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), t)


# Research-paper grammar that nobody says out loud. Each pair fixes the
# verb and leaves the finding alone: "links to" and "is linked to" are the
# same strength of claim, so this can no more raise certainty than soften()
# can. It exists because the takeaway is the one line meant to be repeated
# to another person, and "Vitamin D links to colds" is not a sentence
# anyone would say.
NATURAL = [
    (r"\b[Ll]inks to\b", "is linked to"),
    (r"\b[Ll]ink to\b", "are linked to"),
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


def natural(text: str) -> str:
    """
    Make the takeaway sound like a person saying it.

    "Vitamin D links to colds and flu" is a headline, not a sentence, and
    the takeaway is the line a reader is meant to be able to repeat. Only
    the verb is touched; nothing here changes what is being claimed or how
    strongly, so it composes safely with soften().
    """
    t = text or ""
    for pattern, fixed in _NATURAL:
        t = pattern.sub(fixed, t)
    # "Vitamin D is linked to" needs its capital back if it opened the line.
    return re.sub(r"(^|(?<=[.!?]\s))([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), t)


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
    return _fix_articles(_no_repeats(out))


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


def _figures(sentence: str) -> list[str]:
    bare = _REF.sub(" ", sentence)
    return [f for f in _FIGURE.findall(bare)
            if not re.fullmatch(r"(?:19|20)\d\d", f.lstrip("-"))]


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


def say_nulls(text: str, studies: list[dict]) -> str:
    """
    Every sentence whose figure its abstract calls no clear difference says
    so: a size word for it ("a small effect") becomes "no clear difference",
    and a sentence that does not already say it is followed by one that
    does, carrying the same citation.
    """
    out = []
    for sentence in sentences(text or ""):
        ref = null_figure(sentence, studies)
        if ref is None or _SAID_NULL.search(sentence):
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


def tell_straight(text: str, studies: list[dict]) -> str:
    """Rule 9, both halves, for any gated prose."""
    return say_nulls(keep_comparison(text, studies), studies)


def drop_citing(text: str, skip, count: int) -> str:
    """Every sentence of text except those resting only on studies in skip."""
    if not skip:
        return text or ""
    return " ".join(sentence for sentence in sentences(text or "")
                    if not (refs(sentence, count) and set(refs(sentence, count)) <= set(skip)))


def ground(raw, studies: list[dict], tidy, claim: str = "", skip=()) -> dict | None:
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
    # By here the gate has checked every figure in it against the abstracts.
    # If nothing survived, which line is honest depends on the rest of the
    # layer: "the abstracts don't report it" is only true when no figure
    # reached the page anywhere, and the gate drops sentences one at a time,
    # so the effect-size line can go while an odds ratio two paragraphs up
    # stays. Saying the abstracts carry no figure there would contradict it.
    if not effect or not _NUMERAL.search(_REF.sub(" ", effect)):
        said = paragraphs + [p["assessment"] for p in parts]
        effect = (EFFECT_SIZE_ABOVE
                  if any(_NUMERAL.search(_REF.sub(" ", t)) for t in said)
                  else NO_EFFECT_SIZE)

    # The figure, read as a size. The translation is only ever written beside
    # the figure it came from: a ratio in this line translated and parked
    # under a different ratio in that line is a wrong number, not a plainer
    # one. The label for the takeaway may come from a paragraph instead,
    # because a label only ever weakens wording and cannot mis-state a
    # figure the reader never sees.
    sized = evidence.effect_size(effect)
    # A figure its own abstract reports as no clear difference is sized as
    # none, whatever its distance from 1. The sentence say_nulls added under
    # it becomes the translation, so it is picked as one by says().
    null_ref = next((r for r in (null_figure(t, studies) for t in sentences(effect))
                     if r is not None), None)
    if null_ref is not None or (sized and _SAID_NULL.search(effect)):
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
        (e for e in (evidence.effect_size(t) for t in paragraphs) if e), None)

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


def says(explanation: str, bd: dict, studies: list[dict]) -> list[str]:
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
            if not refs(sentence, count):
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
    pair += [line for line in said if line.startswith(TRANSLATION_LEAD)][:1]
    take(" ".join(pair) if pair else bd.get("effect_size"), 2)
    take(bd.get("applies_to"), 1)
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
