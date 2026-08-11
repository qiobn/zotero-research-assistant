"""Recall evaluation using LLM-as-judge methodology.

True recall measurement requires knowing ALL relevant documents for a query,
not just a pre-defined subset. This module implements the standard IR
pooling method:

1. **Pool construction**: For each query, retrieve a large candidate set
   using the full pipeline (top K=50). Also inject any expected_item_keys
   that the pipeline missed, so the pool represents a broader universe.

2. **Independent judge**: An LLM (gpt5.4 / deepseek-pro) evaluates each
   query-paper pair for relevance. The judge is independent of the retrieval
   pipeline — it uses a different model family and sees title + tags, not
   the retrieval score or matched passage.

3. **Recall computation**: Recall@K = relevant_in_topK / total_relevant_in_pool

The pool method underestimates true recall (relevant papers outside the pool
are invisible), but it's the standard approach in IR evaluation (TREC, BEIR)
and is consistent for A/B comparisons.

Usage:
    python scripts/run_recall_evaluation.py --queries tests/eval_queries_user.json
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from research_core.rag.eval_judge import LLMJudge
from research_core.zotero.client import ZoteroClient

if TYPE_CHECKING:
    from research_core.tools.search import PaperHit


@dataclass
class RecallEvalQuery:
    """A single query for recall evaluation."""
    query_id: str
    query_text: str
    category: str = "direct"
    difficulty: str = "medium"
    expected_item_keys: list[str] = field(default_factory=list)
    notes: str = ""
    # Pool config per query — can override globally
    pool_size: int = 50


@dataclass
class PoolEntry:
    """A paper in the evaluation pool."""
    key: str
    title: str
    tags: list[str]
    abstract: str = ""
    # Retrieved at which ranks (None if not retrieved at that K)
    rank_at_5: int | None = None   # 1-indexed rank in top-5
    rank_at_10: int | None = None
    rank_at_20: int | None = None
    rank_at_50: int | None = None
    # Judge result
    relevant: bool | None = None  # None = not yet judged


@dataclass
class SingleQueryRecallResult:
    """Recall evaluation result for one query."""
    query_id: str
    query_text: str
    category: str
    pool_size: int
    pool_relevant: int          # total relevant papers in pool
    pool_judged: int            # papers that got a judge verdict
    recall_at_5: float          # relevant_in_top5 / pool_relevant
    recall_at_10: float
    recall_at_20: float
    recall_at_50: float
    precision_at_5: float       # relevant_in_top5 / 5
    precision_at_10: float
    precision_at_20: float
    mrr: float                  # reciprocal rank of FIRST relevant result
    ndcg_at_10: float
    first_relevant_rank: int    # 1-indexed, 0 = none found
    top5_keys: list[str] = field(default_factory=list)
    top10_keys: list[str] = field(default_factory=list)
    top20_keys: list[str] = field(default_factory=list)
    relevant_keys: list[str] = field(default_factory=list)  # all judged relevant
    error: str = ""
    latency_ms: float = 0.0
    judge_tokens_in: int = 0
    judge_tokens_out: int = 0


@dataclass
class RecallEvalResult:
    """Aggregated recall evaluation results."""
    total_queries: int = 0
    queries_with_errors: int = 0
    total_pool_entries: int = 0          # total query-paper pairs judged
    total_relevant: int = 0              # total marked relevant by judge
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    recall_at_20: float = 0.0
    recall_at_50: float = 0.0
    precision_at_5: float = 0.0
    precision_at_10: float = 0.0
    precision_at_20: float = 0.0
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    judge_model: str = ""
    per_query: list[SingleQueryRecallResult] = field(default_factory=list)
    by_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    valid_queries: int = 0

    def summary(self) -> str:
        lines = [
            f"Recall Evaluation (judge: {self.judge_model})",
            f"  Queries: {self.total_queries} total, {self.valid_queries} valid, "
            f"{self.queries_with_errors} errors",
            f"  Pool: {self.total_pool_entries} query-paper pairs judged, "
            f"{self.total_relevant} relevant",
            "",
            "  ── Recall ──",
            f"    Recall@5:  {self.recall_at_5:.1%}",
            f"    Recall@10: {self.recall_at_10:.1%}",
            f"    Recall@20: {self.recall_at_20:.1%}",
            f"    Recall@50: {self.recall_at_50:.1%}",
            "",
            "  ── Precision ──",
            f"    Precision@5:  {self.precision_at_5:.1%}",
            f"    Precision@10: {self.precision_at_10:.1%}",
            f"    Precision@20: {self.precision_at_20:.1%}",
            "",
            "  ── Rank Metrics ──",
            f"    MRR:        {self.mrr:.3f}",
            f"    NDCG@10:    {self.ndcg_at_10:.3f}",
        ]
        if self.by_category:
            lines.append("")
            lines.append("  ── By Category ──")
            for cat, stats in sorted(self.by_category.items()):
                lines.append(
                    f"    {cat} (n={stats['count']}, valid={stats['valid_count']}, "
                    f"errors={stats['error_count']}): "
                    f"R@10={stats['recall_at_10']:.1%}, "
                    f"P@10={stats['precision_at_10']:.1%}, "
                    f"MRR={stats['mrr']:.3f}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "judge_model": self.judge_model,
            "total_queries": self.total_queries,
            "queries_with_errors": self.queries_with_errors,
            "valid_queries": self.valid_queries,
            "total_pool_entries": self.total_pool_entries,
            "total_relevant": self.total_relevant,
            "recall_at_5": round(self.recall_at_5, 4),
            "recall_at_10": round(self.recall_at_10, 4),
            "recall_at_20": round(self.recall_at_20, 4),
            "recall_at_50": round(self.recall_at_50, 4),
            "precision_at_5": round(self.precision_at_5, 4),
            "precision_at_10": round(self.precision_at_10, 4),
            "precision_at_20": round(self.precision_at_20, 4),
            "mrr": round(self.mrr, 4),
            "ndcg_at_10": round(self.ndcg_at_10, 4),
            "by_category": self.by_category,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "per_query": [
                {
                    "query_id": r.query_id,
                    "category": r.category,
                    "pool_size": r.pool_size,
                    "pool_relevant": r.pool_relevant,
                    "recall_at_5": round(r.recall_at_5, 4),
                    "recall_at_10": round(r.recall_at_10, 4),
                    "recall_at_20": round(r.recall_at_20, 4),
                    "recall_at_50": round(r.recall_at_50, 4),
                    "precision_at_5": round(r.precision_at_5, 4),
                    "precision_at_10": round(r.precision_at_10, 4),
                    "precision_at_20": round(r.precision_at_20, 4),
                    "mrr": round(r.mrr, 4),
                    "ndcg_at_10": round(r.ndcg_at_10, 4),
                    "first_relevant_rank": r.first_relevant_rank,
                    "error": r.error,
                    "latency_ms": round(r.latency_ms, 1),
                }
                for r in self.per_query
            ],
        }


# ── Core evaluation function ─────────────────────────────────────────────


def evaluate_recall(
    zot: ZoteroClient,
    queries: list[RecallEvalQuery],
    judge: LLMJudge | None = None,
    pool_limit: int = 50,
    judge_model: str = "openai-gpt-5-4",
    include_abstracts: bool = True,
    search_kwargs: dict | None = None,
) -> RecallEvalResult:
    """Run recall evaluation with LLM-as-judge.

    Args:
        zot: ZoteroClient (for metadata lookup).
        queries: List of queries to evaluate.
        judge: LLMJudge instance (created from env config if None).
        pool_limit: Number of results to retrieve per query for pool.
        judge_model: Model name to use for judging.
        include_abstracts: Include paper abstracts in judge input (better accuracy,
                          but more tokens).
        search_kwargs: Extra kwargs forwarded to search_papers — used by the
            component-ablation harness (e.g. enable_bm25/enable_semantic/
            enable_rerank to test single-component retrieval).

    Returns:
        RecallEvalResult with metrics.
    """
    if judge is None:
        from research_core.rag.eval_judge import get_judge
        judge = get_judge(model=judge_model)

    result = RecallEvalResult(
        total_queries=len(queries),
        judge_model=judge.config.model,
    )
    t0 = time.time()

    # Stats accumulators (valid queries only; errored queries are tracked separately)
    all_recall_5: list[float] = []
    all_recall_10: list[float] = []
    all_recall_20: list[float] = []
    all_recall_50: list[float] = []
    all_precision_5: list[float] = []
    all_precision_10: list[float] = []
    all_precision_20: list[float] = []
    all_mrr: list[float] = []
    all_ndcg_10: list[float] = []

    for q_idx, q in enumerate(queries):
        sqr = _evaluate_single_query(
            zot=zot,
            query=q,
            judge=judge,
            pool_limit=pool_limit,
            include_abstracts=include_abstracts,
            search_kwargs=search_kwargs,
        )
        result.per_query.append(sqr)

        if sqr.error:
            result.queries_with_errors += 1
            logger.warning(
                f"[{q_idx + 1}/{len(queries)}] {q.query_id}: ERROR {sqr.error}"
            )
            continue

        result.valid_queries += 1
        all_recall_5.append(sqr.recall_at_5)
        all_recall_10.append(sqr.recall_at_10)
        all_recall_20.append(sqr.recall_at_20)
        all_recall_50.append(sqr.recall_at_50)
        all_precision_5.append(sqr.precision_at_5)
        all_precision_10.append(sqr.precision_at_10)
        all_precision_20.append(sqr.precision_at_20)
        all_mrr.append(sqr.mrr)
        all_ndcg_10.append(sqr.ndcg_at_10)

        result.total_pool_entries += sqr.pool_size
        result.total_relevant += sqr.pool_relevant

        logger.info(
            f"[{q_idx + 1}/{len(queries)}] {q.query_id}: "
            f"R@10={sqr.recall_at_10:.1%}, "
            f"P@10={sqr.precision_at_10:.1%}, "
            f"relevant={sqr.pool_relevant}/{sqr.pool_judged}"
        )

    # Aggregate over valid (non-error) queries only
    n_valid = result.valid_queries
    if n_valid > 0:
        result.recall_at_5 = sum(all_recall_5) / n_valid
        result.recall_at_10 = sum(all_recall_10) / n_valid
        result.recall_at_20 = sum(all_recall_20) / n_valid
        result.recall_at_50 = sum(all_recall_50) / n_valid
        result.precision_at_5 = sum(all_precision_5) / n_valid
        result.precision_at_10 = sum(all_precision_10) / n_valid
        result.precision_at_20 = sum(all_precision_20) / n_valid
        result.mrr = sum(all_mrr) / n_valid
        result.ndcg_at_10 = sum(all_ndcg_10) / n_valid

    # By category
    cat_results: dict[str, dict] = {}
    for sqr in result.per_query:
        cat = sqr.category or "unknown"
        if cat not in cat_results:
            cat_results[cat] = {
                "count": 0,
                "valid_count": 0,
                "error_count": 0,
                "recall_at_5": 0.0,
                "recall_at_10": 0.0,
                "recall_at_20": 0.0,
                "recall_at_50": 0.0,
                "precision_at_5": 0.0,
                "precision_at_10": 0.0,
                "precision_at_20": 0.0,
                "mrr": 0.0,
                "ndcg_at_10": 0.0,
                "pool_relevant": 0,
                "pool_total": 0,
            }
        stats = cat_results[cat]
        stats["count"] += 1
        if sqr.error:
            stats["error_count"] += 1
            continue

        stats["valid_count"] += 1
        stats["recall_at_5"] += sqr.recall_at_5
        stats["recall_at_10"] += sqr.recall_at_10
        stats["recall_at_20"] += sqr.recall_at_20
        stats["recall_at_50"] += sqr.recall_at_50
        stats["precision_at_5"] += sqr.precision_at_5
        stats["precision_at_10"] += sqr.precision_at_10
        stats["precision_at_20"] += sqr.precision_at_20
        stats["mrr"] += sqr.mrr
        stats["ndcg_at_10"] += sqr.ndcg_at_10
        stats["pool_relevant"] += sqr.pool_relevant
        stats["pool_total"] += sqr.pool_size

    for _cat, stats in cat_results.items():
        c = stats["valid_count"]
        for metric in [
            "recall_at_5", "recall_at_10", "recall_at_20", "recall_at_50",
            "precision_at_5", "precision_at_10", "precision_at_20",
            "mrr", "ndcg_at_10",
        ]:
            stats[metric] = round(stats[metric] / c, 4) if c > 0 else 0.0
    result.by_category = cat_results

    result.elapsed_seconds = time.time() - t0
    return result


# ── Single query evaluation ──────────────────────────────────────────────


def _evaluate_single_query(
    zot: ZoteroClient,
    query: RecallEvalQuery,
    judge: LLMJudge,
    pool_limit: int = 50,
    include_abstracts: bool = True,
    preset_hits: list[PaperHit] | None = None,
    search_kwargs: dict | None = None,
) -> SingleQueryRecallResult:
    """Evaluate recall for a single query.

    1. Run full search pipeline with large top_k → pool
    2. Ensure expected_item_keys are in pool
    3. Judge relevance for each pool entry
    4. Compute metrics
    """
    from research_core.rag.retriever import Retriever

    sqr = SingleQueryRecallResult(
        query_id=query.query_id,
        query_text=query.query_text,
        category=query.category,
        pool_size=0,
        pool_relevant=0,
        pool_judged=0,
        recall_at_5=0.0,
        recall_at_10=0.0,
        recall_at_20=0.0,
        recall_at_50=0.0,
        precision_at_5=0.0,
        precision_at_10=0.0,
        precision_at_20=0.0,
        mrr=0.0,
        ndcg_at_10=0.0,
        first_relevant_rank=0,
    )

    try:
        t_query = time.time()

        # ── Step 1: Run search with large pool ──
        if preset_hits is None:
            from research_core.tools.search import search_papers

            # Create retriever for pool construction
            retriever = Retriever()
            hits = search_papers(
                query=query.query_text,
                zot=zot,
                retriever=retriever,
                limit=pool_limit,
                expand_context=False,
                expand_neighbors=False,
                diversity_weight=0.4,
                **(search_kwargs or {}),
            )
        else:
            hits = preset_hits[:pool_limit]

        # ── Step 2: Build pool from search results ──
        pool: dict[str, PoolEntry] = {}

        # Track ranks for each result
        for rank, hit in enumerate(hits, start=1):
            entry = PoolEntry(
                key=hit.key,
                title=hit.title,
                tags=hit.tags,
                abstract=hit.paper_abstract if include_abstracts else "",
            )
            _assign_pool_rank(entry, rank)
            pool[hit.key] = entry

            if rank <= 5:
                sqr.top5_keys.append(hit.key)
            if rank <= 10:
                sqr.top10_keys.append(hit.key)
            if rank <= 20:
                sqr.top20_keys.append(hit.key)

        # ── Step 3: Inject expected_item_keys if missing from pool ──
        for expected_key in query.expected_item_keys:
            if expected_key not in pool:
                try:
                    item = zot.get_item(expected_key)
                    pool[expected_key] = PoolEntry(
                        key=item.key,
                        title=item.title,
                        tags=item.tags,
                        abstract=(item.abstract if include_abstracts and item.abstract else ""),
                    )
                except Exception as e:
                    logger.debug(f"Could not fetch expected item {expected_key}: {e}")

        # ── Step 4: Judge relevance ──
        pool_list = list(pool.values())
        sqr.pool_size = len(pool_list)

        judge_input = [
            {"key": p.key, "title": p.title, "tags": p.tags, "abstract": p.abstract}
            for p in pool_list
        ]

        judgments = judge.judge_query(query.query_text, judge_input)
        sqr.pool_judged = len(judgments)

        # Apply judgments to pool entries
        relevant_keys: set[str] = set()
        for p in pool_list:
            if judgments.get(p.key, False):
                p.relevant = True
                relevant_keys.add(p.key)
            else:
                p.relevant = False

        sqr.pool_relevant = len(relevant_keys)
        sqr.relevant_keys = list(relevant_keys)

        # ── Step 5: Compute metrics ──

        # Build ordered key list from search hits (up to pool_limit)
        all_retrieved_keys = [h.key for h in hits]

        # Recall / Precision at each cutoff
        def _ranked_relevant(k: int) -> list[str]:
            return [key for key in all_retrieved_keys[:k] if key in relevant_keys]

        def _recall_at(k: int) -> float:
            if sqr.pool_relevant == 0:
                return 1.0  # edge case: no relevant items in pool
            return len(_ranked_relevant(k)) / sqr.pool_relevant

        def _precision_at(k: int) -> float:
            if k == 0:
                return 0.0
            return len(_ranked_relevant(k)) / k

        sqr.recall_at_5 = _recall_at(5)
        sqr.recall_at_10 = _recall_at(10)
        sqr.recall_at_20 = _recall_at(20)
        sqr.recall_at_50 = _recall_at(50)
        sqr.precision_at_5 = _precision_at(5)
        sqr.precision_at_10 = _precision_at(10)
        sqr.precision_at_20 = _precision_at(20)

        # MRR: reciprocal rank of first relevant result
        sqr.mrr = 0.0
        sqr.first_relevant_rank = 0
        for rank, key in enumerate(all_retrieved_keys, start=1):
            if key in relevant_keys:
                sqr.mrr = 1.0 / rank
                sqr.first_relevant_rank = rank
                break

        # NDCG@10
        sqr.ndcg_at_10 = _ndcg(all_retrieved_keys, relevant_keys, 10)

        sqr.latency_ms = (time.time() - t_query) * 1000

    except Exception as e:
        sqr.error = str(e)
        logger.warning(f"Query '{query.query_id}' failed: {e}")
        import traceback
        logger.debug(traceback.format_exc())

    return sqr


# ── NDCG helper ──────────────────────────────────────────────────────────


def _ndcg(retrieved_keys: list[str], relevant_keys: set[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at k."""
    if not relevant_keys:
        return 1.0
    relevance = [1.0 if key in relevant_keys else 0.0 for key in retrieved_keys[:k]]
    actual_dcg = _dcg(relevance, k)
    ideal_n = min(len(relevant_keys), k)
    ideal = [1.0] * ideal_n + [0.0] * (k - ideal_n)
    ideal_dcg = _dcg(ideal, k)
    if ideal_dcg == 0:
        return 0.0
    return actual_dcg / ideal_dcg


def _dcg(scores: list[float], k: int) -> float:
    dcg = 0.0
    for i, s in enumerate(scores[:k]):
        dcg += (2 ** s - 1) / math.log2(i + 2)
    return dcg


# ── Assign rank helper ────────────────────────────────────────────────────


def _assign_pool_rank(entry: PoolEntry, rank: int) -> None:
    """Record at which rank cutoffs this entry was retrieved."""
    if rank <= 5:
        entry.rank_at_5 = rank
    if rank <= 10:
        entry.rank_at_10 = rank
    if rank <= 20:
        entry.rank_at_20 = rank
    if rank <= 50:
        entry.rank_at_50 = rank


# ── Multi-system pooling ablation ──────────────────────────────────────────

_ABLATION_METRICS = [
    "recall_at_5", "recall_at_10", "recall_at_20", "recall_at_50",
    "precision_at_5", "precision_at_10", "precision_at_20", "mrr", "ndcg_at_10",
]


def _sqr_from_ranked(
    q: RecallEvalQuery,
    ranked_keys: list[str],
    relevant: set[str],
    pool_size: int,
    pool_judged: int,
) -> SingleQueryRecallResult:
    """Build a SingleQueryRecallResult from ranked keys + shared relevance set."""
    pool_rel = len(relevant)

    def _cnt(k: int) -> int:
        return sum(1 for key in ranked_keys[:k] if key in relevant)

    def _rec(k: int, n: int) -> float:
        return (n / pool_rel) if pool_rel else 1.0

    def _prec(k: int, n: int) -> float:
        return n / k if k else 0.0

    n5, n10, n20, n50 = _cnt(5), _cnt(10), _cnt(20), _cnt(50)
    mrr, first = 0.0, 0
    for i, key in enumerate(ranked_keys, 1):
        if key in relevant:
            mrr, first = 1.0 / i, i
            break

    return SingleQueryRecallResult(
        query_id=q.query_id, query_text=q.query_text, category=q.category,
        pool_size=pool_size, pool_relevant=pool_rel, pool_judged=pool_judged,
        recall_at_5=_rec(5, n5), recall_at_10=_rec(10, n10),
        recall_at_20=_rec(20, n20), recall_at_50=_rec(50, n50),
        precision_at_5=_prec(5, n5), precision_at_10=_prec(10, n10),
        precision_at_20=_prec(20, n20), mrr=mrr,
        ndcg_at_10=_ndcg(ranked_keys, relevant, 10), first_relevant_rank=first,
        top5_keys=ranked_keys[:5], top10_keys=ranked_keys[:10],
        top20_keys=ranked_keys[:20], relevant_keys=sorted(relevant),
    )


def evaluate_multi_config(
    zot: ZoteroClient,
    queries: list[RecallEvalQuery],
    judge: LLMJudge,
    runners: dict[str, Any],
    pool_limit: int = 50,
    include_abstracts: bool = True,
    max_union: int = 120,
) -> dict[str, RecallEvalResult]:
    """Multi-system pooling ablation.

    For each query, run every config ``runner`` (callable: query_text -> ranked
    PaperHits), build a UNION pool across all configs, judge it ONCE, then score
    each config against the shared relevance labels.

    Why this instead of per-config pooling: a config judged on its own pool
    cannot see the relevant papers it failed to retrieve (single-system pooling
    bias) and different configs get different denominators, so their metrics are
    not comparable. Judging the union once gives every config the same
    denominator and rewards configs that surface papers others miss. It also
    costs one judge call per query instead of one per config.

    Returns dict {config_name: RecallEvalResult}.
    """
    config_names = list(runners.keys())
    per_config: dict[str, RecallEvalResult] = {
        n: RecallEvalResult(total_queries=len(queries), judge_model=judge.config.model)
        for n in config_names
    }
    acc: dict[str, dict[str, list[float]]] = {
        n: {m: [] for m in _ABLATION_METRICS} for n in config_names
    }
    pool_totals: dict[str, dict[str, int]] = {
        n: {"pool": 0, "relevant": 0} for n in config_names
    }

    for q in queries:
        # ── Step 1: run every config ──
        config_ranked: dict[str, list] = {}
        for name, runner in runners.items():
            try:
                config_ranked[name] = list(runner(q.query_text))[:pool_limit]
            except Exception as e:
                logger.warning(f"[{q.query_id}] config {name} failed: {e}")
                config_ranked[name] = []

        # ── Step 2: build union pool across configs ──
        pool: dict[str, dict[str, Any]] = {}
        for name, hits in config_ranked.items():
            for rank, hit in enumerate(hits, 1):
                entry = pool.setdefault(hit.key, {
                    "title": hit.title, "tags": hit.tags,
                    "abstract": hit.paper_abstract if include_abstracts else "",
                    "best_rank": rank, "ranks": {},
                })
                entry["ranks"][name] = rank
                entry["best_rank"] = min(entry["best_rank"], rank)

        for ek in q.expected_item_keys:
            if ek not in pool:
                try:
                    item = zot.get_item(ek)
                    pool[ek] = {
                        "title": item.title, "tags": item.tags,
                        "abstract": item.abstract if include_abstracts else "",
                        "best_rank": pool_limit + 1, "ranks": {},
                    }
                except Exception as e:
                    logger.debug(f"Could not fetch expected item {ek}: {e}")

        if len(pool) > max_union:
            ordered = sorted(pool.items(), key=lambda kv: kv[1]["best_rank"])
            pool = dict(ordered[:max_union])

        pool_keys = list(pool.keys())
        if not pool_keys:
            for name in config_names:
                per_config[name].queries_with_errors += 1
            continue

        # ── Step 3: judge the union pool ONCE ──
        judge_input = [
            {"key": k, "title": pool[k]["title"], "tags": pool[k]["tags"],
             "abstract": pool[k]["abstract"]}
            for k in pool_keys
        ]
        try:
            judgments = judge.judge_query(q.query_text, judge_input)
        except Exception as e:
            logger.warning(f"[{q.query_id}] judge failed: {e}")
            for name in config_names:
                per_config[name].queries_with_errors += 1
                per_config[name].per_query.append(SingleQueryRecallResult(
                    query_id=q.query_id, query_text=q.query_text, category=q.category,
                    pool_size=0, pool_relevant=0, pool_judged=0,
                    recall_at_5=0, recall_at_10=0, recall_at_20=0, recall_at_50=0,
                    precision_at_5=0, precision_at_10=0, precision_at_20=0,
                    mrr=0, ndcg_at_10=0, first_relevant_rank=0, error=str(e),
                ))
            continue

        relevant: set[str] = {k for k in pool_keys if judgments.get(k, False)}
        pool_size = len(pool_keys)

        # ── Step 4: score each config against the shared labels ──
        for name in config_names:
            ranked_keys = [h.key for h in config_ranked[name]]
            sqr = _sqr_from_ranked(
                q=q, ranked_keys=ranked_keys, relevant=relevant,
                pool_size=pool_size, pool_judged=pool_size,
            )
            per_config[name].per_query.append(sqr)
            per_config[name].valid_queries += 1
            for m in _ABLATION_METRICS:
                acc[name][m].append(getattr(sqr, m))
            pool_totals[name]["pool"] += pool_size
            pool_totals[name]["relevant"] += len(relevant)

    # ── aggregate each config ──
    for name in config_names:
        res = per_config[name]
        n = res.valid_queries
        for m in _ABLATION_METRICS:
            vals = acc[name][m]
            setattr(res, m, round(sum(vals) / n, 4) if n and vals else 0.0)
        res.total_pool_entries = pool_totals[name]["pool"]
        res.total_relevant = pool_totals[name]["relevant"]

    return per_config
