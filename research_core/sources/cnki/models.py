"""CNKI-specific result models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CnkiPaperHit:
    """Structured paper hit from CNKI search results."""

    title: str
    authors: list[str]
    year: int
    venue: str
    date: str = ""
    doi: str = ""
    abstract: str = ""
    citation_count: int = 0
    download_count: int = 0
    cnki_url: str = ""
    export_id: str = ""
    database_type: str = ""
    journal_level: list[str] = field(default_factory=list)
    is_online_first: bool = False
    in_local_library: bool = False
    sources: list[str] = field(default_factory=lambda: ["cnki"])
