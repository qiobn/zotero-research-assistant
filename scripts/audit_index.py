"""Full-library quality audit — a comprehensive health report for your RAG index.

Usage:
    python scripts/audit_index.py              # full audit
    python scripts/audit_index.py --json       # machine-readable output
    python scripts/audit_index.py --top-n 20   # show more problem papers

Requires: Zotero desktop running (localhost:23119) and an existing ChromaDB index.

This script extends the built-in ``inspect_index`` MCP tool with deeper analysis:
- Per-paper chunk quality scoring
- Cross-paper noise pattern detection (repeated headers, watermarks)
- Coverage gap analysis (library papers missing from index)
- Embedding separation metrics (intra vs inter paper similarity)
- Actionable fix recommendations ranked by impact
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_core.parsers.chunker import CHUNKING_VERSION
from research_core.rag.embedding import get_embedding_function
from research_core.rag.retriever import Retriever
from research_core.zotero.client import ZoteroClient


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class PaperQuality:
    item_key: str
    title: str = ""
    year: int = 0
    total_chunks: int = 0
    total_chars: int = 0
    avg_chunk_length: float = 0.0
    min_chunk_length: int = 0
    max_chunk_length: int = 0
    content_chunks: int = 0
    reference_chunks: int = 0
    figure_table_chunks: int = 0
    short_chunk_count: int = 0  # < 50 chars
    long_chunk_count: int = 0  # > 1200 chars
    garbled_chunk_count: int = 0
    quality_score: float = 0.0  # 0–100, higher = better
    issues: list[str] = field(default_factory=list)


@dataclass
class NoisePattern:
    text: str
    occurrence_count: int
    papers_affected: set[str] = field(default_factory=set)
    likely_type: str = ""  # header / footer / watermark / page_number


@dataclass
class AuditReport:
    # Meta
    chunking_version: str = ""
    embedding_model: str = ""

    # Overview
    total_chunks: int = 0
    total_papers_indexed: int = 0
    total_papers_in_library: int = 0
    papers_with_pdf: int = 0
    papers_missing_from_index: int = 0

    # Chunk stats
    avg_chunk_length: float = 0.0
    median_chunk_length: int = 0
    min_chunk_length: int = 0
    max_chunk_length: int = 0
    short_chunks: int = 0  # < 50 chars
    long_chunks: int = 0  # > 1500 chars
    garbled_chunks: int = 0
    avg_chunks_per_paper: float = 0.0

    # Section breakdown
    content_chunks: int = 0
    reference_chunks: int = 0
    figure_table_chunks: int = 0

    # PDF quality distribution
    papers_with_extraction_issues: int = 0  # < 200 chars total
    papers_single_chunk: int = 0  # only 1 chunk (suspicious)
    papers_empty_extraction: int = 0  # 0 chars extracted

    # Noise
    noise_patterns: list[NoisePattern] = field(default_factory=list)

    # Per-paper quality
    paper_qualities: list[PaperQuality] = field(default_factory=list)
    top_problem_papers: list[PaperQuality] = field(default_factory=list)
    top_healthy_papers: list[PaperQuality] = field(default_factory=list)

    # Embedding separation
    embedding_dim: int = 0
    intra_paper_similarity: float = 0.0  # avg cosine sim within same paper
    inter_paper_similarity: float = 0.0  # avg cosine sim across different papers
    separation_ratio: float = 0.0  # intra / inter (higher = better separation)

    # Recommendations
    recommendations: list[dict] = field(default_factory=list)

    # Timestamp
    audit_timestamp: str = ""


# ── Core audit logic ──────────────────────────────────────────────────


_GARBLE_RE = re.compile(
    r"[^\x00-\x7F一-鿿　-〿＀-￯]"
)

# Common journal header/footer patterns (case-insensitive fragments)
_HEADER_FOOTER_CANDIDATES = [
    re.compile(r"^\d+\s*$", re.MULTILINE),                      # standalone page numbers
    re.compile(r"^[A-Z][a-z]+ \d{1,2},? \d{4}$", re.MULTILINE),  # "January 15, 2024"
    re.compile(r"^Vol(ume)?\.?\s*\d+", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^pp?\.\s*\d+", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^ISSN[:\s]\s*\d", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^DOI[:\s]\s*10\.", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^©\s*\d{4}", re.MULTILINE),
    re.compile(r"^Published (online|by)[:\s]", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^https?://", re.MULTILINE),
    re.compile(r"^Downloaded from", re.MULTILINE | re.IGNORECASE),
]


def _is_garbled(text: str) -> bool:
    """>40% non-ASCII non-CJK chars → likely garbled."""
    if len(text) < 20:
        return False
    unusual = len(_GARBLE_RE.findall(text))
    return unusual / len(text) > 0.4


def _compute_quality_score(pq: PaperQuality) -> float:
    """Simple heuristic quality score for one paper's chunks."""
    if pq.total_chunks == 0:
        return 0.0
    score = 100.0

    # Penalty: very few chunks from a large text
    if pq.total_chars > 2000 and pq.total_chunks <= 1:
        score -= 30

    # Penalty: many short chunks
    if pq.total_chunks > 0:
        short_ratio = pq.short_chunk_count / pq.total_chunks
        score -= short_ratio * 40

    # Penalty: garbled chunks
    if pq.total_chunks > 0:
        garbled_ratio = pq.garbled_chunk_count / pq.total_chunks
        score -= garbled_ratio * 50

    # Penalty: very short total extraction
    if pq.total_chars < 100:
        score -= 50
    elif pq.total_chars < 500:
        score -= 20

    # Bonus: has figure/table chunks (richer structure)
    if pq.figure_table_chunks > 0:
        score += 5

    return max(0.0, min(100.0, score))


def _detect_noise_patterns(
    papers: dict[str, list[dict]],
) -> list[NoisePattern]:
    """Find text lines that appear across multiple papers — likely headers/footers/watermarks.

    Only checks the first and last chunk of each paper (where headers/footers typically land).
    """
    line_counter: Counter = Counter()
    line_papers: dict[str, set[str]] = {}

    for item_key, chunks in papers.items():
        # Only scan first 2 and last 2 chunks per paper
        candidate_chunks = chunks[:2] + chunks[-2:]
        seen_in_paper: set[str] = set()
        for chunk in candidate_chunks:
            for line in chunk["text"].splitlines():
                stripped = line.strip()
                if not stripped or len(stripped) < 5 or len(stripped) > 150:
                    continue
                if stripped in seen_in_paper:
                    continue
                seen_in_paper.add(stripped)
                line_counter[stripped] += 1
                line_papers.setdefault(stripped, set()).add(item_key)

    patterns: list[NoisePattern] = []
    for text, count in line_counter.most_common(50):
        if count < 3:  # Must appear in at least 3 different papers
            break
        papers_affected = line_papers.get(text, set())

        # Classify the noise type
        likely = ""
        for pat in _HEADER_FOOTER_CANDIDATES:
            if pat.search(text):
                if "vol" in text.lower() or "issn" in text.lower() or "doi" in text.lower():
                    likely = "journal_header"
                elif re.match(r"^\d+$", text):
                    likely = "page_number"
                elif "published" in text.lower() or "©" in text:
                    likely = "copyright_footer"
                elif text.startswith("http"):
                    likely = "url"
                else:
                    likely = "header"
                break

        if not likely and len(text.split()) <= 4:
            likely = "possible_header"

        patterns.append(NoisePattern(
            text=text,
            occurrence_count=count,
            papers_affected=papers_affected,
            likely_type=likely,
        ))

    return patterns


def _compute_embedding_separation(
    retriever: Retriever,
    sample_size: int = 20,
    chunk_pairs_per_paper: int = 5,
) -> dict:
    """Sample-based estimate of intra vs inter paper embedding similarity.

    For efficiency, samples up to ``sample_size`` papers and compares
    random chunk pairs within and across papers.
    """
    import random

    indexed = list(retriever.list_indexed_items())
    if len(indexed) < 2:
        return {
            "intra_paper_similarity": 0.0,
            "inter_paper_similarity": 0.0,
            "separation_ratio": 0.0,
            "embedding_dim": 0,
            "note": "Need at least 2 indexed papers for separation analysis.",
        }

    sampled = random.sample(indexed, min(sample_size, len(indexed)))
    ef = get_embedding_function()

    # Collect chunk texts per paper (first N chunks only)
    paper_texts: dict[str, list[str]] = {}
    for key in sampled:
        chunks = retriever.get_item_chunks(key)
        paper_texts[key] = [c.text for c in chunks[:chunk_pairs_per_paper * 2]]

    # Compute embeddings
    all_texts: list[str] = []
    text_to_paper: dict[int, str] = {}
    for key, texts in paper_texts.items():
        for t in texts:
            if t.strip():
                text_to_paper[len(all_texts)] = key
                all_texts.append(t)

    if len(all_texts) < 4:
        return {
            "intra_paper_similarity": 0.0,
            "inter_paper_similarity": 0.0,
            "separation_ratio": 0.0,
            "embedding_dim": 0,
            "note": "Not enough chunk texts for separation analysis.",
        }

    embeddings = ef(all_texts)
    emb_array = __import__("numpy").array(embeddings)
    embedding_dim = emb_array.shape[1]

    # Compute pairwise cosine similarities (normalized embeddings → dot product)
    intra_sims: list[float] = []
    inter_sims: list[float] = []

    n = len(all_texts)
    # Sample pairs to keep it fast
    max_pairs = 500
    indices = list(range(n))
    random.shuffle(indices)

    for i_idx in range(min(n, max_pairs)):
        i = indices[i_idx]
        j = random.choice([x for x in range(n) if x != i])

        sim = float((emb_array[i] @ emb_array[j]))
        if text_to_paper[i] == text_to_paper[j]:
            intra_sims.append(sim)
        else:
            inter_sims.append(sim)

    intra_avg = sum(intra_sims) / len(intra_sims) if intra_sims else 0.0
    inter_avg = sum(inter_sims) / len(inter_sims) if inter_sims else 0.0
    ratio = intra_avg / inter_avg if inter_avg > 0 else 0.0

    return {
        "intra_paper_similarity": round(intra_avg, 4),
        "inter_paper_similarity": round(inter_avg, 4),
        "separation_ratio": round(ratio, 2),
        "embedding_dim": embedding_dim,
        "note": (
            f"Sampled {len(sampled)} papers, {n} chunks. "
            f"intra > inter by {ratio:.1f}x "
            + ("(good separation)" if ratio > 1.3 else
               "(WEAK separation — chunks from different papers may be too similar)")
        ),
    }


# ── Main audit function ───────────────────────────────────────────────


def run_audit(
    *,
    zot: ZoteroClient,
    retriever: Retriever,
    embedding_model: str = "",
    top_n: int = 10,
) -> AuditReport:
    """Run the full quality audit and return a structured report."""

    report = AuditReport(
        chunking_version=CHUNKING_VERSION,
        embedding_model=embedding_model or os.getenv("EMBEDDING_MODEL", "?"),
        audit_timestamp=__import__("datetime").datetime.now().isoformat(),
    )

    total_count = retriever.count()
    if total_count == 0:
        report.recommendations.append({
            "severity": "critical",
            "title": "Index is empty",
            "detail": "No chunks in ChromaDB. Run sync_index first.",
            "action": "python scripts/index_library.py  or  sync_index via MCP",
        })
        return report

    # ── Phase 1: Paginated scan of all indexed chunks ─────────────
    papers: dict[str, list[dict]] = {}  # item_key → [{text, meta}]
    section_counts: Counter = Counter()
    chunk_lengths: list[int] = []
    garbled_count = 0
    short_count = 0
    long_count = 0
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
            papers.setdefault(key, []).append({
                "text": doc,
                "meta": meta,
            })

            doc_len = len(doc)
            chunk_lengths.append(doc_len)
            section_counts[meta.get("section", "content")] += 1

            if meta.get("has_figure_table"):
                figure_table_count += 1
            if doc_len < 50:
                short_count += 1
            if doc_len > 1500:
                long_count += 1
            if _is_garbled(doc):
                garbled_count += 1

        offset += len(docs)

    # ── Phase 2: Per-paper quality scoring ───────────────────────
    paper_qualities: list[PaperQuality] = []
    for key, chunks in papers.items():
        lengths = [len(c["text"]) for c in chunks]
        total_chars = sum(lengths)
        content = sum(1 for c in chunks if c["meta"].get("section") != "references")
        refs = sum(1 for c in chunks if c["meta"].get("section") == "references")
        ft = sum(1 for c in chunks if c["meta"].get("has_figure_table"))
        shorts = sum(1 for l in lengths if l < 50)
        longs = sum(1 for l in lengths if l > 1500)
        garbleds = sum(1 for c in chunks if _is_garbled(c["text"]))

        issues: list[str] = []
        if total_chars < 200:
            issues.append("very_short_extraction")
        if len(chunks) <= 1 and total_chars > 500:
            issues.append("single_chunk_large_text")
        if garbleds > 0:
            issues.append(f"garbled_chunks:{garbleds}")
        if shorts / len(chunks) > 0.3 if chunks else False:
            issues.append("many_short_chunks")
        if longs > 0:
            issues.append(f"long_chunks:{longs}")

        pq = PaperQuality(
            item_key=key,
            title=chunks[0]["meta"].get("title", ""),
            year=chunks[0]["meta"].get("year", 0),
            total_chunks=len(chunks),
            total_chars=total_chars,
            avg_chunk_length=round(sum(lengths) / len(lengths), 1) if lengths else 0,
            min_chunk_length=min(lengths) if lengths else 0,
            max_chunk_length=max(lengths) if lengths else 0,
            content_chunks=content,
            reference_chunks=refs,
            figure_table_chunks=ft,
            short_chunk_count=shorts,
            long_chunk_count=longs,
            garbled_chunk_count=garbleds,
            quality_score=0.0,
            issues=issues,
        )
        pq.quality_score = round(_compute_quality_score(pq), 1)
        paper_qualities.append(pq)

    paper_qualities.sort(key=lambda p: p.quality_score)

    # ── Phase 3: Library coverage analysis ───────────────────────
    library_keys: set[str] = set()
    papers_with_pdf = 0
    try:
        # Get all items from Zotero (up to a reasonable limit)
        items = zot.search_items("", limit=500)
        library_keys = {item.key for item in items}
        # Try to get PDF paths
        all_keys = list(library_keys)
        pdf_paths = zot.get_pdf_paths_for_keys(all_keys) if all_keys else {}
        papers_with_pdf = sum(1 for k, v in pdf_paths.items() if v)
    except Exception:
        pass  # Library analysis is best-effort

    indexed_keys = set(papers.keys())
    missing_keys = library_keys - indexed_keys

    # ── Phase 4: Noise pattern detection ─────────────────────────
    noise_patterns = _detect_noise_patterns(papers)

    # ── Phase 5: Embedding separation ────────────────────────────
    sep = _compute_embedding_separation(retriever)
    report.embedding_dim = sep["embedding_dim"]
    report.intra_paper_similarity = sep["intra_paper_similarity"]
    report.inter_paper_similarity = sep["inter_paper_similarity"]
    report.separation_ratio = sep["separation_ratio"]

    # ── Phase 6: Populate report ─────────────────────────────────
    if chunk_lengths:
        sorted_lengths = sorted(chunk_lengths)
        report.avg_chunk_length = round(sum(chunk_lengths) / len(chunk_lengths), 1)
        report.median_chunk_length = sorted_lengths[len(chunk_lengths) // 2]
        report.min_chunk_length = min(chunk_lengths)
        report.max_chunk_length = max(chunk_lengths)

    report.total_chunks = total_count
    report.total_papers_indexed = len(papers)
    report.total_papers_in_library = len(library_keys)
    report.papers_with_pdf = papers_with_pdf
    report.papers_missing_from_index = len(missing_keys)
    report.short_chunks = short_count
    report.long_chunks = long_count
    report.garbled_chunks = garbled_count
    report.avg_chunks_per_paper = round(total_count / len(papers), 1) if papers else 0
    report.content_chunks = section_counts.get("content", 0)
    report.reference_chunks = section_counts.get("references", 0)
    report.figure_table_chunks = figure_table_count
    report.papers_with_extraction_issues = sum(
        1 for pq in paper_qualities if pq.total_chars < 200
    )
    report.papers_single_chunk = sum(
        1 for pq in paper_qualities if pq.total_chunks <= 1
    )
    report.papers_empty_extraction = sum(
        1 for pq in paper_qualities if pq.total_chars == 0
    )
    report.noise_patterns = noise_patterns[:15]
    report.paper_qualities = paper_qualities
    report.top_problem_papers = paper_qualities[:top_n]
    report.top_healthy_papers = sorted(
        paper_qualities, key=lambda p: -p.quality_score
    )[:top_n]

    # ── Phase 7: Generate recommendations ────────────────────────
    recs: list[dict] = []

    if garbled_count / total_count > 0.05:
        recs.append({
            "severity": "high",
            "title": f"High garbled text rate ({garbled_count}/{total_count} chunks)",
            "detail": ">5% of chunks appear garbled. Check if PDF extraction is producing "
                       "corrupted text (common with scanned PDFs or non-standard encodings).",
            "action": "Run 'python scripts/audit_index.py --json' and inspect garbled "
                       "papers. Consider removing them or re-extracting with OCR.",
        })

    if short_count / total_count > 0.1:
        recs.append({
            "severity": "medium",
            "title": f"Many short chunks ({short_count}/{total_count})",
            "detail": ">10% of chunks are <50 chars. Short chunks lack context and "
                       "hurt retrieval quality.",
            "action": "Increase min_chunk_size or adjust chunking to merge short paragraphs.",
        })

    papers_very_bad = [pq for pq in paper_qualities if pq.quality_score < 30]
    if papers_very_bad:
        recs.append({
            "severity": "high",
            "title": f"{len(papers_very_bad)} papers with very low quality (score < 30)",
            "detail": f"Examples: {', '.join(p.item_key[:8] for p in papers_very_bad[:5])}",
            "action": "Review these papers' PDFs. They may be scanned, encrypted, or "
                       "have severe extraction issues.",
        })

    if report.separation_ratio < 1.2:
        recs.append({
            "severity": "medium",
            "title": f"Weak embedding separation (intra/inter = {report.separation_ratio})",
            "detail": "Chunks from different papers are nearly as similar as chunks from "
                       "the same paper. This degrades retrieval precision.",
            "action": "Consider: (1) chunk size adjustments, (2) embedding model upgrade, "
                       "(3) adding more metadata filtering.",
        })

    if len(missing_keys) > 10:
        sample = list(missing_keys)[:5]
        recs.append({
            "severity": "medium",
            "title": f"{len(missing_keys)} library papers not indexed",
            "detail": f"Examples: {', '.join(sample)}",
            "action": "Run sync_index to index missing papers. Some may lack PDFs "
                       "or have extraction failures.",
        })

    if noise_patterns:
        top_noise = noise_patterns[0]
        recs.append({
            "severity": "low",
            "title": f"Noise patterns detected: {len(noise_patterns)} candidates",
            "detail": f"Most frequent: '{top_noise.text[:60]}' "
                       f"(appears in {top_noise.occurrence_count} papers, "
                       f"likely {top_noise.likely_type})",
            "action": "These patterns can be cleaned with text_cleaner rules. "
                       "Prioritize cleaning headers/footers before the next full reindex.",
        })

    if report.avg_chunks_per_paper < 3:
        recs.append({
            "severity": "low",
            "title": f"Very low avg chunks per paper ({report.avg_chunks_per_paper})",
            "detail": "Papers are barely being chunked. This may indicate short PDFs "
                       "or chunking that's too coarse.",
            "action": "Consider reducing target_chunk_size for finer granularity.",
        })

    report.recommendations = recs
    return report


# ── CLI output formatters ─────────────────────────────────────────────


def print_report(report: AuditReport) -> None:
    """Print a human-readable audit report."""
    sep = "═" * 60

    print(f"\n{sep}")
    print("  RAG Index Quality Audit Report")
    print(f"{sep}")

    # Header
    print(f"\n  Chunking version : {report.chunking_version}")
    print(f"  Embedding model   : {report.embedding_model}")
    print(f"  Audit timestamp   : {report.audit_timestamp}")

    # Overview
    print(f"\n  ── Overview ──")
    print(f"  Total chunks           : {report.total_chunks:>8,}")
    print(f"  Papers indexed         : {report.total_papers_indexed:>8,}")
    print(f"  Papers in library      : {report.total_papers_in_library:>8,}")
    print(f"  Papers with PDFs       : {report.papers_with_pdf:>8,}")
    print(f"  Missing from index     : {report.papers_missing_from_index:>8,}")
    print(f"  Avg chunks per paper   : {report.avg_chunks_per_paper:>8.1f}")

    # Chunk quality
    print(f"\n  ── Chunk Quality ──")
    print(f"  Avg chunk length       : {report.avg_chunk_length:>8.0f} chars")
    print(f"  Median chunk length    : {report.median_chunk_length:>8,} chars")
    print(f"  Min / Max length       : {report.min_chunk_length:>6,} / {report.max_chunk_length:,} chars")
    print(f"  Short chunks (<50)     : {report.short_chunks:>8,}  ({_pct(report.short_chunks, report.total_chunks)})")
    print(f"  Long chunks (>1500)    : {report.long_chunks:>8,}  ({_pct(report.long_chunks, report.total_chunks)})")
    print(f"  Garbled chunks         : {report.garbled_chunks:>8,}  ({_pct(report.garbled_chunks, report.total_chunks)})")

    # Sections
    print(f"\n  ── Section Breakdown ──")
    print(f"  Content chunks         : {report.content_chunks:>8,}  ({_pct(report.content_chunks, report.total_chunks)})")
    print(f"  Reference chunks       : {report.reference_chunks:>8,}  ({_pct(report.reference_chunks, report.total_chunks)})")
    print(f"  Figure/Table chunks    : {report.figure_table_chunks:>8,}  ({_pct(report.figure_table_chunks, report.total_chunks)})")

    # PDF quality
    print(f"\n  ── PDF Quality ──")
    print(f"  Papers with <200 chars : {report.papers_with_extraction_issues:>8,}")
    print(f"  Papers with 1 chunk    : {report.papers_single_chunk:>8,}")
    print(f"  Papers with 0 chars    : {report.papers_empty_extraction:>8,}")

    # Embedding separation
    print(f"\n  ── Embedding Separation ──")
    print(f"  Intra-paper similarity : {report.intra_paper_similarity:.4f}")
    print(f"  Inter-paper similarity : {report.inter_paper_similarity:.4f}")
    ratio_label = "GOOD" if report.separation_ratio > 1.3 else "WEAK"
    print(f"  Separation ratio       : {report.separation_ratio:.2f}x  [{ratio_label}]")
    print(f"  Embedding dim          : {report.embedding_dim}")

    # Health score
    overall = _overall_health(report)
    print(f"\n  ── Overall Health ──")
    print(f"  Score: {overall['score']:.0f}/100  [{overall['grade']}]")
    print(f"  {overall['summary']}")

    # Noise patterns
    if report.noise_patterns:
        print(f"\n  ── Noise Patterns (cross-paper repeated text) ──")
        for i, np_ in enumerate(report.noise_patterns[:10]):
            print(f"  [{np_.likely_type}] \"{np_.text[:70]}\"")
            print(f"      appears in {np_.occurrence_count} papers")

    # Problem papers
    if report.top_problem_papers:
        print(f"\n  ── Top Problem Papers (lowest quality) ──")
        for i, pq in enumerate(report.top_problem_papers):
            title = pq.title[:60] if pq.title else "(unknown)"
            print(f"  {i+1}. [{pq.quality_score:.0f}] {pq.item_key[:10]} | "
                  f"{pq.total_chunks}c / {pq.total_chars}ch | {title}")
            if pq.issues:
                print(f"      Issues: {', '.join(pq.issues)}")

    # Recommendations
    if report.recommendations:
        print(f"\n  ── Recommendations ──")
        for i, rec in enumerate(report.recommendations):
            sev_icon = {"critical": "[!!]", "high": "[!]", "medium": "[~]", "low": "[*]"}.get(
                rec["severity"], "[-]"
            )
            print(f"  {sev_icon} [{rec['severity'].upper()}] {rec['title']}")
            print(f"     {rec['action']}")

    print(f"\n{sep}")
    print(f"  Audit complete. {report.total_chunks} chunks analyzed.")
    print(f"{sep}\n")


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{part / total * 100:.1f}%"


def _overall_health(report: AuditReport) -> dict:
    """Compute an overall health score from 0-100."""
    score = 100.0

    # Garbled chunks penalty
    if report.total_chunks > 0:
        garbled_rate = report.garbled_chunks / report.total_chunks
        if garbled_rate > 0.1:
            score -= 30
        elif garbled_rate > 0.05:
            score -= 15
        elif garbled_rate > 0.02:
            score -= 5

    # Short chunks penalty
    if report.total_chunks > 0:
        short_rate = report.short_chunks / report.total_chunks
        if short_rate > 0.2:
            score -= 20
        elif short_rate > 0.1:
            score -= 10

    # Separation penalty
    if report.separation_ratio > 0:
        if report.separation_ratio < 1.1:
            score -= 20
        elif report.separation_ratio < 1.3:
            score -= 10

    # Coverage penalty
    if report.total_papers_in_library > 0:
        missing_rate = report.papers_missing_from_index / report.total_papers_in_library
        if missing_rate > 0.5:
            score -= 25
        elif missing_rate > 0.2:
            score -= 10

    # Single chunk penalty
    if report.total_papers_indexed > 0:
        single_rate = report.papers_single_chunk / report.total_papers_indexed
        if single_rate > 0.3:
            score -= 15
        elif single_rate > 0.1:
            score -= 5

    score = max(0.0, min(100.0, score))

    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"

    if score >= 80:
        summary = "Index is healthy. Address minor recommendations."
    elif score >= 60:
        summary = "Some issues detected. Prioritize high-severity recommendations."
    elif score >= 40:
        summary = "Significant quality problems. A full reindex after cleaning is recommended."
    else:
        summary = "Critical issues. The index needs a rebuild with improved data quality."

    return {"score": score, "grade": grade, "summary": summary}


# ── CLI entry point ───────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Full-library RAG index quality audit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/audit_index.py              # Full audit, human-readable
  python scripts/audit_index.py --json       # Machine-readable JSON output
  python scripts/audit_index.py --top-n 20   # Show more problem papers
  python scripts/audit_index.py --no-embedding  # Skip embedding analysis (faster)
        """,
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--top-n", type=int, default=10, help="Number of problem papers to show")
    parser.add_argument("--no-embedding", action="store_true",
                        help="Skip embedding separation analysis (faster)")
    parser.add_argument("--persist-dir", default=".chroma_db", help="ChromaDB directory")
    args = parser.parse_args()

    # Initialize clients
    print("Connecting to Zotero...", file=sys.stderr)
    zot = ZoteroClient()
    retriever = Retriever(persist_dir=args.persist_dir)

    embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    print(f"Scanning index ({retriever.count()} chunks)...", file=sys.stderr)
    report = run_audit(
        zot=zot,
        retriever=retriever,
        embedding_model=embedding_model,
        top_n=args.top_n,
    )

    if args.json:
        # Custom JSON encoder for sets
        class SetEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, set):
                    return list(obj)
                return super().default(obj)

        report_dict = {
            "chunking_version": report.chunking_version,
            "embedding_model": report.embedding_model,
            "audit_timestamp": report.audit_timestamp,
            "overview": {
                "total_chunks": report.total_chunks,
                "total_papers_indexed": report.total_papers_indexed,
                "total_papers_in_library": report.total_papers_in_library,
                "papers_with_pdf": report.papers_with_pdf,
                "papers_missing_from_index": report.papers_missing_from_index,
                "avg_chunks_per_paper": report.avg_chunks_per_paper,
            },
            "chunk_quality": {
                "avg_length": report.avg_chunk_length,
                "median_length": report.median_chunk_length,
                "min_length": report.min_chunk_length,
                "max_length": report.max_chunk_length,
                "short_chunks": report.short_chunks,
                "long_chunks": report.long_chunks,
                "garbled_chunks": report.garbled_chunks,
            },
            "section_breakdown": {
                "content": report.content_chunks,
                "references": report.reference_chunks,
                "figure_table": report.figure_table_chunks,
            },
            "pdf_quality": {
                "papers_with_extraction_issues": report.papers_with_extraction_issues,
                "papers_single_chunk": report.papers_single_chunk,
                "papers_empty_extraction": report.papers_empty_extraction,
            },
            "embedding_separation": {
                "intra_paper_similarity": report.intra_paper_similarity,
                "inter_paper_similarity": report.inter_paper_similarity,
                "separation_ratio": report.separation_ratio,
                "embedding_dim": report.embedding_dim,
            },
            "noise_patterns": [
                {
                    "text": np_.text,
                    "occurrence_count": np_.occurrence_count,
                    "papers_affected_count": len(np_.papers_affected),
                    "likely_type": np_.likely_type,
                }
                for np_ in report.noise_patterns
            ],
            "top_problem_papers": [
                {
                    "item_key": pq.item_key,
                    "title": pq.title,
                    "quality_score": pq.quality_score,
                    "total_chunks": pq.total_chunks,
                    "total_chars": pq.total_chars,
                    "issues": pq.issues,
                }
                for pq in report.top_problem_papers
            ],
            "recommendations": report.recommendations,
        }
        print(json.dumps(report_dict, ensure_ascii=False, indent=2, cls=SetEncoder))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
