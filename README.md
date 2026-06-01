# Zotero Research Assistant

Connect your [Zotero](https://www.zotero.org/) library to AI assistants ([Cherry Studio](https://cherry-ai.com/), [Claude Desktop](https://claude.ai/download), [Cursor](https://www.cursor.com/), etc.) via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Search papers by meaning, read PDF passages with page numbers, suggest citations for your drafts, add papers by DOI/URL, and manage tags and collections — all through natural language.

**16 MCP tools**, one intent each, designed so LLMs always pick the right tool.

---

## Features

- **Hybrid search** — Zotero keyword search + ChromaDB semantic search, merged with Reciprocal Rank Fusion; fallback to Zotero full-text index
- **Filter-only search** — list papers by year, tags, or collection with an empty query (e.g. all papers from 2024+)
- **Cross-encoder reranking** — optional `ms-marco-MiniLM-L-6-v2` for higher precision
- **Chinese + English** — `BAAI/bge-m3` embedding (1024-dim, 100+ languages)
- **Page-level traceability** — retrieved passages include exact PDF page numbers
- **Full-text & outline** — read complete paper text or PDF table of contents
- **Incremental index sync** — version-based diff; auto-sync on MCP startup
- **Add papers** — DOI, arXiv, ISBN, BibTeX, or publisher URL (ScienceDirect, Springer, Wiley, …)
- **Open-access PDF waterfall** — arXiv → Unpaywall → Semantic Scholar → PMC
- **Duplicate merge** — find by DOI/title, merge with dry-run preview
- **Annotations** — search highlights across the library; create highlights on PDFs
- **Write safety** — all write/delete operations preview first; **requires explicit user approval** before executing
- **Hybrid Zotero mode** — fast local reads + web API writes (when API key is set)

---

## Requirements

| Component | Version / note |
|-----------|----------------|
| **Python** | 3.11 – 3.13 |
| **Zotero** | 7+ desktop app, running with local API enabled |
| **MCP client** | Cherry Studio, Claude Desktop, Cursor, etc. |
| **LLM** | Any model with tool/function calling (DeepSeek, GPT-4o, Claude, Qwen, …) |
| **Disk** | ~2.5 GB for embedding model (`bge-m3`) on first run |
| **Git** | To clone this repository |

> **Path tip:** Install the project in a short path **without spaces or non-ASCII characters**, e.g. `~/zotero-research-agent` (macOS) or `C:\Users\you\zotero-research-agent` (Windows).

---

## Quick Start

### 1. Clone the repository

**macOS / Linux:**
```bash
cd ~
git clone https://github.com/qiobn/zotero-research-agent.git
cd zotero-research-agent
```

**Windows (cmd):**
```cmd
cd %USERPROFILE%
git clone https://github.com/qiobn/zotero-research-agent.git
cd zotero-research-agent
```

### 2. Install dependencies

Install [uv](https://github.com/astral-sh/uv) if needed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux
```

Then create a virtual environment and install:
```bash
uv venv .venv --python 3.13    # use 3.12 or 3.11 if 3.13 is unavailable
uv pip install -e .
```

Verify:
```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -c "from project_a_mcp.server import mcp; print('OK')"
```

> First run downloads the embedding model (~2.3 GB). If download is slow, set `export HF_ENDPOINT=https://hf-mirror.com` (macOS/Linux) or `set HF_ENDPOINT=https://hf-mirror.com` (Windows) and retry.

### 3. Configure Zotero

**Enable local API** (required):

1. Open Zotero → **Edit → Settings → Advanced**
2. Check **"Allow other applications on this computer to communicate with Zotero"**
3. Confirm in browser: http://localhost:23119/api/ shows JSON

**Create `.env`:**
```bash
cp .env.example .env
```

Minimum for **read-only** (search, read, cite):
```ini
ZOTERO_LOCAL=true
```

For **write operations** (add papers, notes, tags, collections, annotations), also set your [Zotero API key](https://www.zotero.org/settings/keys) (enable library + write access):
```ini
ZOTERO_LOCAL=true
ZOTERO_LIBRARY_ID=12345678
ZOTERO_API_KEY=your_api_key_here
```

### 4. Build the vector index (first time)

Zotero must be running:
```bash
source .venv/bin/activate
python scripts/index_library.py
```

This parses PDFs in your library and stores embeddings in `.chroma_db/` (local only, not in git).  
Typical time: ~3–5 min for 100 papers, ~10–15 min for 500 papers.

After the first run, the MCP server **auto-syncs incrementally** on startup (`ZRA_AUTO_SYNC=true` by default).

### 5. Connect an MCP client

Replace `YOU` with your OS username and adjust the project path if you cloned elsewhere.

#### Cherry Studio (recommended)

**Settings → MCP Servers → Add → JSON mode:**

**macOS:**
```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "/Users/YOU/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/YOU/zotero-research-agent"
    }
  }
}
```

**Windows:**
```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "C:\\Users\\YOU\\zotero-research-agent\\.venv\\Scripts\\python.exe",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "C:\\Users\\YOU\\zotero-research-agent"
    }
  }
}
```

**Quick path check** (run inside the project folder):
```bash
# macOS
echo "$(pwd)/.venv/bin/python"
# Windows
echo %cd%\.venv\Scripts\python.exe
```

Also configure an LLM under **Settings → Model Services** (e.g. DeepSeek, GPT-4o, Claude, Qwen).

#### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/YOU/zotero-research-agent/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/YOU/zotero-research-agent"
    }
  }
}
```

#### Cursor

**Settings → MCP → Add server** with the same `command`, `args`, and `cwd` as above.

### 6. Test the connection

1. Start **Zotero desktop**
2. Open a **new chat** in your MCP client
3. Ask: *"List all collections in my Zotero library"*

If you see your collections, setup is complete.

---

## Example prompts

```
Find papers about 15-minute cities published after 2020
List all papers in my library from 2024 onwards
What does this paper say about the research methodology?
Find papers similar to [paper title]
I'm writing: "Walkability is a key indicator of urban quality..." — suggest citations from my library
Export BibTeX for the top 3 results
Add this paper: 10.1016/j.cities.2025.105902
Add this URL: https://www.sciencedirect.com/science/article/pii/...
Tag these papers as "core reading"
Sync my index — I just added new PDFs
```

**Write operations** (add paper, notes, tags, merge duplicates, create annotations) always **preview first**. The assistant should ask you to confirm (e.g. "确认" / "yes") before executing.

---

## MCP tools (16)

| Category | Tools |
|----------|-------|
| **Discover** | `search_papers`, `find_similar_papers`, `browse_library`, `find_duplicates`, `merge_duplicates` |
| **Read** | `get_paper`, `get_paper_content`, `search_annotations`, `create_annotation` |
| **Write** | `suggest_citations`, `export_bibliography`, `add_paper` |
| **Manage** | `add_note`, `edit_tags`, `manage_collections` |
| **Admin** | `sync_index` |

<details>
<summary>Tool details</summary>

### Discover
- **`search_papers`** — Primary search. Hybrid keyword + semantic. Use `query=""` with `year_from` / tags for filter-only listing.
- **`find_similar_papers`** — Similar papers to one known item (by `item_key`).
- **`browse_library`** — Collections, tags, recent items, items in a collection.
- **`find_duplicates`** / **`merge_duplicates`** — Detect and merge duplicates (dry-run by default).

### Read
- **`get_paper`** — Metadata + abstract.
- **`get_paper_content`** — Modes: semantic query, page, fulltext, outline; optional annotations.
- **`search_annotations`** — Search highlights/comments across all papers.
- **`create_annotation`** — Highlight text on a PDF (dry-run by default).

### Write & manage
- **`suggest_citations`** — Match your draft text to library evidence.
- **`export_bibliography`** — BibTeX or plain citations.
- **`add_paper`** — Import by DOI / arXiv / ISBN / BibTeX / URL (dry-run by default).
- **`add_note`**, **`edit_tags`**, **`manage_collections`** — Library organization (dry-run by default).

### Admin
- **`sync_index`** — Incremental vector index sync. Also runs automatically on MCP startup.

</details>

---

## Configuration

Copy [`.env.example`](./.env.example) to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_LOCAL` | `true` | Read from local Zotero API (fast) |
| `ZOTERO_API_KEY` | — | Required for writes (hybrid mode) |
| `ZOTERO_LIBRARY_ID` | `0` | Your Zotero user ID |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Sentence-transformer for semantic search |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker (`none` to disable) |
| `CHROMA_PERSIST_DIR` | `.chroma_db` | Local vector database path |
| `ZRA_AUTO_SYNC` | `true` | Auto incremental sync on MCP startup |

All data stays **on your machine**: Zotero library, `.chroma_db/`, and HuggingFace model cache (`~/.cache/huggingface/`). Each user indexes their own library independently.

---

## Updating

After pulling new releases from GitHub:

```bash
cd ~/zotero-research-agent          # or your clone path
git pull
uv pip install -e .                 # only if dependencies changed
```

Restart your MCP client conversation to reload the server.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **Connection refused / no results** | Ensure Zotero desktop is running and local API is enabled |
| **New papers not found** | Say *"sync my index"* or restart MCP (auto-sync runs on startup) |
| **Write operations fail** | Set `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID` in `.env` |
| **Slow first start** | Embedding model download (~2.3 GB); use HF mirror or copy cached model |
| **Windows: script blocked** | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell |
| **MCP tools not called** | Use a model that supports function calling; enable MCP tools in client settings |
| **AI executes writes without asking** | Add to system prompt: *"Always wait for my explicit confirmation before confirm=true on write tools"* |

---

## Architecture

```
research_core/          # Shared library (Zotero, RAG, tools)
project_a_mcp/          # MCP server (this repo's main entry)
project_b_agent/        # Full-stack agent scaffold (planned)
```

Each tool maps to **one user intent** — discovery tools return `item_key`, read/write tools consume it.

---

## Development

```bash
uv pip install -e ".[dev]"
pytest tests/ -v
ruff check .
```

See [DEVELOPMENT.md](./DEVELOPMENT.md) for the roadmap.

---

## Comparison with [zotero-mcp](https://github.com/54yyyu/zotero-mcp)

| | **This project** | **zotero-mcp** |
|--|------------------|----------------|
| Install | `git clone` + editable install | `pip` / `uv tool install zotero-mcp-server` |
| Embedding | `bge-m3` (multilingual, built-in) | Optional extras; default English model |
| Tool design | 16 intent-based tools, no overlap | Broader tool surface |
| Index | ChromaDB + incremental version sync | ChromaDB + configurable update schedules |
| Deployment | Local per-user (stdio MCP) | Local + optional HTTP/SSE |

---

## License

MIT
