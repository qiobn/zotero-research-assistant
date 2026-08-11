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

    # Component ablation — isolate BM25 / Dense / CE / MMR contributions
    python scripts/run_recall_evaluation.py --ablation hybrid
    python scripts/run_recall_evaluation.py --ablation-set   # all + comparison
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

from research_core.rag.eval_judge import JudgeConfig, LLMJudge
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


# Component-ablation configs. Each maps to search_papers knobs; the default
# (no --ablation) equals "hybrid_ce_mmr" — the production pipeline.
ABLATIONS: dict[str, dict] = {
    "dense":         {"enable_semantic": True,  "enable_bm25": False, "enable_rerank": False, "diversity_weight": 0.0},
    "bm25":          {"enable_semantic": False, "enable_bm25": True,  "enable_rerank": False, "diversity_weight": 0.0},
    "hybrid":        {"enable_semantic": True,  "enable_bm25": True,  "enable_rerank": False, "diversity_weight": 0.0},
    "hybrid_ce":     {"enable_semantic": True,  "enable_bm25": True,  "enable_rerank": True,  "diversity_weight": 0.0},
    "hybrid_ce_mmr": {"enable_semantic": True,  "enable_bm25": True,  "enable_rerank": True,  "diversity_weight": 0.4},
}
_ABLATION_LABELS = {
    "baseline": "Full pipeline (default)",
    "dense": "Dense only",
    "bm25": "BM25 only",
    "hybrid": "Hybrid (BM25+Dense)",
    "hybrid_ce": "Hybrid + CE",
    "hybrid_ce_mmr": "Hybrid + CE + MMR (default)",
}


def _detect_lang(text: str) -> str:
    """Classify a query as Chinese ('zh') or English ('en') by CJK ratio."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return "zh" if (cjk / max(len(text), 1)) > 0.1 else "en"


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def summarize_by_language(result: RecallEvalResult) -> dict:
    """Aggregate metrics separately for zh and en queries."""
    by_lang: dict[str, dict[str, list[float]]] = {
        "zh": {"r10": [], "r20": [], "mrr": [], "ndcg": []},
        "en": {"r10": [], "r20": [], "mrr": [], "ndcg": []},
    }
    for r in result.per_query:
        if r.error:
            continue
        lang = _detect_lang(r.query_text)
        by_lang[lang]["r10"].append(r.recall_at_10)
        by_lang[lang]["r20"].append(r.recall_at_20)
        by_lang[lang]["mrr"].append(r.mrr)
        by_lang[lang]["ndcg"].append(r.ndcg_at_10)
    out: dict[str, dict] = {}
    for lang, metrics in by_lang.items():
        n = len(metrics["r10"])
        out[lang] = {
            "n": n,
            "R@10": round(_mean(metrics["r10"]), 4),
            "R@20": round(_mean(metrics["r20"]), 4),
            "MRR": round(_mean(metrics["mrr"]), 4),
            "NDCG@10": round(_mean(metrics["ndcg"]), 4),
        }
    return out


def print_language_breakdown(result: RecallEvalResult) -> None:
    print("\n  ── By Query Language ──")
    for lang, s in summarize_by_language(result).items():
        print(f"    {lang:<3} (n={s['n']}): R@10={s['R@10']:.1%} "
              f"R@20={s['R@20']:.1%} MRR={s['MRR']:.3f} NDCG@10={s['NDCG@10']:.3f}")


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
    ablation: str = "",
    ablation_set: bool = False,
) -> None:
    """Run the recall evaluation and print results.

    Args:
        ablation: Single component-ablation config name to run (dense / bm25 /
                  hybrid / hybrid_ce / hybrid_ce_mmr). Empty → full pipeline.
        ablation_set: Run every ablation config and print a comparison table.
    """
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

    # Resolve which ablations to run
    if ablation_set:
        ablations: list[tuple[str, dict]] = list(ABLATIONS.items())
    elif ablation:
        if ablation not in ABLATIONS:
            print(f"Error: unknown ablation '{ablation}'. "
                  f"Available: {', '.join(ABLATIONS)}")
            sys.exit(1)
        ablations = [(ablation, ABLATIONS[ablation])]
    else:
        ablations = [("baseline", {})]

    if ablation_set:
        # Multi-system pooling: judge the UNION pool once per query, score every
        # config against the same relevance labels. One judge call per query
        # (not per config) and no single-system pooling bias.
        from research_core.rag.recall_eval import evaluate_multi_config
        from research_core.rag.retriever import Retriever
        from research_core.tools.search import search_papers

        retriever = Retriever()
        for model in models_to_run:
            config = JudgeConfig.from_env()
            config.model = model
            judge = LLMJudge(config)

            # NB: every ABLATIONS config carries its own diversity_weight, so
            # do NOT pass it here as well (duplicate kwarg) — **kw supplies it.
            runners = {
                name: (lambda q, kw=kw: search_papers(
                    query=q, zot=zot, retriever=retriever, limit=pool_size,
                    expand_context=False, expand_neighbors=False, **kw,
                ))
                for name, kw in ABLATIONS.items()
            }
            results = evaluate_multi_config(
                zot=zot, queries=queries, judge=judge,
                runners=runners, pool_limit=pool_size,
            )

            for name, result in results.items():
                label = _ABLATION_LABELS.get(name, name)
                print(f"\n{'='*60}")
                print(f"  Config: {label:<28} Judge: {model}")
                print(f"{'='*60}")
                print()
                print(result.summary())
                print_language_breakdown(result)
                _save_result(result, model, pool_size, name)

            _print_ablation_comparison(results, model)
        return

    rows: list[tuple[str, str, RecallEvalResult]] = []
    for name, search_kwargs in ablations:
        label = _ABLATION_LABELS.get(name, name)
        for model in models_to_run:
            print(f"\n{'='*60}")
            print(f"  Config: {label:<28} Judge: {model}")
            print(f"{'='*60}")

            config = JudgeConfig.from_env()
            config.model = model
            judge = LLMJudge(config)

            result = evaluate_recall(
                zot=zot,
                queries=queries,
                judge=judge,
                pool_limit=pool_size,
                search_kwargs=search_kwargs,
            )
            rows.append((name, model, result))

            print()
            print(result.summary())
            print_language_breakdown(result)

            if output_json:
                print()
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print_query_detail(result)

            _save_result(result, model, pool_size, name)

    # Comparison table when multiple configs ran under the same judge
    if len(ablations) > 1:
        for model in models_to_run:
            model_rows = [(n, r) for (n, m, r) in rows if m == model]
            if len(model_rows) < 2:
                continue
            _print_ablation_comparison(dict(model_rows), model)


def _save_result(
    result: RecallEvalResult, model: str, pool_size: int, name: str
) -> str:
    """Persist a RecallEvalResult to tests/eval_results/ and return the path."""
    model_slug = model.replace("/", "-").replace(".", "-")
    out_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "eval_results")
    os.makedirs(out_dir, exist_ok=True)
    out_name = f"recall_{model_slug}_pool{pool_size}"
    if name != "baseline":
        out_name += f"_ablation_{name}"
    out_path = os.path.join(out_dir, out_name + ".json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"  Results saved → {out_path}")
    return out_path


def _print_ablation_comparison(
    results: dict[str, RecallEvalResult], model: str
) -> None:
    """Print a per-config comparison table with a zh/en split."""
    print(f"\n{'='*60}")
    print("  ABLATION COMPARISON (union-pool judge)")
    print(f"{'='*60}")
    print(f"\n  Judge: {model}")
    print(f"  {'Config':<28} {'R@5':<8} {'R@10':<8} {'R@20':<8} "
          f"{'P@10':<8} {'MRR':<7} {'NDCG@10':<8} {'zh R@10':<8} {'en R@10':<8}")
    print(f"  {'-'*106}")
    for name, r in results.items():
        label = _ABLATION_LABELS.get(name, name)
        lang = summarize_by_language(r)
        zh_r10 = f"{lang['zh']['R@10']:.1%}" if lang["zh"]["n"] else "-"
        en_r10 = f"{lang['en']['R@10']:.1%}" if lang["en"]["n"] else "-"
        print(f"  {label:<28} {r.recall_at_5:<8.1%} {r.recall_at_10:<8.1%} "
              f"{r.recall_at_20:<8.1%} {r.precision_at_10:<8.1%} "
              f"{r.mrr:<7.3f} {r.ndcg_at_10:<8.3f} {zh_r10:<8} {en_r10:<8}")


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
        "--ablation", default="",
        help="Component ablation to run: dense, bm25, hybrid, hybrid_ce, "
             "hybrid_ce_mmr (empty = full pipeline)",
    )
    parser.add_argument(
        "--ablation-set", action="store_true",
        help="Run all ablations and print a per-component comparison table",
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
        ablation=args.ablation,
        ablation_set=args.ablation_set,
    )


if __name__ == "__main__":
    main()
