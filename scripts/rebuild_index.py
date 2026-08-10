"""Rebuild ChromaDB index from scratch via chroma server.

Deletes the corrupted collection and re-indexes all papers.
Must be run with the chroma server already started.

Usage:
    python scripts/rebuild_index.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")


def main():
    from research_core.rag.store import get_collection
    from research_core.zotero.client import ZoteroClient
    from research_core.rag.retriever import Retriever
    from research_core.rag.indexer import Indexer
    from research_core.tools.admin import sync_index
    from loguru import logger

    # Force fresh connection to chroma server
    import chromadb

    print("=== Step 1: Reset corrupted collection ===")
    client = chromadb.HttpClient(host="127.0.0.1", port=18000)
    client.heartbeat()
    print(f"  ChromaDB server: OK")

    # Drop the corrupted collection
    try:
        client.delete_collection("research_chunks")
        print("  Deleted corrupted 'research_chunks' collection")
    except Exception as e:
        print(f"  Delete skipped (may not exist): {e}")

    # Clear cached singleton so get_collection() creates fresh
    import research_core.rag.store as store_module
    with store_module._lock:
        store_module._collection = None
        store_module._client = None

    print("  Collection reset complete")

    # Verify: collection should be empty
    cols = client.list_collections()
    print(f"  Remaining collections: {len(cols)}")

    print()
    print("=== Step 2: Rebuild index (sync_index) ===")
    print("  This will re-parse all PDFs and recompute embeddings...")
    t0 = time.time()

    zot = ZoteroClient(library_id="0", local=True)
    indexer = Indexer()
    retriever = Retriever()
    report = sync_index(
        zot=zot, indexer=indexer, retriever=retriever,
        force_rebuild=True,
    )

    elapsed = time.time() - t0
    print(f"\n  Rebuild complete in {elapsed:.0f}s")
    print(f"  Papers indexed: {report.papers_indexed}")
    print(f"  Chunks created: {report.chunks_created}")
    print(f"  Errors: {report.errors}")

    print()
    print("=== Step 3: Verify HNSW files ===")
    import sqlite3
    db = sqlite3.connect(".chroma_db/chroma.sqlite3")
    segs = db.execute(
        "SELECT id, type, scope FROM segments WHERE type LIKE '%hnsw%'"
    ).fetchall()
    for s in segs:
        seg_dir = os.path.join(".chroma_db", s[0])
        if os.path.isdir(seg_dir):
            files = os.listdir(seg_dir)
            print(f"  Segment {s[0][:16]}...: {len(files)} files")
            for f in sorted(files):
                fp = os.path.join(seg_dir, f)
                sz = os.path.getsize(fp)
                print(f"    {f}: {sz:,} bytes")
            # Check dimensionality
            import pickle
            meta_path = os.path.join(seg_dir, "index_metadata.pickle")
            if os.path.exists(meta_path):
                with open(meta_path, "rb") as f:
                    meta = pickle.load(f)
                print(f"    dimensionality: {meta.get('dimensionality')}")
                print(f"    total_elements: {meta.get('total_elements_added')}")
        else:
            print(f"  [!] Segment dir missing: {seg_dir}")

    db.close()

    # Final count
    col = get_collection()
    try:
        count = col.count()
        print(f"\n  Final: {count} chunks indexed ✓")
    except Exception as e:
        print(f"\n  [!] Cannot count: {e}")


if __name__ == "__main__":
    main()
