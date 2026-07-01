"""Generate evaluation queries from indexed paper metadata.

Produces a JSON file of {query_text, expected_item_keys, category} entries
by sampling indexed papers and constructing queries from their titles,
abstracts, and keywords. Designed to be reviewed and corrected by a human
before being used as a golden evaluation set.

Usage:
    python scripts/generate_eval_queries.py                    # Generate 75 queries
    python scripts/generate_eval_queries.py --count 50         # 50 queries
    python scripts/generate_eval_queries.py --output my.json   # Custom output path
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_core.rag.retriever import Retriever
from research_core.zotero.client import ZoteroClient


def _make_title_query(item: object) -> dict:
    """Type A (direct): search by title — should return the paper itself."""
    title = (item.title or "").strip()
    # Use first meaningful sentence of the title
    query = title.split(":")[0].split("：")[0].split("—")[0].strip()
    if len(query) < 15:
        query = title[:80]
    return {
        "query_text": query,
        "expected_item_keys": [item.key],
        "category": "direct",
        "difficulty": "easy",
        "notes": f"Title search for: {title[:80]}",
    }


def _make_abstract_query(item: object) -> dict:
    """Type A (direct): use first sentence of abstract — should find the paper."""
    abstract = getattr(item, "abstract", "") or ""
    if not abstract or len(abstract) < 20:
        return None
    # Take first sentence of abstract
    first_sent = abstract.split(".")[0].split("。")[0].strip()
    if len(first_sent) < 20:
        return None
    return {
        "query_text": first_sent[:200],
        "expected_item_keys": [item.key],
        "category": "direct",
        "difficulty": "easy",
        "notes": f"Abstract search for: {(item.title or '')[:60]}",
    }


def _make_topic_query(item: object) -> dict:
    """Type A (direct): paraphrase the topic — should still find the paper."""
    title = (item.title or "").strip()
    # Extract key noun phrases from title
    words = title.replace(":", " ").replace("：", " ").split()
    if len(words) < 4:
        return None
    topic_words = [w for w in words if len(w) > 3 and w.lower()
                   not in ("study", "research", "based", "using", "analysis",
                           "evidence", "case", "data", "model", "method",
                           "approach", "toward", "towards")]
    if len(topic_words) < 2:
        return None
    query = " ".join(topic_words[:6])
    return {
        "query_text": query,
        "expected_item_keys": [item.key],
        "category": "direct",
        "difficulty": "medium",
        "notes": f"Topic search for: {title[:60]}",
    }


def _make_cross_document_query(
    item_a: object, item_b: object, zot: ZoteroClient
) -> dict | None:
    """Type B (cross_document): compare two papers — both should appear."""
    title_a = (item_a.title or "").strip()
    title_b = (item_b.title or "").strip()

    # Build a comparison query from titles
    # Strategy 1: find common topic words
    stop_words = {"the", "of", "in", "and", "on", "a", "an", "for", "to", "with",
                  "based", "using", "study", "research", "analysis", "evidence",
                  "case", "data", "model", "method", "approach", "toward",
                  "urban", "city", "cities", "spatial", "public", "service",
                  "community", "health", "accessibility", "elderly", "older"}
    words_a = set(w.lower() for w in title_a.replace(":", " ").replace("：", " ").split()
                  if len(w) > 3 and w.lower() not in stop_words)
    words_b = set(w.lower() for w in title_b.replace(":", " ").replace("：", " ").split()
                  if len(w) > 3 and w.lower() not in stop_words)
    common = words_a & words_b

    # Strategy 2: if not enough common, pick distinctive words from each
    if len(common) >= 1:
        topic = " and ".join(list(common)[:2])
        query = f"How do different studies approach {topic} in terms of methodology and findings?"
    else:
        # Pick 1-2 key words from each title
        key_a = [w for w in words_a if len(w) > 5][:2]
        key_b = [w for w in words_b if len(w) > 5][:2]
        if not key_a or not key_b:
            return None
        query = (
            f"Compare research methodologies and findings across studies on "
            f"{' '.join(key_a[:1])} and {' '.join(key_b[:1])}"
        )

    return {
        "query_text": query[:200],
        "expected_item_keys": [item_a.key, item_b.key],
        "category": "cross_document",
        "difficulty": "hard",
        "notes": f"Cross-doc: {title_a[:40]} vs {title_b[:40]}",
    }


def _make_no_answer_query(item: object) -> dict:
    """Type C (no_answer): ask about something the paper doesn't cover."""
    title = (item.title or "").strip()
    fake_topics = [
        "machine learning in healthcare",
        "cryptocurrency market analysis",
        "deep sea microbial ecosystems",
        "ancient Roman architecture",
        "quantum computing applications",
    ]
    fake = random.choice(fake_topics)
    return {
        "query_text": f"What are the latest developments in {fake}?",
        "expected_item_keys": [],
        "category": "no_answer",
        "difficulty": "medium",
        "notes": f"No-answer query — paper about: {title[:50]}",
    }


def generate_queries(
    zot: ZoteroClient,
    retriever: Retriever,
    count: int = 75,
    seed: int = 42,
) -> list[dict]:
    """Generate a balanced set of evaluation queries.

    Distribution: ~50% direct, ~25% cross_document, ~15% no_answer, ~10% contradiction.
    """
    random.seed(seed)
    indexed_keys = list(retriever.list_indexed_items())
    if len(indexed_keys) < 5:
        print("Error: need at least 5 indexed papers for evaluation query generation.")
        return []

    # Fetch items
    items_by_key: dict[str, object] = {}
    for key in indexed_keys:
        try:
            items_by_key[key] = zot.get_item(key)
        except Exception:
            pass
    items = list(items_by_key.values())
    random.shuffle(items)

    queries: list[dict] = []
    used_keys: set[str] = set()

    n_direct = int(count * 0.50)
    n_cross = int(count * 0.25)
    n_noans = int(count * 0.15)
    # n_contra = count - n_direct - n_cross - n_noans  # contradiction later

    # Type A: Direct hit queries
    direct_generators = [_make_title_query, _make_abstract_query, _make_topic_query]
    for item in items:
        if len(queries) >= n_direct:
            break
        gen = random.choice(direct_generators)
        q = gen(item)
        if q and q["expected_item_keys"][0] not in used_keys:
            queries.append(q)
            used_keys.add(q["expected_item_keys"][0])

    # Type B: Cross-document queries
    items_with_key = [it for it in items if it.key in items_by_key]
    for _ in range(n_cross * 3):
        if len(queries) - n_direct >= n_cross:
            break
        if len(items_with_key) < 2:
            break
        a, b = random.sample(items_with_key, 2)
        q = _make_cross_document_query(a, b, zot)
        if q:
            queries.append(q)

    # Type C: No-answer queries
    for item in items:
        if len(queries) - n_direct - n_cross >= n_noans:
            break
        q = _make_no_answer_query(item)
        if q:
            queries.append(q)

    # Fill remaining to reach count with direct queries
    for item in items:
        if len(queries) >= count:
            break
        q = _make_title_query(item)
        if q and q["expected_item_keys"][0] not in used_keys:
            queries.append(q)
            used_keys.add(q["expected_item_keys"][0])

    return queries[:count]


def main():
    parser = argparse.ArgumentParser(description="Generate RAG evaluation queries")
    parser.add_argument("--count", type=int, default=75, help="Number of queries")
    parser.add_argument("--output", default="", help="Output JSON path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--persist-dir", default=".chroma_db", help="ChromaDB dir")
    args = parser.parse_args()

    output = args.output or os.path.join(
        os.path.dirname(__file__), "..", "tests", "eval_queries.json"
    )
    output = os.path.abspath(output)

    print("Connecting...")
    zot = ZoteroClient(library_id="0", local=True)
    retriever = Retriever(persist_dir=args.persist_dir)

    print(f"Generating {args.count} evaluation queries (seed={args.seed})...")
    queries = generate_queries(zot, retriever, count=args.count, seed=args.seed)

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(
            {
                "_meta": {
                    "description": "RAG evaluation queries — review and correct before use",
                    "generated_from": f"{len(retriever.list_indexed_items())} indexed papers",
                    "total_queries": len(queries),
                    "instructions": (
                        "Review each query. Fix query_text if unclear. "
                        "Verify expected_item_keys are correct. Add 'contradiction' "
                        "category queries manually. Remove bad queries."
                    ),
                },
                "queries": queries,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # Summary
    cats = {}
    for q in queries:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    print(f"Generated {len(queries)} queries → {output}")
    for cat, cnt in sorted(cats.items()):
        print(f"  {cat}: {cnt}")
    print("\n*** Review and correct this file before using as a golden evaluation set. ***")


if __name__ == "__main__":
    main()
