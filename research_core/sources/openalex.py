"""OpenAlex literature search adapter."""

from __future__ import annotations

import os

import httpx
from loguru import logger

from research_core.sources.models import ExternalPaper

_OPENALEX_BASE = "https://api.openalex.org"
_USER_AGENT = "ZoteroResearchAssistant/0.1 (mailto:dev@example.com)"


def _author_names(authorships: list) -> list[str]:
    names: list[str] = []
    for a in authorships or []:
        author = a.get("author") or {}
        name = author.get("display_name", "")
        if name:
            names.append(name)
    return names


def _extract_oa(work: dict) -> tuple[bool, str]:
    oa = work.get("open_access") or {}
    is_oa = bool(oa.get("is_oa"))
    pdf_url = ""
    primary = work.get("primary_location") or {}
    pdf_url = primary.get("pdf_url") or ""
    if not pdf_url:
        pdf_url = oa.get("oa_url") or ""
    best = work.get("best_oa_location") or {}
    if not pdf_url:
        pdf_url = best.get("pdf_url") or best.get("landing_page_url") or ""
    return is_oa, pdf_url or ""


def search_openalex(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 20,
) -> list[ExternalPaper]:
    """Search OpenAlex works API."""
    if not query.strip():
        return []

    filters: list[str] = []
    if year_from is not None:
        filters.append(f"publication_year:>={year_from}")
    if year_to is not None:
        filters.append(f"publication_year:<={year_to}")

    params: dict = {
        "search": query.strip(),
        "per-page": min(limit * 2, 50),
        "sort": "relevance_score:desc",
    }
    if filters:
        params["filter"] = ",".join(filters)

    mailto = os.getenv("OPENALEX_MAILTO", "dev@example.com")
    params["mailto"] = mailto

    try:
        r = httpx.get(
            f"{_OPENALEX_BASE}/works",
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=20,
        )
        if r.status_code != 200:
            logger.debug(f"OpenAlex search failed: {r.status_code}")
            return []
        results = r.json().get("results") or []
    except Exception as e:
        logger.debug(f"OpenAlex search error: {e}")
        return []

    papers: list[ExternalPaper] = []
    for work in results[: limit * 2]:
        doi = (work.get("doi") or "").replace("https://doi.org/", "").strip()
        venue = ""
        src = work.get("primary_location") or {}
        src_meta = src.get("source") or {}
        venue = src_meta.get("display_name") or ""

        is_oa, oa_pdf = _extract_oa(work)
        papers.append(
            ExternalPaper(
                title=work.get("title") or work.get("display_name") or "",
                authors=_author_names(work.get("authorships") or []),
                year=work.get("publication_year") or 0,
                doi=doi,
                abstract=work.get("abstract") or "",
                venue=venue,
                citation_count=work.get("cited_by_count") or 0,
                is_open_access=is_oa,
                oa_pdf_url=oa_pdf,
                source="openalex",
                source_id=work.get("id") or "",
            )
        )
    return papers
