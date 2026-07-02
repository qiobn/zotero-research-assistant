"""Systematic recall evaluation — Recall@K, MRR, NDCG for RAG retrieval.

Provides a reusable evaluation harness that works with any Retriever +
ZoteroClient pair. Designed for both one-off testing and A/B comparison.

Usage:
    from research_core.rag.evaluation import evaluate_retrieval, EvalResult
    result = evaluate_retrieval(retriever, zot, queries)
    print(result.summary())
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from loguru import logger

from research_core.rag.retriever import Retriever
from research_core.zotero.client import ZoteroClient


@dataclass
class EvalQuery:
    """A single evaluation query."""
    query_id: str
    query_text: str
    expected_item_keys: list[str]   # papers that SHOULD be retrieved
    category: str = "direct"        # direct / cross_document / no_answer / contradiction
    difficulty: str = "medium"      # easy / medium / hard
    notes: str = ""


@dataclass
class SingleQueryResult:
    """Result for one evaluation query."""
    query_id: str
    query_text: str
    category: str
    hits_at_5: list[str] = field(default_factory=list)
    hits_at_10: list[str] = field(default_factory=list)
    hits_at_20: list[str] = field(default_factory=list)
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    recall_at_20: float = 0.0
    reciprocal_rank: float = 0.0
    first_hit_rank: int = 0  # 1-indexed, 0 = no hit
    total_expected: int = 0
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class EvalResult:
    """Aggregated evaluation results."""
    total_queries: int = 0
    queries_with_errors: int = 0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    recall_at_20: float = 0.0
    mrr: float = 0.0           # Mean Reciprocal Rank
    ndcg_at_10: float = 0.0
    per_query: list[SingleQueryResult] = field(default_factory=list)
    by_category: dict[str, dict] = field(default_factory=dict)
    baseline_label: str = ""
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            f"Eval: {self.total_queries} queries ({self.queries_with_errors} errors)",
            f"  Recall@5:  {self.recall_at_5:.3f}",
            f"  Recall@10: {self.recall_at_10:.3f}",
            f"  Recall@20: {self.recall_at_20:.3f}",
            f"  MRR:       {self.mrr:.3f}",
            f"  NDCG@10:   {self.ndcg_at_10:.3f}",
        ]
        if self.by_category:
            lines.append("  By category:")
            for cat, stats in self.by_category.items():
                lines.append(
                    f"    {cat}: R@10={stats['recall_at_10']:.3f}, "
                    f"MRR={stats['mrr']:.3f}, n={stats['count']}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total_queries": self.total_queries,
            "queries_with_errors": self.queries_with_errors,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "recall_at_20": self.recall_at_20,
            "mrr": self.mrr,
            "ndcg_at_10": self.ndcg_at_10,
            "by_category": self.by_category,
            "baseline_label": self.baseline_label,
            "elapsed_seconds": self.elapsed_seconds,
            "per_query": [
                {
                    "query_id": r.query_id,
                    "category": r.category,
                    "recall_at_10": r.recall_at_10,
                    "mrr": r.reciprocal_rank,
                    "first_hit_rank": r.first_hit_rank,
                    "error": r.error,
                }
                for r in self.per_query
            ],
        }


def _dcg(scores: list[float], k: int) -> float:
    """Discounted Cumulative Gain at k."""
    dcg = 0.0
    for i, s in enumerate(scores[:k]):
        dcg += (2 ** s - 1) / math.log2(i + 2)  # i+2 because log2(1)=0
    return dcg


def _ndcg(retrieved_keys: list[str], expected_keys: set[str], k: int) -> float:
    """Normalized DCG at k. Relevance = 1 if in expected set, else 0."""
    if not expected_keys:
        return 1.0
    relevance = [1.0 if key in expected_keys else 0.0 for key in retrieved_keys[:k]]
    actual_dcg = _dcg(relevance, k)
    # Ideal: all expected items ranked first
    ideal_n = min(len(expected_keys), k)
    ideal = [1.0] * ideal_n + [0.0] * (k - ideal_n)
    ideal_dcg = _dcg(ideal, k)
    if ideal_dcg == 0:
        return 0.0
    return actual_dcg / ideal_dcg


def evaluate_retrieval(
    retriever: Retriever,
    zot: ZoteroClient,
    queries: list[EvalQuery],
    top_k: int = 20,
    baseline_label: str = "",
) -> EvalResult:
    """Run evaluation across all queries.

    For each query, searches with semantic retrieval and computes
    recall at multiple cutoffs, MRR, and NDCG.

    Args:
        retriever: Configured Retriever instance.
        zot: ZoteroClient (used to verify expected items exist).
        queries: List of evaluation queries with expected item keys.
        top_k: Maximum number of results to retrieve per query.
        baseline_label: Label for this evaluation run (e.g. "v0.2.0-baseline").
    """
    result = EvalResult(
        total_queries=len(queries),
        baseline_label=baseline_label,
    )
    all_recall_5: list[float] = []
    all_recall_10: list[float] = []
    all_recall_20: list[float] = []
    all_mrr: list[float] = []
    all_ndcg_10: list[float] = []

    t0 = time.time()

    for i, q in enumerate(queries):
        sqr = SingleQueryResult(
            query_id=q.query_id,
            query_text=q.query_text,
            category=q.category,
            total_expected=len(q.expected_item_keys),
        )

        try:
            t_query = time.time()
            results = retriever.search(
                q.query_text,
                n_results=top_k,
                include_references=False,
            )
            sqr.latency_ms = (time.time() - t_query) * 1000

            retrieved_keys = [r.item_key for r in results]
            expected = set(q.expected_item_keys)

            # Recall at each cutoff
            def _recall_at(k: int) -> float:
                if not expected:
                    return 1.0
                hits = len(set(retrieved_keys[:k]) & expected)
                return hits / len(expected)

            sqr.recall_at_5 = _recall_at(5)
            sqr.recall_at_10 = _recall_at(10)
            sqr.recall_at_20 = _recall_at(20)
            sqr.hits_at_5 = retrieved_keys[:5]
            sqr.hits_at_10 = retrieved_keys[:10]
            sqr.hits_at_20 = retrieved_keys[:20]

            # MRR
            sqr.reciprocal_rank = 0.0
            for rank, key in enumerate(retrieved_keys, start=1):
                if key in expected:
                    sqr.reciprocal_rank = 1.0 / rank
                    sqr.first_hit_rank = rank
                    break

            # NDCG
            sqr.ndcg_10 = _ndcg(retrieved_keys, expected, 10)

        except Exception as e:
            sqr.error = str(e)
            result.queries_with_errors += 1
            logger.warning(f"Query '{q.query_id}' failed: {e}")

        all_recall_5.append(sqr.recall_at_5)
        all_recall_10.append(sqr.recall_at_10)
        all_recall_20.append(sqr.recall_at_20)
        all_mrr.append(sqr.reciprocal_rank)
        all_ndcg_10.append(sqr.ndcg_10)
        result.per_query.append(sqr)

    result.elapsed_seconds = time.time() - t0

    # Aggregate
    n = len(queries)
    if n > 0:
        result.recall_at_5 = sum(all_recall_5) / n
        result.recall_at_10 = sum(all_recall_10) / n
        result.recall_at_20 = sum(all_recall_20) / n
        result.mrr = sum(all_mrr) / n
        result.ndcg_at_10 = sum(all_ndcg_10) / n

    # By category
    cat_results: dict[str, dict] = {}
    for sqr in result.per_query:
        cat = sqr.category or "unknown"
        if cat not in cat_results:
            cat_results[cat] = {
                "count": 0, "recall_at_5": 0.0, "recall_at_10": 0.0,
                "recall_at_20": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0,
            }
        cat_results[cat]["count"] += 1
        cat_results[cat]["recall_at_5"] += sqr.recall_at_5
        cat_results[cat]["recall_at_10"] += sqr.recall_at_10
        cat_results[cat]["recall_at_20"] += sqr.recall_at_20
        cat_results[cat]["mrr"] += sqr.reciprocal_rank
        cat_results[cat]["ndcg_at_10"] += sqr.ndcg_10

    for cat, stats in cat_results.items():
        c = stats["count"]
        for metric in ["recall_at_5", "recall_at_10", "recall_at_20", "mrr", "ndcg_at_10"]:
            stats[metric] = round(stats[metric] / c, 4) if c > 0 else 0.0
    result.by_category = cat_results

    return result


def compare_results(before: EvalResult, after: EvalResult) -> dict:
    """Compare two evaluation runs, returning the delta for each metric."""
    def _delta(new: float, old: float) -> str:
        d = new - old
        sign = "+" if d >= 0 else ""
        return f"{sign}{d:.3f}"

    return {
        "recall_at_5": _delta(after.recall_at_5, before.recall_at_5),
        "recall_at_10": _delta(after.recall_at_10, before.recall_at_10),
        "recall_at_20": _delta(after.recall_at_20, before.recall_at_20),
        "mrr": _delta(after.mrr, before.mrr),
        "ndcg_at_10": _delta(after.ndcg_at_10, before.ndcg_at_10),
        "queries_improved": sum(
            1 for a, b in zip(after.per_query, before.per_query)
            if a.recall_at_10 > b.recall_at_10
        ),
        "queries_regressed": sum(
            1 for a, b in zip(after.per_query, before.per_query)
            if a.recall_at_10 < b.recall_at_10
        ),
    }
