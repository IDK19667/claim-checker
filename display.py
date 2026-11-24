"""
What the result page prints, worked out from fields the check already saved.

Nothing here asks a model for anything and nothing here changes an answer:
it only decides which of the saved sentences are shown where, so the same
sentence is never printed twice on one page. static/app.js carries the same
rules (breakdownView) for a check that has just run, and a test runs both on
the same records and compares them.
"""
import re

from markupsafe import Markup, escape

import evidence

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


# verdict.GUIDANCE_LINE, the same words (a test holds them equal; display
# does not import verdict and its provider plumbing).
GUIDANCE_LINE = "Rests on health guidance, not on these studies"


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
    line = str(result.get("evidence_line") or "").strip().rstrip(".")
    # An answer set by health guidance says so even when no study listed is
    # relied on (the one it cited was set aside).
    if line == GUIDANCE_LINE:
        return line + "."
    if not cited:
        return "No direct evidence either way: none of the studies tests this claim."
    if line:
        return line + "."
    tiers = {s.get("tier") or "moderate" for s in cited}
    word = "Strong" if "strong" in tiers else "Moderate" if tiers - {"weak", "retracted"} else "Weak"
    return f"{word} study designs."


# ---- Sources: why each study is on the page --------------------------------
# What each study design does and where it stops. static/app.js carries the
# same table (TYPES) for the study sheet; a test compares the two. The first
# matching label wins, as in the script, so "Retracted Publication" is first:
# a withdrawn trial is described as retracted, never as a trial.
DESIGN_NOTES = (
    ('Retracted Publication', 'retracted', 'Withdrawn by the journal after publication. Its findings should not be relied on.'),
    ('Meta-Analysis', 'strong', 'Combines the results of several studies into one estimate. It is only as sound as the studies it pools, and it can mix very different ones.'),
    ('Network Meta-Analysis', 'strong', 'Combines many studies to compare several treatments at once. Comparisons made only indirectly are less certain than head-to-head trials.'),
    ('Systematic Review', 'strong', 'Searches for every study on a question, using set rules for what counts. It is only as sound as the studies it found.'),
    ('Randomized Controlled Trial', 'strong', 'Randomly assigns people to a treatment or a comparison group. A small size, a short run or a narrow group limits what it shows.'),
    ('Practice Guideline', 'strong', "Recommendations a medical body wrote after reviewing the evidence. They reflect that body's judgment and can lag behind newer studies."),
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


def relied_on(study) -> bool:
    """True for a study the verdict relied on. A study classified off topic
    never is, whatever an old saved record put in its cited list."""
    return (bool(study.get("cited_in_verdict")) and not study.get("off_topic")
            and not evidence.withdrawn(study))


def relied_pmids(cited, studies) -> set:
    """The cited PMIDs less any study classified off topic, retracted or a
    journal notice (evidence.withdrawn): the set every count, mark and line
    on the page is read from, a saved answer's included. A cited PMID that
    names no listed study is not relied on either: nothing on the page can
    show it, so an answer citing only such PMIDs rests on nothing."""
    listed = {s.get("pmid") for s in studies or [] if isinstance(s, dict)}
    off = {s.get("pmid") for s in studies or [] if isinstance(s, dict)
           and (s.get("off_topic") or evidence.withdrawn(s))}
    return (set(cited or []) & listed) - off


def source_groups(studies) -> dict:
    """
    The study list's groups, as indexes into `studies`, in page order: what the
    verdict relied on; background only; read and not relied on; and excluded
    as off topic. Every study is in exactly one. static/app.js sourceGroups
    does the same; a test runs both on the same records.
    """
    out = {"cited": [], "back": [], "others": [], "excluded": []}
    for i, s in enumerate(studies or []):
        key = ("excluded" if s.get("off_topic") else "cited" if relied_on(s)
               else "back" if s.get("background") else "others")
        out[key].append(i)
    return out


def study_why(study) -> str:
    """
    One line on why a study is listed: what the verdict did with it and, for a
    study it leaned on, what that kind of study does and where it stops. All of
    it is read off fields the record already carries. Off topic is checked
    first: a study set aside is never described as relied on.
    """
    if study.get("off_topic"):
        return f"Read and set aside: {str(study['off_topic']).rstrip('.')}."
    if study.get("cited_in_verdict"):
        label, _cls, note = design_note(study.get("publication_types"))
        out = ["Relied on for the verdict."]
        if label != DEFAULT_DESIGN[0]:
            out.append(note)
        if study.get("population"):
            out.append(f"Studied in: {study['population']}.")
        return " ".join(out)
    if study.get("background"):
        why = str(study.get("background_why") or "").rstrip(".")
        return f"Background only, not counted: {why}." if why else "Background only, not counted."
    return "Read, not relied on."


def _count(n, one, many) -> str:
    return f"{n} {one if n == 1 else many}"


def _join_and(items) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def desk_stops(result) -> list:
    """The stops the desk front shows for this result, by name, in order. A
    stop with nothing real to show is left out: Weighed needs a study, Research
    needs a study and something written about it. static/app.js deskStops is
    the same; a test runs both on the same records."""
    studies = (result or {}).get("studies") or []
    out = ["Claim", "Answer", "Read"]
    if studies:
        out.append("Weighed")
        bv = breakdown_view(result)
        says = (bv["says"] if bv else [result.get("explanation")]) if result else []
        if bv or any(str(x or "").strip() for x in says):
            out.append("Research")
    out.append("Open")
    return out


def desk_lines(result) -> dict:
    """The desk front's counted sentences, each worded from the record's own
    study list and nothing else: the Read stop's heading and line, the key to
    the desk, and what the verdict rests on.
    static/app.js deskLines is the same; a test runs both on the same records."""
    studies = (result or {}).get("studies") or []
    g = source_groups(studies)
    n, r, b, o, x = len(studies), len(g["cited"]), len(g["back"]), len(g["others"]), len(g["excluded"])
    if not n:
        return {"read": "No studies read.", "path": "", "legend": "", "rests": ""}
    path = _join_and([f"{r} relied on" if r else "None relied on",
                      f"{b} background only" if b else "",
                      f"{o} read and not relied on" if o else "",
                      f"{x} set aside as off topic" if x else ""]) + "."
    rest = []
    if o + b:
        rest.append(f"the {o + b} read but not relied on" + (" at its edge" if r else ""))
    if x:
        rest.append(f"the {x} off topic face down")
    if r:
        legend = f"In the light: the {_count(r, 'paper', 'papers')} the answer relies on" + (
            ", with " + _join_and(rest) if rest else "") + "."
    else:
        legend = "In the light: " + _join_and(rest) + ". None was relied on."
    more = []
    if o + b:
        more.append(f"{o + b} more {'was' if o + b == 1 else 'were'} read and not relied on")
    if x:
        more.append(f"{x} {'was' if x == 1 else 'were'} set aside as off topic")
    tail = _join_and(more)
    rests = f"Rests on {_count(r, 'study', 'studies')}." + (" " + tail[:1].upper() + tail[1:] + "." if tail else "")
    return {"read": _count(n, "study", "studies") + " read.", "path": path, "legend": legend,
            "rests": rests}


def _plural_design(label: str) -> str:
    """The plural of a lower-case design label: "meta-analyses", "case studies",
    "addresses"; a label already plural or with no plural ("case reports",
    "news") stays. static/app.js pluralDesign is the same."""
    if re.search(r"analysis$", label):
        return re.sub(r"analysis$", "analyses", label)
    if re.search(r"[^aeiou]y$", label):
        return label[:-1] + "ies"
    if re.search(r"(ss|x|ch|sh)$", label):
        return label + "es"
    if label.endswith("s"):
        return label
    return label + "s"


def rests_by_design(studies) -> list:
    """The relied-on studies grouped by design, in first-seen order, each with
    its count, a plural label and the study numbers: "2 meta-analyses (Studies
    1, 4)". static/app.js restsByDesign is the same."""
    order, by = [], {}
    for i, s in enumerate(studies or []):
        if not relied_on(s):
            continue
        d = design_note(s.get("publication_types"))[0]
        if d not in by:
            by[d] = []
            order.append(d)
        by[d].append(i + 1)
    out = []
    for d in order:
        ns = by[d]
        label = d.lower()
        if len(ns) > 1:
            label = _plural_design(label)
        if d == DEFAULT_DESIGN[0]:
            label += ", design not labelled"
        out.append({"count": len(ns), "label": label,
                    "refs": ("Studies " if len(ns) > 1 else "Study ") + ", ".join(str(n) for n in ns)})
    return out


# Plain digits and an ASCII word edge, as in the script's /\b...\d/: "Study ٣" is
# text on both sides and "éStudy 2" a link on both.
_REF_RE = re.compile(r"(?<![A-Za-z0-9_])(Stud(?:y|ies))\s+([0-9]+(?:\s*(?:,\s*and|,|and|&amp;)\s*[0-9]+)*)", re.I)


def desk_refs(text, count) -> Markup:
    """The text, escaped, with each study number it names ("Study 4",
    "Studies 1, 4 and 6") made a link to that study's row in the Weighed list.
    A number past the list stays text. static/app.js deskRefs is the same; a
    test runs both on the same strings."""
    safe = str(escape(text or ""))

    def link(m):
        def one(d):
            i = int(d.group(0))
            if 1 <= i <= count:
                return (f'<a class="ref" href="#row-{i}" data-n="{i}" '
                        f'aria-label="Study {i}, show it in the list">{i}</a>')
            return d.group(0)
        return m.group(1) + " " + re.sub(r"[0-9]+", one, m.group(2))

    return Markup(_REF_RE.sub(link, safe))


def desk_named(result) -> list:
    """The study numbers the short reasoning names ("What the research says"),
    sorted: those rows say "The answer names it". static/app.js deskNamed is
    the same."""
    if not result:
        return []
    n = len(result.get("studies") or [])
    bv = breakdown_view(result)
    lines = [x for x in (bv["says"] if bv else [result.get("explanation")]) if str(x or "").strip()]
    out = set()
    for line in lines:
        for m in _REF_RE.finditer(str(escape(line))):
            out.update(i for i in (int(d) for d in re.findall(r"[0-9]+", m.group(2))) if 1 <= i <= n)
    return sorted(out)


def safe_href(url):
    """The address when it may go in an href: http or https, or a path on this
    site; otherwise None, and the page leaves the link out. static/app.js
    safeHref is the same; a test runs both on the same addresses."""
    from urllib.parse import urlsplit
    raw = str(url if url is not None else "").strip()
    # A space or a control character inside is refused: the browser drops a
    # tab or a newline and reads what is left.
    if not raw or re.search(r"[\x00-\x20\x7f]", raw):
        return None
    if raw.startswith("/") and not raw.startswith("//") and not raw.startswith("/\\"):
        return raw
    # Only the plain form, http:// or https:// and then the host, which both
    # sides read the same way.
    if not re.match(r"(?i)https?://[^/\\?#]", raw):
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    return raw if parts.scheme.lower() in ("http", "https") and parts.netloc else None


# ---- Dates a reader sees ----------------------------------------------------
# One rule: the server prints a day in the site's home zone, inside a <time>
# holding the exact instant; the script rewrites each <time> to the reader's
# own day. With scripts off the day is the home zone's, never UTC's.
HOME_ZONE = "America/Los_Angeles"
_home_tz_memo: list = []


def home_tz():
    """The home zone, or a fixed UTC minus 8 (one logged warning) when the
    time zone database is missing. Never raises."""
    if not _home_tz_memo:
        from datetime import timedelta, timezone
        try:
            from zoneinfo import ZoneInfo
            _home_tz_memo.append(ZoneInfo(HOME_ZONE))
        except Exception:  # noqa: BLE001 - no tz database: a fixed offset, never a crash
            import logging
            logging.getLogger(__name__).warning("Time zone %s is missing; dates use UTC minus 8", HOME_ZONE)
            _home_tz_memo.append(timezone(timedelta(hours=-8), "UTC-8"))
    return _home_tz_memo[0]


def as_instant(value):
    """An aware datetime from a saved ISO date (a date without a zone is UTC), or None."""
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        d = value
    else:
        try:
            d = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def home_day(instant) -> str:
    """"23 November 2026": the instant's day in the home zone."""
    d = as_instant(instant).astimezone(home_tz())
    return f"{d.day} {d.strftime('%B %Y')}"


def with_time(line, when) -> Markup:
    """A line holding a day, with that day wrapped in <time datetime> so the
    script can print the reader's own day. `when` is {"iso", "day"}."""
    line = str(line or "")
    day = (when or {}).get("day") or ""
    if not day or day not in line:
        return escape(line)
    i = line.index(day)
    return Markup("{}<time datetime=\"{}\">{}</time>{}").format(
        line[:i], (when or {}).get("iso") or "", day, line[i + len(day):])


def sentence_case(text) -> str:
    """
    A listed claim as a list shows it: the first letter raised only when the
    first word is all lower case letters (its parts joined by a hyphen or an
    apostrophe, trailing punctuation aside). "mRNA", "pH", "DNA", "COVID-19",
    a number or a quotation mark at the start are left exactly as stored.
    The page only: stored text and links keep the wording as saved.
    static/app.js sentenceCase is the same; a test runs both on the same inputs.
    """
    text = str(text or "")
    word = text.split(None, 1)[0] if text.strip() else ""
    if text[:1] != word[:1]:
        return text
    parts = re.split(r"[-'\u2019]", word.rstrip(".,;:!?"))
    if all(p.isalpha() and p.islower() for p in parts):
        return text[:1].upper() + text[1:]
    return text


def typed_note_parts(typed, reason=None):
    """
    The note in three parts: the words before the reader's text, the
    reader's text, and the words after it, or None when there is nothing to
    explain. The page sets the middle part in <bdi>, so right-to-left text
    the reader typed cannot reorder the sentence around it.
    static/app.js typedNoteParts is the same.
    """
    if not typed:
        return None
    if reason == "wording":
        return ("Showing the saved check for this claim. You typed \u201c", str(typed), "\u201d.")
    return ("Spelling fixed. You typed \u201c", str(typed), "\u201d.")


def typed_note(typed, reason=None) -> str:
    """
    The line under the claim when the answer is to other words than the
    reader's: their own words, and why they differ. A fixed spelling says so;
    a saved check of the same claim in other words says that instead. Empty
    when there is nothing to explain. static/app.js typedNoteParts(...).join("") is the same; a
    test runs both on the same inputs.
    """
    return "".join(typed_note_parts(typed, reason) or ())


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


# A run of 7 or more digits reads as a phone number, however it is spaced:
# digits apart only by spaces, dots, dashes or brackets count as one run.
_PHONE_RUN = re.compile(r"\d(?:[\s().-]*\d){6,}")


def looks_personal(claim_text) -> bool:
    text = str(claim_text or "").replace("\u2019", "'").replace("\u2018", "'")
    return bool(_PERSONAL.search(text)) or bool(_PHONE_RUN.search(text)) or _name_risk(text)


# Vouching. A typed claim is shown in a public list only when every word of it
# is vouched for: a word in this closed list of ordinary claim words (function
# words, question openers, the plain verbs and nouns of claims, and _TERMS; no
# names), or a word in the titles or abstracts of the studies that check read
# (never their authors or journals). Words are compared case-folded, with a
# plain final "s" or "es" folded. A token with digits is vouched for when it
# has at most 5 of them. A lowercase name or a word the studies never use
# keeps a claim off every public list; the check itself is untouched.
_VOUCH_WORDS = frozenset("""
a an the and or but not no never without non if then than so as of to in on at by for from with into over under about after before during while until within up down out off away
is are was were be been being am has have had do does did doing done can could may might will would shall should must
it its this that these those there here what which who why how when where whether
you your someone anyone everyone people person they their them
dont doesnt didnt isnt arent wasnt werent cant cannot wont youre
true false just only also even still too very much many more most less least few lot plenty enough some any all every each other same own several like
good bad better best worse worst safe healthy harmful helpful effective natural normal regular extra main
high higher low lower big small long longer short fast faster quick quickly fully regularly daily often always usually sometimes soon straight clear
new old young older younger early late warm hot cold
cause causing prevent stop cure treat help work make made take taking give giving get getting got go going keep put need use using find feel mean pass live remove
raise increase boost improve reduce cut cutting decrease shorten speed ease relieve soothe protect harm link linked associated affect change catch catching
eat eating drink drinking sleep sleeping breathing exercise exercising recovery
risk chance effect benefit symptom sign problem
health body life day week month year time morning
child children baby babies adult man men woman women pregnant pregnancy
food diet water salt sugar weight amount dose pill tablet medicine
flu cold cough pain sickness fever infection teeth
one two three four five six seven eight nine ten
""".split()) | _TERMS
_VOUCH_TOKEN = re.compile(r"[^\W_]+")


def _vouch_tokens(text) -> list:
    return _VOUCH_TOKEN.findall(str(text or "").replace("\u2019", "'").replace("'", "").casefold())


def _vouch_forms(word: str) -> set:
    forms = {word}
    if len(word) > 3 and word.endswith("s"):
        forms.add(word[:-1])
    if len(word) > 4 and word.endswith("es"):
        forms.add(word[:-2])
    return forms


_VOUCH_CLOSED = frozenset(f for w in _VOUCH_WORDS for f in _vouch_forms(w))


def vouched(claim_text, studies) -> bool:
    """Every word of the claim is vouched for (see _VOUCH_WORDS)."""
    seen = None
    for w in _vouch_tokens(claim_text):
        if any(ch.isdigit() for ch in w):
            if sum(ch.isdigit() for ch in w) > 5:
                return False
            continue
        forms = _vouch_forms(w)
        if forms & _VOUCH_CLOSED:
            continue
        if seen is None:
            seen = set()
            for s in studies or []:
                if isinstance(s, dict):
                    for x in _vouch_tokens(f"{s.get('title') or ''} {s.get('abstract') or ''}"):
                        seen |= _vouch_forms(x)
        if not forms & seen:
            return False
    return True


def public_claim(claim_text, studies) -> bool:
    """The one screen for every public list: a curated example always; any
    other claim when it does not look personal and every word is vouched for."""
    if is_curated_claim(claim_text):
        return True
    return not looks_personal(claim_text) and vouched(claim_text, studies)


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
    whose "rests on" differs from the studies cited, a cited study classified
    off topic, no search query, a missing,
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
    # Only a listed study that is set aside: a PMID no listed study has is
    # the flag above, not this one.
    if (set(cited) & pmids) - relied_pmids(cited, studies):
        out.append("cited_off_topic")
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
