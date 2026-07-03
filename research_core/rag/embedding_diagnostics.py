"""Embedding quality diagnostics — beyond basic intra/inter similarity.

Provides:
1. Per-paper intra-similarity stats (mean, variance, outliers)
2. Cross-paper similarity matrix (top-N most similar paper pairs)
3. Outlier chunk detection (chunks far from their paper's centroid)
4. Chunk length vs embedding quality correlation
5. Section-type embedding separation (are reference chunks the issue?)
6. Topic cluster identification

All analysis is sample-based for efficiency — uses the embedding function
to recompute vectors for a sampled subset of chunks.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class PaperEmbeddingStats:
    item_key: str
    title: str = ""
    chunk_count: int = 0
    intra_mean: float = 0.0      # avg cosine sim within this paper
    intra_std: float = 0.0       # std dev of pairwise sims
    centroid_coherence: float = 0.0  # avg sim of each chunk to centroid
    outlier_count: int = 0        # chunks with sim to centroid < threshold
    outlier_chunk_indices: list[int] = field(default_factory=list)


@dataclass
class EmbeddingDiagnosticReport:
    # Global
    total_papers_sampled: int = 0
    total_chunks_sampled: int = 0
    embedding_dim: int = 0
    intra_paper_mean: float = 0.0
    inter_paper_mean: float = 0.0
    separation_ratio: float = 0.0

    # Per-paper
    paper_stats: list[PaperEmbeddingStats] = field(default_factory=list)
    papers_with_outliers: int = 0
    total_outlier_chunks: int = 0

    # Cross-paper
    most_similar_pairs: list[dict] = field(default_factory=list)  # top-10

    # Correlation: chunk length vs intra-similarity
    length_sim_correlation: float = 0.0  # Pearson r

    # Section-type analysis
    by_section_type: dict[str, dict] = field(default_factory=dict)

    # Recommendations
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


def _cosine_sim(a, b) -> float:
    """Dot product of two normalized vectors = cosine similarity."""
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def run_embedding_diagnostics(
    retriever,
    embedding_function,
    sample_papers: int = 25,
    max_chunks_per_paper: int = 15,
    outlier_threshold: float = 0.3,
    seed: int = 42,
) -> EmbeddingDiagnosticReport:
    """Run comprehensive embedding quality diagnostics.

    Samples papers and chunks, recomputes embeddings, then analyzes
    intra/inter similarity patterns, outliers, and correlations.
    """
    random.seed(seed)
    # We need numpy for centroid/aggregation
    import numpy as np

    report = EmbeddingDiagnosticReport()

    indexed_keys = list(retriever.list_indexed_items())
    if len(indexed_keys) < 3:
        report.issues.append("Need at least 3 indexed papers for diagnostics.")
        return report

    sampled_keys = random.sample(indexed_keys, min(sample_papers, len(indexed_keys)))

    # ── Phase 1: Collect chunk texts and compute embeddings ──
    all_texts: list[str] = []
    text_meta: list[dict] = []  # {item_key, chunk_idx, length, section}

    for key in sampled_keys:
        chunks = retriever.get_item_chunks(key)
        for c in chunks[:max_chunks_per_paper]:
            if c.text.strip():
                all_texts.append(c.text)
                text_meta.append({
                    "item_key": key,
                    "chunk_idx": c.chunk_idx,
                    "length": len(c.text),
                    "section": c.metadata.get("section", "content"),
                })

    if len(all_texts) < 10:
        report.issues.append("Not enough chunk texts for diagnostics.")
        return report

    # Compute embeddings
    embeddings = embedding_function(all_texts)
    emb_array = np.array(embeddings, dtype=np.float32)
    report.embedding_dim = emb_array.shape[1]
    report.total_chunks_sampled = len(all_texts)
    report.total_papers_sampled = len(sampled_keys)

    # ── Phase 2: Per-paper intra-similarity ──
    paper_indices: dict[str, list[int]] = defaultdict(list)
    for i, meta in enumerate(text_meta):
        paper_indices[meta["item_key"]].append(i)

    intra_sims: list[float] = []
    inter_sims: list[float] = []
    paper_stats_list: list[PaperEmbeddingStats] = []
    total_outliers = 0

    for key, indices in paper_indices.items():
        if len(indices) < 2:
            continue

        # Centroid
        paper_vecs = emb_array[indices]
        centroid = paper_vecs.mean(axis=0)
        centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-8)

        # Pairwise sims within paper
        pair_sims: list[float] = []
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                sim = _cosine_sim(paper_vecs[a], paper_vecs[b])
                pair_sims.append(sim)
                intra_sims.append(sim)

        # Centroid coherence
        cent_sims = [_cosine_sim(paper_vecs[i], centroid_norm) for i in range(len(indices))]

        # Outliers
        outlier_indices = [
            text_meta[indices[i]]["chunk_idx"]
            for i, s in enumerate(cent_sims)
            if s < outlier_threshold
        ]

        title = ""
        try:
            chunks = retriever.get_item_chunks(key)
            if chunks:
                title = chunks[0].title or ""
        except Exception:
            pass

        stats = PaperEmbeddingStats(
            item_key=key,
            title=title,
            chunk_count=len(indices),
            intra_mean=float(np.mean(pair_sims)) if pair_sims else 0.0,
            intra_std=float(np.std(pair_sims)) if pair_sims else 0.0,
            centroid_coherence=float(np.mean(cent_sims)),
            outlier_count=len(outlier_indices),
            outlier_chunk_indices=outlier_indices[:5],
        )
        paper_stats_list.append(stats)
        total_outliers += len(outlier_indices)

        # Cross-paper: compare this paper's centroid to others
        for other_key, other_indices in paper_indices.items():
            if other_key <= key:
                continue
            if len(other_indices) < 2:
                continue
            other_centroid = emb_array[other_indices].mean(axis=0)
            other_centroid_norm = other_centroid / (np.linalg.norm(other_centroid) + 1e-8)
            inter_sims.append(_cosine_sim(centroid_norm, other_centroid_norm))

    report.paper_stats = sorted(paper_stats_list, key=lambda s: s.intra_mean)
    report.papers_with_outliers = sum(1 for s in paper_stats_list if s.outlier_count > 0)
    report.total_outlier_chunks = total_outliers
    report.intra_paper_mean = float(np.mean(intra_sims)) if intra_sims else 0.0
    report.inter_paper_mean = float(np.mean(inter_sims)) if inter_sims else 0.0
    report.separation_ratio = round(
        report.intra_paper_mean / report.inter_paper_mean, 2
    ) if report.inter_paper_mean > 0 else 0.0

    # ── Phase 3: Most similar paper pairs ──
    pair_scores: list[tuple[float, str, str]] = []
    for key_a in paper_indices:
        for key_b in paper_indices:
            if key_a >= key_b or len(paper_indices[key_a]) < 2 or len(paper_indices[key_b]) < 2:
                continue
            c_a = emb_array[paper_indices[key_a]].mean(axis=0)
            c_b = emb_array[paper_indices[key_b]].mean(axis=0)
            c_a_n = c_a / (np.linalg.norm(c_a) + 1e-8)
            c_b_n = c_b / (np.linalg.norm(c_b) + 1e-8)
            pair_scores.append((_cosine_sim(c_a_n, c_b_n), key_a, key_b))

    pair_scores.sort(reverse=True)
    for sim, ka, kb in pair_scores[:10]:
        title_a = next((s.title for s in paper_stats_list if s.item_key == ka), ka)
        title_b = next((s.title for s in paper_stats_list if s.item_key == kb), kb)
        report.most_similar_pairs.append({
            "paper_a": ka, "title_a": title_a[:60],
            "paper_b": kb, "title_b": title_b[:60],
            "centroid_similarity": round(sim, 4),
        })

    # ── Phase 4: Length-similarity correlation ──
    lengths = np.array([m["length"] for m in text_meta], dtype=np.float32)
    all_pair_stats: list[tuple[float, float]] = []
    for key, indices in paper_indices.items():
        if len(indices) < 2:
            continue
        for a in range(len(indices)):
            avg_sim = float(np.mean([
                _cosine_sim(emb_array[indices[a]], emb_array[indices[b]])
                for b in range(len(indices)) if b != a
            ]))
            all_pair_stats.append((float(lengths[indices[a]]), avg_sim))

    if len(all_pair_stats) >= 5:
        len_arr = np.array([s[0] for s in all_pair_stats])
        sim_arr = np.array([s[1] for s in all_pair_stats])
        corr = np.corrcoef(len_arr, sim_arr)[0, 1]
        report.length_sim_correlation = round(float(corr), 4) if not np.isnan(corr) else 0.0

    # ── Phase 5: Section-type analysis ──
    section_indices: dict[str, list[int]] = defaultdict(list)
    for i, meta in enumerate(text_meta):
        section_indices[meta["section"]].append(i)

    for sec, indices in section_indices.items():
        if len(indices) < 2:
            continue
        sec_vecs = emb_array[indices]
        cent = sec_vecs.mean(axis=0)
        cent_n = cent / (np.linalg.norm(cent) + 1e-8)
        cent_sims = [_cosine_sim(sec_vecs[i], cent_n) for i in range(len(sec_vecs))]
        report.by_section_type[sec] = {
            "chunk_count": len(indices),
            "intra_section_mean": round(float(np.mean(cent_sims)), 4),
            "intra_section_std": round(float(np.std(cent_sims)), 4),
        }

    # ── Phase 6: Issues & suggestions ──
    if report.separation_ratio < 1.3:
        report.issues.append(
            f"Weak separation (intra/inter = {report.separation_ratio:.2f}x). "
            "Chunks from different papers are nearly as similar as chunks within the same paper."
        )
        report.suggestions.append(
            "Consider: (1) reducing chunk overlap to increase distinctiveness, "
            "(2) increasing min_chunk_size to ensure each chunk carries more unique content, "
            "(3) using metadata-aware retrieval (filter by year/tags) to narrow the search space."
        )

    if report.total_outlier_chunks > report.total_chunks_sampled * 0.1:
        report.issues.append(
            f"{report.total_outlier_chunks}/{report.total_chunks_sampled} chunks are outliers "
            f"(similarity to paper centroid < {outlier_threshold})."
        )
        report.suggestions.append(
            "Outlier chunks may be garbled text, figure/table fragments, or boilerplate. "
            "Check outlier examples and consider quality_flag filtering during retrieval."
        )

    if report.length_sim_correlation < -0.3:
        report.issues.append(
            f"Negative length-similarity correlation ({report.length_sim_correlation:.2f}). "
            "Longer chunks are LESS similar to their paper — possible topic drift in lengthy sections."
        )
    elif report.length_sim_correlation > 0.3:
        report.issues.append(
            f"Positive length-similarity correlation ({report.length_sim_correlation:.2f}). "
            "Longer chunks are MORE similar — short chunks may lack distinguishing context."
        )

    # Check for very similar paper pairs
    if report.most_similar_pairs and report.most_similar_pairs[0]["centroid_similarity"] > 0.85:
        top = report.most_similar_pairs[0]
        report.issues.append(
            f"Highly similar paper pair detected: "
            f"\"{top['title_a'][:30]}\" ↔ \"{top['title_b'][:30]}\" "
            f"(centroid sim = {top['centroid_similarity']:.3f}). "
            "These papers may be near-duplicates or share substantial content."
        )

    # Section type that drags down separation
    for sec, stats in report.by_section_type.items():
        if sec == "content":
            continue
        if stats["intra_section_mean"] < 0.4:
            report.issues.append(
                f"Section '{sec}' has low internal coherence "
                f"(mean={stats['intra_section_mean']:.3f}). "
                f"The {sec} section may be dragging down overall retrieval precision."
            )

    return report


def format_diagnostic_report(report: EmbeddingDiagnosticReport) -> str:
    """Return a human-readable summary of the diagnostics report."""
    lines = []
    sep = "=" * 55
    lines.append(sep)
    lines.append("  Embedding Quality Diagnostic Report")
    lines.append(sep)
    lines.append(f"  Papers sampled : {report.total_papers_sampled}")
    lines.append(f"  Chunks sampled : {report.total_chunks_sampled}")
    lines.append(f"  Embedding dim  : {report.embedding_dim}")

    lines.append(f"\n  -- Global Separation --")
    lines.append(f"  Intra-paper mean  : {report.intra_paper_mean:.4f}")
    lines.append(f"  Inter-paper mean  : {report.inter_paper_mean:.4f}")
    ratio_label = "GOOD" if report.separation_ratio > 1.3 else "WEAK"
    lines.append(f"  Separation ratio  : {report.separation_ratio:.2f}x [{ratio_label}]")

    if report.length_sim_correlation != 0:
        lines.append(f"\n  -- Chunk Length Correlation --")
        lines.append(f"  Length-Sim Pearson r : {report.length_sim_correlation:.3f}")

    if report.papers_with_outliers > 0:
        lines.append(f"\n  -- Outlier Chunks (sim to centroid < 0.3) --")
        lines.append(f"  Papers affected : {report.papers_with_outliers}")
        lines.append(f"  Total outliers  : {report.total_outlier_chunks}")
        for ps in report.paper_stats[:5]:
            if ps.outlier_count > 0:
                lines.append(f"    {ps.item_key}: {ps.outlier_count} outliers "
                             f"(chunks {ps.outlier_chunk_indices}) "
                             f"intra_mean={ps.intra_mean:.3f}")

    if report.by_section_type:
        lines.append(f"\n  -- By Section Type --")
        for sec, stats in report.by_section_type.items():
            lines.append(
                f"    {sec}: {stats['chunk_count']} chunks, "
                f"coherence={stats['intra_section_mean']:.3f} "
                f"(std={stats['intra_section_std']:.3f})"
            )

    if report.most_similar_pairs:
        lines.append(f"\n  -- Most Similar Paper Pairs --")
        for p in report.most_similar_pairs[:5]:
            lines.append(f"    {p['centroid_similarity']:.3f} | "
                         f"{p['title_a'][:25]} ↔ {p['title_b'][:25]}")

    if report.issues:
        lines.append(f"\n  -- Issues --")
        for issue in report.issues:
            lines.append(f"    [!] {issue}")

    if report.suggestions:
        lines.append(f"\n  -- Suggestions --")
        for sug in report.suggestions:
            lines.append(f"    [*] {sug}")

    lines.append(sep)
    return "\n".join(lines)
