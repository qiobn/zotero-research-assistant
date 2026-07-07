"""SQLite metadata database — structured storage for papers, sections,
chunks, figures, and tables. Separated from ChromaDB (which stores only
vector embeddings and search-critical filter fields).

Design:
- papers: one row per Zotero item (title, abstract, authors, etc.)
- sections: hierarchical chapter/section structure within a paper
- chunks_meta: per-chunk location + quality scores
- figures / table_records: caption-anchored media records
- chunk_figure_refs / chunk_table_refs: many-to-many cross-references

Zero user setup — the database file is auto-created inside .chroma_db/
on first sync_index run. SQLite is Python stdlib (no extra dependency).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

DB_FILENAME = "papers.db"

# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class PaperRow:
    item_key: str
    title: str = ""
    year: int = 0
    authors: str = "[]"  # JSON list of strings
    abstract: str = ""
    keywords: str = "[]"  # JSON list of strings
    journal: str = ""
    doi: str = ""
    pub_type: str = "journal_article"
    zotero_version: int = 0
    indexed_at: str = ""

    def author_list(self) -> list[str]:
        try:
            return json.loads(self.authors)
        except (json.JSONDecodeError, TypeError):
            return []

    def keyword_list(self) -> list[str]:
        try:
            return json.loads(self.keywords)
        except (json.JSONDecodeError, TypeError):
            return []


@dataclass
class SectionRow:
    id: int = 0
    item_key: str = ""
    parent_id: int | None = None
    heading: str = ""
    section_type: str = "unknown"
    level: int = 1
    page_start: int = 0
    page_end: int = 0
    chunk_start_idx: int = 0
    chunk_end_idx: int = 0


@dataclass
class ChunkMetaRow:
    id: str = ""                    # "{item_key}:{chunk_idx}"
    item_key: str = ""
    chunk_idx: int = 0
    section_id: int | None = None
    page_start: int = 0
    page_end: int = 0
    quality_flag: str = "good"
    sentence_count: int = 0
    language: str = ""
    is_table: bool = False
    is_figure: bool = False


@dataclass
class FigureRow:
    id: int = 0
    item_key: str = ""
    ref: str = ""
    label: str = ""
    caption: str = ""
    page: int = 0


@dataclass
class TableRow:
    id: int = 0
    item_key: str = ""
    ref: str = ""
    label: str = ""
    caption: str = ""
    page: int = 0


@dataclass
class EnrichedResult:
    """A retrieval result with full structural context attached."""
    text: str
    item_key: str
    chunk_id: str
    score: float
    page_start: int = 0
    page_end: int = 0
    quality_flag: str = "good"
    # Paper context (from papers table)
    paper_title: str = ""
    paper_year: int = 0
    paper_authors: str = ""
    paper_abstract: str = ""
    paper_keywords: str = ""
    paper_doi: str = ""
    # Section context (from sections table)
    section_heading: str = ""
    section_type: str = "unknown"
    section_level: int = 0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "item_key": self.item_key,
            "chunk_id": self.chunk_id,
            "score": self.score,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "quality_flag": self.quality_flag,
            "paper": {
                "title": self.paper_title,
                "year": self.paper_year,
                "authors": self.paper_authors,
                "abstract": self.paper_abstract,
                "keywords": self.paper_keywords,
                "doi": self.paper_doi,
            },
            "section": {
                "heading": self.section_heading,
                "type": self.section_type,
                "level": self.section_level,
            },
        }


# ── Database singleton ─────────────────────────────────────────────────

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path(persist_dir: str = ".chroma_db") -> str:
    return os.path.join(persist_dir, DB_FILENAME)


def get_db(persist_dir: str = ".chroma_db") -> sqlite3.Connection:
    """Return a shared SQLite connection (thread-safe singleton)."""
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        path = _db_path(persist_dir)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _create_tables(_conn)
    return _conn


def close_db() -> None:
    global _conn
    if _conn:
        _conn.close()
        _conn = None


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            item_key       TEXT PRIMARY KEY,
            title          TEXT NOT NULL DEFAULT '',
            year           INTEGER DEFAULT 0,
            authors        TEXT DEFAULT '[]',
            abstract       TEXT DEFAULT '',
            keywords       TEXT DEFAULT '[]',
            journal        TEXT DEFAULT '',
            doi            TEXT DEFAULT '',
            pub_type       TEXT DEFAULT 'journal_article',
            zotero_version INTEGER DEFAULT 0,
            indexed_at     TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key        TEXT NOT NULL REFERENCES papers(item_key) ON DELETE CASCADE,
            parent_id       INTEGER REFERENCES sections(id) ON DELETE SET NULL,
            heading         TEXT DEFAULT '',
            section_type    TEXT NOT NULL DEFAULT 'unknown',
            level           INTEGER DEFAULT 1,
            page_start      INTEGER DEFAULT 0,
            page_end        INTEGER DEFAULT 0,
            chunk_start_idx INTEGER DEFAULT 0,
            chunk_end_idx   INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sections_item ON sections(item_key);
        CREATE INDEX IF NOT EXISTS idx_sections_parent ON sections(parent_id);

        CREATE TABLE IF NOT EXISTS chunks_meta (
            id               TEXT PRIMARY KEY,
            item_key         TEXT NOT NULL REFERENCES papers(item_key) ON DELETE CASCADE,
            chunk_idx        INTEGER NOT NULL,
            section_id       INTEGER REFERENCES sections(id) ON DELETE SET NULL,
            page_start       INTEGER DEFAULT 0,
            page_end         INTEGER DEFAULT 0,
            quality_flag     TEXT DEFAULT 'good',
            sentence_count   INTEGER DEFAULT 0,
            language         TEXT DEFAULT '',
            is_table         INTEGER DEFAULT 0,
            is_figure        INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks_meta(item_key);
        CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks_meta(section_id);

        CREATE TABLE IF NOT EXISTS figures (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key TEXT NOT NULL REFERENCES papers(item_key) ON DELETE CASCADE,
            ref      TEXT NOT NULL DEFAULT '',
            label    TEXT DEFAULT '',
            caption  TEXT DEFAULT '',
            page     INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_figures_item ON figures(item_key);

        CREATE TABLE IF NOT EXISTS table_records (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key TEXT NOT NULL REFERENCES papers(item_key) ON DELETE CASCADE,
            ref      TEXT NOT NULL DEFAULT '',
            label    TEXT DEFAULT '',
            caption  TEXT DEFAULT '',
            page     INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tables_item ON table_records(item_key);

        CREATE TABLE IF NOT EXISTS chunk_figure_refs (
            chunk_id  TEXT NOT NULL REFERENCES chunks_meta(id) ON DELETE CASCADE,
            figure_id INTEGER NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, figure_id)
        );

        CREATE TABLE IF NOT EXISTS chunk_table_refs (
            chunk_id TEXT NOT NULL REFERENCES chunks_meta(id) ON DELETE CASCADE,
            table_id INTEGER NOT NULL REFERENCES table_records(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, table_id)
        );
    """)


# ── CRUD: Papers ───────────────────────────────────────────────────────


def upsert_paper(conn: sqlite3.Connection, paper: PaperRow) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO papers
            (item_key, title, year, authors, abstract, keywords,
             journal, doi, pub_type, zotero_version, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        paper.item_key, paper.title, paper.year,
        paper.authors, paper.abstract, paper.keywords,
        paper.journal, paper.doi, paper.pub_type, paper.zotero_version,
    ))


def get_paper(conn: sqlite3.Connection, item_key: str) -> PaperRow | None:
    row = conn.execute(
        "SELECT * FROM papers WHERE item_key = ?", (item_key,)
    ).fetchone()
    if row is None:
        return None
    return PaperRow(**dict(row))


def delete_paper(conn: sqlite3.Connection, item_key: str) -> None:
    conn.execute("DELETE FROM papers WHERE item_key = ?", (item_key,))
    # CASCADE handles sections, chunks_meta, figures, table_records


# ── CRUD: Sections ─────────────────────────────────────────────────────


def insert_sections(
    conn: sqlite3.Connection,
    sections: list[SectionRow],
) -> list[int]:
    """Insert sections and return their assigned IDs."""
    ids: list[int] = []
    for sec in sections:
        cur = conn.execute("""
            INSERT INTO sections
                (item_key, parent_id, heading, section_type, level,
                 page_start, page_end, chunk_start_idx, chunk_end_idx)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sec.item_key, sec.parent_id, sec.heading, sec.section_type,
            sec.level, sec.page_start, sec.page_end,
            sec.chunk_start_idx, sec.chunk_end_idx,
        ))
        ids.append(cur.lastrowid or 0)
    return ids


def get_sections(conn: sqlite3.Connection, item_key: str) -> list[SectionRow]:
    rows = conn.execute(
        "SELECT * FROM sections WHERE item_key = ? ORDER BY id", (item_key,)
    ).fetchall()
    return [SectionRow(**dict(r)) for r in rows]


def delete_sections(conn: sqlite3.Connection, item_key: str) -> None:
    conn.execute("DELETE FROM sections WHERE item_key = ?", (item_key,))


# ── CRUD: Chunks Meta ──────────────────────────────────────────────────


def insert_chunks_meta(
    conn: sqlite3.Connection,
    chunks: list[ChunkMetaRow],
) -> None:
    conn.executemany("""
        INSERT OR REPLACE INTO chunks_meta
            (id, item_key, chunk_idx, section_id, page_start, page_end,
             quality_flag, sentence_count, language, is_table, is_figure)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            c.id, c.item_key, c.chunk_idx, c.section_id,
            c.page_start, c.page_end, c.quality_flag,
            c.sentence_count, c.language,
            int(c.is_table), int(c.is_figure),
        )
        for c in chunks
    ])


def get_chunks_meta(
    conn: sqlite3.Connection,
    chunk_ids: list[str],
) -> dict[str, ChunkMetaRow]:
    """Batch-fetch chunk metadata by ID."""
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT * FROM chunks_meta WHERE id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    result: dict[str, ChunkMetaRow] = {}
    for r in rows:
        d = dict(r)
        d["is_table"] = bool(d.get("is_table", 0))
        d["is_figure"] = bool(d.get("is_figure", 0))
        result[d["id"]] = ChunkMetaRow(**d)
    return result


def delete_chunks(conn: sqlite3.Connection, item_key: str) -> None:
    conn.execute("DELETE FROM chunks_meta WHERE item_key = ?", (item_key,))


# ── CRUD: Figures & Tables ─────────────────────────────────────────────


def insert_figures(
    conn: sqlite3.Connection,
    figures: list[FigureRow],
) -> list[int]:
    ids: list[int] = []
    for fig in figures:
        cur = conn.execute("""
            INSERT INTO figures (item_key, ref, label, caption, page)
            VALUES (?, ?, ?, ?, ?)
        """, (fig.item_key, fig.ref, fig.label, fig.caption, fig.page))
        ids.append(cur.lastrowid or 0)
    return ids


def insert_tables(
    conn: sqlite3.Connection,
    tables: list[TableRow],
) -> list[int]:
    ids: list[int] = []
    for tbl in tables:
        cur = conn.execute("""
            INSERT INTO table_records (item_key, ref, label, caption, page)
            VALUES (?, ?, ?, ?, ?)
        """, (tbl.item_key, tbl.ref, tbl.label, tbl.caption, tbl.page))
        ids.append(cur.lastrowid or 0)
    return ids


def insert_chunk_figure_refs(
    conn: sqlite3.Connection,
    refs: list[tuple[str, int]],  # [(chunk_id, figure_id), ...]
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO chunk_figure_refs (chunk_id, figure_id) VALUES (?, ?)",
        refs,
    )


def insert_chunk_table_refs(
    conn: sqlite3.Connection,
    refs: list[tuple[str, int]],  # [(chunk_id, table_id), ...]
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO chunk_table_refs (chunk_id, table_id) VALUES (?, ?)",
        refs,
    )


# ── Enriched queries ───────────────────────────────────────────────────


def enrich_results(
    conn: sqlite3.Connection,
    chunk_ids: list[str],
) -> list[EnrichedResult]:
    """Given ChromaDB chunk IDs, return fully enriched results with
    paper and section context from SQLite."""
    if not chunk_ids:
        return []

    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(f"""
        SELECT
            c.id AS chunk_id, c.item_key, c.page_start, c.page_end,
            c.quality_flag,
            p.title AS paper_title, p.year AS paper_year,
            p.authors AS paper_authors, p.abstract AS paper_abstract,
            p.keywords AS paper_keywords, p.doi AS paper_doi,
            s.heading AS section_heading, s.section_type,
            s.level AS section_level
        FROM chunks_meta c
        JOIN papers p ON c.item_key = p.item_key
        LEFT JOIN sections s ON c.section_id = s.id
        WHERE c.id IN ({placeholders})
    """, chunk_ids).fetchall()

    # Preserve input order
    row_by_id = {r["chunk_id"]: r for r in rows}

    results: list[EnrichedResult] = []
    for cid in chunk_ids:
        r = row_by_id.get(cid)
        if r is None:
            results.append(EnrichedResult(
                text="", item_key="", chunk_id=cid, score=0.0,
            ))
            continue
        results.append(EnrichedResult(
            text="",  # text comes from ChromaDB, filled in by caller
            item_key=r["item_key"],
            chunk_id=r["chunk_id"],
            score=0.0,
            page_start=r["page_start"],
            page_end=r["page_end"],
            quality_flag=r["quality_flag"],
            paper_title=r["paper_title"],
            paper_year=r["paper_year"],
            paper_authors=r["paper_authors"],
            paper_abstract=(r["paper_abstract"] or "")[:500],
            paper_keywords=r["paper_keywords"],
            paper_doi=r["paper_doi"],
            section_heading=r["section_heading"] or "",
            section_type=r["section_type"] or "unknown",
            section_level=r["section_level"] or 0,
        ))
    return results
