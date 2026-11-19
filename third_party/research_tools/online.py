"""Search published literature online: OpenAlex, arXiv and Europe PMC.

All three work without a key. A free OpenAlex key is still worth having: without one,
everybody on the same network shares a small daily OpenAlex budget, so a full room on
one Wi-Fi network can use it up quickly. If a source fails, the others still answer and
the result says what happened.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
TIMEOUT = 20.0
HEADERS = {"User-Agent": "research-mcp-workshop/1.0"}


@dataclass
class Paper:
    title: str
    authors: list[str]
    year: int | None
    venue: str
    doi: str
    url: str
    abstract: str
    citations: int | None
    source: str

    def authors_short(self) -> str:
        if not self.authors:
            return "Unknown authors"
        return self.authors[0] + (" et al." if len(self.authors) > 1 else "")

    def reference(self) -> str:
        """A citation built from the database record, never written by an AI model."""
        parts = [self.authors_short(), f"({self.year or 'n.d.'})", self.title.rstrip(".") + "."]
        if self.venue:
            parts.append(self.venue + ".")
        parts.append(self.url)
        return " ".join(parts)


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _describe_failure(err: Exception) -> str:
    """Short reason for a failed source. Never include the URL: it can hold an API key."""
    if isinstance(err, httpx.HTTPStatusError):
        code = err.response.status_code
        return "daily or rate limit reached (HTTP 429)" if code == 429 else f"HTTP {code}"
    if isinstance(err, httpx.TimeoutException):
        return "timed out"
    if isinstance(err, httpx.TransportError):
        return "could not connect"
    return type(err).__name__


# ---- OpenAlex --------------------------------------------------------------
def _rebuild_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Put the words back in order."""
    if not inverted:
        return ""
    positions = [(pos, word) for word, places in inverted.items() for pos in places]
    return " ".join(word for _, word in sorted(positions))


def search_openalex(query: str, limit: int, year_from: int | None, api_key: str = "") -> list[Paper]:
    filters = ["has_abstract:true"]
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    params = {
        "search": query, "per-page": limit, "filter": ",".join(filters),
        "select": "title,publication_year,doi,cited_by_count,abstract_inverted_index,"
                  "authorships,primary_location,id",
    }
    if api_key:
        params["api_key"] = api_key
    response = httpx.get(OPENALEX_URL, params=params, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()

    papers = []
    for work in response.json().get("results", []):
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        venue = (((work.get("primary_location") or {}).get("source")) or {}).get("display_name") or ""
        authors = [a["author"]["display_name"] for a in work.get("authorships", [])
                   if (a.get("author") or {}).get("display_name")]
        papers.append(Paper(
            title=_clean(work.get("title")), authors=authors, year=work.get("publication_year"),
            venue=venue, doi=doi, url=f"https://doi.org/{doi}" if doi else work.get("id", ""),
            abstract=_clean(_rebuild_abstract(work.get("abstract_inverted_index"))),
            citations=work.get("cited_by_count"), source="OpenAlex",
        ))
    return papers


# ---- arXiv -----------------------------------------------------------------
def search_arxiv(query: str, limit: int, year_from: int | None, api_key: str = "") -> list[Paper]:
    words = re.findall(r"[\w-]+", query)
    params = {"search_query": " AND ".join(f"all:{w}" for w in words), "max_results": limit,
              "sortBy": "relevance"}
    response = httpx.get(ARXIV_URL, params=params, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()

    atom = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    papers = []
    for entry in ET.fromstring(response.text).findall("a:entry", atom):
        year = int(entry.findtext("a:published", "0000", atom)[:4]) or None
        if year_from and year and year < year_from:
            continue
        papers.append(Paper(
            title=_clean(entry.findtext("a:title", "", atom)),
            authors=[_clean(a.findtext("a:name", "", atom)) for a in entry.findall("a:author", atom)],
            year=year, venue="arXiv preprint", doi=entry.findtext("arxiv:doi", "", atom),
            url=entry.findtext("a:id", "", atom),
            abstract=_clean(entry.findtext("a:summary", "", atom)), citations=None, source="arXiv",
        ))
    return papers


# ---- Europe PMC ------------------------------------------------------------
def search_europepmc(query: str, limit: int, year_from: int | None, api_key: str = "") -> list[Paper]:
    text = f"({query}) AND FIRST_PDATE:[{year_from}-01-01 TO 3000-01-01]" if year_from else query
    params = {"query": text, "format": "json", "pageSize": limit, "resultType": "core"}
    response = httpx.get(EUROPEPMC_URL, params=params, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()

    papers = []
    for item in response.json().get("resultList", {}).get("result", []):
        doi = item.get("doi") or ""
        authors = [a.strip() for a in (item.get("authorString") or "").rstrip(".").split(",") if a.strip()]
        venue = ((item.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
        url = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}"
        year = item.get("pubYear")
        papers.append(Paper(
            title=_clean(item.get("title")), authors=authors, year=int(year) if year else None,
            venue=venue, doi=doi, url=url, abstract=_clean(item.get("abstractText")),
            citations=item.get("citedByCount"), source="Europe PMC",
        ))
    return papers


# ---- all sources together --------------------------------------------------
SOURCES = {"OpenAlex": search_openalex, "arXiv": search_arxiv, "Europe PMC": search_europepmc}


def _identity(paper: Paper) -> str:
    return paper.doi.lower() or re.sub(r"[^a-z0-9]", "", paper.title.lower())


def merge_papers(results: list[list[Paper]], limit: int) -> list[Paper]:
    """Take papers from each source in turn, skipping duplicates. Papers without an
    abstract go last, because an abstract is what the review is based on."""
    seen, merged = set(), []
    for rank in range(max((len(r) for r in results), default=0)):
        for result in results:
            if rank < len(result):
                paper = result[rank]
                key = _identity(paper)
                if key and key not in seen:
                    seen.add(key)
                    merged.append(paper)
    merged.sort(key=lambda p: not p.abstract)     # stable: keeps the order otherwise
    return merged[:limit]


def search_all(query: str, limit: int = 8, year_from: int | None = None,
               openalex_key: str = "") -> tuple[list[Paper], list[str]]:
    """Search every source at once. Returns (papers, notes about sources that failed)."""
    def run(name: str):
        key = openalex_key if name == "OpenAlex" else ""
        try:
            return SOURCES[name](query, limit, year_from, key), None
        except Exception as err:       # one source being down must not stop the others
            return [], f"{name}: {_describe_failure(err)}"

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        outcomes = list(pool.map(run, SOURCES))
    notes = [note for _, note in outcomes if note]
    return merge_papers([papers for papers, _ in outcomes], limit), notes
