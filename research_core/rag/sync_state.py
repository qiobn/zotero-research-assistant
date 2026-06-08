"""Persistent sync state for incremental indexing.

Tracks which items have been indexed and at what Zotero version,
so subsequent syncs only process new or modified items.

Also tracks chunking strategy version to force rebuild when the
chunking algorithm is upgraded.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from research_core.parsers.chunker import CHUNKING_VERSION


@dataclass
class SyncState:
    """Tracks indexed item versions for incremental sync."""

    item_versions: dict[str, int] = field(default_factory=dict)
    embedding_model: str = ""
    chunking_version: str = ""

    _path: str = field(default="", repr=False)

    @classmethod
    def load(cls, persist_dir: str) -> SyncState:
        path = os.path.join(persist_dir, "_sync_state.json")
        state = cls(_path=path)
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                state.item_versions = data.get("item_versions", {})
                state.embedding_model = data.get("embedding_model", "")
                state.chunking_version = data.get(
                    "chunking_version", ""
                )
            except Exception as e:
                logger.warning(
                    f"Failed to load sync state, starting fresh: {e}"
                )
        return state

    def save(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(
                {
                    "item_versions": self.item_versions,
                    "embedding_model": self.embedding_model,
                    "chunking_version": self.chunking_version,
                },
                f,
                indent=2,
            )

    def needs_rebuild(self, embedding_model: str) -> str | None:
        """Check if a full rebuild is needed. Returns reason or None."""
        if (
            self.embedding_model
            and self.embedding_model != embedding_model
        ):
            return (
                f"Embedding model changed "
                f"({self.embedding_model} -> {embedding_model})"
            )
        if (
            self.chunking_version
            and self.chunking_version != CHUNKING_VERSION
        ):
            return (
                f"Chunking strategy upgraded "
                f"({self.chunking_version} -> {CHUNKING_VERSION})"
            )
        return None

    def diff(
        self, current_versions: dict[str, int]
    ) -> tuple[set[str], set[str], set[str]]:
        """Compare stored state with current library versions.

        Returns (new_keys, modified_keys, deleted_keys).
        """
        stored = set(self.item_versions)
        current = set(current_versions)

        new_keys = current - stored
        deleted_keys = stored - current
        modified_keys = {
            k
            for k in stored & current
            if current_versions[k] != self.item_versions.get(k, -1)
        }
        return new_keys, modified_keys, deleted_keys
