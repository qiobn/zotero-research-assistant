"""CNKI literature discovery tool."""

from __future__ import annotations

from typing import Literal

from research_core.sources.cnki import search_cnki as _search_cnki
from research_core.sources.cnki.models import CnkiPaperHit
from research_core.zotero.client import ZoteroClient

SortBy = Literal["relevance", "citations"]


def _mark_local_library(hits: list[CnkiPaperHit], zot: ZoteroClient) -> None:
    for hit in hits:
        if hit.doi:
            try:
                items = zot.search_items(hit.doi, limit=5)
            except Exception:
                items = []
            target = hit.doi.lower().strip()
            for item in items:
                if item.doi.lower().strip() == target:
                    hit.in_local_library = True
                    break
        if hit.in_local_library:
            continue
        try:
            items = zot.search_items(hit.title[:80], limit=5)
        except Exception:
            continue
        title_key = hit.title.lower().strip()[:80]
        for item in items:
            if item.title.lower().strip()[:80] == title_key:
                hit.in_local_library = True
                break


def search_cnki_literature(
    query: str,
    zot: ZoteroClient | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    search_field: str = "SU",
    author: str = "",
    journal: str = "",
    source_categories: list[str] | None = None,
    limit: int = 20,
    sort_by: SortBy = "relevance",
) -> dict:
    """Search CNKI and return hits with search metadata."""
    hits, meta = _search_cnki(
        query,
        year_from=year_from,
        year_to=year_to,
        search_field=search_field,
        author=author,
        journal=journal,
        source_categories=source_categories,
        limit=limit,
        sort_by=sort_by,
    )
    if zot is not None:
        _mark_local_library(hits, zot)
    return {
        "query": meta.get("query", query),
        "total": meta.get("total", "0"),
        "page": meta.get("page", ""),
        "mode": meta.get("mode", ""),
        "hits": hits,
    }
