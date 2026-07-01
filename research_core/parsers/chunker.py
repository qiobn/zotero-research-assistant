"""Semantic-aware text chunking with page-number preservation.

Strategy:
1. Extract tables and figures as caption-anchored records (see below), and strip
   table blocks out of the prose so the same content is not indexed twice
2. Concatenate the remaining prose pages, repairing PDF soft line-wraps (CJK
   words and hyphenated Latin words broken across visual lines)
3. Detect reference/bibliography section → tag chunks with section metadata
4. Split by paragraphs, merge short ones up to target_chunk_size, split long
   ones at sentence boundaries (CJK- and ASCII-aware)
5. Fallback to character sliding window for unstructured text (e.g. OCR)
6. Hard-cap every chunk at max size, breaking at the best sentence/clause
   boundary so Chinese text is never cut mid-word
7. Post-process: detect figure/table captions → tag has_figure_table

Tables and figures are NOT structured into cells/JSON. Reliable table structuring
is fundamentally a vision problem (see docs for optional docling/open-parse
preprocessing); geometric detection produces garbage on borderless academic
tables. Instead we treat both like lightweight reference records: we capture
*where* a table/figure is, its caption (roughly what it shows), and — for tables
— the raw block content from the caption until the prose narration resumes, so
the values stay searchable. Prose passages that cite "表3"/"Figure 2" are linked
to these records (table_refs / figure_refs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from research_core.parsers.pdf import PageText

CHUNKING_VERSION = "v2.9.0-quality-metadata"

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

# A table caption at the start of a line, mirroring figures: "表3 ...",
# "Table 3. ...", "Tab. 5:", "附表2 ...". Group 1 = display label, group 2 =
# canonical number, group 3 = caption text on the same line.
_TABLE_CAPTION_RE = re.compile(
    r"^[ \t]*((?:附?表|tables?|tab\.?)\s*([0-9]+[A-Za-z]?(?:[-.][0-9]+)?))"
    r"[.:：、\s]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
# Any table/figure caption line — used to stop a captured table block when the
# next caption begins.
_ANY_CAPTION_LINE = re.compile(
    r"^[ \t]*(?:附?表|tables?|tab\.?|fig(?:ure|s)?\.?|图)\s*[0-9]",
    re.IGNORECASE,
)
# Max characters of raw content captured below a table caption.
_TABLE_BLOCK_MAX = 1100
# A table is always a single chunk; cap its full text below the chunk hard-cap
# (1200) so ``_enforce_max_chars`` never splits a table into a useless tail
# fragment. The caption sits at the front, so trimming only drops the body tail.
_TABLE_TEXT_MAX = 1190
# When the text right after the label is a verb/connector ("表1显示…",
# "Table 1 shows…"), the line is a prose reference, not a real caption — skip it
# so a number-dense sentence isn't mistaken for (and doesn't shadow) the table.
_CAPTION_PROSE_LEAD = re.compile(
    r"^(?:显示|表明|所示|可知|中|反映|列出|给出|说明|描述|呈现|总结|展示|指出|可见|表示"
    r"|shows?|lists?|presents?|indicates?|summari[sz]es?|reports?|displays?"
    r"|gives?|describes?|illustrates?|depicts?)",
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
    # Quality scores (computed post-chunking by score_chunk_quality)
    coherence_score: float = 0.0
    information_density: float = 0.0
    boilerplate_ratio: float = 0.0
    sentence_count: int = 0
    starts_with_conjunction: bool = False
    language: str = ""  # "zh" / "en" / "mixed"
    quality_flag: str = "good"  # "good" / "noisy" / "incomplete" / "boilerplate"


def chunk_text(
    pages: list[PageText],
    tables: object = None,  # legacy/ignored: tables are derived from page text
    target_chunk_size: int = 600,
    max_chunk_size: int = 1200,
    min_chunk_size: int = 100,
    overlap_chars: int = 100,
    # Legacy params kept for backward compat (ignored in v2)
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[Chunk]:
    """Split page-level text into semantic chunks, preserving page numbers.

    Tables and figures are first pulled out as caption-anchored records; table
    blocks are removed from the prose stream so their content is not indexed
    twice. The remaining prose is chunked paragraph-first (merge short, split
    long at sentence boundaries; sliding-window fallback for unstructured text).

    A hard character cap is enforced on every chunk so that run-on text with no
    sentence/paragraph structure (data dumps, equations, OCR) can never produce
    an oversized chunk that blows up embedding memory.

    ``tables`` is accepted but ignored (legacy signature); tables are derived
    from the page text directly.
    """
    figures = _extract_figures(pages)
    table_records, table_spans = _extract_tables(pages)
    prose_pages = _strip_table_spans(pages, table_spans)

    full_text, page_boundaries = _build_text_and_boundaries(prose_pages)

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
                    overlap_chars=overlap_chars,
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

    if table_records:
        chunks.extend(_chunk_tables(table_records))
        available_table_refs = {t.ref for t in table_records if t.ref}
        _tag_refs(chunks, available_table_refs, _TABLE_REF_PREFIX, "table_refs")

    if figures:
        chunks.extend(_chunk_figures(figures))
        available_fig_refs = {f.ref for f in figures}
        _tag_refs(chunks, available_fig_refs, _FIGURE_REF_PREFIX, "figure_refs")

    chunks = _enforce_max_chars(chunks, max_chunk_size)
    score_chunks_quality(chunks)
    return chunks


def _tag_captions(chunks: list[Chunk]) -> None:
    """Post-process: detect figure/table captions and tag metadata."""
    for chunk in chunks:
        if chunk.metadata.get("section") == "references":
            continue
        if _FIGURE_TABLE_CAPTION.search(chunk.text):
            chunk.metadata["has_figure_table"] = True


# ── Table extraction (caption + raw content, not structured) ──────


@dataclass
class _Table:
    ref: str
    label: str
    caption: str
    body: str
    page: int


def _extract_tables(
    pages: list[PageText],
) -> tuple[list[_Table], dict[int, list[tuple[int, int]]]]:
    """Find table captions in raw page text and capture each table's block.

    A table runs from its caption line until the prose narration resumes (or a
    size cap / the next caption). We don't structure the cells — academic
    three-line tables can't be reconstructed reliably from text geometry — we
    just keep the caption plus the raw block so values stay searchable, mirroring
    how figures are handled.

    Returns ``(tables, spans_by_page_index)``. The spans let the caller strip
    table blocks out of the prose so the content is not indexed twice. A caption
    is only accepted as a table (and stripped) when the block below it actually
    looks tabular, which guards against prose sentences like "表3 说明了…".
    """
    by_ref: dict[str, _Table] = {}
    spans_by_page: dict[int, list[tuple[int, int]]] = {}
    for pi, pt in enumerate(pages):
        text = pt.text
        for m in _TABLE_CAPTION_RE.finditer(text):
            ref = _canon_table_ref(m.group(2))
            if not ref:
                continue
            if _CAPTION_PROSE_LEAD.match(m.group(3).strip()):
                continue  # "表1显示…/Table 1 shows…" — a prose reference, not a caption
            block_end = _table_block_end(text, m.end())
            body = text[m.end():block_end].strip()
            if not _looks_tabular(body):
                continue  # a prose mention, not an actual table caption
            label = " ".join(m.group(1).split())
            caption = " ".join(m.group(3).split())[:250]
            spans_by_page.setdefault(pi, []).append((m.start(), block_end))
            prev = by_ref.get(ref)
            if prev is None or len(body) > len(prev.body):
                by_ref[ref] = _Table(
                    ref=ref, label=label, caption=caption,
                    body=body[:_TABLE_BLOCK_MAX], page=pt.page_num,
                )
    return list(by_ref.values()), spans_by_page


def _table_block_end(text: str, pos: int) -> int:
    """End offset of a table block that starts just after a caption line.

    Consumes following lines until a prose paragraph resumes, the next caption
    begins, a blank-line gap, or the size cap is reached.
    """
    n = len(text)
    limit = min(n, pos + _TABLE_BLOCK_MAX)
    end = pos
    blanks = 0
    while end < n and end < limit:
        nl = text.find("\n", end)
        if nl == -1:
            nl = n
        line = text[end:nl]
        s = line.strip()
        if not s:
            blanks += 1
            if blanks >= 2:
                break
            end = nl + 1
            continue
        blanks = 0
        if _is_prose_line(s) or _ANY_CAPTION_LINE.match(line):
            break
        end = nl + 1
    return min(end, n)


def _is_prose_line(s: str) -> bool:
    """Heuristic: a line that reads as narrative prose, not a table row.

    Table rows are short and/or number-dense; prose lines close with sentence
    punctuation or run long. The sentence-terminator check is length-aware in a
    CJK-friendly way: Chinese sentences pack meaning into few characters
    ("……为主。" is ~12 chars but is clearly prose), so a terminated line of ≥12
    chars counts as prose, while any line of ≥50 chars (wrapped prose without a
    visible terminator) does too.
    """
    if not s:
        return False
    digits = sum(c.isdigit() for c in s)
    if digits / len(s) >= 0.25:
        return False  # number-dense → a data row, not prose
    if s[-1] in "。！？.!?" and len(s) >= 12:
        return True
    # CJK packs ~2x the information per character, so a long CJK-dominant line
    # with no terminator is almost always a wrapped prose line, not a table cell
    # (table cells are short labels/values). Latin prose only trips at ≥50.
    cjk = sum(1 for c in s if "\u3400" <= c <= "\u9fff")
    if cjk >= len(s) * 0.5 and len(s) >= 24:
        return True
    return len(s) >= 50


def _looks_tabular(body: str) -> bool:
    """True if a captured block looks like table content.

    Table cells sit on their own short lines (PDF column extraction) — either
    brief text labels ("Attribute level", "区名称") or number-dense values. Prose
    that leaked past ``_table_block_end`` would show up as long, sentence-like
    lines, so a high share of short/numeric lines marks a real table. We require
    a few rows to avoid promoting one-line caption fragments.
    """
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    cellish = 0
    for ln in lines:
        digits = sum(c.isdigit() for c in ln)
        if len(ln) <= 25 or digits / len(ln) >= 0.2:
            cellish += 1
    return cellish / len(lines) >= 0.6


def _strip_table_spans(
    pages: list[PageText],
    spans_by_page: dict[int, list[tuple[int, int]]],
) -> list[PageText]:
    """Return pages with captured table spans removed from the prose text."""
    if not spans_by_page:
        return pages
    out: list[PageText] = []
    for pi, pt in enumerate(pages):
        spans = spans_by_page.get(pi)
        if not spans:
            out.append(pt)
            continue
        text = pt.text
        for start, end in sorted(spans, reverse=True):
            text = text[:start] + "\n" + text[end:]
        out.append(PageText(page_num=pt.page_num, text=text))
    return out


def _chunk_tables(tables: list[_Table]) -> list[Chunk]:
    """One chunk per table: caption + raw block content (not structured).

    The chunk is retrievable on its own and resolvable from prose that cites the
    table. No cells/JSON — just the label, caption and the rough content so the
    table's values stay searchable.
    """
    chunks: list[Chunk] = []
    for t in tables:
        head = f"{t.label} {t.caption}".strip() if t.caption else t.label
        text = f"{head}\n{t.body}".strip() if t.body else head
        if len(text) > _TABLE_TEXT_MAX:
            text = text[:_TABLE_TEXT_MAX]
        meta = {
            "is_table": True,
            "has_figure_table": True,
            "table_label": t.label,
            "table_caption": t.caption,
        }
        if t.ref:
            meta["table_ref"] = t.ref
        chunks.append(Chunk(
            text=text,
            page_start=t.page,
            page_end=t.page,
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
    overlap_chars: int = 100,
) -> list[Chunk]:
    """Merge short paragraphs and split long ones at sentence boundaries.

    Adds a ~100-character overlap between consecutive content chunks: the
    trailing text of a chunk is prepended to the next one. The overlap boundary
    is extended backwards to the nearest sentence-start so a CJK word or
    English sentence is never cut mid-token. This preserves context that
    straddles chunk boundaries and improves retrieval recall.
    Overlap is not applied across the references boundary.
    """
    chunks: list[Chunk] = []
    current_text = ""
    current_start = 0
    prev_tail = ""  # overlap text carried into the next content chunk

    def _tail(text: str) -> str:
        """Return the trailing ~overlap_chars of *text*, started at a sentence
        boundary so no sentence or CJK word is cut in half.

        Algorithm: scan backward from (|text| - overlap_chars + small_margin)
        to find the LAST sentence terminator before the overlap window ends.
        The text AFTER that terminator becomes the overlap.

        If no sentence terminator is found, tries clause punctuation as a
        secondary boundary. Returns empty only when no usable boundary exists
        (formula blocks, data dumps)."""
        if overlap_chars <= 0:
            return ""
        if len(text) <= overlap_chars:
            return text.strip()

        # Search window: from (end - overlap_chars) extending overlap_chars
        # forward, so we always capture at least one full sentence boundary.
        search_end = len(text)  # scan up to the very end
        search_start = max(0, len(text) - overlap_chars * 2)

        # Tier 1: find the LAST sentence terminator in the search window
        last_boundary = -1
        for i in range(search_start, search_end):
            if text[i] in "。！？.!?；;…":
                last_boundary = i
        if last_boundary >= 0:
            # Skip whitespace after the terminator
            j = last_boundary + 1
            while j < len(text) and text[j] in " \t\n":
                j += 1
            if j < len(text):
                return text[j:].strip()

        # Tier 2: clause punctuation (last one in window)
        for i in range(search_start, search_end):
            if text[i] in "，、,：:）)】」』":
                last_boundary = i
        if last_boundary >= 0:
            j = last_boundary + 1
            while j < len(text) and text[j] in " \t\n":
                j += 1
            if j < len(text):
                return text[j:].strip()

        # No usable boundary
        return ""

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


# ── Chunk Quality Scoring ────────────────────────────────────────────


# Common CJK + English stopwords for information density calculation
_CJK_STOP = set("的了吗呢吧啊是在有和与及或到对给让把被比向从为以因所以虽然但是如果然而"
                "等这那其该何每某个些很都也太更最极了着过还又之而于之")
_EN_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "in", "of", "to",
    "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "under",
    "and", "or", "not", "but", "if", "while", "where", "when", "which",
    "who", "whom", "whose", "this", "that", "these", "those", "it", "its",
    "they", "them", "their", "we", "us", "our", "he", "she", "his", "her",
    "also", "such", "than", "then", "about", "each", "all", "both", "few",
    "more", "most", "other", "some", "only", "over", "very", "just",
}

# Academic boilerplate fragments (case-insensitive)
_BOILERPLATE_FRAGMENTS = [
    "the author", "the authors", "we would like to thank",
    "this work was supported", "this research was funded",
    "this study was supported", "funded by", "grant number",
    "conflict of interest", "competing interest", "declaration of",
    "supplementary material", "supplementary data", "supporting information",
    "available online", "additional file", "data availability statement",
    "author contributions", "corresponding author", "correspondence to",
    "open access", "creative commons", "cc by", "all rights reserved",
    "published by", "peer review", "submitted", "accepted", "revised",
]

# Words that suggest a chunk was split mid-sentence from the previous chunk
_CONJUNCTION_STARTS = {
    "and", "or", "but", "also", "however", "therefore", "thus", "moreover",
    "furthermore", "additionally", "meanwhile", "nevertheless", "then",
    "此外", "另外", "而且", "并且", "但是", "然而", "因此", "所以",
    "同时", "另一方面", "除此之外", "不仅如此", "综上",
}

_BOILERPLATE_PATTERNS = [
    re.compile(r"(?:http|www\.)\S+", re.IGNORECASE),
    re.compile(r"^\d{1,3}\s*$", re.MULTILINE),  # standalone number (page/line)
]


def score_chunk_quality(chunk: Chunk) -> None:
    """Compute and populate quality scores for a single chunk in-place.

    Lightweight heuristics — no extra model calls. Designed to run at scale
    during indexing without meaningful overhead.
    """
    text = chunk.text.strip()
    if not text:
        chunk.quality_flag = "incomplete"
        return

    # ── Sentence count ──
    sentences = _split_sentences(text)
    chunk.sentence_count = len(sentences)

    # ── Language detection ──
    cjk_chars = sum(1 for c in text if "一" <= c <= "鿿")
    ascii_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    total_alpha = cjk_chars + ascii_chars
    if total_alpha > 0:
        cjk_ratio = cjk_chars / total_alpha
        if cjk_ratio > 0.6:
            chunk.language = "zh"
        elif cjk_ratio < 0.1:
            chunk.language = "en"
        else:
            chunk.language = "mixed"
    else:
        chunk.language = "en"  # default for numeric/formula-heavy

    # ── Information density ──
    chars_no_punct = sum(1 for c in text if c.isalnum() or c.isspace())
    if len(text) > 0:
        chunk.information_density = round(chars_no_punct / len(text), 3)

    # ── Boilerplate ratio ──
    text_lower = text.lower()
    boiler_hits = 0
    for frag in _BOILERPLATE_FRAGMENTS:
        if frag in text_lower:
            boiler_hits += 1
    for pat in _BOILERPLATE_PATTERNS:
        boiler_hits += len(pat.findall(text))
    # Normalize: boiler hits per 100 chars
    chunk.boilerplate_ratio = round(min(1.0, boiler_hits / max(1, len(text)) * 100), 3)

    # ── Coherence proxy ──
    # Use sentence length consistency as a proxy for structural coherence.
    # Well-written prose has moderate variance; lists/tables have extreme variance.
    if len(sentences) >= 2:
        sent_lens = [len(s) for s in sentences]
        avg_len = sum(sent_lens) / len(sent_lens)
        if avg_len > 0:
            variance = sum((l - avg_len) ** 2 for l in sent_lens) / len(sent_lens)
            cv = (variance ** 0.5) / avg_len  # coefficient of variation
            # Map CV to a 0-1 coherence score (lower CV = more coherent)
            chunk.coherence_score = round(max(0.0, min(1.0, 1.0 - cv)), 3)
    else:
        chunk.coherence_score = 1.0  # single sentence = coherent by definition

    # ── Starts with conjunction ──
    first_word = text.split()[0].strip("，。！？.!?；;…,、:：") if text.split() else ""
    chunk.starts_with_conjunction = first_word.lower() in _CONJUNCTION_STARTS

    # ── Quality flag ──
    if len(text) < 40:
        chunk.quality_flag = "incomplete"
    elif chunk.boilerplate_ratio > 0.15:
        chunk.quality_flag = "boilerplate"
    elif chunk.coherence_score < 0.3:
        chunk.quality_flag = "noisy"
    elif chunk.starts_with_conjunction and chunk.information_density < 0.5:
        chunk.quality_flag = "noisy"
    else:
        chunk.quality_flag = "good"


def score_chunks_quality(chunks: list[Chunk]) -> list[Chunk]:
    """Convenience: score all chunks in a list."""
    for c in chunks:
        score_chunk_quality(c)
    return chunks
