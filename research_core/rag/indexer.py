"""Index chunks into ChromaDB with metadata."""

from __future__ import annotations

import chromadb
from loguru import logger

from research_core.parsers.chunker import Chunk
from research_core.rag.store import get_collection, sync_lock


class Indexer:
    """Write document chunks into a ChromaDB collection."""

    def __init__(
        self,
        persist_dir: str = ".chroma_db",
        collection_name: str = "research_chunks",
        collection: chromadb.Collection | None = None,
    ):
        self._collection = collection or get_collection(
            persist_dir, collection_name
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
        with sync_lock:
            self.delete_item(item_key)
            ids = [f"{item_key}:{c.chunk_idx}" for c in chunks]
            documents = [c.text for c in chunks]
            metadatas = [self._build_metadata(c, item_key, title, year) for c in chunks]
            self._collection.upsert(
                ids=ids, documents=documents, metadatas=metadatas
            )
        logger.info(f"Indexed {len(chunks)} chunks for item {item_key}")
        return len(chunks)

    @staticmethod
    def _build_metadata(c: Chunk, item_key: str, title: str, year: int) -> dict:
        """Build ChromaDB metadata (scalar values only) for one chunk."""
        meta = {
            "item_key": item_key,
            "title": title,
            "year": year,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "chunk_idx": c.chunk_idx,
            "section": c.metadata.get("section", "content"),
            "has_figure_table": c.metadata.get("has_figure_table", False),
            "is_table": c.metadata.get("is_table", False),
        }
        if c.metadata.get("is_table"):
            meta["table_caption"] = c.metadata.get("table_caption", "")
            meta["table_part"] = c.metadata.get("table_part", 1)
            meta["table_parts"] = c.metadata.get("table_parts", 1)
            meta["n_rows"] = c.metadata.get("n_rows", 0)
            meta["n_cols"] = c.metadata.get("n_cols", 0)
            table_json = c.metadata.get("table_json")
            if table_json:
                meta["table_json"] = table_json
        return meta

    def delete_item(self, item_key: str) -> int:
        """Delete all chunks for an item. Returns the count deleted."""
        with sync_lock:
            existing = self._collection.get(
                where={"item_key": item_key}, include=[]
            )
            ids = existing.get("ids", []) or []
            if ids:
                self._collection.delete(ids=ids)
        return len(ids)

    def count(self) -> int:
        return self._collection.count()
