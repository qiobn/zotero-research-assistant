"""Structured reading note generator.

Extracts key components from a paper (research question, methodology, data,
findings, limitations) using the vector index, then assembles them into a
structured note template that can be saved to Zotero.
"""

from __future__ import annotations

from research_core.rag.retriever import Retriever
from research_core.zotero.client import ZoteroClient

# Section queries — what to search for in the paper to extract each component
_SECTION_QUERIES = {
    "research_question": "research question objective aim purpose of this study",
    "methodology": "methodology method approach technique framework model used",
    "data": "data source dataset sample participants study area collected",
    "key_findings": "results findings show demonstrate reveal indicate conclude",
    "limitations": "limitation weakness shortcoming future research gap constraint",
    "contribution": "contribution novelty significance implication innovation",
}


def generate_reading_note(
    *,
    item_key: str,
    retriever: Retriever,
    zot: ZoteroClient,
    sections: list[str] | None = None,
    passages_per_section: int = 2,
) -> dict:
    """Generate a structured reading note for a single paper.

    Extracts key academic components by querying the paper's indexed content
    for each section type. Returns a structured template with extracted evidence
    that the AI can refine into a polished note.

    Args:
        item_key: Zotero item key of the paper to analyze.
        retriever: Vector store retriever.
        zot: Zotero client.
        sections: Which sections to extract. Defaults to all:
            ["research_question", "methodology", "data", "key_findings",
             "limitations", "contribution"].
        passages_per_section: Max passages to extract per section (default 2).

    Returns:
        Dict with paper metadata and extracted sections ready for note creation.
    """
    # Get paper metadata
    try:
        item = zot.get_item(item_key)
    except Exception as e:
        return {"error": f"Could not find paper with key '{item_key}': {e}"}

    # Determine which sections to extract
    target_sections = sections if sections else list(_SECTION_QUERIES.keys())
    valid_sections = [s for s in target_sections if s in _SECTION_QUERIES]

    if not valid_sections:
        return {"error": f"Invalid sections. Valid options: {list(_SECTION_QUERIES.keys())}"}

    # Extract passages for each section
    extracted: dict[str, list[dict]] = {}
    has_content = False

    for section in valid_sections:
        query = _SECTION_QUERIES[section]
        chunks = retriever.search_within_item(item_key, query, n_results=passages_per_section)

        passages = []
        for chunk in chunks:
            if chunk.score and chunk.score > 0.2:
                passages.append({
                    "text": chunk.text[:400],
                    "page": chunk.page_start,
                    "relevance": round(chunk.score, 3),
                })

        extracted[section] = passages
        if passages:
            has_content = True

    if not has_content:
        return {
            "error": "[MATERIAL GAP] No indexed content found for this paper. "
                     "Ensure it has a PDF attached and run sync_index.",
            "item_key": item_key,
        }

    # Format citation
    authors = item.authors
    year = ZoteroClient.parse_year(item.date)
    first_author = authors[0].split()[-1] if authors else "Unknown"

    return {
        "item_key": item_key,
        "title": item.title,
        "authors": authors,
        "year": year,
        "doi": item.doi,
        "citation": f"{first_author} et al. ({year})" if len(authors) > 2
                    else f"{first_author} ({year})",
        "sections": extracted,
        "note_template_instruction": (
            "READING NOTE GUIDELINES:\n"
            "Based on the extracted passages above, create a structured reading note. "
            "For each section:\n"
            "1. Research Question: State the core RQ in one sentence.\n"
            "2. Methodology: Summarize the approach (qualitative/quantitative/mixed, "
            "specific technique, framework used).\n"
            "3. Data: What data was used, from where, what sample size.\n"
            "4. Key Findings: 2-3 bullet points of main results.\n"
            "5. Limitations: What the authors acknowledge or what you identify.\n"
            "6. Contribution: What's novel about this paper.\n\n"
            "Keep each section to 1-3 sentences. Use the page numbers for reference. "
            "If a section's passages are irrelevant, write 'Not clearly stated in paper.' "
            "The note should be useful for quick recall months later."
        ),
    }
