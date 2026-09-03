# Development Log

> A technical diary covering every significant update: what was done, what problem it solved,
> the technical decisions behind it, and directions for future optimization.
>
> For project maintainers and contributors. More detailed and technical than CHANGELOG.
>
> **[中文版](./DEVELOPMENT_LOG.md)**

---

## v0.4.10.dev0 — Consistency and Packaging Baseline (2026-09-03)

Development continues on `feat/lightweight-graphrag`, which is 12 linear commits
ahead of `main`. PyPI 0.4.9 remains the published release; source now identifies
as `0.4.10.dev0`.

- The canonical tool count is **40**: 36 always-on tools and four tools that are
  registered only with `CNKI_ENABLED=true`.
- `search_papers` is explicitly a single-query retrieval engine. The MCP client
  owns query translation, decomposition and multi-call merging; `expand_query`
  exposes only user-defined synonyms and Zotero tags. OPUS-MT remains an optional,
  separate index-time metadata-enrichment step.
- Hatch wheel builds now package `.claude/skills` as `project_a_mcp/skills`. The
  server prefers installed resources and falls back to source-tree skills.
- The default index-time NMT cache is now `{CHROMA_PERSIST_DIR}/hf_cache` on every
  platform, replacing a Windows path literal that was invalid as a POSIX default.

Next validation: build and install the wheel in a clean environment, smoke-test
the MCP skill resources, and add deterministic contract tests for MCP responses.

---

## v0.3.0 — RAG Pipeline Upgrade (2026-07-06)

### Dual-Format Output: JSON + Markdown Context Block (2026-07-10, `706afff`)

**Problem:** All MCP tools returned pure JSON, causing three issues for LLMs:
1. Token waste — JSON syntax overhead (quotes, brackets, keys) inflates token count
2. Attention dilution — LLMs struggle to distinguish evidence text from metadata in flat JSON
3. Unreadable scores — `score: 0.0321` has no intuitive meaning for LLMs

**Solution:** Per [Anthropic MCP Best Practices](https://github.com/anthropics/skills/blob/main/skills/mcp-builder/reference/mcp_best_practices.md), added pre-rendered Markdown `context_block` to core retrieval tools as dual-format output alongside JSON items. Markdown is the LLM consumption channel; JSON is the programmatic channel.

**Design decisions:**
- Blockquote (`>`) for cited evidence — highest LLM attention weight among formats
- `###` numbered headings — tree-structured mental model of result sets
- ★★★/★★/★ instead of raw floats — Cross-Encoder score percentile bucketing (>75th → high, >25th → medium)
- `_snippet()` sentence-boundary truncation — CJK `。！？；` + EN `.!?` bidirectional
- CJK name format detection — Unicode range `"一" <= c <= "鿿"` for surname-first detection

**Token analysis (6 papers, cl100k_base):**
- Old pure JSON: 1,559 tokens
- New JSON+MD: 1,762 tokens (+13% — dual format overhead)
- Context block alone: 931 tokens (40% savings vs old JSON)
- Verdict: accepted ~13% overhead; LLM comprehension gains justify the cost. A future
  `response_format` parameter will let users choose JSON-only or markdown-only.

**Least confident about:**
1. Dual-format redundancy — `items` and `context_block` duplicate info, actually 13% more total tokens
2. No A/B LLM response quality test — inferring Markdown superiority without hard data
3. Percentile-based tiering with <4 results may produce inaccurate tier assignments

**Future work:**
- Add `response_format="json"` / `"markdown"` / `"both"` parameter
- A/B test LLM citation accuracy: JSON vs Markdown
- Extend to `generate_reading_note`, `find_arguments`

---

## v0.3.0 — RAG Pipeline Upgrade (2026-07-06)

### Why This Release

The v0.2.0 RAG pipeline was "it works" quality: PDF extraction → chunk → embed → index → search.
An audit of 20 papers revealed:

- Embedding separation ratio of 1.13x (threshold: 1.3x) — papers not well-distinguished
- Journal boilerplate "Keywords:", "A R T I C L E I N F O" being indexed as semantic content (85%+)
- Zero retrieval observability — no way to know why a paper didn't show up
- No systematic evaluation — can't tell if a change is improvement or regression
- All chunks treated equally — no distinction between coherent paragraphs and text fragments

---

### Phase 0: Baseline Audit (2026-06-30, `f9a5947`)

**New files:**
- `scripts/index_sample.py` — sample-index N papers for rapid testing
- `scripts/audit_index.py` (~800 lines) — 7-phase library quality audit

**Audit baseline (20 papers / 2102 chunks):**

| Metric | Value | Verdict |
|--------|-------|---------|
| Garbled chunks | 0% | Excellent |
| Long chunks (>1500) | 0% | Cap working |
| Short chunks (<50) | 2.8% | Acceptable |
| Figure/Table chunks | 16.9% | Extraction working |
| Embedding separation | 1.13x | WEAK (≥1.3x needed) |
| Noise patterns | "Keywords:", "A R T I C L E I N F O" | 85%+ of papers |
| Avg chunks/paper | 105.1 | Too fine-grained |
| Health score | 65/100 (B) | Needs improvement |

**Problem solved:** Zero visibility into index quality → quantified baseline
**Decision:** Audit before code — know the problem before coding

---

### Phase 1.1: Text Cleaning Engine (2026-07-01, `f9a5947`)

**New:** `research_core/parsers/text_cleaner.py` (~350 lines)

**52 blacklist regex rules across 3 categories:**
- EN journals (9): article-info blocks, abstract headers, keyword headers, running headers
- CN journals (24): volume/issue lines, CLC numbers, document codes, dates, funding info, author bios
- Universal (19): standalone page numbers, DOI/ISSN/ISBN lines, URL fragments, repeated punctuation, whitespace

**API:** Returns `(cleaned_text, CleaningReport)` tuple with line-level and category-level statistics
**Integration:** Called in `admin.py _parse_and_chunk()` before chunking
**Env var:** `ZRA_CLEAN_ENABLED=true` (default on)
**Measured:** 10.6% avg line removal (CN 19.3%, EN 7.2%)

**Decision: Blacklist > Heuristic.** Academic journal boilerplate is highly formulaic and publisher-specific. Regex exact-match has near-zero false-positive risk — no paper's body text contains "〔中图分类号〕TU984.2". Heuristic frequency-counting would flag real keywords like "Accessibility" as noise.
**Next:** PDF quality scorer (native vs. scanned vs. encrypted); adjust cleaning by quality

---

### Phase 1.2: Evaluation Framework (2026-07-01, `f9a5947`)

**New files:** `research_core/rag/evaluation.py` (~250 lines), `tests/eval_queries.json` (60 queries), `scripts/run_evaluation.py`, `scripts/generate_eval_queries.py`

**Query distribution:**
- Direct hit (~50%): answer in a single chunk
- Cross-document (~25%): requires synthesizing across papers
- No-answer rejection (~15%): verify retrieval doesn't hallucinate

**Metrics:** Recall@5/10/20, MRR, NDCG@10 (DCG formula: `(2^s-1)/log2(i+2)`)
**Baseline:** R@5=0.792, R@10=0.825, R@20=0.867, MRR=0.736

**CLI:**
```bash
python scripts/run_evaluation.py              # Full evaluation
python scripts/run_evaluation.py --baseline   # Save as baseline
python scripts/run_evaluation.py --compare    # A/B comparison
```

**Decision: LLM-generated candidates + human review.** Pure manual is too slow; pure LLM is too inaccurate

---

### Phase 1.3: Retrieval Trace Logging (2026-07-01, `f9a5947`)

**New:** `research_core/rag/logger.py` (~210 lines)

**20+ fields per trace:** trace_id, timestamp, query, strategy (hybrid/semantic/keyword/fallback), candidate counts (keyword/semantic/merged), reranker state (enabled/model/pre/post), top-20 results (key/title/score/rank/source), latency breakdown (keyword/semantic/rerank/total ms), fallback info

**Storage:** JSONL append-only + byte-offset index file (`_retrieval_log.idx`) for fast random access by trace_id

**3 new MCP tools:** `recent_retrievals`, `retrieval_trace` (replay by ID), `retrieval_stats` (aggregate)
**Integrated in:** `search_papers()` — auto-logged on every search

**Decision: Byte-offset index > SQLite.** JSONL is grep-friendly, hand-editable, zero-dependency — better for an embedded personal research tool

---

### Phase 2.1: Chunk Quality Metadata (2026-07-02, `b70e539`)

**Modified:** `chunker.py` v2.8.0 → v2.9.0

**7 new quality fields on Chunk:**

| Field | Type | Meaning |
|-------|------|---------|
| `coherence_score` | float [0,1] | Sentence-length CV → low = fragmented |
| `information_density` | float [0,1] | (len - stopword_len) / len |
| `boilerplate_ratio` | float [0,1] | Known-template fragment match ratio |
| `sentence_count` | int | Complete sentences |
| `starts_with_conjunction` | bool | Starts with "and/but/however" → prior chunk was cut |
| `language` | str | "zh" / "en" / "mixed" (via CJK/ASCII ratio) |
| `quality_flag` | str | "good" / "noisy" / "incomplete" / "boilerplate" |

**Scoring:** `score_chunk_quality()` — lightweight heuristics only (CV, stopword ratio, template matching). No extra model. Chunks are ~600 chars — too small to justify a second-model pass
**Storage:** ChromaDB metadata, supports quality-aware filtering at retrieval time

**Decision: Heuristic > Model.** Sentence-length variance and stopword ratio suffice; no extra dependency or latency

---

### Phase 2.2: SQLite Metadata DB + Section Detection (2026-07-02, `9948ac2`)

**New files:** `research_core/rag/database.py` (~370 lines), `research_core/parsers/section_detector.py` (~250 lines)

**SQLite database (`.chroma_db/papers.db`), 7 tables:**

| Table | Content | Key fields |
|-------|---------|------------|
| `papers` | Paper metadata | title, abstract, authors(JSON), keywords(JSON), doi, journal, year |
| `sections` | Hierarchical IMRaD structure | heading, section_type, level, parent_id, page_start/end |
| `chunks_meta` | Chunk location + quality | id, section_id, chunk_idx, page_start/end, 7 quality fields |
| `figures` | Figure captions | figure_label, figure_ref, caption, page |
| `table_records` | Table captions | table_label, table_ref, caption, raw_content, page |
| `chunk_figure_refs` | Many-to-many cross-refs | chunk_id ↔ figure_id |
| `chunk_table_refs` | Many-to-many cross-refs | chunk_id ↔ table_id |

**Key decisions:**
- Abstracts stored in SQLite but **NOT embedded in ChromaDB**. An abstract is a "distilled version" of a paper — it has moderate similarity to any relevant query, causing it to dominate and flatten search results
- Thread-safe singleton init (`sqlite3.check_same_thread=False` + `RLock`)
- Zero user config — auto-created on first `sync_index`
- Separation of concerns: ChromaDB stores only retrieval-essential fields; detailed metadata lives in SQLite

**Section detector:**
- H1: numbered sections "1. Introduction", Chinese "一、引言", bare keywords "Methods\n"
- H2/H3: "1.1 Study Area", "（一）研究区域", circled numbers
- `_classify_section_type()` — keyword mapping to 11 IMRaD types
- `_is_valid_heading()` — length + alpha-ratio filters to reject noise
- Quality-aware: skips boilerplate/incomplete chunks for heading detection

---

### Phase 2.2 (cont.): Section-Parent Context Expansion (2026-07-02, `86c34d4`)

**Three new Retriever methods:**

1. `expand_to_section(item_key, chunk_idx)` — SQLite lookup chain: chunk → section_id → all chunks in section → ChromaDB fetch → concatenated full section text

2. `_attach_section_contexts(results)` — batch expansion with `dict` cache to avoid redundant DB + ChromaDB queries

3. `enrich(results)` — batch JOIN across papers + sections to inject paper_abstract, paper_authors, paper_year, paper_doi, paper_keywords, section_heading, section_type

**Effect:** `search_papers(expand_context=True)` → `matched_passage` goes from 300-char chunk fragment to 2000-char complete section

---

### Phase 2.4: Embedding Quality Diagnostics (2026-07-03, `82918ed`)

**New:** `research_core/rag/embedding_diagnostics.py` (~372 lines)

**6-phase analysis pipeline:**

| Phase | Analysis | Method |
|-------|----------|--------|
| 1 | Sample papers + compute embeddings | Random sample, numpy float32 |
| 2 | Intra-paper separation | Pairwise cosine sim + centroid coherence |
| 3 | Most similar paper pairs | Centroid-to-centroid, top-10 |
| 4 | Length-similarity correlation | Pearson r |
| 5 | Section-type separation | Group by section_type, centroid coherence |
| 6 | Auto-issue detection + fixes | Separation < 1.3, outlier rate > 10%, etc. |

**Findings on test set (20 papers):**
- Separation ratio 0.95x (below 1.0 — inter-paper > intra-paper sim)
- Positive length-sim correlation r=0.44 (longer chunks match easier)
- Top paper pair at 0.88 sim (potential near-duplicates)
- Cleaning + overlap changes dropped separation from 1.13x to 0.95x — confirmed metadata-aware retrieval is essential

---

### Overlap Rewrite (v2.8.0) (2026-07-01, `f9a5947`)

**Change:** sentence-based overlap (1 sentence) → character-based (100 chars) + sentence-boundary completion

**`_tail()` algorithm rewritten:**
1. Forward search in overlap window `[end-overlap_chars*2, end]`: find last sentence boundary
2. Backward search to sentence start: extend from boundary backward
3. Clause punctuation fallback: use `,;，；` etc. when no full sentence
4. Safe empty fallback: return "" when nothing found (never garbage)

**Bug fix iterations:**
- v1 (backward-only): EN overlap 1% — searching backward from `start` found no boundaries
- v2 (forward-only): found boundaries but took text after them (empty at end of text)
- v3 (final): search entire window for last sentence boundary, take text after it → works

**Measured:** CN 67%/EN 46-50% overlap coverage

---

### A/B Evaluation Results

Text cleaning + overlap changes:
- Recall@5: -0.009 (within noise)
- MRR: +0.011 (slight improvement)

**Conclusion:** Changes are backward-compatible. They're "infrastructure" — they don't directly boost raw precision, but enable quality-aware filtering, metadata retrieval, and query rewrite

---

#### ONNX INT8 Embedding Backend (2026-07-08)

- New `ONNXInt8Embedding` class — ONNX Runtime INT8 quantized inference
- Uses community pre-quantized model (~347MB vs 2.3GB FP32)
- `EMBEDDING_BACKEND=auto` (default): prefers ONNX INT8, falls back to sentence-transformers
- Benchmark: 3.7x encode speedup, 0.95 fidelity, 74% chunk@10 overlap with FP32
- Zero-config — `auto` mode selects best backend automatically
- Reduces first-install disk footprint from ~4.3GB to ~370MB

#### MMR Diversity Reranking (2026-07-07, `980ab81` / `ac925fd`)

- Chunk-level MMR: lambda=0.6, uses existing bge-m3 embeddings from ChromaDB (~15ms)
- Hard cap: 3 chunks/paper; per-document penalty: 0.1 per extra chunk
- Enabled by default; tested on 6 queries: avg +1.2 papers, max chunks 4.8->2.7

#### Query Rewrite Dictionary (2026-07-07, `c10aeff` / `89ecae5`)

- 3-layer: ~300 built-in pairs + Zotero tags + add_query_synonym MCP tool
- CN-EN bidirectional, LRU-cached, zero latency, no LLM dependency

### Known Issues (v0.3.0)

1. **Embedding separation 0.95x** — wait for metadata-enhanced reranking
2. **Length-sim positive correlation r=0.44** — chunks may rank by length not content
3. **CNKI module unstable** — depends on browser automation (Playwright + Chrome CDP), disabled by default
4. **No Contextual Summarization** — requires MCP server to have independent LLM access

### Next Steps

| Priority | Task | Expected Impact |
|----------|------|-----------------|
| P0 | Adaptive Chunk Size (methods=400, discussion=700) | Better long-paragraph retrieval |
| P1 | Metadata-Enhanced Reranking (citations, journal, retraction) | Better academic ranking |
| P2 | Unified `diagnose_rag` MCP tool | One-click pipeline diagnostics |
| P3 | Contextual Summarization (PaperQA2-style) | Query-relevant summary reranking (needs extra LLM) |

---

## v0.2.0 — Standalone MCP Server Release (2026-06-11)

**Related commits:** `52b8247` `7b88467` `3bdda3c` `191ee35` `9fe189b` `484e871` et al. (PR #1–#9)

Refactored from agent scaffold to pure MCP server. 32 single-intent tools, each mapping one user intent, composing via `item_key`.

---

### Architecture: Removing Agent Scaffold (`7b88467`)

**Removed:** `project_b_agent/` (FastAPI backend), `research_core/llm/`, `research_core/eval/`, agent dependencies (fastapi, uvicorn, sse-starlette, aiosqlite, litellm, openai)

**Why:** This project is an MCP tool server — the LLM is provided by the client (Cursor/Cherry Studio/Claude Desktop), not built-in
**Impact:** Version jumped from 0.1.2 to 0.2.0

---

### P0–P2 Stability Fixes

**P0: Bug fixes** (`52a5d54`)
- `embeddings` → `embedding` typo in health check (caused persistent ImportError)
- `and`/`or` operator precedence in `_diagnose_error` short-circuiting the combined condition

**P1: Concurrency + memory + response cap** (`191ee35`)
- Unified ChromaDB client singleton + `sync_lock` (RLock protecting all writes)
- Paginated `inspect_index` reads (1000 chunks per page) to prevent OOM on large libraries
- `_truncate_response()` function: MCP response cap at 50K chars, `[TRUNCATED]` markers

**P2: Architecture cleanup** (`9fe189b`)
- Extracted `expand_citation_network` from server.py to `research_core/tools/citation_network.py`
- Global HTTP client: concurrency control, per-host rate limiting, 429/5xx auto-retry
- Unified response format: bare lists → `{data: [...], count: N}`
- Response truncation: 80K cap → trim text fields first → drop items last

---

### Concurrency & Performance (`3bdda3c`)

- Thread-safe singleton init (embedding, reranker, store, server globals)
- `verify.py` three-outcome model (True/False/None): transient network errors no longer drop legitimate papers
- External API calls routed through shared HTTP client (retry/rate-limit/timeout/cache)
- Zotero client socket timeout + circuit breaker
- HNSW tuning (search_ef=100, construction_ef/M env-overridable)
- Device-aware batched embedding (cuda/mps/cpu)
- Parallel PDF parse+chunk, serial indexing under sync_lock

---

### Tables/Figures: From Structured Extraction to Caption Anchors (`e7b7f0c` → `52b8247`)

**Stage 1: ML structured extraction attempt** (`e7b7f0c`)
- `ZRA_TABLE_MODE=ml` using Microsoft Table Transformer
- `table_ml.py` — thread-safe lazy-loaded model with config compatibility shims
- Tables rendered as Markdown from PDF text layer (no OCR)
- Cost: torchvision + timm + pillow deps, `[tables]` optional extra

**Stage 2: Abandoned — caption anchors only** (`52b8247`)
- Removed all structured table extraction (lite mode, ML mode, `[tables]` extra, `table_ml.py`)
- Caption-anchored records: label + ref + raw content for tables; label + ref + caption for figures
- CJK-aware prose boundary detection; tabular-content guard; measure-word guard
- Prose "as shown in Table 3" auto-links to records

**Why abandoned:** PyMuPDF geometric detection produced garbage on borderless three-line academic tables — one paper yielded 110 fake multi-part "tables"; multi-column prose and references were mis-segmented as dozens of fake tables. Reliable table structuring is fundamentally a vision problem (requires "looking at the image"), not solvable by text/geometry methods

---

### CJK-Aware Chunking (`59637d8`)

**Problem:** Chunker assumed English conventions — required whitespace after terminators (Chinese 。！？ have none), hard-split only on spaces/newlines. Chinese chunks were cut mid-sentence; PDF soft line-wraps broke words (`满\n意度`)

**Fixes:**
- `_split_sentences()` — CJK terminators split directly (no space check); ASCII terminators remain space-gated (prevents splitting "U.S."/"3.14")
- `_hard_split()` — optimal boundary: sentence terminator → clause punctuation `，、` → whitespace
- `_join_soft_wraps()` — repair CJK soft wraps + Latin hyphenation at page assembly time
- Chunker: v2.4-cjk-aware (auto-triggers full rebuild)

---

### Table/Figure Cross-Referencing (`776ba34` / `435a4f5`)

**Table xref** (v2.5-table-xref):
- Parse label + canonical ref from caption (表3 / Table 3 / Tab. 5 / 附表2)
- Prepend natural-language column summary to table chunks for semantic recall
- Scan prose for table mentions, intersect with actual tables in paper
- `get_item_tables(item_key, refs)` — fetch by ref, multi-part tables in order
- Measure-word guard: excludes "试图3次" / "代表3个" false matches

**Figure xref** (v2.6-figure-xref, no image recognition):
- Symmetric design to table xref
- Extract from raw (pre-soft-wrap-join) text to keep captions bounded
- Measure-word guard for "5 items" false matches
- `get_item_figures(item_key, refs)`

---

### Zotero API Filter Fix (`18b9451`)

**Problem:** Zotero local API silently ignored combined negation filter `"-attachment || note"`, returning ALL items. Of 732 items in a real library, 468 were attachments/notes incorrectly treated as papers.

**Fix:** Client-side exclusion using positive per-type version queries. Same guard applied to `get_all_items_minimal` and `search_all_annotations`.

**ChromaDB fix:** `collection.modify()` with `hnsw:space` rejected by ChromaDB as distance-function change. Strip it before modify.

---

### Structured Table Extraction + Hard Chunk Cap (`70b4317`)

**Problem:** A 15MB data-heavy PDF failed indexing with 20 GiB MPS allocation. Root cause: a borderless data table produced a 9038-char (2294-token) run-on chunk (no sentence boundaries); at batch size 64, bge-m3 attention tensor reached 20.07 GiB.

**Fixes:**
- Extract ruled tables from page text (conservative line detection)
- Render tables as row-grouped Markdown (header repeated per part) + JSON payload
- `_enforce_max_chars()` — universal hard cap, no chunk exceeds max_chunk_size (v2.3-tables-hardcap)
- `EMBEDDING_MAX_SEQ_LEN=1024` safety net env var
- Verified: previously-failing papers (QZATEAF3: 141 chunks, D9VKKS8H: 85 chunks) now index cleanly

---

### CI & Publishing

- **GitHub Actions** (`a33e0bf`): ruff + unit/core tests on Python 3.11/3.12, push to main and PRs
- **MIT LICENSE** (`c1412e0`)
- **`scripts/publish.sh`** (`fc9b6bb`): credential-free release; reads from `~/.pypirc` or `TWINE_*` env vars; `--test` and `--check-only` modes
- `.pypirc` in `.gitignore` to prevent token leaks (`f382e61`)

---

## v0.1.0 — Initial Development (2026-05-15 ~ 2026-06-09)

> Most development in this period occurred on a different machine (Co-authored-by: Cursor). Reconstructed from commit records.

### Timeline

| Date | Feature | Commit |
|------|---------|--------|
| 05-15 | Project launch: 13 MCP tools + shared core | `077d7a1` |
| 05-18 | Cherry Studio setup guide (non-developer friendly) | `68b6621` |
| 05-20 | RAG pipeline v2.1 + figure/table captions + bilingual README + 3 admin tools | `1817edd` |
| 05-25 | Expand to 16 tools; write-safety (explicit confirm); filter-only search | `e51a578` |
| 05-28 | Rename to zotero-research-assistant | `34a3fb0` |
| 05-30 | Online literature discovery; OA PDF waterfall (6 sources) | `a0d91ce` |
| 06-01 | Anti-hallucination: Corpus-First, [MATERIAL GAP], Three-Index Verification | `f9affee` |
| 06-02 | Citation network expansion | `8762f0d` |
| 06-03 | CNKI integration (Playwright + Chrome CDP) | `22f1372` |
| 06-06 | Argument finder | `da9ec43` |
| 06-07 | Literature review generator + smart tag suggestions | `f4c4dff` |
| 06-09 | Reading status detection + personalized recommendations | `6ee526f` |

### Version History

| Version | Date | Event |
|---------|------|-------|
| v0.1.1 | 05-20 | RAG optimization + bilingual README + 3 admin tools |
| v0.1.2 | 05-28 | PyPI publish, HF mirror support, Cherry Studio guide rewrite |
| v0.2.0 | 06-11 | Standalone MCP server, 32 tools, CJK chunking, table xref, concurrency |

---

## Foundational Technical Decisions (v0.1.0 era)

### Embedding Model
**bge-m3 (1024-dim) > all-MiniLM-L6-v2 (384-dim).** BGE-M3 supports 100+ languages with strong CJK + English performance. MiniLM is too small for Chinese. Cost: ~2.3GB first download (one-time).

### Vector Database
**ChromaDB > Qdrant/Milvus.** Lightweight, Python-native, zero-ops, embedded — fits single-researcher local use. Qdrant/Milvus are server-grade overkill.

### Hybrid Search Architecture
**Keyword + Semantic RRF fusion.** Pure semantic misses exact matches (DOI, author names); pure keyword misses semantically related but differently-worded papers. RRF is simple, effective, and literature-validated.

### Cross-Encoder Reranking
**ms-marco-MiniLM-L-6-v2** (~80MB). Bi-encoder recall + Cross-encoder precision is a validated paradigm. MiniLM balances speed and accuracy.

### Anti-Hallucination Strategy
**Three-layer defense:** (1) Three-Index Verification — every result with DOI cross-checked against CrossRef/OpenAlex/S2; (2) `[MATERIAL GAP]` tags — explicit markers when search returns zero results, preventing LLM fabrication; (3) Source provenance — every paper has a verifiable link.

### Table/Figure Strategy
**Caption anchors > Structured extraction.** See v0.2.0 details. This decision went through a full "try ML → discover it doesn't work → rollback" cycle. Reliable table structuring is a vision problem — text/geometry solutions produce garbage on academic papers.

---

## v0.4.3 — Windows HNSW Cross-Process Bug: Diagnosis + Client-Server Fix (2026-07-16)

### The Symptom

Every `search_papers` call from Cherry Studio timed out. The ChromaDB log showed:
```
Error executing plan: Error sending backfill request to compactor:
Error constructing hnsw segment reader: Error loading hnsw index
```
The collection (19,790 chunks across 248 papers) was completely unqueryable.

### The Investigation

**Hypothesis 1 — Compaction corruption:** `bilingual_enrich.py` had run 633 small
`collection.update()` calls, generating 1266 WAL entries. Maybe ChromaDB's async
compaction left the HNSW segment in a partial state when the process exited.

→ **WRONG.** Even with a completely fresh `add()` of 2000 items, the HNSW failed
on the next process read. The compaction state looked healthy (queue empty, max_seq_id
consistent).

**Minimal reproduction:**
```python
# Write 2000 items in process A
col.add(ids=ids, embeddings=np.random.randn(2000, 128))
col.count()  # → 2000 OK

# Read in process B
col.count()  # → Error loading hnsw index
```
2000 random vectors, zero business code, zero enrichment. 100% reproducible.

**Threshold discovery — systematic parameter sweep:**

| n | dim | data size | Cross-process |
|---|-----|-----------|---------------|
| 500 | 128 | 0.25MB | OK |
| 750 | 1024 | 2.9MB | OK |
| 1000 | 128 | 0.5MB | **FAILED** |
| 1000 | 1024 | 3.9MB | **FAILED** |
| 2000 | 128 | 1.0MB | **FAILED** |

The failure threshold is exactly **1000 items**, independent of dimension or data size.
Below 1000, ChromaDB keeps all data in the WAL (embeddings_queue) without building an
HNSW index. At >= 1000, it triggers HNSW construction — and the resulting segment files
(`data_level0.bin`, `link_lists.bin`) cannot be loaded by a different process.

**Root cause:** ChromaDB's `hnswlib` (C++ library) produces HNSW segment files on
Windows that are **process-local** — they can only be loaded by the process that
created them. This is a known issue (chroma-core/chroma#3058). The same code works
on Linux without issues.

### The Fix: Client-Server Architecture

ChromaDB's recommended solution for cross-process access is client-server mode
(`chroma run` + `HttpClient`). A single long-running server process owns the
database files; all clients connect via HTTP. No cross-process file access → no
HNSW loader crash.

**Implementation:**

1. **`research_core/rag/chroma_server.py`** (NEW) — Manages a `chroma run` subprocess:
   start on MCP server startup, stop on shutdown, health-check via heartbeat endpoint.
   Reuses existing server if already listening.

2. **`research_core/rag/store.py`** — `get_collection()` now creates `HttpClient` by
   default (connects to `127.0.0.1:18000`). Falls back to `PersistentClient` if server
   is unreachable. Controlled by `ZRA_CHROMA_MODE` env var.

3. **`project_a_mcp/server.py`** — Lifespan starts/stops the ChromaDB server
   subprocess. No user-visible change — everything is automatic.

**Verification** (after full sync via HttpClient):
```
Same-process:   19,790 chunks, query 0.1s, 10 hits OK
Cross-process:  19,790 chunks, query OK, 5 hits OK  ← previously crashed here
```

**Safety nets retained:**
- `_startup_diagnostics()` HNSW health check + auto-repair (for persistent mode users)
- NMT preload made synchronous (no more race condition on first search)

**Zero new dependencies.** All imports are stdlib or already in pyproject.toml.

---

## Conventions

- Each significant update records: date, version, related commits, category
- Categories: Added / Fixed / Changed / Optimized / Removed / Technical Decision
- **"Problem solved" and "Future direction" are the core** — essential for understanding tech debt and roadmap
- Evaluate whether to update this file + `CHANGELOG.md` on every commit (see `CLAUDE.md` rules)
