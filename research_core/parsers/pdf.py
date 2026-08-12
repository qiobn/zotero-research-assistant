"""PDF text extraction with per-page tracking and column-aware reading.

We extract prose text only. Tables and figures are handled downstream by the
chunker as lightweight caption-anchored records (see ``chunker.py``): we record
where they are and roughly what they contain, but we do NOT structure table
cells. Reliable table structuring is a vision problem — geometric/line-based
detection produces garbage on borderless "three-line" academic tables and even
mis-segments multi-column prose. Users who need true table structuring can
preprocess with a visual parser (docling / open-parse / unstructured); see docs.

Column-aware extraction
-----------------------
The naive ``page.get_text("text")`` interleaves lines between columns for many
two/three-column journal PDFs (L1, R1, L2, R2 ...), producing garbled reading
order. We instead cluster text lines by x-position into real columns (block
geometry) and read left column → right column, top-to-bottom within each
column. A cluster only counts as a real column when it holds a substantial
share of the page's lines, so headers, footers, figure captions and sidebars
are not mistaken for columns. Between columns we emit a blank line so the
chunker keeps a paragraph boundary.

Quality gate
------------
Each PDF also gets an ``ExtractionQuality`` report (garbled characters,
word-by-word fragmentation, empty pages, scanned/no-text). The indexer uses it
so broken extractions are flagged and reported instead of silently indexed.
No OCR is performed; scanned PDFs simply report ``scanned=True``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf


@dataclass
class PageText:
    page_num: int
    text: str


@dataclass
class ExtractionQuality:
    """Quality signals for a parsed PDF (consumed by the indexer quality gate)."""
    page_count: int = 0
    total_chars: int = 0
    empty_pages: int = 0
    garbled: bool = False      # U+FFFD / NUL replacement chars present
    fragmented: bool = False   # word-by-word line fragmentation (broken layout)
    scanned: bool = False      # no extractable text (image-only PDF)


@dataclass
class ParsedPDF:
    pages: list[PageText] = field(default_factory=list)
    quality: ExtractionQuality = field(default_factory=ExtractionQuality)
    # Retained for backward compatibility; always empty (tables are derived from
    # page text by the chunker, not structured here).
    tables: list = field(default_factory=list)


# ── Column detection constants ────────────────────────────────────────────
_MIN_COLUMN_LINES = 3          # a real column needs at least this many lines
_COLUMN_X_TOL = 16             # points; x0 band for clustering lines into a column
_COLUMN_SHARE = 0.25           # cluster kept as a column if lines >= share*max
_SCANNED_CHARS = 300           # below this total → treated as scanned/no-text
_EMPTY_PAGE_CHARS = 30


def extract_pdf_text(path: str) -> list[PageText]:
    """Backward-compatible helper: return prose pages only."""
    return extract_pdf(path).pages


def extract_pdf(path: str) -> ParsedPDF:
    """Extract per-page prose text from a PDF in column-aware reading order.

    Pure text extraction via PyMuPDF; no OCR (scanned PDFs yield empty pages and
    are reported via ``quality.scanned``). Tables/figures are not structured
    here — the chunker turns their captions into reference records.
    """
    pages: list[PageText] = []
    quality = ExtractionQuality()
    with pymupdf.open(path) as doc:
        quality.page_count = doc.page_count
        for i, page in enumerate(doc):
            text = _extract_page_columns(page)
            quality.total_chars += len(text)
            if len(text.strip()) < _EMPTY_PAGE_CHARS:
                quality.empty_pages += 1
            if _REPLACEMENT_CHAR.search(text):
                quality.garbled = True
            if text.strip():
                pages.append(PageText(page_num=i + 1, text=text))
    quality.scanned = quality.total_chars < _SCANNED_CHARS and quality.page_count > 0
    quality.fragmented = _detect_fragmentation(pages)
    return ParsedPDF(pages=pages, quality=quality)


# ── Column-aware page extraction ──────────────────────────────────────────

def _page_text_lines(page):
    """Return (x0, x1, y0, text) for every text line, in dict extraction order."""
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type", 0) != 0:  # skip image blocks
            continue
        for line in block.get("lines", []):
            b = line.get("bbox", [])
            txt = "".join(s.get("text", "") for s in line.get("spans", []))
            if txt.strip() and len(b) >= 4:
                lines.append((b[0], b[2], b[1], txt))
    return lines


def _cluster_columns(lines):
    """Greedy x-clustering of text lines into column groups.

    Clusters by the line's START x (x0), not its width: within a column the
    x0s sit in a narrow band (indentation is a few points), while columns start
    at widely separated positions. Clustering by width (x1) fails because a
    wide line's right edge can reach close to the next column, merging the two.

    Returns list of (column_x0, [lines]) in left-to-right x order.
    """
    if not lines:
        return []
    ordered = sorted(lines, key=lambda ln: ln[0])
    clusters = []
    cur = [ordered[0]]
    cur_max_x0 = ordered[0][0]
    for ln in ordered[1:]:
        if ln[0] - cur_max_x0 > _COLUMN_X_TOL:
            clusters.append(cur)
            cur = [ln]
            cur_max_x0 = ln[0]
        else:
            cur.append(ln)
            cur_max_x0 = max(cur_max_x0, ln[0])
    clusters.append(cur)
    return [(min(c[0] for c in group), group) for group in clusters]


def _real_columns(clusters):
    """Keep only substantial text columns (drop captions/headers/sidebars).

    A cluster is a real column only if it holds at least ``_COLUMN_SHARE`` of
    the largest cluster's lines (and at least ``_MIN_COLUMN_LINES``). This
    prevents a 1-3 line figure caption or running head from being treated as a
    second column.
    """
    if not clusters:
        return []
    max_n = max(len(c[1]) for c in clusters)
    threshold = max(_MIN_COLUMN_LINES, int(max_n * _COLUMN_SHARE))
    return [c for c in clusters if len(c[1]) >= threshold]


def _extract_page_columns(page) -> str:
    """Column-aware reading order for one page.

    Single column → top-to-bottom (then left-to-right within a line).
    Multi column → left-to-right columns, each top-to-bottom; blank line
    between columns so the chunker keeps a paragraph boundary.
    """
    lines = _page_text_lines(page)
    if not lines:
        return ""
    columns = _real_columns(_cluster_columns(lines))
    if len(columns) <= 1:
        col_lines = columns[0][1] if columns else lines
        col_lines = sorted(col_lines, key=lambda ln: (ln[2], ln[0]))  # y, then x
        return "\n".join(ln[3] for ln in col_lines)
    parts = []
    for _x0, col_lines in sorted(columns, key=lambda c: c[0]):
        col_lines = sorted(col_lines, key=lambda ln: (ln[2], ln[0]))
        parts.append("\n".join(ln[3] for ln in col_lines))
    return "\n\n".join(parts)


# ── Fragmentation / quality detection ─────────────────────────────────────

_REPLACEMENT_CHAR = re.compile(r"[�\x00]")
_CJK_CHAR = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def _is_single_word_fragment(line: str) -> bool:
    """True for the word-per-line broken layout (e.g. 'police' / 'accident' / ...).

    Only pure-Latin single/duo-word lines count — Chinese headers and DOI lines
    are legitimate short lines and must not trigger this (they contain CJK or
    many tokens).
    """
    line = line.strip()
    if not line or _CJK_CHAR.search(line):
        return False
    tokens = line.split()
    if not tokens or len(tokens) > 2:
        return False
    return all(len(t) <= 20 for t in tokens)


def _detect_fragmentation(pages: list[PageText], sample_pages: int = 8) -> bool:
    """Heuristic for word-by-word layout fragmentation.

    A broken 'one word per line' extraction (e.g. Sasidharan 2015) fragments the
    document from the FIRST content page onward. Healthy papers only have
    localized short lines — table cells, equations, headers — confined to body
    pages. So we flag only when BOTH the first content page AND a majority of
    sampled pages are mostly single-word Latin lines.
    """
    ratios: list[float] = []
    for pt in pages[:sample_pages]:
        total = 0
        short = 0
        for line in pt.text.splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            if _is_single_word_fragment(line):
                short += 1
        if total >= 20:
            ratios.append(short / total)
    if len(ratios) < 3:
        return False
    first_broken = ratios[0] > 0.4
    majority_broken = sum(1 for r in ratios if r > 0.4) / len(ratios) >= 0.6
    return first_broken and majority_broken
