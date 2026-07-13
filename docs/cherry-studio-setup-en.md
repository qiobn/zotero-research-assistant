# Zotero Research Assistant — Cherry Studio Setup Guide

**English** | **[中文](./cherry-studio-setup.md)**

This guide helps you connect your Zotero library to Cherry Studio so you can
search, read, and manage papers using natural language. No coding required.

**Time needed:** 10–15 minutes. Configure once, use forever.

---

## What You'll Get

After setup, chat with your Zotero library in Cherry Studio:

- "Find papers about urban green space and public health"
- "What does this paper say about research methods?"
- "Summarize these 5 papers into a literature review"
- "Which papers can I cite for this paragraph I'm writing?"
- "Add this DOI to my library: 10.1016/j.cities.2025.105902"
- "What should I read next based on my recent activity?"
- "Check if my system is healthy"

---

## Step 1: Install Python

### Check if already installed

Open a terminal (Windows: `cmd`, macOS: `Terminal`) and run:

```
python --version
```

If it shows **Python 3.11 or higher** (e.g. 3.12, 3.13), skip to Step 2.

### Install if needed

**Windows:**
1. Download from https://www.python.org/downloads/
2. Run the installer. **Check "Add python.exe to PATH"** on the first page.
3. Click "Install Now". Restart your terminal afterwards.

**macOS:**
```
brew install python
```

---

## Step 2: Install zra-mcp

Open a terminal and run:

```
pip install zra-mcp
```

> If `pip` is not found, try `pip3 install zra-mcp` (common on macOS).
> On first run, a ~347 MB AI model downloads automatically. This is a one-time download.

### Verify installation

```
pip show zra-mcp
```

If it shows package information, you're good.

### For China users: set a mirror

The model is hosted on HuggingFace, which may be slow in China. Set a mirror first:

**macOS / Linux:**
```
export HF_ENDPOINT=https://hf-mirror.com
```

**Windows (cmd):**
```
set HF_ENDPOINT=https://hf-mirror.com
```

Then run `pip install zra-mcp`. You'll add this to Cherry Studio's MCP env vars in Step 4 so it persists.

---

## Step 3: Configure Zotero

### 3.1 Enable Zotero Local API

1. Open **Zotero desktop** (version 7 or later)
2. Go to **Edit → Settings → Advanced** (macOS: Zotero → Preferences → Advanced)
3. Check **"Allow other applications on this computer to communicate with Zotero"**
4. Verify: open http://localhost:23119/api/ in your browser. You should see JSON text.

### 3.2 Get a Zotero API Key (optional, for write access)

Skip this if you only need to search and read papers.

To **add papers, write notes, or manage tags** via AI:
1. Go to https://www.zotero.org/settings/keys and log in
2. Click "Create new private key", check "Allow write access"
3. Copy the generated key (a long string of letters and numbers)
4. Note the number next to "userID" at the top of the page (e.g. 12345678)

---

## Step 4: Connect Cherry Studio

### 4.1 Install Cherry Studio

Download from https://cherry-ai.com/ and install.

### 4.2 Configure an AI model

Go to **Cherry Studio → Settings → Model Services**, add a model and its API key.

Recommended models:
- **DeepSeek-V3** — best value, excellent Chinese
- **Claude Sonnet** — most reliable tool calling
- **GPT-4o** — solid all-rounder

### 4.3 Add the MCP server

Open **Cherry Studio → Settings → MCP Servers**, click **Add MCP Server**:

| Field | Value |
|-------|-------|
| Name | `zra-mcp` |
| Description | `Zotero Research Assistant` |
| Command | `zra-mcp` |
| Args | *(leave empty)* |
| Env | `ZOTERO_LOCAL` = `true` |

> **Read-only (search + read)?** The one env var above is all you need.
>
> **Need write access (add papers, notes, tags)?** Add two more env vars:
> `ZOTERO_LIBRARY_ID` = `your userID number`
> `ZOTERO_API_KEY` = `your key`
>
> **In China?** Add one more for faster model downloads:
> `HF_ENDPOINT` = `https://hf-mirror.com`

Click **Save**. No files to create — everything lives in this config.

> **Source install?** Change "Command" to the full Python path (e.g. `D:\project\.venv\Scripts\python.exe`), and "Args" to `-m`, `project_a_mcp.server`.

---

#### If Cherry Studio asks for JSON format

```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "description": "Zotero Research Assistant",
      "baseUrl": "",
      "command": "zra-mcp",
      "args": [],
      "env": {
        "ZOTERO_LOCAL": "true",
        "HF_ENDPOINT": "https://hf-mirror.com"
      },
      "isActive": true
    }
  }
}
```

> For write access, add `"ZOTERO_LIBRARY_ID": "12345678"` and `"ZOTERO_API_KEY": "your-key"` to `env`.
> Source install: replace `"command": "zra-mcp"` with Python path, `"args": []` with `"args": ["-m", "project_a_mcp.server"]`.

### 4.4 Verify it works

1. Make sure **Zotero desktop is running**
2. Open Cherry Studio and start a new conversation
3. Type: **"List all my Zotero collections"**
4. If the AI responds with your collection names — done!

**Not working?**
- Is Zotero running? (The Zotero window must be open, not minimized to tray)
- Restart Cherry Studio after adding the MCP server
- Check the MCP server env vars are set correctly (ZOTERO_LOCAL=true at minimum)
- Run `zra-mcp` directly in a terminal to see error messages

---

## Daily Use

1. Start Zotero desktop
2. Open Cherry Studio and chat normally

The MCP server auto-starts. The index updates automatically. No terminal needed.

> **First use:** Building the initial index takes time — ~3-10 minutes per 100 papers.
> It runs in the background. Search results may be incomplete until it finishes.

---

## Usage Tips

### Search
| You say | What happens |
|---------|-------------|
| "Find papers about gravity model" | Searches your library by topic |
| "Show papers from 2020-2024 tagged methodology" | Filters by year and tag |
| "Find papers similar to this one" | Semantic similarity search |
| "Search online for urban planning papers" | Searches OpenAlex + CrossRef + Semantic Scholar |

### Read
| You say | What happens |
|---------|-------------|
| "What is this paper about?" | Returns metadata + abstract |
| "What does the Methods section say?" | Searches within the paper's PDF |
| "Show me the full paper" | Returns complete text (up to 50 pages) |
| "Find my highlights about GIS" | Searches your annotations across all papers |

### Write & Organize
| You say | What happens |
|---------|-------------|
| "Add this DOI to my library" | Imports paper metadata + downloads PDF |
| "Cite papers for this paragraph I wrote" | Suggests matching citations |
| "Export these as BibTeX" | Formats bibliography entries |
| "Tag these papers as core-reading" | Applies tags (preview first) |
| "Summarize these 5 papers into a review" | Generates literature review materials |
| "Recommend what to read next" | Personalized recommendations |

---

## FAQ

**Q: Search isn't finding a paper I just added?**
Tell the AI "sync my index". New PDFs need to be indexed first.

**Q: Can't add papers or write notes?**
You need a Zotero API key — see Step 3.2, then add the env vars to MCP config. Without them, you can still search and read.

**Q: "Connection refused" error?**
Zotero desktop must be running. Check Step 3.1.

**Q: Model download is very slow?**
Set the HF mirror — see Step 2. Or copy the model folder from someone who already has it:
```
~/.cache/huggingface/hub/models--skatzR--USER-BGE-M3-ONNX-INT8/
```

**Q: How to check if everything works?**
Ask the AI: "Check system health."

**Q: How to update?**
```
pip install --upgrade zra-mcp
```
Then restart Cherry Studio.
