"""Index chunks into ChromaDB with metadata."""

from __future__ import annotations

import os
import threading

import chromadb
from loguru import logger

from research_core.parsers.chunker import Chunk
from research_core.rag.store import get_collection, sync_lock

# ── Index-time bilingual enrichment ─────────────────────────────────────

_NMT_LOCK = threading.Lock()
_nmt_pipeline = None  # lazy-loaded: (tokenizer, model)


def _is_chinese_text(text: str, threshold: float = 0.3) -> bool:
    """Detect if a text is primarily Chinese by CJK character ratio."""
    if not text:
        return False
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return cjk / len(text) > threshold


def _get_nmt_model():
    """Lazy-load OPUS-MT zh→en model (shared with query_rewriter)."""
    global _nmt_pipeline
    if _nmt_pipeline is not None:
        return _nmt_pipeline
    with _NMT_LOCK:
        if _nmt_pipeline is not None:
            return _nmt_pipeline
        cache_dir = os.getenv("ZRA_NMT_CACHE_DIR")
        if not cache_dir:
            # Use a known ASCII-only path to avoid sentencepiece Chinese-char crash
            d = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
            if os.path.isabs(d) and all(ord(c) < 128 for c in d):
                cache_dir = os.path.join(d, "hf_cache")
            else:
                cache_dir = "D:\\tmp\\zra_nmt_cache"
                os.makedirs(cache_dir, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from transformers import MarianMTModel, MarianTokenizer
            model_name = "Helsinki-NLP/opus-mt-zh-en"
            tokenizer = MarianTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
            model = MarianMTModel.from_pretrained(model_name, cache_dir=cache_dir)
            _nmt_pipeline = (tokenizer, model)
        except Exception as e:
            logger.warning(f"NMT model failed to load for index enrichment: {e}")
            _nmt_pipeline = (None, None)
        return _nmt_pipeline


# Per-paper translation cache: {(title, keywords): {"title_en": str, "keywords_en": str}}
_index_translation_cache: dict[tuple[str, str], dict[str, str]] = {}
_index_translation_lock = threading.Lock()


def _translate_paper_metadata(title: str, keywords: str) -> dict[str, str]:
    """Translate title + keywords for a Chinese paper.

    Returns {"title_en": ..., "keywords_en": ..., "translated": bool}.
    Results are cached per (title, keywords) pair to avoid re-translation
    across chunks of the same paper.
    """
    if not title or not _is_chinese_text(title):
        return {"title_en": "", "keywords_en": "", "translated": False}

    cache_key = (title, keywords)
    with _index_translation_lock:
        if cache_key in _index_translation_cache:
            return _index_translation_cache[cache_key]

    tokenizer, model = _get_nmt_model()
    if tokenizer is None or model is None:
        return {"title_en": "", "keywords_en": "", "translated": True}  # mark as attempted

    title_en = ""
    keywords_en = ""
    try:
        t_in = tokenizer(title, return_tensors="pt", padding=True, truncation=True, max_length=128)
        t_out = model.generate(**t_in, max_new_tokens=128)
        title_en = tokenizer.decode(t_out[0], skip_special_tokens=True)

        if keywords and _is_chinese_text(keywords):
            k_in = tokenizer(keywords, return_tensors="pt", padding=True, truncation=True, max_length=128)
            k_out = model.generate(**k_in, max_new_tokens=128)
            keywords_en = tokenizer.decode(k_out[0], skip_special_tokens=True)
    except Exception as e:
        logger.debug(f"Index translation failed for '{title[:40]}': {e}")

    result = {"title_en": title_en, "keywords_en": keywords_en, "translated": True}
    with _index_translation_lock:
        # Keep cache bounded — 8192 entries is plenty for any library
        if len(_index_translation_cache) > 8192:
            _index_translation_cache.clear()
        _index_translation_cache[cache_key] = result
    return result


# ── Chunk enrichment ────────────────────────────────────────────────────


def _enrich_chunk_text(
    chunk: Chunk, title: str, year: int, keywords: str = "",
) -> str:
    """Prepend paper + section + keyword context to chunk text before embedding.

    Anthropic "Contextual Retrieval" (2024): chunks embedded with surrounding
    context achieve 49% fewer retrieval failures vs bare text.

    Academic papers have a unique advantage: author-assigned keywords. These
    are high-quality, expert-curated topic signals that dramatically improve
    retrieval precision when included in the chunk context.

    For Chinese papers, title and keywords are automatically translated to
    English and appended as [Title_EN: ...] [Keywords_EN: ...], enabling
    BM25 cross-lingual matching for English queries.

    Format: "[Keywords: ...] [Title: {paper} ({year})] [Section: {heading}]
             [Title_EN: ...] [Keywords_EN: ...]\\n{text}"
    """
    parts: list[str] = []

    # Keywords (academic paper advantage — author-curated topic signals)
    if keywords:
        short_kw = keywords[:200] if len(keywords) > 200 else keywords
        parts.append(f"[Keywords: {short_kw}]")

    # Paper context
    if title:
        short_title = title[:150] if len(title) > 150 else title
        ctx = f"Title: {short_title}"
        if year:
            ctx += f" ({year})"
        parts.append(f"[{ctx}]")

    # Section context
    section = chunk.metadata.get("section", "")
    if section and section != "content":
        short_section = section[:120] if len(section) > 120 else section
        parts.append(f"[Section: {short_section}]")

    # Bilingual enrichment: for Chinese papers, append English translations
    # so BM25 can match English queries against Chinese content.
    trans = _translate_paper_metadata(title, keywords)
    if trans.get("title_en"):
        parts.append(f"[Title_EN: {trans['title_en'][:150]}]")
    if trans.get("keywords_en"):
        parts.append(f"[Keywords_EN: {trans['keywords_en'][:200]}]")

    if not parts:
        return chunk.text

    return " ".join(parts) + "\n" + chunk.text


class Indexer:
    """Write document chunks into a ChromaDB collection."""

    def __init__(
        self,
        persist_dir: str = ".chroma_db",
        collection_name: str = "research_chunks",
        collection: chromadb.Collection | None = None,
    ):
        self._collection = collection or get_collection(
            persist_dir, collection_name
        )

    @property
    def collection(self):
        return self._collection

    def index_chunks(
        self,
        chunks: list[Chunk],
        item_key: str,
        title: str = "",
        year: int = 0,
        keywords: str = "",
    ) -> int:
        """Add or replace chunks for one item. Returns number of chunks indexed.

        Each chunk text is enriched with keywords + paper + section context
        before embedding. Academic paper keywords are a unique advantage:
        expert-curated, high-density topic signals that boost both dense
        (embedding) and sparse (BM25) retrieval precision.
        """
        if not chunks:
            return 0
        with sync_lock:
            self.delete_item(item_key)
            ids = [f"{item_key}:{c.chunk_idx}" for c in chunks]
            documents = [
                _enrich_chunk_text(c, title, year, keywords) for c in chunks
            ]
            metadatas = [self._build_metadata(c, item_key, title, year) for c in chunks]
            self._collection.upsert(
                ids=ids, documents=documents, metadatas=metadatas
            )
        logger.info(f"Indexed {len(chunks)} chunks for item {item_key}")
        return len(chunks)

    @staticmethod
    def _build_metadata(c: Chunk, item_key: str, title: str, year: int) -> dict:
        """Build ChromaDB metadata (scalar values only) for one chunk."""
        meta = {
            "item_key": item_key,
            "title": title,
            "year": year,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "chunk_idx": c.chunk_idx,
            "section": c.metadata.get("section", "content"),
            "has_figure_table": c.metadata.get("has_figure_table", False),
            "is_table": c.metadata.get("is_table", False),
            "is_figure": c.metadata.get("is_figure", False),
            # Basic tags (v3.0.0+)
            "quality_flag": c.quality_flag,
            "sentence_count": c.sentence_count,
            "language": c.language,
        }
        if c.metadata.get("is_table"):
            # Caption-anchored table record: where it lives + caption + raw block
            # content (kept searchable, not structured into cells).
            meta["table_caption"] = c.metadata.get("table_caption", "")
            meta["table_label"] = c.metadata.get("table_label", "")
            table_ref = c.metadata.get("table_ref")
            if table_ref:
                meta["table_ref"] = table_ref
        elif c.metadata.get("is_figure"):
            # Caption-only figure record: where it lives + roughly what it shows.
            meta["figure_caption"] = c.metadata.get("figure_caption", "")
            meta["figure_label"] = c.metadata.get("figure_label", "")
            figure_ref = c.metadata.get("figure_ref")
            if figure_ref:
                meta["figure_ref"] = figure_ref
        else:
            # Prose chunks: record which tables/figures they cite so a passage
            # like "as shown in Table 3 / Figure 2" can be resolved to content.
            table_refs = c.metadata.get("table_refs")
            if table_refs:
                meta["table_refs"] = table_refs
            figure_refs = c.metadata.get("figure_refs")
            if figure_refs:
                meta["figure_refs"] = figure_refs
        return meta

    def delete_item(self, item_key: str) -> int:
        """Delete all chunks for an item. Returns the count deleted."""
        with sync_lock:
            existing = self._collection.get(
                where={"item_key": item_key}, include=[]
            )
            ids = existing.get("ids", []) or []
            if ids:
                self._collection.delete(ids=ids)
        return len(ids)

    def count(self) -> int:
        return self._collection.count()
