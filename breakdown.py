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
    (r"\bduration\b", "how long it lasted", None),
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
    (r"\bcognitive function\b", "thinking and memory", None),
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
    words. Keep every sentence under 25 words; a sentence over 28 is
    deleted, brackets and all, so a long one is wasted work.
  * Say "people with low levels", never "deficient people". Name a group
    by what is true of it in ordinary words.
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
    # Not "stops X-ing": "where it stops working" is a question about where
    # the effect ends, not a claim that it prevents anything, and "reduces
    # providing protection" is not a sentence.
    (r"\bstops\b(?!\s+\w+ing\b)", "reduces"),
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
    return _fix_articles(out)


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


def ground(raw, studies: list[dict], tidy, claim: str = "") -> dict | None:
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
        said = plain(soften(tidy(str(value or ""), limit)), kept_terms)
        return keep_sentences(said, count, known)

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
        elif out[key] and out[key] not in (NO_EFFECT_SIZE, EFFECT_SIZE_ABOVE):
            out[key] = gloss_once(out[key], done)

    # Two measurements, kept with the text they describe: how many distinct
    # records the whole layer rests on (the summary line shows it), and what
    # reading grade the surviving prose came out at.
    out["rests_on"] = len(cited_pmids(out, studies))
    out["grade"] = reading_grade(flatten(out), claim_terms(claim))
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
