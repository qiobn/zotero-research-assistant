"""Index chunks into ChromaDB with metadata."""

from __future__ import annotations

import chromadb
from loguru import logger

from research_core.parsers.chunker import Chunk
from research_core.rag.embedding import get_embedding_function


class Indexer:
    """Write document chunks into a ChromaDB collection."""

    def __init__(
        self,
        persist_dir: str = ".chroma_db",
        collection_name: str = "research_chunks",
    ):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=get_embedding_function(),
        )

    @property
    def collection(self):
        return self._collection

    def index_chunks(
        self,
        chunks: list[Chunk],
        item_key: str,
        title: str = "",
        year: int = 0,
    ) -> int:
        """Add or replace chunks for one item. Returns number of chunks indexed."""
        if not chunks:
            return 0
        self.delete_item(item_key)
        ids = [f"{item_key}:{c.chunk_idx}" for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [
            {
                "item_key": item_key,
                "title": title,
                "year": year,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "chunk_idx": c.chunk_idx,
                "section": c.metadata.get("section", "content"),
                "has_figure_table": c.metadata.get(
                    "has_figure_table", False
                ),
            }
            for c in chunks
        ]
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(f"Indexed {len(chunks)} chunks for item {item_key}")
        return len(chunks)

    def delete_item(self, item_key: str) -> int:
        """Delete all chunks for an item. Returns the count deleted."""
        existing = self._collection.get(where={"item_key": item_key}, include=[])
        ids = existing.get("ids", []) or []
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def count(self) -> int:
        return self._collection.count()
