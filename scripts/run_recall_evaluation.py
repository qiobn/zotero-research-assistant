"""Run recall evaluation with LLM-as-judge.

Measures true recall (not just precision) by using an external LLM to
determine the full set of relevant papers for each query via the
pooling method.

Usage:
    # Default: run with gpt5.4 judge on eval_queries_user.json
    python scripts/run_recall_evaluation.py

    # With specific judge model
    python scripts/run_recall_evaluation.py --judge deepseek-openai-deepseek-v4-pro

    # Both judges, multiple pool sizes
    python scripts/run_recall_evaluation.py --all-judges --pool-sizes 30 50

    # JSON output
    python scripts/run_recall_evaluation.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env before any project imports
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from research_core.rag.eval_judge import LLMJudge
from research_core.rag.recall_eval import (
    RecallEvalQuery,
    RecallEvalResult,
    evaluate_recall,
)
from research_core.zotero.client import ZoteroClient


def load_queries(path: str) -> list[RecallEvalQuery]:
    """Load evaluation queries from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    queries_data = data.get("queries", data)
    queries: list[RecallEvalQuery] = []
    for i, q in enumerate(queries_data):
        queries.append(RecallEvalQuery(
            query_id=q.get("query_id", f"Q{i:03d}"),
            query_text=q["query_text"],
            category=q.get("category", "direct"),
            difficulty=q.get("difficulty", "medium"),
            expected_item_keys=q.get("expected_item_keys", []),
            notes=q.get("notes", ""),
        ))
    return queries


def print_query_detail(result: RecallEvalResult) -> None:
    """Print per-query breakdown."""
    print("\n  ── Per-Query Breakdown ──")
    print(f"  {'ID':<10} {'Category':<16} {'Rel/Pool':<10} "
          f"{'R@5':<8} {'R@10':<8} {'R@20':<8} {'P@10':<8} {'MRR':<6}")
    print(f"  {'-'*66}")
    for r in result.per_query:
        if r.error:
            print(f"  {r.query_id:<10} {'ERROR: ' + r.error:<56}")
            continue
        print(f"  {r.query_id:<10} {r.category:<16} "
              f"{r.pool_relevant}/{r.pool_size:<6} "
              f"{r.recall_at_5:<8.1%} {r.recall_at_10:<8.1%} "
              f"{r.recall_at_20:<8.1%} {r.precision_at_10:<8.1%} "
              f"{r.mrr:<6.3f}")
    print()


def run_eval(
    query_path: str,
    judge_model: str = "openai-gpt-5-4",
    pool_size: int = 50,
    all_judges: bool = False,
    output_json: bool = False,
) -> None:
    """Run the recall evaluation and print results."""
    # Load queries
    if not os.path.exists(query_path):
        # Try default path
        default_path = os.path.join(
            os.path.dirname(__file__), "..", "tests", "eval_queries_user.json"
        )
        query_path = os.path.abspath(default_path)
        if not os.path.exists(query_path):
            default_path2 = os.path.join(
                os.path.dirname(__file__), "..", "tests", "eval_queries.json"
            )
            query_path = os.path.abspath(default_path2)

    if not os.path.exists(query_path):
        print(f"Error: query file not found")
        sys.exit(1)

    print(f"Loading queries from {query_path}...")
    queries = load_queries(query_path)
    print(f"Loaded {len(queries)} queries ({pool_size=})")

    print("Connecting to Zotero...")
    zot = ZoteroClient(library_id="0", local=True)

    models_to_run = [judge_model]
    if all_judges:
        models_to_run = [
            "openai-gpt-5-4",
            "deepseek-openai-deepseek-v4-pro",
        ]

    for model in models_to_run:
        print(f"\n{'='*60}")
        print(f"  Judge: {model}")
        print(f"{'='*60}")

        from research_core.rag.eval_judge import JudgeConfig
        config = JudgeConfig.from_env()
        config.model = model
        judge = LLMJudge(config)

        result = evaluate_recall(
            zot=zot,
            queries=queries,
            judge=judge,
            pool_limit=pool_size,
        )

        print()
        print(result.summary())

        if output_json:
            print()
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print_query_detail(result)

        # Save results
        model_slug = model.replace("/", "-").replace(".", "-")
        out_dir = os.path.join(
            os.path.dirname(__file__), "..", "tests", "eval_results"
        )
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(
            out_dir,
            f"recall_{model_slug}_pool{pool_size}.json",
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"  Results saved → {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="RAG Recall Evaluation with LLM-as-Judge"
    )
    parser.add_argument(
        "--queries", default="",
        help="Path to eval queries JSON (default: tests/eval_queries_user.json)",
    )
    parser.add_argument(
        "--judge", default="openai-gpt-5-4",
        help="Judge model name (default: openai-gpt-5-4)",
    )
    parser.add_argument(
        "--pool-size", type=int, default=50,
        help="Number of results to retrieve for pool (default: 50)",
    )
    parser.add_argument(
        "--all-judges", action="store_true",
        help="Run with both gpt5.4 and deepseek-pro judges",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output per-query results as JSON",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only load queries and build pool, do not call judge API",
    )
    args = parser.parse_args()

    run_eval(
        query_path=args.queries,
        judge_model=args.judge,
        pool_size=args.pool_size,
        all_judges=args.all_judges,
        output_json=args.json,
    )


if __name__ == "__main__":
    main()
