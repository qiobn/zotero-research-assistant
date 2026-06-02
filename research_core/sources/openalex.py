"""OpenAlex literature search adapter."""

from __future__ import annotations

import os

import httpx
from loguru import logger

from research_core.sources.models import ExternalPaper

_OPENALEX_BASE = "https://api.openalex.org"
_USER_AGENT = "ZoteroResearchAssistant/0.1 (mailto:dev@example.com)"


def _mailto() -> str:
    return os.getenv("OPENALEX_MAILTO", os.getenv("UNPAYWALL_EMAIL", "dev@example.com"))


def _author_names(authorships: list) -> list[str]:
    names: list[str] = []
    for a in authorships or []:
        author = a.get("author") or {}
        name = author.get("display_name", "")
        if name:
            names.append(name)
    return names


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _extract_publisher(work: dict) -> str:
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    return source.get("host_organization_name") or source.get("display_name") or ""


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


_OPENALEX_FIELD_IDS: dict[str, int] = {
    "business": 14,
    "management": 14,
    "accounting": 14,
    "economics": 20,
    "finance": 20,
    "sociology": 33,
    "social sciences": 33,
    "psychology": 32,
    "computer science": 17,
    "environmental science": 23,
    "geography": 19,
    "earth science": 19,
    "political science": 33,
    "education": 33,
    "medicine": 27,
    "engineering": 22,
    "tourism": 14,
    "marketing": 14,
    "law": 33,
    "arts and humanities": 12,
    "decision sciences": 18,
}


def search_openalex(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 20,
    sort_by: str = "relevance",
    fields_of_study: list[str] | None = None,
) -> list[ExternalPaper]:
    """Search OpenAlex works API."""
    if not query.strip():
        return []

    filters: list[str] = []
    if year_from is not None:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to is not None:
        filters.append(f"until_publication_date:{year_to}-12-31")
    if fields_of_study:
        field_ids = list({
            _OPENALEX_FIELD_IDS[f.lower()]
            for f in fields_of_study
            if f.lower() in _OPENALEX_FIELD_IDS
        })
        if field_ids:
            filters.append(f"topics.field.id:{'|'.join(str(fid) for fid in field_ids)}")

    fetch_count = min(max(limit, 10), 50)
    oa_sort = "cited_by_count:desc" if sort_by == "citations" else "relevance_score:desc"
    params: dict = {
        "search": query.strip(),
        "per-page": fetch_count,
        "sort": oa_sort,
        "mailto": _mailto(),
    }
    if filters:
        params["filter"] = ",".join(filters)

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
    for work in results[:fetch_count]:
        doi = (work.get("doi") or "").replace("https://doi.org/", "").strip()
        primary = work.get("primary_location") or {}
        src_meta = (primary.get("source") or {})
        venue = src_meta.get("display_name") or ""

        is_oa, oa_pdf = _extract_oa(work)
        abstract = work.get("abstract") or _reconstruct_abstract(work.get("abstract_inverted_index"))
        papers.append(
            ExternalPaper(
                title=work.get("title") or work.get("display_name") or "",
                authors=_author_names(work.get("authorships") or []),
                year=work.get("publication_year") or 0,
                doi=doi,
                abstract=abstract,
                venue=venue,
                publisher=_extract_publisher(work),
                citation_count=work.get("cited_by_count") or 0,
                is_open_access=is_oa,
                oa_pdf_url=oa_pdf,
                source="openalex",
                source_id=work.get("id") or "",
            )
        )
    return papers
