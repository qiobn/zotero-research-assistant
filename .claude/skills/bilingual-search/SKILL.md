---
name: bilingual-search
description: >
  How to run the mandatory 5-7 call weighted bilingual search against the Zotero
  MCP server. Use whenever the user asks a Chinese-language or mixed CN/EN query
  against their library (search_papers), especially cross-document / relationship
  queries, comprehensive literature review, or when a single call returns <10
  results. Skip for pure title/keyword matches or when the user wants a quick answer.
---

# Bilingual Search — Multi-Call Weighted Strategy

Single-vector embedding can only approximate one semantic direction. To match the
recall of a full search pipeline (dictionary + NMT + tags + decomposition), make
**5-7 `search_papers` calls per Chinese query** and weight-merge the results.
This is not optional — single-call or dual-call recall is measurably lower
(measurement history: tests/eval_results/RECALL_EVALUATION_LOG.md).

## The 7 slots

| Slot | Query | Weight |
|------|-------|:------:|
| A | CN original query text | 3 |
| B | Full EN academic translation | 3 |
| C | CN keywords-only (drop function words: 的/与/和/在/中) | 2 |
| D | EN keywords (translate the CN keywords) | 2 |
| E | EN keywords + synonyms from `expand_query()` | 1 |
| F | Reverse / complementary angle (e.g. "A对B的影响" → "B的影响因素 A") | 1 |
| G | Broader framing of the topic (optional) | 1 |

English-source queries may omit the CN slots; run whatever slots apply and merge
the ones you have.

## Merge (RRF-like weighting)

Pool all results from the slots, then score each paper:

```
score = 0
  +3  if found in slot A (CN original) or B (EN translation)
  +2  if found in slot C (CN keywords) or D (EN keywords)
  +1  if found in slot E (synonyms), F (reverse), or G (broad)
  +1  bonus per additional call it appears in (max +4)
```

Sort by score descending; break ties by best individual rank. Remove duplicates.
Present top 15-20.

Papers found by multiple angles are the most reliable signal — a high "appears in
N slots" count (high score) means the paper matched several independent
formulations of the query.

## Worked example

Query: "社区公共体育设施与居民健康满意度的关系"

- A. `search_papers("社区公共体育设施 居民 健康 满意度 关系")`
- B. `search_papers("community public sports facilities resident health satisfaction impact relationship")`
- C. `search_papers("公共体育设施 健康 满意度 影响 因素 居民")`
- D. `search_papers("public sports facilities health satisfaction wellbeing residents")`
- E. `expand_query("公共体育设施")` then `search_papers("public sports infrastructure community fitness facilities health outcomes")`
- F. `search_papers("居民 主观幸福感 社区体育设施 影响 因素")` — reverse angle
- G. `search_papers("community sports participation neighborhood wellbeing")` — broader

Weight-merge the 7 result sets, present top 20.

## Evaluation harness

The same strategy is encoded as machine-readable plans for deterministic
evaluation: `tests/strategy_variants_7call.json` (slot weights A/B=3, C/D=2,
E/F/G=1, +1 bonus per extra call, max +4). Run with
`python scripts/run_strategy_eval.py --variants tests/strategy_variants_7call.json`.
The JSON is the canonical executable form; this skill is the human-readable form.

## References

- `tests/eval_results/RECALL_EVALUATION_LOG.md` — full comparison history (7-call
  RRF beats internal NMT+dictionary baseline: R@10 60.9% → 69.8%; formal 20-query
  re-run: 40.8% → 56.3%, cross_document 15.9% → 55.2%)
