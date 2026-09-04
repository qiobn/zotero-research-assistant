"""Versioned metadata for one locally built RAG index.

The manifest is the single record that ties together the Chroma collection,
SQLite metadata database, BM25 snapshot, and the runtime settings that shape
their corpus. It deliberately does not own those stores; callers only need to
create a build, finish it with observed counts, and reject stale sparse state.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from research_core.parsers.chunker import CHUNKING_VERSION
from research_core.parsers.text_cleaner import CLEANER_VERSION

MANIFEST_FILENAME = "_index_manifest.json"
MANIFEST_SCHEMA_VERSION = 1
BM25_TOKENIZER_VERSION = "cjk-unigram-bigram-ascii-v1"


def _enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() == "true"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IndexRuntime:
    """Configuration values that change indexed text or vector semantics."""

    chunking_version: str
    cleaner_version: str
    clean_enabled: bool
    embedding_backend: str
    embedding_model: str
    embedding_max_seq_len: int
    bilingual_enrichment: bool
    bm25_tokenizer_version: str = BM25_TOKENIZER_VERSION

    @classmethod
    def from_environment(cls) -> IndexRuntime:
        return cls(
            chunking_version=CHUNKING_VERSION,
            cleaner_version=CLEANER_VERSION,
            clean_enabled=_enabled("ZRA_CLEAN_ENABLED"),
            embedding_backend=os.getenv("EMBEDDING_BACKEND", "auto").strip().lower(),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
            embedding_max_seq_len=int(os.getenv("EMBEDDING_MAX_SEQ_LEN", "1024")),
            bilingual_enrichment=_enabled("ZRA_INDEX_BILINGUAL_ENRICHMENT"),
        )


@dataclass
class IndexManifest:
    """Persisted status and invariants for a RAG index build."""

    build_id: str
    runtime: IndexRuntime
    status: str = "building"
    created_at: str = field(default_factory=_timestamp)
    completed_at: str = ""
    vector_chunks: int = 0
    metadata_chunks: int = 0
    bm25_chunks: int = 0
    error: str = ""
    schema_version: int = MANIFEST_SCHEMA_VERSION

    @classmethod
    def start(
        cls, runtime: IndexRuntime, build_id: str | None = None
    ) -> IndexManifest:
        return cls(build_id=build_id or uuid.uuid4().hex, runtime=runtime)

    @classmethod
    def load(cls, persist_dir: str) -> IndexManifest | None:
        path = Path(persist_dir) / MANIFEST_FILENAME
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
                logger.warning("Index manifest schema is unsupported; rebuilding is required")
                return None
            runtime = IndexRuntime(**data.pop("runtime"))
            return cls(runtime=runtime, **data)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Failed to read index manifest: {exc}")
            return None

    def save(self, persist_dir: str) -> None:
        """Atomically replace the manifest after a state transition."""
        directory = Path(persist_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MANIFEST_FILENAME
        temporary = directory / f".{MANIFEST_FILENAME}.{self.build_id}.tmp"
        payload = asdict(self)
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def compatibility_issues(self, runtime: IndexRuntime) -> list[str]:
        """Return corpus-affecting runtime changes that require rebuilding."""
        if self.status != "ready":
            return [f"Index build is {self.status}"]
        issues: list[str] = []
        for name in (
            "chunking_version",
            "cleaner_version",
            "clean_enabled",
            "embedding_backend",
            "embedding_model",
            "embedding_max_seq_len",
            "bilingual_enrichment",
            "bm25_tokenizer_version",
        ):
            if getattr(self.runtime, name) != getattr(runtime, name):
                issues.append(
                    f"{name} changed ({getattr(self.runtime, name)!r} -> "
                    f"{getattr(runtime, name)!r})"
                )
        return issues

    def finish(
        self,
        *,
        vector_chunks: int,
        metadata_chunks: int,
        bm25_chunks: int,
        error: str = "",
    ) -> None:
        """Mark the build ready only when all searchable copies agree."""
        self.vector_chunks = vector_chunks
        self.metadata_chunks = metadata_chunks
        self.bm25_chunks = bm25_chunks
        self.completed_at = _timestamp()
        counts_match = vector_chunks == metadata_chunks == bm25_chunks
        self.status = "ready" if counts_match and not error else "degraded"
        self.error = error or (
            "Index copies disagree: "
            f"vector={vector_chunks}, metadata={metadata_chunks}, bm25={bm25_chunks}"
            if not counts_match
            else ""
        )

    @property
    def bm25_is_current(self) -> bool:
        return self.status == "ready" and self.bm25_chunks == self.vector_chunks
