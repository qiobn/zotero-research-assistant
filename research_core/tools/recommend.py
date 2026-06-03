"""Personalized paper recommendations based on recent reading activity.

Identifies the user's active research focus from recently read/annotated papers,
then finds related literature via OpenAlex Related Works and Semantic Scholar.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from loguru import logger

from research_core.sources.models import ExternalPaper, OnlinePaperHit
from research_core.tools.reading_status import _parse_date
from research_core.zotero.client import ZoteroClient

# Union type for paper results from different sources
PaperResult = ExternalPaper | OnlinePaperHit


def _extract_focus_papers(
    zot: ZoteroClient,
    days: int = 60,
    max_seeds: int = 5,
) -> list[dict]:
    """Identify recently active papers ranked by engagement (annotations > notes > modified)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    raw_items = zot._zot.items(
        itemType="-attachment || note",
        sort="dateModified",
        direction="desc",
        limit=100,
    )

    scored: list[tuple[float, dict]] = []

    for raw in raw_items:
        data = raw.get("data", raw)
        key = data.get("key", "")
        title = data.get("title", "")
        date_modified = data.get("dateModified", "")
        if not key or not title:
            continue

        mod_dt = _parse_date(date_modified)
        if mod_dt is None or mod_dt < cutoff:
            continue

        annotation_count = 0
        note_count = 0
        try:
            children = zot._zot.children(key)
            for ch in children:
                ch_data = ch.get("data", ch)
                if ch_data.get("itemType") == "note":
                    note_count += 1
                elif ch_data.get("contentType") == "application/pdf":
                    ch_key = ch_data.get("key", "")
                    try:
                        anns = zot._zot.children(ch_key)
                        annotation_count += sum(
                            1 for a in anns
                            if a.get("data", a).get("itemType") == "annotation"
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        # Engagement score: annotations weighted most, then notes, then recency
        days_ago = (now - mod_dt).days
        recency_bonus = max(0, (days - days_ago) / days)
        score = annotation_count * 3 + note_count * 2 + recency_bonus

        if score > 0:
            tags = [t.get("tag", "") for t in data.get("tags", [])]
            scored.append((score, {
                "key": key,
                "title": title,
                "doi": data.get("DOI", ""),
                "tags": tags,
                "score": score,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:max_seeds]]


def _get_recommendations_for_paper(paper: dict, limit: int = 10) -> list[PaperResult]:
    """Get recommendations for a single paper using available APIs."""
    hits: list[PaperResult] = []
    doi = paper.get("doi", "")

    # Strategy 1: OpenAlex Related Works
    if doi:
        try:
            from research_core.sources.openalex import get_related_works, resolve_openalex_id

            oa_id = resolve_openalex_id(doi=doi)
            if oa_id:
                related = get_related_works(oa_id, limit=limit)
                hits.extend(related)
        except Exception as e:
            logger.debug(f"OpenAlex related works failed for {doi}: {e}")

    # Strategy 2: S2 Recommendations (DOIs need "DOI:" prefix for S2 API)
    if doi:
        try:
            from research_core.sources.semantic_scholar import get_s2_recommendations

            s2_hits = get_s2_recommendations(paper_ids=[f"DOI:{doi}"], limit=limit)
            hits.extend(s2_hits)
        except Exception as e:
            logger.debug(f"S2 recommendations failed for {doi}: {e}")

    return hits


def recommend_papers(
    *,
    zot: ZoteroClient,
    days: int = 60,
    max_seeds: int = 5,
    limit: int = 15,
) -> dict:
    """Generate personalized recommendations based on recent reading activity.

    Algorithm:
    1. Identify top-N most engaged papers (by annotations, notes, recency)
    2. For each, query OpenAlex Related Works + S2 Recommendations in parallel
    3. Merge, deduplicate, rank by frequency across seeds, exclude already-in-library

    Args:
        zot: Zotero client.
        days: Look-back window for identifying active papers (default 60 days).
        max_seeds: Max seed papers to base recommendations on.
        limit: Max recommendations to return.

    Returns:
        Dict with seed_papers, recommendations, and focus_topics.
    """
    # Step 1: Identify focus papers
    seeds = _extract_focus_papers(zot, days=days, max_seeds=max_seeds)

    if not seeds:
        return {
            "seed_papers": [],
            "recommendations": [],
            "focus_topics": [],
            "message": "[MATERIAL GAP] No recently active papers found. "
                       "Read and annotate papers in Zotero to enable personalized recommendations.",
        }

    # Step 2: Get recommendations for each seed in parallel
    all_hits: list[PaperResult] = []

    with ThreadPoolExecutor(max_workers=min(len(seeds), 4)) as executor:
        futures = {
            executor.submit(_get_recommendations_for_paper, seed, limit): seed
            for seed in seeds
        }
        for future in as_completed(futures, timeout=45):
            try:
                hits = future.result()
                all_hits.extend(hits)
            except Exception as e:
                logger.debug(f"Recommendation task failed: {e}")

    # Step 3: Deduplicate and rank by appearance frequency
    doi_map: dict[str, PaperResult] = {}
    doi_count: Counter[str] = Counter()

    for hit in all_hits:
        identifier = hit.doi.lower().strip() if hit.doi else hit.title.lower().strip()[:80]
        if not identifier:
            continue
        if identifier not in doi_map:
            doi_map[identifier] = hit
        doi_count[identifier] += 1

    # Step 4: Exclude papers already in library
    library_dois: set[str] = set()
    library_titles: set[str] = set()
    try:
        lib_items = zot._zot.items(itemType="-attachment || note", limit=200)
        for raw in lib_items:
            data = raw.get("data", raw)
            d = data.get("DOI", "").lower().strip()
            if d:
                library_dois.add(d)
            t = data.get("title", "").lower().strip()[:80]
            if t:
                library_titles.add(t)
    except Exception:
        pass

    # Step 5: Build final ranked list
    ranked = []
    for identifier, count in doi_count.most_common():
        hit = doi_map[identifier]
        doi_lower = hit.doi.lower().strip() if hit.doi else ""
        title_lower = hit.title.lower().strip()[:80] if hit.title else ""

        if doi_lower in library_dois or title_lower in library_titles:
            continue

        # Handle both ExternalPaper and OnlinePaperHit
        source_url = ""
        sources: list[str] = []
        if isinstance(hit, OnlinePaperHit):
            source_url = hit.source_url
            sources = hit.sources
        elif isinstance(hit, ExternalPaper):
            source_url = f"https://doi.org/{hit.doi}" if hit.doi else ""
            sources = [hit.source] if hit.source else []

        ranked.append({
            "title": hit.title,
            "authors": hit.authors,
            "year": hit.year,
            "doi": hit.doi,
            "citation_count": hit.citation_count,
            "source_url": source_url,
            "relevance_score": count,
            "sources": sources,
        })

        if len(ranked) >= limit:
            break

    # Step 6: Extract focus topics from seed tags
    all_tags: Counter[str] = Counter()
    for seed in seeds:
        for tag in seed.get("tags", []):
            if not tag.startswith("_"):
                all_tags[tag] += 1

    return {
        "seed_papers": [
            {"key": s["key"], "title": s["title"], "engagement_score": s["score"]}
            for s in seeds
        ],
        "recommendations": ranked,
        "recommendation_count": len(ranked),
        "focus_topics": [tag for tag, _ in all_tags.most_common(10)],
        "lookback_days": days,
    }
