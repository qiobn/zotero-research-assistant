"""Offline regression tests for RAG index build metadata."""

from __future__ import annotations

from research_core.rag.index_manifest import IndexManifest, IndexRuntime


def _runtime(**changes) -> IndexRuntime:
    values = {
        "chunking_version": "chunk-v1",
        "cleaner_version": "clean-v1",
        "clean_enabled": True,
        "embedding_backend": "onnx_int8",
        "embedding_model": "BAAI/bge-m3",
        "embedding_max_seq_len": 1024,
        "bilingual_enrichment": True,
    }
    values.update(changes)
    return IndexRuntime(**values)


def test_manifest_round_trip_and_ready_state(tmp_path):
    manifest = IndexManifest.start(_runtime())
    manifest.finish(vector_chunks=12, metadata_chunks=12, bm25_chunks=12)
    manifest.save(str(tmp_path))

    loaded = IndexManifest.load(str(tmp_path))

    assert loaded is not None
    assert loaded.build_id == manifest.build_id
    assert loaded.status == "ready"
    assert loaded.bm25_is_current is True
    assert loaded.compatibility_issues(_runtime()) == []


def test_manifest_marks_mismatched_copies_degraded(tmp_path):
    manifest = IndexManifest.start(_runtime())
    manifest.finish(vector_chunks=12, metadata_chunks=11, bm25_chunks=12)
    manifest.save(str(tmp_path))

    loaded = IndexManifest.load(str(tmp_path))

    assert loaded is not None
    assert loaded.status == "degraded"
    assert loaded.bm25_is_current is False
    assert "Index copies disagree" in loaded.error


def test_manifest_detects_all_corpus_affecting_runtime_changes():
    manifest = IndexManifest.start(_runtime())
    manifest.finish(vector_chunks=1, metadata_chunks=1, bm25_chunks=1)

    issues = manifest.compatibility_issues(
        _runtime(clean_enabled=False, embedding_max_seq_len=512)
    )

    assert any(issue.startswith("clean_enabled changed") for issue in issues)
    assert any(issue.startswith("embedding_max_seq_len changed") for issue in issues)
