"""Regression tests for persisted section hierarchy."""

from __future__ import annotations

import pytest
from research_core.parsers.chunker import Chunk
from research_core.rag.database import close_db, get_db, get_sections
from research_core.tools.admin import _index_metadata


@pytest.fixture
def persist_dir(tmp_path):
    close_db()
    yield str(tmp_path)
    close_db()


def _chunk(chunk_idx: int, heading: str) -> Chunk:
    return Chunk(
        text=f"{heading}\nBody text for this section.",
        page_start=chunk_idx + 1,
        page_end=chunk_idx + 1,
        chunk_idx=chunk_idx,
        language="en",
        sentence_count=1,
    )


def _index(persist_dir: str, chunks: list[Chunk]) -> None:
    _index_metadata(
        item_key="PARENT-TEST",
        title="Section hierarchy test",
        year=2026,
        authors="[]",
        abstract="",
        keywords="[]",
        journal="",
        doi="",
        pub_type="journalArticle",
        zotero_version=1,
        chunks=chunks,
        persist_dir=persist_dir,
    )


def test_index_metadata_persists_nested_section_parents(persist_dir):
    _index(
        persist_dir,
        [
            _chunk(0, "1. Introduction"),
            _chunk(1, "2. Methods"),
            _chunk(2, "2.1 Data Collection"),
            _chunk(3, "2.1.1 Sampling"),
            _chunk(4, "3. Results"),
        ],
    )

    sections = get_sections(get_db(persist_dir), "PARENT-TEST")
    assert [section.heading for section in sections] == [
        "Introduction",
        "Methods",
        "Data Collection",
        "Sampling",
        "Results",
    ]
    assert [section.parent_id for section in sections] == [
        None,
        None,
        sections[1].id,
        sections[2].id,
        None,
    ]


def test_reindex_replaces_section_hierarchy_without_stale_parents(persist_dir):
    _index(
        persist_dir,
        [
            _chunk(0, "1. Methods"),
            _chunk(1, "1.1 Data Collection"),
        ],
    )
    _index(
        persist_dir,
        [
            _chunk(0, "1. Results"),
            _chunk(1, "1.1 Findings"),
        ],
    )

    conn = get_db(persist_dir)
    sections = get_sections(conn, "PARENT-TEST")
    assert len(sections) == 2
    assert [section.heading for section in sections] == ["Results", "Findings"]
    assert sections[0].parent_id is None
    assert sections[1].parent_id == sections[0].id
    assert conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0] == 2
