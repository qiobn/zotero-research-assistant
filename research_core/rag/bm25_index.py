"""BM25 sparse keyword index on chunk texts.

Provides exact-match / keyword retrieval over all indexed chunks,
complementing ChromaDB's semantic (dense) search. The two are merged
via three-way RRF fusion in search_papers().

Why BM25 on chunks when Zotero API already does keyword search?
Zotero searches metadata only (title, abstract, tags). BM25 searches
the ACTUAL PDF chunk text — critical for finding method names, variable
definitions, dataset references, and other terms that appear only in
the body text.

Tokenizer: CJK character unigrams + bigrams + ASCII words (>=2 chars).
Persists to .chroma_db/_bm25_index.pkl alongside the vector index.
"""

from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass

from loguru import logger

BM25_FILENAME = "_bm25_index.pkl"

# ── Tokenizer ────────────────────────────────────────────────────────

# Matches ASCII words: at least 2 alphabetic chars, not pure digits
_ASCII_WORD = re.compile(r"[a-zA-Z]{2,}")


def _is_cjk(c: str) -> bool:
    """Check if character is in the CJK Unified Ideographs range."""
    return "一" <= c <= "鿿"


def tokenize(text: str) -> list[str]:
    """Tokenize mixed CN/EN academic text for BM25 indexing.

    CJK: character unigrams + bigrams (no segmenter needed).
    EN: lowercase alpha words of length >= 2.

    This hybrid approach handles Chinese academic papers with embedded
    English terminology, which is the dominant pattern in the user's corpus.
    """
    tokens: list[str] = []

    # ── CJK: unigrams + bigrams ──
    cjk_chars = [c for c in text if _is_cjk(c)]
    for c in cjk_chars:
        tokens.append(c)
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i] + cjk_chars[i + 1])

    # ── EN: lowercase alpha words ──
    en_words = _ASCII_WORD.findall(text.lower())
    tokens.extend(en_words)

    return tokens


# ── BM25 Index ───────────────────────────────────────────────────────


@dataclass
class BM25Hit:
    """A single BM25 search result."""
    chunk_id: str       # "{item_key}:{chunk_idx}"
    item_key: str
    score: float
    text: str           # snippet of the matched chunk


class BM25Index:
    """BM25 sparse index over all chunk texts in the library.

    Built from ChromaDB's stored documents. Persisted as a pickle file
    alongside the vector index so it survives server restarts.

    Usage:
        index = BM25Index(persist_dir)
        index.build_from_collection(chroma_collection)
        hits = index.search("gravity model", top_k=50)
    """

    def __init__(self, persist_dir: str = ".chroma_db"):
        self._persist_dir = persist_dir
        self._pickle_path = os.path.join(persist_dir, BM25_FILENAME)
        self._model = None          # BM25Okapi instance
        self._chunk_ids: list[str] = []      # parallel to _corpus
        self._item_keys: list[str] = []      # parallel to _corpus
        self._chunk_texts: list[str] = []    # parallel to _corpus
        self._ready = False

    # ── Properties ────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def chunk_count(self) -> int:
        return len(self._chunk_ids)

    # ── Build ─────────────────────────────────────────────────────

    def build_from_collection(self, collection) -> int:
        """Build BM25 index from all documents in a ChromaDB collection.

        Fetches all chunk texts and IDs via paginated scan, tokenizes
        each one, and builds the BM25Okapi model.

        Returns the number of chunks indexed.
        """
        from rank_bm25 import BM25Okapi

        logger.info("BM25: scanning ChromaDB collection for chunk texts...")

        # Paginated fetch to avoid OOM on large collections
        all_ids: list[str] = []
        all_texts: list[str] = []
        all_items: list[str] = []
        page_size = 500
        offset = 0

        while True:
            result = collection.get(
                limit=page_size,
                offset=offset,
                include=["documents", "metadatas"],
            )
            ids_batch = result.get("ids", [])
            if not ids_batch:
                break

            docs_batch = result.get("documents", []) or []
            metas_batch = result.get("metadatas", []) or []

            all_ids.extend(ids_batch)
            all_texts.extend(docs_batch)
            for meta in metas_batch:
                all_items.append(meta.get("item_key", "") if meta else "")

            offset += page_size
            if offset % 5000 == 0:
                logger.info(f"BM25: scanned {offset} chunks...")

        if not all_ids:
            logger.warning("BM25: no chunks found in collection — index empty")
            # Do not leave a previous corpus on disk: a zero-vector index and a
            # stale sparse pickle would otherwise look superficially healthy.
            self.delete()
            self._chunk_ids = []
            self._item_keys = []
            self._chunk_texts = []
            return 0

        # Tokenize all texts
        logger.info(f"BM25: tokenizing {len(all_texts)} chunks...")
        tokenized = [tokenize(t) for t in all_texts]

        # Build BM25 model
        logger.info("BM25: building Okapi model...")
        self._model = BM25Okapi(tokenized)
        self._chunk_ids = all_ids
        self._item_keys = all_items
        self._chunk_texts = all_texts
        self._ready = True

        # Persist
        self._save()

        logger.info(
            f"BM25: index built — {len(all_ids)} chunks, "
            f"{sum(len(t) for t in tokenized):,} tokens"
        )
        return len(all_ids)

    # ── Search ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 50,
    ) -> list[BM25Hit]:
        """Search the BM25 index for chunks matching the query.

        Args:
            query: Keyword query string.
            top_k: Max number of results to return.

        Returns:
            Ranked list of BM25Hit, ordered by BM25 score descending.
        """
        if not self._ready or self._model is None:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self._model.get_scores(tokens)
        # Get indices of top-k scores
        if top_k >= len(scores):
            top_indices = list(range(len(scores)))
        else:
            # Use numpy-style argpartition for efficiency
            import numpy as np
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        hits: list[BM25Hit] = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            chunk_id = self._chunk_ids[idx]
            item_key = self._item_keys[idx] if idx < len(self._item_keys) else ""
            if not item_key:
                # Parse from chunk_id: "{item_key}:{chunk_idx}"
                item_key = chunk_id.rsplit(":", 1)[0] if ":" in chunk_id else ""

            hits.append(BM25Hit(
                chunk_id=chunk_id,
                item_key=item_key,
                score=float(scores[idx]),
                text=self._chunk_texts[idx][:300] if idx < len(self._chunk_texts) else "",
            ))

        return hits

    # ── Persistence ────────────────────────────────────────────────

    def _save(self) -> None:
        """Serialize BM25 model and metadata to pickle."""
        os.makedirs(self._persist_dir, exist_ok=True)
        data = {
            "model": self._model,
            "chunk_ids": self._chunk_ids,
            "item_keys": self._item_keys,
            "chunk_texts": self._chunk_texts,
        }
        with open(self._pickle_path, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.debug(f"BM25 index saved to {self._pickle_path}")

    def load(self) -> bool:
        """Load BM25 model from pickle. Returns True if loaded successfully."""
        if not os.path.exists(self._pickle_path):
            logger.debug("BM25: no saved index found")
            return False

        try:
            with open(self._pickle_path, "rb") as f:
                data = pickle.load(f)

            self._model = data.get("model")
            self._chunk_ids = data.get("chunk_ids", [])
            self._item_keys = data.get("item_keys", [])
            self._chunk_texts = data.get("chunk_texts", [])

            if self._model is None:
                logger.warning("BM25: saved index has no model — needs rebuild")
                return False

            self._ready = True
            logger.info(
                f"BM25: loaded index — {len(self._chunk_ids)} chunks"
            )
            return True
        except Exception as e:
            logger.warning(f"BM25: failed to load index: {e}")
            return False

    def delete(self) -> None:
        """Remove the persisted BM25 index file."""
        if os.path.exists(self._pickle_path):
            os.remove(self._pickle_path)
        self._ready = False
        self._model = None
