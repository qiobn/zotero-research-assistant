"""Semantic Scholar literature search adapter."""

from __future__ import annotations

import os

from loguru import logger

from research_core.sources import http_client as _http
from research_core.sources.models import ExternalPaper

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = (
    "paperId,title,authors,year,abstract,externalIds,"
    "isOpenAccess,openAccessPdf,citationCount,journal,publicationVenue"
)


_S2_VALID_FIELDS = frozenset({
    "Computer Science", "Medicine", "Biology", "Chemistry", "Physics",
    "Mathematics", "Materials Science", "Engineering", "Environmental Science",
    "Business", "Economics", "Sociology", "Psychology", "Political Science",
    "Geography", "History", "Art", "Philosophy", "Linguistics", "Education",
    "Agricultural and Food Sciences", "Law",
})


def search_semantic_scholar(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 20,
    sort_by: str = "relevance",
    fields_of_study: list[str] | None = None,
) -> list[ExternalPaper]:
    """Search Semantic Scholar paper search API."""
    if not query.strip():
        return []

    headers: dict[str, str] = {}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    fetch_count = min(max(limit, 10), 50)
    params: dict = {
        "query": query.strip(),
        "limit": fetch_count,
        "fields": _FIELDS,
    }
    if year_from is not None:
        params["year"] = f"{year_from}-"
    if year_to is not None:
        params["year"] = f"-{year_to}" if year_from is None else f"{year_from}-{year_to}"
    if fields_of_study:
        valid = [f for f in fields_of_study if f in _S2_VALID_FIELDS]
        if valid:
            params["fieldsOfStudy"] = ",".join(valid)

    data: list = []
    try:
        r = _http.get(
            f"{_S2_BASE}/paper/search",
            params=params,
            headers=headers,
            timeout=20,
        )
        if r.status_code != 200:
            logger.debug(f"Semantic Scholar search failed: {r.status_code}")
            return []
        data = r.json().get("data") or []
    except Exception as e:
        logger.debug(f"Semantic Scholar search error: {e}")
        return []

    papers: list[ExternalPaper] = []
    for item in data:
        year = item.get("year") or 0
        if year_from is not None and year and year < year_from:
            continue
        if year_to is not None and year and year > year_to:
            continue

        ext = item.get("externalIds") or {}
        doi = ext.get("DOI") or ""
        oa = item.get("openAccessPdf") or {}
        oa_url = oa.get("url") or ""
        journal = item.get("journal") or {}
        venue_obj = item.get("publicationVenue") or {}
        venue = journal.get("name") or venue_obj.get("name") or ""
        publisher = venue_obj.get("name") or ""

        authors = [
            a.get("name", "")
            for a in item.get("authors") or []
            if a.get("name")
        ]

        papers.append(
            ExternalPaper(
                title=item.get("title") or "",
                authors=authors,
                year=year,
                doi=doi,
                abstract=item.get("abstract") or "",
                venue=venue,
                publisher=publisher,
                citation_count=item.get("citationCount") or 0,
                is_open_access=bool(item.get("isOpenAccess")),
                oa_pdf_url=oa_url,
                source="semantic_scholar",
                source_id=item.get("paperId") or "",
            )
        )
        if len(papers) >= fetch_count:
            break

    if sort_by == "citations":
        papers.sort(key=lambda p: p.citation_count, reverse=True)
    return papers


# ── Recommendations API ──

_S2_REC_BASE = "https://api.semanticscholar.org/recommendations/v1"
_REC_FIELDS = (
    "paperId,title,authors,year,abstract,externalIds,"
    "isOpenAccess,openAccessPdf,citationCount,journal,publicationVenue"
)


def get_s2_recommendations(
    paper_ids: list[str],
    *,
    limit: int = 30,
) -> list[ExternalPaper]:
    """Get recommended papers from Semantic Scholar based on seed paper(s).

    Uses S2's recommendation engine which considers citation patterns,
    co-citation, and content similarity. Much more effective than keyword
    search for finding related papers in a specific intellectual lineage.

    Args:
        paper_ids: List of S2 paper IDs, DOIs (prefixed with "DOI:"), or
            other supported identifiers.
        limit: Max recommendations to return (max 500).
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    fetch_count = min(limit, 100)

    try:
        if len(paper_ids) == 1:
            r = _http.get(
                f"{_S2_REC_BASE}/papers/forpaper/{paper_ids[0]}",
                params={"fields": _REC_FIELDS, "limit": fetch_count, "from": "recent"},
                headers=headers,
                timeout=20,
            )
        else:
            r = _http.post(
                f"{_S2_REC_BASE}/papers",
                params={"fields": _REC_FIELDS, "limit": fetch_count},
                json={"positivePaperIds": paper_ids, "negativePaperIds": []},
                headers=headers,
                timeout=20,
            )

        if r.status_code != 200:
            logger.debug(f"S2 recommendations failed: {r.status_code} - {r.text[:200]}")
            return []

        data = r.json().get("recommendedPapers") or []
    except Exception as e:
        logger.debug(f"S2 recommendations error: {e}")
        return []

    papers: list[ExternalPaper] = []
    for item in data:
        if not item:
            continue
        ext = item.get("externalIds") or {}
        doi = ext.get("DOI") or ""
        oa = item.get("openAccessPdf") or {}
        oa_url = oa.get("url") or ""
        journal = item.get("journal") or {}
        venue_obj = item.get("publicationVenue") or {}
        venue = journal.get("name") or venue_obj.get("name") or ""
        publisher = venue_obj.get("name") or ""

        authors = [
            a.get("name", "")
            for a in item.get("authors") or []
            if a.get("name")
        ]

        papers.append(
            ExternalPaper(
                title=item.get("title") or "",
                authors=authors,
                year=item.get("year") or 0,
                doi=doi,
                abstract=item.get("abstract") or "",
                venue=venue,
                publisher=publisher,
                citation_count=item.get("citationCount") or 0,
                is_open_access=bool(item.get("isOpenAccess")),
                oa_pdf_url=oa_url,
                source="semantic_scholar",
                source_id=item.get("paperId") or "",
            )
        )
        if len(papers) >= fetch_count:
            break

    papers.sort(key=lambda p: p.citation_count, reverse=True)
    return papers
