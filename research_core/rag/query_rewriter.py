"""Lightweight query validation and user-built bilingual term lookup.

Design principle: bilingual search strategy is OWNED by the external LLM.
This module provides ONLY:
- User-built synonym thesaurus (add / list / remove / lookup)
- Zotero tag auto-collection (populated during sync_index)
- Basic query validation (not empty, not gibberish)

The external LLM:
- Detects the user's language and decides whether to search CN+EN or EN-only
- Calls expand_query() when it needs standard EN equivalents of methodology terms
- Decomposes complex queries into multiple search_papers() calls itself

No preset dictionaries. No NMT. No internal query rewriting.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from loguru import logger

# ── Language detection ─────────────────────────────────────────────────

_DETECT_CACHE: dict[str, str] = {}


def detect_language(text: str) -> str:
    """Detect language of a query string. Returns 'zh', 'en', or 'mixed'.

    Uses CJK character ratio. Cached for repeated calls on the same text.
    """
    if text in _DETECT_CACHE:
        return _DETECT_CACHE[text]

    cjk = sum(1 for c in text if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total = cjk + ascii_alpha
    if total == 0:
        result = "en"
    elif cjk / max(total, 1) > 0.5:
        result = "zh"
    elif ascii_alpha / max(total, 1) > 0.8:
        result = "en"
    else:
        result = "mixed"

    _DETECT_CACHE[text] = result
    if len(_DETECT_CACHE) > 256:
        _DETECT_CACHE.clear()
    return result


# ── Query validation ────────────────────────────────────────────────────

# Characters that should never dominate a query (gibberish / encoding errors)
_GIBBERISH_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def validate(query: str) -> str | None:
    """Validate a search query. Returns error message string or None if valid.

    Checks: not empty, minimum length, no control characters.
    Does NOT modify the query — the external LLM owns query formulation.
    """
    if not query or not query.strip():
        return "Query is empty"

    stripped = query.strip()
    if len(stripped) < 2:
        return "Query too short (minimum 2 characters)"

    if _GIBBERISH_RE.search(stripped):
        return "Query contains invalid characters"

    return None


# ── User-built synonym thesaurus ────────────────────────────────────────

_user_synonyms: dict[str, list[str]] = {}


def load_user_synonyms(synonym_file: str) -> None:
    """Load user-defined synonyms from a persisted JSON file."""
    global _user_synonyms
    if not os.path.exists(synonym_file):
        return
    try:
        with open(synonym_file, encoding="utf-8") as f:
            data = json.load(f)
        _user_synonyms = data.get("entries", {})
        logger.info(f"Loaded {len(_user_synonyms)} user synonym pairs")
    except (json.JSONDecodeError, OSError):
        _user_synonyms = {}


def add_user_synonym(cn_term: str, en_terms: list[str]) -> None:
    """Add or update a user-defined synonym pair."""
    cn_term = cn_term.strip()
    _user_synonyms[cn_term] = [t.strip() for t in en_terms]


def remove_user_synonym(cn_term: str) -> bool:
    """Remove a user synonym. Returns True if it existed."""
    cn_term = cn_term.strip()
    if cn_term in _user_synonyms:
        del _user_synonyms[cn_term]
        return True
    return False


def get_user_synonyms() -> dict[str, list[str]]:
    """Return all user-defined synonyms (for persistence)."""
    return dict(_user_synonyms)


# ── Zotero tag auto-collection ──────────────────────────────────────────

_user_tags: dict[str, list[str]] = {}


def load_user_tags(tags: list[str]) -> None:
    """Feed Zotero tags into the rewriter for term lookup.

    Called during sync_index or whenever tag data is refreshed.
    """
    for tag in tags:
        tag_lower = tag.strip().lower()
        if tag_lower not in _user_tags:
            _user_tags[tag_lower] = [tag.strip()]


# ── Term expansion (user dict + tags only) ──────────────────────────────


@lru_cache(maxsize=1024)
def expand_query(term: str) -> dict:
    """Look up a term in user-built thesaurus and Zotero tags.

    Returns a dict with 'synonyms' (user-defined) and 'tags' (Zotero).
    The external LLM calls this to find standard EN equivalents of
    methodology terms before constructing EN search queries.

    Example:
        expand_query("两步移动搜索法")
        → {"synonyms": [], "tags": ["可达性", "两步移动搜索法", "2SFCA"]}
    """
    term_lower = term.strip().lower()
    result: dict = {"synonyms": [], "tags": []}

    # User-defined synonym lookup (exact match on CN term)
    for cn_term, en_terms in _user_synonyms.items():
        if cn_term in term or term_lower in cn_term.lower():
            result["synonyms"].extend(en_terms)

    # Zotero tag lookup (substring match)
    for tag, variants in _user_tags.items():
        if term_lower in tag or tag in term_lower:
            for v in variants:
                if v.lower() not in [s.lower() for s in result["tags"]]:
                    result["tags"].append(v)
            if len(result["tags"]) >= 20:
                break

    return result


# ── Singleton ──────────────────────────────────────────────────────────

_rewriter: QueryRewriter | None = None


class QueryRewriter:
    """Minimal rewriter — exposes user synonym management and term lookup.

    The actual query expansion strategy is owned by the external LLM.
    This class only provides the data (user dict + tags) for lookup.
    """

    def list_synonyms(self) -> dict[str, list[str]]:
        return get_user_synonyms()

    def add_synonym(self, cn_term: str, en_terms: list[str]) -> None:
        add_user_synonym(cn_term, en_terms)

    def remove_synonym(self, cn_term: str) -> bool:
        return remove_user_synonym(cn_term)

    def expand(self, term: str) -> dict:
        return expand_query(term)


def get_rewriter() -> QueryRewriter:
    global _rewriter
    if _rewriter is None:
        _rewriter = QueryRewriter()
    return _rewriter
