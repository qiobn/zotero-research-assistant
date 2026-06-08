"""Semantic-aware text chunking with page-number preservation.

Strategy (v2.1-semantic):
1. Concatenate pages preserving page boundaries
2. Detect reference/bibliography section → tag chunks with section metadata
3. Split by paragraphs (double newline)
4. Merge short paragraphs up to target_chunk_size
5. Split long paragraphs at sentence boundaries
6. Fallback to character sliding window for unstructured text (e.g. OCR)
7. Post-process: detect figure/table captions → tag has_figure_table
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from research_core.parsers.pdf import PageText

CHUNKING_VERSION = "v2.1-semantic"

_SENTENCE_ENDS = re.compile(
    r"(?<=[.!?。！？；\n])\s+"
)

_REFERENCES_HEADING = re.compile(
    r"^\s*(References|Bibliography|REFERENCES|BIBLIOGRAPHY|参考文献|引用文献)\s*$",
    re.MULTILINE,
)

_POST_REFERENCES_HEADING = re.compile(
    r"^\s*(Appendi(?:x|ces)|APPENDI(?:X|CES)|附录|Supplementary|SUPPLEMENTARY|"
    r"Supporting\s+Information|Acknowledgements?|ACKNOWLEDGEMENTS?|致谢)"
    r"(\s+[A-Z0-9.:]+)?\s*$",
    re.MULTILINE,
)

_FIGURE_TABLE_CAPTION = re.compile(
    r"^(Fig(?:ure|\.)?|Table|TABLE|FIGURE|图|表)\s*[0-9A-Z]+[.:]?\s",
    re.MULTILINE,
)


@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int
    chunk_idx: int
    metadata: dict = field(default_factory=dict)


def chunk_text(
    pages: list[PageText],
    target_chunk_size: int = 600,
    max_chunk_size: int = 1200,
    min_chunk_size: int = 100,
    overlap_sentences: int = 1,
    # Legacy params kept for backward compat (ignored in v2)
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[Chunk]:
    """Split page-level text into semantic chunks, preserving page numbers.

    Paragraph-first strategy: splits on double newlines, merges short
    paragraphs, and splits long ones at sentence boundaries. Falls back
    to sliding window for text without paragraph structure.
    """
    full_text, page_boundaries = _build_text_and_boundaries(pages)
    if not full_text.strip():
        return []

    ref_start, ref_end = _find_references_range(full_text)

    paragraphs = _split_paragraphs(full_text)
    if not paragraphs:
        return []

    has_paragraph_structure = (
        len(paragraphs) > 2
        and any(len(p.text) > 50 for p in paragraphs)
    )

    if has_paragraph_structure:
        chunks = _chunk_by_paragraphs(
            paragraphs,
            page_boundaries=page_boundaries,
            target_size=target_chunk_size,
            max_size=max_chunk_size,
            min_size=min_chunk_size,
            ref_start=ref_start,
            ref_end=ref_end,
        )
    else:
        chunks = _chunk_sliding_window(
            full_text,
            page_boundaries=page_boundaries,
            chunk_size=target_chunk_size,
            overlap=min(overlap, target_chunk_size // 5),
            ref_start=ref_start,
            ref_end=ref_end,
        )

    _tag_captions(chunks)
    return chunks


def _tag_captions(chunks: list[Chunk]) -> None:
    """Post-process: detect figure/table captions and tag metadata."""
    for chunk in chunks:
        if chunk.metadata.get("section") == "references":
            continue
        if _FIGURE_TABLE_CAPTION.search(chunk.text):
            chunk.metadata["has_figure_table"] = True


# ── Internal data structures ──────────────────────────────────────


@dataclass
class _Paragraph:
    text: str
    char_start: int
    char_end: int


# ── Core logic ────────────────────────────────────────────────────


def _build_text_and_boundaries(
    pages: list[PageText],
) -> tuple[str, list[tuple[int, int, int]]]:
    """Concatenate pages into one string, tracking page char boundaries."""
    full_text = ""
    boundaries: list[tuple[int, int, int]] = []
    for pt in pages:
        start = len(full_text)
        full_text += pt.text + "\n"
        boundaries.append((start, len(full_text), pt.page_num))
    return full_text, boundaries


def _find_references_range(full_text: str) -> tuple[int, int]:
    """Find char range [start, end) of the References section.

    Returns (start, end) where:
    - start: beginning of References heading
    - end: beginning of next section (Appendix, etc.) or len(full_text)

    If no References found, returns (len, len) → nothing tagged.
    """
    match = _REFERENCES_HEADING.search(full_text)
    if not match:
        return (len(full_text), len(full_text))

    ref_start = match.start()

    post_match = _POST_REFERENCES_HEADING.search(
        full_text, pos=match.end()
    )
    ref_end = post_match.start() if post_match else len(full_text)

    return (ref_start, ref_end)


def _split_paragraphs(full_text: str) -> list[_Paragraph]:
    """Split text into paragraphs by double newlines or significant gaps."""
    raw_parts = re.split(r"\n\s*\n", full_text)
    paragraphs: list[_Paragraph] = []
    pos = 0
    for part in raw_parts:
        idx = full_text.find(part, pos)
        if idx == -1:
            idx = pos
        stripped = part.strip()
        if stripped:
            paragraphs.append(_Paragraph(
                text=stripped,
                char_start=idx,
                char_end=idx + len(part),
            ))
        pos = idx + len(part)
    return paragraphs


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using punctuation boundaries."""
    parts = _SENTENCE_ENDS.split(text)
    return [s.strip() for s in parts if s.strip()]


def _chunk_by_paragraphs(
    paragraphs: list[_Paragraph],
    *,
    page_boundaries: list[tuple[int, int, int]],
    target_size: int,
    max_size: int,
    min_size: int,
    ref_start: int,
    ref_end: int,
) -> list[Chunk]:
    """Merge short paragraphs and split long ones at sentence boundaries."""
    chunks: list[Chunk] = []
    current_text = ""
    current_start = 0

    def emit(text: str, char_start: int, char_end: int):
        stripped = text.strip()
        if not stripped or len(stripped) < min_size // 2:
            return
        is_ref = ref_start <= char_start < ref_end
        page_start = _page_at(char_start, page_boundaries)
        page_end = _page_at(
            min(char_end, char_start + len(stripped)) - 1,
            page_boundaries,
        )
        meta: dict = {}
        if is_ref:
            meta["section"] = "references"
        chunks.append(Chunk(
            text=stripped,
            page_start=page_start,
            page_end=page_end,
            chunk_idx=len(chunks),
            metadata=meta,
        ))

    for para in paragraphs:
        para_text = para.text

        # Force split at reference section boundaries
        crosses_ref = (
            current_start < ref_start <= para.char_start
            or current_start < ref_end <= para.char_start
        )

        if (
            not crosses_ref
            and len(current_text) + len(para_text) + 1 <= max_size
        ):
            if not current_text:
                current_start = para.char_start
            if current_text:
                current_text += "\n\n" + para_text
            else:
                current_text = para_text
        else:
            if current_text:
                emit(current_text, current_start, para.char_start)
                current_text = ""

            if len(para_text) > max_size:
                sentences = _split_sentences(para_text)
                if not sentences:
                    sentences = [para_text]

                sub_chunk = ""
                sub_start = para.char_start
                for sent in sentences:
                    if (
                        len(sub_chunk) + len(sent) + 1 > max_size
                        and sub_chunk
                    ):
                        emit(sub_chunk, sub_start, para.char_end)
                        sub_start = para.char_start + para.text.find(
                            sent, len(sub_chunk) - len(sent)
                            if len(sub_chunk) > len(sent) else 0
                        )
                        sub_chunk = ""
                    if sub_chunk:
                        sub_chunk += " " + sent
                    else:
                        sub_chunk = sent

                if sub_chunk:
                    emit(sub_chunk, sub_start, para.char_end)
            else:
                current_start = para.char_start
                current_text = para_text

    if current_text and len(current_text.strip()) >= min_size // 2:
        emit(
            current_text,
            current_start,
            current_start + len(current_text),
        )

    return chunks


def _chunk_sliding_window(
    full_text: str,
    *,
    page_boundaries: list[tuple[int, int, int]],
    chunk_size: int,
    overlap: int,
    ref_start: int,
    ref_end: int,
) -> list[Chunk]:
    """Fallback: fixed-size sliding window (for text without paragraphs)."""
    chunks: list[Chunk] = []
    pos = 0
    while pos < len(full_text):
        end = min(pos + chunk_size, len(full_text))
        text = full_text[pos:end].strip()
        if not text:
            break
        is_ref = ref_start <= pos < ref_end
        page_start = _page_at(pos, page_boundaries)
        page_end = _page_at(end - 1, page_boundaries)
        meta: dict = {}
        if is_ref:
            meta["section"] = "references"
        chunks.append(Chunk(
            text=text,
            page_start=page_start,
            page_end=page_end,
            chunk_idx=len(chunks),
            metadata=meta,
        ))
        pos = end - overlap if end < len(full_text) else end
    return chunks


# ── Utilities ─────────────────────────────────────────────────────


def _page_at(
    char_pos: int, boundaries: list[tuple[int, int, int]]
) -> int:
    """Map a character offset to its page number."""
    for start, end, page_num in boundaries:
        if start <= char_pos < end:
            return page_num
    return boundaries[-1][2] if boundaries else 0
