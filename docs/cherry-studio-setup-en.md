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

Then run `pip install zra-mcp`. You'll also add this to your config file in Step 3 so it persists.

---

## Step 3: Configure Zotero

### 3.1 Enable Zotero Local API

1. Open **Zotero desktop** (version 7 or later)
2. Go to **Edit → Settings → Advanced** (macOS: Zotero → Preferences → Advanced)
3. Check **"Allow other applications on this computer to communicate with Zotero"**
4. Verify: open http://localhost:23119/api/ in your browser. You should see JSON text.

### 3.2 Create a config file (.env)

Pick a folder for your config — for example `D:\zotero-ai` (Windows) or `~/zotero-ai` (macOS).

Create a file named **`.env`** in that folder with this content:

```
ZOTERO_LOCAL=true
HF_ENDPOINT=https://hf-mirror.com
```

> If you also want to **add papers, write notes, or manage tags** via AI, you need a Zotero API key. Get one at https://www.zotero.org/settings/keys, then add these lines:
> ```
> ZOTERO_LIBRARY_ID=12345678
> ZOTERO_API_KEY=your-key-here
> ```
> (Replace `12345678` with the number next to "userID" on that page.)

**How to create a .env file:**
- **Windows:** Right-click → New → Text Document. Rename to `.env` (remove `.txt`). Edit with Notepad.
- **macOS:** In Terminal: `cd ~/zotero-ai && echo "ZOTERO_LOCAL=true" > .env`

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

Open **Cherry Studio → Settings → MCP Servers**, click **Add**, and fill in:

| Field | Value |
|-------|-------|
| Name | `zra-mcp` |
| Description | `Zotero Research Assistant` |
| Command | `zra-mcp` |
| Args | *(leave empty)* |
| Env | *(leave empty — set in .env file)* |

Click **Save**.

> **Source install?** If you installed from source instead of pip, use these values instead:
> - **Command:** Full path to Python (e.g. `D:\project\.venv\Scripts\python.exe` or `/home/user/project/.venv/bin/python`)
> - **Args:** `-m`, `project_a_mcp.server`

> **JSON mode?** If Cherry Studio asks for JSON, paste:
> ```json
> {
>   "mcpServers": {
>     "zra-mcp": {
>       "name": "zra-mcp",
>       "description": "Zotero Research Assistant",
>       "baseUrl": "",
>       "command": "zra-mcp",
>       "args": [],
>       "env": {},
>       "isActive": true
>     }
>   }
> }
> ```
> Source install: replace `"command": "zra-mcp"` with `"command": "/path/to/python"` and add `"args": ["-m", "project_a_mcp.server"]`.

### 4.4 Verify it works

1. Make sure **Zotero desktop is running**
2. Open Cherry Studio and start a new conversation
3. Type: **"List all my Zotero collections"**
4. If the AI responds with your collection names — done!

**Not working?**
- Is Zotero running? (Check the taskbar — the Zotero window must be open)
- Restart Cherry Studio after adding the MCP server
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
You need a Zotero API key — see Step 3.2. Without it, you can still search and read.

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
