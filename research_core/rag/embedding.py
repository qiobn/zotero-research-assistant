"""Shared embedding function for ChromaDB backed by sentence-transformers.

Loads the model once (lazy singleton). Defaults to BAAI/bge-m3 which supports
Chinese + English + multilingual with 1024-dim dense vectors.
"""

from __future__ import annotations

import os
import threading

from chromadb.api.types import (
    Documents,
    EmbeddingFunction,
    Embeddings,
)
from loguru import logger

_DEFAULT_MODEL = "BAAI/bge-m3"


class SentenceTransformerEmbedding(EmbeddingFunction[Documents]):
    """ChromaDB-compatible wrapper around sentence-transformers."""

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or os.getenv("EMBEDDING_MODEL", _DEFAULT_MODEL)
        self._model = None
        self._batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

    def name(self) -> str:
        return f"sentence-transformer-{self._model_name}"

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._apply_hf_mirror()
            device = self._resolve_device()
            logger.info(f"Loading embedding model: {self._model_name} (device={device})")
            try:
                self._model = SentenceTransformer(self._model_name, device=device)
            except Exception:
                if not os.environ.get("HF_ENDPOINT"):
                    logger.warning(
                        "Model download failed. If you are in China, set "
                        "HF_ENDPOINT=https://hf-mirror.com in your .env file and retry."
                    )
                raise
            logger.info(f"Embedding model loaded, dim={self._model.get_embedding_dimension()}")

    @staticmethod
    def _resolve_device() -> str | None:
        """Pick the fastest available device: cuda → mps → cpu.

        Honors EMBEDDING_DEVICE override. Returns None to let
        sentence-transformers auto-select if torch is unavailable.
        """
        override = os.getenv("EMBEDDING_DEVICE", "").strip()
        if override:
            return override
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
            return "cpu"
        except Exception:
            return None

    @staticmethod
    def _apply_hf_mirror():
        """Apply HF_ENDPOINT from .env if set, enabling China mirror support."""
        endpoint = os.environ.get("HF_ENDPOINT")
        if endpoint:
            os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)
            logger.info(f"Using HuggingFace mirror: {endpoint}")

    def __call__(self, input: Documents) -> Embeddings:
        self._load()
        embeddings = self._model.encode(
            input,
            normalize_embeddings=True,
            batch_size=self._batch_size,
            convert_to_numpy=True,
        )
        return embeddings.tolist()


_singleton: SentenceTransformerEmbedding | None = None
_init_lock = threading.Lock()


def get_embedding_function() -> SentenceTransformerEmbedding:
    """Return a singleton embedding function. Thread-safe via double-checked locking."""
    global _singleton
    if _singleton is None:
        with _init_lock:
            if _singleton is None:
                _singleton = SentenceTransformerEmbedding()
    return _singleton
