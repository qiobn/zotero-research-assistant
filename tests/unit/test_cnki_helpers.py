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
from research_core.tools.find_related import _generate_queries


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

    def test_max_5_queries(self):
        qs = _generate_queries(keywords=["a", "b", "c", "d", "e", "f", "g"])
        assert len(qs) <= 5

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
