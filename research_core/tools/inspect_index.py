"""Index inspection, quality diagnostics, and recall testing."""

from __future__ import annotations

import re
from collections import Counter

from research_core.parsers.chunker import CHUNKING_VERSION
from research_core.rag.retriever import Retriever
from research_core.zotero.client import ZoteroClient


def inspect_index(
    *,
    retriever: Retriever,
    item_key: str | None = None,
) -> dict:
    """Inspect the vector index quality and content.

    Global mode (item_key=None): returns aggregate stats and problem detection.
    Per-paper mode (item_key given): returns detailed chunk breakdown.
    """
    if item_key:
        return _inspect_single_paper(retriever, item_key)
    return _inspect_global(retriever)


def _inspect_global(retriever: Retriever) -> dict:
    """Aggregate index statistics and problem detection.

    Uses paginated reads to avoid loading entire index into memory.
    """
    total_count = retriever.count()
    if total_count == 0:
        return {
            "status": "empty",
            "total_chunks": 0,
            "total_papers": 0,
            "message": (
                "索引为空，请先执行 sync_index。"
                " / Index is empty. Run sync_index first."
            ),
            "chunking_version": CHUNKING_VERSION,
        }

    papers: dict[str, list[int]] = {}
    section_counts: Counter = Counter()
    chunk_lengths: list[int] = []
    issues: list[str] = []
    figure_table_count = 0

    _PAGE_SIZE = 1000
    offset = 0
    while offset < total_count:
        raw = retriever._collection.get(
            include=["documents", "metadatas"],
            limit=_PAGE_SIZE,
            offset=offset,
        )
        docs = raw.get("documents", []) or []
        metas = raw.get("metadatas", []) or []
        if not docs:
            break

        for doc, meta in zip(docs, metas, strict=True):
            key = meta.get("item_key", "unknown")
            papers.setdefault(key, []).append(len(doc))
            chunk_lengths.append(len(doc))
            section_counts[meta.get("section", "content")] += 1
            if meta.get("has_figure_table"):
                figure_table_count += 1

            if len(doc) < 50:
                issues.append(
                    f"{key}: chunk too short ({len(doc)} chars)"
                )
            if _is_garbled(doc):
                issues.append(f"{key}: possible garbled text")

        offset += len(docs)

    papers_with_few_chunks = [
        f"{k} ({len(v)} chunks)"
        for k, v in papers.items()
        if len(v) <= 1
    ]
    if papers_with_few_chunks:
        for p in papers_with_few_chunks[:5]:
            issues.append(f"Very few chunks: {p}")

    return {
        "status": "ok" if not issues else "has_issues",
        "chunking_version": CHUNKING_VERSION,
        "total_chunks": total_count,
        "total_papers": len(papers),
        "section_breakdown": dict(section_counts),
        "figure_table_chunks": figure_table_count,
        "chunk_stats": {
            "avg_length": round(
                sum(chunk_lengths) / len(chunk_lengths)
            ),
            "min_length": min(chunk_lengths),
            "max_length": max(chunk_lengths),
            "median_length": sorted(chunk_lengths)[
                len(chunk_lengths) // 2
            ],
        },
        "avg_chunks_per_paper": round(
            total_count / len(papers), 1
        ) if papers else 0,
        "papers_with_issues": len(issues),
        "issues": issues[:15],
        "top_papers_by_chunks": _top_papers(papers, n=5),
    }


def _inspect_single_paper(
    retriever: Retriever, item_key: str
) -> dict:
    """Detailed chunk breakdown for one paper."""
    chunks = retriever.get_item_chunks(item_key)
    if not chunks:
        return {
            "status": "not_found",
            "item_key": item_key,
            "message": (
                f"论文 {item_key} 未在索引中找到。"
                f" / Paper {item_key} not found in index."
            ),
        }

    chunk_details: list[dict] = []
    for c in chunks:
        section = c.metadata.get("section", "content")
        detail: dict = {
            "chunk_idx": c.chunk_idx,
            "pages": f"{c.page_start}-{c.page_end}",
            "length": len(c.text),
            "section": section,
            "preview": c.text[:80] + ("..." if len(c.text) > 80 else ""),
        }
        if c.metadata.get("has_figure_table"):
            detail["has_figure_table"] = True
        chunk_details.append(detail)

    lengths = [len(c.text) for c in chunks]
    ref_chunks = sum(
        1 for c in chunks
        if c.metadata.get("section") == "references"
    )
    fig_tab_chunks = sum(
        1 for c in chunks
        if c.metadata.get("has_figure_table")
    )

    return {
        "status": "ok",
        "item_key": item_key,
        "title": chunks[0].title if chunks else "",
        "total_chunks": len(chunks),
        "content_chunks": len(chunks) - ref_chunks,
        "reference_chunks": ref_chunks,
        "figure_table_chunks": fig_tab_chunks,
        "page_coverage": f"{chunks[0].page_start}-{chunks[-1].page_end}",
        "chunk_stats": {
            "avg_length": round(sum(lengths) / len(lengths)),
            "min_length": min(lengths),
            "max_length": max(lengths),
        },
        "chunks": chunk_details,
        "chunking_version": CHUNKING_VERSION,
    }


def _top_papers(
    papers: dict[str, list[int]], n: int = 5
) -> list[dict]:
    """Return top N papers by chunk count."""
    sorted_papers = sorted(
        papers.items(), key=lambda x: len(x[1]), reverse=True
    )
    return [
        {
            "item_key": k,
            "chunk_count": len(v),
            "total_chars": sum(v),
        }
        for k, v in sorted_papers[:n]
    ]


_GARBLE_RE = re.compile(
    r"[^\x00-\x7F\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]"
)


def _is_garbled(text: str) -> bool:
    """Heuristic: text is garbled if >40% non-ASCII non-Chinese chars."""
    if len(text) < 20:
        return False
    unusual = len(_GARBLE_RE.findall(text))
    return unusual / len(text) > 0.4


# ── Recall Testing ────────────────────────────────────────────────


def test_recall(
    *,
    retriever: Retriever,
    zot: ZoteroClient,
    item_key: str,
) -> dict:
    """Test whether a paper's chunks can be retrieved by its own title.

    Searches using the paper's title (and abstract snippet if available)
    and checks if the paper's own chunks appear in the top results.
    """
    try:
        item = zot.get_item(item_key)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Cannot find item {item_key}: {e}",
        }

    query = item.title
    if hasattr(item, "abstract") and item.abstract:
        query += " " + item.abstract[:200]

    results = retriever.search(
        query, n_results=20, include_references=True
    )

    own_chunks = [r for r in results if r.item_key == item_key]

    indexed_chunks = retriever.get_item_chunks(item_key)
    total_indexed = len(indexed_chunks)

    if total_indexed == 0:
        return {
            "status": "not_indexed",
            "item_key": item_key,
            "title": item.title,
            "message": (
                "该论文未被索引（无 chunk）。请执行 sync_index。"
                " / Paper not indexed. Run sync_index."
            ),
        }

    recall_at_20 = len(own_chunks) / total_indexed
    best_rank = None
    for i, r in enumerate(results):
        if r.item_key == item_key:
            best_rank = i + 1
            break

    if not own_chunks:
        status = "fail"
        message = (
            "检索失败：用论文标题搜索 top-20 中没有命中自身。"
            " / FAIL: paper's own chunks not found in top-20. "
            "Possible cause: poor chunking, garbled PDF, "
            "or embedding mismatch."
        )
    elif recall_at_20 >= 0.5:
        status = "good"
        message = (
            f"召回良好：top-20 中命中 {len(own_chunks)}/"
            f"{total_indexed} chunks (recall={recall_at_20:.0%})."
        )
    else:
        status = "partial"
        message = (
            f"部分召回：top-20 中仅命中 {len(own_chunks)}/"
            f"{total_indexed} chunks (recall={recall_at_20:.0%})."
            " 部分内容可能检索不到。"
        )

    return {
        "status": status,
        "item_key": item_key,
        "title": item.title,
        "message": message,
        "total_indexed_chunks": total_indexed,
        "chunks_found_in_top20": len(own_chunks),
        "recall_at_20": round(recall_at_20, 3),
        "best_rank": best_rank,
        "top_match_score": round(
            own_chunks[0].score, 3
        ) if own_chunks else None,
    }
