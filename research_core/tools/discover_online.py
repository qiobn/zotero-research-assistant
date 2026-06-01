"""Search external literature databases (OpenAlex, Semantic Scholar)."""

from __future__ import annotations

from research_core.sources.models import ExternalPaper, OnlinePaperHit
from research_core.sources.openalex import search_openalex
from research_core.sources.semantic_scholar import search_semantic_scholar
from research_core.zotero.client import ZoteroClient


def _merge_papers(
    openalex: list[ExternalPaper],
    s2: list[ExternalPaper],
    limit: int,
) -> list[OnlinePaperHit]:
    """Merge results from multiple sources with RRF dedup."""
    ranks: dict[str, list[int]] = {}
    records: dict[str, ExternalPaper] = {}
    source_lists = [("openalex", openalex), ("semantic_scholar", s2)]

    for _source_name, papers in source_lists:
        for rank, paper in enumerate(papers, start=1):
            key = paper.merge_key()
            ranks.setdefault(key, []).append(rank)
            existing = records.get(key)
            if existing is None:
                records[key] = paper
                continue
            # Enrich sparse record with fields from the other source
            if not existing.abstract and paper.abstract:
                existing.abstract = paper.abstract
            if not existing.doi and paper.doi:
                existing.doi = paper.doi
            if not existing.oa_pdf_url and paper.oa_pdf_url:
                existing.oa_pdf_url = paper.oa_pdf_url
            if not existing.is_open_access and paper.is_open_access:
                existing.is_open_access = paper.is_open_access
            if existing.citation_count < paper.citation_count:
                existing.citation_count = paper.citation_count
            if paper.source and paper.source not in (existing.source or ""):
                existing.source = f"{existing.source},{paper.source}" if existing.source else paper.source

    rrf_k = 60
    scored: list[tuple[float, str]] = []
    for key, rank_list in ranks.items():
        score = sum(1.0 / (rrf_k + r) for r in rank_list)
        scored.append((score, key))
    scored.sort(reverse=True)

    hits: list[OnlinePaperHit] = []
    for score, key in scored[:limit]:
        paper = records[key]
        sources = [s.strip() for s in (paper.source or "").split(",") if s.strip()]
        if not sources:
            sources = ["unknown"]
        hits.append(
            OnlinePaperHit(
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                doi=paper.doi,
                abstract=paper.abstract[:500] + ("..." if len(paper.abstract) > 500 else ""),
                venue=paper.venue,
                citation_count=paper.citation_count,
                is_open_access=paper.is_open_access,
                oa_pdf_url=paper.oa_pdf_url,
                sources=sources,
                score=round(score, 4),
            )
        )
    return hits


def _mark_local_library(hits: list[OnlinePaperHit], zot: ZoteroClient) -> None:
    """Set in_local_library for hits whose DOI already exists in Zotero."""
    for hit in hits:
        if not hit.doi:
            continue
        try:
            items = zot.search_items(hit.doi, limit=5)
        except Exception:
            continue
        target = hit.doi.lower().strip()
        for item in items:
            if item.doi.lower().strip() == target:
                hit.in_local_library = True
                break


def search_online_literature(
    query: str,
    zot: ZoteroClient | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 15,
) -> list[OnlinePaperHit]:
    """Search OpenAlex and Semantic Scholar in parallel, merge by DOI/title."""
    if not query.strip():
        return []

    per_source = max(limit, 10)
    oa_papers = search_openalex(
        query, year_from=year_from, year_to=year_to, limit=per_source
    )
    s2_papers = search_semantic_scholar(
        query, year_from=year_from, year_to=year_to, limit=per_source
    )

    hits = _merge_papers(oa_papers, s2_papers, limit=limit)
    if zot is not None:
        _mark_local_library(hits, zot)
    return hits
