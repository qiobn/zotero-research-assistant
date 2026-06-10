"""Three-Index Verification: cross-check citations against S2, OpenAlex, CrossRef.

Filters out potentially fabricated citations by requiring a paper to be findable
in at least one of the three major bibliographic indices.

Verification uses a three-outcome model per index:
  - True  = confirmed exists (HTTP 200)
  - False = confirmed absent (HTTP 404/400)
  - None  = inconclusive (network error, timeout, 5xx)

A paper is REJECTED only when all indices confirm absence (all return False).
If any index returns True, the paper is verified. If results are mixed with
None (inconclusive), the paper is kept (fail-open on network issues).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from research_core.sources import http_client as _http
from research_core.sources.models import OnlinePaperHit

_CROSSREF_BASE = "https://api.crossref.org/works"
_S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_OPENALEX_BASE = "https://api.openalex.org/works"
_TIMEOUT = 10


def _check_crossref(doi: str) -> bool | None:
    """Verify DOI in CrossRef. Returns True/False/None (inconclusive on error)."""
    try:
        r = _http.head(f"{_CROSSREF_BASE}/{doi}", timeout=_TIMEOUT)
        if r.status_code == 200:
            return True
        if r.status_code in (404, 400):
            return False
        return None
    except Exception:
        return None


def _check_openalex(doi: str) -> bool | None:
    """Verify DOI in OpenAlex. Returns True/False/None (inconclusive on error)."""
    try:
        r = _http.get(
            f"{_OPENALEX_BASE}/doi:{doi}",
            params={"select": "id"},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return True
        if r.status_code in (404, 400):
            return False
        return None
    except Exception:
        return None


def _check_s2(doi: str) -> bool | None:
    """Verify DOI in Semantic Scholar. Returns True/False/None (inconclusive on error)."""
    try:
        r = _http.get(
            f"{_S2_BASE}/DOI:{doi}",
            params={"fields": "paperId"},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return True
        if r.status_code in (404, 400):
            return False
        return None
    except Exception:
        return None


def verify_single(doi: str) -> bool:
    """Check if a DOI is findable in at least one of three indices.

    Returns True if:
      - Verified in ANY index (confirmed exists), OR
      - Results are inconclusive (network errors) — fail-open to avoid
        rejecting legitimate papers due to transient network issues.

    Returns False only when ALL indices confirm the DOI does not exist
    (all return 404/400).
    """
    if not doi or not doi.strip():
        return True

    doi = doi.strip()

    results: list[bool | None] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_check_crossref, doi),
            pool.submit(_check_openalex, doi),
            pool.submit(_check_s2, doi),
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                result = None
            if result is True:
                return True
            results.append(result)

    # If ALL indices confirm absent (False), reject. Otherwise keep (fail-open).
    if all(r is False for r in results) and results:
        return False
    return True


def verify_batch(hits: list[OnlinePaperHit], max_workers: int = 6) -> list[OnlinePaperHit]:
    """Filter a batch of hits, keeping only those verified in at least one index.

    Papers without DOI are kept (we cannot verify/disprove them via DOI lookup).
    Papers are ONLY rejected when all three indices confirm absence (404).
    Network errors result in fail-open (paper kept).
    """
    if not hits:
        return hits

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
                # Outer exception → fail-open
                verified_indices.add(idx)

    rejected_count = len(needs_check) - len(verified_indices)

    if rejected_count > 0:
        logger.info(
            f"Three-Index Verification: {rejected_count}/{len(needs_check)} "
            f"papers with DOI confirmed absent from all indices (filtered out)"
        )

    # Maintain original ordering
    result_set = verified_indices
    ordered: list[OnlinePaperHit] = []
    for i, h in enumerate(hits):
        if not (h.doi and h.doi.strip()):
            ordered.append(h)
        elif i in result_set:
            ordered.append(h)

    return ordered
