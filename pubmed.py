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
import xml.etree.ElementTree as ET

import requests

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = 15


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
    resp = requests.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
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
    resp = requests.get(f"{EUTILS_BASE}/efetch.fcgi", params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    records = []
    for article_el in root.findall("PubmedArticle"):
        try:
            records.append(_parse_article(article_el))
        except Exception:
            # One malformed record shouldn't take down the whole request.
            continue
    return records


def search_and_fetch(query: str, max_results: int = 8) -> list[dict]:
    """Convenience wrapper: search, then immediately fetch full details."""
    pmids = search_pubmed(query, max_results=max_results)
    return fetch_details(pmids)


def broaden_query(query: str) -> str | None:
    """
    Drop the last AND-term from a query ("a AND b AND c" -> "a AND b").
    Returns None when there's nothing left to drop. Used once, when a
    search comes back empty: the model sometimes over-specifies.
    """
    parts = [p.strip() for p in query.split(" AND ") if p.strip()]
    if len(parts) < 2:
        return None
    return " AND ".join(parts[:-1])


def search_with_fallback(query: str, max_results: int = 8) -> tuple[list[dict], str, bool]:
    """
    search_and_fetch, retried once with a broader query if nothing matched.
    Returns (studies, query_actually_used, was_broadened).
    """
    studies = search_and_fetch(query, max_results=max_results)
    if studies:
        return studies, query, False
    broader = broaden_query(query)
    if not broader:
        return [], query, False
    studies = search_and_fetch(broader, max_results=max_results)
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
    r = requests.get(f"{EUTILS_BASE}/elink.fcgi", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    linksets = r.json().get("linksets") or [{}]
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
