"""Three-Index Verification: cross-check citations against S2, OpenAlex, CrossRef.

Filters out potentially fabricated citations by requiring a paper to be findable
in at least one of the three major bibliographic indices.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from loguru import logger

from research_core.sources.models import OnlinePaperHit

_CROSSREF_BASE = "https://api.crossref.org/works"
_S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_OPENALEX_BASE = "https://api.openalex.org/works"
_TIMEOUT = 10


def _check_crossref(doi: str) -> bool:
    """Verify DOI exists in CrossRef."""
    try:
        r = httpx.head(
            f"{_CROSSREF_BASE}/{doi}",
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        return r.status_code == 200
    except Exception:
        return False


def _check_openalex(doi: str) -> bool:
    """Verify DOI exists in OpenAlex."""
    try:
        r = httpx.get(
            f"{_OPENALEX_BASE}/doi:{doi}",
            params={"select": "id"},
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


def _check_s2(doi: str) -> bool:
    """Verify DOI exists in Semantic Scholar."""
    try:
        r = httpx.get(
            f"{_S2_BASE}/DOI:{doi}",
            params={"fields": "paperId"},
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


def verify_single(doi: str) -> bool:
    """Check if a DOI is findable in at least one of three indices.

    Returns True if verified in ANY of: CrossRef, OpenAlex, Semantic Scholar.
    Papers without DOI are assumed unverifiable (return True to avoid filtering
    legitimate results that simply lack DOI metadata).
    """
    if not doi or not doi.strip():
        return True

    doi = doi.strip()

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_check_crossref, doi),
            pool.submit(_check_openalex, doi),
            pool.submit(_check_s2, doi),
        ]
        for future in as_completed(futures):
            try:
                if future.result():
                    return True
            except Exception:
                continue
    return False


def verify_batch(hits: list[OnlinePaperHit], max_workers: int = 6) -> list[OnlinePaperHit]:
    """Filter a batch of hits, keeping only those verified in at least one index.

    Papers without DOI are kept (we cannot verify/disprove them via DOI lookup).
    This prevents fabricated citations with fake DOIs from passing through.
    """
    if not hits:
        return hits

    # Separate: papers with DOI need verification, papers without pass through
    needs_check = [(i, h) for i, h in enumerate(hits) if h.doi and h.doi.strip()]

    if not needs_check:
        return hits

    verified_indices: set[int] = set()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(verify_single, h.doi): i
            for i, h in needs_check
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                if future.result():
                    verified_indices.add(idx)
            except Exception:
                verified_indices.add(idx)

    # Keep verified papers in original order, then append no-DOI papers
    verified = [h for i, h in needs_check if i in verified_indices]
    rejected_count = len(needs_check) - len(verified)

    if rejected_count > 0:
        logger.info(
            f"Three-Index Verification: {rejected_count}/{len(needs_check)} "
            f"papers with DOI could NOT be verified in any index (filtered out)"
        )

    # Maintain original ordering
    result_set = set(verified_indices)
    ordered: list[OnlinePaperHit] = []
    for i, h in enumerate(hits):
        if not (h.doi and h.doi.strip()):
            ordered.append(h)
        elif i in result_set:
            ordered.append(h)

    return ordered
