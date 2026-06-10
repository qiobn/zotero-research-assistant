"""Admin tools — index maintenance with incremental sync."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from loguru import logger

from research_core.parsers.chunker import CHUNKING_VERSION, chunk_text
from research_core.parsers.pdf import extract_pdf_text
from research_core.rag.indexer import Indexer
from research_core.rag.retriever import Retriever
from research_core.rag.sync_state import SyncState
from research_core.zotero.client import ZoteroClient


def _parse_and_chunk(pdf_path: str):
    """Parse a PDF and chunk it. Pure CPU/IO work — safe to run in threads.

    Returns (chunks, total_chars). chunks is None when no text was extractable.
    """
    pages = extract_pdf_text(pdf_path)
    if not pages:
        return None, 0
    total_chars = sum(len(p.text) for p in pages)
    chunks = chunk_text(pages)
    return chunks, total_chars


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    total_chunks_after: int = 0
    incremental: bool = True
    quality_summary: dict = field(default_factory=dict)
    rebuild_reason: str = ""


def sync_index(
    zot: ZoteroClient,
    indexer: Indexer,
    retriever: Retriever,
    force_rebuild: bool = False,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> SyncReport:
    """Synchronize the vector index with the Zotero library.

    Incremental by default: uses Zotero item versions to detect new, modified,
    and deleted items. Only changed items are re-parsed and re-indexed.

    Auto-detects when chunking strategy or embedding model has been upgraded
    and forces a full rebuild for better quality.

    force_rebuild=True drops ALL stored state and reindexes everything.
    """
    report = SyncReport(incremental=not force_rebuild)
    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    state = SyncState.load(persist_dir)

    rebuild_reason = state.needs_rebuild(embedding_model)
    if rebuild_reason and not force_rebuild:
        logger.warning(f"{rebuild_reason} — forcing full rebuild")
        force_rebuild = True
        report.incremental = False
        report.rebuild_reason = rebuild_reason

    if force_rebuild:
        indexed_keys = retriever.list_indexed_items()
        for key in indexed_keys:
            indexer.delete_item(key)
            report.removed.append(key)
        state.item_versions.clear()

    current_versions = zot.get_item_versions()
    new_keys, modified_keys, deleted_keys = state.diff(current_versions)

    for key in deleted_keys:
        indexer.delete_item(key)
        report.removed.append(key)
        state.item_versions.pop(key, None)

    keys_to_process = new_keys | modified_keys
    if not keys_to_process:
        logger.info("No new or modified items to index")
        report.total_chunks_after = indexer.count()
        state.embedding_model = embedding_model
        state.chunking_version = CHUNKING_VERSION
        state.save()
        for key in current_versions:
            if key not in keys_to_process and key not in deleted_keys:
                report.skipped.append(key)
        return report

    logger.info(
        f"Incremental sync: {len(new_keys)} new, "
        f"{len(modified_keys)} modified, "
        f"{len(deleted_keys)} deleted"
    )

    pdf_paths = zot.get_pdf_paths_for_keys(list(keys_to_process))

    chunk_lengths: list[int] = []
    chunks_per_paper: list[int] = []
    issues: list[str] = []

    # Keys without a local PDF are skipped up-front.
    process_keys: list[str] = []
    for key in keys_to_process:
        if pdf_paths.get(key):
            process_keys.append(key)
        else:
            report.skipped.append(key)
            state.item_versions[key] = current_versions[key]

    workers = max(1, int(os.getenv("ZRA_SYNC_WORKERS", "4")))
    # Bounded batches cap peak memory (chunks held before indexing).
    batch_size = max(workers * 4, 16)

    for i in range(0, len(process_keys), batch_size):
        batch = process_keys[i:i + batch_size]

        # Phase 1: parse + chunk in parallel (CPU/IO-bound, no shared state).
        parsed: dict[str, tuple] = {}
        if workers > 1 and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_parse_and_chunk, pdf_paths[k]): k for k in batch
                }
                for fut in as_completed(futures):
                    k = futures[fut]
                    try:
                        parsed[k] = (*fut.result(), None)
                    except Exception as e:
                        parsed[k] = (None, 0, e)
        else:
            for k in batch:
                try:
                    parsed[k] = (*_parse_and_chunk(pdf_paths[k]), None)
                except Exception as e:
                    parsed[k] = (None, 0, e)

        # Phase 2: index serially (embedding + ChromaDB upsert under sync_lock).
        for key in batch:
            chunks, total_chars, err = parsed.get(key, (None, 0, None))
            if err is not None:
                logger.error(f"sync_index failed for {key}: {err}")
                report.failed.append({"key": key, "error": str(err)})
                continue
            if chunks is None:
                report.failed.append({
                    "key": key,
                    "error": "no text extracted",
                    "hint": "Possibly scanned/encrypted PDF",
                })
                issues.append(f"{key}: no extractable text (scanned/encrypted?)")
                continue

            try:
                if total_chars < 200:
                    issues.append(f"{key}: very short extraction ({total_chars} chars)")

                item = zot.get_item(key)
                year = ZoteroClient.parse_year(item.date)
                indexer.index_chunks(
                    chunks, item_key=key, title=item.title, year=year
                )

                chunks_per_paper.append(len(chunks))
                for c in chunks:
                    chunk_lengths.append(len(c.text))

                if len(chunks) <= 1 and total_chars > 500:
                    issues.append(
                        f"{key}: only {len(chunks)} chunk(s) from {total_chars} chars"
                    )

                if key in new_keys:
                    report.added.append(key)
                else:
                    report.updated.append(key)
                state.item_versions[key] = current_versions[key]
            except Exception as e:
                logger.error(f"sync_index failed for {key}: {e}")
                report.failed.append({"key": key, "error": str(e)})

    for key in current_versions:
        if key not in keys_to_process and key not in deleted_keys:
            if key not in state.item_versions:
                state.item_versions[key] = current_versions[key]

    state.embedding_model = embedding_model
    state.chunking_version = CHUNKING_VERSION
    state.save()
    report.total_chunks_after = indexer.count()

    if chunk_lengths:
        report.quality_summary = {
            "chunking_version": CHUNKING_VERSION,
            "papers_processed": len(chunks_per_paper),
            "total_chunks_created": sum(chunks_per_paper),
            "avg_chunk_length": round(
                sum(chunk_lengths) / len(chunk_lengths)
            ),
            "min_chunk_length": min(chunk_lengths),
            "max_chunk_length": max(chunk_lengths),
            "avg_chunks_per_paper": round(
                sum(chunks_per_paper) / len(chunks_per_paper), 1
            ),
            "papers_with_issues": len(issues),
            "issues": issues[:20],
        }

    return report
