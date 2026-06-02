"""CNKI paper detail + navigate pages tools."""

from __future__ import annotations

from research_core.sources.cnki.browser import cnki_page
from research_core.sources.cnki.detail import extract_paper_detail


def cnki_paper_detail(cnki_url: str) -> dict:
    """Extract full metadata from a CNKI paper's detail page.

    Navigates to the paper URL and extracts DOI, abstract, keywords,
    authors, affiliations, fund info, and the export_id for Zotero import.

    If a DOI is found, the paper can be added to Zotero via add_paper(doi).
    If no DOI, use cnki_add_to_zotero(export_ids=[export_id]) instead.
    """
    if not cnki_url.strip():
        return {"error": "cnki_url is required"}

    with cnki_page() as page:
        detail = extract_paper_detail(page, cnki_url=cnki_url.strip())

    return {
        "title": detail.title,
        "authors": detail.authors,
        "affiliations": detail.affiliations,
        "abstract": detail.abstract,
        "keywords": detail.keywords,
        "fund": detail.fund,
        "journal": detail.journal,
        "pub_info": detail.pub_info,
        "doi": detail.doi,
        "issn": detail.issn,
        "export_id": detail.export_id,
        "citation_info": detail.citation_info,
        "page_url": detail.page_url,
        "has_doi": bool(detail.doi),
        "zotero_hint": (
            f"Use add_paper(identifier='{detail.doi}') to import via DOI."
            if detail.doi
            else f"Use cnki_add_to_zotero(export_ids=['{detail.export_id}']) to import directly."
        ),
    }
