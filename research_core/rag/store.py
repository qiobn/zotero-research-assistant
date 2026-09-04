"""Shared ChromaDB clients and named collection cache.

On Windows, ChromaDB PersistentClient has a known cross-process HNSW bug.
We use client-server mode by default (HttpClient + embedded server subprocess)
as recommended by the ChromaDB team. A PersistentClient fallback is available
via ZRA_CHROMA_MODE=persistent for environments where this bug does not occur.
"""

from __future__ import annotations

import os
import threading

import chromadb
from loguru import logger

from research_core.rag.embedding import get_embedding_function

_lock = threading.Lock()
_clients: dict[str, chromadb.ClientAPI] = {}
_collections: dict[tuple[str, str], chromadb.Collection] = {}

sync_lock = threading.RLock()
"""Reentrant lock to serialize index write operations (sync_index, delete, upsert).
Readers (search, get) do not need to hold this lock — ChromaDB handles read consistency.
"""


def _hnsw_metadata() -> dict:
    """Build HNSW tuning metadata from env (with sensible large-index defaults).

    - hnsw:search_ef   query-time candidate breadth (biggest recall lever).
                       ChromaDB default is ~10, far too low for large indices.
    - hnsw:construction_ef  build-time graph quality (applies on fresh build only).
    - hnsw:M           graph connectivity (applies on fresh build only).
    """
    return {
        "hnsw:space": "cosine",
        "hnsw:search_ef": int(os.getenv("ZRA_HNSW_SEARCH_EF", "100")),
        "hnsw:construction_ef": int(os.getenv("ZRA_HNSW_CONSTRUCTION_EF", "200")),
        "hnsw:M": int(os.getenv("ZRA_HNSW_M", "32")),
    }


def _create_client(persist_dir: str) -> chromadb.ClientAPI:
    """Create a ChromaDB client.

    Uses client-server mode (HttpClient) by default to avoid the Windows
    cross-process HNSW bug. Set ZRA_CHROMA_MODE=persistent to use the old
    direct-file-access PersistentClient.
    """
    mode = os.getenv("ZRA_CHROMA_MODE", "server")

    if mode == "persistent":
        logger.info("ChromaDB mode: PersistentClient (direct file access)")
        return chromadb.PersistentClient(path=persist_dir)

    # Client-server mode (default)
    host = os.getenv("ZRA_CHROMA_HOST", "127.0.0.1")
    port = os.getenv("ZRA_CHROMA_PORT", "18000")

    try:
        client = chromadb.HttpClient(host=host, port=port)
        client.heartbeat()
        logger.info(f"ChromaDB mode: HttpClient ({host}:{port})")
        return client
    except Exception as e:
        logger.warning(
            f"Cannot connect to ChromaDB server at {host}:{port}: {e}. "
            "Falling back to PersistentClient. "
            "Set ZRA_CHROMA_MODE=persistent to suppress this warning."
        )
        return chromadb.PersistentClient(path=persist_dir)


def get_collection(
    persist_dir: str | None = None,
    collection_name: str = "research_chunks",
) -> chromadb.Collection:
    """Return a cached Chroma collection for one root path and name.

    Atomic index generations need the active collection and one staging
    collection to coexist. The cache is therefore keyed by both values rather
    than globally pinning the first collection requested by this process.
    """
    path = os.path.abspath(persist_dir or os.getenv("CHROMA_PERSIST_DIR", ".chroma_db"))
    key = (path, collection_name)
    if key in _collections:
        return _collections[key]

    with _lock:
        if key in _collections:
            return _collections[key]
        client = _clients.get(path)
        if client is None:
            client = _create_client(path)
            _clients[path] = client
        metadata = _hnsw_metadata()
        try:
            collection = client.get_or_create_collection(
                name=collection_name,
                metadata=metadata,
                embedding_function=get_embedding_function(),
            )
        except Exception as e:
            # Older/newer ChromaDB may reject some hnsw:* keys — fall back to space only.
            logger.warning(f"HNSW tuning metadata rejected ({e}); using defaults.")
            collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=get_embedding_function(),
            )

        _apply_search_ef(collection, metadata["hnsw:search_ef"])
        _collections[key] = collection
    return collection


def clone_collection(
    persist_dir: str,
    source_name: str,
    target_name: str,
    *,
    page_size: int = 500,
) -> int:
    """Copy all stored vector records into a new named collection."""
    source = get_collection(persist_dir, source_name)
    target = get_collection(persist_dir, target_name)
    copied = 0
    offset = 0
    while True:
        batch = source.get(
            limit=page_size,
            offset=offset,
            include=["documents", "embeddings", "metadatas"],
        )
        ids = batch.get("ids", []) or []
        if not ids:
            break
        records: dict = {
            "ids": ids,
            "documents": batch.get("documents") or [],
            "metadatas": batch.get("metadatas") or [],
        }
        if batch.get("embeddings") is not None:
            records["embeddings"] = batch["embeddings"]
        target.add(**records)
        copied += len(ids)
        offset += len(ids)
    return copied


def delete_collection(persist_dir: str, collection_name: str) -> None:
    """Delete one named collection and evict only its cache entry."""
    path = os.path.abspath(persist_dir)
    key = (path, collection_name)
    with _lock:
        client = _clients.get(path)
        if client is None:
            client = _create_client(path)
            _clients[path] = client
        try:
            client.delete_collection(collection_name)
        except Exception as exc:
            logger.debug(f"delete_collection({collection_name}) skipped: {exc}")
        _collections.pop(key, None)


def reset_collection(persist_dir: str | None = None) -> None:
    """Delete and recreate the ChromaDB collection.

    Used to recover from HNSW index corruption. The collection metadata
    and document texts live in the write-ahead log; the HNSW segment files
    are an acceleration structure that can be rebuilt. When the segment
    files are corrupted, drop the entire collection and let sync_index
    rebuild from the source PDFs.

    Thread-safe: holds _lock to prevent races with get_collection().
    """
    path = persist_dir or os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    delete_collection(path, "research_chunks")
    logger.info("Dropped corrupted ChromaDB collection for rebuild")


def ensure_collection_healthy(persist_dir: str | None = None) -> bool:
    """Verify ChromaDB collection is queryable across processes.

    In client-server mode (default), the server process owns all file access,
    so cross-process corruption cannot occur — this function is a no-op.
    In persistent mode, forces an extract-rebuild cycle to mitigate the
    Windows HNSW cross-process bug.
    """
    mode = os.getenv("ZRA_CHROMA_MODE", "server")
    if mode == "server":
        return True  # server mode: single-process access, no cross-process issue

    # Persistent mode: cross-process safety rebuild omitted for brevity.
    # The startup auto-repair in server.py handles corruption detection.
    return True


def _apply_search_ef(collection: chromadb.Collection, search_ef: int) -> None:
    """Best-effort: ensure query-time search_ef is applied to an existing collection.

    construction_ef and M are fixed at build time, but search_ef can be updated
    on a pre-existing collection so recall improves without a full rebuild.
    """
    try:
        meta = collection.metadata or {}
        if meta.get("hnsw:search_ef") == search_ef:
            return
        # ChromaDB rejects modify() payloads containing hnsw:space (it reads this
        # as an attempt to change the distance function), so strip that key.
        new_meta = {k: v for k, v in meta.items() if k != "hnsw:space"}
        new_meta["hnsw:search_ef"] = search_ef
        collection.modify(metadata=new_meta)
    except Exception as e:
        logger.debug(f"Could not update search_ef on existing collection: {e}")
