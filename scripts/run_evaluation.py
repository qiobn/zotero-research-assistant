"""Run evaluation on the current index and optionally compare against a baseline.

Usage:
    python scripts/run_evaluation.py                        # Run eval, print summary
    python scripts/run_evaluation.py --json                 # JSON output
    python scripts/run_evaluation.py --save-baseline        # Save as baseline for later comparison
    python scripts/run_evaluation.py --compare baseline.json  # Compare against saved baseline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_core.rag.evaluation import (
    EvalQuery,
    EvalResult,
    compare_results,
    evaluate_retrieval,
)
from research_core.rag.retriever import Retriever
from research_core.zotero.client import ZoteroClient


def load_queries(path: str) -> list[EvalQuery]:
    """Load evaluation queries from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    queries_data = data.get("queries", data)
    queries: list[EvalQuery] = []
    for i, q in enumerate(queries_data):
        queries.append(EvalQuery(
            query_id=q.get("query_id", f"Q{i:03d}"),
            query_text=q["query_text"],
            expected_item_keys=q.get("expected_item_keys", []),
            category=q.get("category", "direct"),
            difficulty=q.get("difficulty", "medium"),
            notes=q.get("notes", ""),
        ))
    return queries


def main():
    parser = argparse.ArgumentParser(description="RAG retrieval evaluation")
    parser.add_argument("--queries", default="", help="Path to eval queries JSON")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save results as baseline for future comparison")
    parser.add_argument("--compare", default="", help="Path to baseline JSON for comparison")
    parser.add_argument("--persist-dir", default=".chroma_db", help="ChromaDB dir")
    parser.add_argument("--label", default="", help="Label for this evaluation run")
    args = parser.parse_args()

    # Resolve query file path
    if args.queries:
        query_path = args.queries
    else:
        query_path = os.path.join(
            os.path.dirname(__file__), "..", "tests", "eval_queries.json"
        )
    query_path = os.path.abspath(query_path)

    if not os.path.exists(query_path):
        print(f"Error: query file not found: {query_path}")
        print("Run 'python scripts/generate_eval_queries.py' first.")
        sys.exit(1)

    print(f"Loading queries from {query_path}...")
    queries = load_queries(query_path)
    print(f"Loaded {len(queries)} queries")

    print("Connecting to Zotero...")
    zot = ZoteroClient(library_id="0", local=True)
    retriever = Retriever(persist_dir=args.persist_dir)

    label = args.label or f"index-{retriever.count()}chunks"
    print(f"Running evaluation ({retriever.count()} chunks indexed)...")

    result = evaluate_retrieval(
        retriever=retriever,
        zot=zot,
        queries=queries,
        top_k=20,
        baseline_label=label,
    )

    # Output
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.summary())

        # Show worst-performing queries
        failed = [r for r in result.per_query if r.recall_at_10 == 0 and not r.error]
        if failed:
            print(f"\n  [!] {len(failed)} queries with zero recall@10:")
            for r in failed[:5]:
                print(f"    [{r.query_id}] {r.query_text[:80]}")

        # Show best queries
        perfect = [r for r in result.per_query if r.recall_at_10 >= 1.0]
        if perfect:
            print(f"\n  [OK] {len(perfect)} queries with perfect recall@10")

    # Save baseline
    if args.save_baseline:
        baseline_path = os.path.join(
            os.path.dirname(query_path), "eval_baseline.json"
        )
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\nBaseline saved -> {baseline_path}")

    # Compare
    if args.compare:
        if not os.path.exists(args.compare):
            print(f"Error: baseline file not found: {args.compare}")
            sys.exit(1)
        with open(args.compare, encoding="utf-8") as f:
            baseline_data = json.load(f)

        # Reconstruct baseline result (minimal — just metrics)
        baseline = EvalResult(
            total_queries=baseline_data["total_queries"],
            recall_at_5=baseline_data["recall_at_5"],
            recall_at_10=baseline_data["recall_at_10"],
            recall_at_20=baseline_data["recall_at_20"],
            mrr=baseline_data["mrr"],
            ndcg_at_10=baseline_data["ndcg_at_10"],
            per_query=[
                type("_", (), {
                    "recall_at_10": r["recall_at_10"],
                    "query_id": r["query_id"],
                })()
                for r in baseline_data.get("per_query", [])
            ],
            baseline_label=baseline_data.get("baseline_label", "baseline"),
        )

        delta = compare_results(baseline, result)
        print(f"\n  ── Comparison: {baseline.baseline_label} → {label} ──")
        for metric, change in delta.items():
            if metric in ("queries_improved", "queries_regressed"):
                print(f"  {metric}: {change}")
            else:
                print(f"  Δ {metric}: {change}")


if __name__ == "__main__":
    main()
