"""PDF text + table extraction with per-page tracking.

Tables are extracted separately from prose using PyMuPDF's *line-based*
detection only. We deliberately avoid the "text" strategy: on borderless
academic PDFs it produces false positives (multi-column reference lists get
mangled into garbage cells), which hurts retrieval quality far more than it
helps. Only confidently-detected ruled tables are structured; everything else
flows through as prose and is bounded by the chunker's hard size cap.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

import pymupdf
from loguru import logger

# A detected table region must overlap a text block by at least this fraction
# for the block to be considered "part of the table" and removed from prose.
_TABLE_OVERLAP_THRESHOLD = 0.5
# Minimum fraction of non-empty cells for a detected grid to be trusted.
_TABLE_MIN_FILL = 0.5

_CAPTION_PREFIXES = ("table", "表", "tab.", "tab ")


@dataclass
class PageText:
    page_num: int
    text: str


@dataclass
class TableData:
    """A structured table extracted from a ruled region of a page."""

    page_num: int
    caption: str
    columns: list[str]
    rows: list[list[str]]

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.columns)


@dataclass
class ParsedPDF:
    pages: list[PageText] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)


def extract_pdf_text(path: str) -> list[PageText]:
    """Backward-compatible helper: prose pages only (table regions removed)."""
    return extract_pdf(path).pages


def extract_pdf(path: str) -> ParsedPDF:
    """Extract prose text and structured tables from a PDF.

    Table handling depends on ZRA_TABLE_MODE:
    - "lite" (default): conservative line-based detection only — fast, no extra
      deps, but only catches ruled tables. Borderless/three-line tables fall
      through to prose and are bounded by the chunker's hard size cap.
    - "ml": Microsoft Table Transformer — reliably structures borderless and
      three-line academic tables (requires the optional `[tables]` extra). Falls
      back to "lite" automatically if the dependencies/models are unavailable.

    In both modes, detected table regions are stripped from the prose so the
    same content is not indexed twice.
    """
    if _table_mode() == "ml":
        try:
            return _extract_with_ml(path)
        except Exception as e:  # never let table extraction break indexing
            logger.warning(f"ML table mode failed ({e}); falling back to lite mode")
    return _extract_lite(path)


def _table_mode() -> str:
    return os.getenv("ZRA_TABLE_MODE", "lite").strip().lower()


def _extract_lite(path: str) -> ParsedPDF:
    pages: list[PageText] = []
    tables: list[TableData] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            page_num = i + 1
            table_rects: list[pymupdf.Rect] = []
            try:
                found = page.find_tables()  # default = conservative line-based
                for t in found.tables:
                    td = _build_table(page, t, page_num)
                    if td is not None:
                        tables.append(td)
                        table_rects.append(pymupdf.Rect(t.bbox))
            except Exception:
                table_rects = []
            text = _prose_excluding_tables(page, table_rects)
            if text.strip():
                pages.append(PageText(page_num=page_num, text=text))
    return ParsedPDF(pages=pages, tables=tables)


def _extract_with_ml(path: str) -> ParsedPDF:
    from research_core.parsers import table_ml

    ml_tables = table_ml.extract_tables(path)
    if not ml_tables:
        # No tables found (or deps missing) — fall back so prose still indexes.
        return _extract_lite(path)

    by_page: dict[int, list] = defaultdict(list)
    for mt in ml_tables:
        by_page[mt.page_num].append(mt)

    pages: list[PageText] = []
    tables: list[TableData] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            page_num = i + 1
            rects = [pymupdf.Rect(mt.bbox) for mt in by_page.get(page_num, [])]
            text = _prose_excluding_tables(page, rects)
            if text.strip():
                pages.append(PageText(page_num=page_num, text=text))
            tables.extend(mt.data for mt in by_page.get(page_num, []))
    return ParsedPDF(pages=pages, tables=tables)


def _prose_excluding_tables(page, table_rects: list) -> str:
    """Return page text with blocks inside detected table regions removed."""
    if not table_rects:
        return page.get_text("text")
    kept: list[str] = []
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, btext = block[0], block[1], block[2], block[3], block[4]
        if not btext.strip():
            continue
        brect = pymupdf.Rect(x0, y0, x1, y1)
        if any(_overlap_fraction(brect, r) >= _TABLE_OVERLAP_THRESHOLD for r in table_rects):
            continue
        kept.append(btext)
    return "\n".join(kept)


def _overlap_fraction(block: "pymupdf.Rect", table: "pymupdf.Rect") -> float:
    """Fraction of the block's area covered by the table rectangle."""
    inter = block & table
    if not inter or block.get_area() <= 0:
        return 0.0
    return inter.get_area() / block.get_area()


def _build_table(page, table, page_num: int) -> TableData | None:
    """Validate and structure a detected table; return None if low-confidence."""
    try:
        grid = table.extract()
    except Exception:
        return None
    if not grid or table.col_count < 2 or table.row_count < 2:
        return None

    norm = [[_clean_cell(c) for c in row] for row in grid]
    total = sum(len(r) for r in norm)
    nonempty = sum(1 for r in norm for c in r if c)
    if total == 0 or nonempty / total < _TABLE_MIN_FILL:
        return None

    columns, data_rows = _split_header(table, norm)
    if not data_rows:
        return None

    caption = _find_caption(page, pymupdf.Rect(table.bbox))
    return TableData(
        page_num=page_num,
        caption=caption,
        columns=columns,
        rows=data_rows,
    )


def _clean_cell(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _split_header(table, norm: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Return (column_names, data_rows), flattening header names."""
    header = getattr(table, "header", None)
    if header is not None and getattr(header, "names", None):
        names = [_clean_cell(n) or f"col_{i+1}" for i, n in enumerate(header.names)]
        # An external header is not part of the extracted grid rows.
        data = norm if getattr(header, "external", False) else norm[1:]
        return names, [r for r in data if any(r)]
    # No detected header: use the first row as column names.
    names = [c or f"col_{i+1}" for i, c in enumerate(norm[0])]
    return names, [r for r in norm[1:] if any(r)]


def _find_caption(page, table_rect: "pymupdf.Rect") -> str:
    """Find a 'Table N ...' caption immediately above or below the table."""
    candidates: list[tuple[float, str]] = []
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, btext = block[0], block[1], block[2], block[3], block[4]
        text = " ".join(btext.split())
        if not text or not text.lower().startswith(_CAPTION_PREFIXES):
            continue
        # Distance from the caption block to the table edge (above or below).
        if y1 <= table_rect.y0:
            dist = table_rect.y0 - y1
        elif y0 >= table_rect.y1:
            dist = y0 - table_rect.y1
        else:
            continue
        candidates.append((dist, text))
    if not candidates:
        return ""
    candidates.sort(key=lambda c: c[0])
    # Only accept a nearby caption (avoid grabbing an unrelated paragraph).
    dist, text = candidates[0]
    return text[:300] if dist < 80 else ""
