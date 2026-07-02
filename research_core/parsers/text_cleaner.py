"""Text cleaner for academic PDF extraction — removes journal boilerplate noise.

Design principle: blacklist > heuristic. Academic journal headers, footers,
and metadata lines are highly formulaic and publisher-specific. Regex rules
have near-zero false-positive risk because no paper's *content* contains lines
like "〔中图分类号〕TU984.2" or "A R T I C L E  I N F O".

Rules are organized by noise category and source (English / Chinese / universal).
Each rule is a (regex_pattern, category_label) tuple. Matching lines are removed.
Lines are matched independently — paragraph structure is preserved.

Usage:
    from research_core.parsers.text_cleaner import clean_text, CleaningReport
    cleaned_text, report = clean_text(raw_text)
    print(report.summary())
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


# ── Chunking version bump on strategy change ──────────────────────────
CLEANER_VERSION = "v1.0.0"


# ── Blacklist Rules ────────────────────────────────────────────────────

@dataclass
class CleaningRule:
    pattern: re.Pattern
    category: str
    description: str


def _build_rules() -> list[CleaningRule]:
    """Return the full blacklist of cleaning rules.

    Rules are ordered by specificity: more specific patterns first to avoid
    partial matches being consumed by broader patterns.
    """
    return [
        # ═══ English journal boilerplate ═══

        # Elsevier "ARTICLE INFO" block (spaced-out uppercase)
        CleaningRule(
            re.compile(r"^A\s+R\s+T\s+I\s+C\s+L\s+E\s+I\s+N\s+F\s+O\s*$"),
            "en_article_info",
            "Elsevier ARTICLE INFO header",
        ),
        # "ABSTRACT" in spaced-out uppercase
        CleaningRule(
            re.compile(r"^A\s+B\s+S\s+T\s+R\s+A\s+C\s+T\s*$"),
            "en_abstract_header",
            "ABSTRACT header in spaced caps",
        ),
        # Keywords header line
        CleaningRule(
            re.compile(r"^Keywords[:：]?\s*$", re.IGNORECASE),
            "en_keywords_header",
            "Keywords header line",
        ),
        # Elsevier competing interests boilerplate opener
        CleaningRule(
            re.compile(
                r"^(The authors declare that they have no known|"
                r"interests or personal relationships that could have appeared|"
                r"Declaration of [Cc]ompeting [Ii]nterest)",
            ),
            "en_disclaimer",
            "Competing interests / disclaimer boilerplate",
        ),
        # CRediT author statement line
        CleaningRule(
            re.compile(
                r"^(Conceptualization\.|Methodology\.|Software\.|Validation\.|"
                r"Formal analysis\.|Investigation\.|Resources\.|Data [Cc]uration\.|"
                r"Writing .*\.|Visualization\.|Supervision\.|Project administration\.|"
                r"Funding acquisition\.)\s",
            ),
            "en_credit_statement",
            "CRediT author contribution statement",
        ),
        # "Received: ... / Accepted: ... / Published: ..." meta line
        CleaningRule(
            re.compile(
                r"^(Received|Accepted|Published|Revised)[:\s].*\d{4}",
                re.IGNORECASE,
            ),
            "en_publication_dates",
            "Publication timeline metadata",
        ),

        # ═══ Chinese journal boilerplate ═══

        # Volume/issue header: "第30 卷第9 期" or "Vol．30，No．9"
        CleaningRule(
            re.compile(r"第\s*\d+\s*卷\s*第?\s*\d+\s*期"),
            "cn_volume_issue",
            "Chinese journal volume/issue header",
        ),
        CleaningRule(
            re.compile(
                r"Vol[\.\s．　，,]+\d+[,\.\s．　，]+"
                r"\s*No[\.\s．　]+\d+",
                re.IGNORECASE,
            ),
            "cn_volume_issue_en",
            "Volume/issue in English (incl. fullwidth punctuation)",
        ),
        # Journal name as standalone header line (e.g. "地理学报", "地球科学进展")
        # Only match when preceded/followed by other meta indicators in the same chunk
        # These are handled contextually in _filter_contextual()

        # Article number: "〔文章编号〕1002-2031(2023)04-0087-09"
        CleaningRule(
            re.compile(r"[〔\[]\s*文章编号\s*[〕\]]"),
            "cn_article_id",
            "Article number (文章编号)",
        ),
        # CLC number: "〔中图分类号〕TU984.2"
        CleaningRule(
            re.compile(r"[〔\[]\s*中图分类号\s*[〕\]][：:]?"),
            "cn_clc",
            "Chinese Library Classification (中图分类号)",
        ),
        # Document identifier code: "〔文献标识码〕A" or "文献标志码: A"
        CleaningRule(
            re.compile(r"[〔\[]\s*文献标识[码志]\s*[〕\]][：:]?\s*[A-Za-z]"),
            "cn_document_code",
            "Document identifier code (文献标识码)",
        ),
        CleaningRule(
            re.compile(r"文献标识[码志][：:]\s*[A-Za-z]"),
            "cn_document_code",
            "Document identifier code (alt format)",
        ),
        # Full-width DOI header: "〔ＤＯＩ〕..."
        # Only remove if the DOI is the primary content of the line
        CleaningRule(
            re.compile(
                r"[〔\[]\s*ＤＯＩ\s*[〕\]]\s*１０\.\d{1,5}/[^\s]{4,80}\s*$"
            ),
            "cn_doi_fullwidth",
            "DOI header in fullwidth characters (DOI-primary line)",
        ),
        # Bare bracket section headers: "〔摘　要〕", "〔关键词〕"
        # These are Chinese journal section markers with no content after them
        CleaningRule(
            re.compile(
                r"^[〔\[]\s*(?:摘\s*要|关键词|关键\s*词|"
                r"ABSTRACT?|Keywords?|KEY\s*WORDS?)\s*[〕\]]\s*$",
                re.IGNORECASE,
            ),
            "cn_section_bracket",
            "Bare bracket section header (摘要/关键词/Abstract)",
        ),
        # Empty bracket pairs: "〔〕" or "〔　〕" (whitespace-only brackets)
        CleaningRule(
            re.compile(r"[〔\[]\s*[〕\]]"),
            "cn_empty_bracket",
            "Empty/whitespace-only bracket pair",
        ),
        # Standard DOI line
        CleaningRule(
            re.compile(r"^DOI[：:]\s*10\.", re.IGNORECASE),
            "doi_line",
            "DOI metadata line",
        ),
        # ISSN/ISBN lines
        CleaningRule(
            re.compile(r"^(ISSN|ISBN)[：:]\s*\d", re.IGNORECASE),
            "issn_isbn",
            "ISSN/ISBN metadata line",
        ),

        # Received/revised dates
        CleaningRule(
            re.compile(r"[〔\[]\s*(修回|收稿|修订|投稿|录用|收到)日期\s*[〕\]]"),
            "cn_dates",
            "Date metadata in brackets",
        ),
        CleaningRule(
            re.compile(
                r"^(收稿|修回|修订|投稿|录用|收到)日期[：:].*\d{4}",
            ),
            "cn_dates",
            "Date metadata line",
        ),
        # Also match inline: "收稿日期：2021-06-30； 修回日期：2021-08-13."
        CleaningRule(
            re.compile(
                r"(收稿|修回|修订|录用)日期[：:]\s*\d{4}[-—]\d{2}[-—]\d{2}",
            ),
            "cn_dates_inline",
            "Date metadata inline",
        ),

        # Funding information
        CleaningRule(
            re.compile(r"[〔\[]?\s*(基金项目|基金资助)\s*[：:〕\]](?!\s*$)", re.IGNORECASE),
            "cn_funding",
            "Funding declaration (基金项目)",
        ),
        CleaningRule(
            re.compile(r"国家自然科学基金(项目|资助)?", re.IGNORECASE),
            "cn_funding_nsfc",
            "NSFC funding declaration",
        ),
        # Foundation line in English: "[Foundation: ...]"
        CleaningRule(
            re.compile(r"^\s*\[?\s*Foundation\s*[：:]\s*No\.", re.IGNORECASE),
            "en_funding",
            "Foundation acknowledgement line",
        ),

        # Author biography — bracket form: "〔作者简介〕..."
        CleaningRule(
            re.compile(r"[〔\[]\s*作者简介\s*[〕\]]"),
            "cn_author_bio",
            "Author biography (作者简介) in brackets",
        ),
        # Author biography — inline form: "作者简介：...1990—..."
        CleaningRule(
            re.compile(
                r"^(第一作者简介|作者简介|通讯作者|通信作者)\s*[：:].*\d{4}\s*[——\-]",

            ),
            "cn_author_bio",
            "Author biography inline with birth year",
        ),
        # Author biography — simpler form without year: "作者简介: 陈煜婷(1995-)，女"
        CleaningRule(
            re.compile(
                r"^(第一作者简介|作者简介)\s*[：:].*\(\d{4}[-—]\s*\)",
            ),
            "cn_author_bio",
            "Author biography inline with birth year in parens",
        ),
        CleaningRule(
            re.compile(r"^\*\s*(通讯|通信)作者[：:].*\d{4}"),
            "cn_corresponding",
            "Corresponding author (通信作者)",
        ),

        # Citation format
        CleaningRule(
            re.compile(r"[〔\[]\s*(引用格式|引文格式)\s*[〕\]]"),
            "cn_citation_format",
            "Citation format boilerplate",
        ),
        CleaningRule(
            re.compile(r"^引用格式[：:]"),
            "cn_citation_format",
            "Citation format line",
        ),

        # Thesis/dissertation cover info
        CleaningRule(
            re.compile(
                r"^(学生姓名|指导教师|专业学位类别|研究方向|"
                r"答辩委员会主席|授位时间|培养单位)[：:]",
            ),
            "cn_thesis_cover",
            "Chinese thesis cover metadata",
        ),
        CleaningRule(
            re.compile(r"(硕士|博士|专业)学位论文\s*$"),
            "cn_thesis_degree",
            "Thesis degree type line",
        ),
        CleaningRule(
            re.compile(r"^（专业学位）$"),
            "cn_thesis_degree_type",
            "Professional degree label",
        ),

        # ═══ Universal noise ═══

        # English journal running header: "Land 2023, 12, 629" or "Land 2023, 12, x of y"
        CleaningRule(
            re.compile(
                r"^[A-Z][a-z]+ \d{4}, \d{1,2}, (?:x\s*)?\d+(?:\s+of\s+\d+)?\s*$"
            ),
            "en_running_header",
            "Journal running header (e.g. 'Land 2023, 12, 629')",
        ),
        # "X of Y" standalone page counter
        CleaningRule(
            re.compile(r"^\d{1,2}\s+of\s+\d{1,2}\s*$"),
            "en_page_counter",
            "Page counter in running header",
        ),
        # Chinese editor credit: "责任编辑：申小菊" / "责任编辑　申小菊"
        CleaningRule(
            re.compile(r"责任编辑[：:\s　]"),
            "cn_editor_credit",
            "Editor credit (责任编辑)",
        ),
        # Page continuation markers: "（上接第75页）" / "（下转第117页）"
        CleaningRule(
            re.compile(r"[（(]\s*(上接|下转|续接)\s*第\s*\d+\s*页\s*[）)]"),
            "cn_page_continuation",
            "Page continuation marker (上接/下转第X页)",
        ),
        # Chinese fullwidth standalone page number: "７８" / "０９"
        # (fullwidth digits as standalone lines — common in Chinese journal running headers)
        CleaningRule(
            re.compile(r"^[０-９]{1,3}\s*$"),
            "page_number_cn_fullwidth",
            "Fullwidth digit page number",
        ),
        # "总第XXX期" — total issue number in Chinese journal headers
        CleaningRule(
            re.compile(r"总第\s*\d+\s*期"),
            "cn_total_issue",
            "Total issue number (总第X期)",
        ),

        # Standalone publication month/year: "2021 年9 月" / "Sep．2021" / "Mar., 2022" /
        # "November, 2019" (full month names, both fullwidth and ASCII dots)
        CleaningRule(
            re.compile(
                r"^\s*(?:\d{4}\s*年\s*\d{1,2}\s*月|"
                r"[A-Z][a-z]{2,8}[．\.]\s*\d{4}|"
                r"[A-Z][a-z]{2,8},\s*\d{4})\s*$"
            ),
            "pub_date_standalone",
            "Standalone publication date line",
        ),
        # Page range: "2260-2272页" / "506-519页第38卷第4期"
        CleaningRule(
            re.compile(r"^\d{1,6}\s*[-—]\s*\d{1,6}\s*页"),
            "cn_page_range",
            "Page range with 页 suffix",
        ),
        # Independent page numbers with CJK dots: "· ７８ ·"
        CleaningRule(
            re.compile(r"^·\s*\d{1,4}\s*·\s*$"),
            "page_number_cn_dot",
            "Chinese page number with dots",
        ),
        # Copyright footer
        CleaningRule(
            re.compile(r"^©\s*\d{4}", re.IGNORECASE),
            "copyright",
            "Copyright footer",
        ),
        CleaningRule(
            re.compile(r"^All rights reserved\.?\s*$", re.IGNORECASE),
            "copyright",
            "All rights reserved footer",
        ),
        # "Published by ..." line
        CleaningRule(
            re.compile(r"^Published (by|online)[:\s]", re.IGNORECASE),
            "published_by",
            "Published by / online metadata",
        ),
        # Download source
        CleaningRule(
            re.compile(r"^Downloaded (from|by)[:\s]", re.IGNORECASE),
            "downloaded_from",
            "Download source stamp",
        ),
        # URL-only line
        CleaningRule(
            re.compile(r"^https?://\S+\.\S+\s*$"),
            "url_line",
            "Standalone URL line",
        ),
        # Email-only line
        CleaningRule(
            re.compile(r"^\s*E[- ]?mail[：:]\s*\S+@\S+\s*$", re.IGNORECASE),
            "email_line",
            "Email address line",
        ),
        # "Key words:" variant
        CleaningRule(
            re.compile(r"^Key\s+[Ww]ords[:：]?\s*$"),
            "en_keywords_variant",
            "Key words header variant",
        ),
    ]


# ── Contextual filtering (multi-line awareness) ──────────────────────


def _is_page_number_candidate(line: str, neighbors: list[str]) -> bool:
    """A lone number is a page number ONLY if surrounded by other noise lines.

    Without context, a standalone number could be a data point, equation
    reference, or section number. We only remove it when neighboring lines
    are also noise.
    """
    s = line.strip()
    if not s:
        return False
    # Must be purely numeric (with optional whitespace padding)
    if not re.match(r"^\d{1,4}\s*$", s):
        return False
    # Check neighbors: at least one neighbor must be a known noise category
    for nb in neighbors:
        nb_s = nb.strip()
        if not nb_s:
            continue
        if (
            re.search(r"第\s*\d+\s*卷|Vol\.\d+|收稿日期|基金项目|作者简介|"
                      r"ARTICLE INFO|ABSTRACT|Keywords|Downloaded|Published",
                      nb_s, re.IGNORECASE)
        ):
            return True
    return False


# ── Public API ─────────────────────────────────────────────────────────


@dataclass
class CleaningReport:
    """Summary of what was cleaned from a text."""
    total_lines_in: int = 0
    total_lines_out: int = 0
    removed_by_category: Counter = field(default_factory=Counter)
    removed_samples: list[tuple[str, str]] = field(default_factory=list)  # (line, category)

    @property
    def total_removed(self) -> int:
        return self.total_lines_in - self.total_lines_out

    def summary(self) -> str:
        if self.total_removed == 0:
            return f"Cleaned: 0 lines removed ({self.total_lines_in} total)"
        cats = ", ".join(
            f"{cat}:{cnt}" for cat, cnt in self.removed_by_category.most_common(5)
        )
        return (
            f"Cleaned: {self.total_removed}/{self.total_lines_in} lines removed. "
            f"Top categories: {cats}"
        )


# Singleton rules — built once and reused
_rules: list[CleaningRule] | None = None


def get_rules() -> list[CleaningRule]:
    global _rules
    if _rules is None:
        _rules = _build_rules()
    return _rules


def clean_text(text: str) -> tuple[str, CleaningReport]:
    """Remove academic journal boilerplate from extracted PDF text.

    Args:
        text: Raw text extracted from a single PDF page or concatenated pages.

    Returns:
        (cleaned_text, CleaningReport) — cleaned text with noise lines removed,
        plus a report of what was cleaned.
    """
    rules = get_rules()
    lines = text.splitlines()
    report = CleaningReport(total_lines_in=len(lines))

    kept_lines: list[str] = []
    # Keep track for contextual checks
    buffer: list[str] = []  # last few kept lines for neighbor-aware rules

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Empty lines pass through (preserve paragraph breaks)
        if not stripped:
            kept_lines.append(line)
            buffer.append("")
            if len(buffer) > 3:
                buffer.pop(0)
            continue

        # Check against blacklist
        removed = False
        for rule in rules:
            if rule.pattern.search(stripped):
                report.removed_by_category[rule.category] += 1
                if len(report.removed_samples) < 10:
                    preview = stripped[:80] + ("..." if len(stripped) > 80 else "")
                    report.removed_samples.append((preview, rule.category))
                removed = True
                break

        if removed:
            continue

        # Contextual: standalone page numbers
        if _is_page_number_candidate(stripped, buffer):
            report.removed_by_category["page_number_contextual"] += 1
            if len(report.removed_samples) < 10:
                report.removed_samples.append((stripped, "page_number_contextual"))
            continue

        kept_lines.append(line)
        buffer.append(stripped)
        if len(buffer) > 3:
            buffer.pop(0)

    report.total_lines_out = len(kept_lines)

    # Preserve original line ending convention (or default to \n)
    result = "\n".join(kept_lines)

    # Post-clean: collapse 3+ consecutive blank lines to 2
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result, report


def clean_text_light(text: str) -> str:
    """Convenience: clean and return only the text, discarding the report."""
    cleaned, _ = clean_text(text)
    return cleaned
