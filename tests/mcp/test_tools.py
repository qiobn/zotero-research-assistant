"""Integration tests for all 13 MCP tools against a real Zotero library.

These require:
  1. Zotero 7 running with local API on port 23119
  2. At least a few indexed papers in .chroma_db (run sync_index first)

Run with: pytest tests/mcp/test_tools.py -v
"""

from __future__ import annotations

import asyncio

from project_a_mcp.server import mcp

# ── Helpers ──────────────────────────────────────────────────────


async def call(name: str, args: dict | None = None):
    """Call an MCP tool and extract the structured result."""
    res = await mcp.call_tool(name, args or {})
    sc = res.structured_content
    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
        return sc["result"]
    return sc


# ── DISCOVER ─────────────────────────────────────────────────────


class TestSearchPapers:
    """search_papers: find papers by topic/keyword/filter."""

    def test_basic_query(self):
        results = asyncio.run(call("search_papers", {"query": "public service", "limit": 5}))
        assert isinstance(results, list)
        assert len(results) > 0
        first = results[0]
        assert "key" in first
        assert "title" in first
        assert "score" in first

    def test_year_filter(self):
        results = asyncio.run(
            call("search_papers", {"query": "public service", "year_from": 2024, "limit": 10})
        )
        for r in results:
            assert r["year"] >= 2024, f"Year filter failed: {r['year']} < 2024 for {r['title']}"

    def test_empty_query_with_tags(self):
        results = asyncio.run(
            call("search_papers", {"query": "", "tags_include": ["ABM"], "limit": 5})
        )
        assert isinstance(results, list)

    def test_returns_source_field(self):
        results = asyncio.run(call("search_papers", {"query": "LLM agent", "limit": 3}))
        for r in results:
            assert r["source"] in ("keyword", "semantic", "hybrid")


class TestSearchOnlineLiterature:
    """search_online_literature: external OpenAlex + Semantic Scholar search."""

    def test_basic_query(self):
        results = asyncio.run(
            call("search_online_literature", {"query": "transformer language model", "limit": 5})
        )
        assert isinstance(results, list)
        assert len(results) > 0
        first = results[0]
        assert "title" in first
        assert "doi" in first
        assert "sources" in first
        assert isinstance(first["sources"], list)

    def test_year_filter(self):
        results = asyncio.run(
            call(
                "search_online_literature",
                {"query": "large language model", "year_from": 2023, "limit": 5},
            )
        )
        for r in results:
            if r["year"]:
                assert r["year"] >= 2023


class TestFindSimilarPapers:
    """find_similar_papers: given a paper key, find related ones."""

    def test_returns_different_papers(self):
        results = asyncio.run(call("find_similar_papers", {"item_key": "LRNFHGDD", "limit": 5}))
        assert isinstance(results, list)
        assert len(results) >= 1
        keys = {r["key"] for r in results}
        assert "LRNFHGDD" not in keys, "Source paper should not appear in similar results"

    def test_scores_descending(self):
        results = asyncio.run(call("find_similar_papers", {"item_key": "LRNFHGDD", "limit": 5}))
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestBrowseLibrary:
    """browse_library: navigate library structure."""

    def test_collections_scope(self):
        r = asyncio.run(call("browse_library", {"scope": "collections"}))
        assert r["scope"] == "collections"
        assert r["total"] > 0
        assert "key" in r["items"][0]
        assert "name" in r["items"][0]

    def test_tags_scope(self):
        r = asyncio.run(call("browse_library", {"scope": "tags", "limit": 5}))
        assert r["scope"] == "tags"
        assert r["total"] > 0
        assert "tag" in r["items"][0]

    def test_recent_scope(self):
        r = asyncio.run(call("browse_library", {"scope": "recent", "limit": 3}))
        assert r["scope"] == "recent"
        assert len(r["items"]) > 0
        assert "title" in r["items"][0]


class TestFindDuplicates:
    """find_duplicates: detect duplicate papers."""

    def test_returns_list(self):
        results = asyncio.run(call("find_duplicates"))
        assert isinstance(results, list)
        for group in results:
            assert "items" in group
            assert "match_reason" in group
            assert len(group["items"]) >= 2
            assert group["match_reason"] in ("doi_match", "title_match")


# ── READ ─────────────────────────────────────────────────────────


class TestGetPaper:
    """get_paper: metadata + abstract of one paper."""

    def test_returns_full_metadata(self):
        item = asyncio.run(call("get_paper", {"item_key": "LRNFHGDD"}))
        assert item["key"] == "LRNFHGDD"
        assert "title" in item
        assert "abstract" in item
        assert "authors" in item
        assert len(item["authors"]) > 0
        assert "tags" in item


class TestGetPaperContent:
    """get_paper_content: read inside a specific paper."""

    def test_query_mode(self):
        c = asyncio.run(
            call(
                "get_paper_content",
                {"item_key": "LRNFHGDD", "query": "agent-based modeling", "limit": 3},
            )
        )
        assert c["item_key"] == "LRNFHGDD"
        assert len(c["passages"]) > 0
        p = c["passages"][0]
        assert "text" in p
        assert "page_start" in p
        assert p["score"] is not None

    def test_page_mode(self):
        c = asyncio.run(call("get_paper_content", {"item_key": "LRNFHGDD", "page": 3}))
        for p in c["passages"]:
            assert p["page_start"] <= 3 <= p["page_end"]

    def test_default_mode(self):
        c = asyncio.run(call("get_paper_content", {"item_key": "LRNFHGDD", "limit": 2}))
        assert len(c["passages"]) > 0
        assert c["passages"][0]["page_start"] <= 2, "Default should return early pages"


class TestSearchAnnotations:
    """search_annotations: cross-paper annotation search."""

    def test_returns_list(self):
        results = asyncio.run(call("search_annotations", {"query": "model", "limit": 5}))
        assert isinstance(results, list)
        for r in results:
            assert "item_key" in r
            assert "title" in r
            assert "text" in r or "comment" in r


# ── WRITE ────────────────────────────────────────────────────────


class TestSuggestCitations:
    """suggest_citations: recommend library papers for user's draft text."""

    def test_returns_per_paper_results(self):
        draft = (
            "Large language models have been used as agents in transportation simulation "
            "to model traveler decision-making behavior."
        )
        results = asyncio.run(call("suggest_citations", {"draft_text": draft, "top_k": 3}))
        assert isinstance(results, list)
        assert len(results) > 0
        keys = [r["item_key"] for r in results]
        assert len(keys) == len(set(keys)), "Each paper should appear at most once"
        first = results[0]
        assert "evidence_text" in first
        assert "page" in first
        assert "relevance" in first
        assert "authors" in first
        assert "year" in first


class TestExportBibliography:
    """export_bibliography: BibTeX or formatted citations."""

    def test_bibtex_format(self):
        bib = asyncio.run(
            call("export_bibliography", {"item_keys": ["LRNFHGDD"], "format": "bibtex"})
        )
        assert bib["format"] == "bibtex"
        assert "@" in bib["combined_text"]
        assert "LRNFHGDD" in bib["entries"]

    def test_citation_format(self):
        bib = asyncio.run(
            call("export_bibliography", {"item_keys": ["LRNFHGDD"], "format": "citation"})
        )
        assert bib["format"] == "citation"
        assert "Gurcan" in bib["combined_text"] or "gurcan" in bib["combined_text"].lower()

    def test_multiple_keys(self):
        bib = asyncio.run(
            call(
                "export_bibliography",
                {"item_keys": ["LRNFHGDD", "YFW6QSP5"], "format": "bibtex"},
            )
        )
        assert len(bib["entries"]) == 2


class TestAddPaper:
    """add_paper: add papers by DOI/URL (preview mode only in tests)."""

    def test_doi_preview(self):
        r = asyncio.run(
            call("add_paper", {"identifier": "10.1038/s41586-021-03819-2", "confirm": False})
        )
        assert r["success"] is False
        assert "Preview" in r["error"]
        assert r["title"] != ""
        assert r["metadata"] is not None

    def test_invalid_doi(self):
        r = asyncio.run(
            call("add_paper", {"identifier": "10.9999/nonexistent-doi-xxx", "confirm": False})
        )
        assert r["success"] is False
        assert "Could not fetch" in r["error"]


# ── MANAGE ───────────────────────────────────────────────────────


class TestAddNote:
    """add_note: create notes with dry-run safety."""

    def test_dry_run_preview(self):
        r = asyncio.run(
            call(
                "add_note",
                {
                    "item_key": "LRNFHGDD",
                    "title": "Test note",
                    "content": "This should NOT be saved.",
                    "confirm": False,
                },
            )
        )
        assert r["confirmed"] is False
        assert r["preview"]["action"] == "create_note"
        assert "next_step" in r["preview"]


class TestEditTags:
    """edit_tags: batch tag operations with dry-run safety."""

    def test_dry_run_diff(self):
        r = asyncio.run(
            call(
                "edit_tags",
                {
                    "item_keys": ["LRNFHGDD"],
                    "add": ["test-tag-alpha"],
                    "confirm": False,
                },
            )
        )
        assert r["confirmed"] is False
        diff = r["preview"]["items"][0]
        assert "test-tag-alpha" in diff["to_add"]
        assert "test-tag-alpha" in diff["after"]

    def test_empty_op_returns_error(self):
        r = asyncio.run(
            call("edit_tags", {"item_keys": ["LRNFHGDD"], "confirm": False})
        )
        assert r["error"] != ""


class TestManageCollections:
    """manage_collections: collection operations with dry-run."""

    def test_create_preview(self):
        r = asyncio.run(
            call(
                "manage_collections",
                {"action": "create", "name": "Test Collection", "confirm": False},
            )
        )
        assert r["confirmed"] is False
        assert r["preview"]["action"] == "create_collection"
        assert r["preview"]["name"] == "Test Collection"

    def test_add_items_missing_params(self):
        r = asyncio.run(
            call("manage_collections", {"action": "add_items", "confirm": False})
        )
        assert r["error"] != ""


# ── ADMIN ────────────────────────────────────────────────────────


class TestSyncIndex:
    """sync_index: version-based incremental index maintenance."""

    def test_incremental_skips_existing(self):
        r = asyncio.run(call("sync_index", {}))
        assert isinstance(r["skipped"], list)
        assert r["total_chunks_after"] > 0
        assert r["incremental"] is True
        assert len(r["added"]) == 0, "Incremental sync should add nothing when library unchanged"
        assert len(r["updated"]) == 0, "Should have no updates on unchanged library"
