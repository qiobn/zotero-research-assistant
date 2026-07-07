# Development Plan — RAG Full-Pipeline Optimization

> Last updated: 2026-07-06 | Current version: v0.3.0

---

## Progress Overview

```
Phase 0 (Audit)    ████████████████████ 100%  ✅ DONE
Phase 1 (P0)       ████████████████████ 100%  ✅ DONE
Phase 2 (P1)       ████████████████████ 100%  ✅ DONE
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

### 1.1 PDF Text Cleaning Pipeline ✅

> Implemented: `research_core/parsers/text_cleaner.py` (~350 lines). 52 blacklist rules across EN journal (9), CN journal (24), Universal (19). Returns `(cleaned_text, CleaningReport)`. Integrated in `admin.py` `_parse_and_chunk()`. Env var: `ZRA_CLEAN_ENABLED=true` (default on).

---

### 1.2 Systematic Recall Evaluation Framework ✅

> Implemented: `research_core/rag/evaluation.py` (~250 lines). 60 golden queries in `tests/eval_queries.json`. Metrics: Recall@5/10/20, MRR, NDCG@10. `scripts/run_evaluation.py` with `--save-baseline` / `--compare`.

---

### 1.3 Retrieval Log / Trace ✅

> Implemented: `research_core/rag/logger.py` (~210 lines). JSONL append-only + byte-offset index. 3 MCP tools: `recent_retrievals`, `retrieval_trace`, `retrieval_stats`. Integrated in `search_papers()`.

---

## Phase 2: P1 — Quality Ceiling Raisers ✅

### 2.1 Chunk Quality Metadata ✅

> Implemented: `research_core/parsers/chunker.py` (v2.9.0). 7 quality fields: `coherence_score`, `information_density`, `boilerplate_ratio`, `sentence_count`, `starts_with_conjunction`, `language`, `quality_flag`. Lightweight heuristic scoring via `score_chunk_quality()`. Stored in ChromaDB metadata.

### 2.2 SQLite Metadata Database + Section-Parent Context ✅

> Implemented: Replaced original Parent-Child dual index plan with cleaner architecture. `research_core/rag/database.py` (~370 lines): 7 tables (papers, sections, chunks_meta, figures, table_records + cross-refs). `Retriever.expand_to_section()` and `_attach_section_contexts()` provide section-parent context expansion via SQLite JOIN. Result enrichment via `enrich()`.

### 2.3 PDF Text Cleaner (In-Pipeline) ✅

> Integrated in `admin.py _parse_and_chunk()`. `ZRA_CLEAN_ENABLED=true` (default). Cleaning stats in `SyncReport`.

### 2.4 Embedding Quality Diagnostics ✅

> Implemented: `research_core/rag/embedding_diagnostics.py` (~372 lines). 6-phase analysis: intra/inter similarity, outlier detection, length correlation, section-type analysis, automated issues + suggestions.

### 2.5 Contextual Summarization (PaperQA2-inspired) ⬜

> **DEFERRED to Phase 3**: requires MCP server to have its own LLM access, a new architectural dependency. May be better as post-MCP step handled by client LLM.

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
| Phase 1 (P0) | 3 | 3 | 0 | Done |
| Phase 2 (P1) | 5 | 4 | 1 | Done (deferred 2.5) |
| Phase 3 (P2) | 5 | 0 | 5 | 8-12 days |
| **Total** | **18** | **12** | **6** | **~11 days remaining** |

### Immediate Next Step

```
→ Phase 3.1: Query Rewrite (Academic Scene)
   Chinese-English bilingual expansion + synonym expansion
```

### Key Decisions Log

| Decision | Choice | Date |
|----------|--------|------|
| Parent-Child implementation | Section-Parent context expansion (simpler, cleaner) | 2026-07-02 |
| Abstract storage | SQLite only, NOT embedded | 2026-07-01 |
| Evaluation set construction | LLM generate + human review — 60 golden queries | 2026-07-01 |
| PDF cleaning aggressiveness | Blacklist regex (exact match, near-zero false positives) | 2026-07-01 |
| Start with audit or code | Audit first — done | 2026-06-30 |

### Key Decisions Log

| Decision | Choice | Date |
|----------|--------|------|
| Parent-Child implementation | Full rebuild (clean architecture) | 2026-06-30 |
| Evaluation set construction | LLM generate + human review | 2026-06-30 |
| PDF cleaning aggressiveness | Mark-only first (conservative), then block based on data | 2026-06-30 |
| Start with audit or code | Audit first — done | 2026-06-30 |
