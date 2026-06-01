"""Semantic Scholar literature search adapter."""

from __future__ import annotations

import os
import time

import httpx
from loguru import logger

from research_core.sources.models import ExternalPaper

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = (
    "paperId,title,authors,year,abstract,externalIds,"
    "isOpenAccess,openAccessPdf,citationCount,journal,publicationVenue"
)


def search_semantic_scholar(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 20,
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

    data: list = []
    for attempt in range(3):
        try:
            r = httpx.get(
                f"{_S2_BASE}/paper/search",
                params=params,
                headers=headers,
                timeout=20,
            )
            if r.status_code == 429:
                time.sleep(2**attempt)
                continue
            if r.status_code != 200:
                logger.debug(f"Semantic Scholar search failed: {r.status_code}")
                return []
            data = r.json().get("data") or []
            break
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
    return papers
