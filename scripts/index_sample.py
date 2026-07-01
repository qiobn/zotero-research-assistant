"""Quick test indexing — sample N papers for rapid RAG pipeline testing.

Usage:
    python scripts/index_sample.py              # default: 20 papers
    python scripts/index_sample.py --count 10   # 10 papers
    python scripts/index_sample.py --random     # random sample instead of first N
"""

from __future__ import annotations

import argparse
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

from research_core.parsers.chunker import chunk_text
from research_core.parsers.pdf import PageText, extract_pdf
from research_core.parsers.text_cleaner import clean_text
from research_core.rag.indexer import Indexer
from research_core.zotero.client import ZoteroClient


def main(count: int = 20, random_sample: bool = False):
    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    zot = ZoteroClient(library_id="0", local=True)
    indexer = Indexer(persist_dir=persist_dir)

    # Get all items with PDFs
    logger.info("Fetching Zotero items...")
    all_items = zot.search_items("", limit=500)
    logger.info(f"Found {len(all_items)} total items in library")

    # Find items with PDF attachments
    all_keys = [item.key for item in all_items]
    pdf_paths = zot.get_pdf_paths_for_keys(all_keys)
    keys_with_pdf = [k for k, v in pdf_paths.items() if v]
    logger.info(f"Items with local PDFs: {len(keys_with_pdf)}")

    if not keys_with_pdf:
        logger.error("No items with local PDF attachments found!")
        return

    # Sample
    if random_sample:
        sampled = random.sample(keys_with_pdf, min(count, len(keys_with_pdf)))
    else:
        sampled = keys_with_pdf[:count]

    logger.info(f"Sampling {len(sampled)} papers for test indexing...")

    total_chunks = 0
    success = 0
    failed = 0

    t0 = time.time()
    for i, key in enumerate(sampled):
        pdf_path = pdf_paths[key]
        item = zot.get_item(key)
        title = item.title[:80] if item.title else "(no title)"
        year = ZoteroClient.parse_year(item.date)

        try:
            parsed = extract_pdf(pdf_path)
            if not parsed.pages:
                logger.warning(f"  [{i+1}/{len(sampled)}] SKIP (no text): {title}")
                failed += 1
                continue

            # Apply text cleaning to each page before chunking
            clean_enabled = os.getenv("ZRA_CLEAN_ENABLED", "true").lower() == "true"
            pages_for_chunking = parsed.pages
            if clean_enabled:
                cleaned_pages = []
                for pt in parsed.pages:
                    cleaned_text, _ = clean_text(pt.text)
                    cleaned_pages.append(PageText(page_num=pt.page_num, text=cleaned_text))
                pages_for_chunking = cleaned_pages

            total_chars = sum(len(p.text) for p in pages_for_chunking)
            chunks = chunk_text(pages_for_chunking)

            if not chunks:
                logger.warning(f"  [{i+1}/{len(sampled)}] SKIP (no chunks): {title}")
                failed += 1
                continue

            n = indexer.index_chunks(chunks, item_key=key, title=item.title, year=year)
            logger.info(
                f"  [{i+1}/{len(sampled)}] OK {n}c / {total_chars}ch | {year} | {title}"
            )
            total_chunks += n
            success += 1

        except Exception as e:
            logger.error(f"  [{i+1}/{len(sampled)}] FAIL: {title} — {e}")
            failed += 1

    elapsed = time.time() - t0
    logger.info(
        f"\nDone in {elapsed:.1f}s — {success} indexed ({total_chunks} chunks), "
        f"{failed} failed, {indexer.count()} total chunks in DB"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sample-index N papers for testing")
    parser.add_argument("--count", type=int, default=20, help="Number of papers to index")
    parser.add_argument("--random", action="store_true", help="Random sample")
    args = parser.parse_args()
    main(count=args.count, random_sample=args.random)
