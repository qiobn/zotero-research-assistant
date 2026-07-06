# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-06

A major RAG quality release — production-grade retrieval pipeline with text cleaning,
chunk quality scoring, SQLite metadata layer, section-parent context expansion,
embedding diagnostics, retrieval observability, and systematic evaluation.

### Added

**RAG Pipeline — Data Quality**
- **Text cleaning engine** (`research_core/parsers/text_cleaner.py`): 52 blacklist
  regex rules removing journal boilerplate — EN article-info blocks, CN volume/issue
  lines, CLC numbers, funding footers, page numbers, DOI lines. Controlled by
  `ZRA_CLEAN_ENABLED=true` (default on). Cleaning stats reported in `sync_index`.

**RAG Pipeline — Chunk Quality**
- **Chunk quality scoring** (`research_core/parsers/chunker.py`, v2.9.0): 7 quality
  fields per chunk — `coherence_score`, `information_density`, `boilerplate_ratio`,
  `sentence_count`, `starts_with_conjunction`, `language` (zh/en/mixed), `quality_flag`
  (good/noisy/incomplete/boilerplate). Lightweight heuristic scoring for quality-aware
  retrieval filtering.

**RAG Pipeline — Metadata & Context**
- **SQLite metadata database** (`research_core/rag/database.py`): 7 relational tables
  (`papers`, `sections`, `chunks_meta`, `figures`, `table_records`, and cross-reference
  tables) inside `.chroma_db/papers.db`. Zero user setup — auto-created on first sync.
- **Section-parent context expansion** (`Retriever.expand_to_section()`): hit a chunk
  → fetch its entire enclosing section from SQLite + ChromaDB, providing LLM with
  complete paragraph context instead of isolated fragments. Cache-batched.
- **Result enrichment** (`Retriever.enrich()`): batch-fetches paper + section metadata
  (abstract, authors, year, DOI, keywords, section heading/type) via SQLite JOIN.

**RAG Pipeline — Diagnostics & Evaluation**
- **Embedding quality diagnostics** (`research_core/rag/embedding_diagnostics.py`):
  6-phase analysis — per-paper intra-similarity, cross-paper separation ratio, outlier
  chunk detection (centroid coherence < 0.3), chunk length-similarity Pearson
  correlation, section-type embedding separation, automated issues + fix suggestions.
- **Evaluation framework** (`research_core/rag/evaluation.py`): Recall@5/10/20, MRR,
  NDCG@10 metrics. 60 golden queries (`tests/eval_queries.json`) across direct hit,
  cross-document, and no-answer categories. `scripts/run_evaluation.py` with
  `--save-baseline` / `--compare` for A/B testing.
- **Index audit script** (`scripts/audit_index.py`): 7-phase full-library quality
  audit — paginated scan, per-paper scoring, library coverage, noise detection,
  embedding separation, health scoring, and actionable recommendations.

**RAG Pipeline — Observability**
- **Retrieval logging** (`research_core/rag/logger.py`): JSONL append-only trace
  logging with byte-offset index. Captures: query, strategy, candidate counts,
  reranker details, top-20 results with scores/sources, latency breakdown
  (keyword/semantic/rerank/total). 3 new MCP tools: `recent_retrievals`,
  `retrieval_trace` (replay by trace ID), `retrieval_stats` (aggregate analytics).

**MCP Tools**
- Total tool count: 32 → 35 (3 retrieval log tools: `recent_retrievals`,
  `retrieval_trace`, `retrieval_stats`).

**Documentation**
- READMEs (EN/CN) updated with all new RAG features, enhanced client setup sections
  for Claude Desktop, Cherry Studio, and Codex CLI.
- `DEVELOPMENT_PLAN.md` updated: Phase 0/1/2 marked complete, decision log synced.
- `DEVELOPMENT_LOG.md` — developer-facing detailed change log from project inception
  to v0.3.0.

### Changed

- **Chunking overlap rewritten** (`chunker.py`, v2.8.0): sentence-based (1 sentence)
  → character-based (100 chars) with sentence-boundary completion. Forward-then-backward
  search algorithm ensures overlap always captures meaningful context.
- **Section detection** (`section_detector.py`): IMRaD classification via regex heading
  patterns (EN numbered "1. Introduction", CN "一、引言"). Quality-aware — skips
  boilerplate/incomplete chunks for heading detection.
- **Retriever search** now auto-enriches results with paper + section metadata from
  SQLite (zero latency cost — single JOIN). `expand_context` parameter enables
  section-parent context expansion.
- **`search_papers`** emits JSONL retrieval trace on every call with full latency
  breakdown.
- **Architecture**: `research_core/rag/` now includes `database.py`, `evaluation.py`,
  `logger.py`, `embedding_diagnostics.py`.
- Removed emoji from all terminal output for Windows GBK compatibility.

### Fixed

- Sentence-boundary overlap algorithm: forward-then-backward search now correctly
  finds sentence boundaries within the overlap region. Previous backward-only search
  missed boundaries, producing only 1% overlap for English text.
- Syntax errors in `search.py` from duplicate `matched_passage`/`matched_page` lines
  after edit.
- `.claude/` directory added to `.gitignore`.

## [0.2.0] - 2026-06-11

A large reliability- and productization-focused release. The project is now a
standalone MCP server (no agent scaffold) with 32 single-intent tools.

### Added
- **Standalone MCP server** exposing 32 tools across Discover / Read / Write /
  Cite / Insight / Admin, each mapping to one user intent and composing via `item_key`.
- **`expand_citation_network`** — forward/backward citation-graph expansion via
  OpenAlex, with multi-seed DOI support.
- **`find_related_literature`** — one call runs 5 parallel strategies
  (Corpus-First, keyword, citation network, S2 recommendations, OpenAlex related
  works) with three-index verification.
- **Table & figure cross-referencing** — prose that cites "Table 3 / Figure 2"
  is linked to caption-anchored table/figure records, resolved together in
  `get_paper_content` (`referenced_tables` / `referenced_figures`).
- **CJK-aware chunking** — sentence splitting and soft-wrap repair so Chinese
  text is no longer cut mid-word.
- Shared HTTP client with global concurrency cap, per-host rate limiting,
  retry/backoff, and a short-TTL response cache.
- Configurable `EMBEDDING_MAX_SEQ_LEN` (bounds GPU/MPS memory) and `HF_ENDPOINT`
  (HuggingFace mirror, e.g. for users in China).
- Bilingual README and a Cherry Studio setup guide (English + Chinese) aimed at
  non-developers.

### Changed
- **Tables and figures are now caption-anchored records** instead of being parsed
  into structured cells. Reliable table structuring is a vision problem;
  geometric/line-based detection produced garbage on borderless academic tables
  and mis-segmented multi-column prose. Table *values* stay searchable; users who
  need true structure are pointed to docling / open-parse / unstructured.
- Hardened MCP tools: write operations are dry-run by default and require explicit
  confirmation; responses are size-capped to protect the LLM context window;
  errors return structured bilingual diagnostics.
- Improved RAG recall and speed; thread-safe lazy initialization of Zotero client,
  retriever, and indexer.
- Incremental index sync by default (Zotero version tracking); auto full rebuild
  when the embedding model or chunking version changes.

### Removed
- Built-in structured table extraction (PyMuPDF "lite" mode + Table Transformer
  "ml" mode), the `[tables]` optional extra, and `table_ml.py`.
- Legacy agent scaffold (the project is MCP-server only).

### Fixed
- Correct Zotero item filtering and ChromaDB `search_ef` modification.
- Hard chunk-size cap to prevent embedding out-of-memory on pathological inputs.
- Unit tests updated for the shared HTTP client refactor.

## [0.1.1] - earlier

- Initial public iteration: literature search, RAG pipeline, reading analysis,
  citation management, review/reading-note generation, tag suggestions, and the
  first Cherry Studio setup guide.

[0.3.0]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.3.0
[0.2.0]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.2.0
[0.1.1]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.1.1
