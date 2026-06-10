"""Optional cross-encoder reranker for improving retrieval precision.

When enabled, the retriever over-fetches candidates and this module re-scores
them with a cross-encoder model, keeping only the top-k most relevant results.

Controlled by RERANKER_MODEL env var. Set to empty string to disable.
Default: cross-encoder/ms-marco-MiniLM-L-6-v2 (fast, ~80MB).
"""

from __future__ import annotations

import os
import threading

from loguru import logger

_DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Lazy-loaded cross-encoder for reranking retrieval results."""

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or os.getenv("RERANKER_MODEL", _DEFAULT_MODEL)
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info(f"Loading reranker model: {self._model_name}")
            self._model = CrossEncoder(self._model_name)
            logger.info("Reranker model loaded")

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """Re-score documents against a query using a cross-encoder.

        Returns list of (original_index, score) sorted by score descending,
        truncated to top_k if specified.
        """
        if not documents:
            return []
        self._load()
        pairs = [[query, doc] for doc in documents]
        scores = self._model.predict(pairs)
        indexed_scores = list(enumerate(scores.tolist()))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        if top_k:
            indexed_scores = indexed_scores[:top_k]
        return indexed_scores


_singleton: CrossEncoderReranker | None = None
_disabled: bool | None = None
_init_lock = threading.Lock()


def get_reranker() -> CrossEncoderReranker | None:
    """Return a singleton reranker, or None if disabled via env. Thread-safe."""
    global _singleton, _disabled
    if _disabled is None:
        with _init_lock:
            if _disabled is None:
                model = os.getenv("RERANKER_MODEL", _DEFAULT_MODEL)
                _disabled = model.strip() == "" or model.lower() == "none"
    if _disabled:
        return None
    if _singleton is None:
        with _init_lock:
            if _singleton is None:
                _singleton = CrossEncoderReranker()
    return _singleton
