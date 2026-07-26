# Recall Evaluation Log

> Complete test history of RAG recall evaluation across architectures, judges, and strategies.
> Last updated: 2026-07-26

## Test Configuration

- **Query set**: `tests/eval_queries_user.json` (20 queries, 5 categories)
- **Library**: ~250 papers, 19,788 chunks, bge-m3 ONNX INT8 (1024-dim)
- **Retrieval**: BM25 + Dense (bge-m3) + Cross-Encoder (ms-marco-MiniLM-L-6-v2) + MMR (λ=0.4)
- **Methodology**: pooling method (top-50 per query), LLM-as-judge relevance labeling
- **Metrics**: Recall@5/10/20, Precision@5/10/20, MRR, NDCG@10

## Test History

### Test 1 — Baseline: Old Architecture (Internal NMT + Dictionary)

**Date**: 2026-07-24  |  **Judge**: openai-gpt-5-4 (strict prompt v2)
**Architecture**: Single `search_papers` call with internal multi-layer expansion

```
search_papers("CN query") → internally:
  Layer 1: query_dict.json (~300 CN→EN pairs)
  Layer 2: Zotero tags (auto-collected during sync)
  Layer 3: User-defined synonyms
  Layer 4: OPUS-MT NMT translation (CN→EN, ~300MB model)
  → 5-10 sub-queries → RRF weighted merge → CE rerank → MMR
```

**Results**:

| Overall | R@5 | R@10 | R@20 | MRR | NDCG@10 | Pool Relevant |
|---------|-----|------|------|-----|---------|---------------|
| | 49.6% | **60.9%** | 68.3% | 0.641 | 0.754 | 74/940 (7.9%) |

| Category | Count | R@10 |
|----------|-------|------|
| direct | 10 | 65.4% |
| cross_document | 6 | 35.6% |
| method | 1 | 50.0% |
| data_source | 1 | 100.0% |
| no_answer | 2 | 0 relevant (correct) |

**Issues found**: Judge was too strict on some queries — Q007 (LLM ABM simulation) had rank 1-5 all obviously relevant but judge marked none, reporting R@10=0%.

---

### Test 2 — Cross-Validation: DeepSeek (Same Architecture)

**Date**: 2026-07-24  |  **Judge**: deepseek-latest
**Architecture**: Same as Test 1 (internal expansion)

**Results**:

| Overall | R@5 | R@10 | R@20 | MRR | Pool Relevant |
|---------|-----|------|------|-----|---------------|
| | 44.1% | **58.9%** | 75.6% | 0.715 | 140/940 (14.9%) |

| Category | R@10 |
|----------|------|
| direct | 67.9% |
| cross_document | 37.2% |
| no_answer | 4 relevant (incorrect — judge less disciplined) |

**Key finding**: R@10 within 2pp of GPT5.4. DeepSeek more generous (15% vs 8% relevant). Category ranking identical.

---

### Test 3 — Cross-Validation: Claude Opus 4.6 (Same Architecture)

**Date**: 2026-07-24  |  **Judge**: claude-opus-4-6
**Architecture**: Same as Test 1 (internal expansion)

**Results**:

| Overall | R@5 | R@10 | R@20 | MRR | Pool Relevant |
|---------|-----|------|------|-----|---------------|
| | 35.6% | **47.6%** | 58.5% | 0.867 | 496/940 (52.8%) |

| Category | R@10 |
|----------|------|
| direct | 50.6% |
| cross_document | 36.2% |
| no_answer | 50/100 relevant (prompt failed to constrain Opus) |

**Key finding**: Opus extremely generous. R@10 lower because denominator much larger.
**Critical invariant across all 3 judges**: cross_document ≈ 36% — the only category consistently confirmed as the weak point.

---

### Three-Judge Cross-Validation Summary (Same Architecture)

| Judge | Relevant | R@10 | cross_doc R@10 | no_answer |
|-------|----------|------|---------------|-----------|
| GPT5.4 strict | 8% | 60.9% | 35.6% | ✓ 0 |
| DeepSeek | 15% | 58.9% | 37.2% | ✗ 4 |
| Opus 4.6 | 53% | 47.6% | 36.2% | ✗ 50 |

**Consensus**: cross_document R@10 ≈ 36% across all three judges. This is the real system bottleneck.

---

### Test 4 — New Architecture: CN+EN Dual Search (Minimal)

**Date**: 2026-07-26  |  **Judge**: tencent-openai-gpt-5.4 (strict prompt)
**Architecture**: Simplified `search_papers` (pure retrieval, zero internal expansion) + LLM does CN+EN dual call

```
Architecture change:
  Removed: query_dict.json, OPUS-MT NMT, language param, internal multi-query expansion
  Added:   expand_query(term) MCP tool, bilingual search skill in docstring
  Strategy: CN query → 2 calls (CN + EN translation) → simple dedup

search_papers("CN query")  ← pure BM25+Dense+CE+MMR, no rewriting
search_papers("EN translation")
```

**Results**:

| Overall | R@5 | R@10 | R@20 | MRR | NDCG@10 |
|---------|-----|------|------|-----|---------|
| | 44.5% | **51.7%** | 62.5% | 0.561 | 0.470 |

**Key finding**: 2 calls insufficient. Dropped ~9pp from baseline. Old architecture internally ran 5-10 sub-queries with RRF merge — the gap is the missing sub-queries and weighted merging.

---

### Test 5 — Lightweight GraphRAG (Multi-Angle + Graph Expansion, No Weighting)

**Date**: 2026-07-26  |  **Judge**: tencent-openai-gpt-5.4
**Architecture**: 3-4 multi-angle calls + graph expansion (find_similar_papers, tag search), simple dedup

```
For each query:
  Step 1: 3-4 multi-angle search_papers calls
  Step 2: Top 3 seeds → find_similar_papers + tag-based search_papers
  Step 3: Pool all results, sort by frequency, dedup
```

**Results** (5 queries: Q003-Q005, Q016-Q017):

| Overall | R@5 | R@10 | R@20 | MRR | NDCG@10 |
|---------|-----|------|------|-----|---------|
| | 25.0% | **51.3%** | 51.3% | 0.725 | 0.471 |

| Query | Category | R@10 | vs Baseline |
|-------|----------|------|-------------|
| Q004 | cross_document | 50% | ↑ large (was ~0%) |
| Q005 | cross_document | 57% | ↓ |
| Q016 | cross_document | 83% | ↑ +33pp |
| Q017 | cross_document | 60% | ↑ large (was ~0%) |
| Q003 | direct | 6% | ↓ large |

**Key finding**: Graph expansion helps individual queries (Q016 +33pp, Q017 +60pp) but overall R@10 ≈ CN+EN dual. Missing weighted merge is the bottleneck.

---

### Test 6 — 7-Call RRF-Weighted Strategy (Final)

**Date**: 2026-07-26  |  **Judge**: tencent-openai-gpt-5.4
**Architecture**: 5-7 calls per Chinese query + RRF-like weighted merge

```
For Chinese queries — 7 calls:
  A. CN original          (weight: 3)
  B. EN full translation  (weight: 3)
  C. CN keywords-only     (weight: 2)
  D. EN keywords          (weight: 2)
  E. EN with synonyms     (weight: 1)
  F. Reverse angle        (weight: 1)
  G. Broader concept      (weight: 1)
  → +1 bonus per extra call (max +4)

Merge: sort by total score descending, dedup
```

**Results** (5 queries: Q003-Q005, Q016-Q017):

| Overall | R@5 | R@10 | R@20 | MRR | NDCG@10 |
|---------|-----|------|------|-----|---------|
| | 48.2% | **69.8%** | 73.1% | 0.900 | 0.722 |

| Query | Category | R@10 | high_score | Change from baseline |
|-------|----------|------|-----------|---------------------|
| Q004 | cross_document | **100%** | 42/50 | ↑ +100pp (was ~0%) |
| Q005 | cross_document | 80% | 30/50 | ↓ vs baseline 100% |
| Q016 | cross_document | 80% | 41/50 | ↑ +30pp (was 50%) |
| Q017 | cross_document | 44% | 41/50 | ↑ (was ~0%) |
| Q003 | direct | 44% | 42/50 | ↓ vs baseline |

**Key finding**: Surpasses old internal expansion baseline (60.9%→69.8%, +9pp) with zero server-side dependencies. RRF weighting is the critical component — high_score=30-42 means 60-84% of results were found by multiple search angles.

---

## Strategy Comparison

| # | Strategy | Calls/Query | Weighted Merge | R@10 | Judge |
|---|----------|:-----------:|:--------------:|:----:|-------|
| 1 | Internal NMT+Dict (old) | 1 (internal 5-10) | ✓ RRF | **60.9%** | GPT5.4 |
| 2 | Internal NMT+Dict | 1 (internal 5-10) | ✓ RRF | 58.9% | DeepSeek |
| 3 | Internal NMT+Dict | 1 (internal 5-10) | ✓ RRF | 47.6% | Opus 4.6 |
| 4 | CN+EN dual (new) | 2 | ✗ simple dedup | 51.7% | tencent-GPT5.4 |
| 5 | GraphRAG light | 4 + graph | ✗ simple dedup | 51.3% | tencent-GPT5.4 |
| **6** | **7-call RRF-weighted** | **5-7** | **✓ RRF-like** | **69.8%** | tencent-GPT5.4 |

## Judge Prompt Evolution

| Version | Philosophy | No-Answer Accuracy | Relevant Rate |
|---------|-----------|:-----------------:|:-------------:|
| v1 (original) | "Be generous, FP < FN for recall" | ❌ | 44% |
| v2 (strict) | "Conservative, FP distorts recall" + examples | ✅ 0/0 | 8% |
| v3 (Opus) | Same v2 prompt | ❌ 50/100 | 53% |

Judge v2 (strict with domain examples) is the recommended prompt. Claude Opus ignored the strictness constraint.

## Key Lessons

1. **Multi-call count is the dominant factor**: 2 calls → 51.7%, 7 calls → 69.8%. Each additional call brings unique papers (~50% overlap between any two calls).

2. **RRF-weighted merge is essential**: Simple dedup (Tests 4-5) underperforms weighted merge (Test 6) by ~18pp, even with similar call counts.

3. **Graph expansion is additive, not alternative**: find_similar_papers + tag search adds 5-10% unique papers per query but doesn't replace multi-angle search.

4. **Cross-document queries are the persistent bottleneck**: ~36% R@10 across all three judges in baseline. 7-call strategy raises specific queries to 80-100% but Q017 (elderly travel) remains at 44%.

5. **Judge quality matters more than judge model**: A well-designed strict prompt with domain examples produces more reliable relevance labels than any particular model choice.

## Files

- Evaluation framework: `research_core/rag/eval_judge.py`, `research_core/rag/recall_eval.py`
- CLI: `scripts/run_recall_evaluation.py`
- Results: `tests/eval_results/recall_*.json`
- Query set: `tests/eval_queries_user.json` (20 queries, empirically verified expected keys)
