"""Semantic-aware text chunking with page-number preservation.

Strategy:
1. Concatenate pages, repairing PDF soft line-wraps (CJK words and hyphenated
   Latin words broken across visual lines)
2. Detect reference/bibliography section → tag chunks with section metadata
3. Split by paragraphs (double newline)
4. Merge short paragraphs up to target_chunk_size
5. Split long paragraphs at sentence boundaries (CJK- and ASCII-aware)
6. Fallback to character sliding window for unstructured text (e.g. OCR)
7. Append structured tables as dedicated Markdown chunks
8. Hard-cap every chunk at max size, breaking at the best sentence/clause
   boundary so Chinese text is never cut mid-word
9. Post-process: detect figure/table captions → tag has_figure_table
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from research_core.parsers.pdf import PageText, TableData

CHUNKING_VERSION = "v2.4-cjk-aware"

# Sentence boundaries, CJK-aware. CJK terminators (。！？；…) are NOT followed by
# a space in Chinese/Japanese text, so we split immediately after them; ASCII
# terminators must be followed by whitespace (avoids splitting "U.S." or "3.14").
_SENTENCE_ENDS = re.compile(
    r"(?<=[。！？；…])\s*|(?<=[.!?;])\s+"
)

# A run of CJK ideographs / kana on both sides of a single newline is a soft
# line-wrap from PDF layout (CJK uses no inter-word spaces), so we join them.
_CJK = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
_CJK_SOFT_WRAP = re.compile(rf"(?<=[{_CJK}])\n+(?=[{_CJK}])")
# Latin hyphenation across a line break: "exam-\nple" -> "example".
_LATIN_HYPHEN_WRAP = re.compile(r"([A-Za-z])-\n+([a-z])")

# Boundary characters for the hard splitter, strongest first. CJK/ASCII sentence
# terminators are preferred, then clause punctuation, then whitespace.
_HARD_CUT_TIERS = ("。！？!?；;…", "，、,：:）)】」』", " \t\n")

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
    tables: list[TableData] | None = None,
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

    Structured ``tables`` (extracted separately from prose) are appended as
    dedicated Markdown chunks, row-grouped so each stays under ``max_chunk_size``.
    Finally a hard character cap is enforced on every chunk so that run-on text
    with no sentence/paragraph structure (data dumps, equations, OCR, borderless
    tables) can never produce an oversized chunk that blows up embedding memory.
    """
    full_text, page_boundaries = _build_text_and_boundaries(pages)

    chunks: list[Chunk] = []
    if full_text.strip():
        ref_start, ref_end = _find_references_range(full_text)
        paragraphs = _split_paragraphs(full_text)
        if paragraphs:
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
                    overlap_sentences=overlap_sentences,
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

    if tables:
        chunks.extend(_chunk_tables(tables, max_size=max_chunk_size))

    chunks = _enforce_max_chars(chunks, max_chunk_size)
    return chunks


def _tag_captions(chunks: list[Chunk]) -> None:
    """Post-process: detect figure/table captions and tag metadata."""
    for chunk in chunks:
        if chunk.metadata.get("section") == "references":
            continue
        if _FIGURE_TABLE_CAPTION.search(chunk.text):
            chunk.metadata["has_figure_table"] = True


# ── Table chunking ────────────────────────────────────────────────


def _chunk_tables(tables: list[TableData], *, max_size: int) -> list[Chunk]:
    """Turn structured tables into Markdown chunks, row-grouped under max_size.

    Each chunk carries:
    - text: a self-contained Markdown table (caption + repeated header + rows),
      which embeds well and is directly LLM-readable.
    - metadata.table_json: the structured form of the rows in this chunk, a
      JSON object {caption, page, columns, rows:[{col: val}], part, parts,
      n_rows_total}. For complex/multi-level headers, column names are already
      flattened (e.g. "Group A / Mean") so each row maps cleanly to scalars.
    """
    chunks: list[Chunk] = []
    for table in tables:
        groups = _row_groups(table, max_size)
        parts = len(groups)
        for part_i, rows in enumerate(groups):
            md = _table_markdown(table.caption, table.columns, rows, part_i, parts)
            payload = {
                "caption": table.caption,
                "page": table.page_num,
                "columns": table.columns,
                "rows": [
                    dict(zip(table.columns, row, strict=False)) for row in rows
                ],
                "part": part_i + 1,
                "parts": parts,
                "n_rows_total": table.n_rows,
            }
            chunks.append(Chunk(
                text=md,
                page_start=table.page_num,
                page_end=table.page_num,
                chunk_idx=len(chunks),
                metadata={
                    "is_table": True,
                    "has_figure_table": True,
                    "table_caption": table.caption,
                    "table_part": part_i + 1,
                    "table_parts": parts,
                    "n_rows": table.n_rows,
                    "n_cols": table.n_cols,
                    "table_json": json.dumps(payload, ensure_ascii=False),
                },
            ))
    return chunks


def _row_groups(table: TableData, max_size: int) -> list[list[list[str]]]:
    """Split a table's rows so each group's Markdown stays under max_size."""
    overhead = len(_table_markdown(table.caption, table.columns, [], 0, 1))
    budget = max(max_size - overhead, max_size // 2)
    groups: list[list[list[str]]] = []
    current: list[list[str]] = []
    current_len = 0
    for row in table.rows:
        row_len = sum(len(c) for c in row) + 3 * max(len(row), 1) + 2
        if current and current_len + row_len > budget:
            groups.append(current)
            current = []
            current_len = 0
        current.append(row)
        current_len += row_len
    if current:
        groups.append(current)
    return groups or [[]]


def _table_markdown(
    caption: str,
    columns: list[str],
    rows: list[list[str]],
    part_i: int,
    parts: int,
) -> str:
    """Render a (slice of a) table as a GitHub-flavored Markdown table."""
    lines: list[str] = []
    if caption:
        lines.append(caption + (f" (part {part_i + 1}/{parts})" if parts > 1 else ""))
    elif parts > 1:
        lines.append(f"(table part {part_i + 1}/{parts})")
    cols = columns or ["col_1"]
    lines.append("| " + " | ".join(_md_cell(c) for c in cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in rows:
        cells = [_md_cell(c) for c in row]
        if len(cells) < len(cols):
            cells += [""] * (len(cols) - len(cells))
        lines.append("| " + " | ".join(cells[: len(cols)]) + " |")
    return "\n".join(lines)


def _md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


# ── Hard size cap ─────────────────────────────────────────────────


def _enforce_max_chars(chunks: list[Chunk], max_size: int) -> list[Chunk]:
    """Guarantee no chunk exceeds max_size chars; split oversized ones.

    This is the universal safety floor: regardless of how a chunk was produced
    (prose, table, run-on data), it will be bounded. Chunk indices are
    reassigned sequentially so ids stay unique.
    """
    out: list[Chunk] = []
    for chunk in chunks:
        if len(chunk.text) <= max_size:
            out.append(chunk)
            continue
        for piece in _hard_split(chunk.text, max_size):
            out.append(Chunk(
                text=piece,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                chunk_idx=0,
                metadata=dict(chunk.metadata),
            ))
    for i, chunk in enumerate(out):
        chunk.chunk_idx = i
    return out


def _hard_split(text: str, max_size: int) -> list[str]:
    """Split text into <= max_size pieces at the best available boundary.

    Prefers sentence terminators (。！？.!?), then clause punctuation (，、,：),
    then whitespace. This keeps Chinese text — which has no inter-word spaces —
    from being cut mid-word/mid-sentence the way a whitespace-only splitter does.
    """
    pieces: list[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        end = min(pos + max_size, n)
        if end < n:
            cut = _best_cut(text, pos + max_size // 2, end)
            if cut > pos:
                end = cut
        piece = text[pos:end].strip()
        if piece:
            pieces.append(piece)
        pos = end if end > pos else pos + max_size
    return pieces


def _best_cut(text: str, lo: int, hi: int) -> int:
    """Return the best split offset in [lo, hi), or -1 if none.

    Tries each boundary tier (strongest first) and returns the position just
    after the rightmost matching char so the boundary stays with the left piece.
    """
    for tier in _HARD_CUT_TIERS:
        idx = max((text.rfind(ch, lo, hi) for ch in tier), default=-1)
        if idx >= 0:
            return idx + 1
    return -1


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
        full_text += _join_soft_wraps(pt.text) + "\n"
        boundaries.append((start, len(full_text), pt.page_num))
    return full_text, boundaries


def _join_soft_wraps(text: str) -> str:
    """Repair PDF layout line-wraps so words/sentences aren't broken mid-token.

    PDF extraction inserts a newline at every visual line break, including in the
    middle of a CJK sentence ("满\\n意度") or a hyphenated English word
    ("exam-\\nple"). We rejoin those soft wraps. Paragraph breaks (blank lines)
    and newlines adjacent to punctuation are preserved.
    """
    text = _LATIN_HYPHEN_WRAP.sub(r"\1\2", text)
    text = _CJK_SOFT_WRAP.sub("", text)
    return text


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
    overlap_sentences: int = 1,
) -> list[Chunk]:
    """Merge short paragraphs and split long ones at sentence boundaries.

    Adds a small sentence-level overlap between consecutive content chunks: the
    trailing `overlap_sentences` sentences of a chunk are prepended to the next
    one. This preserves context that straddles chunk boundaries and improves
    retrieval recall. Overlap is not applied across the references boundary.
    """
    chunks: list[Chunk] = []
    current_text = ""
    current_start = 0
    prev_tail = ""  # trailing sentences carried into the next content chunk

    def _tail(text: str) -> str:
        if overlap_sentences <= 0:
            return ""
        sentences = _split_sentences(text)
        if not sentences:
            return ""
        return " ".join(sentences[-overlap_sentences:])

    def emit(text: str, char_start: int, char_end: int):
        nonlocal prev_tail
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
        stored_text = stripped
        if is_ref:
            meta["section"] = "references"
            prev_tail = ""  # never bridge across the references boundary
        else:
            if prev_tail and not stored_text.startswith(prev_tail):
                stored_text = f"{prev_tail} {stored_text}"
            prev_tail = _tail(stripped)
        chunks.append(Chunk(
            text=stored_text,
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
