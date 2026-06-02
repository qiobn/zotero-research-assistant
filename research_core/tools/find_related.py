"""Find related literature by auto-generating multiple queries from paper metadata.

Unified tool that covers both online (OpenAlex/S2/CrossRef) and CNKI search.
Reduces 8-12 manual search rounds to a single tool call.
"""

from __future__ import annotations

import itertools
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from loguru import logger

from research_core.sources.models import OnlinePaperHit
from research_core.tools.discover_online import (
    _fetch_all_sources,
    _merge_papers,
)
from research_core.tools.discover_online import (
    _mark_local_library as _mark_local_online,
)
from research_core.zotero.client import ZoteroClient

SortBy = Literal["relevance", "citations"]
Scope = Literal["online", "cnki", "both"]

_CHINESE_STOPWORDS = frozenset(
    "的了是在有不这我他她它们你个中大上为以及与对其可被"
    "从而所也就都已将会能把被要让用着到过没很还因但如果"
    "虽然因为所以如何什么怎么那些这些通过进行研究分析"
    "基于本文探讨影响因素机制作用视角下理论方法模型框架"
)

_ENGLISH_STOPWORDS = frozenset(
    "the a an in on of to for with by from at is are was were be been being "
    "and or not this that these those it its as but if than then so do does did "
    "has have had will would can could may might shall should about between "
    "through during into over after before under above how what which where when "
    "who whom whose why all both each few more most other some such no nor any "
    "study research paper analysis based using approach method model framework "
    "effect effects impact influence role".split()
)

_FIELDS_OF_STUDY_MAP: dict[str, list[str]] = {
    "Business": ["Business", "Economics"],
    "Economics": ["Business", "Economics"],
    "Sociology": ["Sociology"],
    "Psychology": ["Psychology"],
    "Computer Science": ["Computer Science"],
    "Medicine": ["Medicine"],
    "Environmental Science": ["Environmental Science"],
    "Geography": ["Geography", "Environmental Science"],
    "Education": ["Education"],
    "Political Science": ["Political Science"],
    "Engineering": ["Engineering"],
    "Tourism": ["Business", "Economics", "Sociology"],
    "Marketing": ["Business", "Economics"],
    "Law": ["Law", "Political Science"],
}


def _is_chinese(text: str) -> bool:
    """Check if text contains predominantly Chinese characters."""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return chinese_chars > len(text) * 0.3


def _extract_chinese_terms(text: str) -> list[str]:
    """Extract meaningful Chinese terms from text without jieba.

    Strategy: split on punctuation, common delimiters, and stopword characters.
    For longer segments, also split on common structural particles.
    """
    segments = re.split(r"[，。、；：！？\s,;:!?\-—–/()（）【】\[\]\"\'""'']+", text)
    terms = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) <= 8:
            if len(seg) >= 2 and not all(c in _CHINESE_STOPWORDS for c in seg):
                terms.append(seg)
        else:
            sub_parts = re.split(r"[的了是在有对与及其]", seg)
            for part in sub_parts:
                part = part.strip()
                if 2 <= len(part) <= 8 and not all(c in _CHINESE_STOPWORDS for c in part):
                    terms.append(part)
    return terms


def _extract_english_terms(text: str) -> list[str]:
    """Extract meaningful English terms (multi-word phrases preserved)."""
    words = re.findall(r"[a-zA-Z][\w-]*", text.lower())
    meaningful = [w for w in words if w not in _ENGLISH_STOPWORDS and len(w) > 2]
    return meaningful


def _quote_phrase(phrase: str) -> str:
    """Wrap multi-word phrases in quotes for exact matching in search APIs."""
    if " " in phrase.strip() and not phrase.startswith('"'):
        return f'"{phrase.strip()}"'
    return phrase.strip()


def _generate_queries(
    title: str = "",
    abstract: str = "",
    keywords: list[str] | None = None,
) -> list[str]:
    """Generate 3-5 search queries from paper metadata (pure rules, no LLM).

    Strategy:
    1. Use keyword combinations with quoted multi-word phrases
    2. Use title core terms as disambiguating context
    3. Combine keywords with title domain words for cross-context queries
    """
    queries: list[str] = []
    kw_list = [k.strip() for k in (keywords or []) if k.strip()]

    if kw_list:
        quoted_kws = [_quote_phrase(k) for k in kw_list]
        if len(kw_list) >= 3:
            for combo in itertools.combinations(quoted_kws[:6], 2):
                queries.append(" ".join(combo))
        if len(kw_list) >= 4:
            for combo in itertools.combinations(quoted_kws[:5], 3):
                queries.append(" ".join(combo))
        if len(kw_list) <= 4:
            queries.append(" ".join(quoted_kws))

    if title.strip():
        title_clean = re.sub(r"[——\-—–:：].*$", "", title.strip())
        if _is_chinese(title_clean):
            terms = _extract_chinese_terms(title_clean)
            if len(terms) >= 2:
                queries.append(" ".join(terms[:4]))
        else:
            terms = _extract_english_terms(title_clean)
            if len(terms) >= 2:
                queries.append(" ".join(terms[:5]))

    if title.strip() and kw_list:
        title_clean = re.sub(r"[——\-—–:：].*$", "", title.strip())
        if _is_chinese(title_clean):
            title_terms = _extract_chinese_terms(title_clean)
        else:
            title_terms = _extract_english_terms(title_clean)
        if title_terms and kw_list:
            domain_word = title_terms[0] if title_terms else ""
            if domain_word:
                for kw in kw_list[:2]:
                    combo_q = f"{_quote_phrase(kw)} {domain_word}"
                    queries.append(combo_q)

    if abstract and len(queries) < 4:
        abs_text = abstract[:300]
        if _is_chinese(abs_text):
            terms = _extract_chinese_terms(abs_text)
            if terms:
                queries.append(" ".join(terms[:4]))
        else:
            terms = _extract_english_terms(abs_text)
            if terms:
                queries.append(" ".join(terms[:5]))

    seen: list[str] = []
    for q in queries:
        if q and q not in seen:
            seen.append(q)
    return seen[:6]


def _build_relevance_terms(
    title: str = "",
    keywords: list[str] | None = None,
) -> set[str]:
    """Build a set of relevance terms from paper metadata for post-filtering."""
    terms: set[str] = set()
    for kw in (keywords or []):
        kw_clean = kw.strip().lower()
        if kw_clean:
            terms.add(kw_clean)
            for word in re.findall(r"[a-z\u4e00-\u9fff]+", kw_clean):
                if len(word) >= 3 and word not in _ENGLISH_STOPWORDS:
                    terms.add(word)
    if title.strip():
        title_lower = title.strip().lower()
        for word in re.findall(r"[a-z\u4e00-\u9fff]+", title_lower):
            if len(word) >= 4 and word not in _ENGLISH_STOPWORDS:
                terms.add(word)
    return terms


def _relevance_score(hit_title: str, hit_abstract: str, relevance_terms: set[str]) -> float:
    """Score 0-1 indicating how relevant a hit is to the original paper."""
    if not relevance_terms:
        return 1.0
    text = f"{hit_title} {hit_abstract}".lower()
    matched = sum(1 for term in relevance_terms if term in text)
    return matched / len(relevance_terms)


def _filter_irrelevant(
    hits: list,
    relevance_terms: set[str],
    min_score: float = 0.15,
) -> list:
    """Remove hits that have negligible overlap with the paper's topic."""
    if not relevance_terms:
        return hits
    filtered = []
    for hit in hits:
        title = getattr(hit, "title", "")
        abstract = getattr(hit, "abstract", "")
        score = _relevance_score(title, abstract, relevance_terms)
        if score >= min_score:
            filtered.append(hit)
        else:
            logger.debug(f"Filtered out irrelevant hit: {title[:60]}... (score={score:.2f})")
    return filtered


def _dedup_cnki_hits(all_hits: list) -> list:
    """Deduplicate CNKI hits by normalized title."""
    seen_titles: set[str] = set()
    unique: list = []
    for hit in all_hits:
        key = re.sub(r"\s+", "", hit.title.lower().strip())
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique.append(hit)
    return unique


def _run_online_related(
    queries: list[str],
    *,
    year_from: int | None,
    year_to: int | None,
    limit: int,
    sort_by: SortBy,
    fields_of_study: list[str] | None,
    relevance_terms: set[str],
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Run multiple queries through online sources and merge."""
    from research_core.tools.discover_online import _fetch_depth

    fetch_depth = _fetch_depth(limit)
    all_source_lists: list[tuple[str, list]] = []

    with ThreadPoolExecutor(max_workers=min(len(queries) * 4, 16)) as pool:
        futures = {}
        for i, query in enumerate(queries):
            future = pool.submit(
                _fetch_all_sources,
                query,
                year_from=year_from,
                year_to=year_to,
                fetch_depth=fetch_depth,
                sort_by=sort_by,
                fields_of_study=fields_of_study,
            )
            futures[future] = f"batch_{i}"

        for future in as_completed(futures):
            try:
                source_lists = future.result()
                all_source_lists.extend(source_lists)
            except Exception as exc:
                logger.debug(f"Online related search batch failed: {exc}")

    if not all_source_lists:
        return []

    hits = _merge_papers(all_source_lists, limit=limit * 2, sort_by=sort_by)
    hits = _filter_irrelevant(hits, relevance_terms)
    hits = hits[:limit]
    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def _run_cnki_related(
    queries: list[str],
    *,
    year_from: int | None,
    year_to: int | None,
    source_categories: list[str] | None,
    limit: int,
    sort_by: SortBy,
    relevance_terms: set[str],
    zot: ZoteroClient | None,
) -> list:
    """Run multiple queries through CNKI in a single browser session."""
    from research_core.sources.cnki.browser import cnki_page
    from research_core.sources.cnki.exceptions import CnkiCaptchaError
    from research_core.sources.cnki.models import CnkiPaperHit
    from research_core.sources.cnki.scripts import (
        BASIC_SEARCH_JS,
        BASIC_SEARCH_URL,
    )
    from research_core.sources.cnki.search import (
        _apply_filters,
        _raw_to_hits,
    )
    from research_core.tools.discover_cnki import _mark_local_library

    all_hits: list[CnkiPaperHit] = []

    with cnki_page() as page:
        for i, query in enumerate(queries):
            try:
                if i == 0:
                    page.goto(BASIC_SEARCH_URL, wait_until="domcontentloaded")
                    raw = page.evaluate(BASIC_SEARCH_JS, {"query": query})
                else:
                    raw = page.evaluate(BASIC_SEARCH_JS, {"query": query})
            except Exception as exc:
                if "timeout" in str(exc).lower():
                    logger.debug(f"CNKI query '{query}' timed out, skipping")
                    continue
                logger.debug(f"CNKI query '{query}' failed: {exc}")
                continue

            if raw.get("error") == "captcha":
                raise CnkiCaptchaError(
                    "CNKI captcha detected. Solve it in Chrome, then retry."
                )

            hits, total, page_info = _raw_to_hits(raw, limit=20)
            all_hits.extend(hits)
            logger.debug(f"CNKI related query '{query}' -> {len(hits)} hits")

    all_hits = _dedup_cnki_hits(all_hits)
    all_hits = _filter_irrelevant(all_hits, relevance_terms)

    all_hits = _apply_filters(
        all_hits,
        year_from=year_from,
        year_to=year_to,
        limit=limit,
        sort_by=sort_by,
    )

    if zot is not None:
        _mark_local_library(all_hits, zot)

    return all_hits


def _run_citation_network(
    *,
    doi: str = "",
    title: str = "",
    year_from: int | None,
    year_to: int | None,
    fields_of_study: list[str] | None,
    relevance_terms: set[str],
    limit: int,
    zot: ZoteroClient | None,
) -> list[OnlinePaperHit]:
    """Expand related papers via citation network (cited_by + references)."""
    from research_core.sources.openalex import (
        get_cited_by,
        get_references,
        resolve_openalex_id,
    )

    openalex_id = resolve_openalex_id(doi=doi, title=title)
    if not openalex_id:
        logger.debug(f"Citation network: could not resolve OpenAlex ID for doi={doi}, title={title[:50]}")
        return []

    logger.debug(f"Citation network: resolved {openalex_id}")

    common_kwargs = {
        "year_from": year_from,
        "year_to": year_to,
        "fields_of_study": fields_of_study,
        "limit": limit,
    }

    citing_papers: list = []
    referenced_papers: list = []

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_citing = pool.submit(get_cited_by, openalex_id, **common_kwargs)
        future_refs = pool.submit(get_references, openalex_id, **common_kwargs)

        try:
            citing_papers = future_citing.result()
        except Exception as exc:
            logger.debug(f"Citation network cited_by failed: {exc}")
        try:
            referenced_papers = future_refs.result()
        except Exception as exc:
            logger.debug(f"Citation network references failed: {exc}")

    source_lists: list[tuple[str, list]] = []
    if citing_papers:
        source_lists.append(("cited_by", citing_papers))
    if referenced_papers:
        source_lists.append(("references", referenced_papers))

    if not source_lists:
        return []

    hits = _merge_papers(source_lists, limit=limit * 2, sort_by="citations")
    # Lower threshold for citation network hits — the citation relationship
    # itself is a relevance signal, so we only filter out extreme noise.
    hits = _filter_irrelevant(hits, relevance_terms, min_score=0.08)
    hits = hits[:limit]

    if zot is not None:
        _mark_local_online(hits, zot)
    return hits


def find_related_literature(
    scope: Scope = "online",
    title: str = "",
    abstract: str = "",
    keywords: list[str] | None = None,
    doi: str = "",
    fields_of_study: list[str] | None = None,
    source_categories: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 30,
    sort_by: SortBy = "relevance",
    zot: ZoteroClient | None = None,
) -> dict:
    """Find literature related to a known paper by auto-generating multiple queries.

    Accepts paper metadata (title, abstract, keywords) and automatically:
    1. Generates 3-6 diverse search queries from the metadata
    2. Executes all queries (online / CNKI / both) with optional field filtering
    3. Post-filters irrelevant results via keyword overlap scoring
    4. Deduplicates and merges results
    5. If keyword search yields few results, falls back to citation network
       expansion (papers that cite or are cited by the seed paper via OpenAlex)

    Args:
        doi: Optional DOI of the seed paper. Enables citation network expansion
            as a fallback when keyword search returns insufficient results.
        fields_of_study: Optional discipline filter to improve precision.
            Valid values: Business, Economics, Sociology, Psychology,
            Computer Science, Medicine, Environmental Science, Geography,
            Education, Political Science, Engineering, Tourism, Marketing, Law.
    """
    queries = _generate_queries(title=title, abstract=abstract, keywords=keywords)
    if not queries:
        return {"error": "Provide at least title or keywords to generate queries.", "queries": []}

    relevance_terms = _build_relevance_terms(title=title, keywords=keywords)

    result: dict = {"queries_generated": queries, "scope": scope}

    if scope in ("online", "both"):
        online_hits = _run_online_related(
            queries,
            year_from=year_from,
            year_to=year_to,
            limit=limit,
            sort_by=sort_by,
            fields_of_study=fields_of_study,
            relevance_terms=relevance_terms,
            zot=zot,
        )

        # Fallback: if keyword search returned too few results, try citation network
        if len(online_hits) < 5 and (doi or title):
            logger.info(
                f"Keyword search returned only {len(online_hits)} hits, "
                "expanding via citation network..."
            )
            citation_hits = _run_citation_network(
                doi=doi,
                title=title,
                year_from=year_from,
                year_to=year_to,
                fields_of_study=fields_of_study,
                relevance_terms=relevance_terms,
                limit=limit,
                zot=zot,
            )
            if citation_hits:
                existing_keys = {h.doi.lower() for h in online_hits if h.doi}
                for ch in citation_hits:
                    if ch.doi and ch.doi.lower() in existing_keys:
                        continue
                    online_hits.append(ch)
                    existing_keys.add(ch.doi.lower() if ch.doi else "")
                online_hits = online_hits[:limit]
                result["citation_network_used"] = True

        result["online_hits"] = online_hits
        result["online_count"] = len(online_hits)

    if scope in ("cnki", "both"):
        cnki_hits = _run_cnki_related(
            queries,
            year_from=year_from,
            year_to=year_to,
            source_categories=source_categories,
            limit=limit,
            sort_by=sort_by,
            relevance_terms=relevance_terms,
            zot=zot,
        )
        result["cnki_hits"] = cnki_hits
        result["cnki_count"] = len(cnki_hits)

    return result
