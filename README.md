# Zotero Research Assistant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io/)

**[English](./README.md)** | **[中文](./README_zh.md)**

---

> **Turn your Zotero library into an AI-powered research engine.**
>
> Search by meaning, discover related papers across 200M+ works, get personalized reading recommendations, and manage your entire academic workflow — all through natural language.

Works with **Cursor**, **Claude Desktop**, **Cherry Studio**, **Trae**, **OpenAI Codex CLI**, and any MCP-compatible client.

## Preface

This project was built to help graduate students and researchers — especially those without a computer science background — leverage AI-enhanced Zotero for more efficient academic workflows. The documentation is deliberately detailed and step-by-step. Cherry Studio was chosen as the primary interaction interface because it provides a user-friendly GUI that doesn't require any terminal expertise. We believe powerful research tools should be accessible to everyone, not just developers.

**If you have no programming experience**, go directly to [docs/cherry-studio-setup-en.md](./docs/cherry-studio-setup-en.md) and follow the instructions step by step. Try to complete it independently — if you get stuck at any point, paste the error message to any AI chatbot (ChatGPT, DeepSeek, Kimi, etc.) and ask for help. Consider this your first step into the world of programming and AI tools. It's easier than you think.

---

### Highlights

| | |
|---|---|
| **35 MCP Tools** | One intent per tool — LLMs always pick the right one |
| **Hybrid RAG Search** | Keyword + semantic (bge-m3, 100+ languages) + cross-encoder reranking |
| **Semantic Chunking** | Paragraph-aware splitting with section detection, chunk quality scoring (7 metrics) |
| **Text Cleaning Engine** | 52 blacklist rules strip journal boilerplate (headers, footers, article info) from EN+CN papers |
| **Section-Parent Context** | Hit a chunk → auto-expand to its full enclosing section for richer LLM context |
| **SQLite Metadata Layer** | 7-table relational DB (papers, sections, chunks_meta, figures, tables) separate from vector store |
| **Retrieval Observability** | JSONL trace logging with byte-offset index — replay any past search to debug rankings |
| **Multi-Source Discovery** | OpenAlex + CrossRef + Semantic Scholar in parallel, Three-Index Verification to prevent fabricated citations |
| **Citation Network Expansion** | Corpus-First strategy + forward/backward citations + OpenAlex Related Works |
| **Anti-Hallucination** | Zero-fabrication policy with `[MATERIAL GAP]` structural tags; every paper has a verifiable source link |
| **RAG Evaluation** | Recall@K, MRR, NDCG metrics + A/B baseline comparison + 60 golden queries |
| **Embedding Diagnostics** | 6-phase analysis: intra/inter separation, outlier detection, length correlation, section analysis |
| **Personalized Recommendations** | Learns from your reading activity and annotations to suggest what to read next |
| **Literature Review Generator** | Select papers → extract evidence with citations → AI synthesizes thematic review |
| **Smart Tag Suggestions** | Auto-analyze metadata to recommend methodology/domain/data tags (confirm before apply) |
| **Argument Finder** | Find supporting & opposing evidence for your thesis from your library |
| **CNKI Integration** | Optional Chinese literature search with journal-level tags (CSSCI/PKU Core/CSCD) |
| **OA PDF Waterfall** | arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC automatic full-text retrieval |
| **Write Safety** | All destructive operations require explicit user approval (dry-run by default) |

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Client Setup](#client-setup)
  - [Cursor](#cursor)
  - [Claude Desktop](#claude-desktop)
  - [Cherry Studio](#cherry-studio)
  - [OpenAI Codex CLI](#openai-codex-cli)
- [Example Workflows](#example-workflows)
- [MCP Tools (32)](#mcp-tools-32)
- [Configuration](#configuration)
- [CNKI Setup (Optional)](#cnki-setup-optional)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)
- [Development](#development)
- [Acknowledgments](#acknowledgments)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## Features

### Local Library Intelligence

- **Hybrid search** — Zotero keyword search + ChromaDB semantic search, merged with Reciprocal Rank Fusion; fallback to Zotero full-text index
- **Filter-only search** — list papers by year, tags, or collection with an empty query
- **Cross-encoder reranking** — optional `ms-marco-MiniLM-L-6-v2` for higher precision
- **Multilingual** — `BAAI/bge-m3` embedding (1024-dim, 100+ languages including Chinese and English)
- **Page-level traceability** — retrieved passages include exact PDF page numbers
- **Full-text & outline** — read complete paper text or PDF table of contents
- **Incremental index sync** — version-based diff; auto-sync on MCP startup

### Semantic RAG Pipeline

- **Text cleaning** — 52 blacklist regex rules strip journal boilerplate before chunking (EN: article-info blocks, running headers; CN: volume/issue lines, CLC numbers, funding footers; Universal: page numbers, DOI lines, repeated punctuation). Controlled by `ZRA_CLEAN_ENABLED=true` (default on). Cleaning stats reported in `sync_index`.
- **Paragraph-aware chunking** — splits on natural boundaries (paragraphs → sentences), adaptive merging to target 600-char chunks; CJK-aware sentence splitting (breaks at `。！？` without needing spaces) and PDF soft-wrap repair (`满\n意度`→`满意度`) so sentences are never cut mid-word
- **Chunk quality scoring** — every chunk gets 7 quality fields: `coherence_score` (sentence length variation), `information_density` (stopword ratio), `boilerplate_ratio`, `sentence_count`, `starts_with_conjunction`, `language` (zh/en/mixed), `quality_flag` (good/noisy/incomplete/boilerplate). Stored in ChromaDB metadata for quality-aware filtering.
- **Section detection** — IMRaD classification via regex heading patterns (EN numbered "1. Introduction", CN "一、引言"); automatically identifies and tags reference/boilerplate sections to exclude from search by default
- **Figure & table caption tagging** — detects `Figure/Fig./Table/图/表` captions and marks chunks for targeted retrieval
- **Table & figure cross-referencing** — tables and figures are indexed as lightweight caption-anchored records, not structured into cells. For a table we keep *where* it is, its caption, and the raw block content from the caption until the prose resumes (so its values stay searchable); for a figure we keep only *where* it is and *roughly what it shows* (its caption — no image recognition). Prose passages that cite "Table 3" / "Figure 2" are auto-linked to those records, surfaced via `get_paper_content`'s `referenced_tables` / `referenced_figures`. (True table structuring is a vision problem — see [Tables & figures](#tables--figures) for optional visual parsers.)
- **Section-parent context expansion** — when `expand_context=True`, hitting a chunk also fetches its entire enclosing section from SQLite + ChromaDB, giving the LLM complete paragraph context instead of an isolated fragment. Cache-batched for performance.
- **SQLite metadata database** — separate relational layer (inside `.chroma_db/papers.db`) with 7 tables: `papers` (title, abstract, authors, year, DOI, keywords), `sections` (hierarchical IMRaD structure), `chunks_meta` (location + quality scores), `figures`, `table_records`, and cross-reference tables. Zero user setup — auto-created on first sync.
- **Embedding diagnostics** — 6-phase analysis pipeline: per-paper intra-similarity, cross-paper separation ratio, outlier chunk detection (centroid coherence < 0.3), chunk length-similarity Pearson correlation, section-type embedding separation, automated issue detection + fix suggestions.
- **Evaluation framework** — 60 golden queries (direct, cross-document, no-answer categories), metrics: Recall@5/10/20, MRR, NDCG@10. CLI with `--save-baseline` and `--compare` for A/B testing.
- **Retrieval logging** — every search emits a JSONL trace (query, strategy, candidate counts, reranker details, top-20 results with scores, latency breakdown by keyword/semantic/rerank/total). Byte-offset index file enables fast replay by trace ID. Three query tools: `recent_retrievals`, `retrieval_trace`, `retrieval_stats`.
- **Chunking versioning** — strategy changes auto-trigger full index rebuild; no stale data
- **Index diagnostics** — `inspect_index` shows chunk statistics, quality flag distribution, section breakdown, figure/table counts, garbled text detection
- **Recall testing** — `test_recall` verifies a paper's own chunks appear in top-20 search results
- **Health monitoring** — `check_health` diagnoses connections, index status, embedding model, and configuration

### Online Literature Discovery

- **Multi-source search** — queries OpenAlex, CrossRef, and Semantic Scholar in parallel with publisher-diverse ranking
- **Corpus-First strategy** — when a paper's reference list is available, the system expands citation networks from those known references as the PRIMARY search strategy
- **Discipline filtering** — optional `fields_of_study` parameter constrains results to relevant academic fields
- **Related paper discovery** — provide a paper's metadata → generates tiered pairwise queries → searches all sources → post-filters → returns deduplicated hits
- **Three-Index Verification** — every result with a DOI is cross-checked against CrossRef, OpenAlex, and Semantic Scholar; unverifiable papers are filtered out
- **Source verification** — every returned paper includes a verifiable link (DOI URL, Semantic Scholar URL, or CNKI link)
- **Anti-hallucination guardrails** — structural `[MATERIAL GAP]` tags when search returns zero results

### CNKI (Chinese Literature)

- **CNKI integration** — optional Chinese journal search via browser automation (disabled by default, enabled on demand)
- **Journal-level tags** — search results include indexing status badges (CSSCI, PKU Core, CSCD, SCI, EI)
- **Direct Zotero import** — export papers from CNKI to Zotero without manual DOI lookup
- **Paper detail extraction** — full metadata (abstract, keywords, DOI, affiliations) from CNKI detail pages
- **Smart pagination** — AI proactively fetches more results when thorough coverage is needed

### Reading Insight & Recommendations

- **Reading status detection** — heuristic classification (deep_read / browsed / unread) based on annotation count, notes, and PDF open history
- **Personalized recommendations** — identifies your most-engaged papers → queries OpenAlex Related Works + S2 Recommendations in parallel → ranks by cross-seed frequency
- **Focus topic extraction** — surfaces your active research themes from recent reading tags
- **Literature review generation** — select papers → extract relevant passages with page-level citations → structured output for AI synthesis
- **Smart tag suggestions** — analyzes title/abstract to recommend methodology, domain, and data-type tags; suggest-only (never auto-applies)
- **Argument finder** — given a thesis/claim, searches library for evidence grouped by stance (support/oppose/neutral)

### Library Management

- **Add papers** — DOI, arXiv, ISBN, BibTeX, or publisher URL (ScienceDirect, Springer, Wiley, …)
- **Open-access PDF waterfall** — arXiv → Unpaywall → OpenAlex → Semantic Scholar → CORE → PMC
- **Duplicate merge** — find by DOI/title, merge with dry-run preview
- **Annotations** — search highlights across the library; create highlights on PDFs
- **Write safety** — all write/delete operations preview first; requires explicit user approval
- **Hybrid Zotero mode** — fast local reads + web API writes (when API key is set)

---

## Requirements

| Component | Version / Note |
|-----------|----------------|
| **Python** | 3.11+ |
| **Zotero** | 7+ desktop app, running with local API enabled |
| **MCP client** | Cursor, Claude Desktop, Cherry Studio, Trae, Codex CLI, etc. |
| **LLM** | Any model with tool/function calling (Claude, GPT-4o, DeepSeek, Qwen, Gemini, …) |
| **Disk** | ~2.5 GB for embedding model (`bge-m3`) on first run |
| **Git** | Only needed for Option B (clone from source) |

> **Path tip:** Install in a short path without spaces or non-ASCII characters, e.g. `~/zotero-research-assistant` (macOS/Linux) or `C:\Dev\zotero-research-assistant` (Windows).

---

## Quick Start

### 1. Install

**Option A: pip install (recommended for most users)**

```bash
pip install zotero-research-assistant
```

With CNKI (Chinese literature) support:
```bash
pip install "zotero-research-assistant[cnki]"
```

After installing, run `zra-mcp` to start the MCP server. Skip to [Step 2](#2-configure-zotero).

**Option B: Clone from source (for development or customization)**

```bash
git clone https://github.com/qiobn/zotero-research-assistant.git
cd zotero-research-assistant
```

Install [uv](https://github.com/astral-sh/uv) (fast Python package manager) if not already present:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

Create a virtual environment and install:

```bash
uv venv .venv --python 3.13      # use 3.12 or 3.11 if unavailable
uv pip install -e .
```

Verify installation:

```bash
# macOS / Linux
source .venv/bin/activate
python -c "from project_a_mcp.server import mcp; print('OK')"

# Windows (PowerShell)
.venv\Scripts\activate
python -c "from project_a_mcp.server import mcp; print('OK')"
```

> First run downloads the embedding model (~2.3 GB). If download is slow, set `HF_ENDPOINT=https://hf-mirror.com` and retry.

### 2. Configure Zotero

**Enable local API** (required):

1. Open Zotero → **Edit → Settings → Advanced**
2. Check **"Allow other applications on this computer to communicate with Zotero"**
3. Verify: http://localhost:23119/api/ should return JSON

**Set environment variables:**

If you used Option B (clone), create a `.env` file in the project folder:
```bash
cp .env.example .env
```

If you used Option A (pip install), set environment variables in your shell or create a `.env` file in your working directory.

Minimum for **read-only** mode (search, read, cite):
```ini
ZOTERO_LOCAL=true
```

For **write operations** (add papers, notes, tags, collections), also set your [Zotero API key](https://www.zotero.org/settings/keys):
```ini
ZOTERO_LOCAL=true
ZOTERO_LIBRARY_ID=12345678
ZOTERO_API_KEY=your_api_key_here
```

### 3. Build the vector index (first time)

The MCP server **auto-syncs on startup** (`ZRA_AUTO_SYNC=true` by default). On first launch it will parse all your PDFs and build the semantic index automatically.

If you cloned from source and want to build the index manually:
```bash
python scripts/index_library.py
```

The index is stored in `.chroma_db/` (local only).

> **First-time indexing can take a while — let it run in the background.** The
> first build parses every PDF and computes embeddings; the more papers, the
> longer it takes (rough guide: ~3–5 min for 100 papers, ~10–15 min for 500,
> longer on CPU or large libraries). The auto-sync runs in a background thread
> and does not block the client; for the manual script you can background it too
> (e.g. `nohup python scripts/index_library.py &`). Only the first build (or
> after library changes) waits — subsequent startups are fast incremental syncs.

### 4. Connect your AI client

See the [Client Setup](#client-setup) section below for your specific tool.

### 5. Test the connection

1. Start **Zotero desktop**
2. Open a **new chat** in your MCP client
3. Ask: *"List all collections in my Zotero library"*

If you see your collections, setup is complete.

---

## Client Setup

This is an MCP server over **stdio**. The config is the same everywhere — only *where you put it* differs. There are two base forms:

- **pip install:** the command is simply `zra-mcp`.
- **Source install:** point at the project's Python and set the working directory.

```bash
# Source install — get the Python path (run inside the project folder)
# macOS / Linux:
echo "$(pwd)/.venv/bin/python"
# Windows (PowerShell):
echo "$PWD\.venv\Scripts\python.exe"
```

> **Windows source installs:** use `<project>\.venv\Scripts\python.exe` for both `command` and `cwd`.

### Cursor

**Settings → MCP → Add new MCP server**, or edit `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "zra-mcp": { "command": "zra-mcp" }
  }
}
```

Source install — use the full path instead:

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

Restart Cursor — the tools appear in Agent mode.

### Claude Desktop

**Config file location:**
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json` (e.g. `C:\Users\YourName\AppData\Roaming\Claude\claude_desktop_config.json`)

Create or edit the file:

```json
{
  "mcpServers": {
    "zra-mcp": { "command": "zra-mcp" }
  }
}
```

Source install — replace `command` with the full Python path and add `args` + `cwd`:

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

Restart Claude Desktop — a hammer icon appears in the chat input area. Click it to see the 35 available tools.

> **Note:** Claude Desktop requires a Pro or Team subscription. The `.env` file is auto-detected from the project directory (source install) or the current working directory (pip install). Environment variables set in your shell are also picked up.

### Cherry Studio

**Settings → MCP Servers → Add → JSON mode.** Cherry Studio needs three extra fields (`name`, `type`, `isActive`):

```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "zra-mcp"
    }
  }
}
```

Source install — swap `command` for the full Python path and add `args` + `cwd`:

```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

Then:
1. **Settings → Model Services** — configure an LLM (DeepSeek, GPT-4o, Claude, Qwen, …). For the best tool-calling experience, Claude or GPT-4o is recommended.
2. Start a new chat → click the **MCP toggle** (plug icon) in the chat input bar to enable tools.
3. The MCP server status should show **"Connected"** with 35 tools listed.

If the `.env` file is not in the default search path, add `"cwd": "/path/to/dir/containing/.env"` to the JSON config.

Full step-by-step walkthrough with screenshots: [docs/cherry-studio-setup-en.md](./docs/cherry-studio-setup-en.md).

### OpenAI Codex CLI

**pip install** (simplest):

```json
{
  "mcpServers": {
    "zra-mcp": { "command": "zra-mcp" }
  }
}
```

**Source install:**

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

Add this to `~/.codex/config.json` (global) or `<project>/.codex/config.json` (per-project). Then run `codex "find papers about urban planning in my library"` — the 35 tools are auto-discovered.

Run `codex mcp list` to verify the server is connected and all tools are registered. Environment variables are read from the `cwd` directory's `.env` file, or your shell environment.

> **Any other stdio MCP client** (Trae, Windsurf, …) uses the same config — point it at the `command` / `args` / `cwd` above. Environment is read from `<project>/.env` automatically.

---

## Example Workflows

### Research Discovery

```
User: Find papers about 15-minute cities published after 2020
  → search_papers (local library)

User: Search online for recent studies on urban green infrastructure
  → search_online_literature (OpenAlex + CrossRef + S2)

User: I'm reading this paper [title, keywords]. Find me related literature.
  → find_related_literature (5 parallel strategies, verified results)

User: Show me who cites this paper and what it references
  → expand_citation_network (forward + backward citations)
```

### Reading & Analysis

```
User: What does this paper say about the research methodology?
  → get_paper_content (semantic search within paper)

User: Summarize these 5 papers into a literature review about "method evolution"
  → generate_review_note → AI synthesizes thematic review with citations

User: My thesis is "public services are unevenly distributed" — find evidence
  → find_arguments (returns supporting + opposing passages with stance labels)

User: What should I read next?
  → recommend_papers (based on your annotation activity)

User: Show me all figures and tables mentioned in this paper
  → get_paper_content (filtered to figure/table chunks)
```

### Writing & Citing

```
User: I'm writing: "Walkability is a key indicator of urban quality..." — suggest citations
  → suggest_citations (matches your draft to library evidence)

User: Export BibTeX for the top 3 results
  → export_bibliography

User: Add this paper: 10.1016/j.cities.2025.105902
  → add_paper (preview → confirm → auto-downloads OA PDF)
```

### Library Organization

```
User: Analyze these papers and suggest tags
  → suggest_tags (methodology/domain/data classification, suggest-only)

User: Tag these papers as "core reading"
  → edit_tags (preview → confirm)

User: Which papers have I actually read? Which are unread?
  → reading_status (heuristic: annotations, notes, PDF open history)
```

### System Diagnostics

```
User: Is everything working correctly?
  → check_health (connection, index, embedding, configuration)

User: How good is my index quality?
  → inspect_index (chunk stats, section breakdown, figure/table counts)

User: Can this paper be retrieved properly?
  → test_recall (searches by title, checks if own chunks appear in top-20)
```

> **Write safety**: all destructive operations (add paper, notes, tags, merge duplicates) always preview first. The assistant asks for explicit confirmation before executing.

---

## MCP Tools (35)

| Category | Tools |
|----------|-------|
| **Discover** | `search_papers`, `search_online_literature`, `search_cnki_literature`, `find_related_literature`, `expand_citation_network`, `cnki_paper_detail`, `cnki_navigate_pages`, `find_similar_papers`, `browse_library`, `find_duplicates`, `merge_duplicates` |
| **Read** | `get_paper`, `get_paper_content`, `search_annotations`, `create_annotation` |
| **Write** | `suggest_citations`, `export_bibliography`, `add_paper`, `cnki_add_to_zotero` |
| **Manage** | `add_note`, `edit_tags`, `manage_collections` |
| **Insight** | `reading_status`, `recommend_papers`, `generate_review_note`, `generate_reading_note`, `suggest_tags`, `find_arguments` |
| **Admin** | `sync_index`, `check_health`, `inspect_index`, `test_recall`, `recent_retrievals`, `retrieval_trace`, `retrieval_stats` |

<details>
<summary>Expand tool details</summary>

### Discover
- **`search_papers`** — Primary search in your local library. Hybrid keyword + semantic. Use `query=""` with `year_from` / tags for filter-only listing.
- **`search_online_literature`** — Online discovery (English/international: OpenAlex, CrossRef, Semantic Scholar). Supports `fields_of_study` for discipline filtering.
- **`search_cnki_literature`** — CNKI Chinese journal search (optional module, disabled by default). Only triggered when user explicitly requests Chinese papers.
- **`find_related_literature`** — Multi-strategy related paper search. Supports Corpus-First mode, keyword search, citation network expansion, and Semantic Scholar recommendations — all in parallel.
- **`expand_citation_network`** — Find papers via citation relationships (forward & backward via OpenAlex). Accepts multiple DOIs for multi-seed expansion.
- **`cnki_paper_detail`** — Full metadata from a CNKI paper page.
- **`cnki_navigate_pages`** — Pagination & re-sorting for CNKI results.
- **`find_similar_papers`** — Similar papers to a known item (by `item_key`).
- **`browse_library`** — Collections, tags, recent items.
- **`find_duplicates`** / **`merge_duplicates`** — Detect and merge duplicates (dry-run by default).

### Read
- **`get_paper`** — Metadata + abstract.
- **`get_paper_content`** — Modes: semantic query, page range, fulltext, outline; optional annotations overlay.
- **`search_annotations`** — Search highlights/comments across all papers.
- **`create_annotation`** — Highlight text on a PDF (dry-run by default).

### Write & Manage
- **`suggest_citations`** — Match your draft text to library evidence.
- **`export_bibliography`** — BibTeX or formatted citations.
- **`add_paper`** — Import by DOI / arXiv / ISBN / BibTeX / URL (dry-run by default).
- **`cnki_add_to_zotero`** — Import CNKI papers directly (no DOI needed).
- **`add_note`**, **`edit_tags`**, **`manage_collections`** — Library organization (dry-run by default).

### Insight
- **`reading_status`** — Analyze reading progress. Classifies papers as `deep_read`, `browsed`, or `unread`.
- **`recommend_papers`** — Personalized recommendations via OpenAlex + S2.
- **`generate_review_note`** — Extract evidence from multiple papers for literature review.
- **`generate_reading_note`** — Structured reading note for one paper.
- **`suggest_tags`** — Analyze metadata to suggest tags. Suggest-only, never auto-applies.
- **`find_arguments`** — Find supporting and opposing evidence for a claim/thesis.

### Admin
- **`sync_index`** — Incremental vector index sync. Auto-runs on MCP startup. Reports quality summary, cleaning stats, and detects chunking version changes.
- **`check_health`** — Diagnose connections, index status, embedding model, online APIs, and configuration. Bilingual output with fix suggestions.
- **`inspect_index`** — View index quality: chunk stats, quality flag distribution, section breakdown, figure/table counts, garbled text detection, and per-paper details.
- **`test_recall`** — Test retrieval quality for a specific paper by querying with its title and checking if its own chunks are returned.
- **`recent_retrievals`** — Browse recent search traces (filter by strategy: hybrid/semantic/keyword/fallback). See what queries were run, how many results returned, and latency breakdown.
- **`retrieval_trace`** — Replay a specific past retrieval by trace ID. Shows full query, candidate counts, reranker details, and ranked result list — useful for debugging "why did this paper rank 5th not 1st?"
- **`retrieval_stats`** — Aggregate statistics: total queries, average latency, strategy distribution, fallback rate.

</details>

---

## Configuration

Copy [`.env.example`](./.env.example) to `.env` and adjust:

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_LOCAL` | `true` | Read from local Zotero API (fast) |
| `ZOTERO_API_KEY` | — | Required for write operations (hybrid mode) |
| `ZOTERO_LIBRARY_ID` | `0` | Your Zotero user ID |
| `EMBEDDING_BACKEND` | `auto` | Backend: `auto` (ONNX INT8 preferred), `onnx_int8`, `sentence_transformers` |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Sentence-transformer for semantic search (FP32 backend only) |
| `EMBEDDING_MAX_SEQ_LEN` | `1024` | Cap on embedding sequence length; bounds GPU/MPS memory on pathological long inputs |
| `HF_ENDPOINT` | — | HuggingFace mirror for model downloads (e.g. `https://hf-mirror.com` for users in China) |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker (`none` to disable) |
| `CHROMA_PERSIST_DIR` | `.chroma_db` | Local vector database path |
| `ZRA_AUTO_SYNC` | `true` | Auto incremental sync on MCP startup |
| `ZRA_CLEAN_ENABLED` | `true` | Strip journal boilerplate before chunking (headers, footers, article-info blocks) |
| `SEMANTIC_SCHOLAR_API_KEY` | — | Optional; higher rate limits for online search |
| `OPENALEX_MAILTO` | — | Optional; polite pool for OpenAlex API |
| `UNPAYWALL_EMAIL` | — | Optional; Unpaywall OA PDF lookup |
| `CORE_API_KEY` | — | Optional; CORE repository full-text |
| `CNKI_ENABLED` | `false` | Enable CNKI browser search (see below) |
| `CNKI_CDP_URL` | — | Chrome remote debugging URL |

All data stays **on your machine**: Zotero library, `.chroma_db/`, and HuggingFace model cache (`~/.cache/huggingface/`).

---

## Tables & figures

Tables and figures are **not** parsed into structured cells. Reliable table
structuring is fundamentally a vision problem: text/geometry-based detection
produces garbage on borderless "three-line" academic tables and even mis-segments
multi-column prose and reference lists into fake tables. So instead of pretending
to structure them, the indexer keeps lightweight **caption-anchored records**:

- **Tables** — the caption (e.g. "Table 3 …"), the page, and the raw block
  content from the caption until the prose resumes, so the table's *values* stay
  searchable. No cell/column structure.
- **Figures** — the caption only (roughly what the figure shows). No image is
  decoded.
- Prose that cites "Table 3" / "Figure 2" is linked to those records, so a
  passage and the thing it references resolve together (`referenced_tables` /
  `referenced_figures` in `get_paper_content`).

**Want true structured tables?** Preprocess your PDFs with a dedicated visual
document parser and store the result (e.g. Markdown/HTML) as a note or attachment
that gets indexed as text. Good options:

| Tool | Notes |
|------|-------|
| [MinerU](https://github.com/opendatalab/MinerU) | Best for academic papers, **CJK**, and complex/nested tables; preserves table structure and converts formulas to LaTeX (AGPL-3.0) |
| [Docling](https://github.com/docling-project/docling) | IBM; strong layout + `TableFormer` table-structure model, exports Markdown/JSON, native LangChain/LlamaIndex integration (MIT) |
| [Marker](https://github.com/datalab-to/marker) | Fast PDF→Markdown/JSON at scale (Surya OCR); good general table support |
| [PyMuPDF4LLM](https://github.com/pymupdf/RAG) | Lightweight, no ML — fastest for **native (non-scanned)** PDFs; same PyMuPDF engine this project already uses |

The first three use vision/ML models (heavier, slower) and are intentionally
kept out of the default pipeline; PyMuPDF4LLM is light but only handles
born-digital PDFs.

---

## CNKI Setup (Optional)

> **CNKI (China National Knowledge Infrastructure) is disabled by default.** It is only needed for searching Chinese-language journal papers. When you first ask the AI for Chinese literature (e.g., "search CNKI for…" or "检索中文文献"), it will prompt you to complete the setup below.

CNKI has no public API. This project uses [Playwright](https://playwright.dev/) to connect to your logged-in Chrome browser via CDP (Chrome DevTools Protocol), following the same approach as [cookjohn/cnki-skills](https://github.com/cookjohn/cnki-skills).

### Step 1: Install optional dependencies

```bash
uv pip install -e ".[cnki]"
playwright install chromium
```

### Step 2: Start Chrome with remote debugging

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# Linux
google-chrome --remote-debugging-port=9222
```

### Step 3: Log in to CNKI

Open https://www.cnki.net/ in that Chrome window and log in (typically requires institutional VPN or campus network).

### Step 4: Enable in `.env`

```env
CNKI_ENABLED=true
CNKI_CDP_URL=http://127.0.0.1:9222
```

### Step 5: Restart the MCP server

Reopen a chat window or restart your MCP client.

### Verify

Ask the AI: *"Search CNKI for highly-cited papers on geodetector since 2020"*

If results appear (with title, authors, journal, citations, and journal level tags like CSSCI/PKU Core), the setup is working.

### How it works

1. `search_cnki_literature` or `find_related_literature(scope="cnki")` → returns hits with `export_id` and `journal_level`
2. You select papers → AI calls `cnki_add_to_zotero(export_ids=[...])` → papers appear in Zotero
3. No DOI lookup needed; metadata is fetched from CNKI's internal export API

### Notes

- **Trigger:** CNKI tools are only called when you explicitly mention Chinese literature, CNKI, 知网, 核心期刊, CSSCI, etc.
- **Captcha:** If a Tencent slider captcha appears, solve it in the Chrome window and retry.
- **Zotero import:** Requires Zotero desktop running (uses localhost:23119 Connector API).
- **Compliance:** Requires legitimate institutional CNKI access.
- **Before each session:** Ensure the Chrome window from Step 2 is still running and the CNKI login is active.

### Known Issues & Limitations

> The CNKI module is currently unstable and disabled by default. It relies on browser automation which is inherently fragile.

| Issue | Cause | Workaround |
|-------|-------|------------|
| **Timeout on search** | CNKI pages load slowly; anti-bot throttling | Simplify your query; retry after a few seconds |
| **Chrome connection refused** | Chrome not started with `--remote-debugging-port` | Close ALL Chrome windows, restart with the flag |
| **Stale login session** | CNKI sessions expire after ~30 min | Re-login in the Chrome window |
| **Consecutive timeouts** | Rate limiting by CNKI | Wait 30s and retry |
| **Export to Zotero fails** | Zotero desktop not running | Ensure Zotero is running and API responds |

If CNKI consistently fails, fall back to the English-language online search (`search_online_literature` / `find_related_literature`) which is stable and does not require browser automation.

---

## Updating

**pip users:**
```bash
pip install --upgrade zotero-research-assistant
```

**Source install users:**
```bash
cd ~/zotero-research-assistant       # or your clone path
git pull
uv pip install -e .              # if dependencies changed
```

If using CNKI:
```bash
uv pip install -e ".[cnki]"
playwright install chromium
```

Restart your MCP client to reload the server.

> **Note:** If the chunking strategy has been updated in a new version, `sync_index` will automatically detect the version change and rebuild the entire index on next run.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **Connection refused / no results** | Ensure Zotero desktop is running and local API is enabled |
| **New papers not found** | Say *"sync my index"* or restart MCP (auto-sync on startup) |
| **Write operations fail** | Set `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID` in `.env` |
| **Slow first start** | Embedding model download (~2.3 GB); use `HF_ENDPOINT=https://hf-mirror.com` |
| **Windows: script blocked** | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell |
| **MCP tools not called** | Use a model with function calling; enable MCP/tools in client settings |
| **AI executes writes without asking** | Add to system prompt: *"Always wait for explicit confirmation before executing writes"* |
| **Poor search results** | Ask *"check my system health"* → `check_health` diagnoses issues; use *"show recent retrievals"* → `recent_retrievals` to inspect past query traces |
| **"Why didn't this paper show up?"** | Ask *"show my recent retrievals"* → get the trace ID → *"replay retrieval trace \[id\]"* to see full ranking details |
| **Index seems stale** | Ask *"inspect my index"* → `inspect_index` shows version and quality metrics |
| **CNKI: "search is disabled"** | Complete the [CNKI Setup](#cnki-setup-optional) steps |
| **CNKI: captcha** | Solve the slider in the Chrome window, then retry the search |

---

## Architecture

```
research_core/          # Shared library — Zotero client, RAG pipeline, search adapters, tools
  parsers/              #   PDF extraction, CJK-aware chunking, text cleaner (52 rules),
                        #   section detector (IMRaD), chunk quality scoring, caption detection
  rag/                  #   ChromaDB indexer/store/retriever, SQLite metadata DB,
                        #   embedding diagnostics, evaluation framework, retrieval logger
  tools/                #   35 tool implementations (one file per domain)
  zotero/               #   Zotero local + web API client
project_a_mcp/          # MCP server entry point (stdio transport)
scripts/                # CLI utilities (index_library, index_sample, audit_index,
                        #   run_evaluation, generate_eval_queries)
tests/                  # Unit + integration tests, 60 golden eval queries
docs/                   # Detailed setup guides (Cherry Studio CN/EN)
```

Each tool maps to **one user intent** — discovery tools return `item_key`, read/write tools consume it.

---

## Development

```bash
uv pip install -e ".[dev]"
pytest tests/ -v
ruff check .
ruff format .
```

Run CNKI integration tests (requires active CNKI session):
```bash
CNKI_ENABLED=true CNKI_CDP_URL=http://127.0.0.1:9222 pytest tests/mcp/test_cnki.py -v
```

---

## Acknowledgments

This project was inspired by and built upon ideas from:

- **[zotero-mcp](https://github.com/54yyyu/zotero-mcp)** — Pioneering work on connecting Zotero with AI assistants via MCP.
- **[cnki-skills](https://github.com/cookjohn/cnki-skills)** — Elegant approach to CNKI browser automation via Chrome DevTools Protocol.
- **[academic-research-skills](https://github.com/Imbad0202/academic-research-skills)** — Inspiration for the Corpus-First search strategy and structured anti-hallucination patterns.
- **[nature-skills](https://github.com/Yuan1z0825/nature-skills)** — Inspiration for the Three-Index Verification approach.

Thank you to the authors of these projects for sharing their work with the community.

---

## Disclaimer

1. **AI output quality depends on the connected model.** Although this project implements multiple anti-hallucination mechanisms (Three-Index Verification, `[MATERIAL GAP]` tagging, source provenance), the final quality of literature reviews, summaries, and recommendations is ultimately determined by the LLM you connect. Always verify AI-generated citations against the original sources before using them in academic work.

2. **For learning and research purposes only.** This project is open-source and intended solely for personal academic research and educational use. It is not commercialized. If any content or functionality inadvertently infringes on intellectual property or terms of service of third-party platforms, please open an issue and we will address it promptly.

3. **CNKI module compliance.** The CNKI browser automation module is provided for convenience only. Users must have legitimate institutional access. This module is disabled by default.

4. **Data privacy.** All processing happens locally by default. Your PDFs are parsed and embedded on your machine. However, if you configure a cloud-based LLM, paper content will be sent to that external service. Users working with sensitive or unpublished research should be aware of this.

5. **Trademark notice.** "Zotero" is a registered trademark of the Corporation for Digital Scholarship. This project is an independent community tool and is not affiliated with, endorsed by, or officially connected to Zotero.

---

## License

MIT
