# CLAUDE.md

## Project Overview

This is **Zotero Research Assistant** — an MCP (Model Context Protocol) server that turns a Zotero reference library into an AI-searchable knowledge base. 40 MCP tools across 6 categories (36 always-on + 4 CNKI-conditional). **Core focus: production-grade RAG pipeline (chunking + retrieval quality) for academic papers.**

- **Author:** qiobn
- **Language:** Python 3.11+
- **Package:** `zra-mcp`
- **Entry:** `project_a_mcp/server.py` → `zra-mcp` CLI command
- **Key deps:** ChromaDB, onnxruntime (INT8 default), sentence-transformers (FP32 fallback), PyMuPDF, FastMCP, PyZotero

## Architecture

```
research_core/
  parsers/     — PDF extraction, text cleaner (52 rules), chunker, section detector
  rag/         — ChromaDB store, retriever, SQLite metadata DB, evaluation, logger, diagnostics
  tools/       — 40 MCP tool adapters (36 always-on + 4 CNKI-conditional)
  zotero/      — Zotero local + web API client
project_a_mcp/ — MCP server entry point (stdio)
scripts/       — CLI utils (index_library, audit_index, run_evaluation, benchmark_*, publish)
tests/         — pytest suite + 60 golden eval queries
docs/          — Setup guides (Cherry Studio CN/EN)
```

## Development Rules

### Commit & Log Protocol

**On every significant change (new feature, bug fix, non-trivial refactor), you MUST:**

1. **Update `docs/DEVELOPMENT_LOG.md` and `docs/DEVELOPMENT_LOG_EN.md`** — Record:
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
     feat: add client-guided bilingual search strategy

     Keep search_papers as a single-query retrieval engine. Expose
     user-defined synonyms and Zotero tags through expand_query so MCP
     clients can formulate multi-call CN/EN searches when needed.

     Package strategy skills with the wheel and expose them as MCP
     resources for skill-aware clients.
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

### Security: Credential Files

- **NEVER write API keys, tokens, or credentials to any file inside the project
  directory unless it is already in `.gitignore`.** Python build backends
  (hatchling, setuptools) may include non-gitignored files in sdists, leaking
  secrets to PyPI.
- When you need a temp credential file (e.g., for twine uploads), write it to
  `/tmp/` (outside the project), upload immediately, then delete it.
- After creating any new credential file pattern, verify `.gitignore` covers
  it — use a wildcard like `.pypirc*` rather than exact filenames.
- `.env` is gitignored; `.env.example` is committed (template only, no real secrets)
- All configurable values have env var overrides
- Defaults are zero-config: users should get basic functionality without any `.env` edits
- New env vars must be documented in: `.env.example`, both READMEs' config tables

### Documentation Sync

When adding/removing features or changing behavior, update:
1. `README.md` (English) + `README_zh.md` (Chinese) — keep in sync
2. `CHANGELOG.md` — keep-a-changelog format
3. `docs/DEVELOPMENT_LOG.md` and `docs/DEVELOPMENT_LOG_EN.md` — technical details and decisions
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

## Current State (v0.4.10.dev0)

- Development baseline: `feat/lightweight-graphrag`, 12 commits ahead of `main`
- 40 MCP tools (36 always-on + 4 CNKI-conditional)
- `search_papers` is intentionally single-query: the MCP client owns query translation,
  decomposition and multi-call merging; `expand_query` exposes user synonyms and Zotero tags
- Strategy skills are in `.claude/skills/` for source checkouts and packaged as
  `project_a_mcp/skills` in wheels; FastMCP exposes them as MCP resources
- Key features: BM25+Dense hybrid retrieval, ONNX INT8 embedding, MMR diversity,
  optional index-time bilingual metadata enrichment, contextual chunk enrichment, dual-format output,
  externalized multi-call search strategy (7-call RRF-weighted)
- Next priorities: wheel install/resource smoke test, no-answer judge calibration,
  contract tests for MCP responses, and chunk-size tuning
