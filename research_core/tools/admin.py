"""Admin tools — index maintenance with incremental sync."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from loguru import logger

from research_core.parsers.chunker import CHUNKING_VERSION, chunk_text
from research_core.parsers.pdf import extract_pdf
from research_core.parsers.section_detector import (
    build_section_map,
)
from research_core.rag.database import (
    ChunkMetaRow,
    FigureRow,
    PaperRow,
    TableRow,
    count_chunk_metadata,
    delete_paper,
    get_db,
    insert_chunk_figure_refs,
    insert_chunk_table_refs,
    insert_chunks_meta,
    list_paper_keys,
    upsert_paper,
)
from research_core.rag.index_generation import IndexGenerationStore
from research_core.rag.index_manifest import IndexManifest, IndexRuntime
from research_core.rag.indexer import Indexer
from research_core.rag.retriever import Retriever
from research_core.rag.store import clone_collection, delete_collection, sync_lock
from research_core.rag.sync_state import SyncState
from research_core.zotero.client import ZoteroClient


def _parse_and_chunk(pdf_path: str, clean: bool = True):
    """Parse a PDF and chunk it. Pure CPU/IO work — safe to run in threads.

    Returns (chunks, total_chars, cleaning_stats, quality, error).
    chunks is None when no text was extractable or extraction failed quality gates.
    cleaning_stats is a dict {lines_in, lines_out, removed_by_category} or None.
    quality is an ExtractionQuality (see parsers.pdf); None on hard errors.
    error is None on success, else the exception/blocking reason string.
    """
    from research_core.parsers.pdf import PageText
    from research_core.parsers.text_cleaner import clean_text

    parsed = extract_pdf(pdf_path)
    if not parsed.pages:
        return None, 0, None, parsed.quality, None

    cleaning_stats: dict | None = None

    if clean:
        total_lines_in = 0
        total_lines_out = 0
        all_categories: dict[str, int] = {}
        cleaned_pages: list[PageText] = []
        for pt in parsed.pages:
            cleaned_text, report = clean_text(pt.text)
            cleaned_pages.append(PageText(page_num=pt.page_num, text=cleaned_text))
            total_lines_in += report.total_lines_in
            total_lines_out += report.total_lines_out
            for cat, cnt in report.removed_by_category.items():
                all_categories[cat] = all_categories.get(cat, 0) + cnt
        cleaning_stats = {
            "lines_in": total_lines_in,
            "lines_out": total_lines_out,
            "lines_removed": total_lines_in - total_lines_out,
            "top_categories": dict(
                sorted(all_categories.items(), key=lambda x: -x[1])[:10]
            ),
        }
        pages_for_chunking = cleaned_pages
    else:
        pages_for_chunking = parsed.pages

    total_chars = sum(len(p.text) for p in pages_for_chunking)
    chunks = chunk_text(pages_for_chunking)
    return chunks, total_chars, cleaning_stats, parsed.quality, None


def _index_metadata(
    *,
    item_key: str,
    title: str,
    year: int,
    authors: str,
    abstract: str,
    keywords: str,
    journal: str,
    doi: str,
    pub_type: str,
    zotero_version: int,
    chunks: list,
    persist_dir: str = ".chroma_db",
) -> dict:
    """Write paper/section/chunk metadata to SQLite.

    Called after chunking, before ChromaDB indexing.
    Returns stats dict for the SyncReport.
    """

    conn = get_db(persist_dir)

    # 1. Upsert paper
    paper = PaperRow(
        item_key=item_key,
        title=title,
        year=year,
        authors=authors,
        abstract=abstract,
        keywords=keywords,
        journal=journal,
        doi=doi,
        pub_type=pub_type,
        zotero_version=zotero_version,
    )
    upsert_paper(conn, paper)

    # 2. Detect sections
    chunk_list = list(chunks)  # ensure list
    sections_info, chunk_section_map = build_section_map(chunk_list)

    # Build rows before insertion. ``parent_idx`` is local to the detector's
    # result, so it is resolved to a SQLite ``parent_id`` below.
    section_rows = []
    for sec in sections_info:
        section_rows.append({
            "item_key": item_key,
            "parent_id": None,
            "heading": sec.heading,
            "section_type": sec.section_type,
            "level": sec.level,
            "page_start": sec.page_start,
            "page_end": sec.page_end,
            "chunk_start_idx": sec.chunk_start_idx,
            "chunk_end_idx": sec.chunk_end_idx,
        })

    # Delete old sections + chunks for this paper before inserting new
    conn.execute("DELETE FROM sections WHERE item_key = ?", (item_key,))
    conn.execute("DELETE FROM chunks_meta WHERE item_key = ?", (item_key,))

    section_ids: list[int] = []
    for section_idx, sr in enumerate(section_rows):
        parent_idx = sections_info[section_idx].parent_idx
        if parent_idx is not None:
            if parent_idx < 0 or parent_idx >= len(section_ids):
                raise ValueError(
                    "Section hierarchy must reference an earlier parent section"
                )
            sr["parent_id"] = section_ids[parent_idx]

        cur = conn.execute("""
            INSERT INTO sections
                (item_key, parent_id, heading, section_type, level,
                 page_start, page_end, chunk_start_idx, chunk_end_idx)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sr["item_key"], sr["parent_id"], sr["heading"],
            sr["section_type"], sr["level"],
            sr["page_start"], sr["page_end"],
            sr["chunk_start_idx"], sr["chunk_end_idx"],
        ))
        section_ids.append(cur.lastrowid or 0)

    # 3. Insert chunks_meta
    chunk_meta_rows = []
    for i, c in enumerate(chunk_list):
        section_id = None
        if i in chunk_section_map:
            si = chunk_section_map[i]
            if si < len(section_ids):
                section_id = section_ids[si]

        chunk_meta_rows.append(ChunkMetaRow(
            id=f"{item_key}:{c.chunk_idx}",
            item_key=item_key,
            chunk_idx=c.chunk_idx,
            section_id=section_id,
            page_start=c.page_start,
            page_end=c.page_end,
            quality_flag=c.quality_flag,
            sentence_count=c.sentence_count,
            language=c.language,
            is_table=c.metadata.get("is_table", False),
            is_figure=c.metadata.get("is_figure", False),
        ))
    insert_chunks_meta(conn, chunk_meta_rows)

    # 4. Extract figures and tables from chunk metadata
    figures: list[FigureRow] = []
    tables: list[TableRow] = []
    fig_refs: list[tuple[str, int]] = []
    table_refs: list[tuple[str, int]] = []

    for c in chunk_list:
        meta = c.metadata
        chunk_id = f"{item_key}:{c.chunk_idx}"

        if meta.get("is_figure") and meta.get("figure_ref"):
            fig = FigureRow(
                item_key=item_key,
                ref=meta.get("figure_ref", ""),
                label=meta.get("figure_label", ""),
                caption=meta.get("figure_caption", ""),
                page=c.page_start,
            )
            # Deduplicate by ref within same paper
            if fig.ref not in {f.ref for f in figures}:
                figures.append(fig)

        if meta.get("is_table") and meta.get("table_ref"):
            tbl = TableRow(
                item_key=item_key,
                ref=meta.get("table_ref", ""),
                label=meta.get("table_label", ""),
                caption=meta.get("table_caption", ""),
                page=c.page_start,
            )
            if tbl.ref not in {t.ref for t in tables}:
                tables.append(tbl)

    # Re-fetch figure/table IDs after insert for cross-refs
    conn.execute("DELETE FROM figures WHERE item_key = ?", (item_key,))
    conn.execute("DELETE FROM table_records WHERE item_key = ?", (item_key,))
    conn.execute(
        "DELETE FROM chunk_figure_refs WHERE chunk_id LIKE ?",
        (f"{item_key}:%",),
    )
    conn.execute(
        "DELETE FROM chunk_table_refs WHERE chunk_id LIKE ?",
        (f"{item_key}:%",),
    )

    fig_id_map: dict[str, int] = {}
    for fig in figures:
        cur = conn.execute(
            "INSERT INTO figures (item_key, ref, label, caption, page) VALUES (?,?,?,?,?)",
            (fig.item_key, fig.ref, fig.label, fig.caption, fig.page),
        )
        fig_id_map[fig.ref] = cur.lastrowid or 0

    tbl_id_map: dict[str, int] = {}
    for tbl in tables:
        cur = conn.execute(
            "INSERT INTO table_records (item_key, ref, label, caption, page) VALUES (?,?,?,?,?)",
            (tbl.item_key, tbl.ref, tbl.label, tbl.caption, tbl.page),
        )
        tbl_id_map[tbl.ref] = cur.lastrowid or 0

    # Cross-references
    for c in chunk_list:
        chunk_id = f"{item_key}:{c.chunk_idx}"
        meta = c.metadata
        if meta.get("table_refs") and tbl_id_map:
            refs_raw = meta.get("table_refs", "")
            ref_tokens: list[str] = (
                refs_raw if isinstance(refs_raw, list)
                else [t.strip() for t in refs_raw.split(",") if t.strip()]
            )
            for ref_token in ref_tokens:
                ref_token = ref_token.strip() if isinstance(ref_token, str) else str(ref_token)
                if ref_token in tbl_id_map:
                    table_refs.append((chunk_id, tbl_id_map[ref_token]))
        if meta.get("figure_refs") and fig_id_map:
            refs_raw = meta.get("figure_refs", "")
            ref_tokens: list[str] = (
                refs_raw if isinstance(refs_raw, list)
                else [t.strip() for t in refs_raw.split(",") if t.strip()]
            )
            for ref_token in ref_tokens:
                ref_token = ref_token.strip() if isinstance(ref_token, str) else str(ref_token)
                if ref_token in fig_id_map:
                    fig_refs.append((chunk_id, fig_id_map[ref_token]))

    if fig_refs:
        insert_chunk_figure_refs(conn, fig_refs)
    if table_refs:
        insert_chunk_table_refs(conn, table_refs)

    conn.commit()

    return {
        "sections": len(section_rows),
        "chunks_meta": len(chunk_meta_rows),
        "figures": len(figures),
        "tables": len(tables),
        "fig_refs": len(fig_refs),
        "table_refs": len(table_refs),
    }


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    total_chunks_after: int = 0
    bm25_indexed: int = 0
    incremental: bool = True
    quality_summary: dict = field(default_factory=dict)
    rebuild_reason: str = ""
    cleaning_enabled: bool = True
    total_lines_cleaned: int = 0
    cleaning_categories: dict = field(default_factory=dict)
    index_build_id: str = ""
    index_status: str = ""
    metadata_chunks: int = 0


def _remove_indexed_item(indexer: Indexer, persist_dir: str, item_key: str) -> int:
    """Delete an item from both search stores before it is removed or replaced."""
    deleted = indexer.delete_item(item_key)
    conn = get_db(persist_dir)
    delete_paper(conn, item_key)
    conn.commit()
    return deleted


def _finalize_index_build(
    *,
    indexer: Indexer,
    retriever: Retriever,
    persist_dir: str,
    manifest: IndexManifest,
    report: SyncReport,
) -> None:
    """Rebuild sparse retrieval and persist one observed index-build outcome."""
    bm25_count = 0
    error = ""
    try:
        from research_core.rag.bm25_index import BM25Index

        bm25 = BM25Index(persist_dir)
        bm25_count = bm25.build_from_collection(indexer.collection)
        retriever._bm25 = None  # noqa: SLF001
        logger.info(f"BM25 index rebuilt: {bm25_count} chunks")
    except Exception as exc:
        error = f"BM25 index rebuild failed: {exc}"
        logger.warning(error)

    vector_chunks = indexer.count()
    metadata_chunks = count_chunk_metadata(get_db(persist_dir))
    manifest.finish(
        vector_chunks=vector_chunks,
        metadata_chunks=metadata_chunks,
        bm25_chunks=bm25_count,
        error=error,
    )
    manifest.save(persist_dir)
    report.total_chunks_after = vector_chunks
    report.metadata_chunks = metadata_chunks
    report.bm25_indexed = bm25_count
    report.index_build_id = manifest.build_id
    report.index_status = manifest.status


def _promote_generation(
    *,
    generation_store: IndexGenerationStore,
    generation,
    manifest: IndexManifest,
    root_persist_dir: str,
    state: SyncState,
) -> bool:
    """Publish a validated staging generation, then retire only old generations."""
    if manifest.status != "ready":
        logger.error(
            f"Index generation {generation.build_id} was not activated: "
            f"{manifest.error or manifest.status}"
        )
        return False

    generation_store.activate(generation)
    state.save()
    for stale in generation_store.stale_generations(keep=2):
        try:
            delete_collection(root_persist_dir, stale.collection_name)
            generation_store.discard(stale)
        except Exception as exc:
            logger.warning(f"Failed to clean retired index generation {stale.build_id}: {exc}")
    return True


def sync_index(
    zot: ZoteroClient,
    indexer: Indexer,
    retriever: Retriever,
    force_rebuild: bool = False,
) -> SyncReport:
    """Synchronize the library through one serialized generation transaction."""
    with sync_lock:
        return _sync_index_locked(zot, indexer, retriever, force_rebuild)


def _sync_index_locked(
    zot: ZoteroClient,
    indexer: Indexer,
    retriever: Retriever,
    force_rebuild: bool = False,
) -> SyncReport:
    """Synchronize the vector index with the Zotero library.

    Incremental by default: uses Zotero item versions to detect new, modified,
    and deleted items. Only changed items are re-parsed and re-indexed.

    Auto-detects when chunking strategy or embedding model has been upgraded
    and forces a full rebuild for better quality.

    force_rebuild=True drops ALL stored state and reindexes everything.
    """
    report = SyncReport(incremental=not force_rebuild)
    root_persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    runtime = IndexRuntime.from_environment()
    generation_store = IndexGenerationStore(root_persist_dir)
    active_generation = generation_store.active()

    state = SyncState.load(root_persist_dir)
    previous_manifest = IndexManifest.load(active_generation.persist_dir)

    rebuild_reason = state.needs_rebuild(embedding_model)
    manifest_issues = (
        previous_manifest.compatibility_issues(runtime)
        if previous_manifest is not None
        else []
    )
    rebuild_reasons = [reason for reason in (rebuild_reason, *manifest_issues) if reason]
    if rebuild_reasons and not force_rebuild:
        report.rebuild_reason = "; ".join(rebuild_reasons)
        logger.warning(f"{report.rebuild_reason} — forcing full rebuild")
        force_rebuild = True
        report.incremental = False

    current_versions = zot.get_item_versions()
    new_keys, modified_keys, deleted_keys = state.diff(current_versions)
    active_indexed_keys: set[str] = set()
    if force_rebuild:
        active_indexed_keys = retriever.list_indexed_items()

    needs_generation = (
        force_rebuild
        or active_generation.legacy
        or previous_manifest is None
        or bool(new_keys | modified_keys | deleted_keys)
    )
    if not needs_generation:
        report.total_chunks_after = retriever.count()
        report.metadata_chunks = count_chunk_metadata(
            get_db(active_generation.persist_dir)
        )
        report.bm25_indexed = previous_manifest.bm25_chunks
        report.index_build_id = previous_manifest.build_id
        report.index_status = previous_manifest.status
        report.skipped.extend(current_versions)
        return report

    staging_generation = generation_store.begin()
    try:
        if not force_rebuild:
            generation_store.clone_metadata(active_generation, staging_generation)
            clone_collection(
                root_persist_dir,
                active_generation.collection_name,
                staging_generation.collection_name,
            )
        staging_indexer = Indexer(
            persist_dir=root_persist_dir,
            collection_name=staging_generation.collection_name,
        )
        staging_retriever = Retriever(
            persist_dir=staging_generation.persist_dir,
            collection=staging_indexer.collection,
            follow_active_generation=False,
        )
    except Exception:
        delete_collection(root_persist_dir, staging_generation.collection_name)
        generation_store.discard(staging_generation)
        raise

    # From here onward every mutation targets the unreachable staging generation.
    indexer = staging_indexer
    retriever = staging_retriever
    persist_dir = staging_generation.persist_dir
    manifest = IndexManifest.start(runtime, build_id=staging_generation.build_id)
    manifest.save(persist_dir)

    if force_rebuild:
        indexed_keys = active_indexed_keys | list_paper_keys(
            get_db(active_generation.persist_dir)
        )
        report.removed.extend(sorted(indexed_keys))
        state.item_versions.clear()
        new_keys = set(current_versions)
        modified_keys = set()
        deleted_keys = set()

    for key in deleted_keys:
        _remove_indexed_item(indexer, persist_dir, key)
        report.removed.append(key)
        state.item_versions.pop(key, None)

    keys_to_process = new_keys | modified_keys
    if not keys_to_process:
        logger.info("No new or modified items to index")
        state.embedding_model = embedding_model
        state.chunking_version = CHUNKING_VERSION
        _finalize_index_build(
            indexer=indexer,
            retriever=retriever,
            persist_dir=persist_dir,
            manifest=manifest,
            report=report,
        )
        _promote_generation(
            generation_store=generation_store,
            generation=staging_generation,
            manifest=manifest,
            root_persist_dir=root_persist_dir,
            state=state,
        )
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

    q_scanned = 0
    q_garbled = 0
    q_fragmented = 0

    for i in range(0, len(process_keys), batch_size):
        batch = process_keys[i:i + batch_size]

        # Phase 1: parse + chunk in parallel (CPU/IO-bound, no shared state).
        parsed: dict[str, tuple] = {}
        clean_enabled = os.getenv("ZRA_CLEAN_ENABLED", "true").lower() == "true"
        if workers > 1 and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_parse_and_chunk, pdf_paths[k], clean_enabled): k
                    for k in batch
                }
                for fut in as_completed(futures):
                    k = futures[fut]
                    try:
                        parsed[k] = fut.result()
                    except Exception as e:
                        parsed[k] = (None, 0, None, None, e)
        else:
            for k in batch:
                try:
                    parsed[k] = _parse_and_chunk(pdf_paths[k], clean=clean_enabled)
                except Exception as e:
                    parsed[k] = (None, 0, None, None, e)

        report.cleaning_enabled = clean_enabled
        total_cleaned_lines = 0
        all_clean_cats: dict[str, int] = {}

        # Phase 2: index serially (embedding + ChromaDB upsert under sync_lock).
        for key in batch:
            result = parsed.get(key, (None, 0, None, None, None))
            chunks, total_chars, cleaning_stats, quality, err = result
            if err is not None:
                logger.error(f"sync_index failed for {key}: {err}")
                report.failed.append({"key": key, "error": str(err)})
                continue
            if chunks is None or (quality and quality.scanned):
                q_scanned += 1
                report.failed.append({
                    "key": key,
                    "error": "no text extracted",
                    "hint": "Possibly scanned/encrypted PDF",
                })
                issues.append(f"{key}: no extractable text (scanned/encrypted?)")
                continue
            if quality and quality.garbled:
                q_garbled += 1
                report.failed.append({
                    "key": key,
                    "error": "garbled extraction",
                    "hint": "Extracted text contains replacement/NUL characters",
                })
                issues.append(f"{key}: garbled extraction (replacement chars)")
                continue
            if quality and quality.fragmented:
                q_fragmented += 1
                report.failed.append({
                    "key": key,
                    "error": "fragmented layout",
                    "hint": "Word-by-word extraction; text is unusable",
                })
                issues.append(f"{key}: fragmented layout (word-per-line)")
                continue

            try:
                if total_chars < 200:
                    issues.append(f"{key}: very short extraction ({total_chars} chars)")

                # Track cleaning stats
                if cleaning_stats:
                    total_cleaned_lines += cleaning_stats.get("lines_removed", 0)
                    for cat, cnt in cleaning_stats.get("top_categories", {}).items():
                        all_clean_cats[cat] = all_clean_cats.get(cat, 0) + cnt

                item = zot.get_item(key)
                year = ZoteroClient.parse_year(item.date)

                # Build keywords from Zotero tags — academic paper advantage
                # Filter out organizational tags (to-*, single chars, status labels)
                _ORG_TAG_PREFIXES = ("to-", "status:", "project:")
                raw_tags = getattr(item, "tags", []) or []
                meaningful_tags = [
                    t for t in raw_tags
                    if len(t) >= 3
                    and not any(t.lower().startswith(p) for p in _ORG_TAG_PREFIXES)
                ]
                keywords_str = ", ".join(meaningful_tags) if meaningful_tags else ""

                # Remove every old chunk first. Upserts alone leave stale tail
                # chunks when a revised PDF produces fewer chunks than before.
                _remove_indexed_item(indexer, persist_dir, key)
                indexer.index_chunks(
                    chunks, item_key=key, title=item.title, year=year,
                    keywords=keywords_str,
                )

                # Write structured metadata to SQLite
                try:
                    _index_metadata(
                        item_key=key,
                        title=item.title or "",
                        year=year,
                        authors=json.dumps(item.authors) if item.authors else "",
                        abstract=getattr(item, "abstract", "") or "",
                        keywords=keywords_str,
                        journal=getattr(item, "publicationTitle", "") or "",
                        doi=getattr(item, "doi", "") or "",
                        pub_type=(
                            "thesis" if "论文" in (item.title or "")
                            else "journal_article"
                        ),
                        zotero_version=current_versions.get(key, 0),
                        chunks=chunks,
                        persist_dir=persist_dir,
                    )
                except Exception as e:
                    logger.warning(f"SQLite metadata write failed for {key}: {e}")

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
    report.total_lines_cleaned = total_cleaned_lines
    report.cleaning_categories = dict(
        sorted(all_clean_cats.items(), key=lambda x: -x[1])[:15]
    )
    report.quality_summary = {
        "scanned": q_scanned,
        "garbled": q_garbled,
        "fragmented": q_fragmented,
    }

    # ── Load user Zotero tags into query rewriter for personalized expansion ──
    try:
        from research_core.rag.query_rewriter import get_user_synonyms, load_user_tags

        # Collect all tags from Zotero (persistent across syncs)
        all_items = zot.search_items(query="", limit=10000)
        all_tags: list[str] = []
        for item in all_items:
            all_tags.extend(item.tags)
        load_user_tags(all_tags)

        # Load user-defined synonyms from disk
        synonyms_file = os.path.join(root_persist_dir, "query_dict_user.json")
        from research_core.rag.query_rewriter import load_user_synonyms
        load_user_synonyms(synonyms_file)
        user_syns = get_user_synonyms()
        report.query_expansion = {
            "user_tags_loaded": len(set(all_tags)),
            "user_synonyms": len(user_syns),
        }
    except Exception:
        pass  # best-effort; query expansion still works with Layer 1 (built-in)

    # ── Ensure ChromaDB HNSW segment is fully persisted ──
    # ChromaDB 1.5.x uses async Rust compaction; the HNSW segment may be
    # incomplete when the Python process exits. Force a clean rebuild to
    # guarantee future processes can query the collection.
    try:
        from research_core.rag.store import ensure_collection_healthy
        healthy = ensure_collection_healthy(root_persist_dir)
        if not healthy:
            logger.error(
                "HNSW rebuild failed — "
                "collection may be unqueryable after server restart. "
                "Run sync_index again or check disk space."
            )
    except Exception as e:
        logger.warning(f"HNSW health check failed (non-fatal): {e}")

    _finalize_index_build(
        indexer=indexer,
        retriever=retriever,
        persist_dir=persist_dir,
        manifest=manifest,
        report=report,
    )

    _promote_generation(
        generation_store=generation_store,
        generation=staging_generation,
        manifest=manifest,
        root_persist_dir=root_persist_dir,
        state=state,
    )

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
            "extraction_quality": {
                "scanned": q_scanned,
                "garbled": q_garbled,
                "fragmented": q_fragmented,
            },
        }

    # Log rotation: cleanup entries older than 90 days (best-effort)
    try:
        from research_core.rag.logger import RetrievalLogger
        rl = RetrievalLogger(persist_dir=root_persist_dir)
        rl.rotate(keep_days=90)
    except Exception:
        pass

    return report


# ── Retrieval log query tools ─────────────────────────────────────────


def get_recent_retrievals(
    n: int = 20,
    strategy: str = "",
    success_only: bool = True,
    persist_dir: str = ".chroma_db",
) -> list[dict]:
    """Return the most recent N retrieval log entries."""
    from research_core.rag.logger import RetrievalLogger
    log = RetrievalLogger(persist_dir)
    return log.get_recent(n=n, strategy=strategy, success_only=success_only)


def get_retrieval_trace(trace_id: str, persist_dir: str = ".chroma_db") -> dict | None:
    """Look up a specific retrieval trace by its ID."""
    from research_core.rag.logger import RetrievalLogger
    log = RetrievalLogger(persist_dir)
    return log.get_by_trace_id(trace_id)


def get_retrieval_stats(persist_dir: str = ".chroma_db") -> dict:
    """Return aggregate statistics on all logged retrievals."""
    from research_core.rag.logger import RetrievalLogger
    log = RetrievalLogger(persist_dir)
    return log.stats()
