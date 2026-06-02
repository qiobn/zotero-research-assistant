"""CNKI (中国知网) browser-based literature search."""

from research_core.sources.cnki.detail import CnkiPaperDetail, extract_paper_detail
from research_core.sources.cnki.exceptions import (
    CnkiCaptchaError,
    CnkiConfigError,
    CnkiSearchError,
    CnkiTimeoutError,
)
from research_core.sources.cnki.models import CnkiPaperHit
from research_core.sources.cnki.search import search_cnki
from research_core.sources.cnki.zotero_export import CnkiExportResult, export_cnki_to_zotero

__all__ = [
    "CnkiCaptchaError",
    "CnkiConfigError",
    "CnkiExportResult",
    "CnkiPaperDetail",
    "CnkiPaperHit",
    "CnkiSearchError",
    "CnkiTimeoutError",
    "export_cnki_to_zotero",
    "extract_paper_detail",
    "search_cnki",
]
