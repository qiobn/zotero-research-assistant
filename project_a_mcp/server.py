"""Zotero Research Assistant — MCP server.

28 tools, one intent each, designed to compose via `item_key`.

Categories:
  DISCOVER   search_papers, search_online_literature, search_cnki_literature,
             find_related_literature, cnki_paper_detail, cnki_navigate_pages,
             find_similar_papers, browse_library, find_duplicates, merge_duplicates
  READ       get_paper, get_paper_content, search_annotations, create_annotation
  WRITE      suggest_citations, export_bibliography, add_paper, cnki_add_to_zotero
  MANAGE     add_note, edit_tags, manage_collections
  INSIGHT    reading_status, recommend_papers, generate_review_note, suggest_tags,
             find_arguments
  ADMIN      sync_index
"""

from __future__ import annotations

import os
import threading
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import wraps
from typing import Literal

from dotenv import load_dotenv
from fastmcp import FastMCP
from loguru import logger
from research_core.rag.indexer import Indexer
from research_core.rag.retriever import Retriever
from research_core.tools import (
    add_note as _add_note,
)
from research_core.tools import (
    add_paper as _add_paper,
)
from research_core.tools import (
    browse_library as _browse_library,
)
from research_core.tools import (
    cnki_add_to_zotero as _cnki_add_to_zotero,
)
from research_core.tools import (
    cnki_navigate_pages as _cnki_navigate_pages,
)
from research_core.tools import (
    cnki_paper_detail as _cnki_paper_detail,
)
from research_core.tools import (
    create_annotation as _create_annotation,
)
from research_core.tools import (
    edit_tags as _edit_tags,
)
from research_core.tools import (
    export_bibliography as _export_bibliography,
)
from research_core.tools import (
    find_duplicates as _find_duplicates,
)
from research_core.tools import (
    find_related_literature as _find_related_literature,
)
from research_core.tools import (
    find_similar_papers as _find_similar_papers,
)
from research_core.tools import (
    get_paper as _get_paper,
)
from research_core.tools import (
    get_paper_content as _get_paper_content,
)
from research_core.tools import (
    manage_collections as _manage_collections,
)
from research_core.tools import (
    merge_duplicates as _merge_duplicates,
)
from research_core.tools import (
    search_annotations as _search_annotations,
)
from research_core.tools import (
    search_cnki_literature as _search_cnki_literature,
)
from research_core.tools import (
    search_online_literature as _search_online_literature,
)
from research_core.tools import (
    search_papers as _search_papers,
)
from research_core.tools import (
    suggest_citations as _suggest_citations,
)
from research_core.tools import (
    sync_index as _sync_index,
)
from research_core.tools.arguments import find_arguments as _find_arguments
from research_core.tools.reading_status import get_reading_status as _get_reading_status
from research_core.tools.recommend import recommend_papers as _recommend_papers
from research_core.tools.review import generate_review_note as _generate_review_note
from research_core.tools.suggest_tags import suggest_tags as _suggest_tags
from research_core.utils import normalize_list
from research_core.zotero.client import ZoteroClient

load_dotenv()


def _background_sync():
    """Run incremental sync in a background thread on server startup."""
    try:
        logger.info("Background sync: starting incremental index sync...")
        report = _sync_index(
            zot=_get_zot(),
            indexer=_get_indexer(),
            retriever=_get_retriever(),
            force_rebuild=False,
        )
        logger.info(
            f"Background sync complete: {len(report.added)} added, "
            f"{len(report.updated)} updated, {len(report.skipped)} skipped, "
            f"{len(report.failed)} failed"
        )
    except Exception as e:
        logger.warning(f"Background sync failed (non-fatal): {e}")


@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[None]:
    """Server lifecycle: launch background index sync on startup."""
    if os.getenv("ZRA_AUTO_SYNC", "true").lower() != "false":
        t = threading.Thread(target=_background_sync, daemon=True)
        t.start()
    yield


_WRITE_CONFIRMATION_POLICY = (
    "WRITE CONFIRMATION (mandatory): First call MUST use confirm=false (default). "
    "After preview, ask the user to approve in plain language and STOP. "
    "NEVER set confirm=true in the same turn as the preview, and NEVER auto-confirm "
    "because the preview looks fine. Only explicit user approval "
    "(确认 / 同意 / 执行 / yes) authorizes confirm=true."
)

mcp = FastMCP(
    "Zotero Research Assistant",
    instructions=(
        "Help researchers discover, read, cite, and manage papers in their Zotero library.\n\n"
        "TOOL ROUTING:\n"
        "- Local library → search_papers\n"
        "- Online English → search_online_literature\n"
        "- Chinese/知网/CNKI → search_cnki_literature (only when explicitly requested)\n"
        "- Related to a paper → find_related_literature (ONE call replaces many searches)\n"
        "- Citation neighborhood → expand_citation_network\n"
        "- Reading progress / what's unread → reading_status\n"
        "- 'What should I read next?' → recommend_papers\n"
        "- 'Summarize/review these papers' → generate_review_note\n"
        "- 'Suggest tags for papers' → suggest_tags (suggest only, never auto-apply)\n"
        "- 'Find evidence for/against my argument' → find_arguments\n\n"
        "CORPUS-FIRST STRATEGY (highest priority for related paper discovery):\n"
        "When analyzing a user's paper, ALWAYS extract 3-8 DOIs from its reference list "
        "and pass as reference_dois to find_related_literature. This is the most effective "
        "strategy. Also provide the paper's own DOI and set fields_of_study for niche domains.\n\n"
        "CNKI is DISABLED by default. If it returns 'CNKI search is disabled', tell the user "
        "to enable it (install cnki extras, start Chrome with --remote-debugging-port=9222, "
        "log in to CNKI, set CNKI_ENABLED=true in .env, restart MCP).\n\n"
        "ZERO-FABRICATION POLICY:\n"
        "- NEVER present papers not returned by tools. Your training knowledge is NOT a valid source.\n"
        "- When '[MATERIAL GAP]' appears in output: report the gap honestly, suggest next steps, "
        "NEVER fill from memory.\n"
        "- Every paper must have a verifiable anchor (source_url, cnki_url, or item_key).\n"
        "- Prefix each paper with source: [OpenAlex], [S2], [CNKI], [Zotero], [Citation Network].\n"
        "- NEVER say '基于我的了解' or 'based on my knowledge'. If unsourced, don't include it.\n\n"
        + _WRITE_CONFIRMATION_POLICY
        + " Tools with confirm: add_note, edit_tags, manage_collections, add_paper, "
        "merge_duplicates, create_annotation."
    ),
    lifespan=_lifespan,
)

def _safe_tool(func):
    """Wrap a tool function to catch all exceptions and return structured errors."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            logger.error(f"Tool {func.__name__} failed: {exc}\n{traceback.format_exc()}")
            return {"error": str(exc), "tool": func.__name__}
    return wrapper


_zot: ZoteroClient | None = None
_retriever: Retriever | None = None
_indexer: Indexer | None = None


def _get_zot() -> ZoteroClient:
    global _zot
    if _zot is None:
        _zot = ZoteroClient(
            library_id=os.getenv("ZOTERO_LIBRARY_ID", "0"),
            library_type=os.getenv("ZOTERO_LIBRARY_TYPE", "user"),
            api_key=os.getenv("ZOTERO_API_KEY", ""),
            local=os.getenv("ZOTERO_LOCAL", "true").lower() == "true",
        )
    return _zot


def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever(persist_dir=os.getenv("CHROMA_PERSIST_DIR", ".chroma_db"))
    return _retriever


def _get_indexer() -> Indexer:
    global _indexer
    if _indexer is None:
        _indexer = Indexer(persist_dir=os.getenv("CHROMA_PERSIST_DIR", ".chroma_db"))
    return _indexer


# ╔══════════════════════════════════════════════════════════════╗
# ║  DISCOVER                                                    ║
# ╚══════════════════════════════════════════════════════════════╝


@mcp.tool()
@_safe_tool
def search_papers(
    query: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    tags_include: list[str] | None = None,
    tags_exclude: list[str] | None = None,
    collection_key: str = "",
    limit: int = 10,
) -> list[dict]:
    """Find papers in the user's Zotero library by topic, keywords, or filters.

    This is the PRIMARY discovery tool. Use it whenever the user wants to find papers
    they haven't yet picked out. Combines keyword matching against Zotero's library
    AND semantic similarity against the indexed PDF chunks, merged with Reciprocal
    Rank Fusion. The user does not need to specify which mechanism.

    Two usage modes:
      1. With query: hybrid search (keyword + semantic + reranking).
      2. Without query (query=""): pure filter mode — returns all papers matching
         year/tags/collection filters, sorted by date added. Use this when the user
         asks to "list papers from 2024" or "show all papers tagged X" without
         specifying a topic.

    When NOT to use:
    - User already gave you a paper key → use get_paper or get_paper_content instead.
    - User wants more papers like a specific one they named → use find_similar_papers.
    - User wants to browse collections/tags/recent additions → use browse_library.
    - User is writing a draft and wants citations for it → use suggest_citations.
    - User wants to discover papers NOT in their library → use search_online_literature (default).
      Use search_cnki_literature only if they explicitly ask for 中文文献/知网/CNKI/核心期刊.

    Args:
        query: Natural-language topic, concept, or keyword string. Can be empty ("")
               to list all papers matching the filters without topic search.
        year_from/year_to: Publication year window (inclusive). Either or both optional.
        tags_include: Only return papers carrying ALL these tags.
        tags_exclude: Drop any paper carrying ANY of these tags.
        collection_key: Restrict search to a single Zotero collection.
        limit: Max results to return (default 10).

    Returns:
        List of papers ordered by relevance (or date if no query), each with key,
        title, authors, year, DOI, tags, score, source ('keyword' | 'semantic' |
        'hybrid'), and the best matching passage with its page number when available.
    """
    hits = _search_papers(
        query=query,
        zot=_get_zot(),
        retriever=_get_retriever(),
        year_from=year_from,
        year_to=year_to,
        tags_include=normalize_list(tags_include, "tags_include"),
        tags_exclude=normalize_list(tags_exclude, "tags_exclude"),
        collection_key=collection_key,
        limit=limit,
    )
    return [h.__dict__ for h in hits]


@mcp.tool()
@_safe_tool
def search_online_literature(
    query: str,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 15,
    sort_by: Literal["relevance", "citations"] = "relevance",
    fields_of_study: list[str] | None = None,
) -> dict:
    """Search OpenAlex + Semantic Scholar + CrossRef for English/international papers.

    Default for online search. Use sort_by="citations" for high-impact surveys.
    Set fields_of_study to constrain to a discipline (e.g. ['Sociology', 'Geography']).

    Args:
        query: Keywords or natural-language search string.
        year_from/year_to: Publication year window.
        limit: Max results (default 15).
        sort_by: "relevance" or "citations".
        fields_of_study: Discipline filter (Business, Economics, Sociology, Psychology,
            Computer Science, Medicine, Environmental Science, Geography, Education,
            Political Science, Engineering, Tourism, Marketing, Law).
    """
    hits = _search_online_literature(
        query=query,
        zot=_get_zot(),
        year_from=year_from,
        year_to=year_to,
        limit=limit,
        sort_by=sort_by,
        fields_of_study=fields_of_study,
    )
    result_list = [h.__dict__ for h in hits]
    response = {
        "results": result_list,
        "count": len(result_list),
        "verified_sources_only": True,
    }
    if not result_list:
        response["[MATERIAL GAP]"] = (
            "NO_RESULTS_FOUND. This tool returned zero verified papers. "
            "DO NOT fabricate or recall papers from memory. "
            "REQUIRED: Report gap honestly, suggest alternative queries or broader filters."
        )
    return response


@mcp.tool()
@_safe_tool
def search_cnki_literature(
    query: str,
    year_from: int | None = None,
    year_to: int | None = None,
    search_field: str = "SU",
    author: str = "",
    journal: str = "",
    source_categories: list[str] | None = None,
    limit: int = 20,
    sort_by: Literal["relevance", "citations"] = "relevance",
) -> dict:
    """Search CNKI (中国知网) for Chinese journal papers. Disabled by default.

    ONLY call when user explicitly requests Chinese literature (中文文献, 知网, CNKI, 核心期刊).
    For bilingual requests: call both search_online_literature AND this tool.

    Args:
        query: Keywords (Chinese or English).
        year_from/year_to: Publication year window.
        search_field: SU=主题, TI=篇名, KY=关键词, AB=摘要 (default SU).
        author: Author filter.
        journal: Journal filter.
        source_categories: e.g. ["CSSCI", "北大核心", "SCI"].
        limit: Max hits (default 20).
        sort_by: "relevance" or "citations".
    """
    result = _search_cnki_literature(
        query=query,
        zot=_get_zot(),
        year_from=year_from,
        year_to=year_to,
        search_field=search_field,
        author=author,
        journal=journal,
        source_categories=normalize_list(source_categories, "source_categories"),
        limit=limit,
        sort_by=sort_by,
    )
    response = {
        **{k: v for k, v in result.items() if k != "hits"},
        "hits": [h.__dict__ for h in result["hits"]],
        "verified_sources_only": True,
    }
    if not result["hits"]:
        response["[MATERIAL GAP]"] = (
            "NO_CNKI_RESULTS. This tool returned zero verified papers from CNKI. "
            "DO NOT fabricate or recall papers from memory. "
            "REQUIRED: Report gap honestly. Suggest: check CNKI login, simplify query, broaden year range."
        )
    return response


@mcp.tool()
@_safe_tool
def find_related_literature(
    scope: Literal["online", "cnki", "both"] = "online",
    title: str = "",
    abstract: str = "",
    keywords: list[str] | None = None,
    doi: str = "",
    reference_dois: list[str] | None = None,
    fields_of_study: list[str] | None = None,
    source_categories: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 30,
    sort_by: Literal["relevance", "citations"] = "relevance",
) -> dict:
    """Find related literature — ONE call, 5 parallel strategies, verified results.

    PREFERRED tool when user provides a paper and wants related literature.
    Runs in parallel: Corpus-First, keyword search, citation network,
    S2 recommendations, and OpenAlex Related Works. Results are deduplicated
    and verified against 3 bibliographic indices.

    CORPUS-FIRST (most effective): pass reference_dois with 3-8 DOIs from the
    paper's reference list. Always try to extract these when reading a paper.

    Args:
        scope: "online" (default), "cnki", or "both".
        title: Paper title.
        abstract: Paper abstract (helps query generation).
        keywords: Paper keywords (most effective for queries).
        doi: Seed paper DOI (enables citation network + S2 + related works).
        reference_dois: 3-8 DOIs from paper's references (triggers Corpus-First).
        fields_of_study: Discipline filter (Business, Economics, Sociology, etc.).
        source_categories: CNKI filter: ["CSSCI", "北大核心", "SCI"].
        year_from/year_to: Publication year window.
        limit: Max results per scope (default 30).
        sort_by: "relevance" or "citations".
    """
    result = _find_related_literature(
        scope=scope,
        title=title,
        abstract=abstract,
        keywords=normalize_list(keywords, "keywords"),
        doi=doi,
        reference_dois=normalize_list(reference_dois, "reference_dois"),
        fields_of_study=normalize_list(fields_of_study, "fields_of_study"),
        source_categories=normalize_list(source_categories, "source_categories"),
        year_from=year_from,
        year_to=year_to,
        limit=limit,
        sort_by=sort_by,
        zot=_get_zot(),
    )
    if "online_hits" in result:
        result["online_hits"] = [h.__dict__ for h in result["online_hits"]]
    if "cnki_hits" in result:
        result["cnki_hits"] = [h.__dict__ for h in result["cnki_hits"]]

    # Structural anti-hallucination markers
    result["verified_sources_only"] = True
    total_hits = result.get("online_count", 0) + result.get("cnki_count", 0)
    if total_hits == 0:
        result["[MATERIAL GAP]"] = (
            "NO_RESULTS_FOUND. This tool returned zero verified papers. "
            "DO NOT fabricate or recall papers from memory. "
            "REQUIRED ACTIONS: (1) Report gap honestly to user, "
            "(2) Suggest: provide reference_dois from paper's bibliography for Corpus-First search, "
            "(3) Or try different keywords / broaden year range / remove field filter."
        )
    return result


@mcp.tool()
@_safe_tool
def expand_citation_network(
    dois: list[str] | None = None,
    doi: str = "",
    title: str = "",
    fields_of_study: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 30,
) -> dict:
    """Explore citation neighborhood: papers that cite or are cited by seed paper(s).

    Useful when keyword search fails for niche topics. If the paper has no DOI,
    use DOIs of its key references as seeds.

    Args:
        dois: List of seed DOIs (preferred for multi-seed expansion).
        doi: Single seed DOI.
        title: Paper title (fallback if no DOI).
        fields_of_study: Discipline filter.
        year_from/year_to: Publication year window.
        limit: Max total results (default 30).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from research_core.sources.openalex import (
        get_cited_by,
        get_references,
        resolve_openalex_id,
    )

    # Normalize input: support both single doi and multi-doi
    seed_dois = normalize_list(dois, "dois") or []
    if not seed_dois and doi:
        seed_dois = [doi]
    if not seed_dois and not title:
        return {"error": "Provide at least doi, dois, or title to identify seed paper(s)."}

    # Resolve all seeds to OpenAlex IDs
    resolved_ids: list[str] = []
    failed_seeds: list[str] = []

    for d in seed_dois:
        oa_id = resolve_openalex_id(doi=d.strip())
        if oa_id:
            resolved_ids.append(oa_id)
        else:
            failed_seeds.append(d)

    if not resolved_ids and title:
        oa_id = resolve_openalex_id(title=title)
        if oa_id:
            resolved_ids.append(oa_id)

    if not resolved_ids:
        return {
            "error": "Could not find any seed paper(s) in OpenAlex. Verify the DOIs are correct.",
            "failed_dois": failed_seeds,
            "title_provided": title[:80] if title else "",
        }

    norm_fields = normalize_list(fields_of_study, "fields_of_study")
    per_seed_limit = max(limit // len(resolved_ids), 10)
    half = max(per_seed_limit // 2, 5)

    all_citing: list = []
    all_refs: list = []

    with ThreadPoolExecutor(max_workers=min(len(resolved_ids) * 2, 8)) as pool:
        futures = {}
        for oa_id in resolved_ids:
            futures[pool.submit(get_cited_by, oa_id, year_from=year_from, year_to=year_to,
                                fields_of_study=norm_fields, limit=half)] = ("citing", oa_id)
            futures[pool.submit(get_references, oa_id, year_from=year_from, year_to=year_to,
                                fields_of_study=norm_fields, limit=half)] = ("refs", oa_id)

        for future in as_completed(futures):
            kind, _ = futures[future]
            try:
                papers = future.result()
                if kind == "citing":
                    all_citing.extend(papers)
                else:
                    all_refs.extend(papers)
            except Exception:
                pass

    # Deduplicate by DOI
    def _dedup(papers: list) -> list:
        seen: set[str] = set()
        unique = []
        for p in papers:
            key = p.doi.lower() if p.doi else p.title.lower()[:50]
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        return unique

    all_citing = _dedup(all_citing)
    all_refs = _dedup(all_refs)
    all_citing.sort(key=lambda p: p.citation_count, reverse=True)
    all_refs.sort(key=lambda p: p.citation_count, reverse=True)
    all_citing = all_citing[:limit]
    all_refs = all_refs[:limit]

    def _paper_to_dict(p) -> dict:
        d = p.__dict__ if hasattr(p, "__dict__") else dict(p)
        if p.doi:
            d["source_url"] = f"https://doi.org/{p.doi}"
        elif p.source_id:
            d["source_url"] = p.source_id
        else:
            d["source_url"] = ""
        return d

    response = {
        "seeds_resolved": len(resolved_ids),
        "failed_seeds": failed_seeds if failed_seeds else None,
        "citing_papers": [_paper_to_dict(p) for p in all_citing],
        "citing_count": len(all_citing),
        "referenced_papers": [_paper_to_dict(p) for p in all_refs],
        "references_count": len(all_refs),
        "verified_sources_only": True,
    }
    if not all_citing and not all_refs:
        response["[MATERIAL GAP]"] = (
            "NO_CITATION_NETWORK_RESULTS. Forward and backward citations both empty. "
            "DO NOT fabricate or recall papers from memory. "
            "REQUIRED: Report gap honestly. The DOI may not be indexed in OpenAlex."
        )
    return response


@mcp.tool()
@_safe_tool
def cnki_add_to_zotero(
    export_ids: list[str],
) -> dict:
    """Add CNKI papers directly to Zotero — no DOI needed.

    Use after search_cnki_literature to import selected papers. Takes the
    export_id field(s) from search results, calls CNKI's internal export API
    to get full metadata (title, authors, journal, abstract, keywords, etc.),
    then pushes to Zotero's local Connector API.

    Requires Zotero desktop to be running (localhost:23119).

    Workflow:
    1. search_cnki_literature → user picks papers
    2. cnki_add_to_zotero(export_ids=[hit.export_id for selected hits])
    3. Papers appear in Zotero's currently selected collection

    When NOT to use:
    - Paper has a DOI and user wants international metadata → use add_paper(doi).
    - User hasn't searched CNKI yet → use search_cnki_literature first.

    Args:
        export_ids: List of export_id strings from search_cnki_literature results.

    Returns:
        {success, message, papers_saved, papers: [{title, authors, journal, year}]}.
    """
    result = _cnki_add_to_zotero(
        export_ids=normalize_list(export_ids, "export_ids"),
    )
    return result.__dict__


@mcp.tool()
@_safe_tool
def cnki_paper_detail(cnki_url: str) -> dict:
    """Extract full metadata from a CNKI paper's detail page.

    Navigates to the paper URL and extracts: title, authors, affiliations,
    abstract, keywords, DOI (if available), fund info, ISSN, and export_id.

    Use when the user asks about a specific CNKI paper's details, or to check
    if a DOI exists before deciding how to import to Zotero.

    If DOI is found → suggest add_paper(identifier=doi) for standard import.
    If no DOI → suggest cnki_add_to_zotero(export_ids=[export_id]) for direct import.

    When NOT to use:
    - User just wants to add a paper to Zotero → use cnki_add_to_zotero directly.
    - User wants to search → use search_cnki_literature.

    Args:
        cnki_url: Full URL to the CNKI paper detail page (from search result's cnki_url field).

    Returns:
        {title, authors, affiliations, abstract, keywords, doi, issn, export_id,
         has_doi, zotero_hint, ...}.
    """
    return _cnki_paper_detail(cnki_url=cnki_url)


@mcp.tool()
@_safe_tool
def cnki_navigate_pages(
    action: str = "next",
    sort_by: str = "",
) -> dict:
    """Fetch more CNKI results or change sort order (auto-pagination).

    This tool expands CNKI search results beyond the initial page. Call it
    PROACTIVELY (without user explicitly asking for "next page") when:

    1. User requests a LARGE number of papers (e.g. limit > 20, or "找30篇", "多找一些")
       and the first page didn't return enough results.
    2. User asks for THOROUGH / DEEP search (e.g. "深入检索", "全面检索", "尽量多找").
    3. The agent judges that current results are insufficient to fulfill the user's
       request (e.g. user wants comprehensive coverage on a topic, or asked for
       high-cited papers but the first page had few highly-cited ones).
    4. User explicitly says "more results", "还有吗", "继续找", "下一批".

    Do NOT use this when:
    - No prior CNKI search was performed → use search_cnki_literature first.
    - User wants a completely different query → use search_cnki_literature (fresh search).
    - First page already has enough results for the user's request.

    Actions:
    - Pagination: action="next" (default), "previous", or a page number like "3"
    - Sorting: set sort_by="citations"/"date"/"downloads"/"relevance"/"comprehensive"
      (action is ignored when sort_by is provided)

    Args:
        action: "next", "previous", or a page number string. Default "next".
        sort_by: If provided, changes sort order instead of paginating.

    Returns:
        {action, total, page, hits: [...]} with additional results.
    """
    result = _cnki_navigate_pages(action=action, sort_by=sort_by)
    if "hits" in result:
        result["hits"] = [h.__dict__ for h in result["hits"]]
    return result


@mcp.tool()
@_safe_tool
def find_similar_papers(item_key: str, limit: int = 10) -> list[dict]:
    """Find papers similar to a SPECIFIC paper the user has identified.

    Use this when the user says things like "find more papers like THIS one",
    "papers with the same methodology as X", or "what else is in my library
    related to this study". The input is the key of a known paper, NOT a query.

    When NOT to use:
    - User is searching by topic words → use search_papers.
    - User wants the contents of the source paper itself → use get_paper_content.

    Args:
        item_key: The Zotero item key of the source paper.
        limit: Max number of similar papers to return.

    Returns:
        Ranked list of similar papers, each with key, title, authors, year,
        relevance score, and a representative passage from each match.
    """
    hits = _find_similar_papers(item_key, _get_zot(), _get_retriever(), limit=limit)
    return [h.__dict__ for h in hits]


@mcp.tool()
@_safe_tool
def browse_library(
    scope: Literal["collections", "tags", "recent", "collection_items"],
    collection_key: str = "",
    limit: int = 20,
) -> dict:
    """Explore the STRUCTURE of the Zotero library (not its content).

    Use this for navigation, not for finding papers by topic. Choose `scope`:
      - "collections":      list all collections (folders) with their keys
      - "tags":             list all tags used in the library
      - "recent":           list recently added papers
      - "collection_items": list papers inside a specific collection (requires collection_key)

    When NOT to use:
    - User wants papers by topic or keyword → use search_papers.
    - User wants the metadata of one specific paper → use get_paper.

    Returns:
        {scope, items: [...], total}. Each item carries `key`, `name`/`tag`/`title` as
        appropriate for the scope.
    """
    res = _browse_library(scope, _get_zot(), collection_key=collection_key, limit=limit)
    return res.__dict__


@mcp.tool()
@_safe_tool
def find_duplicates() -> list[dict]:
    """Find duplicate papers in the library.

    Scans all items and groups them by normalized title or DOI match. Use this
    when the user wants to clean up their library or check for duplicate entries.

    When NOT to use:
    - User wants to find papers by topic → use search_papers.
    - User wants papers similar to one paper → use find_similar_papers.

    Returns:
        List of duplicate groups. Each group has `items` (list of papers with
        key, title, authors, year, doi) and `match_reason` ('doi_match' or 'title_match').
    """
    groups = _find_duplicates(_get_zot())
    return [g.__dict__ for g in groups]


@mcp.tool()
@_safe_tool
def merge_duplicates(
    keeper_key: str,
    duplicate_keys: list[str],
    confirm: bool = False,
) -> dict:
    """Merge duplicate papers into a single keeper item.

    SAFETY: defaults to dry-run. First call shows what will be merged (tags,
    collections, children to re-parent). """ + _WRITE_CONFIRMATION_POLICY + """

    The merge process:
    1. Combines tags from all duplicates into the keeper
    2. Adds the keeper to any collections the duplicates belong to
    3. Re-parents child items (notes, attachments) to the keeper
       (skips duplicate attachments based on contentType+filename+md5)
    4. Moves duplicate items to Zotero trash (not permanent delete)

    Use this AFTER find_duplicates has identified duplicate groups.

    When NOT to use:
    - Duplicates haven't been identified yet → use find_duplicates first.

    Args:
        keeper_key: The item key to keep (the "primary" copy).
        duplicate_keys: List of item keys to merge into keeper and trash.
        confirm: Must be True to execute. Default False = preview only.

    Returns:
        {confirmed, preview, result, error}.
    """
    res = _merge_duplicates(
        keeper_key=keeper_key,
        duplicate_keys=normalize_list(duplicate_keys, "duplicate_keys"),
        zot=_get_zot(),
        confirm=confirm,
    )
    return res.__dict__


# ╔══════════════════════════════════════════════════════════════╗
# ║  READ                                                        ║
# ╚══════════════════════════════════════════════════════════════╝


@mcp.tool()
@_safe_tool
def get_paper(item_key: str) -> dict:
    """Get metadata + abstract of ONE specific paper.

    Use this when the user has already identified a paper (via search_papers,
    find_similar_papers, browse_library, or by directly naming a key) and wants
    its bibliographic details: title, authors, date, abstract, DOI, tags, collections.

    When NOT to use:
    - User wants to read passages or specific content of the paper → use get_paper_content.
    - User wants formatted citation text → use export_bibliography.

    Args:
        item_key: The Zotero item key of the paper.

    Returns:
        Item dict with key, title, abstract, authors, date, doi, url, item_type,
        tags, collections, citation_key.
    """
    item = _get_paper(item_key, _get_zot())
    return item.model_dump()


@mcp.tool()
@_safe_tool
def get_paper_content(
    item_key: str,
    query: str = "",
    page: int | None = None,
    include_annotations: bool = False,
    mode: Literal["", "fulltext", "outline"] = "",
    limit: int = 5,
) -> dict:
    """Read content INSIDE a specific paper.

    Use this to read what a paper actually says. Five modes:
      1. `mode='fulltext'` → return the COMPLETE paper text (up to 50 pages,
         paginated). Best for "show me the whole paper" or reading long sections.
      2. `mode='outline'` → return the PDF table of contents (headings + page
         numbers). Best for "what's the structure of this paper?".
      3. `query` provided → returns the top passages semantically matching the query,
         restricted to this paper only. Best for "What does paper X say about Y?".
      4. `page` provided → returns all chunks that intersect that page number. Best
         for "What is on page N of paper X?".
      5. Neither → returns the first `limit` chunks (paper opening / intro).

    Set `include_annotations=True` to additionally return the user's OWN highlights
    and comments on this paper. Works with any mode.

    When NOT to use:
    - User wants to find papers across the library → use search_papers.
    - User wants metadata only (no body text) → use get_paper.
    - User wants to search annotations across ALL papers → use search_annotations.

    Args:
        item_key: The paper to read from. Required.
        mode: "" (default), "fulltext", or "outline".
        query: Topic words to search for inside this paper (mode 3).
        page: Specific page number (mode 4).
        include_annotations: If True, also fetch user highlights/notes.
        limit: Max number of passages to return.

    Returns:
        {item_key, title, passages, annotations, outline, fulltext}.
    """
    content = _get_paper_content(
        item_key=item_key,
        retriever=_get_retriever(),
        zot=_get_zot(),
        query=query,
        page=page,
        include_annotations=include_annotations,
        mode=mode,
        limit=limit,
    )
    return content.__dict__


@mcp.tool()
@_safe_tool
def search_annotations(query: str, limit: int = 20) -> list[dict]:
    """Search highlights and comments across ALL papers in the library.

    Use this when the user asks things like "where did I highlight gravity model",
    "find my notes about methodology", or "what did I annotate about X".

    When NOT to use:
    - User wants annotations from ONE specific paper → use get_paper_content with
      include_annotations=True.
    - User wants to find papers by topic → use search_papers.

    Args:
        query: Keyword or phrase to search within annotation text and comments.
        limit: Max results to return.

    Returns:
        List of matching annotations, each with item_key, title (of parent paper),
        annotation text, comment, page number, type, and color.
    """
    return _search_annotations(query, _get_zot(), limit=limit)


@mcp.tool()
@_safe_tool
def create_annotation(
    item_key: str,
    text: str,
    page: int = 0,
    comment: str = "",
    color: str = "#ffd400",
    tags: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """Create a highlight annotation on a paper's PDF.

    SAFETY: defaults to dry-run. First call shows a preview of the annotation
    to be created. """ + _WRITE_CONFIRMATION_POLICY + """

    Automatically resolves the parent item key to its PDF attachment — the user
    only needs to provide the paper's item_key, not the attachment key.

    Use this when the user asks to highlight text, annotate a passage, or mark
    a section of a paper.

    When NOT to use:
    - User wants to write a reading note → use add_note.
    - User wants to search existing annotations → use search_annotations.

    Args:
        item_key: The paper's item key (parent item, not the attachment).
        text: The text to highlight in the PDF.
        page: 0-based page index (default 0 = first page).
        comment: Optional comment attached to the highlight.
        color: Hex color for the highlight (default #ffd400 = yellow).
        tags: Optional tags for the annotation.
        confirm: Must be True to create. Default False = preview only.

    Returns:
        {confirmed, preview, result, error}.
    """
    res = _create_annotation(
        item_key=item_key,
        text=text,
        zot=_get_zot(),
        page=page,
        comment=comment,
        color=color,
        tags=normalize_list(tags, "tags") or None,
        confirm=confirm,
    )
    return res.__dict__


# ╔══════════════════════════════════════════════════════════════╗
# ║  WRITE                                                       ║
# ╚══════════════════════════════════════════════════════════════╝


@mcp.tool()
@_safe_tool
def suggest_citations(draft_text: str, top_k: int = 5) -> list[dict]:
    """For a passage from the USER'S OWN WRITING, suggest papers from their library
    that could be cited to support each claim.

    The input is text the user has WRITTEN (a paragraph of their draft), not a
    search query. Each suggestion includes the matching evidence text and page so
    the user can verify the citation is appropriate, plus authors and year so you
    can immediately render inline citations like (Smith, 2023).

    Workflow this fits:
        draft_text → suggest_citations → (user picks which to keep) → export_bibliography

    When NOT to use:
    - User just wants to find papers about a topic → use search_papers.
    - User asks "what does X say about Y" → use get_paper_content.

    Args:
        draft_text: The user's own paragraph or sentence (NOT a search query).
        top_k: Max number of papers to suggest (1 best chunk per paper).

    Returns:
        List of suggestions with item_key, title, authors, year, evidence_text,
        page, and relevance score.
    """
    suggestions = _suggest_citations(draft_text, _get_retriever(), _get_zot(), top_k=top_k)
    return [s.__dict__ for s in suggestions]


@mcp.tool()
@_safe_tool
def export_bibliography(
    item_keys: list[str],
    format: Literal["bibtex", "citation"] = "bibtex",
) -> dict:
    """Export formatted bibliography entries for a SET of papers.

    Use this when the user needs ready-to-paste citation text (typically BibTeX for
    LaTeX, or a plain author-year-title rendering for Word/Markdown). The papers
    must already be identified by their item keys.

    When NOT to use:
    - User hasn't picked which papers to cite yet → use suggest_citations first.
    - User wants metadata for inspection, not export → use get_paper.

    Args:
        item_keys: List of Zotero item keys to export.
        format: "bibtex" (default) or "citation" (plain "Authors (Year). Title.").

    Returns:
        {format, entries: {key: text}, combined_text: <all entries joined>}.
    """
    bib = _export_bibliography(normalize_list(item_keys, "item_keys"), _get_zot(), fmt=format)
    return bib.__dict__


@mcp.tool()
@_safe_tool
def add_paper(
    identifier: str,
    collection_key: str = "",
    tags: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """Add a NEW paper to the Zotero library by DOI, arXiv ID, ISBN, BibTeX, or URL.

    SAFETY: defaults to preview mode. First call fetches metadata and shows what
    would be created. """ + _WRITE_CONFIRMATION_POLICY + """
    On confirm=true, automatically tries to download open-access PDF via
    arXiv → Unpaywall → Semantic Scholar → PMC waterfall.

    Supported identifier formats:
      - DOI: "10.1234/abcd" or "https://doi.org/10.1234/abcd"
      - arXiv: "2301.00001" or "https://arxiv.org/abs/2301.00001"
      - ISBN: "978-0-123456-78-9" or "0123456789"
      - BibTeX: a full @article{...} entry string
      - URL: any other http(s) link

    When NOT to use:
    - Paper is already in the library → use search_papers to verify first.
    - User wants to read or cite an existing paper → use get_paper / suggest_citations.

    Args:
        identifier: DOI, arXiv ID, ISBN, BibTeX string, or URL.
        collection_key: Optional collection to add the paper to.
        tags: Optional tags to apply to the new paper.
        confirm: Must be True to actually create. Default False = preview only.

    Returns:
        {success, item_key, title, doi, pdf_attached, metadata, error}.
    """
    res = _add_paper(
        identifier=identifier,
        zot=_get_zot(),
        collection_key=collection_key,
        tags=normalize_list(tags, "tags") or None,
        confirm=confirm,
    )
    return res.__dict__


# ╔══════════════════════════════════════════════════════════════╗
# ║  MANAGE                                                      ║
# ╚══════════════════════════════════════════════════════════════╝


@mcp.tool()
@_safe_tool
def add_note(
    item_key: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """Attach a NOTE to a paper in the Zotero library.

    SAFETY: defaults to dry-run. First call returns a preview of what would be
    created. """ + _WRITE_CONFIRMATION_POLICY + """

    Use this for capturing reading notes, summaries, key insights, or any text the
    user wants permanently attached to a paper.

    When NOT to use:
    - User wants to label papers for organization → use edit_tags.
    - User wants to organize papers into folders → use manage_collections.

    Args:
        item_key: Parent paper's key.
        title: Note heading (rendered as <h1>).
        content: Note body. May contain basic HTML.
        tags: Optional tags to attach to the note itself.
        confirm: Must be True to actually write. Default False = preview only.

    Returns:
        {confirmed, preview, result, error}. When confirmed=False, the response is
        a safe preview only.
    """
    res = _add_note(
        item_key=item_key,
        title=title,
        content=content,
        zot=_get_zot(),
        tags=normalize_list(tags, "tags") or None,
        confirm=confirm,
    )
    return res.__dict__


@mcp.tool()
@_safe_tool
def edit_tags(
    item_keys: list[str],
    add: list[str] | None = None,
    remove: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """Add or remove TAGS on one or more papers.

    SAFETY: defaults to dry-run. First call returns a diff preview per paper
    (current tags, what will be added, what will be removed, resulting set). """ + _WRITE_CONFIRMATION_POLICY + """

    Use this for organizing the library: bulk-categorizing papers, marking
    to-read/read, project labels, etc.

    When NOT to use:
    - User wants to write reading notes → use add_note.
    - User just wants to see which tags exist → use browse_library(scope='tags').

    Args:
        item_keys: Paper keys to operate on. Supports batch.
        add: Tags to add (no-op for papers that already have them).
        remove: Tags to remove (no-op for papers without them).
        confirm: Must be True to apply. Default False = preview only.

    Returns:
        {confirmed, preview: {action, add, remove, items: [diffs]}, result}.
    """
    res = _edit_tags(
        item_keys=normalize_list(item_keys, "item_keys"),
        zot=_get_zot(),
        add=normalize_list(add, "add"),
        remove=normalize_list(remove, "remove"),
        confirm=confirm,
    )
    return res.__dict__


@mcp.tool()
@_safe_tool
def manage_collections(
    action: Literal["create", "add_items", "remove_items"],
    name: str = "",
    parent_key: str = "",
    collection_key: str = "",
    item_keys: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """Create collections (folders) or add/remove papers from them.

    SAFETY: defaults to dry-run. First call shows a preview. """ + _WRITE_CONFIRMATION_POLICY + """

    Actions:
      - "create":       Create a new collection. Requires `name`, optional `parent_key`.
      - "add_items":    Add papers to an existing collection. Requires `collection_key`
                        and `item_keys`.
      - "remove_items": Remove papers from a collection. Requires `collection_key`
                        and `item_keys`.

    When NOT to use:
    - User wants to browse existing collections → use browse_library(scope='collections').
    - User wants to add/remove tags → use edit_tags.

    Args:
        action: One of "create", "add_items", "remove_items".
        name: Collection name (for "create").
        parent_key: Optional parent collection key (for "create" under a folder).
        collection_key: Collection key (for "add_items" / "remove_items").
        item_keys: Paper keys to add or remove (for "add_items" / "remove_items").
        confirm: Must be True to apply. Default False = preview only.

    Returns:
        {confirmed, preview, result, error}.
    """
    res = _manage_collections(
        action=action,
        zot=_get_zot(),
        name=name,
        parent_key=parent_key,
        collection_key=collection_key,
        item_keys=normalize_list(item_keys, "item_keys"),
        confirm=confirm,
    )
    return res.__dict__


# ╔══════════════════════════════════════════════════════════════╗
# ║  ADMIN                                                       ║
# ╚══════════════════════════════════════════════════════════════╝


@mcp.tool()
@_safe_tool
def sync_index(force_rebuild: bool = False) -> dict:
    """Synchronize the vector index with the current Zotero library.

    Run this AFTER the user has added new PDFs to Zotero (otherwise the semantic
    side of search_papers will miss the new papers).

    **Incremental by default**: uses Zotero item version tracking to detect new,
    modified, and deleted items. Only changed items are re-parsed and re-indexed.
    If the embedding model has changed since the last sync, a full rebuild is
    triggered automatically.

    Set force_rebuild=True to wipe and reindex everything (slow; only use if
    chunking parameters changed or the index is suspected corrupt).

    Args:
        force_rebuild: Wipe and reindex from scratch. Default False (incremental).

    Returns:
        {added, updated, skipped, removed, failed, total_chunks_after, incremental}.
    """
    report = _sync_index(
        zot=_get_zot(),
        indexer=_get_indexer(),
        retriever=_get_retriever(),
        force_rebuild=force_rebuild,
    )
    return report.__dict__


# ── INSIGHT ──────────────────────────────────────────────────────


@mcp.tool()
@_safe_tool
def reading_status(
    item_keys: list[str] | None = None,
    scope: Literal["all", "unread", "deep_read", "browsed"] = "all",
    days_recent: int = 30,
    limit: int = 30,
) -> dict:
    """Analyze reading status of papers in the library using engagement heuristics.

    Classification rules:
    - deep_read: ≥3 annotations OR ≥1 note attached
    - browsed: 1-2 annotations OR PDF opened in Zotero reader within days_recent
    - unread: no annotations, no notes, PDF never opened recently

    Detection: uses PDF attachment's dateModified as proxy for "opened in reader"
    (Zotero 7 saves reading position on close, updating the attachment timestamp).

    Args:
        item_keys: Check specific papers. If omitted, scans recent library items.
        scope: Filter by status ("all" | "unread" | "deep_read" | "browsed").
        days_recent: Window for "recently opened PDF" detection (default 30 days).
        limit: Max results.
    """
    results = _get_reading_status(
        zot=_get_zot(),
        item_keys=normalize_list(item_keys, "item_keys"),
        scope=scope,
        days_recent=days_recent,
        limit=limit,
    )
    counts = {"deep_read": 0, "browsed": 0, "unread": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"items": results, "count": len(results), "summary": counts}


@mcp.tool()
@_safe_tool
def recommend_papers(
    days: int = 60,
    max_seeds: int = 5,
    limit: int = 15,
) -> dict:
    """Personalized paper recommendations based on your recent reading activity.

    Algorithm: identifies your most-engaged papers (by annotations, notes, recency)
    → queries OpenAlex Related Works + S2 Recommendations for each → merges,
    deduplicates, removes already-in-library papers → ranks by cross-seed frequency.

    Args:
        days: Look-back window for activity detection (default 60 days).
        max_seeds: Max seed papers to base recommendations on.
        limit: Max recommendations to return.
    """
    return _recommend_papers(
        zot=_get_zot(),
        days=days,
        max_seeds=max_seeds,
        limit=limit,
    )


@mcp.tool()
@_safe_tool
def generate_review_note(
    item_keys: list[str],
    focus: str = "",
    passages_per_paper: int = 5,
) -> dict:
    """Generate structured literature review material from multiple papers.

    Extracts relevant passages from each paper (via vector index), organizes
    them with inline citations (Author, Year, p.X). Returns structured evidence
    plus academic writing guidelines for synthesizing a thematic review.

    IMPORTANT: The AI must write a THEMATIC review (organized by intellectual
    themes and trends), NOT a paper-by-paper summary. Trace how ideas evolve,
    identify agreements/contradictions, and highlight research gaps.

    Workflow: user selects papers → this tool gathers evidence → AI synthesizes.

    Args:
        item_keys: Zotero item keys of papers to include in the review.
        focus: Optional topic/question to focus extraction on. If empty,
            returns the most important passages from each paper.
        passages_per_paper: Max passages per paper (default 5).
    """
    return _generate_review_note(
        item_keys=normalize_list(item_keys, "item_keys") or [],
        retriever=_get_retriever(),
        zot=_get_zot(),
        focus=focus,
        passages_per_paper=passages_per_paper,
    )


@mcp.tool()
@_safe_tool
def suggest_tags(
    item_keys: list[str],
) -> dict:
    """Suggest tags for papers based on title/abstract/keyword analysis.

    Recommends methodology tags (method:X), domain tags (domain:X), and
    data type tags (data:X), plus matches against existing library tags.

    IMPORTANT: This tool only SUGGESTS — it does NOT apply tags. After review,
    use edit_tags with confirm=true to apply the user's chosen tags.

    Args:
        item_keys: Papers to analyze for tag suggestions.
    """
    return _suggest_tags(
        item_keys=normalize_list(item_keys, "item_keys") or [],
        zot=_get_zot(),
    )


@mcp.tool()
@_safe_tool
def find_arguments(
    claim: str,
    item_keys: list[str] | None = None,
    top_k: int = 10,
) -> dict:
    """Find supporting and opposing evidence for a claim from your library.

    Searches for passages relevant to the user's thesis/argument, then
    classifies each by stance (support/oppose/neutral) using textual signals.
    Returns evidence grouped by stance with inline citations.

    Use this when the user is writing a Discussion section and needs to know
    which library papers support or challenge their argument.

    Args:
        claim: The thesis or argument to find evidence for/against.
        item_keys: Optional — restrict search to specific papers.
        top_k: Max total evidence passages (default 10).
    """
    return _find_arguments(
        claim=claim,
        retriever=_get_retriever(),
        zot=_get_zot(),
        top_k=top_k,
        item_keys=normalize_list(item_keys, "item_keys"),
    )


def main():
    """Entry point for `zra-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
