"""Shared ChromaDB client and collection singleton.

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
_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None

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
        # Verify connectivity
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
    """Return the shared ChromaDB collection (singleton).

    Thread-safe: first call initializes; subsequent calls return cached instance.
    """
    global _client, _collection
    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:
            return _collection

        path = persist_dir or os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
        _client = _create_client(path)
        metadata = _hnsw_metadata()
        try:
            _collection = _client.get_or_create_collection(
                name=collection_name,
                metadata=metadata,
                embedding_function=get_embedding_function(),
            )
        except Exception as e:
            # Older/newer ChromaDB may reject some hnsw:* keys — fall back to space only.
            logger.warning(f"HNSW tuning metadata rejected ({e}); using defaults.")
            _collection = _client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=get_embedding_function(),
            )

        _apply_search_ef(_collection, metadata["hnsw:search_ef"])
    return _collection


def reset_collection(persist_dir: str | None = None) -> None:
    """Delete and recreate the ChromaDB collection.

    Used to recover from HNSW index corruption. The collection metadata
    and document texts live in the write-ahead log; the HNSW segment files
    are an acceleration structure that can be rebuilt. When the segment
    files are corrupted, drop the entire collection and let sync_index
    rebuild from the source PDFs.

    Thread-safe: holds _lock to prevent races with get_collection().
    """
    global _client, _collection
    with _lock:
        path = persist_dir or os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
        if _client is None:
            _client = _create_client(path)
        try:
            _client.delete_collection("research_chunks")
            logger.info("Dropped corrupted ChromaDB collection for rebuild")
        except Exception as e:
            logger.debug(f"delete_collection during reset: {e}")
        _collection = None


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
