# CLAUDE.md

## Project Overview

This is **Zotero Research Assistant** — an MCP (Model Context Protocol) server that turns a Zotero reference library into an AI-searchable knowledge base. 36 MCP tools across 6 categories. **Core focus: production-grade RAG pipeline (chunking + retrieval quality) for academic papers.**

- **Author:** qiobn
- **Language:** Python 3.11+
- **Package:** `zotero-research-assistant`
- **Entry:** `project_a_mcp/server.py` → `zra-mcp` CLI command
- **Key deps:** ChromaDB, onnxruntime (INT8 default), sentence-transformers (FP32 fallback), PyMuPDF, FastMCP, PyZotero

## Architecture

```
research_core/
  parsers/     — PDF extraction, text cleaner (52 rules), chunker, section detector
  rag/         — ChromaDB store, retriever, SQLite metadata DB, evaluation, logger, diagnostics
  tools/       — 36 MCP tool implementations
  zotero/      — Zotero local + web API client
project_a_mcp/ — MCP server entry point (stdio)
scripts/       — CLI utils (index_library, audit_index, run_evaluation, benchmark_*, publish)
tests/         — pytest suite + 60 golden eval queries
docs/          — Setup guides (Cherry Studio CN/EN)
```

## Development Rules

### Commit & Log Protocol

**On every significant change (new feature, bug fix, non-trivial refactor), you MUST:**

1. **Update `DEVELOPMENT_LOG.md`** — Record:
   - What was changed (with commit hash)
   - What problem it solved
   - Technical decisions made and their rationale
   - Future optimization directions
   - Known issues introduced

2. **Update `CHANGELOG.md`** — Keep a Changelog format (`Added`/`Changed`/`Fixed`/`Removed` sections under current version). User-facing, less technical than DEVELOPMENT_LOG.

3. **Commit with a conventional commit message — ALWAYS include a body, never an empty commit:**
   - First line: `<type>: <summary>` (50-80 chars, imperative mood)
   - Then a BLANK LINE
   - Then body paragraphs explaining **what** changed, **why**, and any design notes
   - Types: `feat:` / `fix:` / `docs:` / `refactor:` / `chore:`
   - Example:
     ```
     feat: add query rewrite for bilingual academic search

     Dictionary-based CN<->EN term expansion with three layers:
     Layer 1: ~300 built-in methodology pairs from query_dict.json
     Layer 2: auto-extracted from user's Zotero tags during sync
     Layer 3: user-defined via add_query_synonym MCP tool

     Expansion runs in search_papers() — zero added latency, no LLM
     dependency. Each expanded term runs independent semantic search
     with RRF merging and expansion weight scoring.
     ```

4. **Push** after each logical unit of work (not after every micro-edit).

### Code Style

- Follow surrounding code patterns: comment density, naming, idiom
- Use `from __future__ import annotations` in all new files
- Use dataclasses for data containers
- Return typed dataclass instances, not raw dicts
- All write operations default to dry-run preview; require explicit confirmation
- MCP tools: one tool per user intent, compose via `item_key`
- Windows compatibility: no emoji in terminal output (use ASCII alternatives), use `os.path` or `pathlib`

### Testing

- Unit tests in `tests/` with pytest
- Run: `pytest tests/ -v`
- Lint: `ruff check .`
- Format: `ruff format .`
- Evaluation: `python scripts/run_evaluation.py`

### Environment

- `.env` is gitignored; `.env.example` is committed (template only, no real secrets)
- All configurable values have env var overrides
- Defaults are zero-config: users should get basic functionality without any `.env` edits
- New env vars must be documented in: `.env.example`, both READMEs' config tables

### Documentation Sync

When adding/removing features or changing behavior, update:
1. `README.md` (English) + `README_zh.md` (Chinese) — keep in sync
2. `CHANGELOG.md` — keep-a-changelog format
3. `DEVELOPMENT_LOG.md` — technical details and decisions
4. `DEVELOPMENT_PLAN.md` — progress bars and task checkboxes
5. `.env.example` — new env vars with comments

## Key Files Reference

| File | Purpose |
|------|---------|
| `DEVELOPMENT_LOG.md` | Detailed technical changelog (dev-facing) |
| `CHANGELOG.md` | Release changelog (user-facing) |
| `DEVELOPMENT_PLAN.md` | Task tracking + progress bars |
| `README.md` / `README_zh.md` | Bilingual project READMEs |
| `research_core/parsers/chunker.py` | Chunking with CHUNKING_VERSION for auto-rebuild |
| `research_core/parsers/text_cleaner.py` | 52 blacklist rules, CLEANER_VERSION |
| `research_core/rag/database.py` | SQLite metadata DB (auto-created on first sync) |
| `research_core/rag/logger.py` | JSONL retrieval trace logging |
| `research_core/rag/evaluation.py` | Recall@K, MRR, NDCG |
| `research_core/rag/retriever.py` | ChromaDB retriever with section expansion + enrichment |

## Current State (v0.4.0-dev)

- Phase 0/1/2/3 (partial) complete; Phase 4 (v0.4.0) in progress
- 36 MCP tools (32 always-on + 4 CNKI-conditional), all operational
- Key features: BM25+Dense hybrid retrieval, ONNX INT8 embedding, MMR diversity,
  bilingual query expansion, contextual chunk enrichment, dual-format output
- Known issues: evaluation only run on small test set (8 papers)
- Next priorities: run full-pipeline evaluation on real library, chunk size tuning
