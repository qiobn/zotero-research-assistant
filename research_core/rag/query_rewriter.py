"""Multi-layer query expansion for bilingual academic search.

Layers:
1. Built-in methodology dictionary (~200 pairs, query_dict.json)
2. Auto-extracted from user's Zotero tags/keywords (populated during sync_index)
3. User-defined synonyms (add_query_synonym MCP tool)
4. NMT query translation (OPUS-MT CN→EN for Chinese queries; lazy-loaded)

Zero external LLM calls. In-memory LRU cache. The NMT model is loaded on
first Chinese query (~3-5s cold start, ~300MB RAM after loading).

Usage:
    from research_core.rag.query_rewriter import QueryRewriter
    rewriter = QueryRewriter()
    expanded = rewriter.expand("城市公共服务可达性")
    # -> [("城市公共服务可达性", 1.0), ("urban public service accessibility", 0.4),
    #      ("urban public service accessibility", 0.8), ...]
    #    ^--- dict-based layer 1  +  ^--- NMT translation layer 4
"""

from __future__ import annotations

import json
import os
import re
import threading
from functools import lru_cache

from loguru import logger

# ── Load built-in dictionary ──────────────────────────────────────────

_DICT_PATH = os.path.join(os.path.dirname(__file__), "query_dict.json")

with open(_DICT_PATH, encoding="utf-8") as f:
    _RAW = json.load(f)

_ENTRIES: dict[str, list[str]] = _RAW.get("entries", {})

# Build reverse index: EN → CN terms (auto-generated from the CN→EN dict)
_EN_TO_CN: dict[str, str] = {}
for cn_key, en_list in _ENTRIES.items():
    for en_term in en_list:
        en_lower = en_term.lower()
        if en_lower not in _EN_TO_CN:
            _EN_TO_CN[en_lower] = cn_key


# ── Language detection ─────────────────────────────────────────────────

def _detect_query_language(query: str) -> str:
    """Detect language of a query string. Returns 'zh', 'en', or 'mixed'."""
    cjk = sum(1 for c in query if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")
    ascii_alpha = sum(1 for c in query if c.isascii() and c.isalpha())
    total = cjk + ascii_alpha
    if total == 0:
        return "en"
    if cjk / max(total, 1) > 0.5:
        return "zh"
    if ascii_alpha / max(total, 1) > 0.8:
        return "en"
    return "mixed"


# ── Layer 1: Dictionary expansion ─────────────────────────────────────

def _dict_expand(query: str, lang: str) -> list[str]:
    """Scan the bilingual dictionary for terms that appear in the query,
    returning their translations.
    """
    found: list[str] = []

    if lang in ("zh", "mixed"):
        # Scan CN→EN: find Chinese terms in the query, add English translations
        for cn_term, en_terms in _ENTRIES.items():
            if cn_term in query:
                found.extend(en_terms)

    if lang in ("en", "mixed"):
        # Scan EN→CN: find English terms in the query, add Chinese translations
        query_lower = query.lower()
        # Sort by length descending so longer matches take precedence
        for en_term in sorted(_EN_TO_CN, key=len, reverse=True):
            if en_term in query_lower:
                cn_term = _EN_TO_CN[en_term]
                if cn_term not in found:
                    found.append(cn_term)

    return found


# ── Layer 2: User tag expansion (loaded from Zotero) ───────────────────

_user_tags: dict[str, list[str]] = {}


def load_user_tags(tags: list[str]) -> None:
    """Feed Zotero tags into the rewriter for personalized expansion.

    Called during sync_index or whenever tag data is refreshed.
    Each tag becomes a known term that can expand matching queries.
    """
    for tag in tags:
        tag_lower = tag.strip().lower()
        if tag_lower not in _user_tags:
            _user_tags[tag_lower] = [tag.strip()]


def _tag_expand(query: str, lang: str) -> list[str]:
    """Match query against user's Zotero tags. If a tag relates to the
    query, add it as an expansion term."""
    found: list[str] = []
    query_lower = query.lower()

    for tag, variants in _user_tags.items():
        # Check if query contains the tag or vice versa
        if tag in query_lower or any(v.lower() in query_lower for v in variants):
            # Add the tag as an expansion term unless it's already in the query
            if tag not in query_lower:
                found.append(tag)
            for v in variants:
                if v.lower() not in query_lower:
                    found.append(v)

    return found


# ── Layer 3: User-defined synonyms ─────────────────────────────────────

_user_synonyms: dict[str, list[str]] = {}


def load_user_synonyms(synonym_file: str) -> None:
    """Load user-defined synonyms from a JSON file (persisted to disk)."""
    global _user_synonyms
    if not os.path.exists(synonym_file):
        return
    try:
        with open(synonym_file, encoding="utf-8") as f:
            data = json.load(f)
        _user_synonyms = data.get("entries", {})
    except (json.JSONDecodeError, OSError):
        _user_synonyms = {}


def add_user_synonym(cn_term: str, en_terms: list[str]) -> None:
    """Add a user-defined synonym pair. Persisted on next save."""
    cn_term = cn_term.strip()
    _user_synonyms[cn_term] = [t.strip() for t in en_terms]


def get_user_synonyms() -> dict[str, list[str]]:
    """Return all user-defined synonyms (for saving to disk)."""
    return _user_synonyms


def _user_expand(query: str, lang: str) -> list[str]:
    """Look up user-defined synonym dictionary."""
    found: list[str] = []
    query_lower = query.lower()

    for cn_term, en_terms in _user_synonyms.items():
        if lang in ("zh", "mixed") and cn_term in query:
            found.extend(en_terms)
        elif lang in ("en", "mixed"):
            for en_t in en_terms:
                if en_t.lower() in query_lower:
                    found.append(cn_term)
                    break

    return found


# ── Layer 4: NMT query translation (CN→EN) ──────────────────────────────

_NMT_LOCK = threading.Lock()
_nmt_pipeline = None  # lazy-loaded: (tokenizer, model)


def _get_nmt_model():
    """Lazy-load OPUS-MT zh→en model. Returns (tokenizer, model).

    Thread-safe: only one thread loads the model; others wait.
    Model is cached at module level after first load.
    """
    global _nmt_pipeline
    if _nmt_pipeline is not None:
        return _nmt_pipeline

    with _NMT_LOCK:
        if _nmt_pipeline is not None:  # double-check after acquiring lock
            return _nmt_pipeline

        cache_dir = os.getenv("ZRA_NMT_CACHE_DIR")
        if not cache_dir:
            d = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
            if os.path.isabs(d) and all(ord(c) < 128 for c in d):
                cache_dir = os.path.join(d, "hf_cache")
            else:
                cache_dir = "D:\\tmp\\zra_nmt_cache"
                os.makedirs(cache_dir, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

        try:
            import socket
            from transformers import MarianMTModel, MarianTokenizer

            # Set a socket timeout so unreachable HF endpoints don't
            # block search for 30+ seconds. Cached models load from
            # disk in ~1s; downloads need < 10s on a good connection.
            default_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(10)

            model_name = "Helsinki-NLP/opus-mt-zh-en"
            tokenizer = MarianTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
            model = MarianMTModel.from_pretrained(model_name, cache_dir=cache_dir)
            _nmt_pipeline = (tokenizer, model)
            logger.info(f"NMT model loaded from {model_name}")
        except Exception as e:
            logger.warning(f"NMT model failed to load: {e}")
            _nmt_pipeline = (None, None)  # prevent retry
        finally:
            if default_timeout is not None:
                socket.setdefaulttimeout(default_timeout)

        return _nmt_pipeline


def _nmt_translate(text: str, target_len: int = 128) -> str:
    """Translate Chinese text to English using OPUS-MT.

    Returns empty string on any failure (non-fatal — search proceeds
    with the original query + other expansion layers).
    """
    tokenizer, model = _get_nmt_model()
    if tokenizer is None or model is None:
        return ""

    try:
        inputs = tokenizer(text, return_tensors="pt", padding=True,
                           truncation=True, max_length=target_len)
        outputs = model.generate(**inputs, max_new_tokens=target_len)
        return tokenizer.decode(outputs[0], skip_special_tokens=True)
    except Exception as e:
        logger.debug(f"NMT translation failed for '{text[:40]}': {e}")
        return ""


# ── Query decomposition ────────────────────────────────────────────────

_CLAUSE_SPLIT_RE = re.compile(r"[和与及或;；,，、\s]+(?:以及|并且|或者|并且|还有)?\s*")


def _decompose_query(query: str, lang: str) -> list[str]:
    """Split compound queries into sub-queries for independent retrieval.
    Only splits on clear conjunction markers, not on every separator.
    """
    # Don't decompose short queries or queries with meaningful compound terms
    if len(query) < 15:
        return [query]

    # Split on major clause boundaries
    parts = re.split(r"\s*(?:和|与|以及|并且|或者|and|or|;|；)\s*", query)
    # Filter out fragments that are too short to be meaningful
    parts = [p.strip() for p in parts if len(p.strip()) >= 6]
    if len(parts) <= 1:
        return [query]
    # Add the original query as the primary
    return [query] + parts


# ── Main expander ──────────────────────────────────────────────────────


class QueryRewriter:
    """Multi-layer bilingual query expander for academic search."""

    def __init__(self) -> None:
        self._layer1_enabled = True
        self._layer2_enabled = True
        self._layer3_enabled = True

    def expand(self, query: str, language: str = "auto") -> list[tuple[str, float]]:
        """Expand a query into weighted search terms.

        Args:
            query: The user's search query.
            language: \"auto\" to detect from text, \"zh\" to force Chinese
                      expansion, \"en\" to force English-only.

        Returns list of (query_text, weight) where:
        - weight 1.0 = original query
        - weight 0.8 = NMT translation (Layer 4 — OPUS-MT CN→EN)
        - weight 0.4 = dictionary expansion term (Layer 1)
        - weight 0.3 = tag/synonym expansion term (Layer 2/3)
        - 0 < weight < 0.3 = decomposed sub-query
        """
        return _cached_expand(query, self._layer1_enabled,
                              self._layer2_enabled, self._layer3_enabled,
                              language)

    def disable_layer(self, layer: int) -> None:
        """Disable a specific expansion layer (1/2/3)."""
        if layer == 1:
            self._layer1_enabled = False
        elif layer == 2:
            self._layer2_enabled = False
        elif layer == 3:
            self._layer3_enabled = False


@lru_cache(maxsize=512)
def _cached_expand(query: str, l1: bool, l2: bool, l3: bool,
                   language: str = "auto") -> list[tuple[str, float]]:
    """Cached expansion — same query + same layer config → same result.

    Args:
        language: \"auto\" to detect, \"zh\"/\"en\" to override detection.
    """
    if language == "auto":
        lang = _detect_query_language(query)
    else:
        lang = language

    results: list[tuple[str, float]] = [(query, 1.0)]

    if l1:
        for term in _dict_expand(query, lang):
            results.append((term, 0.4))

    if l2:
        for term in _tag_expand(query, lang):
            results.append((term, 0.3))

    if l3:
        for term in _user_expand(query, lang):
            results.append((term, 0.3))

    # Layer 4: NMT translation for Chinese queries (CN→EN)
    if lang in ("zh", "mixed"):
        translated = _nmt_translate(query)
        if translated and translated.lower().strip() != query.lower().strip():
            results.append((translated.strip(), 0.8))

    # Decompose compound queries into sub-queries
    sub_queries = _decompose_query(query, lang)
    for sq in sub_queries[1:]:  # skip the first (original)
        results.append((sq, 0.2))

    # Deduplicate by text
    seen: set[str] = set()
    unique: list[tuple[str, float]] = []
    for text, weight in results:
        text_lower = text.strip().lower()
        if text_lower and text_lower not in seen:
            seen.add(text_lower)
            unique.append((text.strip(), weight))

    return unique


# ── Singleton ──────────────────────────────────────────────────────────

_rewriter: QueryRewriter | None = None


def get_rewriter() -> QueryRewriter:
    global _rewriter
    if _rewriter is None:
        _rewriter = QueryRewriter()
    return _rewriter
