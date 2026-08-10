"""Run deterministic multi-call retrieval strategy evaluation.

This script evaluates pre-authored multi-call query plans against the current
index using the same LLM-as-judge recall harness as `run_recall_evaluation.py`.
It keeps the MCP server architecture unchanged: the server still exposes
single-call retrieval only; this script exists solely to make strategy tests
reproducible.

Usage:
    python scripts/run_strategy_eval.py \
        --queries tests/eval_queries_user.json \
        --variants tests/strategy_variants_7call.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from research_core.rag.eval_judge import JudgeConfig, LLMJudge
from research_core.rag.recall_eval import RecallEvalQuery, RecallEvalResult
from research_core.rag.strategy_eval import (
    StrategyPlan,
    default_output_path,
    execute_strategy_plan,
    load_strategy_plans,
)
from research_core.zotero.client import ZoteroClient


def load_queries(path: str) -> list[RecallEvalQuery]:
    """Load recall queries from JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    queries_data = data.get("queries", data)
    queries: list[RecallEvalQuery] = []
    for i, q in enumerate(queries_data):
        queries.append(
            RecallEvalQuery(
                query_id=q.get("query_id", f"Q{i:03d}"),
                query_text=q["query_text"],
                category=q.get("category", "direct"),
                difficulty=q.get("difficulty", "medium"),
                expected_item_keys=q.get("expected_item_keys", []),
                notes=q.get("notes", ""),
            )
        )
    return queries


def evaluate_strategy(
    *,
    zot: ZoteroClient,
    queries: list[RecallEvalQuery],
    plans: dict[str, StrategyPlan],
    judge: LLMJudge,
    limit: int,
) -> tuple[RecallEvalResult, dict[str, dict]]:
    """Evaluate pre-authored strategy plans with the same recall metrics."""
    from research_core.rag.recall_eval import _evaluate_single_query  # reuse metrics path
    from research_core.rag.retriever import Retriever

    retriever = Retriever()
    result = RecallEvalResult(
        total_queries=len(queries),
        judge_model=judge.config.model,
    )

    all_recall_5: list[float] = []
    all_recall_10: list[float] = []
    all_recall_20: list[float] = []
    all_recall_50: list[float] = []
    all_precision_5: list[float] = []
    all_precision_10: list[float] = []
    all_precision_20: list[float] = []
    all_mrr: list[float] = []
    all_ndcg_10: list[float] = []
    strategy_trace: dict[str, dict] = {}

    for q_idx, query in enumerate(queries):
        plan = plans.get(query.query_id)
        if plan is None:
            from research_core.rag.recall_eval import SingleQueryRecallResult

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
                error="No strategy plan provided for query",
            )
            result.per_query.append(sqr)
            result.queries_with_errors += 1
            continue

        execution = execute_strategy_plan(plan, zot=zot, retriever=retriever, limit=limit)
        strategy_trace[query.query_id] = {
            **execution.to_dict(),
            "strategy": plan.strategy,
            "note": plan.note,
        }

        sqr = _evaluate_single_query(
            zot=zot,
            query=query,
            judge=judge,
            pool_limit=limit,
            include_abstracts=True,
            preset_hits=execution.merged_hits,
        )
        result.per_query.append(sqr)

        if sqr.error:
            result.queries_with_errors += 1
            continue

        result.valid_queries += 1
        result.total_pool_entries += sqr.pool_size
        result.total_relevant += sqr.pool_relevant
        all_recall_5.append(sqr.recall_at_5)
        all_recall_10.append(sqr.recall_at_10)
        all_recall_20.append(sqr.recall_at_20)
        all_recall_50.append(sqr.recall_at_50)
        all_precision_5.append(sqr.precision_at_5)
        all_precision_10.append(sqr.precision_at_10)
        all_precision_20.append(sqr.precision_at_20)
        all_mrr.append(sqr.mrr)
        all_ndcg_10.append(sqr.ndcg_at_10)

        print(
            f"[{q_idx + 1}/{len(queries)}] {query.query_id}: "
            f"R@10={sqr.recall_at_10:.1%}, P@10={sqr.precision_at_10:.1%}, "
            f"plan={''.join(sorted(execution.slot_queries))}"
        )

    if result.valid_queries > 0:
        n = result.valid_queries
        result.recall_at_5 = sum(all_recall_5) / n
        result.recall_at_10 = sum(all_recall_10) / n
        result.recall_at_20 = sum(all_recall_20) / n
        result.recall_at_50 = sum(all_recall_50) / n
        result.precision_at_5 = sum(all_precision_5) / n
        result.precision_at_10 = sum(all_precision_10) / n
        result.precision_at_20 = sum(all_precision_20) / n
        result.mrr = sum(all_mrr) / n
        result.ndcg_at_10 = sum(all_ndcg_10) / n

    by_category: dict[str, dict] = {}
    for sqr in result.per_query:
        cat = sqr.category or "unknown"
        stats = by_category.setdefault(
            cat,
            {
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
            },
        )
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

    for _cat, stats in by_category.items():
        c = stats["valid_count"]
        for metric in (
            "recall_at_5",
            "recall_at_10",
            "recall_at_20",
            "recall_at_50",
            "precision_at_5",
            "precision_at_10",
            "precision_at_20",
            "mrr",
            "ndcg_at_10",
        ):
            stats[metric] = round(stats[metric] / c, 4) if c > 0 else 0.0
    result.by_category = by_category

    return result, strategy_trace


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    parser = argparse.ArgumentParser(
        description="Evaluate deterministic multi-call retrieval strategy plans"
    )
    parser.add_argument(
        "--queries",
        default="",
        help="Path to eval queries JSON (default: tests/eval_queries_user.json)",
    )
    parser.add_argument(
        "--variants",
        required=True,
        help="Path to JSON file describing per-query multi-call variants",
    )
    parser.add_argument(
        "--judge",
        default="openai-gpt-5-4",
        help="Judge model name (default: openai-gpt-5-4)",
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=50,
        help="Merged pool size to evaluate (default: 50)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full result JSON to stdout",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional explicit JSON output path",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    query_path = args.queries or str(project_root / "tests" / "eval_queries_user.json")
    query_path = os.path.abspath(query_path)
    variants_path = os.path.abspath(args.variants)

    if not os.path.exists(query_path):
        raise SystemExit(f"Query file not found: {query_path}")
    if not os.path.exists(variants_path):
        raise SystemExit(f"Variant file not found: {variants_path}")

    queries = load_queries(query_path)
    plans = load_strategy_plans(variants_path)

    print(f"Loaded {len(queries)} queries from {query_path}")
    print(f"Loaded {len(plans)} strategy plans from {variants_path}")
    print("Connecting to Zotero...")
    zot = ZoteroClient(library_id="0", local=True)

    config = JudgeConfig.from_env()
    config.model = args.judge
    judge = LLMJudge(config)

    result, trace = evaluate_strategy(
        zot=zot,
        queries=queries,
        plans=plans,
        judge=judge,
        limit=args.pool_size,
    )

    print()
    print(result.summary())

    payload = result.to_dict()
    payload["strategy"] = {
        "name": "7call_rrf",
        "variants_file": variants_path,
        "pool_size": args.pool_size,
    }
    payload["strategy_trace"] = trace

    if args.json:
        print()
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    out_path = args.output or default_output_path(
        str(project_root),
        variants_path,
        args.judge,
        args.pool_size,
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Results saved → {out_path}")


if __name__ == "__main__":
    main()
