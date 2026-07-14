"""Read latest retrieval logs and write a human-readable summary.

Usage:
    python scripts/show_retrieval.py                  # Latest 5 searches
    python scripts/show_retrieval.py -n 1             # Latest 1 search
    python scripts/show_retrieval.py -w               # Write to .chroma_db/retrieval_log.txt
    python scripts/show_retrieval.py -w -f            # Write and follow (wait for new logs)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _shorten(text: str, width: int = 80) -> str:
    """Truncate text to width with ellipsis."""
    text = str(text)
    return text[:width] + "..." if len(text) > width else text


def _format_trace(entry: dict, show_results: bool = True) -> str:
    """Format a single log entry as readable text."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"Trace ID: {entry.get('trace_id', '?')}")
    lines.append(f"Time:     {entry.get('timestamp', '?')}")
    lines.append(f"Query:    {entry.get('query', '?')}")
    lines.append(f"Strategy: {entry.get('strategy', '?')}")

    # Expanded queries
    exp = entry.get("expanded_queries", [])
    if exp:
        parts = []
        for e in exp:
            txt = e.get("text", "")[:50]
            wt = e.get("weight", 1)
            parts.append(f"{txt}(w={wt})")
        lines.append(f"Expanded: {', '.join(parts)}")

    lines.append("")
    lines.append("  Candidate counts:")
    for key, label in [
        ("candidate_keyword_n", "Zotero keyword"),
        ("candidate_bm25_n", "BM25"),
        ("candidate_semantic_n", "Semantic"),
        ("candidate_merged_n", "Merged"),
    ]:
        val = entry.get(key, 0)
        if val:
            lines.append(f"    {label}: {val}")

    lines.append("")
    lines.append("  Latency breakdown:")
    for key, label in [
        ("latency_keyword_ms", "Keyword"),
        ("latency_bm25_ms", "BM25"),
        ("latency_semantic_ms", "Semantic"),
        ("latency_rerank_ms", "Reranker"),
        ("latency_mmr_ms", "MMR"),
        ("latency_total_ms", "TOTAL"),
    ]:
        val = entry.get(key, 0.0)
        if val:
            bar = "█" * max(1, int(val / entry.get("latency_total_ms", 1) * 40)) if entry.get("latency_total_ms") else ""
            lines.append(f"    {label:12s}  {val:8.0f}ms  {bar}")

    lines.append(f"  Reranker:  {entry.get('reranker_model', 'N/A')} (enabled={entry.get('reranker_enabled', False)})")
    lines.append(f"  Fallback:  {entry.get('fallback_triggered', False)} ({entry.get('fallback_count', 0)} items)")

    # Results
    results = entry.get('results', [])
    if results and show_results:
        lines.append("")
        lines.append(f"  Results ({len(results)} items):")
        for i, r in enumerate(results):
            title = _shorten(r.get("title", "?"), 70)
            score = r.get("score", 0)
            rank = r.get("rank", i + 1)
            src = r.get("source", "?")
            lines.append(f"    #{rank:<2d} [{src:8s}] {score:.4f}  {title}")

    lines.append("")
    return "\n".join(lines)


def read_logs(persist_dir: str = ".chroma_db", n: int = 5) -> list[dict]:
    """Read last N log entries from the JSONL file."""
    log_path = os.path.join(persist_dir, "_retrieval_log.jsonl")
    if not os.path.exists(log_path):
        print(f"[!] Log file not found: {log_path}")
        print("    Run a search_papers query first to generate logs.")
        return []

    entries: list[dict] = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return entries[-n:]


def main():
    parser = argparse.ArgumentParser(description="Read latest retrieval logs")
    parser.add_argument("-n", type=int, default=5, help="Number of latest traces to show (default: 5)")
    parser.add_argument("-w", "--write", action="store_true", help="Write to .chroma_db/retrieval_log.txt")
    parser.add_argument("-f", "--follow", action="store_true", help="Follow mode — wait for new logs")
    parser.add_argument("--show-results", action="store_true", default=True, help="Show result items (default: on)")
    parser.add_argument("--persist-dir", default=".chroma_db", help="ChromaDB persist directory")
    args = parser.parse_args()

    if args.follow:
        # Watch mode — poll for new entries
        log_path = os.path.join(args.persist_dir, "_retrieval_log.jsonl")
        last_size = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        print(f"Following {log_path}... (Ctrl+C to stop)")
        try:
            while True:
                if os.path.exists(log_path):
                    current_size = os.path.getsize(log_path)
                    if current_size > last_size:
                        entries = read_logs(args.persist_dir, n=1)
                        for e in entries:
                            text = _format_trace(e, show_results=args.show_results)
                            print(text)
                        last_size = current_size
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    entries = read_logs(args.persist_dir, n=args.n)
    if not entries:
        return

    lines: list[str] = []
    lines.append(f"Retrieval Logs — Latest {len(entries)} searches")
    lines.append(f"File: {os.path.join(args.persist_dir, '_retrieval_log.jsonl')}")
    lines.append("")
    for entry in reversed(entries):
        lines.append(_format_trace(entry, show_results=args.show_results))

    output = "\n".join(lines)

    if args.write:
        out_path = os.path.join(args.persist_dir, "retrieval_log.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Written to {out_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
