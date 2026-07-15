# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-07-14

### Added
- **NMT query translation** — OPUS-MT CN→EN (Layer 4) for bilingual query expansion.
  Lazy-loaded, ~400ms/query, weight 0.8 in RRF fusion. Only activates for Chinese
  queries. Model cached at `.chroma_db/hf_cache/`.
- **Index-time bilingual enrichment** — Chinese papers' title and keywords are
  automatically translated to English during indexing and appended as
  `[Title_EN: ...] [Keywords_EN: ...]` to BM25/Dense text. Enables BM25
  cross-lingual matching. Index rebuild triggered by CHUNKING_VERSION bump.
- **Dictionary management tools** — `remove_query_synonym()`, `list_query_synonyms()`,
  `import_query_dict()` for managing user-defined CN→EN synonym pairs.
- **Configurable NMT cache dir** — `ZRA_NMT_CACHE_DIR` env var (defaults to
  `.chroma_db/hf_cache/`).

## [0.3.1] - 2026-07-14

### Added
- **BM25 within-paper search** — `search_within_item()` (used by `get_paper_content`
  with query) now uses two-way BM25+Dense RRF fusion, matching the full-library
  search pipeline. Previously only used Dense search, missing rare terms in PDF
  body that embeddings struggle with.

## [0.3.0] - 2026-07-13

### Added
- **BM25 sparse keyword index on chunk texts** — `rank_bm25` with CJK-aware
  tokenizer (character unigrams+bigrams for CN, alpha words for EN). Two-way
  RRF fusion: BM25 (lexical) + ChromaDB (semantic). Persists to
  `.chroma_db/_bm25_index.pkl`. Auto-rebuilt on every sync.
- **Contextual chunk enrichment** — each chunk text is prepended with
  `[Keywords: ...] [Title: ...] [Section: ...]` before embedding and BM25
  indexing. Implements Anthropic "Contextual Retrieval" 2024 technique
  using existing metadata — zero additional cost. Keywords are filtered
  from Zotero tags (excludes organizational labels).
- **Dual-format output (JSON + Markdown context_block)** — key retrieval tools now
  return a `context_block` field containing pre-rendered LLM-optimized Markdown
  alongside the existing JSON items. Blockquote (>) for evidence, star ratings
  (★★★) for relevance tiers, sentence-boundary truncation. Covers all 8
  retrieval/insight tools: `search_papers`, `get_paper_content`,
  `generate_review_note`, `suggest_citations`, `find_similar_papers`,
  `find_arguments`, `generate_reading_note`, `suggest_tags`.
- **Relevance tiers** — each `search_papers` result now carries a `relevance_tier`
  field ("high"/"medium"/"low") computed from Cross-Encoder score percentiles.
- **Full-pipeline evaluation mode** — `run_evaluation.py --full-pipeline` tests
  the complete `search_papers()` pipeline (BM25 + CE rerank + MMR + RRF fusion),
  not just raw semantic search. `evaluate_full_pipeline()` in evaluation.py.
- **Retrieval log rotation** — auto-cleans log entries older than 90 days.
  Triggers on server startup and after each `sync_index`. Uses `f.tell()` for
  cross-platform byte offset accuracy.
- **Expanded section detection** — Roman numerals (I., II.), Chapter prefix
  (CHAPTER 1:), section symbol (§1.), letter subsections (A., B.). ~50 new
  type classification keywords covering non-standard academic headings.
  Number-only fallback for papers with bare numbered markers.
- **ONNX INT8 embedding backend** (`EMBEDDING_BACKEND=auto` / `onnx_int8`) —
  2-3x faster, 4x smaller (347MB vs 2.3GB) embedding on CPU. Zero-config.
- **MMR (Maximal Marginal Relevance) diversity reranking** — chunk-level MMR
  with per-document cap (max 3 chunks per paper) and per-document penalty.
  Default λ=0.4 (grid-search tuned from 0.6).
- **Multi-layer bilingual query expansion** — 3-layer system with ~300 built-in
  methodology term pairs, auto-extracted Zotero tags, and user-defined synonyms.
- **Neighbor chunk expansion** — `expand_neighbors=True` returns hit chunk ±1
  adjacent chunk within the same section.
- **Min chunk size floor (200 chars)** — post-chunking merge pass eliminating
  fragments below the FloTorch 2026 threshold for e2e accuracy.

### Changed
- **Two-way RRF fusion** — simplified from three-way (Zotero API + BM25 + Dense)
  to two-way (BM25 + Dense). Zotero API remains for paper metadata, filters,
  and empty query mode.
- **MMR diversity_weight tuned: 0.6 → 0.4** — grid search on 10 queries.
- **Chunk quality scoring simplified** — dropped unused heuristic fields
  (coherence_score, information_density, boilerplate_ratio).
- **MCP tools: 32 → 36** with add_query_synonym, recent_retrievals,
  retrieval_trace, and retrieval_stats.
- **CHUNKING_VERSION: v2.9.0 → v3.1.0-contextual-chunks**.

### Fixed
- **SQLite authors field** — was empty string, now populated from Zotero item metadata.
- **sync_index dead parameters** — removed unused `chunk_size` and `chunk_overlap`.
- **Section detection `\s*` newline bug** — `\s*` in heading patterns now replaced
  with `[ \\t]*` to prevent greedy newline consumption causing false headings.
- **inspect_index pagination bug** — `len(docs)` was only the last page's count,
  now uses `total_count` for accurate stats.
- **diversity_weight docstring** — corrected from 0.6 to 0.4 in both search.py
  and server.py.

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

[Unreleased]: https://github.com/qiobn/zotero-research-assistant/compare/v0.3.1...main
[0.3.1]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.3.1
[0.3.0]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.3.0
[0.2.0]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.2.0
[0.1.1]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.1.1
