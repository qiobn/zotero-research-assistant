"""Section structure detector — identifies chapter/section headings
in academic papers and classifies them by type.

Heuristic-based: scans chunk text for heading patterns (numbered or
named sections) and maps them to standard IMRaD section types.

Supports:
- English: "1. Introduction", "2.1 Study Area", "Methods", "Results and Discussion"
- Chinese: "一、引言", "（一）研究区域", "1 研究方法", "3. 结果与分析"
- Mixed: papers with both Chinese and English headings
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from research_core.parsers.chunker import Chunk

# ── Heading detection patterns ─────────────────────────────────────────

# H1 patterns (top-level sections)
# NOTE: all numbered patterns use [ \t]* (spaces/tabs only) between the
# number and heading, NOT \s* which would greedily consume \n and match
# body text on the next line as a heading.
_H1_PATTERNS = [
    # Numbered: "1. Introduction", "1  Introduction", "1、引言"
    re.compile(r"^\s*(\d+)[\. \t、．][ \t]*([A-Za-z一-鿿][^\n]{0,80})$", re.MULTILINE),
    # Chinese numbered: "一、引言", "二、研究方法"
    re.compile(r"^\s*([一二三四五六七八九十]+)[、．][ \t]*([^\n]{0,80})$", re.MULTILINE),
    # Roman numerals: "I. Introduction", "II. Methods", "IV. Results"
    # I{1,3} (not I{0,3}) to prevent matching empty string
    re.compile(
        r"^\s*(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{1,3}))"
        r"[\. \t、][ \t]*([A-Za-z][^\n]{0,80})$",
        re.MULTILINE,
    ),
    # Chapter prefix: "CHAPTER 1: Introduction", "Chapter 2. Methods"
    re.compile(
        r"^\s*(?:CHAPTER|Chapter|Ch\.)[ \t]*(\d+)[\. \t:、][ \t]*([A-Za-z][^\n]{0,80})$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # Section symbol: "§1. Introduction", "§2 Methods"
    re.compile(r"^\s*§[ \t]*(\d+)[\. \t、][ \t]*([A-Za-z][^\n]{0,80})$", re.MULTILINE),
    # Bare section keywords (EN)
    re.compile(
        r"^\s*(Introduction|Methods?|Methodology|Results?|Discussion|"
        r"Conclusion|References|Bibliography|Appendix|Appendices|"
        r"Acknowledgments?|Supplementary|Supporting\s+Information)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # Bare section keywords (CN)
    re.compile(
        r"^\s*(引言|绪论|研究方法|方法|结果|结果与分析|结果与讨论|讨论|"
        r"结论|总结|参考文献|附录|致谢|摘要)\s*$",
        re.MULTILINE,
    ),
]

# H2/H3 patterns (sub-sections)
_H2_PATTERNS = [
    # "1.1 Study Area", "2.3.1 Data Collection"
    re.compile(r"^\s*(\d+\.\d+(?:\.\d+)?)[\. \t、][ \t]*([^\n]{0,80})$", re.MULTILINE),
    # "（一）研究区域", "（1）样本选择"
    re.compile(r"^\s*[（(][一二三四五六七八九十\d]+[）)][ \t]*([^\n]{0,80})$", re.MULTILINE),
    # "① 数据来源"
    re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩][ \t]*([^\n]{0,80})$", re.MULTILINE),
    # Letter subsections: "A. Study Area", "B. Data Sources"
    re.compile(r"^\s*([A-F])[\. \t、][ \t]*([A-Za-z][^\n]{0,80})$", re.MULTILINE),
]

# ── Fallback: number-only headings (no heading text after number) ──
# Catches patterns like "1.", "2.3", "一、", "（三）" when there's
# no descriptive heading text following the number. Used when all
# standard patterns fail to match anything.
_FALLBACK_NUMBER_PATTERNS = [
    # "1.", "3.2", "2.3.1" — bare numbered heading
    re.compile(r"^\s*(\d+(?:\.\d+)*)[\.\s、．]?\s*$", re.MULTILINE),
    # "一、" — bare Chinese numbered heading
    re.compile(r"^\s*([一二三四五六七八九十]+)[、．]?\s*$", re.MULTILINE),
    # "（二）", "（3）" — bare parenthetical numbered heading
    re.compile(r"^\s*[（(]([一二三四五六七八九十\d]+)[）)]\s*$", re.MULTILINE),
    # "I.", "IV." — bare Roman numeral heading (must be non-empty)
    re.compile(
        r"^\s*(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{1,3}))"
        r"\s*[\.\s、]?\s*$",
        re.MULTILINE,
    ),
]

# Section type keyword mapping (lowercase for case-insensitive matching)
_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("abstract", ["abstract", "摘要", "a b s t r a c t"]),
    ("introduction", ["introduction", "intro", "引言", "绪论", "背景", "background",
                       "问题提出", "研究背景", "研究意义", "问题的提出",
                       "theoretical framework", "理论框架", "conceptual framework",
                       "概念框架", "研究假设", "hypothesis", "hypotheses",
                       "theoretical background", "理论背景", "理论基础"]),
    ("literature_review", ["literature review", "related work", "文献综述", "研究综述",
                            "前人研究", "文献回顾", "related literature",
                            "institutional background", "制度背景", "政策背景",
                            "literature", "已有研究", "国内外研究"]),
    ("methods", ["method", "methods", "methodology", "研究方法", "方法", "实验设计",
                  "数据", "data", "study area", "研究区域", "研究区", "研究区概况",
                  "材料", "materials", "数据来源", "样本", "sample",
                  "empirical strategy", "empirical framework", "empirical approach",
                  "实证策略", "实证框架", "模型设定", "模型构建", "计量模型",
                  "变量定义", "变量选择", "变量说明", "identification",
                  "estimation strategy", "estimation", "估计方法",
                  "研究设计", "research design", "case study", "案例分析",
                  "survey", "问卷", "调查设计", "measurement", "测量"]),
    ("results", ["result", "results", "finding", "findings", "结果", "结果与分析",
                  "结果与讨论", "实证结果", "分析结果", "analysis",
                  "empirical results", "实证分析", "回归结果", "估计结果",
                  "descriptive statistics", "描述性统计", "基准回归",
                  "baseline results", "main results", "主要结果",
                  "robustness checks", "robustness", "稳健性检验", "稳健性",
                  "内生性", "endogeneity", "异质性", "heterogeneity",
                  "机制分析", "mechanism", "进一步分析"]),
    ("discussion", ["discussion", "讨论", "discussions", "结果讨论", "综合讨论",
                     "policy implications", "policy recommendations",
                     "政策建议", "政策启示", "政策含义", "讨论与启示",
                     "进一步讨论", "implications", "建议", "对策",
                     "研究启示", "管理启示", "实践启示"]),
    ("conclusion", ["conclusion", "conclusions", "结论", "总结", "concluding",
                     "研究结论", "主要结论", "总结与展望", "summary",
                     "结语", "结束语", "concluding remarks", "研究不足",
                     "limitations", "研究局限", "未来研究", "future research"]),
    ("references", ["reference", "references", "bibliography", "参考文献",
                     "引用文献", "文献", "references cited", "注释"]),
    ("appendix", ["appendix", "appendices", "附录", "附表", "附图",
                   "supplementary", "supporting information", "附件"]),
    ("acknowledgments", ["acknowledgment", "acknowledgements", "acknowledgement",
                          "致谢", "感谢", "基金项目", "资助"]),
]


@dataclass
class DetectedSection:
    heading: str
    section_type: str
    level: int                  # 1=top, 2=sub, 3=sub-sub
    chunk_start_idx: int        # first chunk belonging to this section
    chunk_end_idx: int          # last chunk (inclusive)
    page_start: int
    page_end: int
    parent_idx: int | None      # index into the sections list (for hierarchy)


@dataclass
class SectionDetectionResult:
    sections: list[DetectedSection] = field(default_factory=list)
    front_matter_end_chunk: int = 0  # chunks before first real section


def _classify_section_type(heading: str) -> str:
    """Map a heading string to a section type using keyword matching."""
    heading_lower = heading.lower().strip()
    for sec_type, keywords in _TYPE_KEYWORDS:
        for kw in keywords:
            if kw in heading_lower:
                return sec_type
    return "unknown"


def _extract_level_and_heading(
    text: str,
    patterns: list[re.Pattern],
) -> tuple[int, str] | None:
    """Try to match a heading pattern. Returns (level, heading_text) or None."""
    for pat in patterns:
        m = pat.search(text)
        if m:
            # Level: count dots in number for numbered patterns
            groups = m.groups()
            if len(groups) >= 2:
                # Numbered: "1.2.3 Heading"
                num = groups[0]
                level = min(3, num.count(".") + 1)
                heading = groups[1].strip()
            else:
                # Bare keyword
                heading = groups[0].strip()
                level = 1
            return (level, heading)
    return None


def detect_sections(chunks: list[Chunk]) -> SectionDetectionResult:
    """Scan chunks for section headings and return a structured section map.

    Each chunk is checked for heading patterns. When a heading is found,
    a new section begins. Chunks between headings are assigned to the
    preceding section.

    The first chunk(s) before any heading are treated as front matter
    (title, authors, abstract — not a real section).

    Quality-aware: boilerplate and noisy chunks are skipped for heading
    detection to avoid false positives from journal headers/footers.
    """
    result = SectionDetectionResult()
    if not chunks:
        return result

    # Phase 1: Identify heading positions
    heading_hits: list[tuple[int, int, str, str]] = []
    # (chunk_idx, level, heading_text, section_type)

    def _is_valid_heading(text: str, level: int) -> bool:
        """Reject false positives: too long, too short, or looks like prose."""
        stripped = text.strip()
        if len(stripped) < 3 or len(stripped) > 100:
            return False
        # Must contain at least one letter or CJK character
        has_alpha = any(c.isalpha() or ("一" <= c <= "鿿") for c in stripped)
        if not has_alpha:
            return False
        # Reject lines that are mostly numbers/punctuation
        alpha_ratio = sum(1 for c in stripped if c.isalpha() or "一" <= c <= "鿿") / len(stripped)
        if alpha_ratio < 0.3:
            return False
        return True

    for i, chunk in enumerate(chunks):
        # Skip boilerplate/noisy chunks for heading detection
        if chunk.quality_flag == "incomplete":
            continue

        text = chunk.text

        # Only match headings at the START of a chunk (or very near it).
        # Headings that appear mid-paragraph are false positives.
        search_text = text[:200] if len(text) > 200 else text

        # Try H1 patterns first
        hit = _extract_level_and_heading(search_text, _H1_PATTERNS)
        if hit:
            level, heading = hit
            if _is_valid_heading(heading, level):
                heading_hits.append((i, level, heading, _classify_section_type(heading)))
                continue

        # Try H2/H3 patterns
        hit = _extract_level_and_heading(search_text, _H2_PATTERNS)
        if hit:
            level, heading = hit
            if _is_valid_heading(heading, level):
                heading_hits.append((i, level, heading, _classify_section_type(heading)))

    if not heading_hits:
        # Phase 1 fallback: number-only patterns (bare "1.", "2.3", "一、")
        # Uses relaxed validation — numbers alone are short but valid headings
        for i, chunk in enumerate(chunks):
            if chunk.quality_flag == "incomplete":
                continue
            search_text = chunk.text[:200] if len(chunk.text) > 200 else chunk.text
            for pat in _FALLBACK_NUMBER_PATTERNS:
                m = pat.search(search_text)
                if m:
                    num = m.group(1).strip()
                    if num and 1 <= len(num) <= 20:
                        heading_hits.append((i, 1, num, "unknown"))
                        break

    if not heading_hits:
        # Phase 2 fallback: truly no headings — one "unknown" section
        result.sections.append(DetectedSection(
            heading="",
            section_type="unknown",
            level=1,
            chunk_start_idx=0,
            chunk_end_idx=len(chunks) - 1,
            page_start=chunks[0].page_start if chunks else 0,
            page_end=chunks[-1].page_end if chunks else 0,
            parent_idx=None,
        ))
        return result

    # Phase 2: Build sections with chunk ranges
    sections: list[DetectedSection] = []
    parent_stack: list[int] = []  # indices into sections list, by level

    for j, (chunk_idx, level, heading, sec_type) in enumerate(heading_hits):
        # Determine parent
        parent_idx = None
        while parent_stack and sections[parent_stack[-1]].level >= level:
            parent_stack.pop()
        if parent_stack:
            parent_idx = parent_stack[-1]

        # Previous section ends at chunk_idx - 1
        if sections:
            sections[-1].chunk_end_idx = chunk_idx - 1

        # New section starts at chunk_idx
        section = DetectedSection(
            heading=heading,
            section_type=sec_type,
            level=level,
            chunk_start_idx=chunk_idx,
            chunk_end_idx=chunk_idx,  # temporary, will be set by next heading
            page_start=chunks[chunk_idx].page_start,
            page_end=chunks[chunk_idx].page_end,
            parent_idx=parent_idx,
        )
        sections.append(section)
        parent_stack.append(len(sections) - 1)

    # Last section extends to end of chunks
    if sections:
        sections[-1].chunk_end_idx = len(chunks) - 1
        sections[-1].page_end = chunks[-1].page_end

    # Front matter: chunks before first heading
    first_heading_chunk = heading_hits[0][0]
    if first_heading_chunk > 0:
        result.front_matter_end_chunk = first_heading_chunk - 1

    result.sections = sections
    return result


def build_section_map(
    chunks: list[Chunk],
) -> tuple[list[DetectedSection], dict[int, int]]:
    """Convenience: detect sections and return (sections, chunk_to_section_map).

    chunk_to_section_map: {chunk_idx: section_index_in_sections_list}
    """
    result = detect_sections(chunks)
    chunk_map: dict[int, int] = {}
    for si, sec in enumerate(result.sections):
        for ci in range(sec.chunk_start_idx, sec.chunk_end_idx + 1):
            chunk_map[ci] = si
    return result.sections, chunk_map
