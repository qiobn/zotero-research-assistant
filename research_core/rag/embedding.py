"""Embedding function for ChromaDB — pluggable backends.

Supports two backends, controlled by EMBEDDING_BACKEND:

  auto (default)              — ONNX INT8 if available, otherwise sentence-transformers
  onnx_int8                   — ONNX Runtime INT8 (2-3x faster, 4x smaller, <2% precision loss)
  sentence_transformers       — original FP32 PyTorch (highest precision)

ONNX INT8 backend downloads a community-maintained pre-quantized model
(~347 MB vs 2.3 GB for FP32) from HuggingFace. The model is cached in
~/.cache/huggingface/ after first download. Falls back gracefully to
sentence-transformers if ONNX Runtime or the model is unavailable.
"""

from __future__ import annotations

import os
import threading

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from loguru import logger

_DEFAULT_MODEL = "BAAI/bge-m3"
_ONNX_INT8_MODEL = "skatzR/USER-BGE-M3-ONNX-INT8"


# ── Sentence-Transformers backend (FP32) ──────────────────────────────


class SentenceTransformerEmbedding(EmbeddingFunction[Documents]):
    """ChromaDB-compatible wrapper around sentence-transformers (FP32)."""

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or os.getenv("EMBEDDING_MODEL", _DEFAULT_MODEL)
        self._model = None
        self._batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
        self._max_seq_len = int(os.getenv("EMBEDDING_MAX_SEQ_LEN", "1024"))

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
            try:
                if self._max_seq_len > 0 and self._model.max_seq_length > self._max_seq_len:
                    self._model.max_seq_length = self._max_seq_len
            except Exception:
                pass
            logger.info(
                f"Embedding model loaded, dim={self._model.get_embedding_dimension()}, "
                f"max_seq_length={getattr(self._model, 'max_seq_length', '?')}"
            )

    @staticmethod
    def _resolve_device() -> str | None:
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


# ── ONNX INT8 backend ─────────────────────────────────────────────────


class ONNXInt8Embedding(EmbeddingFunction[Documents]):
    """ONNX Runtime INT8 quantized embedding — 2-3x faster, 4x smaller.

    Uses a community-maintained pre-quantized ONNX model. The model file
    (~347 MB) is downloaded from HuggingFace on first use and cached.
    Requires onnxruntime and transformers (tokenizer only).
    """

    def __init__(self) -> None:
        self._session = None
        self._tokenizer = None
        self._batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
        self._max_seq_len = int(os.getenv("EMBEDDING_MAX_SEQ_LEN", "1024"))
        self._loaded = False
        self._dim = 1024  # bge-m3 output dimension

    def name(self) -> str:
        return f"onnx-int8-{_ONNX_INT8_MODEL}"

    def _load(self):
        if self._loaded:
            return

        import onnxruntime as ort
        from huggingface_hub import snapshot_download, try_to_load_from_cache
        from transformers import AutoTokenizer

        self._apply_hf_mirror()

        # Try local cache first — avoids network check on every startup
        cached = try_to_load_from_cache(
            _ONNX_INT8_MODEL, "model_quantized.onnx"
        )
        if cached:
            model_path = os.path.dirname(cached)
            logger.info(f"Using cached ONNX model: {model_path}")
        else:
            logger.info(f"Downloading ONNX INT8 model: {_ONNX_INT8_MODEL}")
            model_path = snapshot_download(_ONNX_INT8_MODEL)

        logger.info(f"Loading ONNX model from {model_path}")
        self._session = ort.InferenceSession(
            f"{model_path}/model_quantized.onnx",
            providers=["CPUExecutionProvider"],
        )

        self._tokenizer = AutoTokenizer.from_pretrained(model_path)
        self._loaded = True
        logger.info(f"ONNX INT8 model loaded, dim={self._dim}")

    @staticmethod
    def _apply_hf_mirror():
        endpoint = os.environ.get("HF_ENDPOINT")
        if endpoint:
            os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)

    def __call__(self, input: Documents) -> Embeddings:
        import numpy as np

        self._load()
        embeddings: list[np.ndarray] = []

        for i in range(0, len(input), self._batch_size):
            batch = input[i : i + self._batch_size]
            enc = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self._max_seq_len,
                return_tensors="np",
            )
            out = self._session.run(
                None,
                {
                    "input_ids": enc["input_ids"].astype(np.int64),
                    "attention_mask": enc["attention_mask"].astype(np.int64),
                },
            )
            # CLS pooling + L2 normalize (BGE models use CLS token)
            cls_emb = out[0][:, 0, :]
            cls_emb = cls_emb / np.linalg.norm(cls_emb, axis=1, keepdims=True)
            embeddings.append(cls_emb)

        return np.concatenate(embeddings).tolist()


# ── Backend selection ──────────────────────────────────────────────────


_singleton: EmbeddingFunction | None = None
_init_lock = threading.Lock()


def get_embedding_function() -> EmbeddingFunction:
    """Return a singleton embedding function based on EMBEDDING_BACKEND.

    Thread-safe via double-checked locking. Backend is selected once and
    cached for the lifetime of the process.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    with _init_lock:
        if _singleton is not None:
            return _singleton

        backend = os.getenv("EMBEDDING_BACKEND", "auto").strip().lower()

        if backend in ("onnx_int8", "auto"):
            try:
                ef = ONNXInt8Embedding()
                ef._load()  # eager load to catch errors early
                _singleton = ef
                logger.info("Embedding backend: ONNX INT8 (onnxruntime)")
                return _singleton
            except Exception as e:
                if backend == "onnx_int8":
                    raise RuntimeError(
                        f"EMBEDDING_BACKEND=onnx_int8 but failed to load: {e}"
                    ) from e
                logger.warning(
                    f"ONNX INT8 backend unavailable ({e}), "
                    f"falling back to sentence-transformers."
                )

        # Default / fallback: sentence-transformers FP32
        _singleton = SentenceTransformerEmbedding()
        logger.info("Embedding backend: sentence-transformers (FP32)")
        return _singleton
