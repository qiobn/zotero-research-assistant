"""Unit tests for CNKI parsing helpers (no browser required)."""

from research_core.sources.cnki.models import CnkiPaperHit
from research_core.sources.cnki.search import (
    _apply_filters,
    _normalize_field,
    _normalize_source_categories,
    _parse_count,
    _parse_year,
    _raw_to_hits,
)
from research_core.sources.cnki.zotero_export import _build_zotero_item, _parse_elearning
from research_core.sources.models import OnlinePaperHit
from research_core.tools.find_related import (
    _build_relevance_terms,
    _filter_irrelevant,
    _generate_queries,
)


class TestCnkiParsing:
    def test_parse_count(self):
        assert _parse_count("403") == 403
        assert _parse_count("1,234") == 1234
        assert _parse_count("") == 0

    def test_parse_year(self):
        assert _parse_year("2020-05-15") == 2020
        assert _parse_year("2019年03期") == 2019
        assert _parse_year("") == 0

    def test_normalize_field(self):
        assert _normalize_field("主题") == "SU"
        assert _normalize_field("TI") == "TI"

    def test_normalize_source_categories(self):
        assert _normalize_source_categories(["CSSCI", "北大核心"]) == ["CSSCI", "hx"]

    def test_raw_to_hits(self):
        raw = {
            "total": "100",
            "page": "1/5",
            "results": [
                {
                    "title": "地理探测器应用研究",
                    "authors": ["王劲峰", "徐新良"],
                    "journal": "地理学报",
                    "date": "2010-09-01",
                    "citations": "1200",
                    "downloads": "5000",
                    "href": "https://kns.cnki.net/example",
                    "exportId": "abc",
                    "database": "期刊",
                    "isOnlineFirst": False,
                }
            ],
        }
        hits, total, page = _raw_to_hits(raw, limit=10)
        assert total == "100"
        assert page == "1/5"
        assert len(hits) == 1
        assert hits[0].title == "地理探测器应用研究"
        assert hits[0].citation_count == 1200
        assert hits[0].year == 2010

    def test_apply_filters_citation_sort(self):
        hits = [
            CnkiPaperHit(title="a", authors=[], year=2021, venue="", citation_count=10),
            CnkiPaperHit(title="b", authors=[], year=2020, venue="", citation_count=400),
            CnkiPaperHit(title="c", authors=[], year=2019, venue="", citation_count=50),
        ]
        out = _apply_filters(hits, year_from=2020, year_to=2021, limit=2, sort_by="citations")
        assert len(out) == 2
        assert out[0].citation_count == 400
        assert out[1].citation_count == 10


class TestCnkiZoteroExport:
    """Tests for CNKI → Zotero export parsing (no network required)."""

    _SAMPLE_ELEARNING = (
        "Title-题名: 地理探测器原理与展望<br>"
        "Author-作者: 王劲峰;徐成东<br>"
        "Source-刊名: 地理学报<br>"
        "Year-年: 2017<br>"
        "PubTime-出版时间: 2017-01-25<br>"
        "Keyword-关键词: 地理探测器;空间分异;交互作用<br>"
        "Summary-摘要: 地理探测器是探测空间分异性...<br>"
        "Roll-卷: 72<br>"
        "Period-期: 1<br>"
        "Page-页码: 116-134<br>"
        "Link-链接: https://kns.cnki.net/example"
    )

    def test_parse_elearning(self):
        parsed = _parse_elearning(self._SAMPLE_ELEARNING)
        assert parsed["title"] == "地理探测器原理与展望"
        assert parsed["authors"] == ["王劲峰", "徐成东"]
        assert parsed["journal"] == "地理学报"
        assert parsed["year"] == "2017"
        assert parsed["volume"] == "72"
        assert parsed["issue"] == "1"
        assert parsed["pages"] == "116-134"
        assert "空间分异" in parsed["keywords"]
        assert len(parsed["keywords"]) == 3
        assert "空间分异性" in parsed["abstract"]

    def test_build_zotero_item(self):
        parsed = _parse_elearning(self._SAMPLE_ELEARNING)
        item = _build_zotero_item(parsed, issn="0375-5444")
        assert item["itemType"] == "journalArticle"
        assert item["title"] == "地理探测器原理与展望"
        assert item["publicationTitle"] == "地理学报"
        assert item["date"] == "2017-01-25"
        assert item["volume"] == "72"
        assert item["issue"] == "1"
        assert item["pages"] == "116-134"
        assert item["language"] == "zh-CN"
        assert item["ISSN"] == "0375-5444"
        assert len(item["creators"]) == 2
        assert item["creators"][0] == {"name": "王劲峰", "creatorType": "author"}
        assert len(item["tags"]) == 3

    def test_build_zotero_item_no_issn(self):
        parsed = _parse_elearning(self._SAMPLE_ELEARNING)
        item = _build_zotero_item(parsed)
        assert "ISSN" not in item


class TestQueryGeneration:
    """Tests for find_related_literature query generation logic."""

    def test_chinese_keywords(self):
        qs = _generate_queries(keywords=["真实性", "非本地餐厅", "社会规范", "在线评论"])
        assert len(qs) >= 3
        assert all(isinstance(q, str) and len(q) > 0 for q in qs)
        assert any("真实性" in q for q in qs)

    def test_english_keywords(self):
        qs = _generate_queries(keywords=["authenticity", "social norms", "online reviews"])
        assert len(qs) >= 2
        assert any("authenticity" in q for q in qs)

    def test_title_only(self):
        qs = _generate_queries(title="品牌真实性对消费者购买意愿的影响")
        assert len(qs) >= 1

    def test_empty_input_returns_empty(self):
        qs = _generate_queries()
        assert qs == []

    def test_deduplication(self):
        qs = _generate_queries(keywords=["A", "B"])
        assert len(qs) == len(set(qs))

    def test_max_6_queries(self):
        qs = _generate_queries(keywords=["a", "b", "c", "d", "e", "f", "g"])
        assert len(qs) <= 6

    def test_quoted_phrases_in_queries(self):
        qs = _generate_queries(keywords=["social norms", "online reviews", "restaurant performance"])
        has_quoted = any('"' in q for q in qs)
        assert has_quoted, f"Expected quoted phrases in queries: {qs}"

    def test_relevance_filter_removes_irrelevant(self):
        terms = _build_relevance_terms(
            title="Authenticity in non-local restaurants",
            keywords=["authenticity", "social norms", "restaurant", "online reviews"],
        )
        relevant_hit = OnlinePaperHit(
            title="Authenticity and Consumer Value Ratings in Restaurants",
            authors=["A"], year=2020, doi="", abstract="Study on restaurant authenticity norms",
            venue="", publisher="", citation_count=0, is_open_access=False, oa_pdf_url="",
        )
        irrelevant_hit = OnlinePaperHit(
            title="Non-local quantum correlations in photonic systems",
            authors=["B"], year=2020, doi="", abstract="Quantum entanglement measurement",
            venue="", publisher="", citation_count=500, is_open_access=False, oa_pdf_url="",
        )
        filtered = _filter_irrelevant([relevant_hit, irrelevant_hit], terms)
        assert relevant_hit in filtered
        assert irrelevant_hit not in filtered

    def test_journal_level_in_raw_to_hits(self):
        raw = {
            "total": "10",
            "page": "1/1",
            "results": [
                {
                    "title": "测试论文",
                    "authors": ["张三"],
                    "journal": "管理世界",
                    "journalLevel": ["CSSCI", "北大核心"],
                    "date": "2023-01-01",
                    "citations": "50",
                    "downloads": "200",
                    "href": "https://kns.cnki.net/test",
                    "exportId": "abc123",
                    "database": "期刊",
                    "isOnlineFirst": False,
                }
            ],
        }
        hits, total, page = _raw_to_hits(raw, limit=10)
        assert hits[0].journal_level == ["CSSCI", "北大核心"]
        assert hits[0].venue == "管理世界"


class TestCitationNetwork:
    """Tests for citation network expansion via OpenAlex."""

    def test_resolve_openalex_id_title_validation(self):
        """Title-based resolution should reject mismatches."""
        from unittest.mock import patch, MagicMock
        from research_core.sources.openalex import resolve_openalex_id

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"title": "Completely Different Paper About Graphene", "id": "https://openalex.org/W123"},
                {"title": "Another Irrelevant Paper", "id": "https://openalex.org/W456"},
            ]
        }

        with patch("research_core.sources.openalex.httpx.get", return_value=mock_resp):
            result = resolve_openalex_id(title="Authenticity in non-local restaurant business")
            assert result is None, "Should not resolve to mismatched title"

    def test_resolve_openalex_id_title_match(self):
        """Title-based resolution should accept close matches."""
        from unittest.mock import patch, MagicMock
        from research_core.sources.openalex import resolve_openalex_id

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "Authenticity in Non-local Restaurant Business Performance",
                    "id": "https://openalex.org/W789",
                },
            ]
        }

        with patch("research_core.sources.openalex.httpx.get", return_value=mock_resp):
            result = resolve_openalex_id(title="Authenticity in non-local restaurant business performance")
            assert result == "https://openalex.org/W789"

    def test_citation_network_fallback_in_find_related(self):
        """find_related_literature should trigger citation fallback when keyword search is empty."""
        from unittest.mock import patch, MagicMock
        from research_core.tools.find_related import find_related_literature
        from research_core.sources.models import ExternalPaper

        mock_paper = ExternalPaper(
            title="Citing Paper About Restaurant Authenticity",
            authors=["Smith J"],
            year=2023,
            doi="10.1234/test",
            abstract="restaurant authenticity social norms",
            venue="Tourism Management",
            publisher="Elsevier",
            citation_count=10,
            is_open_access=False,
            oa_pdf_url="",
            source="openalex",
            source_id="https://openalex.org/W999",
        )

        with (
            patch("research_core.tools.find_related._run_online_related", return_value=[]),
            patch("research_core.sources.openalex.resolve_openalex_id", return_value="https://openalex.org/W100"),
            patch("research_core.sources.openalex.get_cited_by", return_value=[mock_paper]),
            patch("research_core.sources.openalex.get_references", return_value=[]),
        ):
            result = find_related_literature(
                scope="online",
                title="Authenticity in non-local restaurant business",
                keywords=["authenticity", "restaurant", "social norms"],
                doi="10.9999/test",
                limit=10,
            )
            assert result.get("citation_network_used") is True
            assert result["online_count"] >= 1
