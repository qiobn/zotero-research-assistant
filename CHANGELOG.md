# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `LICENSE` file (MIT) — previously only declared in `pyproject.toml` and the README badge.

### Changed
- Cleaned `.env.example`: removed unused `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
  `OLLAMA_API_BASE` and the leftover "Project B" `TAVILY_API_KEY` (the LLM is supplied
  by the MCP client, not this server).

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

[0.2.0]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.2.0
[0.1.1]: https://github.com/qiobn/zotero-research-assistant/releases/tag/v0.1.1
