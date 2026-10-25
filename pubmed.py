"""
Thin wrapper around NCBI's E-utilities (PubMed's free public API).

No API key is required for basic use (rate limit: 3 requests/second).
Docs: https://www.ncbi.nlm.nih.gov/books/NBK25500/

Two calls are made per lookup:
  1. esearch  -> turns a search string into a list of PubMed IDs (PMIDs)
  2. efetch   -> turns a list of PMIDs into full record XML (title,
                 abstract, authors, publication types, journal, year)

Publication type (e.g. "Randomized Controlled Trial" vs "Case Reports")
is what lets the verdict step weigh a large human trial more heavily
than a small preliminary study.
"""

import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

# One way only: evidence.py reads records, it never fetches them. `measures`
# is a pure text predicate and is wanted here because whether a record names
# the claim's subject decides whether it is worth a slot at all.
import evidence

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = 15

# NCBI allows 3 requests a second without a key, and answers 429 past it.
# One check is now five or six calls in a row (four searches and a fetch,
# and a surrogate search when there is one), which is past the limit, so
# they are spaced instead of raced. A lock,
# because Flask serves checks on threads and the limit is per client.
# Spaced a little wider than the limit itself: requests leave 0.35 s apart
# can still arrive four to a second. And one that is turned away anyway
# (429), meets a passing server error, or never connects, is asked again
# after a pause, and once more after a longer one if it still fails: the
# review search made a check one call longer, and in the 21-claim run 3
# checks in 26 failed on a single refused call. A call that fails every time
# is logged and raised, never skipped: a search missing from the merge
# would change which studies the verdict rests on without a word.
MIN_INTERVAL = 0.4
RETRY_AFTER = 1.5
RETRY_AFTER_LONG = 5.0
_RETRY_STATUS = {429, 500, 502, 503, 504}
_last_call = 0.0
_pace = threading.Lock()
_log = logging.getLogger("evident.pubmed")


def _paced_get(endpoint: str, params: dict):
    global _last_call
    with _pace:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    return requests.get(f"{EUTILS_BASE}/{endpoint}", params=params, timeout=TIMEOUT)


def _asked(params: dict) -> str:
    """What a call was for, short enough for a log line. Never the tool
    parameters: they carry the operator's email."""
    term = params.get("term")
    if term:
        return term if len(term) <= 100 else term[:97] + "..."
    ids = [i for i in str(params.get("id") or "").split(",") if i]
    return f"{len(ids)} ids" if len(ids) != 1 else f"id {ids[0]}"


def _get(endpoint: str, params: dict):
    """One E-utilities call, no faster than NCBI's published rate, asked
    again (after RETRY_AFTER, then after RETRY_AFTER_LONG) if NCBI turns it
    away, has a passing fault, or cannot be reached. Every failed try is
    logged. A connection that timed out has already cost up to TIMEOUT
    seconds, so it gets one more try, not two."""
    pauses = (RETRY_AFTER, RETRY_AFTER_LONG)
    failed = 0
    while True:
        resp = fault = None
        try:
            resp = _paced_get(endpoint, params)
        except (requests.ConnectionError, requests.Timeout) as e:
            fault = e
        if fault is None and resp.status_code not in _RETRY_STATUS:
            resp.raise_for_status()
            return resp
        what = type(fault).__name__ if fault else f"HTTP {resp.status_code}"
        tries = 2 if fault else len(pauses) + 1
        failed += 1
        if failed >= tries:
            _log.warning("NCBI %s failed %d times (%s), giving up: %s",
                         endpoint, failed, what, _asked(params))
            if fault:
                raise fault
            resp.raise_for_status()
            raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
        pause = pauses[failed - 1]
        _log.warning("NCBI %s failed (%s), asking again in %.1f s: %s",
                     endpoint, what, pause, _asked(params))
        time.sleep(pause)


def _tool_params():
    """NCBI asks (but doesn't require) a tool name + email for courtesy."""
    return {
        "tool": os.environ.get("NCBI_TOOL_NAME", "health-claim-checker"),
        "email": os.environ.get("NCBI_EMAIL", ""),
    }


def search_pubmed(query: str, max_results: int = 8) -> list[str]:
    """Return a list of PMIDs (strings) matching the query, most relevant first."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",
        **_tool_params(),
    }
    data = _get("esearch.fcgi", params).json()
    result = data.get("esearchresult", {})
    missing = (result.get("warninglist") or {}).get("quotedphrasesnotfound") or []
    if missing:
        _UNKNOWN_PHRASES[query] = [m.strip('"') for m in missing]
    return result.get("idlist", [])


# Quoted phrases PubMed reported it has never seen, by the query that asked,
# read once and dropped by search_phrases.
_UNKNOWN_PHRASES: dict[str, list[str]] = {}


def _text_or_none(el):
    return el.text if el is not None else None


def year_of(value) -> str | None:
    """The first four-digit year in a PubMed date, or None. "1995 Nov 22-29" is 1995."""
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(value or ""))
    return m.group(1) if m else None


def _parse_article(article_el) -> dict:
    """Pull the fields we care about out of one <PubmedArticle> element."""
    medline = article_el.find("MedlineCitation")
    article = medline.find("Article")

    pmid = _text_or_none(medline.find("PMID"))
    # Titles can contain inline markup (<i>, <sub>, <sup>); .text alone
    # would stop at the first tag and truncate the title.
    title_el = article.find("ArticleTitle")
    title = "".join(title_el.itertext()).strip() if title_el is not None else ""
    title = title or "(untitled)"

    # Abstract can have multiple <AbstractText> sections (Background, Methods, etc.)
    abstract_parts = []
    abstract_el = article.find("Abstract")
    if abstract_el is not None:
        for chunk in abstract_el.findall("AbstractText"):
            label = chunk.get("Label")
            text = "".join(chunk.itertext()).strip()
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(abstract_parts) if abstract_parts else None

    # Journal + year. A record with no plain year carries a MedlineDate
    # instead, which is free text: "1995 Nov 22-29", "1998-1999 Winter".
    journal_el = article.find("Journal")
    journal_title = None
    year = None
    if journal_el is not None:
        journal_title = _text_or_none(journal_el.find("Title"))
        pub_date = journal_el.find("JournalIssue/PubDate")
        if pub_date is not None:
            year = year_of(_text_or_none(pub_date.find("Year")) or _text_or_none(
                pub_date.find("MedlineDate")
            ))

    # Authors
    authors = []
    author_list = article.find("AuthorList")
    if author_list is not None:
        for author_el in author_list.findall("Author"):
            last = _text_or_none(author_el.find("LastName"))
            fore = _text_or_none(author_el.find("ForeName"))
            if last:
                authors.append(f"{fore} {last}".strip() if fore else last)

    # Publication types (Randomized Controlled Trial, Meta-Analysis,
    # Case Reports, Review, Observational Study, etc.) — this is the
    # single most useful signal for weighing evidence quality.
    pub_types = []
    pub_type_list = article.find("PublicationTypeList")
    if pub_type_list is not None:
        for pt in pub_type_list.findall("PublicationType"):
            if pt.text:
                pub_types.append(pt.text)

    # Data availability statement (present on some newer articles) —
    # kept for the future "deep dive" feature (not used yet).
    data_statement = None
    coi_and_data = medline.findall(".//DataBankList/DataBank")
    if coi_and_data:
        names = [
            _text_or_none(db.find("DataBankName"))
            for db in coi_and_data
            if _text_or_none(db.find("DataBankName"))
        ]
        if names:
            data_statement = ", ".join(names)

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "journal": journal_title,
        "year": year,
        "authors": authors,
        "publication_types": pub_types,
        "data_banks": data_statement,  # reserved for future deep-dive feature
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def fetch_details(pmids: list[str]) -> list[dict]:
    """Given a list of PMIDs, return full parsed records (title/abstract/etc.)."""
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
        **_tool_params(),
    }
    root = ET.fromstring(_get("efetch.fcgi", params).content)
    records = []
    for article_el in root.findall("PubmedArticle"):
        try:
            records.append(_parse_article(article_el))
        except Exception:
            # One malformed record shouldn't take down the whole request.
            continue
    if len(records) < len(set(pmids)):
        got = {r["pmid"] for r in records}
        _log.warning("efetch returned %d of %d records, missing: %s", len(records),
                     len(set(pmids)), ", ".join(p for p in dict.fromkeys(pmids) if p not in got))
    return records


def search_and_fetch(query: str, max_results: int = 8,
                     surrogate: str = "") -> list[dict]:
    """
    One query in, the studies worth reading out. Runs the searches below,
    fetches the merged list, and drops the records with nothing in them to
    read. The single seam between this module and the rest of the app, so
    everything that wants evidence goes through the same filter.

    `surrogate` is an optional OR-group naming the indirect route to the
    claim's outcome: "dihydrotestosterone OR DHT" for a claim about hair
    loss. When it is given, the last INDIRECT_SLOTS of the list are kept for
    papers found that way, because some claims have no study that measures
    the thing claimed and the nearest evidence is worth showing, labelled.
    """
    keep = INDIRECT_SLOTS if surrogate else 0
    found = _search_all(query, max_results)
    # One fetch for all of it: the review candidates, so their titles can
    # pick the leads, and the first `want` of the rest, which covers the list
    # whichever reviews lead (taking three out of a list moves its sixteenth
    # up by at most three).
    wanted = list(dict.fromkeys(found["pool"] + found["ranked"][:found["want"]]))
    indirect = []
    if surrogate:
        indirect = search_surrogate(query, surrogate, max_results=keep + 1)
    records = {r["pmid"]: r for r in fetch_details(list(dict.fromkeys(wanted + indirect)))}
    lead = lead_reviews(found["pool"], query, found["votes"], records)
    pmids = _with_lead(found, lead)
    pmids += [p for p in indirect if p not in pmids]
    return prioritise(usable([records[p] for p in pmids if p in records]),
                      query, surrogate)[:max_results]


# The fewest studies worth narrowing to. Below this, dropping the papers that
# only half-match would trade noise for nothing, so they stay and the prompt's
# own instruction not to cite an irrelevant study has to carry it.
MIN_ON_TOPIC = 3


def prioritise(studies: list[dict], query: str, surrogate: str = "") -> list[dict]:
    """
    The studies that are about this claim, first, and the ones that are about
    neither half of it dropped.

    PubMed ranks on the words it matched, and when little matches both halves
    of a query it will happily return papers that match one. A search for
    creatine and hair loss comes back with trials of baricitinib for alopecia
    areata and with chemotherapy trials that mention creatinine clearance:
    real papers, about neither creatine nor what the claim asks. That is how a
    hair-lotion trial ends up quoted in an answer about creatine, and how the
    one paper actually titled "Does creatine cause hair loss?" gets crowded
    out of eight slots.

    Three bands, stable within each so PubMed's own relevance order survives:
    both halves named, the subject only, neither. A paper found through the
    surrogate search counts as on-outcome, because measuring the indirect
    route is the whole reason it is here.
    """
    groups = core_groups(query)
    if not groups:
        return list(studies or [])
    subject, outcome = groups[0], (groups[1] if len(groups) > 1 else "")
    # A search that asked for the claim's setting has already had PubMed hold
    # every paper to it, so the subject is read the way a person would read it
    # (see _names_subject). Any other search is read exactly as it was.
    names = _names_subject if setting_groups(query) else evidence.measures

    def band(study) -> int:
        if not names(study, subject):
            return 0
        if not outcome or evidence.measures(study, outcome):
            return 2
        return 2 if evidence.indirect(study, outcome, surrogate) else 1

    bands = {2: [], 1: [], 0: []}
    for study in studies or []:
        bands[band(study)].append(study)
    on_topic = bands[2] + bands[1]
    return on_topic if len(on_topic) >= MIN_ON_TOPIC else on_topic + bands[0]


# The endings a word can take and still be the same word: vaccine, vaccines,
# vaccination, vaccinated. Spelled out, not a prefix, so that "creatine" does
# not reach "creatinine".
_WORD_END = re.compile(r"(?:ations?|ion|ing|ed|es|s|e)$")
_WORD_FAMILY = r"(?:e|es|s|ed|ing|ings|ation|ations|ated|ates|ate|ion|ions)?"


def _word_pattern(word: str) -> str:
    root = word.lower()
    if len(root) >= 6:
        stripped = _WORD_END.sub("", root)
        root = stripped if len(stripped) >= 4 else root
    return re.escape(root) + _WORD_FAMILY


def _names_subject(study: dict, group: str) -> bool:
    """
    True when this paper is about the thing `group` names, allowing the other
    form of a word: a paper on "pertussis vaccination" is about "pertussis
    vaccine", and evidence.measures, which wants the term as written, says it
    is not. The paper counts when, for some term of the group, every word of
    the term is in the title or the opening of the abstract.
    """
    if evidence.measures(study, group):
        return True
    study = study or {}
    body = evidence._us(f"{study.get('title') or ''}  "
                        f"{str(study.get('abstract') or '')[:evidence.SUBJECT_WINDOW]}")
    for term in re.split(r"\s+OR\s+", (group or "").strip("() ")):
        words = [w for w in re.findall(r"[A-Za-z0-9]+", evidence._us(term)) if len(w) > 2]
        if words and all(re.search(r"\b" + _word_pattern(w) + r"\b", body, re.IGNORECASE) for w in words):
            return True
    return False


# How many of the slots an indirect search may take. Two: enough to show the
# mechanism evidence exists, few enough that it can never crowd out the
# studies that measure the claim itself.
INDIRECT_SLOTS = 2


def search_surrogate(query: str, surrogate: str, max_results: int = 4) -> list[str]:
    """
    The same first group, searched against the surrogate outcome instead of
    the claim's own. "(creatine OR creatine monohydrate) AND (hair loss OR
    alopecia)" becomes "(creatine OR creatine monohydrate) AND
    (dihydrotestosterone OR DHT)", which is how the 2009 trial that measured
    DHT in rugby players is reached at all: it never says "hair loss". The
    claim's setting stays on it: a stand-in measured in the wrong people is
    not the route to the claim.
    """
    groups = core_groups(query)
    if not groups or not surrogate.strip():
        return []
    group = surrogate.strip()
    if not group.startswith("("):
        group = f"({group})"
    return search_pubmed(" AND ".join([groups[0], group] + setting_groups(query)),
                         max_results=max_results)


def split_and(query: str) -> list[str]:
    """
    Split a query on its top-level ANDs, leaving bracketed groups whole.
    "(a OR b) AND (c OR d)" is two terms, not four: a synonym group is one
    idea, and chopping into it would turn a broadening into a rewrite.
    """
    parts, depth, current = [], 0, []
    tokens = re.split(r"(\s+AND\s+|\(|\))", query)
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and re.fullmatch(r"\s+AND\s+", token or ""):
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(token or "")
    parts.append("".join(current).strip())
    return [p for p in parts if p]


# A claim about stopping a treatment is tested by studies of stopping it.
# "Statins after a heart attack" finds the trials of taking them, which say
# nothing about whether it is safe to quit, so the search carries a group of
# its own for stopping (see verdict.stopping_query). It goes last, so the
# first two groups stay the claim's subject and its outcome.
#
# The words are asked for in the title. Anywhere in the abstract, "withdrawal"
# and "stopping" are in every review of a drug's side effects, and a search
# for statins and stopping came back as eight reviews of statin muscle pain.
# In the title they are the study's subject: "Discontinuation of statin
# therapy after myocardial infarction". The stems catch discontinued,
# stopped and withdrawn.
DISCONTINUATION_GROUP = ("(discontinu*[ti] OR withdraw*[ti] OR cessation[ti] OR deprescrib*[ti] "
                         "OR stop*[ti])")
_STOPPING_TERM = re.compile(r"^(?:discontinu\w*|withdraw\w*|cessation|deprescrib\w*|stop\w*|quit\w*|"
                            r"interrupt\w*|taper\w*|wean\w*|non-?adheren\w*)$", re.IGNORECASE)


def _is_stopping_group(group: str) -> bool:
    """True for a group made only of words for stopping, field tags and stems included."""
    terms = [re.sub(r"\[[^\]]*\]$", "", t.strip()).strip('"*')
             for t in re.split(r"\s+OR\s+", (group or "").strip().strip("()"))]
    return bool(terms) and all(_STOPPING_TERM.match(t) for t in terms)


def has_discontinuation(query: str) -> bool:
    """True when some term of the query is already about stopping."""
    return bool(re.search(r"\b(?:discontinu|withdraw|cessation|deprescrib|stopp|stop\b|quit|"
                          r"interrupt|taper|wean|non-?adheren)", query or "", re.IGNORECASE))


def add_discontinuation(query: str) -> str:
    """
    The query with a group for stopping ANDed on, so the search is for
    studies of stopping what the query is about. Unchanged when it already
    has a term for stopping, or has nothing to add the group to.
    """
    if not split_and(query or "") or has_discontinuation(query):
        return query
    return f"{query} AND {DISCONTINUATION_GROUP}"


# ---------------------------------------------------------------------------
# The claim's setting: who it is about, and when
#
# A claim says who it is about ("in older adults", "newborns") and when
# ("during pregnancy", "before bed", "after a heart attack"). The search step
# was told a third group would only exclude trials, so it left the setting
# out, and a claim about vaccinating mothers was searched as a claim about
# vaccinating babies: PubMed returned infant vaccine trials and uptake
# surveys and not one study of vaccinating a mother.
#
# So the setting is read from the claim in code, from a general vocabulary of
# populations, life stages, conditions and times, and ANDed on as a group of
# its own wherever the query is used (verdict.finish_query), whatever the
# model wrote and whether the query is new or saved. It cannot be lost by a
# model that forgets it, because the model is not the one who adds it.
# It goes after the subject, the outcome and any group for stopping, so the
# first two groups stay the claim's subject and its outcome; broaden_query
# never drops it, and the phrase, review and surrogate searches carry it the
# way they carry the group for stopping.
#
# Each term is asked for as the paper's own word in its title or abstract
# ([tiab]), which is also what marks a group as the setting's: the model
# writes no field tags, so a group made only of [tiab] terms is one this
# module added. It keeps the setting honest, too: a study is about mothers
# when it says so, not when PubMed's subject headings reach it by way of
# "Infant, Newborn".
# ---------------------------------------------------------------------------

# At most this many settings go into one search. Every one is another AND, and
# a third is a guess at which of the claim's words the studies share. The two
# that say most are kept: a time or a condition first, then who.
MAX_SETTINGS = 2

# "after a heart attack", "who have had a stroke", "following surgery".
_AFTER = (r"(?:after|following|post-?|since|had|have\s+had|has\s+had|survivors?\s+of|survived|"
          r"history\s+of|previous|prior|recent|suffered|second|recurrent|repeat)\s+"
          r"(?:(?:a|an|your|the|having|their)\s+)*")
# "people with ...", "patients who have ...", "if you have ...".
_GROUP_NOUN = (r"(?:people|persons?|patients|adults|children|kids|men|women|those|individuals|anyone|"
               r"someone|sufferers|folks|seniors|teens|teenagers|elderly|adolescents|smokers|mothers)")
_WITH = (r"(?:" + _GROUP_NOUN + r"\s+(?:with|having|who\s+(?:have|has|had|suffer\s+from|are\s+diagnosed\s+with)|"
         r"living\s+with|diagnosed\s+with|suffering\s+from)|"
         r"(?:if|when|whether)\s+you(?:\s+have|\s+are|['’]re|\s+suffer\s+from))\s+"
         r"(?:(?:a|an|the|any|chronic|severe|mild|existing|early|advanced|known|established|untreated|"
         r"uncontrolled)\s+)*")
# A number that follows "over" or "aged" is an age, unless a unit comes next.
_UNIT = (r"(?!\s*(?:%|percent|per\s?cent|mg|mcg|g\b|ml|kg|lbs?|mins?|minutes?|hours?|hrs?|days?|weeks?|"
         r"months?|times|bpm|calories|kcal|reps?|sessions?|mmhg|iu|units?|cm|km|miles?))")


def _setting(key: str, rank: int, claim: str, terms: list[str], hides: tuple = ()) -> dict:
    """
    One setting: its key, how much it says about the search (0 a time or a
    condition, 1 an age or a life stage, 2 a sex), the claim wording that
    names it, the words a paper uses for it, and the settings it makes
    redundant ("before pregnancy" hides "during pregnancy").
    """
    return {"key": key, "rank": rank, "claim": re.compile(claim, re.IGNORECASE),
            "terms": terms, "hides": hides}


SETTINGS = [
    # --- times and life events -------------------------------------------
    _setting("preconception", 0,
             r"\b(?:before|prior\s+to|ahead\s+of)\s+(?:(?:a|an|the|your|their|her)\s+)?"
             r"(?:pregnan\w*|conception|conceiving)\b|"
             r"\b(?:try|tries|trying|attempt\w*|plan\w*)\s+(?:to\s+)?(?:conceive|get\s+pregnant|"
             r"become\s+pregnant)|\btrying\s+for\s+a\s+(?:baby|child)|"
             r"\b(?:pre|peri)-?conception\w*|\bpre-?pregnancy\b",
             ["preconception*", "periconception*", "pre-pregnancy", "prepregnancy", "before pregnancy",
              "before conception", "prior to pregnancy", "prior to conception",
              "before and during pregnancy", "planning pregnancy", "planning a pregnancy"],
             hides=("pregnancy",)),
    _setting("pregnancy", 0,
             r"\bpregnan\w*|\bexpect(?:ant|ing)\s+(?:mothers?|women|moms?)\b|\bprenatal\b|\bantenatal\b|"
             r"\bgestation\w*|\btrimester\b|\b(?:mothers?|moms?)[- ]to[- ]be\b|\bmaternal\b",
             ["pregnan*", "maternal", "prenatal", "antenatal"]),
    _setting("postpartum", 0,
             r"\bpost-?partum\b|\bpostnatal\b|\bafter\s+(?:giving\s+birth|delivery|childbirth|"
             r"having\s+(?:a\s+)?baby)\b|\bnew\s+(?:mothers?|moms?)\b",
             ["postpartum", "postnatal", "after delivery", "after childbirth", "puerperal"]),
    _setting("breastfeeding", 0,
             r"\b(?:while|during|when|whilst)\s+(?:you\s+are\s+|she\s+is\s+)?(?:breast-?feeding|nursing|"
             r"lactating)\b|\b(?:breast-?feeding|nursing|lactating)\s+(?:mothers?|women|moms?)\b",
             ["breastfeeding", "breast feeding", "lactation", "lactating"]),
    _setting("before surgery", 0,
             r"\b(?:before|prior\s+to|ahead\s+of)\s+(?:(?:a|an|the|your|any)\s+)?"
             r"(?:surgery|surgeries|operations?)\b|\bpre-?operative\b|\bpre-?surgical\b",
             ["preoperative", "pre-operative", "perioperative", "presurgical", "before surgery",
              "prior to surgery"]),
    _setting("after surgery", 0,
             r"\b(?:after|following|post-?)\s+(?:(?:a|an|the|your|any)\s+)?(?:surgery|surgeries|operations?)\b|"
             r"\bpost-?operative\b|\bpost-?surgical\b|\brecovering\s+from\s+surgery\b",
             ["postoperative", "post-operative", "postsurgical", "after surgery", "following surgery"]),
    _setting("heart attack", 0,
             r"\b" + _AFTER + r"(?:heart\s+attacks?|myocardial\s+infarctions?)\b|"
             r"\bheart\s+attack\s+(?:survivors|patients)\b",
             ["heart attack", "myocardial infarction", "acute coronary syndrome"]),
    _setting("stroke", 0,
             r"\b" + _AFTER + r"strokes?\b|\bstroke\s+(?:survivors|patients)\b",
             ["stroke", "strokes"]),
    _setting("bedtime", 0,
             r"\bbefore\s+(?:going\s+to\s+)?(?:bed|bedtime|sleep|sleeping)\b|\bat\s+bedtime\b|\bbedtime\b|"
             r"\bnightcap\b",
             ["bedtime", "before bed", "before sleep", "pre-sleep", "presleep", "before going to bed",
              "at night", "nighttime", "night-time", "evening", "nocturnal", "overnight", "nightcap"]),
    _setting("before meals", 0,
             r"\bbefore\s+(?:a\s+|each\s+|every\s+|your\s+)?(?:meals?|eating|dinner|lunch|breakfast)\b",
             ["before meals", "before a meal", "before eating", "preprandial", "premeal", "pre-meal",
              "preload"]),
    _setting("after meals", 0,
             r"\bafter\s+(?:a\s+|each\s+|every\s+|your\s+)?(?:meals?|eating|dinner|lunch|breakfast)\b",
             ["after meals", "after a meal", "after eating", "postprandial", "postmeal", "post-meal"]),
    _setting("before exercise", 0,
             r"\bbefore\s+(?:a\s+|your\s+)?(?:workouts?|exercise|training|a\s+run|a\s+race)\b|"
             r"\bpre-?workout\b",
             ["before exercise", "before a workout", "pre-exercise", "preexercise", "pre-workout"]),
    _setting("after exercise", 0,
             r"\bafter\s+(?:a\s+|your\s+)?(?:workouts?|exercise|training|a\s+run|a\s+race)\b|"
             r"\bpost-?workout\b|\bpost-?exercise\b",
             ["after exercise", "after a workout", "post-exercise", "postexercise", "post-workout"]),
    # --- conditions ("people with ...") --------------------------------------
    _setting("type 1 diabetes", 0,
             _WITH + r"(?:type\s+(?:1|one|i)\s+diabet\w*|T1D\w*|juvenile\s+diabetes)",
             ["type 1 diabetes", "type 1 diabetic", "T1D", "T1DM", "insulin-dependent diabetes"],
             hides=("diabetes",)),
    _setting("type 2 diabetes", 0,
             _WITH + r"(?:type\s+(?:2|two|ii)\s+diabet\w*|T2D\w*)",
             ["type 2 diabetes", "type 2 diabetic", "T2D", "T2DM", "NIDDM"],
             hides=("diabetes",)),
    _setting("diabetes", 0,
             _WITH + r"(?:pre-?)?diabet\w*|\bdiabetics\b",
             ["diabetes", "diabetic", "diabetics", "prediabetes"]),
    _setting("hypertension", 0,
             _WITH + r"(?:hypertens\w*|high\s+blood\s+pressure)|\bhypertensive\s+(?:patients|people|adults)\b",
             ["hypertension", "hypertensive", "high blood pressure", "elevated blood pressure"]),
    _setting("high cholesterol", 0,
             _WITH + r"(?:high\s+cholesterol|hypercholesterol\w*|dyslipid\w*|elevated\s+(?:LDL|cholesterol))",
             ["hypercholesterolemia", "hypercholesterolaemia", "high cholesterol", "dyslipidemia",
              "dyslipidaemia", "elevated cholesterol"]),
    _setting("obesity", 0,
             _WITH + r"(?:obes\w*|overweight)|\b(?:obese|overweight)\s+(?:people|adults|patients|"
             r"children|men|women)\b",
             ["obesity", "obese", "overweight"]),
    _setting("heart disease", 0,
             _WITH + r"(?:(?:heart|cardiovascular|coronary(?:\s+(?:artery|heart))?)\s+disease|CVD|CHD|angina)",
             ["heart disease", "cardiovascular disease", "coronary heart disease",
              "coronary artery disease"]),
    _setting("heart failure", 0,
             _WITH + r"heart\s+failure",
             ["heart failure"]),
    _setting("cancer", 0,
             _WITH + r"(?:\w+\s+){0,2}?(?:cancers?|tumou?rs?|malignan\w+)|\bcancer\s+(?:patients|survivors)\b",
             ["cancer", "neoplasm*", "tumor*", "tumour*"]),
    _setting("asthma", 0,
             _WITH + r"asthma\w*|\basthmatics?\b",
             ["asthma", "asthmatic"]),
    _setting("copd", 0,
             _WITH + r"(?:COPD|chronic\s+obstructive\s+(?:pulmonary|lung)\s+disease|emphysema)",
             ["COPD", "chronic obstructive pulmonary disease", "emphysema"]),
    _setting("depression", 0,
             _WITH + r"depress\w*",
             ["depression", "depressive", "depressed"]),
    _setting("anxiety", 0,
             _WITH + r"anxiety(?:\s+disorders?)?",
             ["anxiety", "anxious"]),
    _setting("kidney disease", 0,
             _WITH + r"(?:(?:chronic\s+)?kidney\s+(?:disease|failure|damage)|CKD|renal\s+(?:disease|failure|"
             r"impairment))",
             ["kidney disease", "chronic kidney disease", "CKD", "renal failure", "renal insufficiency"]),
    _setting("arthritis", 0,
             _WITH + r"(?:osteo|rheumatoid\s+)?arthritis",
             ["arthritis", "osteoarthritis", "rheumatoid"]),
    _setting("osteoporosis", 0,
             _WITH + r"(?:osteoporo\w*|osteopenia|low\s+bone\s+(?:density|mass))",
             ["osteoporosis", "osteoporotic", "osteopenia", "low bone density"]),
    _setting("dementia", 0,
             _WITH + r"(?:dementia|alzheimer\w*|cognitive\s+impairment)",
             ["dementia", "Alzheimer*", "cognitive impairment"]),
    _setting("hiv", 0,
             _WITH + r"(?:HIV|AIDS)",
             ["HIV", "human immunodeficiency virus", "AIDS"]),
    _setting("epilepsy", 0,
             _WITH + r"epilep\w*",
             ["epilepsy", "epileptic"]),
    _setting("migraine", 0,
             _WITH + r"migraines?",
             ["migraine", "migraines"]),
    # --- age and life stage ----------------------------------------------------
    _setting("infants", 1,
             r"\b(?:babies|infants?|newborns?|neonat\w+|infancy|nurslings?)\b|"
             r"\bbaby\b(?!\s+(?:aspirin|powder|oil|carrots?|corn|boomers?|spinach|wipes|shampoo|lotion))|"
             r"\bfirst\s+(?:year|12\s+months|six\s+months|6\s+months|months|weeks)\s+of\s+life\b",
             ["infant", "infants", "newborn*", "neonat*", "baby", "babies", "infancy",
              "first year of life"]),
    _setting("children", 1,
             r"\b(?:children|child|kids?|childhood|pediatric|paediatric|schoolchildren|school-?age\w*|"
             r"preschool\w*|toddlers?)\b",
             ["child", "children", "childhood", "pediatric*", "paediatric*", "schoolchild*"]),
    _setting("teenagers", 1,
             r"\b(?:teen|teens|teenagers?|teenage|adolescen\w+|youths?)\b",
             ["adolescent", "adolescents", "adolescence", "teenager*", "teen", "teens", "youth"]),
    _setting("older adults", 1,
             r"\bolder\s+(?:adults?|people|persons?|patients|men|women|individuals|folks)\b|"
             r"\b(?:the\s+)?elderly\b|\bseniors?\b|\bsenior\s+citizens?\b|\bgeriatric\b|\bold\s+age\b|"
             r"\bin\s+later\s+life\b|"
             r"\b(?:over|above|older\s+than|aged|age\s+of)\s+(?:[6-9]\d)\b" + _UNIT + r"|\b[6-8]\d\s*\+",
             ["older adults", "older people", "older persons", "older patients", "older individuals",
              "older men", "older women", "elderly", "geriatric", "seniors", "older age"]),
    _setting("menopause", 1,
             r"\bmenopaus\w*|\bperimenopaus\w*|\bpostmenopaus\w*",
             ["menopause", "menopausal", "perimenopause", "perimenopausal", "postmenopause",
              "postmenopausal"]),
    _setting("athletes", 1,
             r"\bathletes?\b|\bsports\s*(?:people|men|women|players)\b",
             ["athlete", "athletes", "sportspeople"]),
    _setting("smokers", 1,
             r"\bsmokers\b|\bpeople\s+who\s+smoke\b|\bex-?smokers\b|\bcurrent\s+smokers\b",
             ["smoker", "smokers", "tobacco users"]),
    # --- sex ---------------------------------------------------------------------
    _setting("women", 2,
             r"\b(?:women|woman|females?)\b",
             ["women", "woman", "female", "females"]),
    _setting("men", 2,
             r"\b(?:men|man|males?)\b",
             ["men", "man", "male", "males"]),
]

_BOTH_SEXES = re.compile(
    r"\b(?:men|man|males?|boys?)\b.{0,30}\b(?:women|woman|females?|girls?)\b|"
    r"\b(?:women|woman|females?|girls?)\b.{0,30}\b(?:men|man|males?|boys?)\b|"
    r"\bboth\s+sexes\b|\bgender\b", re.IGNORECASE)
_SEX_KEYS = ("women", "men")
# Women asked about for their pregnancy or their menopause are not asked about
# as "women" too.
_IMPLIES_WOMEN = ("pregnancy", "preconception", "postpartum", "breastfeeding", "menopause")


def _tag(term: str) -> str:
    """One term asked for in the paper's own words: pregnan*[tiab],
    "older adults"[tiab]."""
    t = term.strip()
    return f'"{t}"[tiab]' if re.search(r"[\s-]", t) else f"{t}[tiab]"


for _s in SETTINGS:
    _s["group"] = "(" + " OR ".join(_tag(t) for t in _s["terms"]) + ")"
del _s


def is_setting_group(group: str) -> bool:
    """True for a group of nothing but terms asked for in the title or
    abstract, which is how a setting's group is written (and how the model
    never writes a group)."""
    terms = [t.strip() for t in re.split(r"\s+OR\s+", (group or "").strip().strip("()"))]
    return bool(terms) and all(t.endswith("[tiab]") for t in terms)


def _is_protected(group: str) -> bool:
    """A group the broadening never drops: stopping, or the claim's setting."""
    return _is_stopping_group(group) or is_setting_group(group)


def core_groups(query: str) -> list[str]:
    """The query's groups without the setting's: the claim's subject first,
    its outcome second, then any group for stopping."""
    return [g for g in split_and(query or "") if not is_setting_group(g)]


def setting_groups(query: str) -> list[str]:
    """Only the setting's groups, in the order they sit in the query."""
    return [g for g in split_and(query or "") if is_setting_group(g)]


def claim_settings(claim: str) -> list[dict]:
    """
    The settings this claim names, at most MAX_SETTINGS, in the order they
    are ANDed on: the times and conditions first, then the age or life
    stage, then the sex, and in the claim's own order within each. Empty for
    a claim that names none. Only what the claim says: that measles vaccines
    are given to children is not a setting the claim named.
    """
    text = str(claim or "")
    hits = {}
    for s in SETTINGS:
        m = s["claim"].search(text)
        if m:
            hits[s["key"]] = (s["rank"], m.start(), s)
    for _, _, s in list(hits.values()):
        for key in s["hides"]:
            hits.pop(key, None)
    if _BOTH_SEXES.search(text):
        for key in _SEX_KEYS:
            hits.pop(key, None)
    elif any(key in hits for key in _IMPLIES_WOMEN):
        hits.pop("women", None)
    return [s for _, _, s in sorted(hits.values(), key=lambda h: h[:2])][:MAX_SETTINGS]


def add_setting(query: str, claim: str) -> str:
    """
    The query with a group ANDed on for each setting the claim names. Unchanged
    for a claim that names none, for a query with nothing to add the group to,
    and for a group already there, so it can be applied twice.
    """
    if not split_and(query or ""):
        return query
    out = query
    for setting in claim_settings(claim):
        if setting["group"] not in out:
            out = f"{out} AND {setting['group']}"
    return out


# What "saves lives" means. A claim that something saves lives, lets people
# live longer or prevents death, with no disease named, is about death from
# any cause. A review that finds fewer deaths from one disease has found a
# different outcome (screening can lower deaths from the disease it looks for
# and leave deaths overall where they were), so the outcome the search asks
# for is death from any cause. A claim that names its disease keeps its own.
ALL_CAUSE_GROUP = "(all-cause mortality OR overall mortality OR death from any cause OR overall survival)"
_LIVES = re.compile(
    r"\bsav(?:e|es|ed|ing)\s+(?:\w+\s+){0,2}?lives?\b|\blife-?saving\b|\blives?\s+(?:are\s+)?saved\b|"
    r"\b(?:live|lives|living)\s+longer\b|\blonger\s+(?:life|lives|lifespan)\b|"
    r"\b(?:extend|extends|extended|extending|prolong|prolongs|prolonged|prolonging|shorten|shortens)\s+"
    r"(?:\w+\s+){0,2}?(?:life|lives|lifespan)\b|\blife\s*span\b|\blife\s+expectancy\b|\blongevity\b|"
    r"\bdie\s+(?:younger|earlier|sooner)\b|\bpremature\s+deaths?\b", re.IGNORECASE)
_DEATHS = re.compile(
    r"\b(?:prevent\w*|avoid\w*|reduc\w*|lower\w*|cut\w*|decreas\w*|increas\w*|rais\w*|stop\w*|delay\w*|"
    r"postpone\w*|caus\w*|protect\w*\s+against|guard\w*\s+against)\s+"
    r"(?:(?:the|your|a|an|overall|total|early|premature|risk\s+of|chance\s+of|odds\s+of|rate\s+of|"
    r"number\s+of|risk\s+for)\s+)*(?:deaths?|dying|mortality|death\s+rates?)\b", re.IGNORECASE)
# A death from something: "dying from colon cancer", "mortality due to stroke".
_DEATH_FROM = re.compile(
    r"\b(?:deaths?|dying|die[sd]?|mortality|fatalit\w+|lives?)\s+(?:from|due\s+to|of|caused\s+by|related|"
    r"attributable|owing|secondary\s+to|lost\s+to)\b", re.IGNORECASE)


def wants_all_cause(claim: str) -> bool:
    """True for a claim about saving lives, living longer or preventing death
    that names no cause of death."""
    text = str(claim or "")
    return bool((_LIVES.search(text) or _DEATHS.search(text)) and not _DEATH_FROM.search(text))


def add_all_cause(query: str) -> str:
    """
    The query with its outcome group asking for death from any cause. The
    subject stays; the outcome group is replaced, not added to, even when the
    model's own group says "all-cause" somewhere: beside it sit "survival"
    and "longevity", which bring back studies of ageing and of one disease's
    deaths. A group for stopping or for the setting keeps its place.
    """
    parts = split_and(query or "")
    plain = [i for i, p in enumerate(parts) if not _is_protected(p)]
    if not plain:
        return query
    if len(plain) >= 2:
        parts[plain[1]] = ALL_CAUSE_GROUP
    else:
        parts.insert(plain[0] + 1, ALL_CAUSE_GROUP)
    return " AND ".join(parts)


def broaden_query(query: str) -> str | None:
    """
    Drop the last top-level AND-term ("a AND b AND c" -> "a AND b").
    Returns None when there's nothing left to drop. Used once, when a
    search comes back empty: the model sometimes over-specifies.

    A group for stopping or for the claim's setting is never the one dropped:
    a search that has lost it is no longer about the claim. The outcome goes
    first instead, and with nothing but the subject and those groups left
    there is nothing to drop.
    """
    parts = split_and(query)
    if len(parts) < 2:
        return None
    drop = next((i for i in range(len(parts) - 1, 0, -1) if not _is_protected(parts[i])), None)
    if drop is None:
        return None
    return " AND ".join(p for i, p in enumerate(parts) if i != drop)


# The designs that answer a general health claim: pooled evidence first,
# then trials. PubMed's [pt] filter reads its own curated tags, so this
# asks the index a question rather than guessing from a title.
BEST_EVIDENCE_PT = ("systematic review[pt]", "meta-analysis[pt]",
                    "randomized controlled trial[pt]")


def best_evidence_query(query: str) -> str:
    """The same search, restricted to the designs worth weighing most."""
    return f"({query}) AND ({' OR '.join(BEST_EVIDENCE_PT)})"


def phrase_query(query: str) -> str | None:
    """
    The claim's own subject, searched as written: the first term of the first
    group, quoted, against the whole outcome group with its multi-word terms
    quoted too. None when that would be the same search again.

    The search step lists the claim's own words first and its synonyms after.
    The synonyms widen the net, and a loose one widens it too far: "joint
    cracking" and "joint popping" match every paper on noisy knees, and push
    "Knuckle cracking and hand osteoarthritis" down to sixteenth, out of
    reach. "knuckle cracking" alone, as a phrase, puts it sixth.
    """
    groups = split_and(query or "")
    # The groups for stopping and for the setting carry field tags and stems
    # of their own, and are passed through as they are.
    if not groups or any(re.search(r"[\[\]\"]", g) for g in groups if not _is_protected(g)):
        return None
    split = []
    for g in groups:
        if _is_protected(g):
            split.append([g.strip().strip("()")])
            continue
        terms = [t.strip() for t in re.split(r"\s+OR\s+", g.strip().strip("()")) if t.strip()]
        if not terms or any("(" in t or ")" in t for t in terms):
            return None
        split.append([f'"{t}"' if " " in t else t for t in terms])
    head = split[0][:1]
    quoted = any(t.startswith('"') for g, raw in zip(split, groups) if not _is_protected(raw) for t in g)
    if len(split[0]) == 1 and not quoted:
        return None
    return " AND ".join("(" + " OR ".join(g) + ")" for g in [head] + split[1:])


def search_phrases(phrased: str, max_results: int = 8) -> list[str]:
    """
    The phrase search, without the phrases PubMed does not know. A quoted
    phrase that is in no record is not searched as a phrase: PubMed splits it
    into loose words, so slang like "ab exercises" became "ab" (read as
    abnormalities) AND exercise, and filled the list with papers on insulin
    resistance and fat in dogs. An unknown phrase among the outcome's
    synonyms is dropped and the search asked again; when the claim's own
    subject is the unknown one, there is no phrase search to make.
    """
    ids = search_pubmed(phrased, max_results=max_results)
    missing = _UNKNOWN_PHRASES.pop(phrased, [])
    if not missing:
        return ids
    groups = split_and(phrased)
    kept = []
    for g in groups:
        terms = [t.strip() for t in re.split(r"\s+OR\s+", g.strip().strip("()")) if t.strip()]
        terms = [t for t in terms if t.strip('"') not in missing]
        if not terms:
            return []
        kept.append("(" + " OR ".join(terms) + ")")
    again = " AND ".join(kept)
    if again == phrased:
        return ids
    ids = search_pubmed(again, max_results=max_results)
    return [] if _UNKNOWN_PHRASES.pop(again, []) else ids


# How many places at the front of the list the review search may take. Three:
# the main review on a topic is nearly always in PubMed's top three for its
# own words, and more would let reviews of neighbouring questions crowd out
# the trials. Which three is decided by lead_reviews, from the first
# REVIEW_POOL that search returns, because the order NCBI gives is not
# repeatable: the main review is third on one call and sixth on the next.
REVIEW_SLOTS = 3
REVIEW_POOL = 4 * REVIEW_SLOTS


def review_query(query: str) -> str | None:
    """
    The claim's own words for its subject and its outcome, and nothing else,
    asked for as a systematic review or meta-analysis: "(zinc) AND (common
    cold) AND (systematic review OR meta-analysis)". None when the query has
    no group to take a word from.

    The search step lists the claim's own words first in each group, so the
    first term of each is the claim as typed. The synonyms are left out on
    purpose: the main review on a topic uses the topic's own words in its
    title, and every synonym ORed in is another way for a review of
    something else to outrank it. The words are not quoted and carry no
    field tag, so PubMed still maps them to its own subject headings; and
    the design words are words, not the [pt] filter, because a review only
    gets its publication type once it has been indexed, which takes months,
    and the newest review on a question is often the one that settles it.
    """
    every = split_and(query or "")
    groups = [g for g in every if not _is_protected(g)][:2]
    heads = []
    for g in groups:
        terms = [t.strip() for t in re.split(r"\s+OR\s+", g.strip().strip("()")) if t.strip()]
        if terms:
            heads.append(f"({terms[0].strip('()')})")
    if not heads:
        return None
    # A claim about stopping asks for reviews of stopping, in all the words
    # for it: the review is titled "discontinuation of", or "stopping". A
    # claim about a setting asks for reviews in it, in all the words for it:
    # the review of vaccinating mothers says "pregnancy" and not "mothers".
    heads += [g for g in every if _is_protected(g)]
    return " AND ".join(heads) + " AND (systematic review OR meta-analysis)"


# A search "found" a paper when it is within this many times the slots asked
# for: 2 of 8 is 16, the whole of what the strong-design and plain searches
# are asked for, so a paper counts wherever in the window NCBI puts it. Replayed
# over recorded calls, 1.5 (12) was a little steadier on a calm day, but when
# the ranking is shaken by four or more places it lost the papers that two
# searches agreed on (present in 75% of runs against 95%). 2.5 (20) was less
# steady on both, because a bad call's stragglers then vote too. No extra
# call: the searches already return 16.
VOTE_DEPTH = 2.0


def _heads(query: str) -> list[str]:
    """The first term of the query's first two groups: the claim as typed."""
    heads = []
    for g in core_groups(query)[:2]:
        terms = [t.strip() for t in re.split(r"\s+OR\s+", g.strip().strip("()")) if t.strip()]
        if terms:
            heads.append(terms[0].strip("() "))
    return heads


def _newest_first(ids) -> list[str]:
    """PMIDs as numbers, newest first (text for an id that is not one). A
    total order, so what it returns does not depend on what it was given."""
    return sorted(ids, key=lambda p: (len(p), p), reverse=True)


def _design_tier(study: dict) -> int:
    """0 for a systematic review or meta-analysis, 1 for any other review,
    2 for the rest. The newest review on a question has no type yet, so the
    title counts."""
    types = study.get("publication_types") or []
    if ("Meta-Analysis" in types or "Systematic Review" in types
            or re.search(r"\b(?:systematic review|meta-?analys[ie]s)\b",
                         study.get("title") or "", re.IGNORECASE)):
        return 0
    return 1 if any(t.startswith("Review") for t in types) else 2


def _names(title: str, head: str) -> bool:
    """True when the title uses every word of the head term that names
    something, by its stem: "toothpastes" for "toothpaste", "caries" for
    "caries". A head of nothing but filler words names nothing."""
    stems = evidence.outcome_stems(head)
    return bool(stems) and all(evidence.names_any(title, [s]) for s in stems)


def lead_reviews(pool: list[str], query: str, votes, records: dict | None = None) -> list[str]:
    """
    The REVIEW_SLOTS reviews that lead the list, picked from the first
    REVIEW_POOL the review search returned by rules that do not read the
    order NCBI gave them:
      1. one a reader can read (its record has an abstract);
      2. one whose title names both the claim's subject and its outcome,
         as typed, by stem: "Fluoride toothpastes for preventing dental
         caries" over "Biomimetic hydroxyapatite and caries prevention";
      3. one the other searches found too (`votes`, how many of them did);
      4. a meta-analysis or systematic review over any other review;
      5. the newer record.
    Without the records (before the fetch) only 3 and 5 can apply.
    """
    heads = _heads(query)
    ranked = _newest_first(dict.fromkeys(pool))

    def key(p: str):
        if records is None:
            return (-votes(p),)
        rec = records.get(p)
        if rec is None or not (rec.get("abstract") or "").strip():
            return (1, 1, -votes(p), 2)
        named = bool(heads) and all(_names(rec.get("title") or "", h) for h in heads)
        return (0, 0 if named else 1, -votes(p), _design_tier(rec))

    ranked.sort(key=key)
    return ranked[:REVIEW_SLOTS]


def _search_all(query: str, max_results: int) -> dict:
    """
    The four searches, kept apart. `pool` is the review search's candidates;
    `ranked` is everything the other three found, in the order they earn:
    by how many of them found it within VOTE_DEPTH, then by which search
    (strong designs, then the claim's phrase, then the rest), then newest.
    """
    want = max_results * 2
    depth = int(max_results * VOTE_DEPTH)
    ask = max(want, depth)
    asked = review_query(query)
    pool = list(dict.fromkeys(search_pubmed(asked, max_results=REVIEW_POOL))) if asked else []
    best = search_pubmed(best_evidence_query(query), max_results=ask)
    # The phrase search sits between the two: it can only find papers that
    # name a term as written, so what it finds is about the claim.
    phrased = phrase_query(query)
    exact = search_phrases(phrased, max_results=depth) if phrased else []
    general = search_pubmed(query, max_results=ask)

    tiers = [best[:max_results], exact, best[max_results:], general]
    found = [set(best[:depth]), set(exact[:depth]), set(general[:depth])]
    votes = lambda p: sum(p in f for f in found)  # noqa: E731
    tier_of: dict[str, int] = {}
    for n, tier in enumerate(tiers):
        for pmid in tier:
            tier_of.setdefault(pmid, n)
    ranked = _newest_first(tier_of)
    ranked.sort(key=lambda p: (-votes(p), tier_of[p]))
    return {"want": want, "pool": pool, "ranked": ranked, "votes": votes}


def _with_lead(found: dict, lead: list[str]) -> list[str]:
    return (lead + [p for p in found["ranked"] if p not in lead])[:found["want"]]


def search_merged(query: str, max_results: int = 8) -> list[str]:
    """
    Four searches, one list. PubMed ranks by relevance alone, so a narrow
    trial in a convenient journal can crowd out the meta-analysis that
    actually settles the question. One search asks the index for the
    strong designs only, and those take the front of the list; ahead of
    them go the top few reviews found with the claim's own words alone
    (review_query), because the strong-design search ORs in every synonym
    and can still bury the one review the question is known by.

    Every call is esearch, which is free and needs no key, so this costs
    a check nothing but a few hundred milliseconds. Twice the asked-for
    number comes back, because the next step throws away the records that
    have nothing in them to read.

    The order is built so that the same query gives the same list. NCBI's
    relevance sort is not repeatable: the same search asked twice, seconds
    apart, with every call answered 200, comes back with a different top
    sixteen, three or four papers swapped, and a paper that sits near the
    cut-off of one search is in on one call and out on the next. Cutting
    each search at a fixed place, as this used to, turned that noise into
    different evidence: the two papers a high-stakes verdict rested on were
    found by two of the searches every time, ranked fifth to eleventh, and
    still missed the list on most calls. So a paper's place now
    depends on how many of the three searches (strong designs, the claim's
    own phrase, everything) found it within VOTE_DEPTH, which a swap of
    two or three places cannot change, and not on where in a list it was.
    Ties go to the search it came from, in the old order, and then to the
    newer record, which no call can reshuffle.

    The reviews that lead are chosen from the review search's first
    REVIEW_POOL by lead_reviews. Here, without the records, only the votes
    and the newer record can decide; search_and_fetch decides again with
    titles in hand.
    """
    found = _search_all(query, max_results)
    return _with_lead(found, lead_reviews(found["pool"], query, found["votes"]))


def usable(studies: list[dict]) -> list[dict]:
    """
    Drop records with no abstract. Every sentence this tool prints has to
    name a study it came from, and a paper with no abstract gives the
    verdict nothing to stand on: it cannot supply a figure, a population
    or a finding. Leaving one in spends a slot out of eight on a record
    that can only ever be counted, never read.
    """
    return [s for s in studies or [] if (s.get("abstract") or "").strip()]


def search_with_fallback(query: str, max_results: int = 8,
                         surrogate: str = "") -> tuple[list[dict], str, bool]:
    """
    search_and_fetch, retried once with a broader query if nothing matched.
    Returns (studies, query_actually_used, was_broadened).
    """
    studies = search_and_fetch(query, max_results=max_results, surrogate=surrogate)
    if studies:
        return studies, query, False
    broader = broaden_query(query)
    if not broader:
        return [], query, False
    studies = search_and_fetch(broader, max_results=max_results, surrogate=surrogate)
    return studies, broader, bool(studies)


# ---------------------------------------------------------------------------
# The deep dive (layer 3)
#
# Everything below uses elink, NCBI's free link service, so a study page can
# show what the rest of the literature did with a paper: how often it has been
# cited, whether the full text is readable for free, and what sits next to it.
# None of it needs a key. All of it is allowed to fail: a deep dive that
# cannot load must degrade to the abstract we already have, never to an error.
# ---------------------------------------------------------------------------

# How many related studies are worth showing. More than this and the sheet
# stops being a deep dive and starts being a second search.
RELATED_LIMIT = 6


def _elink(linkname: str, pmid: str, db: str = "pubmed") -> list[str]:
    """Return the linked ids for one linkname, or [] if there are none."""
    params = {"dbfrom": "pubmed", "db": db, "linkname": linkname,
              "id": str(pmid), "retmode": "json", **_tool_params()}
    linksets = _get("elink.fcgi", params).json().get("linksets") or [{}]
    for group in linksets[0].get("linksetdbs") or []:
        if group.get("linkname") == linkname:
            return [str(i) for i in group.get("links") or []]
    return []


def cited_by_count(pmid: str) -> int | None:
    """How many PubMed-indexed papers cite this one. None when unknown."""
    try:
        return len(_elink("pubmed_pubmed_citedin", pmid))
    except (requests.RequestException, ValueError, KeyError):
        return None


def free_full_text_url(pmid: str) -> str | None:
    """A PubMed Central link when the full paper is readable for free."""
    try:
        ids = _elink("pubmed_pmc", pmid, db="pmc")
    except (requests.RequestException, ValueError, KeyError):
        return None
    if not ids:
        return None
    return f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{ids[0]}/"


def related_studies(pmid: str, limit: int = RELATED_LIMIT) -> list[dict]:
    """
    The papers PubMed considers nearest to this one, minus the paper itself.
    Returns the same shape as fetch_details, so the UI renders them with the
    existing study markup rather than a second set of templates.
    """
    try:
        ids = [i for i in _elink("pubmed_pubmed", pmid) if str(i) != str(pmid)]
    except (requests.RequestException, ValueError, KeyError):
        return []
    if not ids:
        return []
    try:
        return fetch_details(ids[:limit])
    except (requests.RequestException, ET.ParseError):
        return []


def deep_dive(pmid: str) -> dict:
    """
    Everything layer 3 knows about one study beyond its abstract.

    Always returns the full shape. `partial` is True when at least one of the
    three lookups failed, so the page can say so instead of implying that a
    paper has no citations when NCBI was simply unreachable.
    """
    cited = cited_by_count(pmid)
    full_text = free_full_text_url(pmid)
    related = related_studies(pmid)
    return {
        "pmid": str(pmid),
        "cited_by": cited,
        "full_text_url": full_text,
        "related": related,
        "partial": cited is None,
    }
