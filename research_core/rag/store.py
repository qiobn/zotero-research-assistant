"""Shared ChromaDB client and collection singleton.

Ensures a single PersistentClient and collection instance is reused
across Indexer and Retriever, avoiding duplicate file handles and
enabling safe concurrent access via a module-level lock.
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
        _client = chromadb.PersistentClient(path=path)
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
