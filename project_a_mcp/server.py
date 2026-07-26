"""Zotero Research Assistant — MCP server.

39 MCP tools (35 always-on + 4 CNKI-conditional), one intent each,
designed to compose via `item_key`.

Categories:
  DISCOVER   search_papers, search_online_literature, search_cnki_literature,
             find_related_literature, expand_citation_network, cnki_paper_detail,
             cnki_navigate_pages, find_similar_papers, browse_library, find_duplicates
  READ       get_paper, get_paper_content, search_annotations
  WRITE      add_paper, cnki_add_to_zotero, add_note, edit_tags, manage_collections,
             create_annotation, merge_duplicates
  CITE       suggest_citations, export_bibliography
  INSIGHT    reading_status, recommend_papers, generate_review_note, generate_reading_note,
             suggest_tags, find_arguments
  ADMIN      sync_index, check_health, inspect_index, test_recall,
             recent_retrievals, retrieval_trace, retrieval_stats,
             add_query_synonym, remove_query_synonym, list_query_synonyms,
             import_query_dict

Note: CNKI tools (search_cnki_literature, cnki_paper_detail, cnki_navigate_pages,
cnki_add_to_zotero) are only registered when CNKI_ENABLED=true.
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
from research_core.tools.health import check_health as _check_health
from research_core.tools.inspect_index import inspect_index as _inspect_index
from research_core.tools.inspect_index import test_recall as _test_recall
from research_core.tools.reading_note import generate_reading_note as _generate_reading_note
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
    """Server lifecycle: start ChromaDB server, preload models, background sync."""
    # ── Start ChromaDB embedded server (solves Windows HNSW cross-process bug) ──
    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    if os.getenv("ZRA_CHROMA_MODE", "server") == "server":
        try:
            from research_core.rag.chroma_server import start_server
            start_server(persist_dir)
        except Exception as e:
            logger.warning(f"ChromaDB embedded server failed to start: {e}")

    # ── Preload heavy models BEFORE accepting requests ──
    # Cross-Encoder (~18s) is lazy-loaded on first use. Preloading
    # synchronously ensures it's ready before the server accepts connections.
    _preload_models()

    if os.getenv("ZRA_AUTO_SYNC", "true").lower() != "false":
        t = threading.Thread(target=_background_sync, daemon=True)
        t.start()
    # Quick startup diagnostics (non-blocking, just log)
    threading.Thread(target=_startup_diagnostics, daemon=True).start()
    yield
    # ── Shutdown: stop ChromaDB server ──
    if os.getenv("ZRA_CHROMA_MODE", "server") == "server":
        try:
            from research_core.rag.chroma_server import stop_server
            stop_server()
        except Exception:
            pass


def _preload_models() -> None:
    """Preload Cross-Encoder model before server accepts connections.

    Bilingual search strategy is now owned by the external LLM (no NMT needed).
    Only the Cross-Encoder reranker needs preloading (~18s first load, ~80MB).
    """
    # Cross-Encoder reranker (~18s first load, ~80MB)
    try:
        from research_core.rag.reranker import get_reranker
        reranker = get_reranker()
        if reranker is not None:
            t = threading.Thread(target=reranker.load, daemon=True)
            t.start()
            t.join(timeout=30)
            if t.is_alive():
                logger.warning("Cross-Encoder preload taking >30s — continuing startup")
            else:
                logger.info("✓ Cross-Encoder preloaded")
    except Exception as e:
        logger.debug(f"Cross-Encoder preload skipped: {e}")


def _startup_diagnostics() -> None:
    """Log startup health status for troubleshooting.

    Heavy model preloading is handled by _preload_models() in the lifespan
    before the server accepts connections. This function only runs lightweight
    health checks (Zotero API, index count) that don't block startup.
    """
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:23119/api/", timeout=3)
        if resp.status_code == 200:
            logger.info("✓ Zotero local API reachable")
        else:
            logger.warning(f"⚠ Zotero local API returned {resp.status_code}")
    except Exception:
        logger.warning(
            "⚠ Zotero local API unreachable — "
            "ensure Zotero desktop is running with local API enabled"
        )

    try:
        count = _get_retriever().count()
        if count == 0:
            logger.warning(
                "⚠ Vector index empty — ask AI to 'sync index' or run sync_index tool"
            )
        else:
            logger.info(f"✓ Vector index ready ({count} chunks)")
    except Exception as e:
        err_msg = str(e).lower()
        if "hnsw" in err_msg or "backfill" in err_msg or "compactor" in err_msg:
            logger.error(
                f"✗ ChromaDB HNSW index corrupted ({e})\n"
                "  Auto-repair: dropping broken collection and triggering re-sync..."
            )
            try:
                from research_core.rag.store import reset_collection
                reset_collection()
                t = threading.Thread(target=_background_sync, daemon=True)
                t.start()
                logger.info("  Auto-repair sync started — search will be available shortly")
            except Exception as repair_err:
                logger.error(f"  Auto-repair failed: {repair_err}")
        else:
            logger.warning(f"⚠ Cannot check vector index: {e}")

    # Log rotation: cleanup entries older than 90 days
    try:
        from research_core.rag.logger import RetrievalLogger
        rl = RetrievalLogger(persist_dir=os.getenv("CHROMA_PERSIST_DIR", ".chroma_db"))
        removed = rl.rotate(keep_days=90)
        if removed > 0:
            logger.info(f"✓ Log rotation: removed {removed} old entries")
    except Exception:
        pass  # best-effort, never block startup


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
        "- Related to ONE paper (have title/abstract/DOI) → find_related_literature "
        "(ONE call = 5 parallel verified strategies; PREFERRED)\n"
        "- Pure citation graph from a LIST of seed DOIs, or niche topic where keyword "
        "search fails → expand_citation_network (multi-seed, no verification)\n"
        "- Reading progress / what's unread → reading_status\n"
        "- 'What should I read next?' → recommend_papers\n"
        "- 'Summarize/review these papers' → generate_review_note\n"
        "- 'Analyze this ONE paper's structure' → generate_reading_note\n"
        "- 'Suggest tags for papers' → suggest_tags (suggest only, never auto-apply)\n"
        "- 'Find evidence for/against my argument' → find_arguments\n\n"
        "CORPUS-FIRST STRATEGY (highest priority for related paper discovery):\n"
        "When analyzing a user's paper, ALWAYS extract 3-8 DOIs from its reference list "
        "and pass as reference_dois to find_related_literature. This is the most effective "
        "strategy. Also provide the paper's own DOI and set fields_of_study for niche domains.\n\n"
        "- Something broken / 'not working' / connectivity issue → check_health\n\n"
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

_CNKI_ENABLED = os.getenv("CNKI_ENABLED", "false").lower() == "true"


def _cnki_tool():
    """Register a CNKI tool only when CNKI_ENABLED=true.

    When disabled (default), CNKI tools are NOT exposed to the LLM, cutting the
    tool surface from 32 to 28 and reducing tool-selection load.
    """
    if _CNKI_ENABLED:
        return mcp.tool()

    def _noop(func):
        return func

    return _noop


_MAX_RESPONSE_CHARS = 80000
_TRIM_TARGET_CHARS = 60000


def _truncate_response(result):
    """Cap tool response size to prevent LLM context overflow.

    Strategy: trim verbose text fields FIRST (abstracts, passages, previews),
    preserving the number of items. Only drop items as a last resort.
    This ensures search results stay complete while individual entries get shorter.
    """
    if result is None:
        return result
    import json
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) <= _MAX_RESPONSE_CHARS:
        return result

    if isinstance(result, str):
        return (
            result[:_TRIM_TARGET_CHARS]
            + "\n\n[...TRUNCATED — ask for specific pages or sections...]"
        )

    if not isinstance(result, dict):
        return result

    result = dict(result)

    # Phase 1: Trim long text fields within list items (keep all items)
    _LIST_KEYS = (
        "data", "chunks", "passages", "items", "results", "hits",
        "referenced_tables", "referenced_figures",
    )
    _TEXT_FIELDS = (
        "text", "abstract", "passage", "preview", "matched_passage",
        "evidence", "content", "fulltext",
    )
    for key in _LIST_KEYS:
        if key not in result or not isinstance(result[key], list):
            continue
        for item in result[key]:
            if not isinstance(item, dict):
                continue
            for tf in _TEXT_FIELDS:
                if tf in item and isinstance(item[tf], str) and len(item[tf]) > 300:
                    item[tf] = item[tf][:300] + "..."

    # Phase 1b: Trim top-level long strings (fulltext, text)
    for key in ("fulltext", "text"):
        if key in result and isinstance(result[key], str):
            if len(result[key]) > _TRIM_TARGET_CHARS:
                result[key] = (
                    result[key][:_TRIM_TARGET_CHARS]
                    + "\n\n[...TRUNCATED...]"
                )

    # Re-check size after trimming text
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) <= _MAX_RESPONSE_CHARS:
        return result

    # Phase 2: Still too large — trim list lengths (keep at least 15 items)
    for key in _LIST_KEYS:
        if key in result and isinstance(result[key], list):
            total = len(result[key])
            if total > 15:
                result[key] = result[key][:15]
                result["_truncated"] = True
                result["_truncated_note"] = (
                    f"{key}: showing 15/{total} items. "
                    "Use a smaller limit or ask for specific items."
                )
                break

    # Phase 3: Nuclear option — hard character cap
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) > _MAX_RESPONSE_CHARS:
        result["_truncated"] = True
        result["_truncated_note"] = (
            "Response still too large after trimming. "
            "Please use a smaller limit or request specific sections."
        )

    return result


def _normalize_response(result):
    """Ensure tool responses have a consistent dict envelope.

    Bare lists get wrapped as {data: [...], count: N} so the LLM always
    receives a dict with predictable top-level keys.
    """
    if isinstance(result, list):
        return {"data": result, "count": len(result)}
    return result


def _safe_tool(func):
    """Wrap tool functions: catch exceptions, return structured diagnostics."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            result = _normalize_response(result)
            return _truncate_response(result)
        except Exception as exc:
            logger.error(f"Tool {func.__name__} failed: {exc}\n{traceback.format_exc()}")
            error_msg = str(exc)
            diagnosis = _diagnose_error(error_msg, func.__name__)
            result = {"error": error_msg, "tool": func.__name__}
            if diagnosis:
                result["diagnosis"] = diagnosis
                result["disclaimer"] = (
                    "问题分析仅供参考，若未解决请查看实际报错。"
                    " / This diagnosis is for reference only; "
                    "check the actual error if unresolved."
                )
            return result
    return wrapper


def _diagnose_error(error_msg: str, tool_name: str) -> str | None:
    """Provide a user-friendly diagnosis for common errors."""
    lower = error_msg.lower()

    if "connection refused" in lower or ("connect" in lower and "23119" in lower):
        return (
            "可能原因 / Possible cause: Zotero 桌面版未启动或本地 API 未开启。\n"
            "建议 / Suggestion: 打开 Zotero → 设置 → 高级 → 启用本地 API。\n"
            "或调用 check_health 进行完整诊断。"
        )
    if "no items" in lower or ("empty" in lower and "index" in lower):
        return (
            "可能原因 / Possible cause: 向量索引为空，需要先构建索引。\n"
            "建议 / Suggestion: 对我说 \"sync index\" 来构建论文索引。"
        )
    if "api key" in lower or "unauthorized" in lower or "403" in lower:
        return (
            "可能原因 / Possible cause: Zotero API Key 未配置或已失效。\n"
            "建议 / Suggestion: 检查 .env 中的 ZOTERO_API_KEY。\n"
            "获取地址: https://www.zotero.org/settings/keys"
        )
    if "timeout" in lower or "timed out" in lower:
        return (
            "可能原因 / Possible cause: 网络超时（可能是防火墙或代理问题）。\n"
            "建议 / Suggestion: 检查网络连接，或尝试使用代理。\n"
            "本地功能不受影响。"
        )
    if "permission" in lower and "read-only" in lower:
        return (
            "可能原因 / Possible cause: 当前为只读模式，无法执行写入操作。\n"
            "建议 / Suggestion: 在 .env 中配置 ZOTERO_API_KEY 和 ZOTERO_LIBRARY_ID 以启用写入。"
        )
    if "collection" in lower and ("not found" in lower or "does not exist" in lower):
        return (
            "可能原因 / Possible cause: 向量数据库集合不存在或损坏。\n"
            "建议 / Suggestion: 尝试重建索引: sync_index(force_rebuild=True)"
        )
    return None


_zot: ZoteroClient | None = None
_retriever: Retriever | None = None
_indexer: Indexer | None = None
_globals_lock = threading.Lock()


def _get_zot() -> ZoteroClient:
    global _zot
    if _zot is None:
        with _globals_lock:
            if _zot is None:
                try:
                    _zot = ZoteroClient(
                        library_id=os.getenv("ZOTERO_LIBRARY_ID", "0"),
                        library_type=os.getenv("ZOTERO_LIBRARY_TYPE", "user"),
                        api_key=os.getenv("ZOTERO_API_KEY", ""),
                        local=os.getenv("ZOTERO_LOCAL", "true").lower() == "true",
                    )
                except Exception as e:
                    raise ConnectionError(
                        f"Failed to initialize Zotero client: {e}. "
                        "Please ensure Zotero is running and .env is correctly configured."
                    ) from e
    return _zot


def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        with _globals_lock:
            if _retriever is None:
                try:
                    _retriever = Retriever(
                        persist_dir=os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to initialize vector index: {e}. "
                        "Check CHROMA_PERSIST_DIR in .env and ensure the directory is writable."
                    ) from e
    return _retriever


def _get_indexer() -> Indexer:
    global _indexer
    if _indexer is None:
        with _globals_lock:
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
    expand_context: bool = False,
    expand_neighbors: bool = False,
    diversity_weight: float = 0.4,
) -> dict:
    """Find papers in your Zotero library by topic, keywords, or filters.

    Pure single-query retrieval engine: BM25 + Dense (bge-m3) + Cross-Encoder
    + MMR diversity. No internal query rewriting or translation.

    *** BILINGUAL SEARCH — MANDATORY MULTI-CALL STRATEGY ***

    Single-vector embedding can only approximate one semantic direction.
    To match recall of a full search pipeline, you MUST make multiple calls.
    This is NOT optional — single-call recall is ~30% lower.

    === FOR CHINESE QUERIES (3-5 calls) ===

    1. CN original: search_papers("原始中文查询")
    2. EN translation: search_papers("your English translation")
    3. CN keywords-only: extract 3-5 key terms, search_papers("关键词1 关键词2 ...")
    4. EN synonyms: call expand_query() for methodology terms, then search_papers("EN term1 synonym1 synonym2")
    5. (if causal/relationship) CN reverse angle: search_papers("B如何影响A" or "A与B的相关性")

    Merge: pool all results, sort by frequency of appearance across calls
    (papers appearing in 3+ calls → rank higher), remove duplicates.

    For METHODOLOGY terms — ALWAYS call expand_query() before EN search:
      expand_query("两步移动搜索法") → use returned synonyms in EN query
      expand_query("多主体建模") → use returned synonyms in EN query

    Example — "社区公共体育设施与居民健康满意度的关系":
      1. search_papers("社区公共体育设施 居民 健康 满意度 关系")
      2. search_papers("community public sports facilities resident health satisfaction impact")
      3. search_papers("公共体育设施 健康 满意度 影响 因素")    ← keyword-only angle
      4. search_papers("community sports infrastructure population health wellbeing empirical") ← broader EN angle
      → Merge 4 result sets, prioritize papers found in 3+ calls

    === FOR ENGLISH QUERIES (1-2 calls) ===
    Single call is usually sufficient. Optionally add a synonym variant.

    === FOR COMPLEX / CAUSAL QUERIES ===
    Add a 5th call with reversed or complementary angle.

    *** GRAPH EXPANSION — FOR MAXIMAL RECALL ***
    After initial multi-angle search, use EXISTING tools to expand around
    the top seed papers (zero new dependencies — all tools already available):

    STEP 1 — Seed discovery:
      Run the 3-5 search_papers calls as described above.
      Identify the top 3-5 most promising papers from merged results.

    STEP 2 — Graph expansion (for each top seed paper):
      → find_similar_papers(seed_key, limit=10)
        (vector-based similar content — finds papers missed by keyword search)
      → search_papers with the seed paper's KEY TAGS
        (e.g. search_papers("", tags_include=["两步移动搜索法", "可达性"]))
      → expand_citation_network(seed_doi)
        (forward/backward citations via OpenAlex — finds citing/cited papers)

    STEP 3 — Merge all results:
      Pool everything from Steps 1+2, sort by frequency (papers found in
      3+ expansion paths rank highest), remove duplicates, present to user.

    When to use graph expansion:
      - Cross-document / relationship queries (need broad coverage)
      - User explicitly asks for comprehensive literature review
      - Initial search_papers returns <10 results
    When NOT needed:
      - Single-concept or title-match queries
      - User is in a hurry and wants quick results

    Two usage modes:
      1. With query: hybrid search (keyword + semantic + BM25 + reranking).
      2. Without query (query=\"\"): pure filter mode — returns all papers
         matching year/tags/collection, sorted by date added.

    When NOT to use:
    - User gave you a paper key → use get_paper / get_paper_content.
    - User wants more like a specific paper → use find_similar_papers.
    - User is browsing collections/tags/recent → use browse_library.
    - User wants citations for a draft → use suggest_citations.
    - User wants papers NOT in their library → use search_online_literature.

    Args:
        query: Natural-language topic or keywords in ANY language.
               bge-m3 embeddings are multilingual — CN and EN both work.
        year_from/year_to: Publication year window (inclusive).
        tags_include: Only papers carrying ALL these tags.
        tags_exclude: Drop papers carrying ANY of these tags.
        collection_key: Restrict to a single Zotero collection.
        limit: Max results (default 10).
        expand_context: Attach full section text (2000 chars vs 300).
        expand_neighbors: Attach ±1 neighbor chunks (lighter alternative).
        diversity_weight: MMR diversity (0.4=default, 0=disabled).

    Returns:
        dict with count, query, items (paper list), and context_block
        (pre-rendered Markdown for LLM reading — use this as primary output).
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
        expand_context=expand_context,
        expand_neighbors=expand_neighbors,
        diversity_weight=diversity_weight,
    )

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    context_block = renderer.render_search_results(query, hits, limit)

    return {
        "count": len(hits),
        "query": query,
        "items": [
            {
                "key": h.key,
                "title": h.title,
                "authors": h.authors,
                "year": h.year,
                "doi": h.doi,
                "tags": h.tags,
                "score": h.score,
                "matched_page": h.matched_page,
                "source": h.source,
                "relevance_tier": h.relevance_tier,
                "section_heading": h.section_heading,
                "section_type": h.section_type,
            }
            for h in hits
        ],
        "context_block": context_block,
    }


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

    When NOT to use:
    - User wants papers already IN their library → use search_papers.
    - User has a specific paper and wants related work → use find_related_literature.
    - User wants Chinese/CNKI papers → use search_cnki_literature.
    - User wants citation neighborhood of a known paper → use expand_citation_network.

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
        "source": "online_apis",
    }
    if not result_list:
        response["[MATERIAL GAP]"] = (
            "NO_RESULTS_FOUND. This tool returned zero papers. "
            "DO NOT fabricate or recall papers from memory. "
            "REQUIRED: Report gap honestly, suggest alternative queries or broader filters."
        )
    return response


@_cnki_tool()
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
        "source": "cnki",
    }
    if not result["hits"]:
        response["[MATERIAL GAP]"] = (
            "NO_CNKI_RESULTS. This tool returned zero papers from CNKI. "
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

    result["three_index_verified"] = True
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

    Best for: expanding a LIST of seed DOIs at once, or niche topics where
    keyword search fails. This is a pure citation-graph walk (no three-index
    verification, no parallel keyword/recommendation strategies).

    When NOT to use:
    - You have ONE paper (title/abstract/DOI) and want broad related work →
      use find_related_literature instead (5 verified strategies in one call).

    Args:
        dois: List of seed DOIs (preferred for multi-seed expansion).
        doi: Single seed DOI.
        title: Paper title (fallback if no DOI).
        fields_of_study: Discipline filter.
        year_from/year_to: Publication year window.
        limit: Max total results (default 30).
    """
    from research_core.tools.citation_network import (
        expand_citation_network as _expand_cn,
    )

    return _expand_cn(
        dois=normalize_list(dois, "dois"),
        doi=doi,
        title=title,
        fields_of_study=normalize_list(fields_of_study, "fields_of_study"),
        year_from=year_from,
        year_to=year_to,
        limit=limit,
    )


@_cnki_tool()
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


@_cnki_tool()
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


@_cnki_tool()
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
def find_similar_papers(item_key: str, limit: int = 10) -> dict:
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
        A dict with count, source_key, source_title, items (JSON metadata),
        and context_block (LLM-optimized Markdown with blockquote evidence).
    """
    try:
        source = _get_zot().get_item(item_key)
        source_title = source.title
    except Exception:
        source_title = f"paper {item_key}"

    hits = _find_similar_papers(item_key, _get_zot(), _get_retriever(), limit=limit)

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    context_block = renderer.render_similar_papers(source_title, hits, limit)

    return {
        "count": len(hits),
        "source_key": item_key,
        "source_title": source_title,
        "items": [
            {
                "key": h.key, "title": h.title, "authors": h.authors,
                "year": h.year, "doi": h.doi, "tags": h.tags,
                "score": h.score, "matched_page": h.matched_page,
                "source": h.source, "relevance_tier": h.relevance_tier,
                "section_heading": h.section_heading, "section_type": h.section_type,
            }
            for h in hits
        ],
        "context_block": context_block,
    }


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
        {item_key, title, passages, annotations, outline, fulltext,
         referenced_tables, referenced_figures, context_block}. context_block is
        a pre-rendered Markdown block optimized for LLM reading — use it as your
        primary source for presenting paper content. When a returned passage cites a
         table or figure (e.g. "as shown in Table 3 / Figure 2"), that table's
         content / figure's caption is resolved into `referenced_tables` /
         `referenced_figures`, and the passage lists the labels in
         `cites_tables` / `cites_figures`. Tables and figures are caption-anchored
         records (caption + rough content for tables; caption only for figures) —
         neither is structured into cells and no image is decoded.
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

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    context_block = renderer.render_paper_content(content, mode=mode, query=query)

    result = content.__dict__
    result["context_block"] = context_block
    return result


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
def suggest_citations(draft_text: str, top_k: int = 5) -> dict:
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
        A dict with:
        - count: number of suggestions
        - items: list of suggestion metadata (item_key, title, authors, year, page, relevance)
        - context_block: pre-rendered Markdown with evidence blockquotes and citations
    """
    suggestions = _suggest_citations(draft_text, _get_retriever(), _get_zot(), top_k=top_k)

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    context_block = renderer.render_citation_suggestions(draft_text, suggestions)

    return {
        "count": len(suggestions),
        "items": [
            {
                "item_key": s.item_key,
                "title": s.title,
                "authors": s.authors,
                "year": s.year,
                "page": s.page,
                "relevance": s.relevance,
            }
            for s in suggestions
        ],
        "context_block": context_block,
    }


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

    When NOT to use:
    - User wants to find papers by topic → use search_papers.
    - User wants personalized recommendations → use recommend_papers.

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

    When NOT to use:
    - User wants to search by specific topic → use search_online_literature.
    - User has a paper and wants related work → use find_related_literature.
    - User wants to check reading progress → use reading_status.

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
    ALWAYS end the review with a numbered "References" section (provided in
    the `reference_list` field of the response).

    Workflow: user selects papers → this tool gathers evidence → AI synthesizes.

    When NOT to use:
    - User wants to find evidence for/against a specific argument → use find_arguments.
    - User wants citations for their own draft text → use suggest_citations.
    - User wants to read one paper's content → use get_paper_content.

    Args:
        item_keys: Zotero item keys of papers to include in the review.
        focus: Optional topic/question to focus extraction on. If empty,
            returns the most important passages from each paper.
        passages_per_paper: Max passages per paper (default 5).
    """
    data = _generate_review_note(
        item_keys=normalize_list(item_keys, "item_keys") or [],
        retriever=_get_retriever(),
        zot=_get_zot(),
        focus=focus,
        passages_per_paper=passages_per_paper,
    )

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    data["context_block"] = renderer.render_review_materials(data)
    return data


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

    When NOT to use:
    - User already knows which tags to apply → use edit_tags directly.
    - User wants to see existing tags → use browse_library(scope='tags').

    Args:
        item_keys: Papers to analyze for tag suggestions.
    """
    data = _suggest_tags(
        item_keys=normalize_list(item_keys, "item_keys") or [],
        zot=_get_zot(),
    )

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    data["context_block"] = renderer.render_tag_suggestions(data)
    return data


@mcp.tool()
@_safe_tool
def generate_reading_note(
    item_key: str,
    sections: list[str] | None = None,
    passages_per_section: int = 2,
) -> dict:
    """Generate a structured reading note template for ONE paper.

    Extracts key academic components (RQ, methodology, data, findings,
    limitations, contribution) by querying the paper's indexed PDF content.
    Returns structured evidence that the AI refines into a polished note.

    Typical workflow: user opens a paper → this tool extracts structure →
    AI writes a concise reading note → user saves via add_note.

    When NOT to use:
    - User wants a review across MULTIPLE papers → use generate_review_note.
    - User wants to read raw content → use get_paper_content.
    - User already has a note to write → use add_note directly.

    Args:
        item_key: The paper's Zotero item key.
        sections: Which sections to extract. Default all:
            ["research_question", "methodology", "data", "key_findings",
             "limitations", "contribution"].
        passages_per_section: Max passages per section (default 2).
    """
    data = _generate_reading_note(
        item_key=item_key,
        retriever=_get_retriever(),
        zot=_get_zot(),
        sections=normalize_list(sections, "sections"),
        passages_per_section=passages_per_section,
    )

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    data["context_block"] = renderer.render_reading_note(data)
    return data


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

    When NOT to use:
    - User wants a literature review across papers → use generate_review_note.
    - User wants citations for their draft text → use suggest_citations.
    - User wants to search by topic (no specific claim) → use search_papers.

    Args:
        claim: The thesis or argument to find evidence for/against.
        item_keys: Optional — restrict search to specific papers.
        top_k: Max total evidence passages (default 10).
    """
    data = _find_arguments(
        claim=claim,
        retriever=_get_retriever(),
        zot=_get_zot(),
        top_k=top_k,
        item_keys=normalize_list(item_keys, "item_keys"),
    )

    from research_core.rag.rendering import get_renderer

    renderer = get_renderer()
    data["context_block"] = renderer.render_argument_evidence(data)
    return data


@mcp.tool()
@_safe_tool
def check_health(verbose: bool = False) -> dict:
    """Diagnose connection, index, and configuration status.

    Call this when the user reports issues ("not working", "no results", "连不上")
    or when you suspect something is misconfigured. Returns a structured report
    with status, issues found, and actionable fix suggestions.

    Checks: Zotero connectivity, write capability, vector index, embedding model,
    online API access, and environment configuration.

    When NOT to use:
    - User is asking a normal research question → use search/read tools.
    - User wants to rebuild the index → use sync_index.

    Args:
        verbose: Include extra debug details (default False).
    """
    return _check_health(
        zot=_get_zot(),
        retriever=_get_retriever(),
        verbose=verbose,
    )


@mcp.tool()
@_safe_tool
def inspect_index(item_key: str | None = None) -> dict:
    """View index quality: chunk stats, coverage, and potential issues.

    Two modes:
    - Global (no item_key): aggregate stats across the entire index —
      total chunks, avg size, papers with issues, section breakdown.
    - Per-paper (with item_key): detailed chunk list with page ranges,
      lengths, previews, and section tags.

    Use this when the user asks "how is my index?", "what's indexed?",
    or when diagnosing poor search results.

    When NOT to use:
    - User wants to search for content → use search_papers.
    - User wants to rebuild index → use sync_index.

    Args:
        item_key: Optional paper key. If given, shows that paper's chunks.
    """
    return _inspect_index(
        retriever=_get_retriever(),
        item_key=item_key,
    )


@mcp.tool()
@_safe_tool
def test_recall(item_key: str) -> dict:
    """Test retrieval quality for a specific paper.

    Searches the index using the paper's title and checks if the paper's
    own chunks appear in results. Useful for diagnosing "why can't I find
    this paper?" issues.

    Returns recall status and the matched/missed chunks.

    When NOT to use:
    - User wants to search broadly → use search_papers.
    - User wants index stats → use inspect_index.

    Args:
        item_key: The paper's Zotero item key to test.
    """
    return _test_recall(
        retriever=_get_retriever(),
        zot=_get_zot(),
        item_key=item_key,
    )


# ── Retrieval Log Tools ──────────────────────────────────────────────

@mcp.tool()
def recent_retrievals(
    n: int = 20,
    strategy: str = "",
) -> list[dict]:
    """Show recent retrieval logs — what queries were run, how they performed.

    Useful for diagnosing "why did my last search return these results?"

    Args:
        n: Number of recent entries to return (max 50).
        strategy: Filter by strategy type (hybrid / semantic / keyword / fallback).
                  Empty string = all strategies.
    """
    from research_core.tools.admin import get_recent_retrievals
    return get_recent_retrievals(n=min(n, 50), strategy=strategy)


@mcp.tool()
def retrieval_trace(trace_id: str) -> dict:
    """Replay a specific retrieval trace — see exactly what happened.

    Args:
        trace_id: The trace ID from a recent_retrievals entry.
    """
    from research_core.tools.admin import get_retrieval_trace
    result = get_retrieval_trace(trace_id)
    if result is None:
        return {"error": f"Trace not found: {trace_id}"}
    return result


@mcp.tool()
def retrieval_stats() -> dict:
    """Get aggregate retrieval statistics — total queries, avg latency, error rate."""
    from research_core.tools.admin import get_retrieval_stats
    return get_retrieval_stats()


# ── Bilingual Term Lookup & Synonym Management ──────────────────────────

@mcp.tool()
def expand_query(term: str) -> dict:
    """Look up a term in the user's bilingual thesaurus and Zotero tags.

    Use this BEFORE translating a Chinese query to English — it tells you
    the standard English name for methodology terms and domain jargon.

    Returns two lists:
    - synonyms: user-defined CN→EN mappings (from add_query_synonym)
    - tags: matching Zotero tags from the user's library

    Example:
      expand_query("两步移动搜索法")
      → {"synonyms": [], "tags": ["两步移动搜索法", "可达性", "2SFCA"]}

    Args:
        term: A Chinese methodology/domain term to look up
    """
    from research_core.rag.query_rewriter import get_rewriter
    return get_rewriter().expand(term)


@mcp.tool()
def add_query_synonym(cn_term: str, en_terms: list[str]) -> dict:
    """Add a bilingual synonym pair to the user's personal thesaurus.

    These synonyms are used by expand_query() to help translate Chinese
    methodology terms to their standard English equivalents.

    Synonyms are persisted to .chroma_db/query_dict_user.json.

    Use an empty list for en_terms to remove a synonym.

    Args:
        cn_term: Chinese term (e.g. "社会网络分析")
        en_terms: English equivalents (e.g. ["social network analysis", "SNA"])
    """
    import json
    import os

    from research_core.rag.query_rewriter import (
        add_user_synonym,
        get_user_synonyms,
    )

    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    synonym_file = os.path.join(persist_dir, "query_dict_user.json")

    if not en_terms:
        syns = get_user_synonyms()
        syns.pop(cn_term.strip(), None)
    else:
        add_user_synonym(cn_term, en_terms)

    syns = get_user_synonyms()
    os.makedirs(persist_dir, exist_ok=True)
    with open(synonym_file, "w", encoding="utf-8") as f:
        json.dump({"entries": syns}, f, ensure_ascii=False, indent=2)

    return {
        "status": "ok",
        "synonym_count": len(syns),
        "synonyms": {k: v for k, v in syns.items()},
    }


@mcp.tool()
def remove_query_synonym(term: str) -> dict:
    """Remove a user-defined bilingual synonym pair.

    Args:
        term: The Chinese term to remove (e.g. "社会网络分析")
    """
    import json
    import os

    from research_core.rag.query_rewriter import get_user_synonyms

    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    synonym_file = os.path.join(persist_dir, "query_dict_user.json")

    syns = get_user_synonyms()
    removed = syns.pop(term.strip(), None)

    if removed is not None:
        with open(synonym_file, "w", encoding="utf-8") as f:
            json.dump({"entries": syns}, f, ensure_ascii=False, indent=2)

    return {
        "status": "ok" if removed is not None else "not_found",
        "removed_term": term.strip(),
        "was_removed": removed is not None,
        "synonym_count": len(syns),
    }


@mcp.tool()
def list_query_synonyms() -> dict:
    """List all user-defined bilingual synonym pairs.

    Returns the full dictionary of CN→[EN, ...] mappings currently loaded
    in the query rewriter, including those added via add_query_synonym
    and import_query_dict.
    """
    from research_core.rag.query_rewriter import get_user_synonyms

    syns = get_user_synonyms()
    return {
        "status": "ok",
        "synonym_count": len(syns),
        "synonyms": syns,
    }


@mcp.tool()
def import_query_dict(entries: str) -> dict:
    """Bulk-import bilingual synonym pairs from a JSON string.

    The JSON must be an object where each key is a Chinese term and each
    value is a list of English equivalents:
      {"空间可达性": ["spatial accessibility", "space accessibility"],
       "职住关系": ["job-housing relationship", "jobs-housing balance"]}

    Existing entries with the same Chinese term are overwritten. Other
    existing entries are preserved. Clears the expansion cache so new
    entries take effect immediately.

    Args:
        entries: JSON string of CN→[EN, ...] mappings
    """
    import json
    import os

    from research_core.rag.query_rewriter import (
        add_user_synonym,
        get_user_synonyms,
    )

    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    synonym_file = os.path.join(persist_dir, "query_dict_user.json")

    try:
        data = json.loads(entries)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON: {e}"}

    if not isinstance(data, dict):
        return {"status": "error", "error": "JSON must be an object with CN→[EN] pairs"}

    imported = 0
    for cn_term, en_terms in data.items():
        if isinstance(cn_term, str) and isinstance(en_terms, list):
            add_user_synonym(cn_term, [str(e) for e in en_terms])
            imported += 1

    # Persist
    syns = get_user_synonyms()
    os.makedirs(persist_dir, exist_ok=True)
    with open(synonym_file, "w", encoding="utf-8") as f:
        json.dump({"entries": syns}, f, ensure_ascii=False, indent=2)

    return {
        "status": "ok",
        "imported": imported,
        "total_synonyms": len(syns),
    }


def main():
    """Entry point for `zra-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
