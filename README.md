# Zotero Research Assistant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io/)

**[English](./README.md)** | **[中文](./README_zh.md)**

---

> **Turn your Zotero library into an AI-searchable knowledge base.**
>
> A production-grade RAG pipeline — from PDF chunking to bilingual semantic retrieval — that runs entirely on your machine. Find papers by meaning, not just keywords. Works with any MCP-compatible AI client.

---

## Table of Contents

- [RAG Pipeline](#rag-pipeline) — the core
- [Quick Start](#quick-start)
- [Client Setup](#client-setup)
- [MCP Tools (36)](#mcp-tools-36)
- [Configuration](#configuration)
- [Tables & Figures](#tables--figures)
- [Other Features](#other-features)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)
- [Acknowledgments](#acknowledgments)
- [License](#license)

---

## RAG Pipeline

The RAG pipeline is the heart of this project. Every design decision — from chunking strategy to embedding backend to diversity reranking — is optimized for one goal: **maximize retrieval precision for academic papers on consumer hardware.**

### Pipeline Overview

```
Your Zotero Library
      │
      ▼
┌──────────────────────────────────────────────────────┐
│ 1. PDF EXTRACTION (PyMuPDF)                          │
│    Page-by-page text extraction, parallel processing │
├──────────────────────────────────────────────────────┤
│ 2. TEXT CLEANING (52 regex rules)                    │
│    Strips journal boilerplate: article-info blocks,  │
│    CLC numbers, funding footers, page numbers, DOIs  │
│    EN journals (9 rules) · CN journals (24) · Univ. (19) │
│    Avg 10.6% line removal (CN 19.3%, EN 7.2%)       │
├──────────────────────────────────────────────────────┤
│ 3. SEMANTIC CHUNKING (v3.0.0)                        │
│    Paragraph-aware splitting, CJK sentence detection │
│    Soft-wrap repair (满\n意度→满意度)                │
│    IMRaD section classification (11 types)           │
│    200-char min floor (FloTorch 2026 benchmark)      │
│    Per-chunk: language tag (zh/en/mixed), completeness│
├──────────────────────────────────────────────────────┤
│ 4. EMBEDDING (bge-m3, ONNX INT8)                     │
│    1024-dim dense vectors, 100+ languages            │
│    ONNX Runtime INT8: ~347MB (vs 2.3GB FP32)         │
│    2-3x faster on CPU, <1% R@5 loss vs FP32           │
│    Auto-fallback to FP32 if ONNX unavailable         │
├──────────────────────────────────────────────────────┤
│ 5. CHROMADB INDEXING                                 │
│    HNSW cosine index, 64-dim batch upsert            │
│    Quality flags + language tags stored in metadata  │
│    Incremental sync by Zotero version tracking       │
│    Auto-rebuild on chunking strategy change          │
├──────────────────────────────────────────────────────┤
│ 6. SQLite METADATA LAYER                             │
│    7 relational tables (papers, sections,            │
│    chunks_meta, figures, tables + cross-refs)        │
│    Abstracts stored but NOT embedded in ChromaDB     │
│    (prevents abstract-dominating-search problem)     │
└──────────────────────────────────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────────────────┐
│ 7. HYBRID SEARCH + RERANKING                         │
│    BM25 (sparse) + ChromaDB (dense)                  │
│    Merged via two-way Reciprocal Rank Fusion (k=60)  │
│    ↓                                                  │
│    Cross-Encoder reranking (ms-marco-MiniLM-L-6-v2)  │
│    ↓                                                  │
│    MMR diversity (λ=0.4, max 3 chunks/paper)         │
│    ↓                                                  │
│    Bilingual query expansion (CN↔EN dictionary)      │
│    ↓                                                  │
│    Dual-format output: JSON items + Markdown          │
│    context_block (blockquote evidence, ★★★ tiers)    │
└──────────────────────────────────────────────────────┘
```

### Key Pipeline Features

| Feature | Description |
|---------|-------------|
| **Text Cleaning** | 52 blacklist regex rules remove journal boilerplate before chunking. Covers EN (article-info, running headers), CN (volume/issue, CLC, funding), and universal patterns (page numbers, DOIs). Zero false-positive risk — no paper body contains "〔中图分类号〕" |
| **CJK-Aware Chunking** | Sentence splitting at `。！？` without requiring trailing whitespace. PDF soft-wrap repair. Paragraph-aware merge/split with ~100-char overlap between consecutive chunks ensures context continuity across chunk boundaries. IMRaD section detection via regex heading patterns (EN "1. Introduction", CN "一、引言"). References sections auto-excluded from search. |
| **Chunk Tagging** | Each chunk tagged with language (zh/en/mixed), sentence count, and a completeness flag (good/incomplete). 200-char minimum floor prevents sub-43-token fragments that tank end-to-end accuracy. |
| **ONNX INT8 Embedding** | Default backend uses ONNX Runtime with a pre-quantized bge-m3 model (~347MB vs 2.3GB FP32). 2-3x faster on CPU, 4x less disk, <1% retrieval precision impact. Auto-fallback to sentence-transformers FP32 if onnxruntime is unavailable. |
| **SQLite Metadata DB** | 7 relational tables (papers, sections, chunks_meta, figures, tables, cross-refs) separate from ChromaDB. Abstracts stored but NOT embedded — prevents the "abstract matches everything" problem. Zero user setup. |
| **Section-Parent Expansion** | `expand_context=True` fetches the full enclosing section for each hit chunk (~2000 chars vs 300), giving the LLM complete paragraph context. Neighbor expansion (±1 chunk) as lighter alternative. |
| **Hybrid Search + RRF** | BM25 sparse retrieval + ChromaDB dense semantic search merged via two-way Reciprocal Rank Fusion. BM25 protects exact matches (rare terminology, method names, variable names); semantic provides concept-level discovery. |
| **Cross-Encoder Reranking** | Optional ms-marco-MiniLM-L-6-v2 (~80MB) re-scores top candidates for higher precision. Query-dependent — unlike static quality scores, only fires when relevant. |
| **Dual-Format Output** | Key tools return both `items` (JSON metadata) and `context_block` (LLM-optimized Markdown). Blockquote for evidence text, ★★★ star ratings for relevance tiers, sentence-boundary truncation. Markdown is the primary LLM consumption channel; JSON serves programmatic consumers. |
| **Relevance Tiers** | Each result gets a percentile-based `relevance_tier` (high/medium/low) computed from Cross-Encoder scores. LLMs understand ★★★ more intuitively than raw floats like 0.0321. |
| **MMR Diversity** | Maximal Marginal Relevance at the chunk level (λ=0.4, tuned via grid search). Prevents single-paper dominance in top results. Hard cap of 3 chunks per paper + per-document penalty. +54% paper diversity vs un-diversified. |
| **Bilingual Query Expansion** | Dictionary-based CN↔EN term mapping with zero latency (LRU-cached lookup). Built-in dictionary covers common academic terms like methodology names and research concepts. Auto-extracts from your Zotero tags for personalization. Supports adding custom term pairs via `add_query_synonym` tool — your dictionary grows with your library. |
| **Retrieval Observability** | Every search emits a JSONL trace: query, strategy, candidate counts, reranker state, top-20 results with scores, latency breakdown (keyword/semantic/rerank/MMR/total). Byte-offset index for fast replay. 3 query tools: `recent_retrievals`, `retrieval_trace`, `retrieval_stats`. |
| **Embedding Diagnostics** | 6-phase analysis: intra/inter-paper similarity, outlier chunk detection, chunk length-similarity Pearson correlation, section-type embedding separation, automated issue detection + fix suggestions. |
| **Systematic Evaluation** | 60 golden queries across direct-hit, cross-document, and no-answer categories. Metrics: Recall@5/10/20, MRR, NDCG@10. CLI with `--save-baseline` / `--compare` for A/B testing. |
| **Index Audit** | 7-phase library quality audit: paginated scan, per-paper scoring, library coverage, noise detection, embedding separation, health scoring, recommendations. |

---

## Quick Start

### 1. Install

```bash
pip install zra-mcp
```

> ONNX INT8 embedding (~347MB) is the default. It is 2-3x faster on CPU and uses 4x less disk than FP32.

### 2. Configure Zotero

Enable the Zotero local API: **Edit → Settings → Advanced →** check "Allow other applications on this computer to communicate with Zotero."

Create a `.env` file in your working directory (minimum read-only mode):
```ini
ZOTERO_LOCAL=true
```

For write operations (add papers, notes, tags), add your [Zotero API key](https://www.zotero.org/settings/keys):
```ini
ZOTERO_LOCAL=true
ZOTERO_LIBRARY_ID=12345678
ZOTERO_API_KEY=your_api_key_here
```

### 3. Connect your AI client

See [Client Setup](#client-setup). The MCP server auto-syncs your index on startup.

### 4. Test

Start Zotero, open a new chat, ask: *"List all collections in my Zotero library."*

> First-run builds a vector index of your PDFs. This is a one-time cost — subsequent startups use incremental sync. See the pipeline diagram above for what happens under the hood.

---

## Client Setup

All MCP clients use the same stdio config. Two forms:

- **pip install:** command is `zra-mcp`
- **Source install:** full Python path + `args: ["-m", "project_a_mcp.server"]` + `cwd`

### Cursor

**Settings → MCP → Add new MCP server**, or `.cursor/mcp.json`:
```json
{ "mcpServers": { "zra-mcp": { "command": "zra-mcp" } } }
```

### Claude Desktop

Edit `claude_desktop_config.json`:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{ "mcpServers": { "zra-mcp": { "command": "zra-mcp" } } }
```

Restart — hammer icon appears. Requires Pro or Team subscription.

### Cherry Studio

**Settings → MCP Servers → Add → JSON mode.** Cherry Studio needs extra fields:
```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp", "type": "stdio", "isActive": true,
      "command": "zra-mcp"
    }
  }
}
```
Then: **Settings → Model Services** (Claude/GPT-4o recommended for tool calling) → new chat → enable MCP toggle. Full guide: [docs/cherry-studio-setup-en.md](./docs/cherry-studio-setup-en.md).

### Codex CLI

`~/.codex/config.json`:
```json
{ "mcpServers": { "zra-mcp": { "command": "zra-mcp" } } }
```
Verify: `codex mcp list`.

> **Any other stdio MCP client** uses the same config. Env vars read from `<project>/.env`.

---

## MCP Tools (36)

| Category | Tools |
|----------|-------|
| **Discover** | `search_papers`, `search_online_literature`, `search_cnki_literature`, `find_related_literature`, `expand_citation_network`, `cnki_paper_detail`, `cnki_navigate_pages`, `find_similar_papers`, `browse_library`, `find_duplicates`, `merge_duplicates` |
| **Read** | `get_paper`, `get_paper_content`, `search_annotations`, `create_annotation` |
| **Write** | `suggest_citations`, `export_bibliography`, `add_paper`, `cnki_add_to_zotero` |
| **Manage** | `add_note`, `edit_tags`, `manage_collections` |
| **Insight** | `reading_status`, `recommend_papers`, `generate_review_note`, `generate_reading_note`, `suggest_tags`, `find_arguments` |
| **Admin** | `sync_index`, `check_health`, `inspect_index`, `test_recall`, `recent_retrievals`, `retrieval_trace`, `retrieval_stats`, `add_query_synonym` |

<details>
<summary>Expand tool details</summary>

### Discover
- **`search_papers`** — Primary search. Hybrid keyword + semantic. Supports `expand_context`, `expand_neighbors`, `diversity_weight` (MMR, default 0.4). Returns dual-format output: `items` (JSON metadata) + `context_block` (LLM-optimized Markdown with blockquote evidence and ★★★ relevance tiers).
- **`search_online_literature`** — OpenAlex + CrossRef + Semantic Scholar (English/international).
- **`search_cnki_literature`** — CNKI Chinese journal search (optional, browser automation).
- **`find_related_literature`** — 5 parallel strategies: Corpus-First, keyword, citation, S2 recommendations, OpenAlex.
- **`expand_citation_network`** — Forward/backward citations via OpenAlex.
- **`find_similar_papers`** / **`browse_library`** / **`find_duplicates`** / **`merge_duplicates`** — Library navigation.
- **`cnki_paper_detail`** / **`cnki_navigate_pages`** — CNKI detail + pagination.

### Read
- **`get_paper`** — Metadata + abstract.
- **`get_paper_content`** — Semantic query, page range, fulltext, or outline; optional annotations overlay.
- **`search_annotations`** — Cross-paper highlight/comment search.
- **`create_annotation`** — PDF highlight (dry-run by default).

### Write & Manage
- **`suggest_citations`** — Match draft text to library evidence.
- **`export_bibliography`** — BibTeX or formatted citations.
- **`add_paper`** — Import by DOI/arXiv/ISBN/BibTeX/URL (dry-run by default).
- **`add_note`** / **`edit_tags`** / **`manage_collections`** — Library organization (dry-run by default).

### Insight
- **`reading_status`** — Classify as deep_read / browsed / unread.
- **`recommend_papers`** — Personalized via OpenAlex + S2.
- **`generate_review_note`** — Multi-paper evidence extraction for literature review.
- **`generate_reading_note`** — Structured single-paper note.
- **`suggest_tags`** — Methodology/domain/data tag suggestions (suggest-only).
- **`find_arguments`** — Stance-classified evidence search (support/oppose/neutral).

### Admin
- **`sync_index`** — Incremental vector index sync. Auto-runs on startup.
- **`check_health`** — Connection, index, embedding model, API diagnostics.
- **`inspect_index`** — Chunk stats, completeness flags, section breakdown, per-paper details.
- **`test_recall`** — Retrieval quality test for a specific paper.
- **`recent_retrievals`** / **`retrieval_trace`** / **`retrieval_stats`** — Retrieval observability.
- **`add_query_synonym`** — Add bilingual query expansion pairs.

</details>

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_LOCAL` | `true` | Read from local Zotero API |
| `ZOTERO_API_KEY` | — | Required for write operations |
| `ZOTERO_LIBRARY_ID` | `0` | Your Zotero user ID |
| `EMBEDDING_BACKEND` | `auto` | `auto` (ONNX INT8 preferred), `onnx_int8`, `sentence_transformers` |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Model for sentence_transformers backend (ONNX INT8 uses pre-quantized model automatically) |
| `EMBEDDING_MAX_SEQ_LEN` | `1024` | Sequence length cap (memory safety) |
| `HF_ENDPOINT` | — | HuggingFace mirror (e.g. `https://hf-mirror.com`) |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder (`none` to disable) |
| `CHROMA_PERSIST_DIR` | `.chroma_db` | Vector database path |
| `ZRA_AUTO_SYNC` | `true` | Auto incremental sync on startup |
| `ZRA_CLEAN_ENABLED` | `true` | Strip journal boilerplate before chunking |
| `SEMANTIC_SCHOLAR_API_KEY` | — | Higher rate limits for online search |
| `OPENALEX_MAILTO` | — | OpenAlex polite pool |
| `UNPAYWALL_EMAIL` | — | Unpaywall OA PDF lookup |
| `CORE_API_KEY` | — | CORE repository full-text |
| `CNKI_ENABLED` | `false` | Enable CNKI browser search |
| `CNKI_CDP_URL` | — | Chrome remote debugging URL |

---

## Tables & Figures

Tables and figures are **caption-anchored records** — not parsed into structured cells. Reliable table structuring is a vision problem. Our approach:

- **Tables:** caption + canonical ref + raw content block (values stay searchable)
- **Figures:** caption only (roughly what the figure shows — no image decoding)
- **Cross-referencing:** prose "as shown in Table 3 / Figure 2" auto-links to records

For true structured tables, preprocess PDFs with [MinerU](https://github.com/opendatalab/MinerU), [Docling](https://github.com/docling-project/docling), [Marker](https://github.com/datalab-to/marker), or [PyMuPDF4LLM](https://github.com/pymupdf/RAG).

---

## Other Features

### Online Literature Discovery
- Multi-source search (OpenAlex + CrossRef + Semantic Scholar in parallel)
- Corpus-First citation network expansion
- Three-Index Verification (CrossRef + OpenAlex + S2) — unverifiable papers filtered out
- Anti-hallucination: `[MATERIAL GAP]` tags when search returns zero results

### CNKI (Chinese Literature)
- Optional browser automation via Chrome DevTools Protocol
- Journal-level tags (CSSCI, PKU Core, CSCD, SCI, EI)
- Direct Zotero import without DOI lookup

### Reading & Writing
- Reading status detection (deep_read / browsed / unread)
- Personalized recommendations from reading activity
- Literature review generator with page-level citations
- Argument finder: stance-classified evidence (support/oppose/neutral)
- Smart tag suggestions (methodology/domain/data-type, suggest-only)

### Library Management
- Add papers by DOI, arXiv, ISBN, BibTeX, or publisher URL
- OA PDF waterfall: arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC
- Duplicate detection and merge (dry-run preview)
- All write operations require explicit confirmation

---

## Updating

```bash
pip install --upgrade zra-mcp
```

> If the chunking strategy has been updated, `sync_index` auto-detects the version change and rebuilds.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **Connection refused / no results** | Ensure Zotero desktop is running with local API enabled |
| **New papers not found** | Say "sync my index" or restart MCP (auto-sync on startup) |
| **Write operations fail** | Set `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID` in `.env` |
| **Slow first start** | First-run indexing downloads ONNX INT8 model (~347MB). Use `HF_ENDPOINT=https://hf-mirror.com` in China |
| **Poor search results** | Ask "check system health" → `check_health`; "show recent retrievals" → `recent_retrievals` |
| **"Why didn't this paper show up?"** | "Show recent retrievals" → get trace ID → "replay retrieval trace [id]" |
| **Index seems stale** | "Inspect my index" → `inspect_index` shows version and quality |
| **Windows: script blocked** | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell |
| **MCP tools not called** | Use a model with function calling; enable MCP/tools in client settings |

---

## Architecture

```
research_core/
  parsers/     — PDF extraction, text cleaner (52 rules), CJK-aware chunker,
                IMRaD section detector, chunk quality tagging
  rag/         — ChromaDB store + retriever, ONNX INT8 + FP32 embedding,
                SQLite metadata DB, Cross-Encoder + MMR reranking,
                bilingual query expansion, evaluation, retrieval logger,
                embedding diagnostics
  tools/       — 36 MCP tool implementations (discover/read/write/manage/insight/admin)
  zotero/      — Zotero local + web API client
project_a_mcp/ — MCP server entry point (stdio transport)
scripts/       — CLI utilities (index, audit, evaluate, benchmark, publish)
tests/         — pytest suite + 60 golden eval queries
docs/          — Setup guides (Cherry Studio CN/EN), development logs
```

---

## Acknowledgments

Inspired by [zotero-mcp](https://github.com/54yyyu/zotero-mcp), [cnki-skills](https://github.com/cookjohn/cnki-skills), [academic-research-skills](https://github.com/Imbad0202/academic-research-skills), [nature-skills](https://github.com/Yuan1z0825/nature-skills).

---

## License

MIT
