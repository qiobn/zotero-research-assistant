"""Search external literature databases (OpenAlex, Semantic Scholar, CrossRef)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from loguru import logger

from research_core.sources.crossref import search_crossref
from research_core.sources.models import ExternalPaper, OnlinePaperHit
from research_core.sources.openalex import search_openalex
from research_core.sources.semantic_scholar import search_semantic_scholar
from research_core.zotero.client import ZoteroClient

SortBy = Literal["relevance", "citations"]

# Elsevier DOI prefix — used for a targeted CrossRef pass to improve publisher coverage.
_ELSEVIER_DOI_PREFIX = "10.1016"
_FETCH_DEPTH_CAP = 50


def _fetch_depth(limit: int) -> int:
    """How many hits to pull from each source before merge."""
    return min(max(limit * 3, 30), _FETCH_DEPTH_CAP)


def _is_elsevier(paper: ExternalPaper) -> bool:
    doi = paper.doi.lower().strip()
    if doi.startswith(f"{_ELSEVIER_DOI_PREFIX}/"):
        return True
    return "elsevier" in paper.publisher.lower()


def _merge_papers(
    source_lists: list[tuple[str, list[ExternalPaper]]],
    limit: int,
    *,
    sort_by: SortBy = "relevance",
) -> list[OnlinePaperHit]:
    """Merge results from multiple sources with RRF dedup."""
    ranks: dict[str, list[int]] = {}
    records: dict[str, ExternalPaper] = {}

    for _source_name, papers in source_lists:
        for rank, paper in enumerate(papers, start=1):
            key = paper.merge_key()
            ranks.setdefault(key, []).append(rank)
            existing = records.get(key)
            if existing is None:
                records[key] = paper
                continue
            if not existing.abstract and paper.abstract:
                existing.abstract = paper.abstract
            if not existing.doi and paper.doi:
                existing.doi = paper.doi
            if not existing.publisher and paper.publisher:
                existing.publisher = paper.publisher
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
    score_by_key = dict(scored)

    if sort_by == "citations":
        selected_keys = _select_by_citations(records, score_by_key, limit)
    else:
        selected_keys = _select_with_publisher_diversity(scored, records, limit)

    hits: list[OnlinePaperHit] = []
    for key in selected_keys:
        paper = records[key]
        sources = [s.strip() for s in (paper.source or "").split(",") if s.strip()]
        if not sources:
            sources = ["unknown"]
        source_url = ""
        if paper.doi:
            source_url = f"https://doi.org/{paper.doi}"
        elif paper.source_id and paper.source == "openalex":
            source_url = paper.source_id
        elif paper.source_id and paper.source == "semantic_scholar":
            source_url = f"https://www.semanticscholar.org/paper/{paper.source_id}"
        hits.append(
            OnlinePaperHit(
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                doi=paper.doi,
                abstract=paper.abstract[:500] + ("..." if len(paper.abstract) > 500 else ""),
                venue=paper.venue,
                publisher=paper.publisher,
                citation_count=paper.citation_count,
                is_open_access=paper.is_open_access,
                oa_pdf_url=paper.oa_pdf_url,
                sources=sources,
                source_url=source_url,
                score=round(score_by_key.get(key, 0.0), 4),
            )
        )

    if sort_by == "citations":
        hits.sort(key=lambda h: (h.citation_count, h.score), reverse=True)
    return hits


def _select_by_citations(
    records: dict[str, ExternalPaper],
    score_by_key: dict[str, float],
    limit: int,
) -> list[str]:
    """Pick top hits by citation count (best for high-impact literature surveys)."""
    ranked = sorted(
        records.keys(),
        key=lambda key: (records[key].citation_count, score_by_key.get(key, 0.0)),
        reverse=True,
    )
    return ranked[:limit]


def _select_with_publisher_diversity(
    scored: list[tuple[float, str]],
    records: dict[str, ExternalPaper],
    limit: int,
) -> list[str]:
    """Pick top hits while ensuring Elsevier papers are not dropped entirely."""
    selected: list[str] = []
    selected_set: set[str] = set()

    for _score, key in scored:
        if len(selected) >= limit:
            break
        selected.append(key)
        selected_set.add(key)

    elsevier_selected = sum(1 for key in selected if _is_elsevier(records[key]))
    min_elsevier = min(2, max(1, limit // 5))
    if elsevier_selected >= min_elsevier:
        return selected

    elsevier_candidates = [
        (score, key)
        for score, key in scored
        if key not in selected_set and _is_elsevier(records[key])
    ]
    if not elsevier_candidates:
        return selected

    score_by_key = dict(scored)
    for score, key in elsevier_candidates:
        if elsevier_selected >= min_elsevier:
            break
        if len(selected) < limit:
            selected.append(key)
            selected_set.add(key)
            elsevier_selected += 1
            continue
        for idx in range(len(selected) - 1, -1, -1):
            existing_key = selected[idx]
            if _is_elsevier(records[existing_key]):
                continue
            if score <= score_by_key.get(existing_key, 0.0):
                continue
            selected[idx] = key
            selected_set.add(key)
            elsevier_selected += 1
            break

    return selected


def _fetch_all_sources(
    query: str,
    *,
    year_from: int | None,
    year_to: int | None,
    fetch_depth: int,
    sort_by: SortBy,
    fields_of_study: list[str] | None = None,
) -> list[tuple[str, list[ExternalPaper]]]:
    """Query all bibliographic sources in parallel."""
    common = {
        "year_from": year_from,
        "year_to": year_to,
        "limit": fetch_depth,
        "sort_by": sort_by,
    }
    tasks = {
        "openalex": lambda: search_openalex(query, **common, fields_of_study=fields_of_study),
        "semantic_scholar": lambda: search_semantic_scholar(
            query, **common, fields_of_study=fields_of_study
        ),
        "crossref": lambda: search_crossref(query, **common),
        "crossref_elsevier": lambda: search_crossref(
            query,
            **common,
            doi_prefix=_ELSEVIER_DOI_PREFIX,
        ),
    }

    source_lists: list[tuple[str, list[ExternalPaper]]] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                papers = future.result()
            except Exception as exc:
                logger.debug(f"Online search source {name} failed: {exc}")
                continue
            if papers:
                source_lists.append((name, papers))
    return source_lists


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
    sort_by: SortBy = "relevance",
    fields_of_study: list[str] | None = None,
) -> list[OnlinePaperHit]:
    """Search OpenAlex, Semantic Scholar, and CrossRef; merge by DOI/title."""
    if not query.strip():
        return []

    fetch_depth = _fetch_depth(limit)
    source_lists = _fetch_all_sources(
        query,
        year_from=year_from,
        year_to=year_to,
        fetch_depth=fetch_depth,
        sort_by=sort_by,
        fields_of_study=fields_of_study,
    )
    if not source_lists:
        return []

    hits = _merge_papers(source_lists, limit=limit, sort_by=sort_by)
    if zot is not None:
        _mark_local_library(hits, zot)
    return hits
