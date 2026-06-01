"""Shared models for external literature sources."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExternalPaper:
    """Normalized paper record from an external bibliographic API."""

    title: str
    authors: list[str]
    year: int
    doi: str
    abstract: str = ""
    venue: str = ""
    publisher: str = ""
    citation_count: int = 0
    is_open_access: bool = False
    oa_pdf_url: str = ""
    source: str = ""
    source_id: str = ""

    def merge_key(self) -> str:
        """Dedup key: DOI if present, else source-specific id."""
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        if self.source_id:
            return f"{self.source}:{self.source_id}"
        return f"title:{self.title.lower().strip()[:120]}"


@dataclass
class OnlinePaperHit:
    """Merged search hit returned to MCP clients."""

    title: str
    authors: list[str]
    year: int
    doi: str
    abstract: str
    venue: str
    publisher: str
    citation_count: int
    is_open_access: bool
    oa_pdf_url: str
    sources: list[str] = field(default_factory=list)
    score: float = 0.0
    in_local_library: bool = False
