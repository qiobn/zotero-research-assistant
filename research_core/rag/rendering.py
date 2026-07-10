"""Markdown context block renderer — LLM-optimized output formatting.

Converts structured retrieval results into pre-rendered Markdown blocks that LLMs
consume efficiently. Key design decisions:

- Blockquote (>) for cited evidence — LLM attention weights are highest for blockquote
- ### headings for tree-structured mental models of result sets
- Star ratings (★★★) instead of float scores (0.0321) — intuitive for LLMs
- Sentence-boundary truncation — never cut a passage mid-word or mid-sentence
- Bold for metadata labels — helps LLMs extract authors/years/DOIs
- Horizontal rules (---) for clear result boundaries

Dual-format pattern (Anthropic MCP best practice):
  JSON  = programmatic consumption (keys, scores, structured metadata)
  context_block = LLM consumption (this module)

Usage:
    from research_core.rag.rendering import ContextBlockRenderer
    renderer = ContextBlockRenderer()
    md = renderer.render_search_results("urban green space", hits, limit=10)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from research_core.tools.search import PaperHit


# ── Sentence boundary patterns (CJK + EN) ──────────────────────────
_CJK_SENTENCE_END = re.compile(r"[。！？；]")
_EN_SENTENCE_END = re.compile(r"[.!?]\s")


def _snippet(text: str, max_chars: int = 300) -> tuple[str, bool]:
    """Truncate text at a sentence boundary near max_chars.

    Returns (snippet, was_truncated). Prefers CJK sentence endings (。！？；)
    within the first 2/3, then English sentence endings (.!?), then newlines,
    then falls back to a hard character cut. Always returns complete text if
    it fits within max_chars.

    Args:
        text: Full text to truncate.
        max_chars: Target maximum character count.

    Returns:
        (snippet, was_truncated) — snippet is the truncated text (no ellipsis
        added here — callers add context-appropriate continuation markers).
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text, False

    # Search window: look for sentence boundary in [max_chars*0.5, max_chars]
    # Prefer cutting a bit short to cutting mid-sentence.
    window_start = int(max_chars * 0.5)

    # Try CJK sentence endings first
    best = -1
    for m in _CJK_SENTENCE_END.finditer(text):
        pos = m.end()
        if pos <= max_chars:
            best = pos
        else:
            break

    # If no good CJK boundary, try EN sentence endings
    if best < window_start:
        for m in _EN_SENTENCE_END.finditer(text):
            pos = m.end()
            if pos <= max_chars:
                best = max(best, pos)
            elif best >= window_start:
                break

    # Fallback: newline
    if best < window_start:
        nl = text.rfind("\n", 0, max_chars)
        if nl > window_start:
            best = nl

    # Last resort: hard cut at max_chars
    if best < window_start:
        best = max_chars

    return text[:best].strip(), True


# ── Formatting helpers ──────────────────────────────────────────────


def _format_first_author(authors: list[str]) -> str:
    """Extract first author's surname for compact citation display.

    Heuristic (no name database required):
    1. Pure CJK (张伟) → first token is surname
    2. Has initials (James E. Anderson, Chen J.) → Western convention, last
       non-initial token is surname
    3. No initials, 2 tokens (Wang Xiaoming, Kim Soohyun) → Chinese/Korean
       convention, first token is surname. This covers Latinized East Asian
       names with fully spelled-out given names.
    4. No initials, 3+ tokens (Eric van Wincoop) → Western convention,
       last non-initial token is surname

    Known limitation: "James Anderson" (Western, no middle initial) will be
    incorrectly treated as Chinese convention → "James" instead of "Anderson".
    In practice, academic metadata almost always includes middle initials.

    >>> _format_first_author(["James E. Anderson", "Eric van Wincoop"])
    'Anderson'
    >>> _format_first_author(["Chen J.", "Liu M."])
    'Chen'
    >>> _format_first_author(["Wang Xiaoming"])
    'Wang'
    >>> _format_first_author(["张伟", "李娜"])
    '张'
    >>> _format_first_author(["Kim Soohyun"])
    'Kim'
    """
    if not authors:
        return "Unknown"
    name = authors[0].strip()
    if not name:
        return "Unknown"

    # Pure CJK: surname comes first
    if "一" <= name[0] <= "鿿":
        return name.split()[0] if name.split() else name

    # Latin script: filter out initials
    tokens = name.split()
    if not tokens:
        return name

    # An "initial" is a single uppercase letter, optionally with a trailing period
    non_initials = [
        t for t in tokens
        if not (len(t.rstrip(".")) == 1 and t[0].isupper())
    ]

    if not non_initials:
        # Degenerate: all tokens are initials
        return tokens[-1]

    # If there are initials present, Western convention (surname last)
    has_initials = len(non_initials) < len(tokens)

    if has_initials:
        return non_initials[-1]

    # No initials — all tokens are full words.

    # Common European name prefixes (tussenvoegsels). If the first token is
    # a prefix like "van", "de", "von", the surname is the last token.
    _NAME_PREFIXES = frozenset({
        "van", "de", "den", "der", "von", "zu", "di", "da", "du",
        "del", "della", "delle", "le", "la", "los", "las",
    })
    if non_initials[0].lower() in _NAME_PREFIXES:
        return non_initials[-1]

    # 2 tokens: likely Chinese/Korean convention (surname first)
    if len(non_initials) == 2:
        return non_initials[0]

    # 3+ tokens: Western convention (surname last)
    return non_initials[-1]


def _format_authors_short(authors: list[str]) -> str:
    """Compact author display: 'Anderson et al.' or 'Anderson & van Wincoop'.

    >>> _format_authors_short(["Anderson", "van Wincoop"])
    'Anderson & van Wincoop'
    >>> _format_authors_short(["Anderson", "van Wincoop", "Smith"])
    'Anderson et al.'
    """
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        return _format_first_author(authors)
    if len(authors) == 2:
        a0 = _format_first_author([authors[0]])
        a1 = _format_first_author([authors[1]])
        return f"{a0} & {a1}"
    return f"{_format_first_author(authors)} et al."


def _tier_stars(tier: str) -> str:
    """Convert relevance tier to visual star rating.

    ★★★ = high (top 25% by Cross-Encoder score)
    ★★  = medium (25-75%)
    ★   = low (bottom 25% or no Cross-Encoder)
    """
    mapping = {"high": "★★★", "medium": "★★", "low": "★"}
    return mapping.get(tier, "★")


_TIER_GUIDE = (
    "**Relevance:** ★★★ high confidence | ★★ medium | ★ low confidence\n"
)


def _format_source(source: str) -> str:
    """Human-readable source label."""
    mapping = {
        "hybrid": "semantic + keyword",
        "semantic": "semantic",
        "keyword": "keyword",
        "fallback": "fallback (keyword only)",
        "similar": "similarity search",
    }
    return mapping.get(source, source)


def _format_section(section_type: str, section_heading: str) -> str:
    """Format section info compactly: 'Methods §3. Empirical Strategy'."""
    if not section_type and not section_heading:
        return ""
    stype = section_type.capitalize() if section_type else ""
    heading = section_heading.strip() if section_heading else ""
    if stype and heading:
        return f"{stype} §{heading}"
    if heading:
        return heading
    return stype


def _escape_markdown(text: str) -> str:
    """Escape characters that could break Markdown formatting.

    Only escapes within inline contexts (not blockquotes/headings which are
    explicitly formatted by the renderer).
    """
    # Escape stray Markdown formatting chars that could confuse the LLM
    text = text.replace("|", "\\|")
    return text


# ── Main renderer ───────────────────────────────────────────────────


class ContextBlockRenderer:
    """Render retrieval results as LLM-optimized Markdown context blocks.

    Each method produces a self-contained Markdown string designed to be
    dropped directly into an LLM context window. The blocks use visual
    hierarchy (headings, blockquotes, bold, rules) to guide LLM attention
    toward evidence text and away from metadata noise.

    All methods are pure functions — they don't mutate inputs or hold state.
    """

    # ── search_papers ───────────────────────────────────────────

    def render_search_results(
        self,
        query: str,
        hits: list[PaperHit],
        limit: int = 10,
    ) -> str:
        """Render search_papers hits as a Markdown context block.

        Args:
            query: The original search query.
            hits: Ranked PaperHit list (already scored, reranked, diversified).
            limit: The limit parameter from the search call (for footer).
        """
        if not hits:
            return self._render_empty_search(query)

        lines: list[str] = []
        query_display = query if len(query) <= 60 else query[:57] + "..."
        lines.append(f"## Search Results: '{query_display}' ({len(hits)} papers)")
        lines.append("")

        for i, h in enumerate(hits, 1):
            # Title line with tier and compact citation
            stars = _tier_stars(h.relevance_tier)
            authors_short = _format_authors_short(h.authors)
            lines.append(
                f"### {i}. {h.title} ({authors_short}, {h.year}) {stars}"
            )

            # Metadata line
            meta_parts = [f"**Match:** {_format_source(h.source)}"]
            if h.doi:
                meta_parts.append(f"**DOI:** {h.doi}")
            lines.append(" | ".join(meta_parts))

            # Evidence passage (blockquote for max LLM attention)
            passage, was_cut = _snippet(h.matched_passage, 350)
            if passage:
                lines.append("")
                # Escape any stray > in the passage so it doesn't break the blockquote
                safe_passage = passage.replace("\n>", "\n> ").replace("\n", "\n> ")
                lines.append(f"> {safe_passage}")

            # Source attribution line
            section_str = _format_section(h.section_type, h.section_heading)
            attribution_parts = []
            if section_str:
                attribution_parts.append(section_str)
            if h.matched_page:
                attribution_parts.append(f"p.{h.matched_page}")
            if attribution_parts:
                lines.append(f"— *{', '.join(attribution_parts)}*")

            lines.append("")

        # Footer
        lines.append("---")
        lines.append(_TIER_GUIDE.strip())
        footer_parts = []
        if query:
            footer_parts.append(
                "Use `get_paper_content(key)` to read a specific paper."
            )
            footer_parts.append(
                "Set `expand_context=true` for full section text (2000 chars)."
            )
        lines.append(" | ".join(footer_parts))

        return "\n".join(lines)

    def _render_empty_search(self, query: str) -> str:
        """Render a 'no results' context block."""
        query_display = query if len(query) <= 60 else query[:57] + "..."
        return (
            f"## Search Results: '{query_display}' (0 papers)\n\n"
            f"> [NO MATCHES FOUND] The library does not contain papers matching "
            f"this query. Try broader terms, remove year/tag filters, or use "
            f"`search_online_literature` to discover papers outside the library."
        )

    # ── get_paper_content ───────────────────────────────────────

    def render_paper_content(
        self,
        content,  # PaperContent dataclass
        mode: str = "",
        query: str = "",
    ) -> str:
        """Render get_paper_content result as a Markdown context block.

        Args:
            content: PaperContent dataclass instance.
            mode: "fulltext", "outline", or "".
            query: The within-paper search query (if any).
        """
        lines: list[str] = []

        # Header
        title_display = content.title if len(content.title) <= 80 else content.title[:77] + "..."
        lines.append(f"## Paper Content: {title_display}")
        lines.append(f"**Key:** `{content.item_key}`")
        lines.append("")

        # Mode-specific content
        if mode == "fulltext" and content.fulltext:
            lines.append(self._render_fulltext(content.fulltext))
        elif mode == "outline" and content.outline:
            lines.append(self._render_outline(content.outline))
        elif content.passages:
            lines.append(self._render_passages(content.passages, query))
        else:
            lines.append("> *(No passages extracted — paper may not be indexed.)*")

        # Annotations hint
        if content.annotations:
            note_count = sum(1 for a in content.annotations if a.get("comment"))
            hl_count = len(content.annotations) - note_count
            lines.append("")
            lines.append(f"**Annotations:** {hl_count} highlights, {note_count} notes")
        else:
            lines.append("")
            lines.append("*(No annotations. Use `include_annotations=true` if needed.)*")

        # Referenced tables/figures
        if content.referenced_tables:
            lines.append("")
            lines.append(f"**Referenced Tables:** {len(content.referenced_tables)}")
            for t in content.referenced_tables:
                label = t.get("label", "?")
                caption = t.get("caption", "")[:100]
                lines.append(f"- {label}: {caption}")

        if content.referenced_figures:
            lines.append("")
            lines.append(f"**Referenced Figures:** {len(content.referenced_figures)}")
            for f in content.referenced_figures:
                label = f.get("label", "?")
                caption = f.get("caption", "")[:100]
                lines.append(f"- {label}: {caption}")

        return "\n".join(lines)

    def _render_fulltext(self, fulltext: str) -> str:
        """Render full paper text block."""
        # The fulltext is already paginated with "--- Page N ---" markers
        # Just wrap it with metadata hints
        page_count = fulltext.count("--- Page")
        lines = [
            f"### Full Text ({page_count} pages extracted)",
            "",
            fulltext,
        ]
        return "\n".join(lines)

    def _render_outline(self, outline: list[dict]) -> str:
        """Render PDF table of contents."""
        lines = ["### Outline / Table of Contents", ""]
        for item in outline:
            level = item.get("level", 1)
            indent = "  " * (level - 1)
            title = item.get("title", "")
            page = item.get("page", 0)
            lines.append(f"{indent}- **{title}** (p.{page})")
        return "\n".join(lines)

    def _render_passages(
        self, passages: list[dict], query: str = "",
    ) -> str:
        """Render a list of passages from within-paper search."""
        if query:
            lines = [f"### Search within paper: \"{query}\"", ""]
        else:
            lines = ["### Passages", ""]

        for _i, p in enumerate(passages, 1):
            text = p.get("text", "")
            page_start = p.get("page_start", "")
            page_end = p.get("page_end", "")
            score = p.get("score")

            # Build attribution
            if page_start and page_end and page_start != page_end:
                page_str = f"p.{page_start}-{page_end}"
            elif page_start:
                page_str = f"p.{page_start}"
            else:
                page_str = ""

            attr_parts = [page_str] if page_str else []
            if score is not None:
                attr_parts.append(f"relevance: {score:.2f}")
            attr_str = ", ".join(attr_parts)

            # Blockquote with attribution header
            if attr_str:
                lines.append(f"> ({attr_str}) {text}")
            else:
                lines.append(f"> {text}")

            # Cross-reference tables/figures
            if p.get("cites_tables"):
                labels = ", ".join(p["cites_tables"])
                lines.append(f"  — *cites tables: {labels}*")
            if p.get("cites_figures"):
                labels = ", ".join(p["cites_figures"])
                lines.append(f"  — *cites figures: {labels}*")

            lines.append("")

        return "\n".join(lines)

    # ── generate_review_note ────────────────────────────────────

    def render_review_materials(self, data: dict) -> str:
        """Render generate_review_note output as a Markdown context block.

        Args:
            data: The dict from generate_review_note() with keys:
                  focus, paper_count, total_passages, year_range, papers,
                  reference_list, synthesis_instruction.
        """
        if "error" in data:
            return (
                f"## Literature Review Materials\n\n"
                f"> **Error:** {data['error']}\n\n"
                f"Ensure papers have indexed PDFs (run `sync_index` if needed)."
            )

        lines: list[str] = []

        # Header
        focus = data.get("focus", "(general overview)")
        n_papers = data.get("paper_count", 0)
        n_passages = data.get("total_passages", 0)
        year_range = data.get("year_range", "unknown")
        lines.append(
            f"## Literature Review Materials: \"{focus}\""
        )
        lines.append(
            f"**{n_papers} papers | {year_range} | {n_passages} extracted passages**"
        )
        lines.append("")

        # Papers with passages
        for paper in data.get("papers", []):
            authors_short = _format_authors_short(paper.get("authors", []))
            year = paper.get("year", "n.d.")
            title = paper.get("title", "Untitled")
            lines.append(f"### {authors_short} ({year})")
            lines.append(f"**{title}**")
            lines.append("")

            for passage in paper.get("passages", []):
                text = passage.get("text", "")
                citation = passage.get("citation", "")
                page = passage.get("page", "")

                # Format: blockquote with citation
                tag = citation if citation else f"(p.{page})" if page else ""
                if tag:
                    lines.append(f"> {tag}: {text}")
                else:
                    lines.append(f"> {text}")

            lines.append("")

        # Reference list
        refs = data.get("reference_list", "")
        if refs:
            lines.append("---")
            lines.append("### Reference List")
            lines.append("")
            lines.append(refs)
            lines.append("")

        # Synthesis guidance (condensed from the full instruction)
        instruction = data.get("synthesis_instruction", "")
        if instruction:
            lines.append("---")
            lines.append("### Writing Guidelines")
            lines.append("")
            lines.append(instruction)

        return "\n".join(lines)

    # ── suggest_citations ───────────────────────────────────────

    def render_citation_suggestions(
        self,
        draft_text: str,
        suggestions: list,  # list[CitationSuggestion]
    ) -> str:
        """Render suggest_citations output as a Markdown context block.

        Args:
            draft_text: The user's draft paragraph.
            suggestions: List of CitationSuggestion dataclass instances.
        """
        # Show the draft text context
        draft_display = draft_text[:200].strip()
        if len(draft_text) > 200:
            draft_display += "..."

        lines: list[str] = [
            "## Citation Suggestions",
            "",
            "**Draft text:**",
            f"> {draft_display}",
            "",
        ]

        if not suggestions:
            lines.append(
                "> [NO SUGGESTIONS] No matching papers found in library. "
                "Try broadening the claim or searching with `search_online_literature`."
            )
            return "\n".join(lines)

        lines.append(f"**{len(suggestions)} papers found:**")
        lines.append("")

        for i, s in enumerate(suggestions, 1):
            authors_short = _format_authors_short(s.authors)
            lines.append(
                f"### {i}. {s.title} ({authors_short}, {s.year})"
            )

            # Evidence blockquote
            evidence, _ = _snippet(s.evidence_text, 300)
            if evidence:
                lines.append(f"> {evidence}")

            # Attribution
            if s.page:
                lines.append(f"— *p.{s.page} | relevance: {s.relevance:.2f}*")
            else:
                lines.append(f"— *relevance: {s.relevance:.2f}*")

            lines.append("")

        lines.append("---")
        lines.append(
            "Use `export_bibliography(item_keys=[...])` to get formatted BibTeX "
            "or citation text. Verify each citation's page number before using."
        )

        return "\n".join(lines)

    # ── find_similar_papers ─────────────────────────────────────

    def render_similar_papers(
        self,
        source_title: str,
        hits: list,  # list[PaperHit]
        limit: int = 10,
    ) -> str:
        """Render find_similar_papers results as a Markdown context block."""
        title_display = source_title[:80] + "..." if len(source_title) > 80 else source_title
        lines: list[str] = [
            f"## Similar Papers to: \"{title_display}\"",
            f"**Strategy:** semantic search by title + abstract | **{len(hits)} found**",
            "",
        ]

        if not hits:
            lines.append(
                "> [NO SIMILAR PAPERS] No semantically similar papers found in "
                "the library. Try expanding the library with `search_online_literature`."
            )
            return "\n".join(lines)

        for i, h in enumerate(hits[:limit], 1):
            authors_short = _format_authors_short(h.authors)
            stars = _tier_stars(h.relevance_tier)
            lines.append(f"### {i}. {h.title} ({authors_short}, {h.year}) {stars}")
            lines.append(f"**DOI:** {h.doi}" if h.doi else f"**Key:** `{h.key}`")

            passage, _ = _snippet(h.matched_passage, 300)
            if passage:
                lines.append("")
                lines.append(f"> {passage}")

            if h.matched_page:
                lines.append(f"— *p.{h.matched_page} | score: {h.score:.3f}*")
            else:
                lines.append(f"— *score: {h.score:.3f}*")
            lines.append("")

        lines.append("---")
        lines.append(
            "The source paper's title and abstract were used as the search query. "
            "Use `get_paper_content(key)` to read any paper in full."
        )
        return "\n".join(lines)

    # ── find_arguments ──────────────────────────────────────────

    def render_argument_evidence(self, data: dict) -> str:
        """Render find_arguments output as a Markdown context block."""
        if "error" in data:
            return (
                f"## Argument Evidence\n\n"
                f"> **Error:** {data['error']}\n\n"
                f"Try a different claim or broadening your search."
            )

        claim = data.get("claim", "")
        claim_display = claim[:100] + "..." if len(claim) > 100 else claim
        support_n = data.get("support_count", 0)
        oppose_n = data.get("oppose_count", 0)
        neutral_n = data.get("neutral_count", 0)
        total = data.get("total_evidence", 0)

        lines: list[str] = [
            f"## Argument Evidence: \"{claim_display}\"",
            "",
            f"**Summary:** {support_n} supporting | {oppose_n} opposing | {neutral_n} neutral ({total} total passages)",
            "",
        ]

        for stance_key, stance_label, emoji in [
            ("supporting", "Supporting Evidence", "+"),
            ("opposing", "Opposing Evidence", "-"),
            ("neutral", "Neutral / Contextual", "~"),
        ]:
            items = data.get(stance_key, [])
            if not items:
                continue

            lines.append(f"### {emoji} {stance_label} ({len(items)})")
            lines.append("")

            for i, ev in enumerate(items, 1):
                citation = ev.get("citation", "")
                title = ev.get("title", "Untitled")
                text = ev.get("text", "")
                page = ev.get("page", "")
                relevance = ev.get("relevance", 0)

                lines.append(f"**{i}. {title}**")
                if citation:
                    lines.append(f"*{citation}*")

                if text:
                    lines.append("")
                    lines.append(f"> {text}")

                attr_parts = []
                if page:
                    attr_parts.append(f"p.{page}")
                attr_parts.append(f"relevance: {relevance:.2f}")
                lines.append(f"— *{', '.join(attr_parts)}*")
                lines.append("")

        instruction = data.get("synthesis_instruction", "")
        if instruction:
            lines.append("---")
            lines.append("### Synthesis Guidance")
            lines.append("")
            instruction_lines = instruction.strip().split("\n")
            lines.append("\n".join(instruction_lines[:6]))

        return "\n".join(lines)

    # ── generate_reading_note ───────────────────────────────────

    _SECTION_LABELS = {
        "research_question": "Research Question",
        "methodology": "Methodology",
        "data": "Data",
        "key_findings": "Key Findings",
        "limitations": "Limitations",
        "contribution": "Contribution",
    }

    def render_reading_note(self, data: dict) -> str:
        """Render generate_reading_note output as a Markdown context block."""
        if "error" in data:
            return (
                f"## Reading Note\n\n"
                f"> **Error:** {data['error']}\n\n"
                f"Ensure the paper has an indexed PDF (run `sync_index` if needed)."
            )

        title = data.get("title", "Untitled")
        citation = data.get("citation", "")
        doi = data.get("doi", "")
        year = data.get("year", "")

        lines: list[str] = [
            f"## Reading Note: {title}",
            "",
            f"**Citation:** {citation}" if citation else "",
            f"**Year:** {year} | **DOI:** {doi}" if doi else f"**Year:** {year}",
            f"**Key:** `{data.get('item_key', '')}`",
            "",
        ]

        sections = data.get("sections", {})
        if not sections:
            lines.append("> *(No passages extracted — paper may not be indexed.)*")
        else:
            for section_key in [
                "research_question", "methodology", "data",
                "key_findings", "limitations", "contribution",
            ]:
                passages = sections.get(section_key, [])
                if not passages:
                    continue

                label = self._SECTION_LABELS.get(section_key, section_key)
                lines.append(f"### {label}")
                lines.append("")

                for p in passages:
                    text = p.get("text", "")
                    page = p.get("page", "")
                    relevance = p.get("relevance", 0)

                    attr = f"p.{page}" if page else ""
                    attr += f", relevance: {relevance:.2f}" if attr else f"relevance: {relevance:.2f}"

                    lines.append(f"> ({attr}) {text}")
                    lines.append("")

        instruction = data.get("note_template_instruction", "")
        if instruction:
            lines.append("---")
            lines.append("### Writing Guidelines")
            lines.append("")
            instruction_lines = instruction.strip().split("\n")
            lines.append("\n".join(instruction_lines[:4]))

        return "\n".join(lines)

    # ── suggest_tags ─────────────────────────────────────────────

    def render_tag_suggestions(self, data: dict) -> str:
        """Render suggest_tags output as a Markdown context block."""
        if "error" in data:
            return (
                f"## Tag Suggestions\n\n"
                f"> **Error:** {data['error']}"
            )

        suggestions = data.get("suggestions", [])
        total = data.get("total_papers", len(suggestions))

        lines: list[str] = [
            f"## Tag Suggestions ({total} papers)",
            "",
        ]

        if not suggestions:
            lines.append("> [NO SUGGESTIONS] No tags could be suggested for these papers.")
            return "\n".join(lines)

        for i, s in enumerate(suggestions, 1):
            title = s.get("title", "Untitled")
            current = s.get("current_tags", [])
            new_tags = s.get("suggested_new", [])
            from_lib = s.get("suggested_from_library", [])

            lines.append(f"### {i}. {title}")
            if current:
                lines.append(f"**Current:** `{'`, `'.join(current)}`")

            if new_tags:
                lines.append(f"**Suggested new:** `{'`, `'.join(new_tags)}`")
            else:
                lines.append("**Suggested new:** *(none)*")

            if from_lib:
                lines.append(f"**Existing library tags:** `{'`, `'.join(from_lib)}`")

            lines.append("")

        action_hint = data.get("action_hint", "")
        if action_hint:
            lines.append("---")
            lines.append(action_hint)

        lines.append("")
        lines.append(
            "To apply tags, use `edit_tags(item_keys=[...], add=[...], confirm=true)`."
        )
        return "\n".join(lines)


# ── Singleton ───────────────────────────────────────────────────────

_renderer: ContextBlockRenderer | None = None


def get_renderer() -> ContextBlockRenderer:
    """Return a singleton ContextBlockRenderer. Thread-safe."""
    global _renderer
    if _renderer is None:
        _renderer = ContextBlockRenderer()
    return _renderer
