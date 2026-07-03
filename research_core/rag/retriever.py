"""Retrieve relevant chunks from ChromaDB."""

from __future__ import annotations

from dataclasses import dataclass, field

import chromadb

from research_core.rag.store import get_collection


@dataclass
class SectionContext:
    """Expanded context for a chunk — its containing section."""
    heading: str = ""
    section_type: str = "unknown"
    full_text: str = ""           # all chunks in this section concatenated
    chunk_ids: list[str] = field(default_factory=list)
    page_start: int = 0
    page_end: int = 0


@dataclass
class RetrievalResult:
    text: str
    item_key: str
    title: str
    page_start: int
    page_end: int
    score: float
    chunk_idx: int = 0
    metadata: dict = field(default_factory=dict)
    section_context: SectionContext | None = None  # populated when expand_context=True


class Retriever:
    """Query the ChromaDB collection for semantically similar chunks."""

    def __init__(
        self,
        persist_dir: str = ".chroma_db",
        collection_name: str = "research_chunks",
        collection: chromadb.Collection | None = None,
    ):
        self._collection = collection or get_collection(
            persist_dir, collection_name
        )
        self._persist_dir = persist_dir

    def search(
        self,
        query: str,
        n_results: int = 8,
        where: dict | None = None,
        include_references: bool = False,
        expand_context: bool = False,
    ) -> list[RetrievalResult]:
        """Semantic search across all indexed chunks.

        By default excludes reference/bibliography sections to reduce noise.
        Set include_references=True to search across all sections.

        When expand_context=True, each result's section_context is populated
        with the full text of its containing section, providing the LLM with
        complete paragraph context instead of a single isolated chunk.
        """
        effective_where = self._build_where(
            where, include_references
        )
        kwargs: dict = {"query_texts": [query], "n_results": n_results}
        if effective_where:
            kwargs["where"] = effective_where
        results = self._collection.query(**kwargs)
        hits = self._to_results(results)

        if expand_context and hits:
            self._attach_section_contexts(hits)

        return hits

    def expand_to_section(
        self,
        item_key: str,
        chunk_idx: int,
    ) -> SectionContext | None:
        """Expand a single chunk to its containing section.

        Looks up the chunk's section in SQLite, fetches ALL chunks in that
        section from ChromaDB, and returns the concatenated full section text.

        Returns None if no section is found for this chunk.
        """
        chunk_id = f"{item_key}:{chunk_idx}"
        try:
            from research_core.rag.database import get_db
            conn = get_db(self._persist_dir)
            row = conn.execute(
                "SELECT section_id, page_start, page_end "
                "FROM chunks_meta WHERE id = ?",
                (chunk_id,),
            ).fetchone()
            if not row or not row[0]:
                return None

            section_id = row[0]
            sec_row = conn.execute(
                "SELECT heading, section_type, page_start, page_end "
                "FROM sections WHERE id = ?",
                (section_id,),
            ).fetchone()
            if not sec_row:
                return None

            # Get all chunks in this section
            chunk_rows = conn.execute(
                "SELECT id, chunk_idx FROM chunks_meta "
                "WHERE section_id = ? ORDER BY chunk_idx",
                (section_id,),
            ).fetchall()

            # Fetch chunk texts from ChromaDB
            chunk_ids = [cr[0] for cr in chunk_rows]
            raw = self._collection.get(ids=chunk_ids, include=["documents"])
            docs = raw.get("documents", []) or []

            # Concatenate in order
            full_text = "\n\n".join(docs) if docs else ""

            return SectionContext(
                heading=sec_row[0] or "",
                section_type=sec_row[1] or "unknown",
                full_text=full_text,
                chunk_ids=chunk_ids,
                page_start=sec_row[2] or 0,
                page_end=sec_row[3] or 0,
            )
        except Exception:
            return None

    def _attach_section_contexts(self, results: list[RetrievalResult]) -> None:
        """Populate section_context for each result. Uses a cache to avoid
        duplicate DB + ChromaDB lookups for chunks in the same section."""
        cache: dict[tuple[str, int], SectionContext | None] = {}

        for r in results:
            cache_key = (r.item_key, r.chunk_idx)
            if cache_key in cache:
                r.section_context = cache[cache_key]
                continue

            ctx = self.expand_to_section(r.item_key, r.chunk_idx)
            cache[cache_key] = ctx
            r.section_context = ctx

    def search_within_item(
        self,
        item_key: str,
        query: str,
        n_results: int = 5,
        include_references: bool = False,
    ) -> list[RetrievalResult]:
        """Semantic search restricted to a single paper's chunks."""
        where: dict = {"item_key": item_key}
        if not include_references:
            where = {
                "$and": [
                    {"item_key": item_key},
                    {"section": {"$ne": "references"}},
                ]
            }
        return self.search(
            query,
            n_results=n_results,
            where=where,
            include_references=True,
        )

    def get_item_chunks(
        self,
        item_key: str,
        page: int | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve all chunks of one paper, optionally filtered by page."""
        where: dict = {"item_key": item_key}
        if page is not None:
            where = {
                "$and": [
                    {"item_key": item_key},
                    {"page_start": {"$lte": page}},
                    {"page_end": {"$gte": page}},
                ]
            }
        raw = self._collection.get(
            where=where, include=["documents", "metadatas"]
        )
        docs = raw.get("documents", []) or []
        metas = raw.get("metadatas", []) or []
        out: list[RetrievalResult] = []
        for doc, meta in zip(docs, metas, strict=True):
            out.append(
                RetrievalResult(
                    text=doc,
                    item_key=meta.get("item_key", ""),
                    title=meta.get("title", ""),
                    page_start=meta.get("page_start", 0),
                    page_end=meta.get("page_end", 0),
                    score=1.0,
                    chunk_idx=meta.get("chunk_idx", 0),
                    metadata=meta,
                )
            )
        out.sort(key=lambda r: r.chunk_idx)
        return out

    def get_figure_table_chunks(
        self,
        item_key: str,
    ) -> list[RetrievalResult]:
        """Retrieve chunks containing figure/table captions for a paper."""
        where: dict = {
            "$and": [
                {"item_key": item_key},
                {"has_figure_table": True},
            ]
        }
        raw = self._collection.get(
            where=where, include=["documents", "metadatas"]
        )
        docs = raw.get("documents", []) or []
        metas = raw.get("metadatas", []) or []
        out: list[RetrievalResult] = []
        for doc, meta in zip(docs, metas, strict=True):
            out.append(
                RetrievalResult(
                    text=doc,
                    item_key=meta.get("item_key", ""),
                    title=meta.get("title", ""),
                    page_start=meta.get("page_start", 0),
                    page_end=meta.get("page_end", 0),
                    score=1.0,
                    chunk_idx=meta.get("chunk_idx", 0),
                    metadata=meta,
                )
            )
        out.sort(key=lambda r: r.chunk_idx)
        return out

    def get_item_tables(
        self,
        item_key: str,
        refs: list[str] | set[str] | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve a paper's table records (caption + raw block content).

        If ``refs`` is given (canonical labels like "3"), only tables whose
        ``table_ref`` matches are returned — this resolves a prose passage's
        cited tables (e.g. "see Table 3") to their content.
        """
        where: dict = {
            "$and": [{"item_key": item_key}, {"is_table": True}]
        }
        raw = self._collection.get(
            where=where, include=["documents", "metadatas"]
        )
        docs = raw.get("documents", []) or []
        metas = raw.get("metadatas", []) or []
        wanted = {str(r) for r in refs} if refs else None
        out: list[RetrievalResult] = []
        for doc, meta in zip(docs, metas, strict=True):
            if wanted is not None and meta.get("table_ref") not in wanted:
                continue
            out.append(
                RetrievalResult(
                    text=doc,
                    item_key=meta.get("item_key", ""),
                    title=meta.get("title", ""),
                    page_start=meta.get("page_start", 0),
                    page_end=meta.get("page_end", 0),
                    score=1.0,
                    chunk_idx=meta.get("chunk_idx", 0),
                    metadata=meta,
                )
            )
        out.sort(key=lambda r: r.metadata.get("table_ref", ""))
        return out

    def get_item_figures(
        self,
        item_key: str,
        refs: list[str] | set[str] | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve a paper's figure records (caption-only).

        If ``refs`` is given (canonical labels like "2"), only matching figures
        are returned — this resolves a prose passage's cited figures (e.g.
        "see Figure 2") to their caption / rough description.
        """
        where: dict = {
            "$and": [{"item_key": item_key}, {"is_figure": True}]
        }
        raw = self._collection.get(
            where=where, include=["documents", "metadatas"]
        )
        docs = raw.get("documents", []) or []
        metas = raw.get("metadatas", []) or []
        wanted = {str(r) for r in refs} if refs else None
        out: list[RetrievalResult] = []
        for doc, meta in zip(docs, metas, strict=True):
            if wanted is not None and meta.get("figure_ref") not in wanted:
                continue
            out.append(
                RetrievalResult(
                    text=doc,
                    item_key=meta.get("item_key", ""),
                    title=meta.get("title", ""),
                    page_start=meta.get("page_start", 0),
                    page_end=meta.get("page_end", 0),
                    score=1.0,
                    chunk_idx=meta.get("chunk_idx", 0),
                    metadata=meta,
                )
            )
        out.sort(key=lambda r: r.metadata.get("figure_ref", ""))
        return out

    def list_indexed_items(self) -> set[str]:
        """Return the set of item_keys currently indexed."""
        raw = self._collection.get(include=["metadatas"])
        metas = raw.get("metadatas", []) or []
        return {m.get("item_key", "") for m in metas if m.get("item_key")}

    @staticmethod
    def _build_where(
        where: dict | None, include_references: bool
    ) -> dict | None:
        """Merge user-provided where filter with reference exclusion."""
        if include_references:
            return where
        ref_filter = {"section": {"$ne": "references"}}
        if where is None:
            return ref_filter
        return {"$and": [where, ref_filter]}

    def count(self) -> int:
        return self._collection.count()

    @staticmethod
    def _to_results(raw: dict) -> list[RetrievalResult]:
        if not raw or not raw.get("documents"):
            return []
        docs = raw["documents"][0]
        metas = (
            raw["metadatas"][0]
            if raw.get("metadatas")
            else [{}] * len(docs)
        )
        dists = (
            raw["distances"][0]
            if raw.get("distances")
            else [0.0] * len(docs)
        )
        out: list[RetrievalResult] = []
        for doc, meta, dist in zip(docs, metas, dists, strict=True):
            out.append(
                RetrievalResult(
                    text=doc,
                    item_key=meta.get("item_key", ""),
                    title=meta.get("title", ""),
                    page_start=meta.get("page_start", 0),
                    page_end=meta.get("page_end", 0),
                    score=1 - dist,
                    chunk_idx=meta.get("chunk_idx", 0),
                    metadata=meta,
                )
            )
        return out
