# Zotero Research Assistant -- Cherry Studio Setup Guide

**English** | **[中文](./cherry-studio-setup.md)**

---

This guide is for users with no coding experience. Follow along step by step — it takes about 10-15 minutes.
Once configured, no maintenance is needed. Just open Cherry Studio and start chatting.


========================================
What You'll Get
========================================

After setup, you can interact with your Zotero library using natural language in Cherry Studio:

  - "Find papers about urban public service accessibility"
  - "What does this paper say about research methods?"
  - "Which papers from my library can I cite for this paragraph?"
  - "Add this DOI to my library"
  - "Summarize these papers into a literature review"
  - "My argument is X — find supporting/opposing evidence from my library"
  - "What should I read next?"
  - "Suggest tags for these papers"
  - "Is the system healthy? Check connection and index"


========================================
Overview
========================================

Step 1  Install Python                        ~3 min
Step 2  Install this project                  ~2 min
Step 3  Configure Zotero connection           ~2 min
Step 4  Connect Cherry Studio                 ~3 min
Step 5  Start using


========================================
Step 1: Install Python (3.11 or higher)
========================================

Check if already installed:

Windows (press Win+R, type cmd, press Enter):
    python --version

    If it says "not recognized", try:
    python3 --version
    or
    py --version
    If none work, Python is not installed.

macOS (open Terminal from Launchpad):
    python3 --version

If it shows Python 3.11.x or higher (e.g. 3.12, 3.13), skip to Step 2. Otherwise install:

  Windows:
    1. Visit https://www.python.org/downloads/
    2. Download the latest version
    3. Run the installer
       !! IMPORTANT: Check "Add python.exe to PATH" at the bottom of the first page !!
    4. Click "Install Now"
    5. Close and reopen cmd, verify with: python --version

  macOS:
    1. If you have Homebrew: run brew install python
    2. If not: install Homebrew first (you'll be asked for your password — it won't show while typing):
       /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    3. Then: brew install python


========================================
Step 2: Install the Project
========================================

Run in your terminal/cmd:

    pip install zra-mcp

Notes:
  - If pip is not found, try: pip3 install zra-mcp
  - macOS users typically need pip3 instead of pip
  - If you have multiple Python versions, pip3 ensures the correct one

For optional CNKI (Chinese academic database) support:

    pip install "zra-mcp[cnki]"
    (or: pip3 install "zra-mcp[cnki]")

Verify installation:

    zra-mcp --help

If it shows help text or no error, installation is successful.

On first run, the embedding model (~2.3 GB) will be downloaded automatically.
If the download is slow, you can set a mirror:
    macOS:   export HF_ENDPOINT=https://hf-mirror.com
    Windows: set HF_ENDPOINT=https://hf-mirror.com
    Then re-run zra-mcp.

Alternatively, copy the model folder from someone who already has it:
    macOS:   ~/.cache/huggingface/hub/models--BAAI--bge-m3/
    Windows: C:\Users\YourUsername\.cache\huggingface\hub\models--BAAI--bge-m3\


========================================
Step 3: Configure Zotero Connection
========================================

-------- 3.1 Enable Zotero Local API --------

1. Open Zotero desktop (requires Zotero 7 or later)
2. Menu → Edit → Preferences (macOS: Zotero → Settings) → Advanced
3. Check "Allow other applications on this computer to communicate with Zotero"
4. Verify: open http://localhost:23119/api/ in your browser
   - If you see JSON text → success
   - If "connection refused" → make sure Zotero is running and step 3 is checked


-------- 3.2 Get Zotero Web API Key (optional, for write operations) --------

Skip this if you only need to search and read papers.
Required if you want to add papers, write notes, or manage tags via AI.

1. Go to https://www.zotero.org/settings/keys
2. Log in to your Zotero account
3. Click "Create new private key"
4. Key Description: anything (e.g. "research-assistant")
5. Check: Allow library access → Allow write access
6. Click Save Key, copy the generated key (a long alphanumeric string)
7. Library ID: the number next to "userID" at the top of the same page (e.g. 12345678)


-------- 3.3 Create Configuration File --------

For pip users, create a .env file in a working directory of your choice.

Pick a directory (no spaces or non-ASCII characters in the path), for example:
  Windows: D:\research-tools\  or  E:\zotero-ai\
  macOS:   ~/research-tools/

Create a plain text file named .env in that directory with the following content:

    ZOTERO_LOCAL=true
    ZOTERO_LIBRARY_ID=12345678
    ZOTERO_API_KEY=aB3xYz9kLmN...

Explanation:
  - ZOTERO_LOCAL=true: required, tells the service to use local Zotero
  - ZOTERO_LIBRARY_ID: the number from step 3.2.7
  - ZOTERO_API_KEY: the key from step 3.2.6. Leave empty if you skipped 3.2
  - For read-only usage (search and read only), just ZOTERO_LOCAL=true is enough

Windows — how to create a .env file:
  1. Open your chosen directory in File Explorer
  2. Right-click → New → Text Document
  3. Rename it to .env (remove the .txt extension)
     If you can't see extensions: View → check "File name extensions"
  4. Right-click → Open with → Notepad, type the content above, save

macOS — create .env file:
  In Terminal (replace the path with your working directory):
    cd ~/research-tools
    echo "ZOTERO_LOCAL=true" > .env

Save and close. Remember the directory where your .env file is — you'll need it for Cherry Studio.


========================================
Step 4: Connect Cherry Studio
========================================

-------- 4.1 Install Cherry Studio --------

Download from https://cherry-ai.com/ and install for your system.


-------- 4.2 Configure LLM (Large Language Model) --------

Cherry Studio needs an AI model to power conversations.
Go to Cherry Studio → Settings → Model Services and configure your model + API Key.

Recommended models:
  DeepSeek-V3     Best value, great for Chinese   https://platform.deepseek.com/
  Qwen2.5-72B    Strongest Chinese model          https://dashscope.aliyun.com/
  Claude Sonnet   Best tool calling accuracy       https://console.anthropic.com/
  GPT-4o          All-around stable                https://platform.openai.com/


-------- 4.3 Add MCP Server --------

1. Open Cherry Studio → Settings → MCP Servers
2. Click "Add", switch to JSON mode
3. Paste the following JSON:

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

That's it! After pip install, the zra-mcp command is globally available — no path configuration needed.

If your .env file is not in the default search path (i.e. zra-mcp can't connect to Zotero),
add a cwd pointing to the directory containing your .env:

macOS example:

    {
      "mcpServers": {
        "zra-mcp": {
          "name": "zra-mcp",
          "type": "stdio",
          "isActive": true,
          "command": "zra-mcp",
          "cwd": "/Users/yourname/research-tools"
        }
      }
    }

Windows example:

    {
      "mcpServers": {
        "zra-mcp": {
          "name": "zra-mcp",
          "type": "stdio",
          "isActive": true,
          "command": "zra-mcp",
          "cwd": "D:\\research-tools"
        }
      }
    }

Note: Windows paths in JSON use double backslashes (\\).

4. Save the configuration


-------- 4.4 Verify Connection --------

Make sure Zotero desktop is running, then start a new chat in Cherry Studio and type:

    List all collections in my Zotero library

If the AI returns your collection list, congratulations — setup is complete!

Troubleshooting:
  1. Is Zotero running?
  2. Is your .env file in the right place? Does cwd point to it?
  3. Did you restart Cherry Studio after changing MCP config?
  4. Try running zra-mcp directly in terminal/cmd to see any error messages
  5. If zra-mcp command is not found, close and reopen your terminal (pip needs a terminal restart)


========================================
Daily Usage
========================================

-------- Before Each Session --------

1. Open Zotero desktop (keep it running)
2. Open Cherry Studio, chat normally

No need to open a terminal, no manual service startup, no manual index sync.
Cherry Studio automatically starts the MCP service, which auto-syncs on startup.

[IMPORTANT: be patient on the very first run while the index builds]
The first time you start, the system parses every PDF in your library and
computes semantic vectors. This takes a while — the more papers, the longer
(hundreds of papers can take tens of minutes or more, depending on your
machine). The good news: it runs automatically in the background, so you can:
  - leave it running and use your computer for other things meanwhile;
  - expect semantic search to be incomplete until it finishes (this is normal);
  - check progress by telling the AI "inspect my index" or "is everything OK".
Only the first build (or when your library changes) needs waiting; later
startups are fast incremental syncs.


[About tables & figures: the default, and whether to chase "precise tables"]
Bottom line first: the default is good enough for almost everyone — no change
needed.

Why it's built this way — the current state of the art:
  - Reconstructing a PDF table into exact rows/columns has no fast, accurate
    text-only solution for academic papers (especially Chinese "three-line" and
    borderless tables). It is fundamentally a vision (look-at-the-image) problem.
  - Guessing from geometry/ruled lines works badly: in practice it mis-detects
    multi-column prose and reference lists as huge fake tables, polluting search.
    So this project deliberately does NOT do that pseudo-structuring.

Default behavior (works out of the box, fast to build):
  - Tables: we record where it is, its caption, and the raw block beneath the
    caption (its values stay searchable) — but not as clean rows/columns.
  - Figures: we record where it is and roughly what the caption says (no image
    recognition).
  - Prose like "as shown in Table 3 / Figure 2" auto-links to that table/figure.
  - Upside: fast indexing. Cost: tables aren't clean structured data.

If you genuinely need precise table structure (e.g. you want the AI to read
exact per-row/per-column values):
  - Preprocess your PDFs with a dedicated visual document parser into
    Markdown/HTML with tables, then index that as a note/attachment. Options:
    MinerU (best for academic/CJK/complex tables), Docling, Marker.
  - The tradeoff: these use vision models — accurate, but heavier and slower, so
    first-build time grows substantially (tens of minutes can become hours,
    depending on library size and machine) and they need extra dependencies.
  - In one line: more complete info / more accurate table structure costs you
    initial build speed. Whether it's worth it depends on how much your research
    relies on table data — your call.


-------- Usage Scenarios --------

[Search Literature]

    Find papers about "15-minute city"
    Find papers on walkability from 2020 onwards
    Search papers tagged "methodology"
    Find papers similar to this one
    Search online for latest research on urban green infrastructure

[Read Papers]

    What is this paper about?
    What does this paper say about "research methods"?
    Show me the table of contents for this paper
    Find all my annotations about "GIS" across papers
    What do the figures and tables in this paper show?

[Literature Review & Arguments]

    Synthesize these papers into a review about "methodology evolution"
    My argument is "public services are unequally distributed" — find evidence
    Extract and compare "data sources" from these 5 papers

[Find Citations for Writing]

    I'm writing: "Walkability is a key indicator of urban quality of life..."
    Find appropriate citations from my library.
    Export BibTeX for the top results.

[Add Papers]

    Add this paper: 10.1016/j.cities.2025.105902
    Add this arXiv paper: 2301.00001

[Library Management]

    Tag these papers as "core reading"
    Create a collection called "thesis references"
    Check for duplicate papers
    Write a reading note for this paper

[Reading Status & Recommendations]

    Which papers have I read? Which haven't I?
    Based on my recent reading, what should I read next?
    Suggest tags for these papers

[System Diagnostics]

    Is the system healthy?
    How is my index quality?
    Can this paper be properly retrieved?


-------- Tips --------

1. Search first, then read: find a paper first, then ask specific questions
2. Citation workflow: paste your paragraph → pick citations → export BibTeX
3. Write operations are safe: add/note/tag operations default to preview mode,
   AI will ask for confirmation before executing
4. Multilingual search: search English papers with Chinese queries and vice versa
5. No manual sync needed: say "sync my index" if you just added papers
6. Literature reviews: select papers, AI synthesizes by theme (not paper-by-paper)
7. Argument search: give your thesis to AI, it categorizes evidence as supporting/opposing
8. Diagnostics: say "check system health" for automatic diagnosis and fix suggestions


========================================
Available Tools (36)
========================================

Search & Discovery
  search_papers           Search local library (keyword + semantic hybrid)
  search_online_literature Online literature search (OpenAlex+CrossRef+S2)
  search_cnki_literature  CNKI Chinese literature search (optional)
  find_related_literature Find related papers (corpus-first + multi-strategy)
  expand_citation_network Citation network expansion (forward/backward)
  find_similar_papers     Find semantically similar papers in library
  browse_library          Browse collections, tags, recent additions
  find_duplicates         Detect duplicate papers
  merge_duplicates        Merge duplicates
  cnki_paper_detail       CNKI paper details
  cnki_navigate_pages     CNKI results pagination

Reading & Annotation
  get_paper               Paper metadata and abstract
  get_paper_content       Read paper content (semantic/page/full/outline)
  search_annotations      Search annotations across papers
  create_annotation       Create PDF highlight annotations

Writing Support
  suggest_citations       Match your draft text to library evidence
  export_bibliography     Export BibTeX or formatted citations
  add_paper               Add via DOI/arXiv/ISBN/URL
  cnki_add_to_zotero      Import directly from CNKI

Library Management
  add_note                Add reading notes
  edit_tags               Batch tag management
  manage_collections      Create and manage collections

Smart Analysis
  reading_status          Reading progress analysis (deep-read/skimmed/unread)
  recommend_papers        Personalized paper recommendations
  generate_review_note    Multi-paper literature review generation
  generate_reading_note   Single-paper structured reading note
  suggest_tags            Smart tag suggestions (methodology/field/data-type)
  find_arguments          Argument finder (supporting/opposing evidence)

System Maintenance & Diagnostics
  sync_index              Sync vector index (usually runs automatically)
  check_health            System health check (connection, index, model, config)
  inspect_index           Index quality inspection (chunk stats, section distribution)
  test_recall             Recall test (verify paper retrieval in top-20)


========================================
FAQ
========================================

Q: Can't find a paper I just added?
A: Tell AI "sync my index". Normally the MCP service auto-syncs on startup.

Q: Error when adding papers / writing notes?
A: You need to configure ZOTERO_API_KEY in .env (step 3.2).
   Without an API Key, only search and read functions are available.

Q: "Connection refused" error during indexing?
A: Make sure Zotero desktop is running with local API enabled (step 3.1).

Q: pip command not found?
A: Try: pip3 install zra-mcp
   macOS users typically need pip3.
   Windows users: if both pip and pip3 don't work, reinstall Python and
   check "Add python.exe to PATH".

Q: Slow download during installation?
A: The embedding model (bge-m3) is ~2.3 GB. Two solutions:
   1. Set mirror:
      macOS:   export HF_ENDPOINT=https://hf-mirror.com
      Windows: set HF_ENDPOINT=https://hf-mirror.com
      Then re-run zra-mcp
   2. Copy the model folder from someone who has it (see Step 2)

Q: AI in Cherry Studio isn't calling tools?
A: Check:
   1. Is MCP tool calling enabled in chat settings?
   2. Does your model support Function Calling? (All recommended models do)
   3. Is the MCP server status showing "Connected"?

Q: How to upgrade?
A: Run: pip install --upgrade zra-mcp
   (or: pip3 install --upgrade zra-mcp)
   Then restart your Cherry Studio conversation.

Q: Moving to a new computer?
A: Redo steps 1-4 on the new machine (~10 min).
   Your Zotero library syncs via your Zotero account; index regenerates automatically.

Q: Poor search results / system issues?
A: Tell AI "check system health" — it will diagnose connection, index, and config
   issues and suggest fixes. Also try "check my index quality" for detailed stats.


========================================
About CNKI (Chinese Academic Database)
========================================

The CNKI module is disabled by default and does not affect other features.

-------- Why Disabled by Default --------

  - CNKI has no public API; this module uses browser automation (less stable)
  - Requires additional Playwright dependencies
  - Requires you to manually log in to CNKI and keep Chrome running
  - CNKI may change its page structure at any time, breaking the module
  - Improper use may trigger CNKI's anti-scraping mechanisms

-------- If You Need Chinese Literature --------

In most cases, online search (OpenAlex/CrossRef/S2) covers major Chinese and English
academic papers. If you specifically need CNKI Chinese journal papers, ask AI:

    "How do I enable the CNKI module?"

AI will guide you through the full setup for your OS.

-------- Risk Notes --------

  - Browser automation is inherently fragile
  - CNKI page structure changes may temporarily break functionality
  - Frequent calls may trigger CAPTCHA or temporary blocks
  - Enable only when needed; disable with CNKI_ENABLED=false in .env


========================================
Appendix A: Install from Source (Developers)
========================================

If you're a developer or need to customize the code:

    git clone https://github.com/qiobn/zotero-research-assistant.git
    cd zotero-research-assistant
    uv venv .venv --python 3.13
    uv pip install -e .

Don't have uv? Install it first:
    macOS:   curl -LsSf https://astral.sh/uv/install.sh | sh
    Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

Source install users need to specify the full Python path in Cherry Studio MCP config:

macOS:

    {
      "mcpServers": {
        "zra-mcp": {
          "name": "zra-mcp",
          "type": "stdio",
          "isActive": true,
          "command": "/your/project/path/zotero-research-assistant/.venv/bin/python",
          "args": ["-m", "project_a_mcp.server"],
          "cwd": "/your/project/path/zotero-research-assistant"
        }
      }
    }

Windows:

    {
      "mcpServers": {
        "zra-mcp": {
          "name": "zra-mcp",
          "type": "stdio",
          "isActive": true,
          "command": "D:\\your\\project\\path\\zotero-research-assistant\\.venv\\Scripts\\python.exe",
          "args": ["-m", "project_a_mcp.server"],
          "cwd": "D:\\your\\project\\path\\zotero-research-assistant"
        }
      }
    }

Get the full path (run inside the project directory):
  macOS:   echo "$(pwd)/.venv/bin/python"
  Windows: echo %cd%\.venv\Scripts\python.exe


========================================
Appendix B: Network Notes (China Mainland)
========================================

If you're in mainland China, these tips may help:

1. pip install (installing this project)
   - PyPI is usually accessible from China
   - If slow, use Tsinghua mirror: pip install -i https://pypi.tuna.tsinghua.edu.cn/simple zra-mcp

2. Downloading the embedding model (~2.3 GB, most likely to get stuck)
   - HuggingFace is often inaccessible from China
   - Solutions:
     a. Set mirror (recommended):
        macOS:   export HF_ENDPOINT=https://hf-mirror.com
        Windows: set HF_ENDPOINT=https://hf-mirror.com
        Then run zra-mcp again
     b. Copy the model folder from someone who has it (see Step 2)

3. Online search during daily use
   - OpenAlex, CrossRef, Semantic Scholar APIs are usually accessible from China
   - LLM APIs: DeepSeek and Qwen don't need a proxy; OpenAI and Claude do

Summary: pip install usually works fine. The main step is setting the HF_ENDPOINT
mirror when downloading the embedding model.
