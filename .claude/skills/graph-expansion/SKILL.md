---
name: graph-expansion
description: "Expand retrieval around seed papers using existing Zotero MCP tools (find_similar_papers, tag search, expand_citation_network) after multi-angle search. Use for cross-document/relationship queries, comprehensive review, or when initial search_papers returns <10 results; skip for single-concept/title matches."
---

# Graph Expansion — Seed-First Literature Expansion

After the 5-7 multi-angle `search_papers` calls (see the `bilingual-search`
skill), expand around the top seed papers using tools that search by *identity
and relationship* rather than by keywords. This recovers papers keyword search
misses.

Measured effect (tests/eval_results/RECALL_EVALUATION_LOG.md): individual
cross-document queries improved R@10 by +33-60pp (Q016, Q017); it is **additive,
not an alternative** — it does not replace multi-angle search.

## Step 1 — Seed discovery

From the weight-merged results of the multi-angle search, pick the **top 3-5
most promising papers**. "Promising" = high merge score (appeared in multiple
angles) or directly on-topic title/abstract.

## Step 2 — Graph expansion (per seed paper)

For each seed, run the applicable ones:

1. `find_similar_papers(seed_key, limit=10)` — vector-based similar content;
   finds papers missed by keyword search.
2. `search_papers("", tags_include=[...])` — with the seed paper's KEY TAGS
   (e.g. `tags_include=["两步移动搜索法", "可达性"]`), empty-query filter mode.
3. `expand_citation_network(seed_doi)` — forward/backward citations via
   OpenAlex; finds papers that cite / are cited by the seed.

## Step 3 — Merge

Pool everything from Steps 1 + 2, sort by **appearance frequency** (papers
reached via 3+ expansion paths rank highest), remove duplicates, present to user.

## When to use

- Cross-document / relationship queries (need broad coverage)
- User explicitly asks for comprehensive literature review
- Initial `search_papers` returns <10 results

## When NOT to use

- Single-concept or title-match queries (overkill, adds latency)
- User is in a hurry and wants quick results
