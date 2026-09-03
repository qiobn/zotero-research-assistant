"""Offline regressions for the RAG index consistency baseline."""

from __future__ import annotations

import pytest

from research_core.rag.database import (
    ChunkMetaRow,
    PaperRow,
    close_db,
    count_chunk_metadata,
    delete_paper,
    get_db,
    insert_chunks_meta,
    list_paper_keys,
    upsert_paper,
)
from research_core.rag.index_manifest import IndexManifest, IndexRuntime


@pytest.fixture
def database(tmp_path):
    close_db()
    conn = get_db(str(tmp_path))
    yield conn
    close_db()


def _runtime() -> IndexRuntime:
    return IndexRuntime(
        chunking_version="chunk-v1",
        cleaner_version="clean-v1",
        clean_enabled=True,
        embedding_backend="onnx_int8",
        embedding_model="BAAI/bge-m3",
        embedding_max_seq_len=1024,
        bilingual_enrichment=True,
    )


def test_delete_paper_cascades_all_structured_metadata(database):
    upsert_paper(database, PaperRow(item_key="PAPER-1", title="Test paper"))
    insert_chunks_meta(
        database,
        [ChunkMetaRow(id="PAPER-1:0", item_key="PAPER-1", chunk_idx=0)],
    )
    figure_id = database.execute(
        "INSERT INTO figures (item_key, ref) VALUES (?, ?)", ("PAPER-1", "fig-1")
    ).lastrowid
    table_id = database.execute(
        "INSERT INTO table_records (item_key, ref) VALUES (?, ?)", ("PAPER-1", "tbl-1")
    ).lastrowid
    database.execute(
        "INSERT INTO chunk_figure_refs (chunk_id, figure_id) VALUES (?, ?)",
        ("PAPER-1:0", figure_id),
    )
    database.execute(
        "INSERT INTO chunk_table_refs (chunk_id, table_id) VALUES (?, ?)",
        ("PAPER-1:0", table_id),
    )
    database.commit()

    delete_paper(database, "PAPER-1")
    database.commit()

    assert list_paper_keys(database) == set()
    assert count_chunk_metadata(database) == 0
    for table in ("figures", "table_records", "chunk_figure_refs", "chunk_table_refs"):
        assert database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_retriever_blocks_bm25_for_a_degraded_manifest(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from research_core.rag.retriever import Retriever

    manifest = IndexManifest.start(_runtime())
    manifest.finish(vector_chunks=2, metadata_chunks=1, bm25_chunks=2)
    manifest.save(str(tmp_path))

    from research_core.rag.bm25_index import BM25Index

    monkeypatch.setattr(
        BM25Index,
        "load",
        lambda _self: pytest.fail("A degraded build must not load BM25"),
    )
    retriever = Retriever.__new__(Retriever)
    retriever._persist_dir = str(tmp_path)
    retriever._bm25 = None

    assert retriever.bm25 is None


def test_retriever_allows_bm25_for_a_ready_manifest(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from research_core.rag.retriever import Retriever

    manifest = IndexManifest.start(_runtime())
    manifest.finish(vector_chunks=2, metadata_chunks=2, bm25_chunks=2)
    manifest.save(str(tmp_path))

    from research_core.rag.bm25_index import BM25Index

    monkeypatch.setattr(BM25Index, "load", lambda instance: setattr(instance, "_ready", True))
    retriever = Retriever.__new__(Retriever)
    retriever._persist_dir = str(tmp_path)
    retriever._bm25 = None

    assert retriever.bm25 is not None
