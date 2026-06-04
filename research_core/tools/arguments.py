"""Argument finder — locate supporting and opposing evidence for a claim.

Given a user's claim/thesis, searches the library for relevant passages and
classifies each by likely stance (support/oppose/neutral) using textual signals.
The AI then makes the final determination and synthesizes the evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from research_core.rag.reranker import get_reranker
from research_core.rag.retriever import RetrievalResult, Retriever
from research_core.zotero.client import ZoteroClient

# Stance signal keywords (heuristic pre-classification)
_SUPPORT_SIGNALS = re.compile(
    r"\b(confirm|support|consistent with|in line with|corroborat|"
    r"demonstrat|evidence for|shows that|found that|suggest that|"
    r"positively associated|significant.{0,20}relationship|"
    r"验证|支持|表明|证实|一致)\b",
    re.IGNORECASE,
)

_OPPOSE_SIGNALS = re.compile(
    r"\b(however|contrary|contradict|inconsistent|challenge|"
    r"no significant|failed to|did not find|unlike|in contrast|"
    r"negatively associated|limitation|critique|debat|"
    r"然而|相反|不一致|质疑|局限|未发现)\b",
    re.IGNORECASE,
)

_NEUTRAL_SIGNALS = re.compile(
    r"\b(review|overview|framework|define|categoriz|typolog|"
    r"literature|previous stud|existing research|"
    r"综述|回顾|框架|定义|分类)\b",
    re.IGNORECASE,
)


@dataclass
class ArgumentEvidence:
    item_key: str
    title: str
    authors: list[str]
    year: int
    text: str
    page: int
    relevance: float
    stance_hint: str  # "support" | "oppose" | "neutral"
    stance_signals: list[str] = field(default_factory=list)


def _detect_stance(text: str) -> tuple[str, list[str]]:
    """Heuristic stance detection from passage text.

    Returns (stance_hint, matched_signals). The AI should use this as a
    starting point but make the final judgment based on full context.
    """
    support_matches = _SUPPORT_SIGNALS.findall(text)
    oppose_matches = _OPPOSE_SIGNALS.findall(text)
    neutral_matches = _NEUTRAL_SIGNALS.findall(text)

    support_score = len(support_matches)
    oppose_score = len(oppose_matches)
    neutral_score = len(neutral_matches)

    signals: list[str] = []
    if support_matches:
        signals.extend(f"+{m}" for m in support_matches[:3])
    if oppose_matches:
        signals.extend(f"-{m}" for m in oppose_matches[:3])

    if oppose_score > support_score and oppose_score > neutral_score:
        return "oppose", signals
    if support_score > oppose_score and support_score > neutral_score:
        return "support", signals
    if neutral_score > 0 and support_score == 0 and oppose_score == 0:
        return "neutral", signals

    # Ambiguous — let AI decide
    return "neutral", signals


def find_arguments(
    *,
    claim: str,
    retriever: Retriever,
    zot: ZoteroClient,
    top_k: int = 10,
    item_keys: list[str] | None = None,
) -> dict:
    """Find evidence that supports or opposes a given claim/thesis.

    Searches the library for passages relevant to the claim, then classifies
    each by likely stance using textual signal analysis. Returns passages
    grouped by stance (support/oppose/neutral) with citations.

    Args:
        claim: The thesis or argument to find evidence for/against.
        retriever: Vector store retriever.
        zot: Zotero client.
        top_k: Max total evidence passages to return.
        item_keys: Optional — restrict search to specific papers.

    Returns:
        Dict with grouped evidence, ready for the AI to synthesize into
        a balanced Discussion section.
    """
    if not claim or not claim.strip():
        return {"error": "No claim provided."}

    # Search for relevant passages
    n_search = max(top_k * 10, 50)
    if item_keys:
        # Search within specific papers
        all_results: list[RetrievalResult] = []
        per_paper = max(n_search // len(item_keys), 10)
        for key in item_keys:
            results = retriever.search_within_item(key, claim, n_results=per_paper)
            all_results.extend(results)
    else:
        all_results = retriever.search(claim, n_results=n_search)

    # Rerank for better relevance
    reranker = get_reranker()
    if reranker and all_results:
        docs = [r.text for r in all_results]
        reranked = reranker.rerank(claim, docs, top_k=min(top_k * 3, len(all_results)))
        all_results = [all_results[idx] for idx, _ in reranked]

    # Deduplicate by paper (keep best passage per paper)
    best_per_paper: dict[str, RetrievalResult] = {}
    for r in all_results:
        existing = best_per_paper.get(r.item_key)
        if existing is None or r.score > existing.score:
            best_per_paper[r.item_key] = r

    ordered = sorted(best_per_paper.values(), key=lambda r: r.score, reverse=True)[:top_k]

    if not ordered:
        return {
            "claim": claim,
            "error": "[MATERIAL GAP] No relevant evidence found in the library for this claim. "
                     "Try broadening the claim or adding more papers to your library.",
        }

    # Fetch paper metadata
    items = zot.get_items_batch([r.item_key for r in ordered])
    items_by_key = {it.key: it for it in items}

    # Classify stance and build evidence list
    evidence_list: list[ArgumentEvidence] = []
    for r in ordered:
        item = items_by_key.get(r.item_key)
        stance_hint, signals = _detect_stance(r.text)
        evidence_list.append(ArgumentEvidence(
            item_key=r.item_key,
            title=item.title if item else r.title,
            authors=item.authors if item else [],
            year=ZoteroClient.parse_year(item.date) if item else 0,
            text=r.text[:500],
            page=r.page_start,
            relevance=round(r.score, 3),
            stance_hint=stance_hint,
            stance_signals=signals,
        ))

    # Group by stance
    support = [e for e in evidence_list if e.stance_hint == "support"]
    oppose = [e for e in evidence_list if e.stance_hint == "oppose"]
    neutral = [e for e in evidence_list if e.stance_hint == "neutral"]

    def _serialize(e: ArgumentEvidence) -> dict:
        authors = e.authors
        first_author = authors[0].split()[-1] if authors else "Unknown"
        if len(authors) > 2:
            citation = f"({first_author} et al., {e.year}, p.{e.page})"
        elif len(authors) == 2:
            citation = f"({first_author} & {authors[1].split()[-1]}, {e.year}, p.{e.page})"
        else:
            citation = f"({first_author}, {e.year}, p.{e.page})"
        return {
            "item_key": e.item_key,
            "title": e.title,
            "citation": citation,
            "text": e.text,
            "page": e.page,
            "relevance": e.relevance,
            "stance_hint": e.stance_hint,
            "stance_signals": e.stance_signals,
        }

    return {
        "claim": claim,
        "total_evidence": len(evidence_list),
        "supporting": [_serialize(e) for e in support],
        "opposing": [_serialize(e) for e in oppose],
        "neutral": [_serialize(e) for e in neutral],
        "support_count": len(support),
        "oppose_count": len(oppose),
        "neutral_count": len(neutral),
        "synthesis_instruction": (
            "ARGUMENT SYNTHESIS GUIDELINES:\n"
            "1. The stance_hint is a HEURISTIC pre-classification based on keyword signals. "
            "You MUST read the full passage text and make your own judgment — the hint may be wrong.\n"
            "2. For the Discussion section: present supporting evidence first, then counterarguments, "
            "then explain how the user's work addresses or reconciles the tension.\n"
            "3. Use hedging language appropriately: 'This aligns with...' / 'In contrast, ... found...'\n"
            "4. If all evidence is neutral, interpret it as 'the literature provides context but "
            "no direct test of this specific claim — the user's contribution fills this gap.'"
        ),
    }
