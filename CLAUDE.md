# CLAUDE.md

## Project Overview

This is **Zotero Research Assistant** — an MCP (Model Context Protocol) server that turns a Zotero reference library into an AI-searchable knowledge base. 35 MCP tools across 6 categories (Discover, Read, Write, Manage, Insight, Admin).

- **Author:** qiobn
- **Language:** Python 3.11+
- **Package:** `zotero-research-assistant`
- **Entry:** `project_a_mcp/server.py` → `zra-mcp` CLI command
- **Key deps:** ChromaDB, sentence-transformers (bge-m3), PyMuPDF, FastMCP, PyZotero

## Architecture

```
research_core/
  parsers/     — PDF extraction, text cleaner (52 rules), chunker, section detector
  rag/         — ChromaDB store, retriever, SQLite metadata DB, evaluation, logger, diagnostics
  tools/       — 35 MCP tool implementations
  zotero/      — Zotero local + web API client
project_a_mcp/ — MCP server entry point (stdio)
scripts/       — CLI utils (index_library, audit_index, run_evaluation, publish)
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

3. **Commit with a conventional commit message:**
   - `feat:` — new feature
   - `fix:` — bug fix
   - `docs:` — documentation only
   - `refactor:` — code restructuring without behavior change
   - `chore:` — tooling, build, dependencies
   - Example: `feat: add query rewrite for Chinese-English bilingual expansion`

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

## Current State (v0.3.0)

- Phase 0/1/2 complete; Phase 3 (P2 refinements) pending
- 35 MCP tools, all operational
- Known issues: weak embedding separation (0.95x), CNKI module unstable
- Next priorities: Query Rewrite (Phase 3.1), Adaptive Chunk Granularity (3.2), MMR Diversity Reranking (3.3)
