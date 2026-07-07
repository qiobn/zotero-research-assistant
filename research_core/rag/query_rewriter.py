"""Multi-layer query expansion for bilingual academic search.

Three layers:
1. Built-in methodology dictionary (~200 pairs, query_dict.json)
2. Auto-extracted from user's Zotero tags/keywords (populated during sync_index)
3. User-defined synonyms (add_query_synonym MCP tool)

Zero external dependencies. No LLM calls. In-memory LRU cache.

Usage:
    from research_core.rag.query_rewriter import QueryRewriter
    rewriter = QueryRewriter()
    expanded = rewriter.expand("城市公共服务可达性")
    # -> [("城市公共服务可达性", 1.0), ("urban public service accessibility", 0.4), ...]
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

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

    def expand(self, query: str) -> list[tuple[str, float]]:
        """Expand a query into weighted search terms.

        Returns list of (query_text, weight) where:
        - weight 1.0 = original query
        - weight 0.4 = dictionary expansion term
        - weight 0.3 = tag/synonym expansion term
        - 0 < weight < 0.3 = decomposed sub-query
        """
        return _cached_expand(query, self._layer1_enabled,
                              self._layer2_enabled, self._layer3_enabled)

    def disable_layer(self, layer: int) -> None:
        """Disable a specific expansion layer (1/2/3)."""
        if layer == 1:
            self._layer1_enabled = False
        elif layer == 2:
            self._layer2_enabled = False
        elif layer == 3:
            self._layer3_enabled = False


@lru_cache(maxsize=512)
def _cached_expand(query: str, l1: bool, l2: bool, l3: bool) -> list[tuple[str, float]]:
    """Cached expansion — same query + same layer config → same result."""
    lang = _detect_query_language(query)
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
