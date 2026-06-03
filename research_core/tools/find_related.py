"""Find related literature by auto-generating multiple queries from paper metadata.

Unified tool that covers both online (OpenAlex/S2/CrossRef) and CNKI search.
Reduces 8-12 manual search rounds to a single tool call.
"""

from __future__ import annotations

import itertools
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from loguru import logger

from research_core.sources.models import OnlinePaperHit
from research_core.tools.discover_online import (
    _fetch_all_sources,
    _merge_papers,
)
from research_core.tools.discover_online import (
    _mark_local_library as _mark_local_online,
)
from research_core.zotero.client import ZoteroClient

SortBy = Literal["relevance", "citations"]
Scope = Literal["online", "cnki", "both"]

_CHINESE_STOPWORDS = frozenset(
    "的了是在有不这我他她它们你个中大上为以及与对其可被"
    "从而所也就都已将会能把被要让用着到过没很还因但如果"
    "虽然因为所以如何什么怎么那些这些通过进行研究分析"
    "基于本文探讨影响因素机制作用视角下理论方法模型框架"
)

_ENGLISH_STOPWORDS = frozenset(
    "the a an in on of to for with by from at is are was were be been being "
    "and or not this that these those it its as but if than then so do does did "
    "has have had will would can could may might shall should about between "
    "through during into over after before under above how what which where when "
    "who whom whose why all both each few more most other some such no nor any "
    "study research paper analysis based using approach method model framework "
    "effect effects impact influence role".split()
)

_FIELDS_OF_STUDY_MAP: dict[str, list[str]] = {
    "Business": ["Business", "Economics"],
    "Economics": ["Business", "Economics"],
    "Sociology": ["Sociology"],
    "Psychology": ["Psychology"],
    "Computer Science": ["Computer Science"],
    "Medicine": ["Medicine"],
    "Environmental Science": ["Environmental Science"],
    "Geography": ["Geography", "Environmental Science"],
    "Education": ["Education"],
    "Political Science": ["Political Science"],
    "Engineering": ["Engineering"],
    "Tourism": ["Business", "Economics", "Sociology"],
    "Marketing": ["Business", "Economics"],
    "Law": ["Law", "Political Science"],
}


def _is_chinese(text: str) -> bool:
    """Check if text contains predominantly Chinese characters."""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return chinese_chars > len(text) * 0.3


def _extract_chinese_terms(text: str) -> list[str]:
    """Extract meaningful Chinese terms from text without jieba.

    Strategy: split on punctuation, common delimiters, and stopword characters.
    For longer segments, also split on common structural particles.
    """
    segments = re.split(r"[，。、；：！？\s,;:!?\-—–/()（）【】\[\]\"\'""'']+", text)
    terms = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) <= 8:
            if len(seg) >= 2 and not all(c in _CHINESE_STOPWORDS for c in seg):
                terms.append(seg)
        else:
            sub_parts = re.split(r"[的了是在有对与及其]", seg)
            for part in sub_parts:
                part = part.strip()
                if 2 <= len(part) <= 8 and not all(c in _CHINESE_STOPWORDS for c in part):
                    terms.append(part)
    return terms


def _extract_english_terms(text: str) -> list[str]:
    """Extract meaningful English terms (multi-word phrases preserved)."""
    words = re.findall(r"[a-zA-Z][\w-]*", text.lower())
    meaningful = [w for w in words if w not in _ENGLISH_STOPWORDS and len(w) > 2]
    return meaningful


def _generate_queries(
    title: str = "",
    abstract: str = "",
    keywords: list[str] | None = None,
) -> list[str]:
    """Generate tiered search queries from paper metadata (pure rules, no LLM).

    Strategy (from specific to broad):
    Tier 1: Pairwise keyword combinations (no quotes — broader matching)
    Tier 2: Single most-distinctive keywords
    Tier 3: Title-derived terms as last resort

    No quoted phrases — APIs handle multi-word terms better without exact-match
    constraints, and quoting drastically reduces recall for niche topics.
    """
    queries: list[str] = []
    kw_list = [k.strip() for k in (keywords or []) if k.strip()]

    # Tier 1: Pairwise keyword combinations (best balance of precision/recall)
    if len(kw_list) >= 2:
        for combo in itertools.combinations(kw_list[:5], 2):
            queries.append(" ".join(combo))

    # Tier 2: Single keywords (broadest, catch-all)
    for kw in kw_list[:4]:
        if len(kw.split()) >= 2:
            queries.append(kw)

    # Tier 3: Title-derived query
    if title.strip():
        title_clean = re.sub(r"[——\-—–:：].*$", "", title.strip())
        if _is_chinese(title_clean):
            terms = _extract_chinese_terms(title_clean)
            if len(terms) >= 2:
                queries.append(" ".join(terms[:3]))
        else:
            terms = _extract_english_terms(title_clean)
            if len(terms) >= 2:
                queries.append(" ".join(terms[:4]))

    seen: list[str] = []
    for q in queries:
        if q and q not in seen:
            seen.append(q)
    return seen[:8]


def _build_relevance_terms(
    title: str = "",
    keywords: list[str] | None = None,
) -> set[str]:
    """Build a set of relevance terms from paper metadata for post-filtering."""
    terms: set[str] = set()
    for kw in (keywords or []):
        kw_clean = kw.strip().lower()
        if kw_clean:
            terms.add(kw_clean)
            for word in re.findall(r"[a-z\u4e00-\u9fff]+", kw_clean):
                if len(word) >= 3 and word not in _ENGLISH_STOPWORDS:
                    terms.add(word)
    if title.strip():
        title_lower = title.strip().lower()
        for word in re.findall(r"[a-z\u4e00-\u9fff]+", title_lower):
            if len(word) >= 4 and word not in _ENGLISH_STOPWORDS:
                terms.add(word)
    return terms


def _relevance_score(hit_title: str, hit_abstract: str, relevance_terms: set[str]) -> float:
    """Score 0-1 indicating how relevant a hit is to the original paper."""
    if not relevance_terms:
        return 1.0
    text = f"{hit_title} {hit_abstract}".lower()
    matched = sum(1 for term in relevance_terms if term in text)
    return matched / len(relevance_terms)


def _filter_irrelevant(
    hits: list,
    relevance_terms: set[str],
    min_score: float = 0.15,
) -> list:
    """Remove hits that have negligible overlap with the paper's topic."""
    if not relevance_terms:
        return hits
    filtered = []
    for hit in hits:
        title = getattr(hit, "title", "")
        abstract = getattr(hit, "abstract", "")
        score = _relevance_score(title, abstract, relevance_terms)
        if score >= min_score:
            filtered.append(hit)
        else:
            logger.debug(f"Filtered out irrelevant hit: {title[:60]}... (score={score:.2f})")
    return filtered


def _dedup_cnki_hits(all_hits: list) -> list:
    """Deduplicate CNKI hits by normalized title."""
    seen_titles: set[str] = set()
    unique: list = []
    for hit in all_hits:
        key = re.sub(r"\s+", "", hit.title.lower().strip())
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique.append(hit)
    return unique


def _run_online_related(
    queries: list[str],
    *,
    year_from: int | None,
    year_to: int | None,
    limit: int,
    sort_by: SortBy,
    fields_of_study: list[str] | None,
    relevance_terms: set[str],
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Run multiple queries through online sources and merge."""
    from research_core.tools.discover_online import _fetch_depth

    fetch_depth = _fetch_depth(limit)
    all_source_lists: list[tuple[str, list]] = []

    with ThreadPoolExecutor(max_workers=min(len(queries) * 4, 16)) as pool:
        futures = {}
        for i, query in enumerate(queries):
            future = pool.submit(
                _fetch_all_sources,
                query,
                year_from=year_from,
                year_to=year_to,
                fetch_depth=fetch_depth,
                sort_by=sort_by,
                fields_of_study=fields_of_study,
            )
            futures[future] = f"batch_{i}"

        for future in as_completed(futures):
            try:
                source_lists = future.result()
                all_source_lists.extend(source_lists)
            except Exception as exc:
                logger.debug(f"Online related search batch failed: {exc}")

    if not all_source_lists:
        return []

    hits = _merge_papers(all_source_lists, limit=limit * 2, sort_by=sort_by)
    hits = _filter_irrelevant(hits, relevance_terms)
    hits = hits[:limit]
    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def _run_cnki_related(
    queries: list[str],
    *,
    year_from: int | None,
    year_to: int | None,
    source_categories: list[str] | None,
    limit: int,
    sort_by: SortBy,
    relevance_terms: set[str],
    zot: ZoteroClient | None,
) -> list:
    """Run multiple queries through CNKI in a single browser session."""
    import time

    from research_core.sources.cnki.browser import cnki_page
    from research_core.sources.cnki.exceptions import CnkiCaptchaError
    from research_core.sources.cnki.models import CnkiPaperHit
    from research_core.sources.cnki.scripts import (
        BASIC_SEARCH_JS,
        BASIC_SEARCH_URL,
    )
    from research_core.sources.cnki.search import (
        _apply_filters,
        _raw_to_hits,
    )
    from research_core.tools.discover_cnki import _mark_local_library

    all_hits: list[CnkiPaperHit] = []
    max_total_seconds = 60
    start_time = time.monotonic()
    timeouts_in_a_row = 0

    with cnki_page() as page:
        for i, query in enumerate(queries):
            elapsed = time.monotonic() - start_time
            if elapsed > max_total_seconds:
                logger.info(f"CNKI time budget exhausted ({elapsed:.0f}s), skipping remaining queries")
                break
            if timeouts_in_a_row >= 2:
                logger.info("CNKI timed out 2x in a row, aborting remaining queries")
                break

            try:
                if i == 0:
                    page.goto(BASIC_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
                    raw = page.evaluate(BASIC_SEARCH_JS, {"query": query})
                else:
                    raw = page.evaluate(BASIC_SEARCH_JS, {"query": query})
            except Exception as exc:
                if "timeout" in str(exc).lower():
                    timeouts_in_a_row += 1
                    logger.debug(f"CNKI query '{query}' timed out, skipping")
                    continue
                logger.debug(f"CNKI query '{query}' failed: {exc}")
                continue

            timeouts_in_a_row = 0

            if raw.get("error") == "captcha":
                raise CnkiCaptchaError(
                    "CNKI captcha detected. Solve it in Chrome, then retry."
                )

            hits, total, page_info = _raw_to_hits(raw, limit=20)
            all_hits.extend(hits)
            logger.debug(f"CNKI related query '{query}' -> {len(hits)} hits")

    all_hits = _dedup_cnki_hits(all_hits)
    all_hits = _filter_irrelevant(all_hits, relevance_terms)

    all_hits = _apply_filters(
        all_hits,
        year_from=year_from,
        year_to=year_to,
        limit=limit,
        sort_by=sort_by,
    )

    if zot is not None:
        _mark_local_library(all_hits, zot)

    return all_hits


def _run_citation_network(
    *,
    doi: str = "",
    title: str = "",
    year_from: int | None,
    year_to: int | None,
    fields_of_study: list[str] | None,
    relevance_terms: set[str],
    limit: int,
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Expand related papers via citation network (cited_by + references)."""
    from research_core.sources.openalex import (
        get_cited_by,
        get_references,
        resolve_openalex_id,
    )

    openalex_id = resolve_openalex_id(doi=doi, title=title)
    if not openalex_id:
        logger.debug(f"Citation network: could not resolve OpenAlex ID for doi={doi}, title={title[:50]}")
        return []

    logger.debug(f"Citation network: resolved {openalex_id}")

    common_kwargs = {
        "year_from": year_from,
        "year_to": year_to,
        "fields_of_study": fields_of_study,
        "limit": limit,
    }

    citing_papers: list = []
    referenced_papers: list = []

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_citing = pool.submit(get_cited_by, openalex_id, **common_kwargs)
        future_refs = pool.submit(get_references, openalex_id, **common_kwargs)

        try:
            citing_papers = future_citing.result()
        except Exception as exc:
            logger.debug(f"Citation network cited_by failed: {exc}")
        try:
            referenced_papers = future_refs.result()
        except Exception as exc:
            logger.debug(f"Citation network references failed: {exc}")

    source_lists: list[tuple[str, list]] = []
    if citing_papers:
        source_lists.append(("cited_by", citing_papers))
    if referenced_papers:
        source_lists.append(("references", referenced_papers))

    if not source_lists:
        return []

    hits = _merge_papers(source_lists, limit=limit * 2, sort_by="citations")
    # Do NOT apply keyword-based relevance filtering to citation network results.
    # The citation relationship itself is the strongest relevance signal —
    # papers that cite or are cited by the seed paper are definitionally related,
    # even if they don't share surface-level keywords.
    hits = hits[:limit]

    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def _run_s2_recommendations(
    *,
    doi: str,
    limit: int,
    relevance_terms: set[str],
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Get recommended papers from Semantic Scholar's recommendation engine.

    Note: S2's DOI resolution can be unreliable for some domains. If it fails
    or returns irrelevant results, this gracefully returns empty.
    """
    from research_core.sources.semantic_scholar import get_s2_recommendations

    if not doi:
        return []

    # Try both DOI formats (S2 DOI resolution can be inconsistent)
    papers = get_s2_recommendations([f"DOI:{doi}"], limit=limit)
    if not papers:
        logger.debug(f"S2 recommendations: no results for DOI:{doi}")
        return []

    # Convert ExternalPaper to OnlinePaperHit via merge pipeline
    source_lists: list[tuple[str, list]] = [("s2_recommendations", papers)]
    hits = _merge_papers(source_lists, limit=limit, sort_by="citations")
    # Light relevance filter — S2 recommendations are already semantically similar
    hits = _filter_irrelevant(hits, relevance_terms, min_score=0.05)
    hits = hits[:limit]

    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def _run_related_works(
    *,
    doi: str,
    title: str,
    year_from: int | None,
    year_to: int | None,
    fields_of_study: list[str] | None,
    limit: int,
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Get semantically related papers via OpenAlex's Related Works algorithm.

    Similar to Google Scholar's "Related articles" but via a stable, free API.
    Based on shared concepts between papers.
    """
    from research_core.sources.openalex import get_related_works, resolve_openalex_id

    oa_id = resolve_openalex_id(doi=doi, title=title)
    if not oa_id:
        logger.debug(f"Related works: could not resolve OpenAlex ID for doi={doi}, title={title[:50]}")
        return []

    papers = get_related_works(
        oa_id,
        year_from=year_from,
        year_to=year_to,
        fields_of_study=fields_of_study,
        limit=limit,
    )
    if not papers:
        return []

    source_lists: list[tuple[str, list]] = [("openalex_related", papers)]
    hits = _merge_papers(source_lists, limit=limit, sort_by="citations")
    hits = hits[:limit]

    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def _run_corpus_first_expansion(
    *,
    reference_dois: list[str],
    year_from: int | None,
    year_to: int | None,
    fields_of_study: list[str] | None,
    limit: int,
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Corpus-First strategy: expand citation network from known reference DOIs.

    This is the most effective strategy when the user's paper provides a reference
    list. The known references are definitionally relevant, and papers that cite
    them (or that they cite) form the most targeted intellectual neighborhood.
    """
    from research_core.sources.openalex import (
        get_cited_by,
        resolve_openalex_id,
    )

    # Resolve DOIs to OpenAlex IDs (in parallel)
    resolved: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(reference_dois), 6)) as pool:
        futures = {pool.submit(resolve_openalex_id, doi=d.strip()): d for d in reference_dois}
        for future in as_completed(futures):
            try:
                oa_id = future.result()
                if oa_id:
                    resolved.append(oa_id)
            except Exception:
                pass

    if not resolved:
        logger.debug(f"Corpus-first: none of {len(reference_dois)} DOIs resolved")
        return []

    logger.info(f"Corpus-first: resolved {len(resolved)}/{len(reference_dois)} seed DOIs")

    # Get papers citing each seed (forward citations are most useful for discovery)
    per_seed = max(limit // len(resolved), 5)
    all_papers: list = []

    with ThreadPoolExecutor(max_workers=min(len(resolved), 4)) as pool:
        futures = [
            pool.submit(
                get_cited_by, oa_id,
                year_from=year_from, year_to=year_to,
                fields_of_study=fields_of_study, limit=per_seed,
            )
            for oa_id in resolved
        ]
        for future in as_completed(futures):
            try:
                papers = future.result()
                all_papers.extend(papers)
            except Exception:
                pass

    if not all_papers:
        return []

    # Deduplicate and merge
    source_lists: list[tuple[str, list]] = [("corpus_expansion", all_papers)]
    hits = _merge_papers(source_lists, limit=limit * 2, sort_by="citations")

    # Deduplicate against seed DOIs themselves
    seed_doi_set = {d.strip().lower() for d in reference_dois}
    hits = [h for h in hits if not (h.doi and h.doi.lower() in seed_doi_set)]
    hits = hits[:limit]

    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def find_related_literature(
    scope: Scope = "online",
    title: str = "",
    abstract: str = "",
    keywords: list[str] | None = None,
    doi: str = "",
    reference_dois: list[str] | None = None,
    fields_of_study: list[str] | None = None,
    source_categories: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 30,
    sort_by: SortBy = "relevance",
    zot: ZoteroClient | None = None,
) -> dict:
    """Find literature related to a known paper by auto-generating multiple queries.

    Accepts paper metadata (title, abstract, keywords) and automatically:
    1. (Corpus-First) If reference_dois provided, expands citation network from
       those known references — this is the PRIMARY strategy when a paper's
       reference list is available
    2. Generates tiered search queries from the metadata (pairwise keywords)
    3. Executes queries (online / CNKI / both) with optional field filtering
    4. Runs citation network expansion from the seed paper DOI
    5. Deduplicates, merges, and post-filters results

    Args:
        doi: Optional DOI of the seed paper. Enables citation network expansion.
        reference_dois: DOIs from the paper's reference list. When provided,
            triggers Corpus-First mode: the system expands citation networks from
            these known references as the PRIMARY search strategy. Provide 3-8
            DOIs of the paper's most important cited works for best results.
        fields_of_study: Optional discipline filter to improve precision.
            Valid values: Business, Economics, Sociology, Psychology,
            Computer Science, Medicine, Environmental Science, Geography,
            Education, Political Science, Engineering, Tourism, Marketing, Law.
    """
    queries = _generate_queries(title=title, abstract=abstract, keywords=keywords)
    if not queries and not reference_dois and not doi:
        return {"error": "Provide at least title, keywords, doi, or reference_dois.", "queries": []}

    relevance_terms = _build_relevance_terms(title=title, keywords=keywords)

    result: dict = {"queries_generated": queries or [], "scope": scope}

    if scope in ("online", "both"):
        # Run ALL strategies IN PARALLEL: corpus-first, keyword, citation network, S2
        online_hits: list[OnlinePaperHit] = []
        citation_hits: list[OnlinePaperHit] = []
        s2_rec_hits: list[OnlinePaperHit] = []
        corpus_hits: list[OnlinePaperHit] = []

        with ThreadPoolExecutor(max_workers=5) as pool:
            # Corpus-First: expand from known reference DOIs (PRIMARY strategy)
            corpus_future = None
            if reference_dois:
                corpus_future = pool.submit(
                    _run_corpus_first_expansion,
                    reference_dois=reference_dois,
                    year_from=year_from,
                    year_to=year_to,
                    fields_of_study=fields_of_study,
                    limit=limit,
                    zot=zot,
                )

            # Keyword search (SUPPLEMENTARY when corpus-first is active)
            kw_future = None
            if queries:
                kw_future = pool.submit(
                    _run_online_related,
                    queries,
                    year_from=year_from,
                    year_to=year_to,
                    limit=limit,
                    sort_by=sort_by,
                    fields_of_study=fields_of_study,
                    relevance_terms=relevance_terms,
                    zot=zot,
                )

            cite_future = None
            if doi or title:
                cite_future = pool.submit(
                    _run_citation_network,
                    doi=doi,
                    title=title,
                    year_from=year_from,
                    year_to=year_to,
                    fields_of_study=fields_of_study,
                    relevance_terms=relevance_terms,
                    limit=limit,
                    zot=zot,
                )

            s2_rec_future = None
            if doi:
                s2_rec_future = pool.submit(
                    _run_s2_recommendations,
                    doi=doi,
                    limit=limit,
                    relevance_terms=relevance_terms,
                    zot=zot,
                )

            # OpenAlex Related Works (semantic similarity)
            related_works_future = None
            if doi or title:
                related_works_future = pool.submit(
                    _run_related_works,
                    doi=doi,
                    title=title,
                    year_from=year_from,
                    year_to=year_to,
                    fields_of_study=fields_of_study,
                    limit=limit,
                    zot=zot,
                )

            # Gather corpus-first results (highest priority)
            if corpus_future:
                try:
                    corpus_hits = corpus_future.result()
                except Exception as exc:
                    logger.debug(f"Corpus-first expansion failed: {exc}")

            if kw_future:
                try:
                    online_hits = kw_future.result()
                except Exception as exc:
                    logger.debug(f"Keyword search failed: {exc}")

            if cite_future:
                try:
                    citation_hits = cite_future.result()
                except Exception as exc:
                    logger.debug(f"Citation network failed: {exc}")

            if s2_rec_future:
                try:
                    s2_rec_hits = s2_rec_future.result()
                except Exception as exc:
                    logger.debug(f"S2 recommendations failed: {exc}")

            related_works_hits: list[OnlinePaperHit] = []
            if related_works_future:
                try:
                    related_works_hits = related_works_future.result()
                except Exception as exc:
                    logger.debug(f"OpenAlex related works failed: {exc}")

        # Merge all sources; corpus-first gets priority ordering
        # Start with corpus hits (most relevant due to known reference expansion)
        merged: list[OnlinePaperHit] = list(corpus_hits)
        existing_keys = {h.doi.lower() for h in merged if h.doi}

        if corpus_hits:
            result["corpus_first_used"] = True
            result["corpus_first_count"] = len(corpus_hits)

        # Then keyword search results
        for h in online_hits:
            if h.doi and h.doi.lower() in existing_keys:
                continue
            merged.append(h)
            existing_keys.add(h.doi.lower() if h.doi else "")

        # Then S2 recommendations
        if s2_rec_hits:
            for h in s2_rec_hits:
                if h.doi and h.doi.lower() in existing_keys:
                    continue
                merged.append(h)
                existing_keys.add(h.doi.lower() if h.doi else "")
            result["s2_recommendations_used"] = True

        # Then OpenAlex Related Works (semantic similarity)
        if related_works_hits:
            for h in related_works_hits:
                if h.doi and h.doi.lower() in existing_keys:
                    continue
                merged.append(h)
                existing_keys.add(h.doi.lower() if h.doi else "")
            result["related_works_used"] = True

        # Then citation network
        if citation_hits:
            for ch in citation_hits:
                if ch.doi and ch.doi.lower() in existing_keys:
                    continue
                merged.append(ch)
                existing_keys.add(ch.doi.lower() if ch.doi else "")
            result["citation_network_used"] = True

        merged = merged[:limit]

        # P2: Three-Index Verification — filter out unverifiable citations
        pre_verify_count = len(merged)
        try:
            from research_core.sources.verify import verify_batch
            merged = verify_batch(merged, max_workers=6)
            rejected = pre_verify_count - len(merged)
            if rejected > 0:
                result["verification_filtered"] = rejected
        except Exception as exc:
            logger.debug(f"Three-index verification skipped: {exc}")

        result["online_hits"] = merged
        result["online_count"] = len(merged)

    if scope in ("cnki", "both"):
        cnki_hits = _run_cnki_related(
            queries,
            year_from=year_from,
            year_to=year_to,
            source_categories=source_categories,
            limit=limit,
            sort_by=sort_by,
            relevance_terms=relevance_terms,
            zot=zot,
        )
        result["cnki_hits"] = cnki_hits
        result["cnki_count"] = len(cnki_hits)

    return result
