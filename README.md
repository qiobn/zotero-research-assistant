# Zotero Research Assistant

[![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io/)

Connect your [Zotero](https://www.zotero.org/) library to AI assistants via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Search papers by meaning, read PDF passages with page numbers, suggest citations for your drafts, add papers by DOI/URL, and manage tags and collections — all through natural language.

Works with **Cursor**, **Claude Desktop**, **Cherry Studio**, **Trae**, **OpenAI Codex CLI**, and any MCP-compatible client.

**22 MCP tools**, one intent each, designed so LLMs always pick the right tool.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Client Setup](#client-setup)
  - [Cursor](#cursor)
  - [Claude Desktop](#claude-desktop)
  - [Cherry Studio](#cherry-studio)
  - [Trae](#trae)
  - [OpenAI Codex CLI](#openai-codex-cli)
  - [Other MCP Clients](#other-mcp-clients)
- [Example Prompts](#example-prompts)
- [MCP Tools (22)](#mcp-tools-22)
- [Configuration](#configuration)
- [CNKI Setup (Optional)](#cnki-setup-optional)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)
- [Development](#development)
- [Comparison](#comparison-with-zotero-mcp)
- [License](#license)

---

## Features

- **Hybrid search** — Zotero keyword search + ChromaDB semantic search, merged with Reciprocal Rank Fusion; fallback to Zotero full-text index
- **Filter-only search** — list papers by year, tags, or collection with an empty query
- **Cross-encoder reranking** — optional `ms-marco-MiniLM-L-6-v2` for higher precision
- **Multilingual** — `BAAI/bge-m3` embedding (1024-dim, 100+ languages including Chinese and English)
- **Page-level traceability** — retrieved passages include exact PDF page numbers
- **Full-text & outline** — read complete paper text or PDF table of contents
- **Online discovery** — search OpenAlex, CrossRef, and Semantic Scholar with publisher-diverse ranking
- **Related paper discovery** — give a paper's metadata, auto-generate multi-query search across all sources in one call
- **CNKI integration** — optional Chinese journal search via browser automation (disabled by default)
- **Incremental index sync** — version-based diff; auto-sync on MCP startup
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
| **Python** | 3.11 – 3.13 |
| **Zotero** | 7+ desktop app, running with local API enabled |
| **MCP client** | Cursor, Claude Desktop, Cherry Studio, Trae, Codex CLI, etc. |
| **LLM** | Any model with tool/function calling (Claude, GPT-4o, DeepSeek, Qwen, Gemini, …) |
| **Disk** | ~2.5 GB for embedding model (`bge-m3`) on first run |
| **Git** | To clone this repository |

> **Path tip:** Install in a short path without spaces or non-ASCII characters, e.g. `~/zotero-research-agent` (macOS/Linux) or `C:\Dev\zotero-research-agent` (Windows).

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/qiobn/zotero-research-agent.git
cd zotero-research-agent
```

### 2. Install dependencies

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

### 3. Configure Zotero

**Enable local API** (required):

1. Open Zotero → **Edit → Settings → Advanced**
2. Check **"Allow other applications on this computer to communicate with Zotero"**
3. Verify: http://localhost:23119/api/ should return JSON

**Create `.env`:**

```bash
cp .env.example .env
```

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

### 4. Build the vector index (first time)

Ensure Zotero is running, then:
```bash
python scripts/index_library.py
```

This parses PDFs and stores embeddings in `.chroma_db/` (local only, not committed to git).
Typical time: ~3–5 min for 100 papers, ~10–15 min for 500 papers.

After the first run, the server **auto-syncs incrementally** on startup (`ZRA_AUTO_SYNC=true`).

### 5. Connect your AI client

See the [Client Setup](#client-setup) section below for your specific tool.

### 6. Test the connection

1. Start **Zotero desktop**
2. Open a **new chat** in your MCP client
3. Ask: *"List all collections in my Zotero library"*

If you see your collections, setup is complete.

---

## Client Setup

All clients use the same MCP server entry point. You need two paths:

| Value | macOS / Linux | Windows |
|-------|--------------|---------|
| **Python binary** | `<project>/.venv/bin/python` | `<project>\.venv\Scripts\python.exe` |
| **Working directory** | `<project>` (full path) | `<project>` (full path) |

Replace `<project>` with your clone path (e.g. `/Users/you/zotero-research-agent` or `C:\Dev\zotero-research-agent`).

Quick path helper (run inside the project folder):
```bash
# macOS / Linux
echo "$(pwd)/.venv/bin/python"

# Windows (PowerShell)
echo "$PWD\.venv\Scripts\python.exe"
```

---

### Cursor

**Settings → MCP → Add new MCP server**

| Field | Value |
|-------|-------|
| Name | `zra-mcp` |
| Type | `command` (stdio) |
| Command | `<project>/.venv/bin/python -m project_a_mcp.server` |

Or add to `.cursor/mcp.json` in your workspace:

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-agent"
    }
  }
}
```

Windows variant:
```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "C:\\Dev\\zotero-research-agent\\.venv\\Scripts\\python.exe",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "C:\\Dev\\zotero-research-agent"
    }
  }
}
```

Restart Cursor after adding the config. The MCP tools will appear in Agent mode.

---

### Claude Desktop

Edit `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-agent"
    }
  }
}
```

Restart Claude Desktop. You should see the MCP tools icon (hammer) in the chat input area.

---

### Cherry Studio

**Settings → MCP Servers → Add → JSON mode:**

```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "/Users/you/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-agent"
    }
  }
}
```

Windows:
```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "C:\\Dev\\zotero-research-agent\\.venv\\Scripts\\python.exe",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "C:\\Dev\\zotero-research-agent"
    }
  }
}
```

Configure an LLM under **Settings → Model Services** (DeepSeek, GPT-4o, Claude, Qwen, etc.). Enable the MCP toggle in the chat interface to activate tools.

> For a detailed step-by-step guide (including screenshots), see [docs/cherry-studio-setup.md](./docs/cherry-studio-setup.md).

---

### Trae

Trae supports MCP servers via its settings panel.

**Settings → MCP → Add Server:**

| Field | Value |
|-------|-------|
| Name | `zra-mcp` |
| Transport | stdio |
| Command | Full path to `.venv/bin/python` (or `.venv\Scripts\python.exe` on Windows) |
| Arguments | `-m project_a_mcp.server` |
| Working Directory | Full path to the project root |

Or add to your Trae MCP configuration file (`.trae/mcp.json` in your workspace or global config):

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-agent"
    }
  }
}
```

Restart Trae after configuration. MCP tools become available in AI chat (Agent mode).

---

### OpenAI Codex CLI

[Codex CLI](https://github.com/openai/codex) supports MCP servers. Add to your `~/.codex/config.json` (or project-level `.codex/config.json`):

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-agent"
    }
  }
}
```

Then run Codex normally — it will discover and use the tools automatically:

```bash
codex "Find papers about urban accessibility in my Zotero library"
```

---

### Other MCP Clients

Any client that supports the [MCP stdio transport](https://modelcontextprotocol.io/docs/concepts/transports) can connect. The universal config is:

| Parameter | Value |
|-----------|-------|
| Transport | `stdio` |
| Command | `<project>/.venv/bin/python` |
| Arguments | `["-m", "project_a_mcp.server"]` |
| Working directory | `<project>` |
| Environment | Reads from `<project>/.env` automatically |

---

## Example Prompts

```
Find papers about 15-minute cities published after 2020
List all papers in my library from 2024 onwards
What does this paper say about the research methodology?
Find papers similar to [paper title]
I'm writing: "Walkability is a key indicator of urban quality..." — suggest citations
Export BibTeX for the top 3 results
Add this paper: 10.1016/j.cities.2025.105902
Search online for recent studies on urban green infrastructure
Find related papers (online + CNKI) for this paper: [title], keywords: [...]
Tag these papers as "core reading"
Sync my index — I just added new PDFs
```

**Write operations** (add paper, notes, tags, merge duplicates) always preview first. The assistant asks for confirmation before executing.

---

## MCP Tools (22)

| Category | Tools |
|----------|-------|
| **Discover** | `search_papers`, `search_online_literature`, `search_cnki_literature`, `find_related_literature`, `cnki_paper_detail`, `cnki_navigate_pages`, `find_similar_papers`, `browse_library`, `find_duplicates`, `merge_duplicates` |
| **Read** | `get_paper`, `get_paper_content`, `search_annotations`, `create_annotation` |
| **Write** | `suggest_citations`, `export_bibliography`, `add_paper`, `cnki_add_to_zotero` |
| **Manage** | `add_note`, `edit_tags`, `manage_collections` |
| **Admin** | `sync_index` |

<details>
<summary>Expand tool details</summary>

### Discover
- **`search_papers`** — Primary search in your local library. Hybrid keyword + semantic. Use `query=""` with `year_from` / tags for filter-only listing.
- **`search_online_literature`** — Online discovery (English/international: OpenAlex, CrossRef, Semantic Scholar). Default for online search unless user explicitly requests Chinese literature.
- **`search_cnki_literature`** — CNKI Chinese journal search (optional module, disabled by default). Only triggered when user explicitly requests Chinese papers / 中文文献 / CNKI.
- **`find_related_literature`** — Auto multi-query search from paper metadata. Provide a paper's title/abstract/keywords → generates 3-5 diverse queries → searches all sources (online/CNKI/both) → returns deduplicated results in one call.
- **`cnki_paper_detail`** — Full metadata (abstract, keywords, DOI, affiliations) from a CNKI paper page.
- **`cnki_navigate_pages`** — Pagination & re-sorting for CNKI results. Used proactively when user needs many papers or deeper search.
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
- **`cnki_add_to_zotero`** — Import CNKI papers directly (no DOI needed). Uses CNKI export API + Zotero Connector.
- **`add_note`**, **`edit_tags`**, **`manage_collections`** — Library organization (dry-run by default).

### Admin
- **`sync_index`** — Incremental vector index sync. Also runs automatically on MCP startup.

</details>

---

## Configuration

Copy [`.env.example`](./.env.example) to `.env` and adjust:

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_LOCAL` | `true` | Read from local Zotero API (fast) |
| `ZOTERO_API_KEY` | — | Required for write operations (hybrid mode) |
| `ZOTERO_LIBRARY_ID` | `0` | Your Zotero user ID |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Sentence-transformer for semantic search |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker (`none` to disable) |
| `CHROMA_PERSIST_DIR` | `.chroma_db` | Local vector database path |
| `ZRA_AUTO_SYNC` | `true` | Auto incremental sync on MCP startup |
| `SEMANTIC_SCHOLAR_API_KEY` | — | Optional; higher rate limits for online search |
| `OPENALEX_MAILTO` | — | Optional; polite pool for OpenAlex API |
| `UNPAYWALL_EMAIL` | — | Optional; Unpaywall OA PDF lookup |
| `CORE_API_KEY` | — | Optional; CORE repository full-text |
| `CNKI_ENABLED` | `false` | Enable CNKI browser search (see below) |
| `CNKI_CDP_URL` | — | Chrome remote debugging URL |

All data stays **on your machine**: Zotero library, `.chroma_db/`, and HuggingFace model cache (`~/.cache/huggingface/`).

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

- **Trigger:** CNKI tools are only called when you explicitly mention Chinese literature, CNKI, 知网, 核心期刊, CSSCI, etc. Regular online search uses OpenAlex/CrossRef/S2.
- **Captcha:** If a Tencent slider captcha appears, solve it in the Chrome window and retry.
- **Zotero import:** Requires Zotero desktop running (uses localhost:23119 Connector API).
- **Compliance:** Requires legitimate institutional CNKI access.
- **Before each session:** Ensure the Chrome window from Step 2 is still running and the CNKI login is active.

---

## Updating

```bash
cd ~/zotero-research-agent       # or your clone path
git pull
uv pip install -e .              # if dependencies changed
```

If using CNKI:
```bash
uv pip install -e ".[cnki]"
playwright install chromium
```

Restart your MCP client to reload the server.

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
| **CNKI: "search is disabled"** | Complete the [CNKI Setup](#cnki-setup-optional) steps |
| **CNKI: captcha** | Solve the slider in the Chrome window, then retry the search |

---

## Architecture

```
research_core/          # Shared library — Zotero client, RAG pipeline, search adapters, tools
project_a_mcp/          # MCP server entry point (stdio transport)
project_b_agent/        # Full-stack agent scaffold (planned)
scripts/                # CLI utilities (index_library.py, etc.)
tests/                  # Unit + integration tests
docs/                   # Detailed setup guides
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

See [DEVELOPMENT.md](./DEVELOPMENT.md) for the roadmap and contribution guidelines.

---

## Comparison with [zotero-mcp](https://github.com/54yyyu/zotero-mcp)

| | **This project** | **zotero-mcp** |
|--|------------------|----------------|
| Install | `git clone` + editable install | `pip install` / `uv tool install` |
| Embedding | `bge-m3` (multilingual, built-in) | Optional; default English model |
| Tool design | 22 intent-based tools, no overlap | Broader tool surface |
| Online search | OpenAlex + CrossRef + S2 + CNKI | — |
| CNKI support | Full (search, detail, export to Zotero) | — |
| Index | ChromaDB + incremental version sync | ChromaDB + scheduled updates |
| Multi-client | Cursor, Claude, Cherry Studio, Trae, Codex | Cursor, Claude |
| Deployment | Local per-user (stdio MCP) | Local + optional HTTP/SSE |

---

## License

MIT
