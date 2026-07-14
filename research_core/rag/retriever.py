"""Retrieve relevant chunks from ChromaDB."""

from __future__ import annotations

from dataclasses import dataclass, field

import chromadb

from research_core.rag.store import get_collection


def _cosine_sim(a, b) -> float:
    """Dot product of two normalized vectors = cosine similarity."""
    if len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


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
class NeighborContext:
    """Expanded context for a chunk — its immediate neighbors (±N chunks)."""
    hit_chunk_idx: int = 0
    prev_chunk_ids: list[str] = field(default_factory=list)
    next_chunk_ids: list[str] = field(default_factory=list)
    full_text: str = ""  # neighbors + hit chunk concatenated in order
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
    section_context: SectionContext | None = None
    neighbor_context: NeighborContext | None = None
    # Paper-level context (populated by enrich())
    paper_abstract: str = ""
    paper_authors: str = ""
    paper_year: int = 0
    paper_doi: str = ""
    paper_keywords: str = ""
    section_heading: str = ""
    section_type: str = ""


class Retriever:
    """Query the ChromaDB collection for semantically similar chunks.

    Also owns the BM25 sparse index for hybrid keyword + semantic retrieval.
    """

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
        self._bm25 = None  # Lazy-loaded BM25Index

    @property
    def bm25(self):
        """Lazy-load the BM25 index. Returns None if not built yet."""
        if self._bm25 is None:
            from research_core.rag.bm25_index import BM25Index
            self._bm25 = BM25Index(self._persist_dir)
            self._bm25.load()  # Try loading; stays unready if file missing
        return self._bm25 if self._bm25.ready else None

    def search_bm25(self, query: str, top_k: int = 50) -> list:
        """Keyword (BM25) search across all indexed chunk texts.

        Returns list of BM25Hit. Returns empty list if BM25 index is not built.
        """
        idx = self.bm25
        if idx is None:
            return []
        return idx.search(query, top_k=top_k)

    def search(
        self,
        query: str,
        n_results: int = 8,
        where: dict | None = None,
        include_references: bool = False,
        expand_context: bool = False,
        expand_neighbors: bool = False,
    ) -> list[RetrievalResult]:
        """Semantic search across all indexed chunks.

        By default excludes reference/bibliography sections to reduce noise.
        Set include_references=True to search across all sections.

        When expand_context=True, each result's section_context is populated
        with the full text of its containing section, providing the LLM with
        complete paragraph context instead of a single isolated chunk.

        When expand_neighbors=True, each result's neighbor_context is populated
        with the hit chunk ±N surrounding chunks within the same section —
        a lighter alternative to full section expansion (~500 chars vs ~2000).
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
        elif expand_neighbors and hits:
            self._attach_neighbor_contexts(hits)

        # Always enrich with paper + section metadata from SQLite
        if hits:
            self.enrich(hits)

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

    def expand_to_neighbors(
        self,
        item_key: str,
        chunk_idx: int,
        n: int = 1,
    ) -> NeighborContext | None:
        """Expand a single chunk to include its immediate neighbors (±N chunks).

        A lightweight alternative to full section expansion — returns the hit
        chunk plus its surrounding paragraphs. Uses SQLite chunks_meta to find
        neighbor chunk IDs within the same section, then fetches texts from
        ChromaDB.

        Returns None if no neighbors are found (single-chunk sections).
        """
        chunk_id = f"{item_key}:{chunk_idx}"
        try:
            from research_core.rag.database import get_db
            conn = get_db(self._persist_dir)

            # Find the section for this chunk
            row = conn.execute(
                "SELECT section_id, page_start, page_end FROM chunks_meta WHERE id = ?",
                (chunk_id,),
            ).fetchone()
            if not row or not row[0]:
                return None

            section_id = row[0]
            page_start = row[1] or 0
            page_end = row[2] or 0

            # Get neighbor chunk IDs in this section
            chunk_rows = conn.execute(
                "SELECT id, chunk_idx, page_start, page_end FROM chunks_meta "
                "WHERE section_id = ? AND chunk_idx >= ? AND chunk_idx <= ? "
                "ORDER BY chunk_idx",
                (section_id, chunk_idx - n, chunk_idx + n),
            ).fetchall()

            if not chunk_rows:
                return None

            all_ids = [cr[0] for cr in chunk_rows]
            # Fetch texts from ChromaDB
            raw = self._collection.get(ids=all_ids, include=["documents"])
            docs = raw.get("documents", []) or []

            # Build full text in chunk_idx order
            id_to_text = {cid: txt for cid, txt in zip(all_ids, docs, strict=True)}
            ordered_texts = [id_to_text.get(cr[0], "") for cr in chunk_rows]
            full_text = "\n\n".join(t for t in ordered_texts if t)

            # Classify neighbors relative to hit chunk
            prev_ids = [cr[0] for cr in chunk_rows if cr[1] < chunk_idx]
            next_ids = [cr[0] for cr in chunk_rows if cr[1] > chunk_idx]

            return NeighborContext(
                hit_chunk_idx=chunk_idx,
                prev_chunk_ids=prev_ids,
                next_chunk_ids=next_ids,
                full_text=full_text,
                page_start=min(cr[2] or page_start for cr in chunk_rows),
                page_end=max(cr[3] or page_end for cr in chunk_rows),
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

    def _attach_neighbor_contexts(self, results: list[RetrievalResult]) -> None:
        """Populate neighbor_context for each result. Uses a cache to avoid
        duplicate DB + ChromaDB lookups."""
        cache: dict[tuple[str, int], NeighborContext | None] = {}

        for r in results:
            cache_key = (r.item_key, r.chunk_idx)
            if cache_key in cache:
                r.neighbor_context = cache[cache_key]
                continue

            ctx = self.expand_to_neighbors(r.item_key, r.chunk_idx)
            cache[cache_key] = ctx
            r.neighbor_context = ctx

    def enrich(self, results: list[RetrievalResult]) -> None:
        """Batch-fetch paper + section metadata from SQLite and inject into
        each RetrievalResult. Always-on — zero cost beyond a SQLite JOIN.

        Populates: paper_abstract, paper_authors, paper_year, paper_doi,
                   paper_keywords, section_heading, section_type.
        """
        chunk_ids = [f"{r.item_key}:{r.chunk_idx}" for r in results]
        if not chunk_ids:
            return

        try:
            from research_core.rag.database import enrich_results, get_db
            enriched = enrich_results(get_db(self._persist_dir), chunk_ids)
        except Exception:
            return

        by_id: dict[str, dict] = {}
        for er in enriched:
            if er.chunk_id:
                by_id[er.chunk_id] = {
                    "paper_abstract": er.paper_abstract,
                    "paper_authors": er.paper_authors,
                    "paper_year": er.paper_year,
                    "paper_doi": er.paper_doi,
                    "paper_keywords": er.paper_keywords,
                    "section_heading": er.section_heading,
                    "section_type": er.section_type,
                }

        for r in results:
            chunk_id = f"{r.item_key}:{r.chunk_idx}"
            ctx = by_id.get(chunk_id, {})
            if ctx:
                r.paper_abstract = ctx.get("paper_abstract", "")
                r.paper_authors = ctx.get("paper_authors", "")
                r.paper_year = ctx.get("paper_year", 0)
                r.paper_doi = ctx.get("paper_doi", "")
                r.paper_keywords = ctx.get("paper_keywords", "")
                r.section_heading = ctx.get("section_heading", "")
                r.section_type = ctx.get("section_type", "")

    def search_within_item(
        self,
        item_key: str,
        query: str,
        n_results: int = 5,
        include_references: bool = False,
    ) -> list[RetrievalResult]:
        """Search restricted to a single paper's chunks.

        Uses both BM25 (lexical) and ChromaDB (semantic) with two-way RRF
        fusion — same hybrid approach as search_papers, scoped to one paper.
        Previously only used semantic search, which missed rare terms
        appearing in the PDF body but poorly captured by embeddings.
        """
        # ── BM25 search (filtered to this paper) ──
        bm25_scores: dict[int, float] = {}  # chunk_idx → BM25 score
        bm25_texts: dict[int, str] = {}
        bm25 = self.bm25
        if bm25 is not None:
            raw_hits = bm25.search(query, top_k=200)
            for h in raw_hits:
                if h.item_key != item_key:
                    continue
                # Parse chunk_idx from chunk_id "{item_key}:{idx}"
                try:
                    cidx = int(h.chunk_id.rsplit(":", 1)[-1])
                except (ValueError, IndexError):
                    continue
                bm25_scores[cidx] = max(bm25_scores.get(cidx, 0), h.score)
                bm25_texts[cidx] = h.text

        # ── Semantic search (ChromaDB, paper-scoped) ──
        where: dict = {"item_key": item_key}
        if not include_references:
            where = {
                "$and": [
                    {"item_key": item_key},
                    {"section": {"$ne": "references"}},
                ]
            }
        semantic_results = self.search(
            query,
            n_results=max(n_results * 3, 30),
            where=where,
            include_references=True,
        )

        # ── Two-way RRF fusion ──
        semantic_ranks: dict[int, tuple[int, RetrievalResult]] = {}
        seen_idx: set[int] = set()
        for rank, r in enumerate(semantic_results):
            cidx = r.chunk_idx
            if cidx in seen_idx:
                continue
            seen_idx.add(cidx)
            semantic_ranks[cidx] = (rank + 1, r)

        rrf_k = 60
        # Collect all unique chunk indices from both sources
        all_indices = set(bm25_scores) | set(semantic_ranks)

        # Sort by BM25 score first to assign BM25 ranks
        bm25_sorted = sorted(bm25_scores.items(), key=lambda x: -x[1])
        bm25_ranks: dict[int, int] = {}
        for rank, (cidx, _) in enumerate(bm25_sorted):
            bm25_ranks[cidx] = rank + 1

        scored: list[tuple[float, int]] = []
        for cidx in all_indices:
            score = 0.0
            if cidx in bm25_ranks:
                score += 1.0 / (rrf_k + bm25_ranks[cidx])
            if cidx in semantic_ranks:
                score += 1.0 / (rrf_k + semantic_ranks[cidx][0])
            scored.append((score, cidx))
        scored.sort(reverse=True)

        # Build results, preferring enriched RetrievalResult from semantic search
        results: list[RetrievalResult] = []
        for _, cidx in scored[:n_results]:
            if cidx in semantic_ranks:
                r = semantic_ranks[cidx][1]
                r.score = round(
                    1.0 / (rrf_k + semantic_ranks[cidx][0])
                    + (1.0 / (rrf_k + bm25_ranks[cidx]) if cidx in bm25_ranks else 0),
                    4,
                )
                results.append(r)
            elif cidx in bm25_texts:
                # BM25-only hit: build a minimal RetrievalResult
                results.append(RetrievalResult(
                    text=bm25_texts[cidx],
                    item_key=item_key,
                    title="",
                    page_start=0,
                    page_end=0,
                    score=round(1.0 / (rrf_k + bm25_ranks[cidx]), 4),
                    chunk_idx=cidx,
                ))

        # Enrich any BM25-only results with paper metadata
        if results:
            self.enrich(results)

        return results

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

    def mmr_diversify(
        self,
        results: list[RetrievalResult],
        diversity_weight: float = 0.6,
        max_per_doc: int = 3,
        doc_penalty: float = 0.1,
    ) -> list[RetrievalResult]:
        """Re-rank results with Maximal Marginal Relevance for diversity.

        Operates at chunk level: selects chunks that are both relevant to
        the query AND dissimilar to already-selected chunks. A hard cap
        (max_per_doc) and per-document penalty prevent single-paper dominance.

        Uses ChromaDB-stored bge-m3 embeddings for similarity — zero extra
        computation beyond a single collection.get() call (~15ms).

        Args:
            results: Pre-ranked result list (typically post Cross-Encoder).
            diversity_weight: λ in MMR formula. 0 = pure relevance, 1 = pure
                              diversity. Production sweet spot: 0.6.
            max_per_doc: Hard cap on chunks per paper.
            doc_penalty: Score penalty per extra chunk from same paper
                         (applied after the 2nd chunk).

        Returns:
            Diversified result list in MMR order, same length as input.
        """
        if not results or len(results) <= 1:
            return results

        lam = diversity_weight
        if lam <= 0:
            return results
        lam = max(0.0, min(1.0, lam))

        # ── 1. Fetch embeddings from ChromaDB ──
        chunk_ids = [f"{r.item_key}:{r.chunk_idx}" for r in results]
        try:
            raw = self._collection.get(ids=chunk_ids, include=["embeddings"])
            id_to_emb = {}
            for cid, emb in zip(raw.get("ids", []), raw.get("embeddings", [])):
                if emb is not None:
                    id_to_emb[cid] = emb
        except Exception:
            return results  # don't break retrieval if embedding fetch fails

        if not id_to_emb:
            return results

        # ── 2. Score normalization (min-max to [0, 1]) ──
        scores = [r.score for r in results if r.score > 0]
        if not scores:
            return results
        min_s, max_s = min(scores), max(scores)
        score_range = max_s - min_s if max_s > min_s else 1.0

        # ── 3. Greedy MMR with per-document penalty ──
        remaining = list(range(len(results)))
        selected: list[int] = []
        doc_counts: dict[str, int] = {}  # item_key → chunks selected so far

        while remaining:
            best_idx = -1
            best_mmr = -float("inf")

            for i in remaining:
                cid = chunk_ids[i]
                emb = id_to_emb.get(cid)
                if emb is None:
                    continue

                # Relevance: normalized score
                relevance = (results[i].score - min_s) / score_range

                # Diversity: max similarity to any selected chunk
                max_sim = 0.0
                if selected:
                    for s in selected:
                        s_emb = id_to_emb.get(chunk_ids[s])
                        if s_emb is None:
                            continue
                        sim = _cosine_sim(emb, s_emb)
                        if sim > max_sim:
                            max_sim = sim

                # Per-document penalty
                cur_doc_count = doc_counts.get(results[i].item_key, 0)
                penalty = 0.0
                if cur_doc_count >= 2:
                    penalty = (cur_doc_count - 1) * doc_penalty

                mmr = lam * relevance - (1 - lam) * max_sim - penalty

                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i

            # Hard cap: skip if doc already has max_per_doc chunks selected
            if best_idx >= 0 and doc_counts.get(results[best_idx].item_key, 0) >= max_per_doc:
                remaining.remove(best_idx)
                continue

            if best_idx < 0:
                break

            selected.append(best_idx)
            remaining.remove(best_idx)
            doc_counts[results[best_idx].item_key] = \
                doc_counts.get(results[best_idx].item_key, 0) + 1

        return [results[i] for i in selected]

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
