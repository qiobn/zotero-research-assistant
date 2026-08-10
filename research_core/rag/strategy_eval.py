"""Deterministic multi-call strategy helpers for retrieval evaluation.

These helpers let us evaluate documented multi-call search strategies (for
example the 7-call weighted bilingual strategy) without pushing any of that
logic back into the MCP server. The server still exposes single-call retrieval;
this module only orchestrates repeatable experiments around it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from research_core.rag.query_rewriter import validate

if TYPE_CHECKING:
    from research_core.rag.retriever import Retriever
    from research_core.tools.search import PaperHit
    from research_core.zotero.client import ZoteroClient


SLOT_WEIGHTS: dict[str, int] = {
    "A": 3,
    "B": 3,
    "C": 2,
    "D": 2,
    "E": 1,
    "F": 1,
    "G": 1,
}
_SLOT_ORDER = {slot: idx for idx, slot in enumerate(SLOT_WEIGHTS)}
_DEFAULT_STRATEGY = "7call_rrf"


@dataclass
class StrategyVariant:
    """One deterministic query variant in a multi-call strategy."""

    slot: str
    query: str
    weight: int
    note: str = ""


@dataclass
class StrategyPlan:
    """Exact query variants for one evaluation query."""

    query_id: str
    query_text: str = ""
    strategy: str = _DEFAULT_STRATEGY
    note: str = ""
    variants: list[StrategyVariant] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_text": self.query_text,
            "strategy": self.strategy,
            "note": self.note,
            "variants": {
                v.slot: {
                    "query": v.query,
                    "weight": v.weight,
                    "note": v.note,
                }
                for v in self.variants
            },
        }


@dataclass
class StrategyExecutionResult:
    """Merged retrieval results plus per-slot trace metadata."""

    merged_hits: list[PaperHit]
    slot_queries: dict[str, str]
    slot_hit_counts: dict[str, int]
    merged_trace: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_queries": dict(self.slot_queries),
            "slot_hit_counts": dict(self.slot_hit_counts),
            "merged_trace": list(self.merged_trace),
        }


def load_strategy_plans(path: str) -> dict[str, StrategyPlan]:
    """Load strategy plans from a JSON file.

    Supported shape:

    {
      "_meta": {...},
      "plans": [
        {
          "query_id": "Q004",
          "query_text": "...",
          "strategy": "7call_rrf",
          "note": "optional",
          "variants": {
            "A": "...",
            "B": {"query": "...", "weight": 3, "note": "optional"}
          }
        }
      ]
    }
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    plan_entries = data.get("plans", data)
    if isinstance(plan_entries, dict):
        plan_entries = [
            {"query_id": query_id, **entry}
            for query_id, entry in plan_entries.items()
        ]
    if not isinstance(plan_entries, list):
        raise ValueError("Strategy plan file must contain a 'plans' list or mapping")

    plans: dict[str, StrategyPlan] = {}
    for entry in plan_entries:
        query_id = str(entry.get("query_id", "")).strip()
        if not query_id:
            raise ValueError("Each strategy plan needs a non-empty query_id")
        if query_id in plans:
            raise ValueError(f"Duplicate strategy plan for query_id={query_id}")

        raw_variants = entry.get("variants", {})
        variants: list[StrategyVariant] = []
        for slot, raw_value in raw_variants.items():
            slot = str(slot).strip().upper()
            if slot not in SLOT_WEIGHTS:
                raise ValueError(f"Unsupported strategy slot: {slot}")

            if isinstance(raw_value, str):
                query = raw_value.strip()
                weight = SLOT_WEIGHTS[slot]
                note = ""
            elif isinstance(raw_value, dict):
                query = str(raw_value.get("query", "")).strip()
                weight = int(raw_value.get("weight", SLOT_WEIGHTS[slot]))
                note = str(raw_value.get("note", "")).strip()
            else:
                raise ValueError(
                    f"Variant {slot} for query_id={query_id} must be a string or object"
                )

            err = validate(query)
            if err:
                raise ValueError(
                    f"Variant {slot} for query_id={query_id} is invalid: {err}"
                )
            variants.append(StrategyVariant(slot=slot, query=query, weight=weight, note=note))

        variants.sort(key=lambda v: _SLOT_ORDER[v.slot])
        plans[query_id] = StrategyPlan(
            query_id=query_id,
            query_text=str(entry.get("query_text", "")).strip(),
            strategy=str(entry.get("strategy", _DEFAULT_STRATEGY)).strip() or _DEFAULT_STRATEGY,
            note=str(entry.get("note", "")).strip(),
            variants=variants,
        )

    return plans


def execute_strategy_plan(
    plan: StrategyPlan,
    *,
    zot: ZoteroClient,
    retriever: Retriever,
    limit: int = 50,
) -> StrategyExecutionResult:
    """Run a deterministic multi-call plan and merge the results."""
    from research_core.tools.search import search_papers

    slot_hits: dict[str, list[PaperHit]] = {}
    slot_queries: dict[str, str] = {}
    slot_hit_counts: dict[str, int] = {}

    for variant in plan.variants:
        hits = search_papers(
            query=variant.query,
            zot=zot,
            retriever=retriever,
            limit=limit,
            expand_context=False,
            expand_neighbors=False,
            diversity_weight=0.4,
        )
        slot_hits[variant.slot] = hits
        slot_queries[variant.slot] = variant.query
        slot_hit_counts[variant.slot] = len(hits)

    merged_hits, merged_trace = merge_weighted_hits(slot_hits, limit=limit)
    return StrategyExecutionResult(
        merged_hits=merged_hits,
        slot_queries=slot_queries,
        slot_hit_counts=slot_hit_counts,
        merged_trace=merged_trace,
    )


def merge_weighted_hits(
    slot_hits: dict[str, list[PaperHit]],
    *,
    limit: int = 50,
    max_bonus: int = 4,
) -> tuple[list[PaperHit], list[dict[str, Any]]]:
    """Merge per-slot retrieval results with slot weights + overlap bonus."""
    candidate_meta: dict[str, dict[str, Any]] = {}

    for slot, hits in slot_hits.items():
        if slot not in SLOT_WEIGHTS:
            raise ValueError(f"Unsupported strategy slot: {slot}")
        slot_weight = SLOT_WEIGHTS[slot]
        seen_keys: set[str] = set()

        for rank, hit in enumerate(hits, start=1):
            if hit.key in seen_keys:
                continue
            seen_keys.add(hit.key)

            meta = candidate_meta.setdefault(
                hit.key,
                {
                    "representative": hit,
                    "representative_weight": slot_weight,
                    "representative_rank": rank,
                    "raw_score": 0,
                    "best_rank": rank,
                    "appearances": 0,
                    "slots": [],
                    "slot_ranks": {},
                },
            )
            meta["raw_score"] += slot_weight
            meta["appearances"] += 1
            meta["best_rank"] = min(meta["best_rank"], rank)
            meta["slots"].append(slot)
            meta["slot_ranks"][slot] = rank

            if (
                slot_weight > meta["representative_weight"]
                or (
                    slot_weight == meta["representative_weight"]
                    and rank < meta["representative_rank"]
                )
            ):
                meta["representative"] = hit
                meta["representative_weight"] = slot_weight
                meta["representative_rank"] = rank

    merged_hits: list[PaperHit] = []
    merged_trace: list[dict[str, Any]] = []

    for key, meta in candidate_meta.items():
        slots = sorted(meta["slots"], key=lambda slot: _SLOT_ORDER[slot])
        bonus = min(max(meta["appearances"] - 1, 0), max_bonus)
        final_score = meta["raw_score"] + bonus
        rep = replace(
            meta["representative"],
            score=round(final_score, 4),
            source=f"strategy:{''.join(slots)}",
        )
        merged_hits.append(rep)
        merged_trace.append(
            {
                "key": key,
                "title": rep.title,
                "score": round(final_score, 4),
                "raw_score": meta["raw_score"],
                "bonus": bonus,
                "appearances": meta["appearances"],
                "best_rank": meta["best_rank"],
                "slots": slots,
                "slot_ranks": dict(meta["slot_ranks"]),
            }
        )

    merged_hits.sort(
        key=lambda hit: (
            -next(t["score"] for t in merged_trace if t["key"] == hit.key),
            next(t["best_rank"] for t in merged_trace if t["key"] == hit.key),
            -next(t["appearances"] for t in merged_trace if t["key"] == hit.key),
            hit.key,
        )
    )
    merged_trace.sort(
        key=lambda item: (-item["score"], item["best_rank"], -item["appearances"], item["key"])
    )
    return merged_hits[:limit], merged_trace[:limit]


def default_output_path(project_root: str, variants_path: str, judge_model: str, pool_size: int) -> str:
    """Build the default JSON output path for a strategy evaluation run."""
    variants_stem = Path(variants_path).stem
    model_slug = judge_model.replace("/", "-").replace(".", "-")
    return str(
        Path(project_root)
        / "tests"
        / "eval_results"
        / f"strategy_{variants_stem}_{model_slug}_pool{pool_size}.json"
    )
