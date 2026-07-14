"""Bilingual enrichment — add [Title_EN:] [Keywords_EN:] to existing chunks.

Reads every chunk from ChromaDB, re-runs _enrich_chunk_text() with NMT
translation enabled, and updates the collection in-place.

Usage:
    python scripts/bilingual_enrich.py              # dry-run (preview)
    python scripts/bilingual_enrich.py --apply      # actually update
    python scripts/bilingual_enrich.py --apply --rebuild-bm25  # update + BM25
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import defaultdict

# Suppress verbose transformers warnings during batch inference
logging.getLogger("transformers").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from research_core.rag.indexer import (
    _enrich_chunk_text,
    _is_chinese_text,
    _translate_paper_metadata,
)
from research_core.rag.store import get_collection, sync_lock


# Regex to recover keywords from an existing enrichment header.
# The header looks like: "[Keywords: tag1, tag2] [Title: ...] [Section: ...]"
_KW_RE = re.compile(r"\[Keywords:\s*(.*?)\](?=\s*\[|$)")


def _extract_keywords(doc_text: str) -> str:
    """Extract the keywords string from an already-enriched chunk document."""
    m = _KW_RE.search(doc_text)
    if m:
        return m.group(1).strip()
    return ""


def _strip_enrichment(doc_text: str) -> str:
    """Return the raw chunk text (everything after the first newline)."""
    idx = doc_text.find("\n")
    if idx != -1:
        return doc_text[idx + 1:]
    return doc_text


def bilingual_enrich(persist_dir: str, apply: bool = False) -> dict:
    """Re-run chunk enrichment with NMT translation on all existing chunks.

    Returns stats about what would be / was updated.
    """
    collection = get_collection(persist_dir, "research_chunks")

    logger.info("Scanning all chunks from ChromaDB...")
    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []
    offset = 0
    page_size = 500

    while True:
        result = collection.get(limit=page_size, offset=offset,
                                include=["documents", "metadatas"])
        batch_ids = result.get("ids", [])
        if not batch_ids:
            break
        all_ids.extend(batch_ids)
        all_docs.extend(result.get("documents", []) or [])
        all_metas.extend(result.get("metadatas", []) or [])
        offset += page_size

    logger.info(f"Loaded {len(all_ids)} total chunks")

    # Group by item_key
    by_paper: dict[str, list[int]] = defaultdict(list)
    for i, cid in enumerate(all_ids):
        item_key = cid.rsplit(":", 1)[0] if ":" in cid else ""
        by_paper[item_key].append(i)

    stats = {
        "total_chunks": len(all_ids),
        "total_papers": len(by_paper),
        "chinese_papers": 0,
        "chunks_updated": 0,
        "chunks_skipped_already_en": 0,
        "chunks_skipped_non_chinese": 0,
        "errors": 0,
    }

    updates: list[dict] = []

    for item_key, indices in by_paper.items():
        # Paper-level metadata (same for all chunks)
        meta0 = all_metas[indices[0]]
        title = meta0.get("title", "")
        year = meta0.get("year", 0)
        if not title or not _is_chinese_text(title):
            stats["chunks_skipped_non_chinese"] += len(indices)
            continue  # skip non-Chinese papers entirely

        stats["chinese_papers"] += 1

        # Extract keywords from first chunk's header (all chunks share metadata)
        keywords = _extract_keywords(all_docs[indices[0]])

        # Pre-translate (cached by _translate_paper_metadata)
        trans = _translate_paper_metadata(title, keywords)

        if not trans.get("title_en"):
            stats["chunks_skipped_already_en"] += len(indices)
            continue  # translation failed or no output → skip

        # Rebuild each chunk's doc with bilingual enrichment
        for idx in indices:
            raw_text = _strip_enrichment(all_docs[idx])
            old_doc = all_docs[idx]

            # Reconstruct enriched text with NMT translation
            # We need section from metadata for the enrichment
            section = all_metas[idx].get("section", "content")

            # Build enrichment parts the same way _enrich_chunk_text does
            parts: list[str] = []

            if keywords:
                short_kw = keywords[:200] if len(keywords) > 200 else keywords
                parts.append(f"[Keywords: {short_kw}]")

            if title:
                short_title = title[:150] if len(title) > 150 else title
                ctx = f"Title: {short_title}"
                if year:
                    ctx += f" ({year})"
                parts.append(f"[{ctx}]")

            if section and section != "content":
                short_section = section[:120] if len(section) > 120 else section
                parts.append(f"[Section: {short_section}]")

            if trans.get("title_en"):
                parts.append(f"[Title_EN: {trans['title_en'][:150]}]")
            if trans.get("keywords_en"):
                parts.append(f"[Keywords_EN: {trans['keywords_en'][:200]}]")

            new_doc = " ".join(parts) + "\n" + raw_text

            if new_doc != old_doc:
                updates.append({"id": all_ids[idx], "document": new_doc})

    stats["chunks_updated"] = len(updates)

    if not apply:
        logger.info(
            f"DRY-RUN: {stats['chunks_updated']} chunks would be updated "
            f"({stats['chinese_papers']} Chinese papers, "
            f"{stats['chunks_skipped_non_chinese']} non-Chinese chunks skipped)"
        )
        for u in updates[:3]:
            logger.info(f"  Sample: {u['id']} — doc update pending")
        if stats['chunks_updated']:
            logger.info(f"  ... and {stats['chunks_updated'] - 3} more" if stats['chunks_updated'] > 3 else "")
        return stats

    # ── Apply ──
    if not updates:
        logger.info("Nothing to update — all chunks already bilingual-enriched")
        return stats

    # Batch update: ChromaDB update() re-embeds documents automatically.
    # The ONNX INT8 bge-m3 model's self-attention intermediate buffers scale
    # as O(batch × seq_len² × dim). On 8GB systems, batch_size > 3 OOMs.
    batch_size = 3
    total_batches = (len(updates) + batch_size - 1) // batch_size
    with sync_lock:
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            ids = [u["id"] for u in batch]
            docs = [u["document"] for u in batch]
            collection.update(ids=ids, documents=docs)
            logger.info(f"  Updated batch {i // batch_size + 1}/{total_batches}: {len(batch)} chunks")

    logger.info(f"Applied bilingual enrichment to {stats['chunks_updated']} chunks")

    return stats


def rebuild_bm25(persist_dir: str) -> int:
    """Rebuild the BM25 sparse index from the updated collection."""
    from research_core.rag.bm25_index import BM25Index
    collection = get_collection(persist_dir, "research_chunks")
    bm25 = BM25Index(persist_dir)
    count = bm25.build_from_collection(collection)
    logger.info(f"BM25 index rebuilt: {count} chunks")
    return count


def main():
    parser = argparse.ArgumentParser(description="Add NMT bilingual enrichment to existing index")
    parser.add_argument("--apply", action="store_true", help="Apply updates (default: dry-run)")
    parser.add_argument("--rebuild-bm25", action="store_true", help="Also rebuild BM25 index after update")
    parser.add_argument("--persist-dir", default=".chroma_db", help="ChromaDB persist directory")
    args = parser.parse_args()

    stats = bilingual_enrich(args.persist_dir, apply=args.apply)

    if args.apply and args.rebuild_bm25 and stats["chunks_updated"]:
        rebuild_bm25(args.persist_dir)

    print()
    print("=" * 55)
    print(f"  Total chunks scanned:    {stats['total_chunks']}")
    print(f"  Total papers:            {stats['total_papers']}")
    print(f"  Chinese papers:          {stats['chinese_papers']}")
    print(f"  Chunks updated:          {stats['chunks_updated']}")
    print(f"  Non-Chinese skipped:     {stats['chunks_skipped_non_chinese']}")
    print(f"  Already bilingual:       {stats['chunks_skipped_already_en']}")
    print(f"  Errors:                  {stats['errors']}")
    print("=" * 55)
    if not args.apply:
        print("  DRY-RUN — pass --apply to apply changes")
    print()


if __name__ == "__main__":
    main()
