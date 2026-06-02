"""Optional CNKI integration tests (require CNKI_ENABLED + Chrome CDP)."""

from __future__ import annotations

import asyncio
import os

import pytest
from project_a_mcp.server import mcp

pytestmark = pytest.mark.skipif(
    os.getenv("CNKI_ENABLED", "false").lower() not in ("1", "true", "yes"),
    reason="CNKI integration test requires CNKI_ENABLED=true and CNKI_CDP_URL",
)


async def _call(name: str, args: dict):
    res = await mcp.call_tool(name, args)
    sc = res.structured_content
    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
        return sc["result"]
    return sc


class TestSearchCnkiLiterature:
    def test_geodetector_citations(self):
        result = asyncio.run(
            _call(
                "search_cnki_literature",
                {
                    "query": "地理探测器",
                    "year_from": 2020,
                    "sort_by": "citations",
                    "limit": 10,
                },
            )
        )
        assert "hits" in result
        assert len(result["hits"]) > 0
        first = result["hits"][0]
        assert "title" in first
        assert "citation_count" in first
        assert "cnki_url" in first
