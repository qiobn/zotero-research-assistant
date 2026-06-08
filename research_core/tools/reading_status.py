"""Reading status heuristic and personalized recommendations.

Determines read/unread/browsed status from Zotero metadata (annotations, notes,
access dates), then uses recent reading patterns to recommend related papers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from research_core.zotero.client import ZoteroClient


@dataclass
class PaperReadingStatus:
    key: str
    title: str
    status: str  # "deep_read" | "browsed" | "unread"
    annotation_count: int = 0
    note_count: int = 0
    date_added: str = ""
    date_modified: str = ""
    keywords: list[str] = field(default_factory=list)
    doi: str = ""


def _parse_date(date_str: str) -> datetime | None:
    """Parse ISO date string from Zotero."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _classify_status(annotation_count: int, note_count: int, pdf_opened_recently: bool) -> str:
    """Heuristic classification of reading status.

    - deep_read: heavily annotated or has notes (strong evidence of thorough reading)
    - browsed: some annotations, or PDF was opened recently in Zotero reader
    - unread: no engagement signals at all
    """
    if annotation_count >= 3 or note_count >= 1:
        return "deep_read"
    if annotation_count >= 1 or pdf_opened_recently:
        return "browsed"
    return "unread"


def get_reading_status(
    *,
    zot: ZoteroClient,
    item_keys: list[str] | None = None,
    scope: str = "all",
    days_recent: int = 30,
    limit: int = 50,
) -> list[dict]:
    """Analyze reading status of papers in the library.

    Args:
        zot: Zotero client instance.
        item_keys: Specific keys to check. If None, scans library.
        scope: "all" | "unread" | "deep_read" | "browsed" — filter results.
        days_recent: PDF attachments modified within this window count as
            "recently opened" (Zotero 7 updates PDF dateModified when the
            reader saves reading position).
        limit: Max items to return.

    Returns:
        List of paper status dicts with key, title, status, annotation_count, etc.
    """
    now = datetime.now(UTC)
    recent_cutoff = now - timedelta(days=days_recent)

    if item_keys:
        raw_items = []
        for key in item_keys[:limit]:
            try:
                raw = zot._zot.item(key)
                raw_items.append(raw)
            except Exception:
                continue
    else:
        raw_items = zot._zot.items(
            itemType="-attachment || note",
            sort="dateModified",
            direction="desc",
            limit=min(limit * 2, 200),
        )

    results: list[PaperReadingStatus] = []

    for raw in raw_items:
        data = raw.get("data", raw)
        key = data.get("key", "")
        title = data.get("title", "")
        if not key or not title:
            continue

        date_modified = data.get("dateModified", "")
        date_added = data.get("dateAdded", "")

        annotation_count = 0
        note_count = 0
        pdf_opened_recently = False
        try:
            children = zot._zot.children(key)
            for ch in children:
                ch_data = ch.get("data", ch)
                ch_type = ch_data.get("itemType", "")
                if ch_type == "note":
                    note_count += 1
                elif ch_data.get("contentType") == "application/pdf":
                    # Check if PDF attachment was recently modified
                    # (Zotero 7 updates this when the built-in reader opens/saves)
                    pdf_mod = _parse_date(ch_data.get("dateModified", ""))
                    if pdf_mod is not None and pdf_mod > recent_cutoff:
                        pdf_opened_recently = True
                    ch_key = ch_data.get("key", "")
                    try:
                        anns = zot._zot.children(ch_key)
                        for a in anns:
                            if a.get("data", a).get("itemType") == "annotation":
                                annotation_count += 1
                    except Exception:
                        pass
        except Exception:
            pass

        status = _classify_status(annotation_count, note_count, pdf_opened_recently)

        if scope != "all" and status != scope:
            continue

        tags = [t.get("tag", "") for t in data.get("tags", [])]

        results.append(PaperReadingStatus(
            key=key,
            title=title,
            status=status,
            annotation_count=annotation_count,
            note_count=note_count,
            date_added=date_added,
            date_modified=date_modified,
            keywords=tags,
            doi=data.get("DOI", ""),
        ))

        if len(results) >= limit:
            break

    return [
        {
            "key": r.key,
            "title": r.title,
            "status": r.status,
            "annotation_count": r.annotation_count,
            "note_count": r.note_count,
            "date_added": r.date_added,
            "date_modified": r.date_modified,
            "keywords": r.keywords,
            "doi": r.doi,
        }
        for r in results
    ]
