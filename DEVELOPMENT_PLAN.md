# Development Plan — RAG Full-Pipeline Optimization

> Last updated: 2026-07-01 | Current version: v0.2.0

---

## Progress Overview

```
Phase 0 (Audit)    ████████████████████ 100%  ✅ DONE
Phase 1 (P0)       ████████░░░░░░░░░░░░  40%  ← IN PROGRESS
Phase 2 (P1)       ░░░░░░░░░░░░░░░░░░░░   0%
Phase 3 (P2)       ░░░░░░░░░░░░░░░░░░░░   0%
```

---

## Phase 0: Baseline Audit ✅

| # | Task | Status | Output |
|---|------|--------|--------|
| 0.1 | `scripts/index_sample.py` — sample N papers for testing | ✅ Done | `scripts/index_sample.py` |
| 0.2 | `scripts/audit_index.py` — full-library quality audit | ✅ Done | `scripts/audit_index.py` |
| 0.3 | venv setup + deps install | ✅ Done | `.venv/` |
| 0.4 | 20-paper test index built | ✅ Done | 2102 chunks, `.chroma_db/` |
| 0.5 | First audit report generated | ✅ Done | See audit results below |

### Audit Baseline (20 papers / 2102 chunks)

| Metric | Value | Verdict |
|--------|-------|---------|
| Garbled chunks | 0% | Excellent |
| Long chunks (>1500) | 0% | Cap working |
| Short chunks (<50) | 2.8% | Acceptable |
| Figure/Table chunks | 16.9% | Extraction working |
| Embedding separation | 1.13x | **WEAK** (threshold 1.3x) |
| Noise patterns | "Keywords:", "A R T I C L E I N F O", "A B S T R A C T" | Confirmed across 85%+ papers |
| Avg chunks/paper | 105.1 | Too fine-grained |
| Health score | 65/100 (B) | Needs improvement |

---

## Phase 1: P0 — Critical Gaps

### 1.1 PDF Text Cleaning Pipeline 🟡

> **Why**: Audit confirmed "Keywords:", "A R T I C L E I N F O", "A B S T R A C T" in 17-18 of 20 papers. These journal format strings are indexed as semantic content, degrading retrieval precision.

**Files to create/modify:**

| File | Purpose |
|------|---------|
| `research_core/parsers/pdf_quality.py` | PDF quality scorer (native/scanned/encrypted/low_quality) |
| `research_core/parsers/text_cleaner.py` | Text cleaning pipeline (headers, footers, watermarks, citation markers) |
| `research_core/parsers/__init__.py` | Export new modules |

**Cleaning rules (based on audit data):**
- [x] Strip "Keywords:" lines (journal keyword headers)
- [x] Strip "A R T I C L E  I N F O" blocks (Elsevier article info)
- [x] Strip "A B S T R A C T" standalone headers
- [x] Remove page numbers (standalone digit lines)
- [x] Remove DOI/ISSN/ISBN lines
- [x] Remove copyright footers ("© 2024...", "Published by...")
- [x] Remove reference citation markers ("[1]", "[2,3,5-8]", "²³")
- [x] Normalize whitespace (merge 3+ consecutive newlines)
- [x] Remove URL fragments in text body
- [x] Deduplicate repeated lines within same paper

**Integration:**
- [x] Call cleaner in `sync_index` before chunking
- [x] Add `cleaned` flag to chunk metadata
- [x] Add `quality_score` to chunk metadata (0-100)
- [x] Add `--clean` flag to `scripts/index_library.py`

**Tests:**
- [x] `tests/core/test_text_cleaner.py` — unit tests per cleaning rule
- [x] `tests/core/test_pdf_quality.py` — quality scorer edge cases

**Estimate:** 3-4 days

---

### 1.2 Systematic Recall Evaluation Framework ⬜

> **Why**: Currently only single-paper `test_recall` (title search). No way to measure if retrieval changes are improvements or regressions.

**Files to create/modify:**

| File | Purpose |
|------|---------|
| `research_core/rag/evaluation.py` | Recall@K, MRR, NDCG computation |
| `tests/eval_queries.json` | Standard evaluation query set (50-100 queries) |
| `scripts/run_evaluation.py` | CLI to run eval and compare baselines |

**Evaluation query set must cover 4 categories:**
- [ ] Type A: Direct hit (answer in single chunk) — 30 queries
- [ ] Type B: Cross-document synthesis — 20 queries
- [ ] Type C: No-answer rejection test — 15 queries
- [ ] Type D: Contradictory document detection — 10 queries

**Metrics to implement:**
- [ ] Recall@5, Recall@10, Recall@20
- [ ] MRR (Mean Reciprocal Rank)
- [ ] NDCG@10
- [ ] Context Precision
- [ ] Per-paper failure analysis

**Build approach:**
- [ ] LLM generates 100 candidate queries from indexed paper metadata
- [ ] Run against current index, flag queries returning zero results
- [ ] Human review top 20 + manual correction
- [ ] Finalize 75 golden queries

**CLI:**
```bash
python scripts/run_evaluation.py              # Full eval
python scripts/run_evaluation.py --baseline   # Save as baseline
python scripts/run_evaluation.py --compare    # Compare vs baseline
```

**Estimate:** 2-3 days

---

### 1.3 Retrieval Log / Trace ⬜

> **Why**: When "why didn't this paper show up?" is asked, there's zero visibility into what happened.

**Files to create/modify:**

| File | Purpose |
|------|---------|
| `research_core/rag/logger.py` | Structured retrieval logging |
| `.chroma_db/_retrieval_log.jsonl` | Log output (append-only) |

**What to log per retrieval:**
- [ ] Timestamp + unique trace_id
- [ ] Original query
- [ ] Search strategy (semantic / keyword / hybrid / fallback)
- [ ] Candidate count per source (keyword_n, semantic_n)
- [ ] RRF fusion weights
- [ ] Top-20 results (item_key, chunk_idx, score, source)
- [ ] If reranker enabled: pre-rerank top and post-rerank top
- [ ] Final returned results
- [ ] Latency breakdown (keyword_ms, semantic_ms, rerank_ms, total_ms)

**MCP integration:**
- [ ] Add `diagnose_retrieval` tool — replay a past trace_id
- [ ] Add `recent_queries` tool — list recent retrieval logs

**Estimate:** 1-2 days

---

## Phase 2: P1 — Quality Ceiling Raisers

### 2.1 Chunk Quality Metadata ⬜

> **Why**: All chunks are treated equally. No way to know if a chunk is a coherent paragraph or a broken sentence fragment.

**Add to Chunk dataclass:**
- [ ] `coherence_score` — sentence-to-sentence embedding cosine mean
- [ ] `information_density` — (length - stopword_length) / length
- [ ] `boilerplate_ratio` — % of text matching known templates
- [ ] `sentence_count` — number of complete sentences
- [ ] `starts_with_conjunction` — boolean (broken from previous chunk?)
- [ ] `language` — "zh" / "en" / "mixed"
- [ ] `quality_flag` — "good" / "noisy" / "incomplete" / "boilerplate"

**Store in ChromaDB metadata:**
- [ ] Add fields to `Indexer._build_metadata()`
- [ ] Add filtering support in `Retriever.search()` (e.g., `min_quality="noisy"`)

**Estimate:** 2 days

---

### 2.2 Parent-Child Dual Granularity Index ⬜

> **Why**: Decision made — full rebuild (clean architecture). Small chunks for precise recall, parent chunks for complete context.

**Implementation:**
- [ ] Add `parent_chunk_id` field to Chunk metadata
- [ ] Chunking strategy: split at section boundaries → sub-split into child chunks (~400 chars) → record parent range
- [ ] Index both child and parent in ChromaDB (parent chunks with `is_parent=True` flag)
- [ ] `Retriever.search()` returns child chunks by default
- [ ] `Retriever.expand_context(child_chunk_id)` returns parent context
- [ ] Add `get_paper_content` mode: `"parent_context"` that auto-expands
- [ ] Requires full index rebuild (`sync_index --force-rebuild`)

**Estimate:** 2-3 days

---

### 2.3 PDF Text Cleaner (In-Pipeline) ⬜

> **Why**: Currently no cleaning happens between PDF extraction and chunking. See 1.1 for the cleaning rules — this task integrates them into the indexing pipeline.

**Difference from 1.1:** 1.1 creates the cleaner modules. This task wires them into `sync_index` and adds the `--clean` flag plus configuration options.

- [ ] Wire `text_cleaner` call into `sync_index` → `_parse_and_chunk`
- [ ] Add `ZRA_CLEAN_ENABLED=true` env var (default on)
- [ ] Add `ZRA_CLEAN_AGGRESSIVE=true` env var (default off — mark only mode)
- [ ] Add cleaning stats to `SyncReport`
- [ ] Update `inspect_index` to show cleaned vs raw stats

**Estimate:** 2-3 days (including 1.1 work)

---

### 2.4 Embedding Quality Diagnostics ⬜

> **Why**: Audit found 1.13x separation ratio (weak). Need deeper diagnostics to understand why and how to fix.

**Add to `scripts/audit_index.py` or new `research_core/rag/embedding_diagnostics.py`:**
- [ ] Per-paper intra-similarity distribution (violin plot data)
- [ ] Cross-paper similarity heatmap (top N papers)
- [ ] Outlier chunk detection (chunks with no close neighbors in same paper)
- [ ] Embedding dimension PCA/UMAP projection data
- [ ] Topic cluster identification (which papers' embeddings clump together)
- [ ] Chunk length vs embedding quality correlation
- [ ] Section type vs embedding separation analysis (are reference chunks the culprit?)

**Estimate:** 1-2 days

---

### 2.5 Contextual Summarization (PaperQA2-inspired) ⬜

> **Why**: PaperQA2's key innovation. Instead of comparing raw chunk text to query, generate a query-relevant summary of each chunk first, then rank summaries.

**Implementation (optional, behind env var):**
- [ ] Add `ZRA_CONTEXTUAL_SUMMARIZE=true` env var
- [ ] After retrieving top-20 candidate chunks, call a lightweight LLM to summarize each chunk in context of the query
- [ ] Use summary text (not raw chunk) for Cross-Encoder reranking
- [ ] Store summary alongside chunk in retrieval result
- [ ] Configurable summary_llm (OpenAI/Ollama/local) — separate from main LLM

**Note:** This requires the MCP server to have its own LLM access, which is a new architectural dependency. May be better as a post-MCP-retrieval step handled by the client LLM itself.

**Estimate:** 2-3 days

---

## Phase 3: P2 — Refinements

### 3.1 Query Rewrite (Academic Scene) ⬜

- [ ] Chinese-English bilingual query expansion
- [ ] Synonym expansion using Zotero tags/keywords as vocabulary
- [ ] Query decomposition for complex multi-clause questions
- [ ] Add `query_rewrite` parameter to `search_papers`

**Estimate:** 2-3 days

---

### 3.2 Adaptive Chunk Granularity ⬜

> **Why**: Audit found avg 105 chunks/paper. For methods sections, small chunks are fine; for discussion sections, larger chunks preserve argument flow.

- [ ] Content type classifier (methods / results / discussion / introduction) based on section heading keywords
- [ ] Adaptive target sizes: methods=400, results=500, discussion=700, intro=600
- [ ] Integrate into `chunk_text()` without breaking existing logic

**Estimate:** 2-3 days

---

### 3.3 Search Result Post-Processing ⬜

- [ ] **MMR diversity re-ranking** — prevent single-paper dominance in top-K
  - Port MMR algorithm from PaperQA2 or implement directly
  - Configurable lambda (diversity vs relevance trade-off)
- [ ] **Auto context expansion** — return adjacent chunks alongside hit chunk
  - Add `expand_context` parameter to `search_papers` and `get_paper_content`
- [ ] **Source diversity guarantee** — ensure top-10 spans at least 3 different papers
- [ ] **Freshness boost** — configurable year weighting

**Estimate:** 1-2 days

---

### 3.4 Comprehensive Diagnostic MCP Tool ⬜

- [ ] New MCP tool: `diagnose_rag` — runs audit + returns human-readable report
- [ ] Integration with existing `check_health` to avoid duplication
- [ ] Output includes: health score, top issues, actionable fix list
- [ ] Supports `--json` for programmatic use

**Estimate:** 1-2 days

---

### 3.5 Metadata-Enhanced Re-Ranking ⬜

> **Why**: PaperQA2 uses citation counts, journal quality, and retraction status in ranking. Your project has access to Zotero metadata plus CrossRef/OpenAlex enrichment.

- [ ] Add `citation_count`, `journal_quality`, `is_retracted` to paper metadata cache
- [ ] Weighted scoring: relevance_score * metadata_boost
- [ ] Configurable metadata weight in search parameters

**Estimate:** 1-2 days

---

## Summary

| Phase | # Tasks | Completed | Remaining | Est. Total Work |
|-------|---------|-----------|-----------|-----------------|
| Phase 0 (Audit) | 5 | 5 | 0 | Done |
| Phase 1 (P0) | 3 | 0 | 3 | 6-9 days |
| Phase 2 (P1) | 5 | 0 | 5 | 9-13 days |
| Phase 3 (P2) | 5 | 0 | 5 | 8-12 days |
| **Total** | **18** | **5** | **13** | **23-34 days** |

### Immediate Next Step

```
→ Phase 1.1: PDF Text Cleaning Pipeline
   Start with text_cleaner.py (highest ROI based on audit data)
```

### Key Decisions Log

| Decision | Choice | Date |
|----------|--------|------|
| Parent-Child implementation | Full rebuild (clean architecture) | 2026-06-30 |
| Evaluation set construction | LLM generate + human review | 2026-06-30 |
| PDF cleaning aggressiveness | Mark-only first (conservative), then block based on data | 2026-06-30 |
| Start with audit or code | Audit first — done | 2026-06-30 |
