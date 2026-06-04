"""Generate structured literature review notes from multiple papers.

Given a set of paper keys and an optional focus topic, extracts relevant passages
from each paper's PDF (via the vector index), groups them thematically, and produces
a structured Markdown review with proper citations (page numbers included).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from research_core.rag.retriever import RetrievalResult, Retriever
from research_core.zotero.client import ZoteroClient


@dataclass
class ReviewNote:
    """Structured review output."""

    title: str
    markdown: str
    papers_used: list[dict] = field(default_factory=list)
    total_passages: int = 0


def _gather_evidence(
    item_keys: list[str],
    retriever: Retriever,
    zot: ZoteroClient,
    focus: str,
    passages_per_paper: int,
) -> list[dict]:
    """Collect relevant passages from each paper."""
    evidence: list[dict] = []

    for key in item_keys:
        try:
            item = zot.get_item(key)
        except Exception:
            continue

        if focus:
            chunks = retriever.search_within_item(key, focus, n_results=passages_per_paper)
        else:
            chunks = retriever.get_item_chunks(key)[:passages_per_paper]

        for chunk in chunks:
            evidence.append({
                "item_key": key,
                "title": item.title,
                "authors": item.authors,
                "year": ZoteroClient.parse_year(item.date),
                "text": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "score": chunk.score,
            })

    return evidence


def _format_citation(paper: dict) -> str:
    """Format a short inline citation like (Author, Year, p.X)."""
    authors = paper.get("authors", [])
    first_author = authors[0].split()[-1] if authors else "Unknown"
    year = paper.get("year", "n.d.")
    page = paper.get("page_start", "")
    if len(authors) > 2:
        return f"({first_author} et al., {year}, p.{page})"
    elif len(authors) == 2:
        second = authors[1].split()[-1]
        return f"({first_author} & {second}, {year}, p.{page})"
    return f"({first_author}, {year}, p.{page})"


def generate_review_note(
    *,
    item_keys: list[str],
    retriever: Retriever,
    zot: ZoteroClient,
    focus: str = "",
    passages_per_paper: int = 5,
    include_evidence: bool = True,
) -> dict:
    """Generate a structured review note from multiple papers.

    This tool extracts relevant passages from each paper via the vector index,
    then organizes them into a structured format that the LLM can use to write
    a cohesive literature review. The output provides raw evidence with citations
    that the AI should synthesize into a narrative.

    Args:
        item_keys: List of Zotero item keys to include in the review.
        retriever: Vector store retriever.
        zot: Zotero client.
        focus: Optional topic/question to focus the review on. If empty,
            extracts the most important passages from each paper.
        passages_per_paper: Max passages to extract per paper (default 5).
        include_evidence: Whether to include raw passage text in output.

    Returns:
        Dict with structured evidence grouped by paper, ready for AI synthesis.
    """
    if not item_keys:
        return {"error": "No item_keys provided."}

    # Gather evidence from all papers
    evidence = _gather_evidence(item_keys, retriever, zot, focus, passages_per_paper)

    if not evidence:
        return {
            "error": "[MATERIAL GAP] No indexed content found for the given papers. "
                     "Run sync_index first, or check that these papers have PDFs attached.",
            "item_keys": item_keys,
        }

    # Group by paper
    papers_map: dict[str, dict] = {}
    for e in evidence:
        key = e["item_key"]
        if key not in papers_map:
            papers_map[key] = {
                "item_key": key,
                "title": e["title"],
                "authors": e["authors"],
                "year": e["year"],
                "passages": [],
            }
        passage_entry = {
            "text": e["text"] if include_evidence else "(text omitted)",
            "page": e["page_start"],
            "citation": _format_citation(e),
        }
        papers_map[key]["passages"].append(passage_entry)

    papers_list = sorted(papers_map.values(), key=lambda p: p["year"] or 0)

    # Build summary metadata
    all_years = [p["year"] for p in papers_list if p["year"]]
    year_range = f"{min(all_years)}–{max(all_years)}" if all_years else "unknown"

    return {
        "focus": focus or "(general overview)",
        "paper_count": len(papers_list),
        "total_passages": len(evidence),
        "year_range": year_range,
        "papers": papers_list,
        "synthesis_instruction": (
            "WRITING GUIDELINES FOR ACADEMIC LITERATURE REVIEW:\n"
            "1. STRUCTURE BY THEME, NOT BY PAPER — Never write 'A studied X, B studied Y'. "
            "Instead, identify cross-cutting themes (e.g., methodological evolution, "
            "theoretical tensions, empirical consensus) and weave multiple sources into each theme.\n"
            "2. TRACE INTELLECTUAL LINEAGE — Show how ideas evolved over time: "
            "who built on whom, where the field shifted direction, what triggered new approaches.\n"
            "3. SYNTHESIZE, DON'T SUMMARIZE — Highlight agreements, contradictions, "
            "and unresolved debates across papers. Compare findings rather than listing them.\n"
            "4. IDENTIFY GAPS — Explicitly state what remains unaddressed, what methodological "
            "limitations persist, and where future research is needed.\n"
            "5. USE CITATIONS AS EVIDENCE — Integrate citations naturally within analytical "
            "sentences (e.g., 'This shift toward mixed-methods approaches (Smith, 2020, p.5; "
            "Lee et al., 2022, p.12) reflects a broader disciplinary trend...').\n"
            "6. MAINTAIN CRITICAL VOICE — Evaluate the strength of evidence, note sample "
            "size limitations, geographic biases, or theoretical blind spots."
        ),
    }
