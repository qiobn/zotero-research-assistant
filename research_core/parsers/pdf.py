"""PDF text extraction with per-page tracking.

We extract prose text only. Tables and figures are handled downstream by the
chunker as lightweight caption-anchored records (see ``chunker.py``): we record
where they are and roughly what they contain, but we do NOT structure table
cells. Reliable table structuring is a vision problem — geometric/line-based
detection produces garbage on borderless "three-line" academic tables and even
mis-segments multi-column prose. Users who need true table structuring can
preprocess with a visual parser (docling / open-parse / unstructured); see docs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf


@dataclass
class PageText:
    page_num: int
    text: str


@dataclass
class ParsedPDF:
    pages: list[PageText] = field(default_factory=list)
    # Retained for backward compatibility; always empty (tables are derived from
    # page text by the chunker, not structured here).
    tables: list = field(default_factory=list)


def extract_pdf_text(path: str) -> list[PageText]:
    """Backward-compatible helper: return prose pages only."""
    return extract_pdf(path).pages


def extract_pdf(path: str) -> ParsedPDF:
    """Extract per-page prose text from a PDF.

    Pure text extraction via PyMuPDF; no OCR (scanned PDFs yield empty pages).
    Tables/figures are not structured here — the chunker turns their captions
    into reference records.
    """
    pages: list[PageText] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                pages.append(PageText(page_num=i + 1, text=text))
    return ParsedPDF(pages=pages)
