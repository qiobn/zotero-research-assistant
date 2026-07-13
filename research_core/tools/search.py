"""Library discovery tools — search, find similar, browse."""

from __future__ import annotations

import time
from dataclasses import dataclass

from research_core.rag.logger import RetrievalLog, RetrievalLogger
from research_core.rag.reranker import get_reranker
from research_core.rag.retriever import Retriever
from research_core.utils import WRITE_PREVIEW_HINT
from research_core.zotero.client import ZoteroClient
from research_core.zotero.models import Item


@dataclass
class PaperHit:
    """A paper-level result combining metadata and (optionally) best matching passage."""

    key: str
    title: str
    authors: list[str]
    year: int
    doi: str
    tags: list[str]
    score: float
    matched_passage: str = ""
    matched_page: int = 0
    source: str = "hybrid"
    paper_abstract: str = ""
    section_heading: str = ""
    section_type: str = ""
    relevance_tier: str = ""  # "high" | "medium" | "low" — for LLM-friendly display


def search_papers(
    query: str,
    zot: ZoteroClient,
    retriever: Retriever,
    year_from: int | None = None,
    year_to: int | None = None,
    tags_include: list[str] | None = None,
    tags_exclude: list[str] | None = None,
    collection_key: str = "",
    limit: int = 10,
    expand_context: bool = False,
    expand_neighbors: bool = False,
    diversity_weight: float = 0.4,
) -> list[PaperHit]:
    """Hybrid search: keyword (Zotero API) + semantic (vector store) merged via RRF.

    When expand_context=True, each result includes the full section text
    (all chunks in the same section) for richer LLM context.

    When expand_neighbors=True, each result includes the hit chunk ±1 neighbor
    chunks — a lighter alternative to full section expansion.

    Applies MMR (Maximal Marginal Relevance) diversity reranking by default
    (diversity_weight=0.4). Chunk-level MMR with hard cap of 3 chunks per
    paper and per-document penalty of 0.1. Set diversity_weight=0 to disable
    (e.g. for targeted single-paper retrieval).

    If query is empty, skips semantic search and returns all items matching the filters
    (year/tags/collection), sorted by date added (most recent first).
    """
    tag_filter: list[str] = []
    if tags_include:
        tag_filter.extend(tags_include)
    if tags_exclude:
        tag_filter.extend(f"-{t}" for t in tags_exclude)

    has_query = bool(query.strip())

    # --- Instrumentation ---
    logger = RetrievalLogger()
    log_params = {
        "limit": limit, "year_from": year_from, "year_to": year_to,
        "tags_include": tags_include, "tags_exclude": tags_exclude,
    }

    keyword_items: list[Item] = []
    semantic_hits = []
    t_keyword = 0.0
    t_semantic = 0.0
    t_rerank = 0.0

    if has_query:
        t0 = time.time()
        keyword_items = zot.search_items(
            query=query,
            limit=limit * 3,
            tag=tag_filter or None,
            collection_key=collection_key,
        )
        t_keyword = (time.time() - t0) * 1000
    else:
        t0 = time.time()
        keyword_items = zot.search_items(
            query="",
            limit=max(limit * 5, 100),
            tag=tag_filter or None,
            collection_key=collection_key,
        )
        t_keyword = (time.time() - t0) * 1000

    reranker = get_reranker()
    overfetch = 3 if reranker is None else 5
    rrf_k = 60

    # ── Query expansion (bilingual + synonym) ──
    expanded_queries: list[tuple[str, float]] = [(query, 1.0)]
    if has_query:
        try:
            from research_core.rag.query_rewriter import get_rewriter
            expanded_queries = get_rewriter().expand(query)
        except Exception:
            pass  # query expansion is best-effort; never block search

    if has_query:
        t0 = time.time()
        # Run semantic search with each expanded query, weighted by expansion score
        if len(expanded_queries) > 1:
            # Multi-query semantic search: run each expanded query, merge via RRF
            all_semantic: list = []
            for eq_text, eq_weight in expanded_queries:
                eq_hits = retriever.search(
                    eq_text, n_results=max(limit * overfetch, 15),
                    expand_context=expand_context,
                    expand_neighbors=expand_neighbors,
                )
                # Apply expansion weight to scores
                for h in eq_hits:
                    h.score *= eq_weight
                all_semantic.extend(eq_hits)
            # Merge: dedupe by item_key, keep max weighted score
            merged: dict[str, type(all_semantic[0])] = {}
            for h in all_semantic:
                if h.item_key not in merged or h.score > merged[h.item_key].score:
                    merged[h.item_key] = h
            semantic_hits = sorted(merged.values(), key=lambda h: h.score, reverse=True)
        else:
            semantic_hits = retriever.search(query, n_results=limit * overfetch,
                                              expand_context=expand_context,
                                              expand_neighbors=expand_neighbors)
        t_semantic = (time.time() - t0) * 1000

    # ── BM25 Sparse Keyword Search (chunk text) ──
    bm25_hits = []
    t_bm25 = 0.0
    if has_query:
        t0 = time.time()
        bm25_hits = retriever.search_bm25(query, top_k=limit * overfetch * 3)
        t_bm25 = (time.time() - t0) * 1000

    pre_rerank_n = len(semantic_hits)
    ce_scores: dict[str, float] = {}  # item_key → Cross-Encoder score
    if reranker and semantic_hits and has_query:
        t0 = time.time()
        docs = [h.text for h in semantic_hits]
        reranked = reranker.rerank(query, docs, top_k=limit * 3)
        # Capture CE scores for tier assignment
        for idx, ce_score in reranked:
            key = semantic_hits[idx].item_key
            ce_scores[key] = float(ce_score)
        semantic_hits = [semantic_hits[idx] for idx, _ in reranked]
        t_rerank = (time.time() - t0) * 1000

    # ── MMR Diversity Reranking (post Cross-Encoder) ──
    t_mmr = 0.0
    if diversity_weight > 0 and semantic_hits and has_query:
        t0 = time.time()
        pre_mmr_n = len(semantic_hits)
        semantic_hits = retriever.mmr_diversify(
            semantic_hits,
            diversity_weight=diversity_weight,
        )
        post_mmr_papers = len(set(h.item_key for h in semantic_hits))
        t_mmr = (time.time() - t0) * 1000
        log_params["mmr_weight"] = diversity_weight
        log_params["mmr_papers_before"] = pre_mmr_n
        log_params["mmr_papers_after"] = post_mmr_papers

    # ── Compute relevance tiers (percentile-based) ──
    _TIER_HIGH = 0
    _TIER_MED = 0
    if ce_scores:
        all_scores = sorted(ce_scores.values())
        n = len(all_scores)
        _TIER_HIGH = all_scores[int(n * 0.75)] if n >= 4 else all_scores[-1]
        _TIER_MED = all_scores[int(n * 0.25)] if n >= 4 else all_scores[0]

    def _assign_tier(item_key: str, source: str) -> str:
        """Assign a relevance tier: high/medium/low."""
        if item_key in ce_scores:
            s = ce_scores[item_key]
            if s >= _TIER_HIGH:
                return "high"
            if s >= _TIER_MED:
                return "medium"
            return "low"
        # No CE score — fall back to source-based tiering
        if source == "hybrid":
            return "medium"
        if source == "semantic":
            return "medium"
        if source == "keyword":
            return "low"
        return "low"  # fallback

    semantic_ranks: dict[str, int] = {}
    semantic_best_passage: dict[str, tuple[str, int]] = {}
    semantic_enriched: dict[str, dict] = {}  # paper_abstract, section_heading, section_type
    seen_keys: set[str] = set()
    for rank, hit in enumerate(semantic_hits):
        if hit.item_key in seen_keys:
            continue
        seen_keys.add(hit.item_key)
        semantic_ranks[hit.item_key] = rank + 1
        if expand_neighbors and hit.neighbor_context:
            semantic_best_passage[hit.item_key] = (
                hit.neighbor_context.full_text[:2000],
                hit.neighbor_context.page_start,
            )
        elif expand_context and hit.section_context:
            semantic_best_passage[hit.item_key] = (
                hit.section_context.full_text[:2000],
                hit.section_context.page_start,
            )
        else:
            semantic_best_passage[hit.item_key] = (hit.text[:300], hit.page_start)
        # Capture enriched paper/section metadata
        semantic_enriched[hit.item_key] = {
            "paper_abstract": getattr(hit, "paper_abstract", "") or "",
            "section_heading": getattr(hit, "section_heading", "") or "",
            "section_type": getattr(hit, "section_type", "") or "",
        }

    # ── BM25 ranks (chunk-level → paper-level best rank) ──
    bm25_ranks: dict[str, int] = {}
    bm25_seen: set[str] = set()
    for rank, hit in enumerate(bm25_hits):
        if hit.item_key in bm25_seen:
            continue
        bm25_seen.add(hit.item_key)
        bm25_ranks[hit.item_key] = rank + 1
        # Capture BM25-matched passage if semantic didn't already
        if hit.item_key not in semantic_best_passage and hit.text:
            semantic_best_passage[hit.item_key] = (hit.text[:300], 0)

    # ── Two-way RRF: BM25 (lexical) + Dense (semantic) ──
    candidate_keys = set(semantic_ranks) | set(bm25_ranks)
    rrf_k = 60
    scored: list[tuple[float, str]] = []
    for key in candidate_keys:
        score = 0.0
        if key in semantic_ranks:
            score += 1.0 / (rrf_k + semantic_ranks[key])
        if key in bm25_ranks:
            score += 1.0 / (rrf_k + bm25_ranks[key])
        scored.append((score, key))
    scored.sort(reverse=True)

    items_by_key: dict[str, Item] = {item.key: item for item in keyword_items}
    missing = [k for k, _ in [(k, s) for s, k in scored[: limit * 2]] if k not in items_by_key]
    for item in zot.get_items_batch(missing):
        items_by_key[item.key] = item

    tags_incl_set = set(tags_include) if tags_include else set()
    tags_excl_set = set(tags_exclude) if tags_exclude else set()
    collection_filter = collection_key.strip() if collection_key else ""

    hits: list[PaperHit] = []
    for score, key in scored:
        item = items_by_key.get(key)
        if not item:
            continue
        year = ZoteroClient.parse_year(item.date)
        if (year_from or year_to) and year == 0:
            continue
        if year_from and year < year_from:
            continue
        if year_to and year > year_to:
            continue
        item_tags = set(item.tags)
        if tags_incl_set and not tags_incl_set.issubset(item_tags):
            continue
        if tags_excl_set and tags_excl_set & item_tags:
            continue
        if collection_filter and collection_filter not in item.collections:
            continue
        passage, page = semantic_best_passage.get(key, ("", 0))
        enriched_meta = semantic_enriched.get(key, {})
        has_bm25 = key in bm25_ranks
        has_semantic = key in semantic_ranks
        if has_bm25 and has_semantic:
            src = "hybrid"
        elif has_bm25:
            src = "keyword"
        else:
            src = "semantic"
        hits.append(
            PaperHit(
                key=item.key,
                title=item.title,
                authors=item.authors,
                year=year,
                doi=item.doi,
                tags=item.tags,
                score=round(score, 4),
                matched_passage=passage,
                matched_page=page,
                source=src,
                paper_abstract=enriched_meta.get("paper_abstract", ""),
                section_heading=enriched_meta.get("section_heading", ""),
                section_type=enriched_meta.get("section_type", ""),
                relevance_tier=_assign_tier(item.key, src),
            )
        )
        if len(hits) >= limit:
            break

    fallback_triggered = False
    fallback_items: list[Item] = []
    if not hits and query.strip():
        fallback_triggered = True
        fallback_items = zot.search_items(
            query=query, limit=limit * 2, qmode="everything",
            tag=tag_filter or None, collection_key=collection_key,
        )
        for item in fallback_items:
            year = ZoteroClient.parse_year(item.date)
            if (year_from or year_to) and year == 0:
                continue
            if year_from and year < year_from:
                continue
            if year_to and year > year_to:
                continue
            fb_tags = set(item.tags)
            if tags_incl_set and not tags_incl_set.issubset(fb_tags):
                continue
            if tags_excl_set and tags_excl_set & fb_tags:
                continue
            if collection_filter and collection_filter not in item.collections:
                continue
            hits.append(
                PaperHit(
                    key=item.key,
                    title=item.title,
                    authors=item.authors,
                    year=year,
                    doi=item.doi,
                    tags=item.tags,
                    score=0.0,
                    source="fallback",
                    relevance_tier="low",
                )
            )
            if len(hits) >= limit:
                break

    # --- Emit retrieval trace ---
    logger.log(RetrievalLog(
        query=query,
        expanded_queries=[{"text": eq[0], "weight": eq[1]} for eq in expanded_queries]
                          if len(expanded_queries) > 1 else [],
        strategy="hybrid" if (has_query and bm25_hits and semantic_hits)
        else ("semantic" if semantic_hits else "keyword" if bm25_hits else "fallback"),
        parameters=log_params,
        candidate_keyword_n=len(keyword_items),
        candidate_bm25_n=len(bm25_hits),
        candidate_semantic_n=pre_rerank_n,
        candidate_merged_n=len(hits),
        reranker_enabled=reranker is not None,
        reranker_model=getattr(reranker, "_model_name", "") if reranker else "",
        reranker_pre_n=pre_rerank_n,
        reranker_post_n=len(semantic_hits),
        results=[
            {"item_key": h.key, "title": h.title[:80], "score": h.score,
             "rank": i + 1, "source": h.source}
            for i, h in enumerate(hits[:20])
        ],
        result_count=len(hits),
        fallback_triggered=fallback_triggered,
        fallback_count=len(fallback_items),
        latency_keyword_ms=round(t_keyword, 1),
        latency_bm25_ms=round(t_bm25, 1),
        latency_semantic_ms=round(t_semantic, 1),
        latency_rerank_ms=round(t_rerank, 1),
        latency_mmr_ms=round(t_mmr, 1),
        latency_total_ms=round(t_keyword + t_semantic + t_rerank + t_mmr, 1),
    ))

    return hits


def find_similar_papers(
    item_key: str,
    zot: ZoteroClient,
    retriever: Retriever,
    limit: int = 10,
) -> list[PaperHit]:
    """Find papers in the library that are conceptually similar to a given paper.

    Strategy: build a query from the source paper's title + abstract (or first chunk if
    abstract is empty), then run semantic search and exclude the source itself.
    """
    source = zot.get_item(item_key)
    query_parts: list[str] = []
    if source.title:
        query_parts.append(source.title)
    if source.abstract:
        query_parts.append(source.abstract)
    if not query_parts:
        chunks = retriever.get_item_chunks(item_key)
        if chunks:
            query_parts.append(chunks[0].text[:800])
    if not query_parts:
        return []
    query = "\n\n".join(query_parts)

    raw_hits = retriever.search(
        query,
        n_results=max(limit * 30, 200),
        where={"item_key": {"$ne": item_key}},
    )

    reranker = get_reranker()
    if reranker and raw_hits:
        docs = [h.text for h in raw_hits]
        reranked = reranker.rerank(query, docs, top_k=limit * 10)
        raw_hits = [raw_hits[idx] for idx, _ in reranked]

    best_score: dict[str, float] = {}
    best_passage: dict[str, tuple[str, int]] = {}
    for hit in raw_hits:
        if hit.item_key not in best_score or hit.score > best_score[hit.item_key]:
            best_score[hit.item_key] = hit.score
            best_passage[hit.item_key] = (hit.text[:300], hit.page_start)

    sorted_keys = sorted(best_score.keys(), key=lambda k: best_score[k], reverse=True)
    candidate_keys = sorted_keys[: limit * 2]
    items = zot.get_items_batch(candidate_keys)
    items_by_key = {it.key: it for it in items}
    hits: list[PaperHit] = []
    for key in candidate_keys:
        item = items_by_key.get(key)
        if not item:
            continue
        passage, page = best_passage[key]
        hits.append(
            PaperHit(
                key=item.key,
                title=item.title,
                authors=item.authors,
                year=ZoteroClient.parse_year(item.date),
                doi=item.doi,
                tags=item.tags,
                score=round(best_score[key], 4),
                matched_passage=passage,
                matched_page=page,
                source="similar",
            )
        )
        if len(hits) >= limit:
            break
    return hits


@dataclass
class BrowseResult:
    scope: str
    items: list[dict]
    total: int


def browse_library(
    scope: str,
    zot: ZoteroClient,
    collection_key: str = "",
    limit: int = 20,
) -> BrowseResult:
    """Explore library structure. scope ∈ {collections, tags, recent, collection_items}."""
    if scope == "collections":
        cols = zot.get_collections()
        flattened = [
            {
                "key": c.get("data", c).get("key", ""),
                "name": c.get("data", c).get("name", ""),
                "parent": c.get("data", c).get("parentCollection", "") or "",
            }
            for c in cols
        ]
        return BrowseResult(scope=scope, items=flattened[:limit], total=len(flattened))

    if scope == "tags":
        tags = zot.get_tags()
        return BrowseResult(
            scope=scope,
            items=[{"tag": t} for t in tags[:limit]],
            total=len(tags),
        )

    if scope == "recent":
        items = zot.get_recent(limit=limit)
        return BrowseResult(
            scope=scope,
            items=[
                {
                    "key": it.key,
                    "title": it.title,
                    "authors": it.authors,
                    "date": it.date,
                    "tags": it.tags,
                }
                for it in items
            ],
            total=len(items),
        )

    if scope == "collection_items":
        if not collection_key:
            return BrowseResult(scope=scope, items=[], total=0)
        items = zot.get_collection_items(collection_key, limit=limit)
        return BrowseResult(
            scope=scope,
            items=[
                {
                    "key": it.key,
                    "title": it.title,
                    "authors": it.authors,
                    "date": it.date,
                    "tags": it.tags,
                }
                for it in items
            ],
            total=len(items),
        )

    return BrowseResult(scope=scope, items=[], total=0)


# ── find_duplicates ──────────────────────────────────────────


@dataclass
class DuplicateGroup:
    """A group of 2+ items that appear to be duplicates."""

    items: list[dict]
    match_reason: str


def find_duplicates(zot: ZoteroClient) -> list[DuplicateGroup]:
    """Find duplicate items in the library by normalized title and/or DOI match."""
    raw_groups = zot.find_duplicates()
    groups: list[DuplicateGroup] = []
    for group in raw_groups:
        dois = {it.get("doi", "") for it in group if it.get("doi")}
        reason = "doi_match" if dois else "title_match"
        groups.append(DuplicateGroup(items=group, match_reason=reason))
    return groups


@dataclass
class MergeResult:
    """Result of a duplicate merge operation."""

    confirmed: bool
    preview: dict
    result: dict | None = None
    error: str = ""


def merge_duplicates(
    keeper_key: str,
    duplicate_keys: list[str],
    zot: ZoteroClient,
    confirm: bool = False,
) -> MergeResult:
    """Merge duplicate items into a keeper. Defaults to dry-run preview.

    Merges tags, collections, and re-parents children from duplicates into the
    keeper item. Duplicate attachments (by contentType+filename+md5) are skipped.
    Duplicates are moved to trash (not permanently deleted).
    """
    if not keeper_key or not duplicate_keys:
        return MergeResult(
            confirmed=False, preview={},
            error="Both keeper_key and duplicate_keys are required.",
        )

    preview = {
        "action": "merge_duplicates",
        "keeper_key": keeper_key,
        "duplicate_keys": duplicate_keys,
        "count": len(duplicate_keys),
    }

    if not confirm:
        try:
            keeper = zot.get_item(keeper_key)
            preview["keeper_title"] = keeper.title
            dup_titles = []
            for dk in duplicate_keys:
                try:
                    d = zot.get_item(dk)
                    dup_titles.append({"key": dk, "title": d.title})
                except Exception:
                    dup_titles.append({"key": dk, "title": "(not found)"})
            preview["duplicates"] = dup_titles
        except Exception as e:
            return MergeResult(confirmed=False, preview=preview, error=str(e))
        preview["next_step"] = WRITE_PREVIEW_HINT
        if not zot.can_write:
            preview["warning"] = (
                "Write operations are not available. To enable writes, add "
                "ZOTERO_API_KEY and ZOTERO_LIBRARY_ID to your .env file."
            )
        return MergeResult(confirmed=False, preview=preview)

    if not zot.can_write:
        return MergeResult(
            confirmed=False, preview=preview,
            error="Write operations are not available.",
        )

    try:
        result = zot.merge_items(keeper_key, duplicate_keys)
        return MergeResult(confirmed=True, preview=preview, result=result)
    except Exception as e:
        return MergeResult(confirmed=False, preview=preview, error=str(e))
