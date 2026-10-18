"""
The words this product never says, and the plain words it says instead.

The copy rules ban a short list of marketing words. A prompt that asks the
model to avoid them is a request, not a guarantee: on a real run "robust"
reached a page twice, once in a model sentence and once in a study's own
conclusion. So the list is enforced here, in code, on every line a reader
can see, whoever wrote it. verdict.tidy_prose runs it on model prose as it
arrives, weigh_evidence on the finished result, the database on every row it
hands back (so a verdict cached before this existed is served clean), and
app._check_response on the whole payload last of all.

How the table is written:

  - Plain words, not synonyms. A banned word is swapped for the ordinary one
    ("leverage" is "use"), never for another word of the same register.
  - Never more certain than the sentence was. Nothing here writes "strong",
    "high quality", "high certainty" or "proven", so a swap can pull a
    sentence back towards the evidence and never past it. Where an adjective
    only dressed a noun ("robust evidence"), it simply goes.
  - Grammar survives. The article follows the next word ("a robust effect"
    is "an effect"), the capital a sentence opening needs is kept, and a verb
    keeps its tense.
  - Where a word is also a thing, that sense stays. "Oral rehydration
    solutions" are medicine and "natural landscapes" are places. "Elevated"
    and "elevation" were never on the list: elevated blood pressure is a
    finding.

Study titles and abstracts are never touched anywhere: they are the paper's
own words, shown as published. What each study concluded is our excerpt of
that abstract, so it is cleaned like the rest.
"""

import re

BANNED_WORDS = ("leverage", "seamless", "empower", "unlock", "robust",
                "actionable", "data-driven", "solutions", "testament",
                "landscape", "delve", "elevate")
# Only the adjective counts as "robust": "robusta" is a coffee and "robustus"
# a species. "Solutions" and "landscapes" count unless they are a liquid or a
# place (_keeps).
_BANNED = re.compile(
    r"\b(?:leverag(?:e|es|ed|ing)|seamless(?:ly)?|empower\w*|unlock\w*|robust(?:ly|ness)?"
    r"|actionable|data[-‐‑ ]driven|solutions|testaments?|landscapes?"
    r"|delv(?:e|es|ed|ing)|elevate|elevates|elevating)\b",
    re.IGNORECASE,
)

# What makes "solutions" a liquid and "landscapes" a place: a word in the few
# before it, or, for a liquid, the words after it naming what it holds.
_LIQUID = frozenset("""
    oral rehydration saline glucose dextrose sugar salt salts electrolyte electrolytes
    intravenous iv nasal topical ophthalmic eye ear irrigation ringer ringers lactated
    hypertonic isotonic hypotonic aqueous antiseptic disinfectant chlorhexidine povidone
    iodine bleach cleaning cleansing mouthwash rinse gargle contact lens dialysis
    preservation cardioplegic buffer buffered sterile parenteral enteral nutrient
    amino lipid colloid crystalloid balanced sodium potassium calcium magnesium
    alcohol ethanol hydrogen peroxide vinegar acid acetic citric mannitol albumin
    water carbohydrate test dilute diluted concentrated antibiotic antimicrobial
    anesthetic anaesthetic injectable infusion liquid chemical sanitizer sanitiser ors
""".split())
_PLACE = frozenset("""
    natural urban rural green forest forested built garden gardens outdoor visual
    scenic agricultural mountain mountainous coastal desert city suburban wild
    woodland farmland
""".split())
_LIQUID_AFTER = re.compile(r"\s+(?:containing|of\s+(?:\d|(?:%s)\b))" % "|".join(sorted(_LIQUID)))

_ANSWER_TO = re.compile(r"\s+to\s+(?:the|this|that|these|those|a|an|our|their|its|his|her|every|any|many)\b",
                        re.IGNORECASE)
# The word before "solutions" that makes it a fix or an answer, not an option.
_QUICK = frozenset("quick quick-fix instant magic miracle".split())
_ANSWER = frozenset("""easy simple single clear obvious perfect ideal universal permanent
    lasting definitive final""".split())

# An adjective that can stand next to "robust" and carry the sentence alone.
_PAIRED = (r"(?:consistent|reliable|reproducible|replicable|rigorous|high[- ]quality|large|clear"
           r"|good|solid|well[- ](?:designed|conducted|powered)|comprehensive|meaningful"
           r"|convincing|credible|persuasive|sound|valid|definitive|significant|accurate"
           r"|precise|stable|thorough|extensive|reasonable|scientific|clinical|empirical)")
_ADVERB = (r"(?:statistically|scientifically|analytically|experimentally|biologically"
           r"|empirically|technically|clinically|genetically|physiologically)")
_WORD = r"[A-Za-z][\w'’-]*"
# The word that follows a dropped adjective: a noun or the next adjective,
# never a conjunction ("robust, but small" must not become "but small").
_NOUN = r"(?!(?:and|or|but|nor|yet)\b)" + _WORD
# What stands before an adjective used as the predicate: "was robust".
_LINKING = (r"(?:is|are|was|were|be|been|being|remain(?:s|ed|ing)?|stay(?:s|ed|ing)?"
            r"|prove[sd]?|proving|seem(?:s|ed)?|appear(?:s|ed)?|look(?:s|ed)?"
            r"|though|although|while|if|when|how|whether)")
_BETWEEN = (r"(?:not|no longer|also|still|generally|equally|only|fairly|largely|always|never"
            r"|rather|relatively|somewhat|too|so|as|broadly)")
# What follows a predicate adjective, as against a noun it describes.
_CLAUSE_END = (r"(?=\s*(?:[,.;:)!?]|$)|\s+(?:and|but|or|after|in|across|when|for|at|with|even"
               r"|if|under|because|as|over|by|against|among|regardless|throughout|within"
               r"|despite|enough|only|though|although|while|than|so|yet|nor|on|from|between"
               r"|once|since|until|whether|where|which|that|too)\b)")
# A word in the few before "robust evidence" that makes it a denial. There the
# swap is "good": dropping the adjective would say there is no evidence at all.
_DENIES = frozenset("""no not cannot lack lacks lacked lacking without absence absent never
    nor neither fail fails failed unable hardly scarcely""".split())
# The word before "empowering" that makes it an adjective, not a verb.
_ADJECTIVE_AFTER = frozenset("""a an the very more most so is are was were be feel felt truly
    really quite highly""".split())


def _recase(was: str, now: str) -> str:
    """Keep the capital a sentence opening needs."""
    if was[:1].isupper() and now[:1].islower():
        return now[:1].upper() + now[1:]
    return now


def _article(article: str, word: str) -> str:
    """`article` (a or an, as written) matched to the word that now follows it."""
    if word.isupper() and len(word) > 1:
        return article
    w = word.lower()
    if re.match(r"(?:uni|use|usu|uti|ura|uro|eu|one|once|ubi)", w):
        vowel = False
    elif re.match(r"(?:hour|honest|honou?r|heir)", w):
        vowel = True
    else:
        vowel = w[:1] in "aeiou"
    return article[0] + ("n" if vowel else "") + " "


def _words_before(text: str, pos: int, count: int) -> list[str]:
    """The last `count` words before `pos` in its own sentence, lower-cased.
    A hyphenated word counts as its parts."""
    sentence = re.split(r"[.;:!?]\s", text[:pos].lower())[-1]
    return re.findall(r"[a-z0-9%']+", sentence.replace("-", " ").replace("%", "% "))[-count:]


def _keeps(text: str, start: int, word: str) -> bool:
    """True for a hit that is a liquid or a place, not a copy word."""
    word = word.lower()
    if word == "solutions":
        if any(w in _LIQUID or w[0].isdigit() or w == "%"
               for w in _words_before(text, start, 3)):
            return True
        return bool(_LIQUID_AFTER.match(text[start + len(word):start + len(word) + 60].lower()))
    if word.startswith("landscape"):
        return any(w in _PLACE for w in _words_before(text, start, 2))
    return False


def banned_words(text: str) -> list[str]:
    """The banned words in `text`, lower-cased, each once, in order of first use."""
    seen: list[str] = []
    for m in _BANNED.finditer(text or ""):
        w = m.group(0).lower()
        if w not in seen and not _keeps(m.string, m.start(), m.group(0)):
            seen.append(w)
    return seen


# ---------------------------------------------------------------------
# Replacements that need to look at their surroundings.
# ---------------------------------------------------------------------

def _attributive(m: re.Match) -> str:
    """"Robust" before a noun. The adjective goes and the article follows the
    noun; after a denial it becomes "good", so "no robust evidence" is not
    turned into "no evidence"."""
    art, noun = m.group("art") or "", m.group("noun")
    words = _words_before(m.string, m.start(), 4)
    if _DENIES & set(words) or any(w.endswith("n't") for w in words):
        return (_article(art, "good") if art else "") + "good " + noun
    return (_article(art, noun) if art else "") + noun


def _noun(m: re.Match) -> str:
    """Only the noun the dropped word stood in front of."""
    return m.group("noun")


def _inconsistent(m: re.Match) -> str:
    """"Non-robust results" are inconsistent; "a non-robust result" is an inconsistent one."""
    return _article(m.group("art"), "inconsistent") + "inconsistent" if m.group("art") else "inconsistent"


def _leftover(m: re.Match) -> str:
    """The last "robust" standing: before punctuation it is a predicate and
    becomes "consistent"; anywhere else it has nothing to describe and goes."""
    return "consistent" if re.match(r"\s*(?:[,.;:)!?]|$)", m.string[m.end():]) else ""


def _empowering(m: re.Match) -> str:
    """"An empowering experience" is supportive; "by empowering patients" is helping."""
    before = _words_before(m.string, m.start(), 1)
    return "supportive" if before and before[0] in _ADJECTIVE_AFTER else "helping"


def _solutions(m: re.Match) -> str:
    if _keeps(m.string, m.start(), m.group(0)):
        return m.group(0)
    before = _words_before(m.string, m.start(), 1)
    # "Solutions to the problem" are answers; "solutions to improve sleep" are options.
    if _ANSWER_TO.match(m.string[m.end():]):
        return "answers"
    if before and before[0] in _QUICK:
        return "fixes"
    if before and before[0] in _ANSWER:
        return "answers"
    return "options"


def _landscape(m: re.Match) -> str:
    """"The research landscape" is the research; "the landscape of X" is the
    state of X; any other landscape is a picture. A place stays a place."""
    word = m.group("land")
    if _keeps(m.string, m.start("land"), word):
        return m.group(0)
    if m.group("noun"):
        return m.group("noun") + (m.group("of") or "")
    plural = word.lower().endswith("s")
    if m.group("of"):
        return ("pictures" if plural else "state") + m.group("of")
    return "pictures" if plural else "picture"


# ---------------------------------------------------------------------
# The table. Each row is (pattern, replacement): a template for m.expand() or
# a function of the match. Order is the order applied, most specific first, so
# "robust to" is read before "robust" and a "robust" before a noun is not
# mistaken for a predicate. Patterns are case-insensitive, and a replacement
# is given the capital the match had.
# ---------------------------------------------------------------------

REPLACEMENTS = [
    # --- robust -------------------------------------------------------
    # Also a method ("robust standard errors"): that loses its adjective with
    # the rest, because the name of a method is not worth the word.
    (r"\bmethodologically\s+robust\b", "well designed"),
    (r"(?P<art>\b[Aa]n?\s+)?\bnon-?robust\b", _inconsistent),
    (r"\brobustness\b", "consistency"),
    (r"\brobust\s+(?:to|against)\b", "stable under"),
    (r"\bas\s+robust\s+as\b", "as consistent as"),
    # "Consistent and robust evidence", "robust, reliable results": keep the
    # adjective that can carry the sentence on its own.
    (rf"\b(?P<adj>{_PAIRED})(?:,\s*(?:and\s+)?|\s+(?:and|or)\s+)(?:{_ADVERB}\s+)?robust\b",
     r"\g<adj>"),
    (rf"\b(?:{_ADVERB}\s+)?robust(?:,\s*(?:and\s+)?|\s+(?:and|or)\s+)(?P<adj>{_PAIRED})\b",
     r"\g<adj>"),
    # "More robust", "very robust": the degree stays, the word is the nearest plain one.
    (r"\b(?P<deg>more|less|most|least|very|highly|fairly|reasonably|relatively|quite|"
     r"particularly|especially|extremely|remarkably|exceptionally|sufficiently|equally|"
     r"somewhat|rather|increasingly|similarly|generally|consistently)\s+robust\b",
     r"\g<deg> consistent"),
    # "Was robust", "remained robust after adjustment": a predicate.
    (rf"(?P<cop>\b{_LINKING}(?:\s+{_BETWEEN})*\s+)(?:{_ADVERB}\s+)?robust\b{_CLAUSE_END}",
     r"\g<cop>consistent"),
    (r"\brobust(?=\s+(?:across|despite|regardless|throughout)\b)", "consistent"),
    (r"\s+robustly(?=\s*(?:[,.;:)!?]|$))", ""),
    (rf"\brobustly\s+(?:and|or)\s+(?P<noun>{_WORD})", _noun),
    (rf"\brobustly,?\s+(?P<noun>{_NOUN})", _noun),
    # "Both robust and seamless": the pair stays a pair.
    (r"\b(?P<both>both|either)\s+robust\s+(?P<and>and|or)\b", r"\g<both> consistent \g<and>"),
    # "Robust, randomized trials": a list of adjectives before a noun.
    (rf"\b(?:{_ADVERB}\s+)?robust,\s+(?P<noun>{_NOUN})", _noun),
    # "Robust or fragile" is a contrast, so it keeps both sides.
    (r"\brobust\s+or\b", "consistent or"),
    # "A robust and unusual result": the adjective and its "and" go.
    (rf"(?P<art>\b[Aa]n?\s+)?(?:{_ADVERB}\s+)?\brobust\s+and\s+(?P<noun>{_WORD})", _attributive),
    # "A robust link", "robust evidence", "these robust trials".
    (rf"(?P<art>\b[Aa]n?\s+)?(?:{_ADVERB}\s+)?\brobust\s+(?P<noun>{_NOUN})", _attributive),
    (r"\brobust\b\s*", _leftover),
    # Whatever "robustly" is still standing has nothing to describe.
    (r"\brobustly\b\s*", ""),
    # --- leverage -----------------------------------------------------
    (r"\ba\s+leverage\b", "an influence"),
    (r"\b(?P<det>the|some|more|less|little|no|any|much|great|greater|real)\s+leverage\b",
     r"\g<det> influence"),
    (r"\bleverages\b", "uses"),
    (r"\bleveraged\b", "used"),
    (r"\bleveraging\b", "using"),
    (r"\bleverage\b", "use"),
    # --- seamless -----------------------------------------------------
    (r"\bseamlessly\b", "smoothly"),
    (r"\bseamless\b", "smooth"),
    # --- empower ------------------------------------------------------
    (r"\b(?P<feel>feel|feels|felt|feeling)(?P<more>\s+more)?\s+empowered\s+to\b",
     r"\g<feel>\g<more> able to"),
    (r"\b(?P<feel>feel|feels|felt|feeling)(?P<more>\s+more)?\s+empowered\b",
     r"\g<feel>\g<more> in control"),
    (r"\bempowered\s+to\b", "helped to"),
    (r"\b(?P<be>is|are|was|were|be|been|being)\s+empowered\b", r"\g<be> helped"),
    (r"\bempowered\b", "engaged"),
    (r"\ban?\s+empowering\b", "a supportive"),
    (r"\bempowering\b", _empowering),
    (r"\bempowerment\b", "autonomy"),
    (r"\bempowers\b", "helps"),
    (r"\bempower\b", "help"),
    (r"\bempower\w*", "support"),
    # --- unlock -------------------------------------------------------
    (r"\bunlocks\b", "opens up"),
    (r"\bunlocked\b", "opened up"),
    (r"\bunlocking\b", "opening up"),
    (r"\bunlock\w*", "open up"),
    # --- actionable, data-driven --------------------------------------
    (r"\ban\s+actionable\b", "a practical"),
    (r"\bactionable\b", "practical"),
    (r"\bdata[-‐‑ ]driven\b", "data-based"),
    # --- solutions ----------------------------------------------------
    (r"\bsolutions\b", _solutions),
    # --- testament ----------------------------------------------------
    (r"\b(?:a\s+)?testament\s+to\b", "a sign of"),
    (r"\btestaments\s+to\b", "signs of"),
    (r"\btestaments\b", "statements"),
    (r"\btestament\b", "statement"),
    # --- landscape ----------------------------------------------------
    (r"\b(?:(?P<noun>research|evidence|literature)\s+)?(?P<land>landscapes?)(?P<of>\s+of\b)?",
     _landscape),
    # --- delve --------------------------------------------------------
    (r"\bdelves\b", "looks"),
    (r"\bdelved\b", "looked"),
    (r"\bdelving\b", "looking"),
    (r"\bdelve\b", "look"),
    # --- elevate ------------------------------------------------------
    (r"\belevates\b", "raises"),
    (r"\belevating\b", "raising"),
    (r"\belevate\b", "raise"),
]
_RULES = [(re.compile(p, re.IGNORECASE), r) for p, r in REPLACEMENTS]


def clean_text(text):
    """
    `text` with every banned word replaced by plain ones. Anything that is
    not a string, and any string with nothing to change, comes back as it was.
    """
    if not isinstance(text, str) or not _BANNED.search(text):
        return text
    out = text
    for pattern, replacement in _RULES:
        def swap(m, replacement=replacement):
            built = replacement(m) if callable(replacement) else m.expand(replacement)
            return _recase(m.group(0), built)
        out = pattern.sub(swap, out)
    return out


# Keys whose strings are somebody else's words, never ours: what the reader
# typed and the search that ran, and the paper as published. Addresses too
# (any key ending "url"), because a link has to stay a link.
_VERBATIM = frozenset("""claim claim_text typed searched terms query search_query_used title
    abstract journal authors pmid publication_types data_banks""".split())


def clean_payload(obj, said: str = ""):
    """
    `obj` (a result, a breakdown, any JSON-shaped value) with every string
    cleaned except the ones in _VERBATIM and the addresses. Returns a copy.

    `said` is the claim the reader typed. A string that is a piece of it (the
    breakdown quotes the claim part by part) is the reader's own wording, so
    it stays as typed, the same as the claim itself.
    """
    if isinstance(obj, str):
        mine = said.lower()
        return obj if mine and obj.lower() in mine else clean_text(obj)
    if isinstance(obj, dict):
        return {k: (v if k in _VERBATIM or str(k).endswith("url") else clean_payload(v, said))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(clean_payload(v, said) for v in obj)
    return obj
