"""Citation network expansion — find papers via forward/backward citations.

Extracted from server.py to keep transport-layer thin and enable reuse
across MCP, Agent, and CLI interfaces.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from research_core.sources.openalex import (
    get_cited_by,
    get_references,
    resolve_openalex_id,
)


def expand_citation_network(
    *,
    dois: list[str] | None = None,
    doi: str = "",
    title: str = "",
    fields_of_study: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 30,
) -> dict:
    """Explore citation neighborhood: papers that cite or are cited by seed paper(s).

    Useful when keyword search fails for niche topics. If the paper has no DOI,
    use DOIs of its key references as seeds.

    Args:
        dois: List of seed DOIs (preferred for multi-seed expansion).
        doi: Single seed DOI.
        title: Paper title (fallback if no DOI).
        fields_of_study: Discipline filter.
        year_from/year_to: Publication year window.
        limit: Max total results (default 30).

    Returns:
        Dict with citing_papers, referenced_papers, counts, and source URLs.
    """
    seed_dois = list(dois) if dois else []
    if not seed_dois and doi:
        seed_dois = [doi]
    if not seed_dois and not title:
        return {"error": "Provide at least doi, dois, or title to identify seed paper(s)."}

    resolved_ids: list[str] = []
    failed_seeds: list[str] = []

    for d in seed_dois:
        oa_id = resolve_openalex_id(doi=d.strip())
        if oa_id:
            resolved_ids.append(oa_id)
        else:
            failed_seeds.append(d)

    if not resolved_ids and title:
        oa_id = resolve_openalex_id(title=title)
        if oa_id:
            resolved_ids.append(oa_id)

    if not resolved_ids:
        return {
            "error": "Could not find any seed paper(s) in OpenAlex. Verify the DOIs are correct.",
            "failed_dois": failed_seeds,
            "title_provided": title[:80] if title else "",
        }

    per_seed_limit = max(limit // len(resolved_ids), 10)
    half = max(per_seed_limit // 2, 5)

    all_citing: list = []
    all_refs: list = []

    with ThreadPoolExecutor(max_workers=min(len(resolved_ids) * 2, 8)) as pool:
        futures = {}
        for oa_id in resolved_ids:
            futures[pool.submit(
                get_cited_by, oa_id,
                year_from=year_from, year_to=year_to,
                fields_of_study=fields_of_study, limit=half,
            )] = ("citing", oa_id)
            futures[pool.submit(
                get_references, oa_id,
                year_from=year_from, year_to=year_to,
                fields_of_study=fields_of_study, limit=half,
            )] = ("refs", oa_id)

        for future in as_completed(futures):
            kind, _ = futures[future]
            try:
                papers = future.result()
                if kind == "citing":
                    all_citing.extend(papers)
                else:
                    all_refs.extend(papers)
            except Exception as exc:
                logger.debug(f"Citation network fetch failed: {exc}")

    # Deduplicate by DOI or title prefix
    def _dedup(papers: list) -> list:
        seen: set[str] = set()
        unique = []
        for p in papers:
            key = p.doi.lower() if p.doi else p.title.lower()[:50]
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        return unique

    all_citing = _dedup(all_citing)
    all_refs = _dedup(all_refs)
    all_citing.sort(key=lambda p: p.citation_count, reverse=True)
    all_refs.sort(key=lambda p: p.citation_count, reverse=True)
    all_citing = all_citing[:limit]
    all_refs = all_refs[:limit]

    def _paper_to_dict(p) -> dict:
        d = p.__dict__ if hasattr(p, "__dict__") else dict(p)
        if p.doi:
            d["source_url"] = f"https://doi.org/{p.doi}"
        elif p.source_id:
            d["source_url"] = p.source_id
        else:
            d["source_url"] = ""
        return d

    response = {
        "seeds_resolved": len(resolved_ids),
        "failed_seeds": failed_seeds if failed_seeds else None,
        "citing_papers": [_paper_to_dict(p) for p in all_citing],
        "citing_count": len(all_citing),
        "referenced_papers": [_paper_to_dict(p) for p in all_refs],
        "references_count": len(all_refs),
        "verified_sources_only": True,
    }
    if not all_citing and not all_refs:
        response["[MATERIAL GAP]"] = (
            "NO_CITATION_NETWORK_RESULTS. Forward and backward citations both empty. "
            "DO NOT fabricate or recall papers from memory. "
            "REQUIRED: Report gap honestly. The DOI may not be indexed in OpenAlex."
        )
    return response
