"""Pure-function tool collection for MCP and agent use.

Each tool maps to a single user intent (not to a backend mechanism), and tools are
designed to compose via `item_key`.
"""

from research_core.sources.cnki.models import CnkiPaperHit
from research_core.sources.models import OnlinePaperHit
from research_core.tools.admin import SyncReport, sync_index
from research_core.tools.cite import (
    BibliographyExport,
    CitationSuggestion,
    export_bibliography,
    suggest_citations,
)
from research_core.tools.cnki_detail import cnki_paper_detail
from research_core.tools.cnki_navigate import cnki_navigate_pages
from research_core.tools.cnki_zotero import CnkiZoteroResult, cnki_add_to_zotero
from research_core.tools.discover_cnki import search_cnki_literature
from research_core.tools.discover_online import search_online_literature
from research_core.tools.find_related import find_related_literature
from research_core.tools.manage import (
    AddPaperResult,
    WriteResult,
    add_note,
    add_paper,
    edit_tags,
    manage_collections,
)
from research_core.tools.read import (
    AnnotationResult,
    PaperContent,
    create_annotation,
    get_paper,
    get_paper_content,
    search_annotations,
)
from research_core.tools.arguments import find_arguments
from research_core.tools.reading_status import get_reading_status
from research_core.tools.recommend import recommend_papers
from research_core.tools.review import generate_review_note
from research_core.tools.suggest_tags import suggest_tags
from research_core.tools.search import (
    BrowseResult,
    DuplicateGroup,
    MergeResult,
    PaperHit,
    browse_library,
    find_duplicates,
    find_similar_papers,
    merge_duplicates,
    search_papers,
)

__all__ = [
    "AddPaperResult",
    "AnnotationResult",
    "BibliographyExport",
    "BrowseResult",
    "CitationSuggestion",
    "CnkiPaperHit",
    "CnkiZoteroResult",
    "DuplicateGroup",
    "MergeResult",
    "OnlinePaperHit",
    "PaperContent",
    "PaperHit",
    "SyncReport",
    "WriteResult",
    "add_note",
    "add_paper",
    "browse_library",
    "cnki_add_to_zotero",
    "cnki_navigate_pages",
    "cnki_paper_detail",
    "create_annotation",
    "edit_tags",
    "export_bibliography",
    "find_duplicates",
    "find_related_literature",
    "find_similar_papers",
    "merge_duplicates",
    "MergeResult",
    "get_paper",
    "get_paper_content",
    "manage_collections",
    "search_annotations",
    "search_cnki_literature",
    "search_online_literature",
    "search_papers",
    "suggest_citations",
    "sync_index",
    "find_arguments",
    "get_reading_status",
    "recommend_papers",
    "generate_review_note",
    "suggest_tags",
]
