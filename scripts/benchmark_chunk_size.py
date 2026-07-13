r"""Benchmark different chunk sizes on retrieval quality.

Rebuilds a small test index for each chunk size and runs evaluation queries
to find the optimal target_chunk_size for bge-m3 on this corpus.

Usage:
    # Quick test with 10 papers (recommended for first run)
    python scripts/benchmark_chunk_size.py --papers 10

    # More thorough with 30 papers
    python scripts/benchmark_chunk_size.py --papers 30

    # With custom chunk sizes to test
    python scripts/benchmark_chunk_size.py --papers 20 --sizes 400,600,900,1200

What it does:
    1. Picks N papers from Zotero (most recently added with PDFs)
    2. For each chunk size: re-parses PDFs → re-embeds → creates separate
       ChromaDB collection → runs eval queries → records metrics
    3. Reports Recall@5/10/20, MRR, NDCG@10 for each chunk size
    4. Recommends the best size

Requires: Zotero desktop running, indexed PDFs available.
Safe: uses temporary collections, does NOT touch your main index.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def get_papers_with_pdfs(zot, n: int = 20) -> list[dict]:
    """Get N most recent papers that have PDF attachments."""
    items = zot.get_recent(limit=min(n * 3, 100))
    papers = []
    seen = set()
    for item in items:
        if len(papers) >= n:
            break
        if item.key in seen:
            continue
        seen.add(item.key)
        pdfs = zot.get_pdf_paths_for_keys([item.key])
        if item.key in pdfs and os.path.exists(pdfs[item.key]):
            papers.append(
                {"key": item.key, "title": item.title, "pdf_path": pdfs[item.key]}
            )
    return papers


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark chunk sizes on retrieval quality"
    )
    parser.add_argument(
        "--papers", type=int, default=20, help="Number of papers to test with"
    )
    parser.add_argument(
        "--sizes", type=str, default="400,600,900,1200",
        help="Comma-separated target chunk sizes to test",
    )
    parser.add_argument(
        "--persist-dir", default=".chroma_db",
        help="ChromaDB directory (test collections created inside)",
    )
    parser.add_argument(
        "--queries", default="",
        help="Path to eval queries JSON (default: tests/eval_queries.json)",
    )
    args = parser.parse_args()

    chunk_sizes = [int(s.strip()) for s in args.sizes.split(",")]

    # Resolve paths
    project_root = Path(__file__).resolve().parent.parent
    queries_path = args.queries or str(project_root / "tests" / "eval_queries.json")
    if not os.path.exists(queries_path):
        print(f"ERROR: Eval queries not found at {queries_path}")
        print("Specify with --queries PATH")
        sys.exit(1)

    persist_dir = str(project_root / args.persist_dir)

    # ── Initialize Zotero ──
    from dotenv import load_dotenv
    load_dotenv()

    from research_core.zotero.client import ZoteroClient
    zot = ZoteroClient(
        library_id=os.getenv("ZOTERO_LIBRARY_ID", "0"),
        library_type=os.getenv("ZOTERO_LIBRARY_TYPE", "user"),
        api_key=os.getenv("ZOTERO_API_KEY", ""),
        local=os.getenv("ZOTERO_LOCAL", "true").lower() == "true",
    )

    # ── Collect papers ──
    print(f"Collecting up to {args.papers} papers with PDFs...")
    papers = get_papers_with_pdfs(zot, n=args.papers)
    if not papers:
        print("ERROR: No papers with PDFs found. Ensure Zotero is running.")
        sys.exit(1)
    print(f"  Found {len(papers)} papers\n")

    # ── Parse and chunk PDFs ──
    from research_core.parsers.chunker import chunk_text
    from research_core.parsers.pdf import extract_pdf_text

    print("Parsing PDFs (reused across all chunk sizes)...")
    all_page_texts: dict[str, list] = {}  # item_key → list[PageText]
    for p in papers:
        try:
            pages = extract_pdf_text(p["pdf_path"])
            all_page_texts[p["key"]] = pages
        except Exception as e:
            print(f"  WARNING: Failed to parse {p['title'][:60]}...: {e}")

    paper_count = len(all_page_texts)
    print(f"  Parsed {paper_count} papers\n")

    # ── Load eval queries ──
    with open(queries_path, encoding="utf-8") as f:
        queries_data = json.load(f)
    eval_queries = queries_data.get("queries", queries_data)
    print(f"Loaded {len(eval_queries)} eval queries\n")

    # ── Run benchmarks ──
    results: list[dict] = []

    for cs in chunk_sizes:
        label = f"chunk_{cs}"
        test_collection = f"bench_chunk_{cs}"
        test_dir = os.path.join(persist_dir, f"_bench_{cs}")

        print(f"{'='*60}")
        print(f"Testing chunk_size={cs} chars (max={cs*2})...")
        print(f"{'='*60}")

        # Clean previous test dir
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
        os.makedirs(test_dir, exist_ok=True)

        # ── Chunk all papers with this size ──
        t0 = time.time()
        all_chunks: list[dict] = []  # [{text, item_key, title, page, chunk_idx, metadata}]
        import hashlib

        for key, pages in all_page_texts.items():
            try:
                chunks = chunk_text(
                    pages,
                    target_chunk_size=cs,
                    max_chunk_size=cs * 2,
                    min_chunk_size=max(60, cs // 6),
                    overlap_chars=max(50, cs // 6),
                )
                for i, chunk in enumerate(chunks):
                    all_chunks.append(
                        {
                            "id": f"{key}:{i}",
                            "text": chunk.text,
                            "item_key": key,
                            "chunk_idx": i,
                            "page_start": chunk.page_start,
                            "page_end": chunk.page_end,
                            "metadata": {
                                "item_key": key,
                                "chunk_idx": i,
                                "page_start": chunk.page_start,
                                "page_end": chunk.page_end,
                                "title": "",
                                "section_type": getattr(chunk, "section_type", "unknown"),
                                "section_heading": getattr(chunk, "section_heading", ""),
                                "language": getattr(chunk, "language", "en"),
                                "quality_flag": getattr(chunk, "quality_flag", "good"),
                            },
                        }
                    )
            except Exception as e:
                print(f"  WARNING: Failed to chunk {key}: {e}")

        chunk_time = time.time() - t0

        if not all_chunks:
            print(f"  ERROR: No chunks produced for size={cs}")
            results.append(
                {"chunk_size": cs, "chunks": 0, "error": "No chunks produced"}
            )
            continue

        # ── Embed and index ──
        t0 = time.time()
        from research_core.rag.embedding import get_embedding_function

        ef = get_embedding_function()

        import chromadb
        client = chromadb.PersistentClient(path=test_dir)
        # Pass embedding_function to prevent ChromaDB from downloading default model
        collection = client.create_collection(
            name="bench_chunks",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

        # Batch insert to avoid OOM — ONNX INT8 needs small batches on CPU
        batch_size = 20
        for b in range(0, len(all_chunks), batch_size):
            batch = all_chunks[b : b + batch_size]
            collection.add(
                ids=[c["id"] for c in batch],
                documents=[c["text"] for c in batch],
                metadatas=[c["metadata"] for c in batch],
            )
            if b % 200 == 0 and b > 0:
                print(f"    ... {b}/{len(all_chunks)} chunks indexed")

        index_time = time.time() - t0
        total_time = chunk_time + index_time

        n_chunks = len(all_chunks)
        avg_chunk_len = sum(len(c["text"]) for c in all_chunks) / max(n_chunks, 1)
        print(
            f"  {n_chunks} chunks, avg {avg_chunk_len:.0f} chars, "
            f"chunk={chunk_time:.1f}s, embed+index={index_time:.1f}s, "
            f"total={total_time:.1f}s"
        )

        # ── Run evaluation ──
        from research_core.rag.evaluation import (
            EvalQuery,
            evaluate_retrieval,
        )
        from research_core.rag.retriever import Retriever

        retriever = Retriever(
            persist_dir=test_dir,
            collection_name="bench_chunks",
            collection=collection,
        )

        queries = [
            EvalQuery(
                query_id=q.get("query_id", f"Q{i:03d}"),
                query_text=q["query_text"],
                expected_item_keys=q.get("expected_item_keys", []),
                category=q.get("category", "direct"),
                difficulty=q.get("difficulty", "medium"),
            )
            for i, q in enumerate(eval_queries)
        ]

        eval_result = evaluate_retrieval(
            retriever, zot, queries, top_k=20, baseline_label=label,
        )
        eval_time = time.time() - t0 - index_time  # approximate

        result = {
            "chunk_size": cs,
            "n_chunks": n_chunks,
            "avg_chunk_chars": round(avg_chunk_len, 0),
            "chunk_time_s": round(chunk_time, 1),
            "index_time_s": round(index_time, 1),
            "total_time_s": round(total_time, 1),
            "recall_at_5": round(eval_result.recall_at_5, 4),
            "recall_at_10": round(eval_result.recall_at_10, 4),
            "recall_at_20": round(eval_result.recall_at_20, 4),
            "mrr": round(eval_result.mrr, 4),
            "ndcg_at_10": round(eval_result.ndcg_at_10, 4),
        }
        results.append(result)

        # Clean up: release references (dir cleanup at end to avoid file locks)
        del collection, client, retriever, ef
        import gc
        gc.collect()

    # ── Report ──
    print(f"\n{'='*80}")
    print("CHUNK SIZE BENCHMARK RESULTS")
    print(f"{'='*80}")
    print(
        f"{'Size':>6} | {'Chunks':>7} | {'AvgLen':>7} | "
        f"{'R@5':>7} | {'R@10':>7} | {'R@20':>7} | "
        f"{'MRR':>7} | {'NDCG@10':>8} | {'Time':>7}"
    )
    print("-" * 80)

    valid_for_r5 = [r for r in results if "recall_at_5" in r]
    if valid_for_r5:
        best_r5 = max(valid_for_r5, key=lambda r: r["recall_at_5"])
        for r in results:
            if "error" in r:
                print(f"  {r['chunk_size']:>5} | ERROR: {r['error']}")
                continue
            flag = " <-- BEST R@5" if r is best_r5 else ""
        print(
            f"  {r['chunk_size']:>5} | {r['n_chunks']:>7} | "
            f"{r['avg_chunk_chars']:>6.0f} | "
            f"{r['recall_at_5']:>7.4f} | {r['recall_at_10']:>7.4f} | "
            f"{r['recall_at_20']:>7.4f} | "
            f"{r['mrr']:>7.4f} | {r['ndcg_at_10']:>8.4f} | "
            f"{r['total_time_s']:>6.1f}s{flag}"
        )
    print("-" * 80)
    print()

    # Recommend
    valid = [r for r in results if "recall_at_5" in r]
    if valid:
        best = max(valid, key=lambda r: r["recall_at_5"])
        print(f"Recommendation: chunk_size = {best['chunk_size']}")
        print(
            f"  R@5={best['recall_at_5']:.4f}, R@10={best['recall_at_10']:.4f}, "
            f"MRR={best['mrr']:.4f}"
        )

        # Compare to current default (600)
        current = next((r for r in valid if r["chunk_size"] == 600), None)
        if current and best["chunk_size"] != 600:
            delta_r5 = (best["recall_at_5"] - current["recall_at_5"]) / max(
                current["recall_at_5"], 0.001
            )
            print(
                f"  vs current default (600): R@5 delta = {delta_r5:+.1%}"
            )

    # Save results
    out_path = project_root / "scripts" / "chunk_benchmark_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "n_papers": paper_count,
                "n_queries": len(eval_queries),
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    try:
        main()
    finally:
        # Clean up temp benchmark dirs
        import time
        time.sleep(0.5)
        for cs in [400, 600, 900, 1200]:  # all possible test sizes
            test_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", ".chroma_db", f"_bench_{cs}",
            )
            test_dir = os.path.normpath(test_dir)
            if os.path.exists(test_dir):
                try:
                    shutil.rmtree(test_dir)
                except Exception:
                    pass
