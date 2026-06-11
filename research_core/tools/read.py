"""Reading tools — get paper metadata and content."""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf
from loguru import logger

from research_core.rag.retriever import Retriever
from research_core.utils import WRITE_PREVIEW_HINT
from research_core.zotero.client import ZoteroClient
from research_core.zotero.models import Annotation, Item


@dataclass
class PaperContent:
    """A paper-level reading result with passages and (optionally) user annotations."""

    item_key: str
    title: str
    passages: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    outline: list[dict] = field(default_factory=list)
    fulltext: str = ""
    referenced_tables: list[dict] = field(default_factory=list)
    referenced_figures: list[dict] = field(default_factory=list)


def get_paper(item_key: str, zot: ZoteroClient) -> Item:
    """Return full metadata for one paper."""
    return zot.get_item(item_key)


def _extract_outline(pdf_path: str) -> list[dict]:
    """Extract PDF table of contents / outline via PyMuPDF."""
    try:
        with pymupdf.open(pdf_path) as doc:
            toc = doc.get_toc(simple=True)
            return [
                {"level": level, "title": heading, "page": page_num}
                for level, heading, page_num in toc
            ]
    except Exception as e:
        logger.debug(f"Failed to extract outline: {e}")
        return []


def _extract_fulltext(pdf_path: str, max_pages: int = 50) -> str:
    """Extract full text from PDF, up to max_pages."""
    try:
        parts: list[str] = []
        with pymupdf.open(pdf_path) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    parts.append(f"\n\n[... truncated at page {max_pages}, {len(doc)} total pages ...]")
                    break
                text = page.get_text("text")
                if text.strip():
                    parts.append(f"--- Page {i + 1} ---\n{text}")
        return "\n\n".join(parts)
    except Exception as e:
        logger.debug(f"Failed to extract fulltext: {e}")
        return ""


def _resolve_referenced_tables(
    retriever: Retriever,
    item_key: str,
    refs: list[str],
) -> list[dict]:
    """Fetch the content of tables cited by retrieved passages.

    Each entry carries the table's label, caption, page and raw block text (the
    table's values, not structured cells), deduplicated by ref.
    """
    unique_refs = list(dict.fromkeys(refs))
    table_chunks = retriever.get_item_tables(item_key, refs=unique_refs)
    out: list[dict] = []
    seen: set[str] = set()
    for t in table_chunks:
        ref = t.metadata.get("table_ref", "")
        if ref in seen:
            continue
        seen.add(ref)
        out.append(
            {
                "table_ref": ref,
                "label": t.metadata.get("table_label", ""),
                "caption": t.metadata.get("table_caption", ""),
                "page": t.page_start,
                "text": t.text,
            }
        )
    return out


def _resolve_referenced_figures(
    retriever: Retriever,
    item_key: str,
    refs: list[str],
) -> list[dict]:
    """Fetch caption-only records of figures cited by retrieved passages.

    No image is decoded — each entry carries the figure's label, caption (a
    rough description of what it shows), and page.
    """
    unique_refs = list(dict.fromkeys(refs))
    figure_chunks = retriever.get_item_figures(item_key, refs=unique_refs)
    out: list[dict] = []
    seen: set[str] = set()
    for f in figure_chunks:
        ref = f.metadata.get("figure_ref", "")
        if ref in seen:
            continue
        seen.add(ref)
        out.append(
            {
                "figure_ref": ref,
                "label": f.metadata.get("figure_label", ""),
                "caption": f.metadata.get("figure_caption", ""),
                "page": f.page_start,
            }
        )
    return out


def get_paper_content(
    item_key: str,
    retriever: Retriever,
    zot: ZoteroClient,
    query: str = "",
    page: int | None = None,
    include_annotations: bool = False,
    mode: str = "",
    limit: int = 5,
) -> PaperContent:
    """Read content inside one paper.

    Mode selection:
    - mode='fulltext' → return complete paper text (paginated, up to 50 pages)
    - mode='outline' → return PDF table of contents / headings
    - query given → semantic search within this paper, return top-N passages
    - page given → return all chunks intersecting that page
    - neither → return the first `limit` chunks (paper opening)
    """
    title = ""
    try:
        title = zot.get_item(item_key).title
    except Exception:
        pass

    result = PaperContent(item_key=item_key, title=title)

    if mode in ("fulltext", "outline"):
        pdf_paths = zot.get_pdf_paths_for_keys([item_key])
        pdf_path = pdf_paths.get(item_key)
        if not pdf_path:
            result.fulltext = "(No PDF available for this paper)"
            return result
        if mode == "fulltext":
            result.fulltext = _extract_fulltext(pdf_path)
        if mode == "outline":
            result.outline = _extract_outline(pdf_path)
            if not result.outline:
                result.outline = [{"level": 0, "title": "(No outline/TOC found in this PDF)", "page": 0}]
    else:
        if query.strip():
            results = retriever.search_within_item(item_key, query, n_results=limit)
        elif page is not None:
            results = retriever.get_item_chunks(item_key, page=page)
        else:
            all_chunks = retriever.get_item_chunks(item_key)
            results = all_chunks[:limit]

        cited_tables: list[str] = []
        cited_figures: list[str] = []
        for r in results:
            passage: dict = {
                "text": r.text,
                "page_start": r.page_start,
                "page_end": r.page_end,
                "score": round(r.score, 3) if r.score else None,
            }
            t_raw = r.metadata.get("table_refs", "")
            if t_raw:
                refs = [x for x in t_raw.split(",") if x]
                passage["cites_tables"] = refs
                cited_tables.extend(refs)
            f_raw = r.metadata.get("figure_refs", "")
            if f_raw:
                refs = [x for x in f_raw.split(",") if x]
                passage["cites_figures"] = refs
                cited_figures.extend(refs)
            result.passages.append(passage)

        # Resolve the concrete path: a passage that cites "Table 3" / "Figure 2"
        # pulls in that table's content / figure's caption from this paper.
        if cited_tables:
            result.referenced_tables = _resolve_referenced_tables(
                retriever, item_key, cited_tables
            )
        if cited_figures:
            result.referenced_figures = _resolve_referenced_figures(
                retriever, item_key, cited_figures
            )

    if include_annotations:
        anns: list[Annotation] = zot.get_annotations(item_key)
        for a in anns:
            result.annotations.append(
                {
                    "type": a.annotation_type,
                    "text": a.text,
                    "comment": a.comment,
                    "page": a.page,
                    "color": a.color,
                }
            )

    return result


@dataclass
class AnnotationResult:
    """Result of creating an annotation."""

    confirmed: bool
    preview: dict
    result: dict | None = None
    error: str = ""


def create_annotation(
    item_key: str,
    text: str,
    zot: ZoteroClient,
    page: int = 0,
    comment: str = "",
    color: str = "#ffd400",
    tags: list[str] | None = None,
    confirm: bool = False,
) -> AnnotationResult:
    """Create a highlight annotation on a paper's PDF. Defaults to dry-run.

    Automatically resolves the parent item key to the PDF attachment key.
    """
    attachment_key = zot.get_pdf_attachment_key(item_key)
    if not attachment_key:
        return AnnotationResult(
            confirmed=False, preview={},
            error=f"No PDF attachment found for item {item_key}.",
        )

    preview = {
        "action": "create_annotation",
        "item_key": item_key,
        "attachment_key": attachment_key,
        "page": page,
        "text": text[:200] + ("..." if len(text) > 200 else ""),
        "comment": comment,
        "color": color,
        "tags": tags or [],
    }

    if not confirm:
        preview["next_step"] = WRITE_PREVIEW_HINT
        if not zot.can_write:
            preview["warning"] = (
                "Write operations require ZOTERO_API_KEY and ZOTERO_LIBRARY_ID."
            )
        return AnnotationResult(confirmed=False, preview=preview)

    if not zot.can_write:
        return AnnotationResult(
            confirmed=False, preview=preview,
            error="Write operations are not available.",
        )

    try:
        resp = zot.create_annotation(
            attachment_key=attachment_key,
            page=page,
            text=text,
            comment=comment,
            color=color,
            tags=tags,
        )
        return AnnotationResult(confirmed=True, preview=preview, result={"zotero_response": resp})
    except Exception as e:
        return AnnotationResult(confirmed=False, preview=preview, error=str(e))


def search_annotations(
    query: str,
    zot: ZoteroClient,
    limit: int = 20,
) -> list[dict]:
    """Search annotations (highlights, comments) across ALL papers in the library.

    Returns matching annotations with their parent paper info and page numbers.
    """
    return zot.search_all_annotations(query, limit=limit)
