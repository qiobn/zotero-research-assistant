# Development Plan — RAG Full-Pipeline Optimization

> Last updated: 2026-09-03 | Current development version: v0.4.10.dev0

---

## Progress Overview

```
Phase 0 (Audit)    ████████████████████ 100%  ✅ DONE
Phase 1 (P0)       ████████████████████ 100%  ✅ DONE
Phase 2 (P1)       ████████████████████ 100%  ✅ DONE
Phase 3 (P2)       ████████░░░░░░░░░░░░  40%  (3.1 superseded, 3.3 complete; 3.2/3.4/3.5 deferred)
Phase 4 (Hardening) ███████████████░░░░░  71%  10 of 14 audit/release tasks complete
```

---

## Phase 3: P2 — Refinements (Partial)

### 3.1 Query Rewrite (Academic Scene) ↩️ Superseded

> The former built-in dictionary and query-time NMT approach was removed. `research_core/rag/query_rewriter.py` now provides only query validation, user-defined synonyms, and Zotero-tag lookup through `expand_query`. The MCP client owns translation, decomposition, and multi-call retrieval strategy; index-time OPUS-MT metadata enrichment remains separately configurable.

### 3.2 Adaptive Chunk Granularity ⬜

> **RESEARCHED & DEFERRED**: 2025-2026 literature (NAACL, Chroma, PaperQA2) shows fixed-size chunking is the strong baseline. Semantic/adaptive chunking does not consistently beat it. PaperQA2 uses fixed ~9000 chars with downstream LLM reranking. Chunk size is the dominant variable, not the splitter method.

### 3.3 Search Result Post-Processing ✅

> All three originally planned items completed:
> - MMR diversity (λ=0.4, grid-search tuned, max 3 chunks/paper)
> - Neighbor chunk expansion (±1 chunk, section-constrained)
> - Source diversity (MMR cap + per-document penalty)

### 3.4 Comprehensive Diagnostic MCP Tool ⬜

> **DEFERRED**: Normal users don't trace why results ranked a certain way. De-prioritized.

### 3.5 Metadata-Enhanced Re-Ranking ⬜

> **RESEARCHED & DEFERRED**: Marginal improvement for personal libraries (5-10%). The strongest signals (citation count, journal tier) are external and add latency. Not worth the complexity at this scale.

---

## Phase 4: Retrieval and Release Hardening (2026-07-13 onward)

Issues identified during full architecture review:

### 4.1 Evaluation Tests Full Pipeline 🔴 HIGH ✅

> **Problem**: `evaluate_retrieval()` only tests `retriever.search()` (pure semantic). BM25, CE reranker, MMR, and RRF fusion have NEVER been evaluated. Every retrieval component you've built lacks quantitative validation.
>
> **Fix**: Add `--full-pipeline` mode to `run_evaluation.py` that calls `search_papers()` instead of `retriever.search()`. Compare semantic-only vs full-pipeline metrics. Save baselines for future regression testing.

**Estimate:** 1 day

---

### 4.2 Log Rotation ⬜ 🟡 MEDIUM

> **Problem**: `_retrieval_log.jsonl` grows unboundedly. Large libraries could hit GB-scale log files.
>
> **Fix**: Add size-based rotation (e.g., keep last 100MB / 10K entries). Or time-based (keep 30 days).

**Estimate:** 0.5 day

---

### 4.3 Authors Field in SQLite ✅ 🟡 MEDIUM

> **Problem**: SQLite `papers.authors` is always `""`. Comment says "ZoteroItem doesn't expose authors as JSON" but `Item.authors` is `list[str]` — it's available.
>
> **Fix**: `json.dumps(item.authors)` when writing to SQLite in `_index_metadata()`.

**Estimate:** 5 minutes

---

### 4.4 Section Parent Linking ⬜ 🟡 MEDIUM

> **Problem**: `section_detector.py` computes `parent_idx` (subsection hierarchy) but SQLite `sections.parent_id` is always NULL. Subsections of Methods, etc. are flattened.
>
> **Fix**: Store parent-child relationships in SQLite during `_index_metadata()`.

**Estimate:** 0.5 day

---

### 4.5 Dead Parameters in sync_index ✅ 🟢 LOW

> **Problem**: `sync_index(chunk_size=800, chunk_overlap=120)` accepts parameters that the chunker ignores (uses its own `target_chunk_size=600` internally since v2).
>
> **Fix**: Remove dead parameters or add deprecation warning.

**Estimate:** 5 minutes

---

### 4.6 Docstring Bug ✅ 🟢 LOW

> **Problem**: `search_papers()` docstring says `diversity_weight=0.6`, actual default is `0.4` (grid-search tuned).
>
> **Fix**: Update docstring.

**Estimate:** 1 minute

---

### 4.7 BM25 Chinese Tokenization ⬜ 🟢 LOW

> **Problem**: Character bigrams work but jieba segmentation would be more accurate for Chinese BM25 queries.
>
> **Fix**: Optional jieba dependency, use if available, fall back to bigrams.

**Estimate:** 0.5 day

---

### 4.8 Personalized Re-Ranking ⬜ 🔵 FUTURE

> 继续保留为未来项；当前优先级低于评估可信度与双语消融控制。

### 4.9 Retrieval evaluation hardening + bilingual ablation control ✅

> **Completed**: evaluator reliability repaired (`eval_judge.py`, `recall_eval.py`, `evaluation.py`), deterministic 7-call strategy harness added (`run_strategy_eval.py` + `strategy_eval.py`), and index-time bilingual enrichment is now gated by `ZRA_INDEX_BILINGUAL_ENRICHMENT` for controlled ablation without changing default retrieval behavior.

### 4.10 Strategy relocation from docstring to skills ✅

> **Completed**: the 7-call weighted bilingual search and GraphRAG expansion strategies were extracted from the `search_papers` docstring into standalone skill files — `.claude/skills/bilingual-search/SKILL.md` and `.claude/skills/graph-expansion/SKILL.md`. The docstring was slimmed 115→59 lines (keeps the slot/weight table, merge rule, and compact example so non-skill MCP clients keep working) and now points to the skills. `tests/strategy_variants_7call.json` (the executable form used by `run_strategy_eval.py`) now cites the skill as its authoritative prose source, removing double maintenance of the strategy.

### 4.11 Skills served as MCP resources ✅

> **Completed**: `server.py` registers FastMCP's native `SkillsDirectoryProvider`, exposing `bilingual-search` and `graph-expansion` as MCP resources (`skill://<name>/SKILL.md` + `_manifest`) so skill-aware clients can load the full strategy on demand. Source checkouts use `.claude/skills`; wheel builds package the same files under `project_a_mcp/skills`. Directory is overridable via `ZRA_SKILLS_DIR`; missing directories / old FastMCP versions skip silently.

### 4.12 Component ablation harness 🟡 RUNNING

> `search_papers` gains `enable_semantic`/`enable_bm25`/`enable_rerank` switches; `run_recall_evaluation.py` gains `--ablation` / `--ablation-set` + per-language (zh/en) metric split. Purpose: quantify whether the EN-only Cross-Encoder helps or hurts Chinese queries, whether MMR trades recall, and each component's contribution. **Pending**: full run requires Zotero desktop running (`--ablation-set`); enrichment-switch ablation needs an index rebuilt with `ZRA_INDEX_BILINGUAL_ENRICHMENT=false`.

### 4.13 Column-aware extraction + extraction quality gate ✅

> `pdf.py` now clusters text lines into real columns (by start-x) and reads left → right column, top-to-bottom, fixing garbled interleaved two-column extraction. `ExtractionQuality` (scanned / garbled / fragmented) gates indexing: broken extractions are skipped and reported (`extraction_quality` counts in sync report) instead of silently indexed. No OCR added. CHUNKING_VERSION → v3.3.0-column-aware (auto rebuild). **Pending**: run `index_library.py --force` to rebuild and observe the quality counts; consider isolating the 64 legal documents in Zotero.

### 4.14 Verifiable index-build baseline ✅

> **Completed**: Chroma vectors, SQLite structured metadata, and the persisted
> BM25 corpus now share an `_index_manifest.json` build record with corpus-shaping
> runtime settings and observed chunk counts. `sync_index` writes `building`
> before mutations, deletes every store consistently for removals and updates,
> then marks the build `ready` only when all three counts agree. `Retriever`
> suppresses BM25 for `building` / `degraded` manifests; health checks expose
> legacy, incomplete, and count-mismatched states. CI covers this baseline on
> all branches. **Still pending**: build Chroma and SQLite in a staging location
> and atomically switch the active build; the current manifest detects and blocks
> mixed state but does not make a multi-store rebuild atomic.

---

> **Problem**: User engagement signals (annotations, reading depth, saved notes) are never used for ranking.
>
> **Fix**: Light boost for papers with user annotations/notes. Available from Zotero local API.

**Estimate:** 1 day

---

## Summary

| Phase | # Tasks | Completed | Remaining |
|-------|---------|-----------|-----------|
| Phase 0 (Audit) | 5 | 5 | 0 |
| Phase 1 (P0) | 3 | 3 | 0 |
| Phase 2 (P1) | 5 | 4 | 1 (deferred) |
| Phase 3 (P2) | 5 | 1 | 1 superseded, 3 deferred |
| Phase 4 (Hardening) | 14 | 10 | 4 |
| **Total** | **32** | **23** | **1 superseded, 8 deferred** |

### Immediate Next Steps

```
→ Implement staging-index build and atomic active-build switching
→ Add deterministic MCP contract tests and run them in CI
→ Calibrate the no-answer evaluation judge with a sanitized fixture corpus
→ Implement section parent linking before relying on nested-section expansion
```
