"""Atomic generation management for the local RAG index.

Chroma collections, SQLite metadata, and BM25 snapshots cannot be committed as
one filesystem transaction. This module hides that coordination behind a small
interface: callers build in a new generation, validate it, then atomically
replace one active-generation pointer. Readers only ever resolve that pointer.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from research_core.rag.bm25_index import BM25_FILENAME
from research_core.rag.database import DB_FILENAME

ACTIVE_GENERATION_FILENAME = "_active_index_generation.json"
GENERATIONS_DIRNAME = "_index_generations"
COLLECTION_PREFIX = "research_chunks__"
POINTER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IndexGeneration:
    """One independently readable RAG index generation."""

    build_id: str
    persist_dir: str
    collection_name: str
    legacy: bool = False


class IndexGenerationStore:
    """Own active-generation resolution and atomic promotion for one index root."""

    def __init__(self, root_dir: str = ".chroma_db"):
        self._root = Path(root_dir).expanduser()

    @property
    def root_dir(self) -> str:
        return str(self._root)

    def active(self) -> IndexGeneration:
        """Return the active generation, or the pre-generation legacy layout."""
        pointer_path = self._root / ACTIVE_GENERATION_FILENAME
        pointer = self._read_pointer()
        if pointer is None:
            if pointer_path.exists():
                raise RuntimeError(
                    "Active index-generation pointer is invalid or references "
                    "a missing generation; refusing legacy fallback"
                )
            return IndexGeneration(
                build_id="legacy",
                persist_dir=str(self._root),
                collection_name="research_chunks",
                legacy=True,
            )
        return IndexGeneration(
            build_id=pointer["build_id"],
            persist_dir=str(self._generation_dir(pointer["build_id"])),
            collection_name=pointer["collection_name"],
        )

    def begin(self) -> IndexGeneration:
        """Reserve an empty, unreachable generation for a new index build."""
        build_id = uuid.uuid4().hex
        generation_dir = self._generation_dir(build_id)
        generation_dir.mkdir(parents=True, exist_ok=False)
        return IndexGeneration(
            build_id=build_id,
            persist_dir=str(generation_dir),
            collection_name=f"{COLLECTION_PREFIX}{build_id}",
        )

    def clone_metadata(self, source: IndexGeneration, target: IndexGeneration) -> None:
        """Copy SQLite and BM25 state into an unreachable target generation."""
        source_dir = Path(source.persist_dir)
        target_dir = Path(target.persist_dir)
        source_db = source_dir / DB_FILENAME
        target_db = target_dir / DB_FILENAME
        if source_db.is_file():
            with sqlite3.connect(source_db) as source_conn:
                with sqlite3.connect(target_db) as target_conn:
                    source_conn.backup(target_conn)

        source_bm25 = source_dir / BM25_FILENAME
        if source_bm25.is_file():
            shutil.copy2(source_bm25, target_dir / BM25_FILENAME)

    def activate(self, generation: IndexGeneration) -> None:
        """Atomically make a fully validated generation visible to readers."""
        if generation.legacy:
            raise ValueError("Legacy layout cannot be promoted as a generation")
        generation_dir = Path(generation.persist_dir)
        if not generation_dir.is_dir():
            raise FileNotFoundError(f"Generation directory is missing: {generation_dir}")

        self._root.mkdir(parents=True, exist_ok=True)
        pointer = self._root / ACTIVE_GENERATION_FILENAME
        temporary = self._root / f".{ACTIVE_GENERATION_FILENAME}.{generation.build_id}.tmp"
        payload = {
            "schema_version": POINTER_SCHEMA_VERSION,
            "build_id": generation.build_id,
            "collection_name": generation.collection_name,
        }
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, pointer)
        try:
            directory_fd = os.open(self._root, os.O_RDONLY)
        except OSError:
            # Windows may reject opening a directory handle altogether.
            pass
        else:
            try:
                os.fsync(directory_fd)
            except OSError:
                # Windows does not support fsync on a directory handle.
                pass
            finally:
                os.close(directory_fd)

    def stale_generations(self, keep: int = 2) -> list[IndexGeneration]:
        """Return ready generations older than the newest ``keep`` versions.

        Interrupted and degraded builds are intentionally excluded: they are
        unreachable through the active pointer but retain their manifests for
        diagnosis instead of being silently cleaned by a later successful sync.
        """
        active = self.active()
        base = self._root / GENERATIONS_DIRNAME
        if not base.is_dir():
            return []
        candidates = sorted(
            (
                path
                for path in base.iterdir()
                if path.is_dir() and self._is_ready_or_unmarked(path)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        retained = {path.name for path in candidates[:keep]}
        retained.add(active.build_id)
        return [
            IndexGeneration(
                build_id=path.name,
                persist_dir=str(path),
                collection_name=f"{COLLECTION_PREFIX}{path.name}",
            )
            for path in candidates
            if path.name not in retained
        ]

    def discard(self, generation: IndexGeneration) -> None:
        """Remove an unreachable or retired generation's filesystem state."""
        if generation.legacy:
            return
        path = Path(generation.persist_dir)
        expected_parent = self._root / GENERATIONS_DIRNAME
        if path.parent != expected_parent:
            raise ValueError("Refusing to remove a generation outside the index root")
        shutil.rmtree(path, ignore_errors=True)

    def _generation_dir(self, build_id: str) -> Path:
        return self._root / GENERATIONS_DIRNAME / build_id

    def _read_pointer(self) -> dict | None:
        path = self._root / ACTIVE_GENERATION_FILENAME
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != POINTER_SCHEMA_VERSION:
                return None
            if not isinstance(payload.get("build_id"), str):
                return None
            if not isinstance(payload.get("collection_name"), str):
                return None
            if not self._generation_dir(payload["build_id"]).is_dir():
                return None
            return payload
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _is_ready_or_unmarked(generation_dir: Path) -> bool:
        manifest_path = generation_dir / "_index_manifest.json"
        if not manifest_path.is_file():
            return True
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8")).get("status") == "ready"
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
