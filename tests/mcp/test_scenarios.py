"""Scenario-based integration tests for all MCP tools.

Tests organized by real-world user scenarios rather than individual tools.
Requires:
  1. Zotero 7 running with local API (port 23119)
  2. Papers indexed in .chroma_db
  3. Network access (for online search, citation network, etc.)
  4. CNKI tests require CNKI_ENABLED=true + Chrome with CDP

Run all (excluding CNKI):
    pytest tests/mcp/test_scenarios.py -v -k "not cnki"

Run with CNKI:
    CNKI_ENABLED=true CNKI_CDP_URL=http://127.0.0.1:9222 pytest tests/mcp/test_scenarios.py -v

Run a specific scenario:
    pytest tests/mcp/test_scenarios.py -v -k "TestScenarioRelatedPaper"
"""

from __future__ import annotations

import asyncio
import os

import pytest

from project_a_mcp.server import mcp

# ── Helpers ──────────────────────────────────────────────────────


async def call(name: str, args: dict | None = None):
    """Call an MCP tool and extract the structured result."""
    res = await mcp.call_tool(name, args or {})
    sc = res.structured_content
    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
        return sc["result"]
    return sc


def run(coro):
    return asyncio.run(coro)


CNKI_ENABLED = os.getenv("CNKI_ENABLED", "false").lower() == "true"


# ══════════════════════════════════════════════════════════════════
# SCENARIO 1: User provides a paper and wants related literature
# ══════════════════════════════════════════════════════════════════


class TestScenarioRelatedPaper:
    """User gives a paper's metadata and asks 'find related literature'."""

    PAPER_TITLE = "Introducing the 15-Minute City"
    PAPER_DOI = "10.3390/smartcities4010006"
    PAPER_KEYWORDS = ["15-minute city", "urban planning", "sustainability"]

    def test_find_related_with_doi(self):
        """find_related_literature should return results using DOI + keywords."""
        result = run(call("find_related_literature", {
            "title": self.PAPER_TITLE,
            "keywords": self.PAPER_KEYWORDS,
            "doi": self.PAPER_DOI,
            "fields_of_study": ["Geography", "Environmental Science"],
            "limit": 10,
        }))
        assert "online_hits" in result
        assert result["online_count"] > 0, "Should find at least some related papers"
        assert result.get("verified_sources_only") is True

        # Check hit structure
        hit = result["online_hits"][0]
        assert "title" in hit
        assert "doi" in hit
        assert "source_url" in hit

    def test_find_related_with_reference_dois(self):
        """Corpus-First mode: providing reference DOIs should yield results."""
        result = run(call("find_related_literature", {
            "title": self.PAPER_TITLE,
            "keywords": self.PAPER_KEYWORDS,
            "reference_dois": [
                "10.1016/j.cities.2021.103229",  # Mouratidis 2021
                "10.1016/j.landurbplan.2020.103898",  # Liu et al 2020
            ],
            "fields_of_study": ["Geography"],
            "limit": 15,
        }))
        assert result["online_count"] > 0
        # Corpus-first should be triggered
        if result.get("corpus_first_used"):
            assert result["corpus_first_count"] > 0

    def test_find_related_keywords_only(self):
        """Should work with just keywords (no DOI)."""
        result = run(call("find_related_literature", {
            "keywords": ["public service facilities", "demand heterogeneity", "latent class"],
            "fields_of_study": ["Sociology", "Geography"],
            "limit": 10,
        }))
        assert "online_hits" in result
        assert isinstance(result.get("queries_generated"), list)
        assert len(result["queries_generated"]) >= 2

    def test_material_gap_handling(self):
        """Extremely niche query should return [MATERIAL GAP] not crash."""
        result = run(call("find_related_literature", {
            "keywords": ["xyznonexistenttopic12345", "abcfaketopic67890"],
            "limit": 5,
        }))
        # Should not crash; may have 0 results with MATERIAL GAP
        assert "online_count" in result
        if result["online_count"] == 0:
            assert "[MATERIAL GAP]" in result


# ══════════════════════════════════════════════════════════════════
# SCENARIO 2: User wants to explore citation neighborhood
# ══════════════════════════════════════════════════════════════════


class TestScenarioCitationNetwork:
    """User has a DOI and wants to explore who cites it / what it cites."""

    def test_expand_single_doi(self):
        """expand_citation_network with a single well-known DOI."""
        result = run(call("expand_citation_network", {
            "doi": "10.3390/smartcities4010006",
            "limit": 10,
        }))
        assert "citing_papers" in result or "error" not in result
        if "citing_papers" in result:
            assert result["citing_count"] >= 0
            assert result["references_count"] >= 0
            total = result["citing_count"] + result["references_count"]
            assert total > 0, "Should find some citations for this paper"
            # Verify structure
            if result["citing_papers"]:
                paper = result["citing_papers"][0]
                assert "title" in paper
                assert "doi" in paper

    def test_expand_multiple_dois(self):
        """Multi-seed expansion with several DOIs."""
        result = run(call("expand_citation_network", {
            "dois": [
                "10.3390/smartcities4010006",
                "10.1016/j.cities.2021.103229",
            ],
            "limit": 15,
        }))
        assert result.get("seeds_resolved", 0) >= 1

    def test_expand_invalid_doi(self):
        """Invalid DOI should return error gracefully."""
        result = run(call("expand_citation_network", {
            "doi": "10.9999/completely-fake-doi-xyz",
        }))
        assert "error" in result or result.get("citing_count", 0) == 0


# ══════════════════════════════════════════════════════════════════
# SCENARIO 3: User does a generic online search
# ══════════════════════════════════════════════════════════════════


class TestScenarioOnlineSearch:
    """User asks 'search for papers about X'."""

    def test_basic_search(self):
        """Basic online search returns verified results."""
        result = run(call("search_online_literature", {
            "query": "urban green infrastructure climate adaptation",
            "limit": 10,
        }))
        assert result["count"] > 0
        assert result["verified_sources_only"] is True
        hit = result["results"][0]
        assert hit["doi"] or hit["source_url"]
        assert hit["title"]
        assert isinstance(hit["sources"], list)

    def test_high_citation_sort(self):
        """sort_by=citations should return high-impact papers first."""
        result = run(call("search_online_literature", {
            "query": "machine learning",
            "sort_by": "citations",
            "year_from": 2020,
            "limit": 5,
        }))
        counts = [r["citation_count"] for r in result["results"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] >= 100, "Top paper should have significant citations"

    def test_fields_of_study_filter(self):
        """fields_of_study should constrain results to relevant disciplines."""
        result = run(call("search_online_literature", {
            "query": "accessibility",
            "fields_of_study": ["Geography"],
            "limit": 10,
        }))
        assert result["count"] > 0

    def test_no_results_gives_material_gap(self):
        """Impossible query should give [MATERIAL GAP], not crash."""
        result = run(call("search_online_literature", {
            "query": "xyznonexistent99999 qrstuvw88888",
            "limit": 5,
        }))
        if result["count"] == 0:
            assert "[MATERIAL GAP]" in result


# ══════════════════════════════════════════════════════════════════
# SCENARIO 4: User wants to read and cite from local library
# ══════════════════════════════════════════════════════════════════


class TestScenarioLocalLibrary:
    """User searches their Zotero library, reads papers, exports citations."""

    def test_search_then_read(self):
        """Search library → get full content from a result."""
        results = run(call("search_papers", {"query": "agent", "limit": 3}))
        assert len(results) > 0
        key = results[0]["key"]

        # Read the paper
        paper = run(call("get_paper", {"item_key": key}))
        assert paper["key"] == key
        assert paper["title"]
        assert "authors" in paper

    def test_search_then_read_content(self):
        """Search → read specific content from PDF."""
        results = run(call("search_papers", {"query": "simulation", "limit": 3}))
        if not results:
            pytest.skip("No papers matching 'simulation' in library")
        key = results[0]["key"]

        content = run(call("get_paper_content", {
            "item_key": key,
            "query": "methodology",
            "limit": 3,
        }))
        assert content["item_key"] == key
        if content["passages"]:
            p = content["passages"][0]
            assert "text" in p
            assert "page_start" in p

    def test_suggest_citations_for_draft(self):
        """Draft text → citation suggestions from library."""
        result = run(call("suggest_citations", {
            "draft_text": "Agent-based models simulate individual behavior to understand emergent patterns in complex systems.",
            "top_k": 5,
        }))
        assert isinstance(result, list)
        if result:
            assert "item_key" in result[0]
            assert "evidence_text" in result[0]
            assert "relevance" in result[0]

    def test_export_bibtex(self):
        """Export BibTeX for a paper."""
        results = run(call("search_papers", {"query": "", "limit": 1}))
        if not results:
            pytest.skip("Empty library")
        key = results[0]["key"]

        bib = run(call("export_bibliography", {
            "item_keys": [key],
            "format": "bibtex",
        }))
        assert "@" in bib["combined_text"]

    def test_browse_collections(self):
        """Browse library structure."""
        r = run(call("browse_library", {"scope": "collections"}))
        assert r["total"] >= 0
        assert isinstance(r["items"], list)


# ══════════════════════════════════════════════════════════════════
# SCENARIO 5: User wants to add an online paper to Zotero
# ══════════════════════════════════════════════════════════════════


class TestScenarioAddPaper:
    """User finds a paper online and wants to import it to Zotero."""

    def test_add_by_doi_preview(self):
        """Preview adding a paper by DOI (no actual write)."""
        result = run(call("add_paper", {
            "identifier": "10.1016/j.cities.2021.103229",
            "confirm": False,
        }))
        assert result["success"] is False  # Preview mode
        assert "Preview" in result.get("error", "")
        assert result["title"] != ""
        assert result["metadata"] is not None

    def test_add_by_arxiv_preview(self):
        """Preview adding a paper by arXiv ID."""
        result = run(call("add_paper", {
            "identifier": "https://arxiv.org/abs/2301.00234",
            "confirm": False,
        }))
        assert result["success"] is False
        # arXiv resolution may or may not return title depending on connectivity
        assert result.get("metadata") is not None or "error" in result

    def test_search_then_add_workflow(self):
        """Complete workflow: online search → pick paper → preview add."""
        # Step 1: Search
        search_result = run(call("search_online_literature", {
            "query": "15-minute city planning",
            "limit": 3,
        }))
        assert search_result["count"] > 0

        # Step 2: Pick first paper with a DOI
        doi = None
        for hit in search_result["results"]:
            if hit.get("doi"):
                doi = hit["doi"]
                break
        if not doi:
            pytest.skip("No DOI in search results")

        # Step 3: Preview add
        add_result = run(call("add_paper", {
            "identifier": doi,
            "confirm": False,
        }))
        assert add_result["title"] != ""


# ══════════════════════════════════════════════════════════════════
# SCENARIO 6: Library management (tags, collections, notes)
# ══════════════════════════════════════════════════════════════════


class TestScenarioManageLibrary:
    """User organizes their library: tags, collections, notes."""

    def test_edit_tags_preview(self):
        """Preview tag operation (dry-run)."""
        results = run(call("search_papers", {"query": "", "limit": 1}))
        if not results:
            pytest.skip("Empty library")
        key = results[0]["key"]

        r = run(call("edit_tags", {
            "item_keys": [key],
            "add": ["__test_scenario_tag__"],
            "confirm": False,
        }))
        assert r["confirmed"] is False
        assert "__test_scenario_tag__" in r["preview"]["items"][0]["to_add"]

    def test_create_collection_preview(self):
        """Preview collection creation (dry-run)."""
        r = run(call("manage_collections", {
            "action": "create",
            "name": "__Test Scenario Collection__",
            "confirm": False,
        }))
        assert r["confirmed"] is False
        assert r["preview"]["name"] == "__Test Scenario Collection__"

    def test_add_note_preview(self):
        """Preview note creation (dry-run)."""
        results = run(call("search_papers", {"query": "", "limit": 1}))
        if not results:
            pytest.skip("Empty library")
        key = results[0]["key"]

        r = run(call("add_note", {
            "item_key": key,
            "title": "Test Note",
            "content": "This is a test note that should NOT be saved.",
            "confirm": False,
        }))
        assert r["confirmed"] is False
        assert r["preview"]["action"] == "create_note"


# ══════════════════════════════════════════════════════════════════
# SCENARIO 7: Find duplicates and manage them
# ══════════════════════════════════════════════════════════════════


class TestScenarioDuplicates:
    """User wants to clean up duplicate papers."""

    def test_find_duplicates(self):
        """find_duplicates returns structured groups."""
        results = run(call("find_duplicates"))
        assert isinstance(results, list)
        for group in results:
            assert "items" in group
            assert "match_reason" in group

    def test_merge_preview(self):
        """If duplicates exist, preview a merge."""
        groups = run(call("find_duplicates"))
        if not groups:
            pytest.skip("No duplicates in library")

        keeper = groups[0]["items"][0]["key"]
        duplicate = groups[0]["items"][1]["key"]
        r = run(call("merge_duplicates", {
            "keeper_key": keeper,
            "duplicate_keys": [duplicate],
            "confirm": False,
        }))
        assert r["confirmed"] is False


# ══════════════════════════════════════════════════════════════════
# SCENARIO 8: CNKI Chinese literature search (optional)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not CNKI_ENABLED, reason="CNKI not enabled")
class TestScenarioCNKI:
    """User explicitly requests Chinese literature from CNKI."""

    def test_basic_cnki_search(self):
        """Basic CNKI keyword search."""
        result = run(call("search_cnki_literature", {
            "query": "公共服务设施",
            "limit": 5,
        }))
        assert "hits" in result
        if result["hits"]:
            hit = result["hits"][0]
            assert "title" in hit
            assert "cnki_url" in hit
            assert "journal_level" in hit

    def test_cnki_with_source_filter(self):
        """CNKI with CSSCI filter."""
        result = run(call("search_cnki_literature", {
            "query": "城市规划",
            "source_categories": ["CSSCI"],
            "limit": 5,
        }))
        assert "hits" in result

    def test_cnki_paper_detail(self):
        """Get full detail from a CNKI paper URL."""
        search = run(call("search_cnki_literature", {
            "query": "地理探测器",
            "limit": 1,
        }))
        if not search.get("hits"):
            pytest.skip("CNKI search returned no results")
        url = search["hits"][0]["cnki_url"]

        detail = run(call("cnki_paper_detail", {"cnki_url": url}))
        assert "title" in detail
        assert "abstract" in detail

    def test_find_related_both_scope(self):
        """find_related_literature with scope='both' includes CNKI."""
        result = run(call("find_related_literature", {
            "title": "城市公共服务设施需求异质性",
            "keywords": ["公共服务设施", "需求异质性", "居民画像"],
            "scope": "both",
            "limit": 10,
        }))
        assert "cnki_hits" in result or "online_hits" in result


# ══════════════════════════════════════════════════════════════════
# SCENARIO 9: Index sync and admin
# ══════════════════════════════════════════════════════════════════


class TestScenarioAdmin:
    """Admin operations: sync index."""

    def test_sync_index(self):
        """Incremental sync should report status."""
        r = run(call("sync_index", {}))
        assert "total_chunks_after" in r
        assert r["total_chunks_after"] > 0
        assert isinstance(r["skipped"], list)


# ══════════════════════════════════════════════════════════════════
# SCENARIO 10: End-to-end pipeline (search → read → cite → export)
# ══════════════════════════════════════════════════════════════════


class TestScenarioEndToEnd:
    """Full academic workflow: find paper → read → cite → export."""

    def test_full_pipeline(self):
        """Simulates a complete user session."""
        # 1. Find papers in local library
        results = run(call("search_papers", {"query": "urban", "limit": 3}))
        if not results:
            pytest.skip("No papers in library")
        key = results[0]["key"]
        title = results[0]["title"]

        # 2. Read the paper content
        content = run(call("get_paper_content", {
            "item_key": key,
            "query": "method",
            "limit": 2,
        }))
        assert content["item_key"] == key

        # 3. Get citation suggestion
        draft = f"Recent studies on urban planning suggest that {title.split()[0].lower()} is important."
        citations = run(call("suggest_citations", {
            "draft_text": draft,
            "top_k": 3,
        }))
        assert isinstance(citations, list)

        # 4. Export bibliography
        bib = run(call("export_bibliography", {
            "item_keys": [key],
            "format": "bibtex",
        }))
        assert "@" in bib["combined_text"]

        # 5. Search online for more related papers
        online = run(call("search_online_literature", {
            "query": title[:60],
            "limit": 5,
        }))
        assert online["verified_sources_only"] is True


# ══════════════════════════════════════════════════════════════════
# SCENARIO 11: Reading status and personalized recommendations
# ══════════════════════════════════════════════════════════════════


class TestScenarioReadingInsight:
    """User asks 'what have I read?' and 'what should I read next?'"""

    def test_reading_status_all(self):
        """Get reading status of all recent papers."""
        result = run(call("reading_status", {"limit": 10}))
        assert "items" in result
        assert "summary" in result
        assert isinstance(result["items"], list)
        for item in result["items"]:
            assert item["status"] in ("deep_read", "browsed", "unread")
            assert "title" in item
            assert "annotation_count" in item

    def test_reading_status_filter_scope(self):
        """Filter by specific reading status."""
        result = run(call("reading_status", {"scope": "unread", "days_recent": 1, "limit": 5}))
        for item in result["items"]:
            assert item["status"] == "unread"

    def test_reading_status_specific_keys(self):
        """Check reading status for specific papers."""
        papers = run(call("search_papers", {"query": "", "limit": 2}))
        if not papers:
            pytest.skip("No papers in library")
        keys = [p["key"] for p in papers]
        result = run(call("reading_status", {"item_keys": keys}))
        assert result["count"] <= len(keys)

    def test_recommend_papers(self):
        """Personalized recommendations based on reading activity."""
        result = run(call("recommend_papers", {"days": 90, "max_seeds": 3, "limit": 10}))
        assert "seed_papers" in result
        assert "recommendations" in result
        assert "focus_topics" in result
        # Either we get recommendations or a MATERIAL GAP message
        if result.get("message"):
            assert "[MATERIAL GAP]" in result["message"]
        else:
            assert isinstance(result["recommendations"], list)

    def test_recommend_then_add_workflow(self):
        """Full workflow: get recommendations → preview adding one."""
        result = run(call("recommend_papers", {"days": 90, "max_seeds": 3, "limit": 5}))
        recs = result.get("recommendations", [])
        if not recs:
            pytest.skip("No recommendations generated")

        # Find first recommendation with a DOI
        doi = None
        for rec in recs:
            if rec.get("doi"):
                doi = rec["doi"]
                break
        if not doi:
            pytest.skip("No DOI in recommendations")

        # Preview adding it
        add_result = run(call("add_paper", {"identifier": doi, "confirm": False}))
        assert add_result["success"] is False  # Preview mode
