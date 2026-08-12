"""Tests for column-aware PDF extraction and the extraction quality gate."""

import pymupdf
from research_core.parsers.pdf import (
    PageText,
    _detect_fragmentation,
    _extract_page_columns,
    extract_pdf,
)


def _two_column_page(left: list[str], right: list[str]):
    """Build a page with two columns at x=40 / x=320, inserted interleaved."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    for i, (ln, rn) in enumerate(zip(left, right, strict=True)):
        y = 50 + i * 15
        page.insert_text((40, y), ln)
        page.insert_text((320, y), rn)
    return page


def test_two_columns_extract_left_then_right():
    left = ["left one", "left two", "left three", "left four"]
    right = ["right one", "right two", "right three", "right four"]
    page = _two_column_page(left, right)
    text = _extract_page_columns(page)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # reading order must be the whole left column, then the whole right column
    assert lines == left + right, lines


def test_two_columns_end_to_end_via_extract_pdf(tmp_path):
    page = _two_column_page(
        ["col A first", "col A second", "col A third", "col A fourth"],
        ["col B first", "col B second", "col B third", "col B fourth"],
    )
    path = tmp_path / "twocol.pdf"
    page.parent.save(path)
    parsed = extract_pdf(str(path))
    lines = [ln for ln in parsed.pages[0].text.splitlines() if ln.strip()]
    assert lines == ["col A first", "col A second", "col A third", "col A fourth",
                     "col B first", "col B second", "col B third", "col B fourth"]


def test_single_column_keeps_order():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    lines = ["alpha line one", "beta line two", "gamma line three"]
    for i, t in enumerate(lines):
        page.insert_text((40, 50 + i * 15), t)
    text = _extract_page_columns(page)
    out = [ln for ln in text.splitlines() if ln.strip()]
    assert out == lines, out


def test_scanned_pdf_flagged(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 200, 200))  # graphics only, no text
    path = tmp_path / "scan.pdf"
    doc.save(path)
    doc.close()
    parsed = extract_pdf(str(path))
    assert parsed.pages == []
    assert parsed.quality.scanned is True


def test_fragmentation_detected_when_pervasive():
    # one word per line on EVERY sampled page -> the broken-layout signal
    pages = [
        PageText(page_num=i, text="\n".join(["the", "quick", "brown", "fox", "jumps", "over"] * 6))
        for i in range(1, 5)
    ]
    assert _detect_fragmentation(pages) is True


def test_fragmentation_not_flagged_when_localized_to_tables():
    # only the last page looks like a table (short cells); body pages are fine
    body = PageText(page_num=1, text="A normal body line with several words.\n" * 40)
    table = PageText(page_num=2, text="\n".join(["Var", "Age", "Sex", "Class", "N"] * 30))
    assert _detect_fragmentation([body, table, body, body]) is False
