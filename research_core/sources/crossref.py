"""CrossRef literature search adapter."""

from __future__ import annotations

import os
import re

import httpx  # noqa: F401
from loguru import logger

from research_core.sources import http_client as _http
from research_core.sources.models import ExternalPaper

_CROSSREF_BASE = "https://api.crossref.org/works"
_USER_AGENT = "ZoteroResearchAssistant/0.1 (mailto:dev@example.com)"
_TAG_RE = re.compile(r"<[^>]+>")


def _mailto() -> str:
    return os.getenv("CROSSREF_MAILTO", os.getenv("UNPAYWALL_EMAIL", "dev@example.com"))


def _strip_html(text: str) -> str:
    return " ".join(_TAG_RE.sub(" ", text).split())


def _parse_year(item: dict) -> int:
    for key in ("published-print", "published-online", "created", "issued"):
        parts = (item.get(key) or {}).get("date-parts") or [[]]
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return 0


def _parse_authors(item: dict) -> list[str]:
    authors: list[str] = []
    for author in item.get("author") or []:
        if author.get("name"):
            authors.append(author["name"])
            continue
        given = author.get("given", "")
        family = author.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)
    return authors


def _is_open_access(item: dict) -> bool:
    for link in item.get("link") or []:
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            return True
    for lic in item.get("license") or []:
        url = (lic.get("URL") or "").lower()
        if "creativecommons.org" in url or "publicdomain" in url:
            return True
    return False


def search_crossref(
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 20,
    doi_prefix: str = "",
    sort_by: str = "relevance",
) -> list[ExternalPaper]:
    """Search CrossRef works API.

    Args:
        doi_prefix: Optional DOI prefix filter (e.g. ``10.1016`` for Elsevier).
    """
    if not query.strip():
        return []

    filters: list[str] = []
    if year_from is not None:
        filters.append(f"from-pub-date:{year_from}-01-01")
    if year_to is not None:
        filters.append(f"until-pub-date:{year_to}-12-31")
    if doi_prefix:
        filters.append(f"prefix:{doi_prefix.strip()}")

    params: dict = {
        "query.bibliographic": query.strip(),
        "rows": min(max(limit, 10), 50),
        "mailto": _mailto(),
        "select": "DOI,title,author,published-print,published-online,created,issued,abstract,publisher,container-title,link,license,is-referenced-by-count",
    }
    if filters:
        params["filter"] = ",".join(filters)
    if sort_by == "citations":
        params["sort"] = "is-referenced-by-count"
        params["order"] = "desc"

    try:
        r = _http.get(
            _CROSSREF_BASE,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=20,
        )
        if r.status_code != 200:
            logger.debug(f"CrossRef search failed: {r.status_code}")
            return []
        items = r.json().get("message", {}).get("items") or []
    except Exception as e:
        logger.debug(f"CrossRef search error: {e}")
        return []

    papers: list[ExternalPaper] = []
    for item in items:
        title_parts = item.get("title") or []
        title = title_parts[0] if title_parts else ""
        if not title:
            continue

        doi = (item.get("DOI") or "").strip()
        container = item.get("container-title") or []
        venue = container[0] if container else ""
        abstract_raw = item.get("abstract") or ""
        abstract = _strip_html(abstract_raw) if abstract_raw else ""

        papers.append(
            ExternalPaper(
                title=title,
                authors=_parse_authors(item),
                year=_parse_year(item),
                doi=doi,
                abstract=abstract,
                venue=venue,
                publisher=item.get("publisher") or "",
                citation_count=item.get("is-referenced-by-count") or 0,
                is_open_access=_is_open_access(item),
                source="crossref",
                source_id=doi,
            )
        )
        if len(papers) >= limit:
            break
    return papers
