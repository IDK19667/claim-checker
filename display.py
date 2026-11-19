"""
What the result page prints, worked out from fields the check already saved.

Nothing here asks a model for anything and nothing here changes an answer:
it only decides which of the saved sentences are shown where, so the same
sentence is never printed twice on one page. static/app.js carries the same
rules (breakdownView) for a check that has just run, and a test runs both on
the same records and compares them.
"""
import re

WHO_LABEL = "Who was studied: "

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])")


def split_sentences(text) -> list:
    return [s.strip() for s in _SENTENCE_END.split(str(text or "").strip()) if s.strip()]


def _key(sentence: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", sentence.lower())[:80]


def unseen(text, seen: set) -> str:
    """`text` without the sentences already printed; what is left is marked printed."""
    out = []
    for sentence in split_sentences(text):
        k = _key(sentence)
        if k and k not in seen:
            seen.add(k)
            out.append(sentence)
    return " ".join(out)


def breakdown_view(result):
    """
    The breakdown as the page prints it, or None when the record has none.

    "What the research says" is picked from the same paragraphs the breakdown
    prints, and says its who-line from the same field as "Who this applies
    to". Printing the breakdown as it stands therefore says several sentences
    twice, the effect size most of all. Here the lines of "What the research
    says" come first, and every later block loses the sentences already shown.
    """
    if not result:
        return None
    bd = result.get("breakdown")
    if not bd:
        return None
    says = [ln for ln in (bd.get("says") or []) if str(ln or "").strip()]
    if not says and str(result.get("explanation") or "").strip():
        says = [result["explanation"]]
    # The who-line is the applies_to sentence again, with a label. The block
    # that says who it applies to is the one that keeps it.
    says = [ln for ln in says if not ln.lower().startswith(WHO_LABEL.strip().lower())]
    if not says and str(result.get("explanation") or "").strip():
        says = [result["explanation"]]
    seen = set()
    for ln in says:
        for s in split_sentences(ln):
            seen.add(_key(s))
    for s in split_sentences(result.get("still_open")):
        seen.add(_key(s))

    parts = []
    for p in bd.get("parts") or []:
        take = unseen(p.get("assessment"), seen)
        if take:
            parts.append({"part": p.get("part") or "", "assessment": take})
    evidence = [x for x in (unseen(para, seen) for para in bd.get("evidence") or []) if x]
    return {
        "says": says,
        "parts": parts,
        "evidence": evidence,
        "effect_size": unseen(bd.get("effect_size"), seen),
        "concluded": bd.get("concluded") or [],
        "strength": unseen(bd.get("strength"), seen),
        "applies_to": unseen(bd.get("applies_to"), seen),
        "populations": bd.get("populations") or [],
        "not_applies_to": unseen(bd.get("not_applies_to"), seen),
        "unknowns": unseen(bd.get("unknowns"), seen),
    }


def confidence_line(result) -> str:
    """
    How much to lean on the answer, in one sentence, from fields already saved.

    The wording of a real check is evidence.evidence_line, worked out on the
    server when it ran. A saved record without it says what its studies were,
    which is all the old level word ever measured.
    """
    studies = result.get("studies") or []
    cited = [s for s in studies if s.get("cited_in_verdict")]
    if not studies:
        return "No direct evidence either way: nothing on PubMed matched well enough to weigh."
    if not cited:
        return "No direct evidence either way: none of the studies tests this claim."
    line = str(result.get("evidence_line") or "").strip().rstrip(".")
    if line:
        return line + "."
    tiers = {s.get("tier") or "moderate" for s in cited}
    word = "Strong" if "strong" in tiers else "Moderate" if tiers - {"weak", "retracted"} else "Weak"
    return f"{word} study designs."


# ---- Sources: why each study is on the page --------------------------------
# What each study design does and where it stops. static/app.js carries the
# same table (TYPES) for the study sheet; a test compares the two. The first
# matching label wins, as in the script.
DESIGN_NOTES = (
    ('Meta-Analysis', 'strong', 'Combines the results of several studies into one estimate. It is only as sound as the studies it pools, and it can mix very different ones.'),
    ('Network Meta-Analysis', 'strong', 'Combines many studies to compare several treatments at once. Comparisons made only indirectly are less certain than head-to-head trials.'),
    ('Systematic Review', 'strong', 'Searches for every study on a question, using set rules for what counts. It is only as sound as the studies it found.'),
    ('Randomized Controlled Trial', 'strong', 'Randomly assigns people to a treatment or a comparison group. A small size, a short run or a narrow group limits what it shows.'),
    ('Practice Guideline', 'strong', "Recommendations a medical body wrote after reviewing the evidence. They reflect that body's judgment and can lag behind newer studies."),
    ('Retracted Publication', 'retracted', 'Withdrawn by the journal after publication. Its findings should not be relied on.'),
    ('Case Reports', 'weak', 'Describes one patient or a few. It can raise a question, but it cannot show cause or how common something is.'),
    ('Editorial', 'weak', "An opinion piece by the journal's editors. It gives a view, not data."),
    ('Comment', 'weak', 'A published reply to another paper. It is not a study.'),
    ('Letter', 'weak', 'A short letter to the journal. It is not a study.'),
    ('News', 'weak', 'A news item. It is not a study.'),
    ('Controlled Clinical Trial', '', 'Tests people against a comparison group without random assignment. The groups may have differed from the start.'),
    ('Clinical Trial', '', 'Tests a treatment on people, not always with a comparison group. Without one it cannot show cause.'),
    ('Observational Study', '', 'Watches what happens to people who did or did not do something. It can show links but not prove cause, since other factors may differ.'),
    ('Multicenter Study', '', 'Run at several sites, which can make results more general. The label says nothing about how well it was designed.'),
    ('Comparative Study', '', 'Compares two or more approaches. What it shows depends on how the comparison was run.'),
    ('Review', '', "An expert's summary of the research. It depends on which studies the author chose."),
)
_IGNORE_TYPES = re.compile(r"^(Journal Article|Research Support|English Abstract|Introductory)")
DEFAULT_DESIGN = ("Journal article", "", "A research paper. The abstract says what kind of study it was.")


def design_note(publication_types) -> tuple:
    """(label, class, note) for the most informative design in `publication_types`."""
    types = [t for t in (publication_types or []) if not _IGNORE_TYPES.match(t)]
    for label, cls, note in DESIGN_NOTES:
        if any(t.startswith(label) for t in types):
            return label, cls, note
    return (types[0] if types else DEFAULT_DESIGN[0]), "", DEFAULT_DESIGN[2]


def study_why(study) -> str:
    """
    One line on why a study is listed: what the verdict did with it and, for a
    study it leaned on, what that kind of study does and where it stops. All of
    it is read off fields the record already carries.
    """
    if study.get("cited_in_verdict"):
        label, _cls, note = design_note(study.get("publication_types"))
        out = ["Relied on for the verdict."]
        if label != DEFAULT_DESIGN[0]:
            out.append(note)
        if study.get("population"):
            out.append(f"Studied in: {study['population']}.")
        return " ".join(out)
    if study.get("off_topic"):
        return f"Read and set aside: {str(study['off_topic']).rstrip('.')}."
    if study.get("background"):
        why = str(study.get("background_why") or "").rstrip(".")
        return f"Background only, not counted: {why}." if why else "Background only, not counted."
    return "Read, not relied on."


# ---- The page's own words for a link preview and a search result -------------

TITLE_MAX = 50       # plus " · Evident" in the tab: about 60 in all
DESCRIPTION_MAX = 155


def _plain(text) -> str:
    """One line, no dashes used as punctuation (the site never prints them)."""
    t = " ".join(str(text or "").split())
    t = re.sub(r"(?<=\d)\s*[—–]\s*(?=\d)", " to ", t)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    return t


def clip(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a word and closed with an ellipsis."""
    t = _plain(text)
    if len(t) <= limit:
        return t
    cut = t[: limit - 1]
    if " " in cut and len(cut.rsplit(" ", 1)[0]) >= limit * 0.5:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "…"


def result_meta(claim: str, label: str, short_answer: str) -> dict:
    """
    <title> and description for a saved result: the verdict's own label first,
    so a clipped claim still sits under the right answer, then the claim in
    quotes; the description is the label and the short answer, whole sentences
    where they fit. Nothing here is stronger than the label it is given.
    """
    room = TITLE_MAX - len(label) - 4  # ": " and the two quotation marks
    title = f"{label}: “{clip(claim.rstrip(' .?!'), max(room, 12))}”"
    lead = f"{label}. "
    answer = _plain(short_answer)
    budget = DESCRIPTION_MAX - len(lead)
    if len(answer) <= budget:
        body = answer
    else:
        whole = ""
        for sentence in _SENTENCE_END.split(answer):
            nxt = (whole + " " + sentence).strip()
            if len(nxt) > budget:
                break
            whole = nxt
        body = whole if len(whole) >= budget * 0.5 else clip(answer, budget)
    return {"title": title, "description": (lead + body).strip()}


# ---- Is a saved answer sound enough to be indexed? ---------------------------

SAVED_VERDICTS = ("true", "false", "complicated", "insufficient")

# A claim is something a person typed. Anything in it that could point at, or
# say something about, one person keeps it away from search engines. The screen
# errs towards flagging: a false positive only costs an index entry, a false
# negative puts a stranger's detail in a search result.
_PERSONAL = re.compile(
    # contact details
    r"[\w.+-]+@[\w-]+\.[\w.]+"                       # an email address
    r"|(?<![\w.])@\w{2,}"                             # an @handle
    r"|\+?\d[\d\s().-]{7,}\d"                        # a phone-like run of digits
    r"|https?://|\bwww\.|\b[\w-]+\.(?:com|org|net|io|co|me|uk|app|tv|ly)\b"   # a URL or domain
    r"|\b\d{1,5}\s+(?:[a-z0-9.'-]+\s+){1,3}(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd"
    r"|court|ct|way|place|close|crescent|terrace)\b"    # street-address-like text
    r"|\bp\.?o\.? box\b|\b[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}\b"   # a PO box, a UK postcode
    # first person and people close to the writer, and gendered pronouns
    r"|\b(?:i|i'm|i've|i'd|i'll|my|me|mine|myself|we|we're|we've|our|ours|ourselves"
    r"|he|she|his|her|hers|him|himself|herself|he's|she's)\b"
    # ages
    r"|\b\d{1,3}[\s-]*(?:years?|yrs?|y)[\s-]*olds?\b|\b\d{1,3}\s*y\.?/?o\b|\b\d{1,3}\s*yrs?\b"
    r"|\b(?:aged?|age of)\s+\d{1,3}\b"
    # personal medical details without a first-person word
    r"|\b(?:diagnosed|tested positive|test results?|blood results?|lab results?|scan results?|biopsy"
    r"|prescribed|prescription|(?:am|is|are|was|were|became|got|getting)\s+pregnant|expecting a baby)\b",
    re.I)

# Capitalised wording that gives a name: titles, a relation then a name.
_NAMEISH = re.compile(
    r"\b(?:Dr|Mr|Mrs|Ms|Miss|Mx|Prof|Professor|Nurse|Sir|Lady)\.?\s+[A-Z][a-z]+"
    r"|\b(?:[Mm]y|[Oo]ur|[Hh]is|[Hh]er)\s+(?:\w+\s+)?(?:son|daughter|wife|husband|partner|mom|mum|mother|dad|father"
    r"|friend|doctor|neighbou?r|colleague|boss|uncle|aunt|cousin|brother|sister|grandma|grandpa|boyfriend"
    r"|girlfriend|child|baby|patient)\s+[A-Z][a-z]+")

_CAPWORD = re.compile(r"\b[A-Z][a-z]+(?:['\u2019]s)?\b")
_CAPRUN = re.compile(r"\b[A-Z][a-z]+(?:['\u2019]s)?(?:\s+[A-Z][a-z]+(?:['\u2019]s)?)+\b")

# Capitalised health, food and brand terms that are not names. Anything else
# capitalised in the middle of a claim is treated as a possible name.
_TERMS = frozenset("""
vitamin vitamins type omega covid coronavirus alzheimer alzheimers parkinson parkinsons crohn crohns lyme
hashimoto hashimotos graves huntington huntingtons asperger aspergers down tourette tourettes raynaud
lou gehrig bell bells palsy epstein barr west nile zika ebola
tylenol advil motrin aleve benadryl zyrtec claritin lipitor xanax valium prozac zoloft adderall ritalin
ozempic wegovy mounjaro zepbound metformin viagra botox gatorade pepsi coke coca cola red bull monster
starbucks apple google bluetooth wifi wi fi gmo
keto ketogenic paleo mediterranean atkins dash whole vegan vegetarian
american british european asian african chinese japanese indian korean western eastern northern southern
himalayan celtic epsom manuka greek french italian mexican swiss dutch
north south east west america europe asia africa australia
""".split())


_OPENERS = frozenset("""does do did is are was were can could will would should may might must has have
why how what when where which who whether""".split())


def _only_terms(words) -> bool:
    return all(w.lower().replace("\u2019s", "").replace("'s", "") in _TERMS for w in words)


def _name_risk(text: str) -> bool:
    """A name, or a capitalised word that is not a known term, not at a sentence start."""
    if _NAMEISH.search(text):
        return True
    for run in _CAPRUN.finditer(text):               # two capitalised words in a row, even at the start
        words = run.group().split()
        if words[0].lower() in _OPENERS:              # "Does Tylenol ...": a question word, not a name
            words = words[1:]
        if words and not _only_terms(words):
            return True
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        for m in _CAPWORD.finditer(sent):
            if not sent[:m.start()].strip(" \"\u201c\u2018'([-"):
                continue                              # the first word of a sentence
            if not _only_terms([m.group()]):
                return True
    return False


def looks_personal(claim_text) -> bool:
    text = str(claim_text or "").replace("\u2019", "'").replace("\u2018", "'")
    return bool(_PERSONAL.search(text)) or _name_risk(text)


# The only claims whose result pages a search engine may index or the sitemap
# may list: the site's own examples. Same list, same order as EXAMPLES in
# static/app.js (a test compares them). A claim a person typed is never here.
CURATED_CLAIMS = (
    "Apple cider vinegar cures diabetes",
    "Cracking your knuckles causes arthritis",
    "Intermittent fasting improves brain function",
    "Detox teas flush toxins out of your body",
    "5G weakens your immune system",
    "Vaccines cause autism",
    "Ozempic cures alcoholism",
)


def _claim_form(claim_text) -> str:
    return re.sub(r"[\s.!?,;:]+$", "", re.sub(r"\s+", " ", str(claim_text or "")).strip()).casefold()


_CURATED_FORMS = frozenset(_claim_form(c) for c in CURATED_CLAIMS)


def is_curated_claim(claim_text) -> bool:
    return _claim_form(claim_text) in _CURATED_FORMS


def indexable_claim(claim_text) -> bool:
    """Curated and not personal. A search engine may see a result page only when this holds
    and saved_record_problems is empty."""
    return is_curated_claim(claim_text) and not looks_personal(claim_text)


def saved_record_problems(claim_text, verdict, cited, studies, breakdown, search_query, saved_at,
                          now=None) -> list:
    """
    What is wrong with a saved answer, by name; an empty list means sound. The
    same kinds scripts/saved_record_audit.py counts (all but the one that needs
    the check log): a cited study that is not among the studies, a breakdown
    whose "rests on" differs from the studies cited, no search query, a missing,
    unreadable or future save date. The page repairs most of these when it
    renders, so a person never sees them; a crawler is told to leave such a
    record out of the index and the sitemap all the same.
    """
    from datetime import datetime, timezone
    out = []
    if not str(claim_text or "").strip():
        out.append("no_claim")
    if looks_personal(claim_text):
        out.append("looks_personal")
    if verdict not in SAVED_VERDICTS:
        out.append("unknown_verdict")
    if not isinstance(cited, (list, tuple)) or not isinstance(studies, list):
        return out + ["unreadable_json"]
    pmids = {s.get("pmid") for s in studies if isinstance(s, dict)}
    if set(cited) - pmids:
        out.append("cited_not_in_studies")
    if isinstance(breakdown, dict) and breakdown.get("rests_on") is not None \
            and breakdown["rests_on"] != len(set(cited) & pmids):
        out.append("count_mismatch")
    if not str(search_query or "").strip():
        out.append("empty_query")
    if not saved_at:
        out.append("missing_date")
    else:
        try:
            d = datetime.fromisoformat(str(saved_at))
            d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            if d > (now or datetime.now(timezone.utc)):
                out.append("future_date")
        except ValueError:
            out.append("unparseable_date")
    return out
