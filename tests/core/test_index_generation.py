"""Offline tests for atomic RAG index generation promotion."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from research_core.rag.bm25_index import BM25_FILENAME
from research_core.rag.database import DB_FILENAME
from research_core.rag.index_generation import IndexGeneration, IndexGenerationStore
from research_core.rag.index_manifest import IndexManifest, IndexRuntime
from research_core.rag.retriever import Retriever
from research_core.rag.store import clone_collection
from research_core.rag.sync_state import SyncState
from research_core.tools.admin import _promote_generation


def _manifest(generation, *, ready: bool) -> IndexManifest:
    runtime = IndexRuntime(
        chunking_version="chunk-v1",
        cleaner_version="clean-v1",
        clean_enabled=True,
        embedding_backend="test",
        embedding_model="test",
        embedding_max_seq_len=1,
        bilingual_enrichment=False,
    )
    manifest = IndexManifest.start(runtime, build_id=generation.build_id)
    manifest.finish(
        vector_chunks=1,
        metadata_chunks=1 if ready else 0,
        bm25_chunks=1,
    )
    manifest.save(generation.persist_dir)
    return manifest


def test_unactivated_generation_never_replaces_active_generation(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    first = store.begin()
    store.activate(first)

    unfinished = store.begin()

    assert store.active() == first
    assert Path(unfinished.persist_dir).is_dir()


def test_activation_replaces_one_pointer_atomically(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    first = store.begin()
    store.activate(first)
    second = store.begin()

    store.activate(second)

    assert store.active() == second
    assert not list(tmp_path.glob(".*.tmp"))


def test_legacy_layout_remains_active_until_first_promotion(tmp_path):
    store = IndexGenerationStore(str(tmp_path))

    assert store.active().legacy is True
    assert store.active().persist_dir == str(tmp_path)

    generation = store.begin()
    store.activate(generation)

    assert store.active().legacy is False
    assert store.active().collection_name == generation.collection_name


def test_invalid_pointer_never_silently_falls_back_to_legacy(tmp_path):
    pointer = tmp_path / "_active_index_generation.json"
    pointer.write_text('{"schema_version": 1, "build_id": "missing", "collection_name": "x"}')

    with pytest.raises(RuntimeError, match="refusing legacy fallback"):
        IndexGenerationStore(str(tmp_path)).active()


def test_clone_metadata_copies_sqlite_and_bm25_without_touching_source(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    source = store.begin()
    source_db = Path(source.persist_dir) / DB_FILENAME
    with sqlite3.connect(source_db) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('source')")
    source_bm25 = Path(source.persist_dir) / BM25_FILENAME
    source_bm25.write_bytes(b"sparse-index")

    target = store.begin()
    store.clone_metadata(source, target)

    with sqlite3.connect(Path(target.persist_dir) / DB_FILENAME) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "source"
    assert (Path(target.persist_dir) / BM25_FILENAME).read_bytes() == b"sparse-index"
    assert source_bm25.read_bytes() == b"sparse-index"


def test_cleanup_never_removes_active_generation(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    first = store.begin()
    store.activate(first)
    second = store.begin()
    store.activate(second)
    third = store.begin()
    store.activate(third)
    os.utime(first.persist_dir, (1, 1))
    os.utime(second.persist_dir, (2, 2))
    os.utime(third.persist_dir, (3, 3))

    stale = store.stale_generations(keep=2)

    assert {generation.build_id for generation in stale} == {first.build_id}
    for generation in stale:
        store.discard(generation)
    assert store.active() == third
    assert Path(third.persist_dir).is_dir()


def test_cleanup_retains_degraded_generation_for_diagnosis(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    failed = store.begin()
    (Path(failed.persist_dir) / "_index_manifest.json").write_text('{"status": "degraded"}')
    ready = store.begin()
    store.activate(ready)

    assert failed not in store.stale_generations(keep=0)


def test_discard_rejects_paths_outside_generation_directory(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    external = IndexGeneration("outside", str(tmp_path.parent), "not-a-generation")

    with pytest.raises(ValueError, match="outside the index root"):
        store.discard(external)


def test_only_ready_manifest_promotes_and_saves_sync_state(tmp_path):
    store = IndexGenerationStore(str(tmp_path))
    state = SyncState(item_versions={"PAPER": 1}, _path=str(tmp_path / "_sync_state.json"))
    first = store.begin()
    ready = _manifest(first, ready=True)

    assert _promote_generation(
        generation_store=store,
        generation=first,
        manifest=ready,
        root_persist_dir=str(tmp_path),
        state=state,
    )
    assert store.active() == first
    assert (tmp_path / "_sync_state.json").is_file()

    failed = store.begin()
    degraded = _manifest(failed, ready=False)
    state.item_versions["PAPER"] = 2

    assert not _promote_generation(
        generation_store=store,
        generation=failed,
        manifest=degraded,
        root_persist_dir=str(tmp_path),
        state=state,
    )
    assert store.active() == first


def test_clone_collection_preserves_documents_metadata_and_embeddings(monkeypatch):
    class Source:
        def get(self, *, limit, offset, include):
            if offset:
                return {"ids": []}
            return {
                "ids": ["PAPER:0"],
                "documents": ["indexed text"],
                "metadatas": [{"item_key": "PAPER"}],
                "embeddings": [[0.1, 0.2]],
            }

    class Target:
        def __init__(self):
            self.records = []

        def add(self, **records):
            self.records.append(records)

    source = Source()
    target = Target()
    collections = {"source": source, "target": target}
    monkeypatch.setattr(
        "research_core.rag.store.get_collection",
        lambda _path, name: collections[name],
    )

    assert clone_collection("/index", "source", "target") == 1
    assert target.records == [{
        "ids": ["PAPER:0"],
        "documents": ["indexed text"],
        "metadatas": [{"item_key": "PAPER"}],
        "embeddings": [[0.1, 0.2]],
    }]


def test_retriever_rebinds_after_pointer_promotion(tmp_path, monkeypatch):
    class Collection:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    store = IndexGenerationStore(str(tmp_path))
    first = store.begin()
    store.activate(first)
    second = store.begin()
    collections = {
        first.collection_name: Collection(3),
        second.collection_name: Collection(7),
    }
    monkeypatch.setattr(
        "research_core.rag.retriever.get_collection",
        lambda _path, name: collections[name],
    )

    retriever = Retriever(persist_dir=str(tmp_path))
    assert retriever.count() == 3

    store.activate(second)

    assert retriever.count() == 7
    assert retriever._persist_dir == second.persist_dir
