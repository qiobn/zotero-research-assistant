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

CHUNKING_VERSION = "v2.6-figure-xref"

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

# A table's own label, parsed from the start of its caption:
# "表3 ...", "Table 3.", "Tab. 5", "附表2".
_TABLE_LABEL_RE = re.compile(
    r"^\s*(?:附?表|tables?|tab\.?)\s*([0-9]+(?:[-.][0-9A-Za-z]+)?|[A-Z]?[0-9]+)",
    re.IGNORECASE,
)
# A reference to a table inside prose: "如表3所示", "见表3、4", "Table 3",
# "Tables 3 and 4", "(Tab. 5)". We match the prefix, then pull one or more
# following numbers joined by list connectors.
_TABLE_REF_PREFIX = re.compile(r"(?:附?表|\btables?|\btab\.)\s*", re.IGNORECASE)
_REF_NUM = re.compile(r"[0-9]+[A-Za-z]?(?:[-.][0-9]+)?")
_REF_CONNECTOR = re.compile(r"\s*(?:[、，,;；]|and|&)\s*", re.IGNORECASE)
# A number followed by a measure word is a quantity, not a reference — guards
# against CJK compounds like "试图3次" (tried 3 times) / "代表3个" (3 of them).
_REF_MEASURE = re.compile(r"[次个種种类張张位名条倍成項项點点步人件年月日種%％]")

# Figures are handled like tables but caption-only: we never decode the image,
# we only record where a figure is mentioned and roughly what it depicts (its
# caption text). A figure caption at the start of a line: "图3 ...",
# "Figure 3. ...", "Fig. 5: ...".
_FIGURE_CAPTION_RE = re.compile(
    r"^[ \t]*((?:fig(?:ure)?\.?|图)\s*([0-9]+[A-Za-z]?(?:[-.][0-9]+)?))"
    r"[.:：、\s]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
# A reference to a figure in prose: "如图3所示", "见图3、4", "Figure 3",
# "(Fig. 5)". \b guards against matching "fig" inside words like "config".
_FIGURE_REF_PREFIX = re.compile(r"(?:\bfig(?:ure|s)?\.?|图)\s*", re.IGNORECASE)


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
        table_chunks = _chunk_tables(tables, max_size=max_chunk_size)
        chunks.extend(table_chunks)
        available_table_refs = {
            c.metadata["table_ref"]
            for c in table_chunks
            if c.metadata.get("table_ref")
        }
        _tag_refs(chunks, available_table_refs, _TABLE_REF_PREFIX, "table_refs")

    figures = _extract_figures(pages)
    if figures:
        chunks.extend(_chunk_figures(figures))
        available_fig_refs = {f.ref for f in figures}
        _tag_refs(chunks, available_fig_refs, _FIGURE_REF_PREFIX, "figure_refs")

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
        label, ref = _table_label_and_ref(table.caption)
        groups = _row_groups(table, max_size)
        parts = len(groups)
        for part_i, rows in enumerate(groups):
            md = _table_markdown(
                table.caption, table.columns, rows, part_i, parts
            )
            payload = {
                "caption": table.caption,
                "label": label,
                "page": table.page_num,
                "columns": table.columns,
                "rows": [
                    dict(zip(table.columns, row, strict=False)) for row in rows
                ],
                "part": part_i + 1,
                "parts": parts,
                "n_rows_total": table.n_rows,
            }
            meta = {
                "is_table": True,
                "has_figure_table": True,
                "table_caption": table.caption,
                "table_label": label,
                "table_part": part_i + 1,
                "table_parts": parts,
                "n_rows": table.n_rows,
                "n_cols": table.n_cols,
                "table_json": json.dumps(payload, ensure_ascii=False),
            }
            if ref:
                meta["table_ref"] = ref
            chunks.append(Chunk(
                text=md,
                page_start=table.page_num,
                page_end=table.page_num,
                chunk_idx=len(chunks),
                metadata=meta,
            ))
    return chunks


# ── Table / figure cross-referencing ──────────────────────────────


def _canon_table_ref(token: str) -> str:
    """Canonicalize a reference token so prose mentions match labels.

    Upper-cases letters and strips leading zeros on the leading number
    ("03" -> "3", "s1" -> "S1", "3-1" -> "3-1").
    """
    t = token.strip().upper()
    m = re.match(r"^0*([0-9]+)(.*)$", t)
    if m:
        t = m.group(1) + m.group(2)
    return t


def _table_label_and_ref(caption: str) -> tuple[str, str]:
    """Parse (display_label, canonical_ref) from a caption, or ('', '')."""
    if not caption:
        return "", ""
    m = _TABLE_LABEL_RE.match(caption)
    if not m:
        return "", ""
    return caption[: m.end()].strip(), _canon_table_ref(m.group(1))


def _find_refs(text: str, prefix_re: re.Pattern) -> list[str]:
    """Extract canonical references (after ``prefix_re``) mentioned in prose.

    Handles single refs and enumerations ("表3、4", "Tables 3 and 4").
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in prefix_re.finditer(text):
        pos = m.end()
        while True:
            nm = _REF_NUM.match(text, pos)
            if not nm:
                break
            ref = _canon_table_ref(nm.group(0))
            is_quantity = bool(_REF_MEASURE.match(text, nm.end()))
            if ref and ref not in seen and not is_quantity:
                seen.add(ref)
                out.append(ref)
            pos = nm.end()
            cm = _REF_CONNECTOR.match(text, pos)
            if not cm:
                break
            pos = cm.end()
    return out


def _find_table_refs(text: str) -> list[str]:
    """Extract canonical table references mentioned in prose, in order."""
    return _find_refs(text, _TABLE_REF_PREFIX)


def _tag_refs(
    chunks: list[Chunk],
    available_refs: set[str],
    prefix_re: re.Pattern,
    key: str,
) -> None:
    """Tag prose chunks with the tables/figures they cite.

    Restricting to refs that actually exist in this paper avoids false links
    (e.g. a chunk mentioning "Table 1" of a *cited* work we didn't extract).
    Table and figure chunks themselves are skipped.
    """
    if not available_refs:
        return
    for chunk in chunks:
        if chunk.metadata.get("is_table") or chunk.metadata.get("is_figure"):
            continue
        found = [r for r in _find_refs(chunk.text, prefix_re) if r in available_refs]
        if found:
            chunk.metadata[key] = ",".join(found)


# ── Figure extraction (caption-only) ──────────────────────────────


@dataclass
class _Figure:
    ref: str
    label: str
    caption: str
    page: int


def _extract_figures(pages: list[PageText]) -> list[_Figure]:
    """Find figure captions in the *raw* page text. We record the label, a
    canonical ref, and the caption text (a rough description of what the figure
    shows) — never the image itself. One entry per ref, longest caption wins.

    Raw page text is used (not the soft-wrap-joined full text) so the caption
    stays bounded to its own line instead of bleeding into the next paragraph.
    """
    by_ref: dict[str, _Figure] = {}
    for pt in pages:
        for m in _FIGURE_CAPTION_RE.finditer(pt.text):
            ref = _canon_table_ref(m.group(2))
            if not ref:
                continue
            label = " ".join(m.group(1).split())
            caption = " ".join(m.group(3).split())[:250]
            prev = by_ref.get(ref)
            if prev is None or len(caption) > len(prev.caption):
                by_ref[ref] = _Figure(
                    ref=ref, label=label, caption=caption, page=pt.page_num
                )
    return list(by_ref.values())


def _chunk_figures(figures: list[_Figure]) -> list[Chunk]:
    """One lightweight chunk per figure: its caption text, made retrievable and
    resolvable from prose that cites the figure. No image data is stored.
    """
    chunks: list[Chunk] = []
    for fig in figures:
        text = f"{fig.label} {fig.caption}".strip() if fig.caption else fig.label
        chunks.append(Chunk(
            text=text,
            page_start=fig.page,
            page_end=fig.page,
            chunk_idx=len(chunks),
            metadata={
                "is_figure": True,
                "has_figure_table": True,
                "figure_label": fig.label,
                "figure_ref": fig.ref,
                "figure_caption": fig.caption,
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
    # A compact natural-language column summary. The header row alone embeds
    # poorly for semantic queries ("which paper measured X vs Y"); spelling the
    # columns out as prose markedly improves table recall.
    col_summary = "; ".join(_md_cell(c) for c in cols if c)
    if col_summary:
        lines.append(f"Columns / 列: {col_summary}")
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
