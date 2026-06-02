"""CNKI literature search via browser automation."""

from __future__ import annotations

import re
from typing import Literal

from loguru import logger

from research_core.sources.cnki.browser import cnki_page
from research_core.sources.cnki.exceptions import CnkiCaptchaError, CnkiTimeoutError
from research_core.sources.cnki.models import CnkiPaperHit
from research_core.sources.cnki.scripts import (
    ADVANCED_SEARCH_JS,
    ADVANCED_SEARCH_URL,
    BASIC_SEARCH_JS,
    BASIC_SEARCH_URL,
    SEARCH_FIELD_IDS,
    SOURCE_CATEGORY_IDS,
)

SortBy = Literal["relevance", "citations"]
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _parse_count(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else 0


def _parse_year(date_text: str) -> int:
    match = _YEAR_RE.search(date_text or "")
    return int(match.group(0)) if match else 0


def _normalize_field(search_field: str) -> str:
    key = search_field.strip()
    return SEARCH_FIELD_IDS.get(key, SEARCH_FIELD_IDS.get(key.upper(), "SU"))


def _normalize_source_categories(categories: list[str] | None) -> list[str]:
    if not categories:
        return []
    ids: list[str] = []
    for cat in categories:
        mapped = SOURCE_CATEGORY_IDS.get(cat.strip(), SOURCE_CATEGORY_IDS.get(cat.strip().upper(), ""))
        if mapped and mapped not in ids:
            ids.append(mapped)
    return ids


def _raw_to_hits(raw: dict, *, limit: int) -> tuple[list[CnkiPaperHit], str, str]:
    if raw.get("error") == "captcha":
        raise CnkiCaptchaError(
            "CNKI captcha detected. Open Chrome (CNKI_CDP_URL), solve the slider captcha, then retry."
        )

    results = raw.get("results") or []
    hits: list[CnkiPaperHit] = []
    for item in results[:limit]:
        hits.append(
            CnkiPaperHit(
                title=item.get("title") or "",
                authors=item.get("authors") or [],
                year=_parse_year(item.get("date") or ""),
                venue=item.get("journal") or "",
                date=item.get("date") or "",
                citation_count=_parse_count(item.get("citations") or ""),
                download_count=_parse_count(item.get("downloads") or ""),
                cnki_url=item.get("href") or "",
                export_id=item.get("exportId") or "",
                database_type=item.get("database") or "",
                journal_level=item.get("journalLevel") or [],
                is_online_first=bool(item.get("isOnlineFirst")),
            )
        )
    return hits, str(raw.get("total") or "0"), str(raw.get("page") or "")


def _apply_filters(
    hits: list[CnkiPaperHit],
    *,
    year_from: int | None,
    year_to: int | None,
    limit: int,
    sort_by: SortBy,
) -> list[CnkiPaperHit]:
    filtered: list[CnkiPaperHit] = []
    for hit in hits:
        if year_from is not None and hit.year and hit.year < year_from:
            continue
        if year_to is not None and hit.year and hit.year > year_to:
            continue
        filtered.append(hit)

    if sort_by == "citations":
        filtered.sort(key=lambda h: (h.citation_count, h.download_count), reverse=True)
    return filtered[:limit]


def _run_basic_search(page, query: str) -> dict:
    page.goto(BASIC_SEARCH_URL, wait_until="domcontentloaded")
    try:
        return page.evaluate(BASIC_SEARCH_JS, {"query": query})
    except Exception as exc:
        if "timeout" in str(exc).lower():
            raise CnkiTimeoutError("CNKI basic search timed out waiting for results.") from exc
        raise


def _run_advanced_search(
    page,
    *,
    query: str,
    search_field: str,
    year_from: int | None,
    year_to: int | None,
    author: str,
    journal: str,
    source_categories: list[str],
) -> dict:
    page.goto(ADVANCED_SEARCH_URL, wait_until="domcontentloaded")
    payload = {
        "query": query,
        "fieldType": _normalize_field(search_field),
        "startYear": str(year_from) if year_from is not None else "",
        "endYear": str(year_to) if year_to is not None else "",
        "author": author,
        "journal": journal,
        "sourceTypes": _normalize_source_categories(source_categories),
    }
    try:
        return page.evaluate(ADVANCED_SEARCH_JS, payload)
    except Exception as exc:
        if "timeout" in str(exc).lower():
            raise CnkiTimeoutError("CNKI advanced search timed out waiting for results.") from exc
        raise


def search_cnki(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    search_field: str = "SU",
    author: str = "",
    journal: str = "",
    source_categories: list[str] | None = None,
    limit: int = 20,
    sort_by: SortBy = "relevance",
) -> tuple[list[CnkiPaperHit], dict]:
    """Search CNKI and return structured hits plus search metadata."""
    if not query.strip():
        return [], {"total": "0", "page": "", "mode": "none"}

    use_advanced = bool(
        _normalize_source_categories(source_categories)
        or author.strip()
        or journal.strip()
        or year_from is not None
        or year_to is not None
    )

    with cnki_page() as page:
        if use_advanced:
            raw = _run_advanced_search(
                page,
                query=query.strip(),
                search_field=search_field,
                year_from=year_from,
                year_to=year_to,
                author=author.strip(),
                journal=journal.strip(),
                source_categories=source_categories or [],
            )
            mode = "advanced"
        else:
            raw = _run_basic_search(page, query.strip())
            mode = "basic"

    hits, total, page_info = _raw_to_hits(raw, limit=max(limit, 20))
    hits = _apply_filters(
        hits,
        year_from=year_from if not use_advanced else None,
        year_to=year_to if not use_advanced else None,
        limit=limit,
        sort_by=sort_by,
    )
    meta = {"total": total, "page": page_info, "mode": mode, "query": query.strip()}
    logger.debug(f"CNKI {mode} search '{query}' -> {len(hits)} hits (total={total})")
    return hits, meta
