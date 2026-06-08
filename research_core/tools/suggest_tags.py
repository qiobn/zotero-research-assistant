"""Suggest tags for papers based on metadata analysis.

Analyzes title, abstract, keywords, and existing library tag patterns to
recommend relevant tags. Does NOT apply tags — only suggests. User must
confirm via edit_tags before any changes are made.
"""

from __future__ import annotations

from loguru import logger

from research_core.zotero.client import ZoteroClient


def _extract_candidate_tags_from_metadata(
    title: str,
    abstract: str,
    existing_tags: list[str],
) -> list[str]:
    """Extract candidate tags from paper metadata using simple heuristics."""
    candidates: list[str] = []

    text = f"{title} {abstract}".lower()

    # Methodology indicators
    method_tags = {
        "machine learning": ["machine learning", "deep learning", "neural network", "random forest", "SVM"],
        "qualitative": ["interview", "qualitative", "ethnograph", "grounded theory", "thematic analysis"],
        "quantitative": ["regression", "statistical", "quantitative", "survey", "questionnaire"],
        "simulation": ["simulation", "agent-based", "monte carlo", "ABM", "cellular automata"],
        "GIS": ["GIS", "spatial analysis", "geograph", "remote sensing", "land use"],
        "meta-analysis": ["meta-analysis", "systematic review", "meta analysis"],
        "case study": ["case study", "case-study"],
        "mixed methods": ["mixed method"],
        "experiment": ["experiment", "randomized", "RCT", "controlled trial"],
        "network analysis": ["network analysis", "social network", "graph theory"],
    }
    for tag, keywords in method_tags.items():
        for kw in keywords:
            if kw.lower() in text:
                candidates.append(f"method:{tag}")
                break

    # Research domain indicators
    domain_tags = {
        "urban planning": ["urban planning", "urban design", "city planning", "zoning"],
        "transportation": ["transport", "mobility", "traffic", "commut"],
        "public health": ["health", "epidemiol", "disease", "medical", "clinical"],
        "environment": ["environment", "climate", "pollution", "sustainab", "ecology"],
        "education": ["education", "learning", "student", "teaching", "curriculum"],
        "economics": ["economic", "market", "price", "GDP", "fiscal"],
        "sociology": ["social", "community", "inequality", "gender", "poverty"],
        "computer science": ["algorithm", "software", "computing", "NLP", "computer vision"],
        "public service": ["public service", "public facilit", "amenity", "accessibility"],
        "aging": ["aging", "ageing", "elderly", "older adult", "gerontol"],
    }
    for tag, keywords in domain_tags.items():
        for kw in keywords:
            if kw.lower() in text:
                candidates.append(f"domain:{tag}")
                break

    # Data type indicators
    data_tags = {
        "survey data": ["survey", "questionnaire", "likert"],
        "spatial data": ["GIS", "shapefile", "raster", "POI", "OpenStreetMap"],
        "census data": ["census", "demographic data", "population data"],
        "social media": ["twitter", "weibo", "social media", "online review"],
        "satellite imagery": ["remote sensing", "satellite", "Landsat", "Sentinel"],
    }
    for tag, keywords in data_tags.items():
        for kw in keywords:
            if kw.lower() in text:
                candidates.append(f"data:{tag}")
                break

    return list(dict.fromkeys(candidates))


def _match_library_tags(
    candidates: list[str],
    library_tags: list[str],
    title: str,
    abstract: str,
) -> list[str]:
    """Find existing library tags that match the paper's content."""
    text = f"{title} {abstract}".lower()
    matched: list[str] = []

    for tag in library_tags:
        if tag.startswith("_"):
            continue
        tag_lower = tag.lower()
        # Match if the tag text appears in paper content
        if len(tag_lower) >= 3 and tag_lower in text:
            matched.append(tag)

    return matched


def suggest_tags(
    *,
    item_keys: list[str],
    zot: ZoteroClient,
) -> dict:
    """Suggest tags for one or more papers based on metadata analysis.

    Returns suggestions only — does NOT modify any papers. User should review
    the suggestions and use edit_tags with confirm=true to apply chosen tags.

    Args:
        item_keys: Papers to analyze.
        zot: Zotero client.

    Returns:
        Dict with per-paper tag suggestions and library-wide tag matches.
    """
    if not item_keys:
        return {"error": "No item_keys provided."}

    # Get existing library tags for matching
    try:
        library_tags = zot.get_tags()
    except Exception:
        library_tags = []

    suggestions: list[dict] = []

    for key in item_keys[:20]:
        try:
            item = zot.get_item(key)
        except Exception as e:
            logger.debug(f"Failed to get item {key}: {e}")
            continue

        current_tags = item.tags

        # Generate candidates from metadata
        new_candidates = _extract_candidate_tags_from_metadata(
            title=item.title,
            abstract=item.abstract,
            existing_tags=current_tags,
        )

        # Match against existing library tags
        library_matches = _match_library_tags(
            new_candidates,
            library_tags,
            title=item.title,
            abstract=item.abstract,
        )

        # Remove tags the paper already has
        current_lower = {t.lower() for t in current_tags}
        new_candidates = [t for t in new_candidates if t.lower() not in current_lower]
        library_matches = [t for t in library_matches if t.lower() not in current_lower]

        suggestions.append({
            "item_key": key,
            "title": item.title,
            "current_tags": current_tags,
            "suggested_new": new_candidates,
            "suggested_from_library": library_matches,
        })

    return {
        "suggestions": suggestions,
        "total_papers": len(suggestions),
        "action_hint": (
            "Review the suggestions above. To apply tags, use edit_tags with "
            "the desired item_keys and tag names. Tags are NOT applied automatically."
        ),
    }
