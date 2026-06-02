"""CNKI search results navigation tool (pagination + sorting)."""

from __future__ import annotations

import re

from research_core.sources.cnki.browser import cnki_page
from research_core.sources.cnki.exceptions import CnkiCaptchaError, CnkiTimeoutError
from research_core.sources.cnki.models import CnkiPaperHit
from research_core.sources.cnki.navigate import NAVIGATE_PAGE_JS, SORT_RESULTS_JS

_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _parse_count(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else 0


def _parse_year(date_text: str) -> int:
    match = _YEAR_RE.search(date_text or "")
    return int(match.group(0)) if match else 0


def _raw_results_to_hits(results: list[dict]) -> list[CnkiPaperHit]:
    """Convert raw JS results to typed CnkiPaperHit objects."""
    hits = []
    for item in results:
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
                is_online_first=bool(item.get("isOnlineFirst")),
            )
        )
    return hits


def cnki_navigate_pages(
    action: str = "next",
    sort_by: str = "",
) -> dict:
    """Navigate CNKI search result pages or change sort order.

    Must be called AFTER search_cnki_literature has produced initial results.
    The browser session retains state from the last search.

    Args:
        action: "next", "previous", or a page number like "3".
        sort_by: If provided, changes sort order instead of paginating.
                 Options: "relevance", "date", "citations", "downloads", "comprehensive".

    Returns:
        Dict with action/sortBy, total, page, and hits.
    """
    with cnki_page() as page:
        if sort_by:
            raw = page.evaluate(SORT_RESULTS_JS, {"sortBy": sort_by.strip().lower()})
        else:
            raw = page.evaluate(NAVIGATE_PAGE_JS, {"action": action.strip().lower()})

    if raw.get("error") == "captcha":
        raise CnkiCaptchaError("CNKI captcha detected. Solve it in Chrome, then retry.")
    if raw.get("error") == "timeout":
        raise CnkiTimeoutError("CNKI page navigation timed out.")
    if raw.get("error"):
        return {"error": raw["error"], "details": raw}

    results = raw.get("results") or []
    hits = _raw_results_to_hits(results)

    return {
        "action": raw.get("action") or raw.get("sortBy", ""),
        "total": raw.get("total", "0"),
        "page": raw.get("page", ""),
        "hits": hits,
    }
