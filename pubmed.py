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
# One check is now three calls in a row (two searches and a fetch), which
# sits exactly on the limit, so they are spaced instead of raced. A lock,
# because Flask serves checks on threads and the limit is per client.
MIN_INTERVAL = 0.35
_last_call = 0.0
_pace = threading.Lock()


def _get(endpoint: str, params: dict):
    """One E-utilities call, no faster than NCBI's published rate."""
    global _last_call
    with _pace:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    resp = requests.get(f"{EUTILS_BASE}/{endpoint}", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


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
    return data.get("esearchresult", {}).get("idlist", [])


def _text_or_none(el):
    return el.text if el is not None else None


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

    # Journal + year
    journal_el = article.find("Journal")
    journal_title = None
    year = None
    if journal_el is not None:
        journal_title = _text_or_none(journal_el.find("Title"))
        pub_date = journal_el.find("JournalIssue/PubDate")
        if pub_date is not None:
            year = _text_or_none(pub_date.find("Year")) or _text_or_none(
                pub_date.find("MedlineDate")
            )

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
    pmids = search_merged(query, max_results=max_results)[:max_results * 2]
    if surrogate:
        for pmid in search_surrogate(query, surrogate, max_results=keep + 1):
            if pmid not in pmids:
                pmids.append(pmid)
    return prioritise(usable(fetch_details(pmids)), query, surrogate)[:max_results]


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
    groups = split_and(query or "")
    if not groups:
        return list(studies or [])
    subject, outcome = groups[0], (groups[1] if len(groups) > 1 else "")

    def band(study) -> int:
        if not evidence.measures(study, subject):
            return 0
        if not outcome or evidence.measures(study, outcome):
            return 2
        return 2 if evidence.indirect(study, outcome, surrogate) else 1

    bands = {2: [], 1: [], 0: []}
    for study in studies or []:
        bands[band(study)].append(study)
    on_topic = bands[2] + bands[1]
    return on_topic if len(on_topic) >= MIN_ON_TOPIC else on_topic + bands[0]


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
    DHT in rugby players is reached at all: it never says "hair loss".
    """
    groups = split_and(query)
    if not groups or not surrogate.strip():
        return []
    group = surrogate.strip()
    if not group.startswith("("):
        group = f"({group})"
    return search_pubmed(f"{groups[0]} AND {group}", max_results=max_results)


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


def broaden_query(query: str) -> str | None:
    """
    Drop the last top-level AND-term ("a AND b AND c" -> "a AND b").
    Returns None when there's nothing left to drop. Used once, when a
    search comes back empty: the model sometimes over-specifies.
    """
    parts = split_and(query)
    if len(parts) < 2:
        return None
    return " AND ".join(parts[:-1])


# The designs that answer a general health claim: pooled evidence first,
# then trials. PubMed's [pt] filter reads its own curated tags, so this
# asks the index a question rather than guessing from a title.
BEST_EVIDENCE_PT = ("systematic review[pt]", "meta-analysis[pt]",
                    "randomized controlled trial[pt]")


def best_evidence_query(query: str) -> str:
    """The same search, restricted to the designs worth weighing most."""
    return f"({query}) AND ({' OR '.join(BEST_EVIDENCE_PT)})"


def search_merged(query: str, max_results: int = 8) -> list[str]:
    """
    Two searches, one list. PubMed ranks by relevance alone, so a narrow
    trial in a convenient journal can crowd out the meta-analysis that
    actually settles the question. The second search asks the index for
    the strong designs only, and those take the front of the list.

    Both calls are esearch, which is free and needs no key, so this costs
    a check nothing but a few hundred milliseconds. Twice the asked-for
    number comes back, because the next step throws away the records that
    have nothing in them to read.
    """
    want = max_results * 2
    best = search_pubmed(best_evidence_query(query), max_results=want)
    general = search_pubmed(query, max_results=want)
    return list(dict.fromkeys(best + general))[:want]


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
