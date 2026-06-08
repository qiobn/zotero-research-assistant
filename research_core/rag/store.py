"""Shared ChromaDB client and collection singleton.

Ensures a single PersistentClient and collection instance is reused
across Indexer and Retriever, avoiding duplicate file handles and
enabling safe concurrent access via a module-level lock.
"""

from __future__ import annotations

import os
import threading

import chromadb

from research_core.rag.embedding import get_embedding_function

_lock = threading.Lock()
_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None

sync_lock = threading.RLock()
"""Reentrant lock to serialize index write operations (sync_index, delete, upsert).
Readers (search, get) do not need to hold this lock — ChromaDB handles read consistency.
"""


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
        _collection = _client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=get_embedding_function(),
        )
    return _collection
