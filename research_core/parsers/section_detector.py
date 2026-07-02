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
from collections import defaultdict
from dataclasses import dataclass, field

from research_core.parsers.chunker import Chunk


# ── Heading detection patterns ─────────────────────────────────────────

# H1 patterns (top-level sections)
_H1_PATTERNS = [
    # Numbered: "1. Introduction", "1  Introduction", "1、引言"
    re.compile(r"^\s*(\d+)[\.\s、．]\s*([A-Za-z一-鿿][^\n]{0,80})$", re.MULTILINE),
    # Chinese numbered: "一、引言", "二、研究方法"
    re.compile(r"^\s*([一二三四五六七八九十]+)[、．]\s*([^\n]{0,80})$", re.MULTILINE),
    # Bare section keywords on their own line: "Introduction\n", "Methods\n"
    re.compile(
        r"^\s*(Introduction|Methods?|Methodology|Results?|Discussion|"
        r"Conclusion|References|Bibliography|Appendix|Appendices|"
        r"Acknowledgments?|Supplementary|Supporting\s+Information)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # Chinese bare section keywords
    re.compile(
        r"^\s*(引言|绪论|研究方法|方法|结果|结果与分析|结果与讨论|讨论|"
        r"结论|总结|参考文献|附录|致谢|摘要)\s*$",
        re.MULTILINE,
    ),
]

# H2/H3 patterns (sub-sections)
_H2_PATTERNS = [
    # "1.1 Study Area", "2.3.1 Data Collection"
    re.compile(r"^\s*(\d+\.\d+(?:\.\d+)?)[\.\s、]\s*([^\n]{0,80})$", re.MULTILINE),
    # "（一）研究区域", "（1）样本选择"
    re.compile(r"^\s*[（(][一二三四五六七八九十\d]+[）)]\s*([^\n]{0,80})$", re.MULTILINE),
    # "① 数据来源"
    re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*([^\n]{0,80})$", re.MULTILINE),
]

# Section type keyword mapping (lowercase for case-insensitive matching)
_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("abstract", ["abstract", "摘要", "abstract", "a b s t r a c t"]),
    ("introduction", ["introduction", "intro", "引言", "绪论", "背景", "background",
                       "问题提出", "研究背景"]),
    ("literature_review", ["literature review", "related work", "文献综述", "研究综述",
                            "前人研究", "文献回顾", "related literature"]),
    ("methods", ["method", "methods", "methodology", "研究方法", "方法", "实验设计",
                  "数据", "data", "study area", "研究区域", "研究区", "研究区概况",
                  "材料", "materials", "数据来源", "样本", "sample"]),
    ("results", ["result", "results", "finding", "findings", "结果", "结果与分析",
                  "结果与讨论", "实证结果", "分析结果", "analysis"]),
    ("discussion", ["discussion", "讨论", "discussions", "结果讨论", "综合讨论",
                     "政策建议", "政策启示", "implications", "建议"]),
    ("conclusion", ["conclusion", "conclusions", "结论", "总结", "concluding",
                     "研究结论", "主要结论", "总结与展望", "summary"]),
    ("references", ["reference", "references", "bibliography", "参考文献",
                     "引用文献", "文献", "references cited"]),
    ("appendix", ["appendix", "appendices", "附录", "附表", "附图",
                   "supplementary", "supporting information"]),
    ("acknowledgments", ["acknowledgment", "acknowledgements", "acknowledgement",
                          "致谢", "感谢"]),
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
        if chunk.quality_flag in ("boilerplate", "incomplete"):
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
        # No headings found — assign all chunks to one "unknown" section
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
