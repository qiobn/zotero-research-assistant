"""Export CNKI papers to Zotero via local Connector API (localhost:23119).

Adapted from cookjohn/cnki-skills push_to_zotero.py.
No DOI required — uses CNKI's internal GetExport API for full metadata.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from loguru import logger

ZOTERO_CONNECTOR_BASE = "http://127.0.0.1:23119/connector"
_HEADERS = {
    "Content-Type": "application/json",
    "X-Zotero-Connector-API-Version": "3",
}

# CNKI internal export API
CNKI_EXPORT_API = "https://kns.cnki.net/dm8/API/GetExport"

# JS to call CNKI export API from browser context (works on any CNKI page)
EXPORT_BY_IDS_JS = """
async (params) => {
  const exportIds = params.exportIds;
  const API_URL = 'https://kns.cnki.net/dm8/API/GetExport';
  const results = [];

  for (const exportId of exportIds) {
    try {
      const resp = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          filename: exportId,
          displaymode: 'GBTREFER,elearning,EndNote',
          uniplatform: 'NZKPT'
        })
      });
      const data = await resp.json();
      if (data.code === 1) {
        const result = {};
        for (const item of data.data) {
          result[item.mode] = item.value[0];
        }
        results.push({ exportId, success: true, data: result });
      } else {
        results.push({ exportId, success: false, error: data.msg || 'Unknown error' });
      }
    } catch (e) {
      results.push({ exportId, success: false, error: e.message || String(e) });
    }
  }

  return results;
}
"""


def _parse_elearning(text: str) -> dict:
    """Parse CNKI ELEARNING export format into structured fields."""
    text = text.replace("<br>", "\n").replace("\r", "")
    text = re.sub(r"<[^>]+>", "", text)

    def get(key: str) -> str:
        m = re.search(rf"{re.escape(key)}:\s*(.+?)(?=\n|$)", text)
        return m.group(1).strip() if m else ""

    return {
        "title": get("Title-题名"),
        "authors": [a.strip() for a in get("Author-作者").split(";") if a.strip()],
        "journal": get("Source-刊名"),
        "year": get("Year-年"),
        "pubTime": get("PubTime-出版时间"),
        "keywords": [k.strip() for k in get("Keyword-关键词").split(";") if k.strip()],
        "abstract": get("Summary-摘要"),
        "volume": get("Roll-卷"),
        "issue": get("Period-期"),
        "pages": get("Page-页码"),
        "link": get("Link-链接"),
    }


def _build_zotero_item(parsed: dict, *, issn: str = "") -> dict:
    """Build Zotero-compatible item from parsed CNKI metadata."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    item = {
        "itemType": "journalArticle",
        "title": parsed.get("title", ""),
        "abstractNote": parsed.get("abstract", ""),
        "date": parsed.get("pubTime") or parsed.get("year", ""),
        "language": "zh-CN",
        "libraryCatalog": "CNKI",
        "accessDate": now,
        "volume": parsed.get("volume", ""),
        "pages": parsed.get("pages", ""),
        "publicationTitle": parsed.get("journal", ""),
        "issue": parsed.get("issue", ""),
        "creators": [
            {"name": a, "creatorType": "author"} for a in parsed.get("authors", [])
        ],
        "tags": [{"tag": k, "type": 1} for k in parsed.get("keywords", [])],
        "attachments": [],
    }
    if parsed.get("link"):
        item["url"] = parsed["link"]
    if issn:
        item["ISSN"] = issn

    return item


def _make_session_id(items: list[dict]) -> str:
    key = "|".join(sorted(item.get("title", "") for item in items))
    return hashlib.md5(key.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]


def _zotero_connector_available() -> bool:
    """Check if Zotero desktop is running with connector API."""
    try:
        r = httpx.get(f"{ZOTERO_CONNECTOR_BASE}/ping", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _push_to_zotero(items: list[dict], uri: str = "") -> tuple[int, str]:
    """Push items to Zotero via saveItems. Returns (status_code, message)."""
    session_id = _make_session_id(items)

    for i, item in enumerate(items):
        if "id" not in item:
            item["id"] = f"cnki_{session_id}_{i}"

    data = {
        "sessionID": session_id,
        "uri": uri,
        "items": items,
    }

    try:
        r = httpx.post(
            f"{ZOTERO_CONNECTOR_BASE}/saveItems",
            json=data,
            headers=_HEADERS,
            timeout=15,
        )
    except httpx.ConnectError:
        return 0, "Zotero 未运行或 Connector API 无法连接 (localhost:23119)"
    except httpx.TimeoutException:
        return -1, "请求超时，Zotero 可能在处理大量数据"

    if r.status_code == 201:
        return 201, f"已保存到 Zotero (session: {session_id})"
    elif r.status_code == 409:
        return 201, f"这批论文已保存过，无需重复添加 (session: {session_id})"
    elif r.status_code == 500:
        return 500, f"Zotero 内部错误: {r.text[:200]}"
    else:
        return r.status_code, f"未知错误 HTTP {r.status_code}: {r.text[:200]}"


@dataclass
class CnkiExportResult:
    """Result of exporting CNKI papers to Zotero."""

    success: bool
    message: str
    papers_saved: int = 0
    papers: list[dict] = field(default_factory=list)


def export_cnki_to_zotero(
    export_ids: list[str],
    page,
) -> CnkiExportResult:
    """Export papers by their CNKI export_ids (from search results) to Zotero.

    Steps:
    1. Call CNKI's GetExport API via browser to get metadata
    2. Parse ELEARNING format
    3. Push to Zotero via localhost:23119/connector/saveItems
    """
    if not export_ids:
        return CnkiExportResult(success=False, message="No export IDs provided")

    if not _zotero_connector_available():
        return CnkiExportResult(
            success=False,
            message="Zotero 未运行。请启动 Zotero 桌面端后重试。",
        )

    raw_results = page.evaluate(EXPORT_BY_IDS_JS, {"exportIds": export_ids})

    items: list[dict] = []
    paper_titles: list[dict] = []

    for raw in raw_results:
        if not raw.get("success"):
            logger.debug(f"CNKI export failed for {raw.get('exportId')}: {raw.get('error')}")
            continue

        data = raw["data"]
        elearning_text = data.get("ELEARNING", "")
        if not elearning_text:
            continue

        parsed = _parse_elearning(elearning_text)
        if not parsed.get("title"):
            continue

        endnote = data.get("ENDNOTE", "")
        issn_match = re.search(r"%@\s*([^\s<]+)", endnote)
        issn = issn_match.group(1) if issn_match else ""

        item = _build_zotero_item(parsed, issn=issn)
        items.append(item)
        paper_titles.append({
            "title": parsed["title"],
            "authors": "; ".join(parsed.get("authors", [])[:3]),
            "journal": parsed.get("journal", ""),
            "year": parsed.get("year", ""),
        })

    if not items:
        return CnkiExportResult(
            success=False,
            message="未能从 CNKI 获取到有效的论文元数据。可能需要重新登录知网。",
        )

    uri = items[0].get("url", "")
    status, msg = _push_to_zotero(items, uri=uri)

    if status == 201:
        return CnkiExportResult(
            success=True,
            message=msg,
            papers_saved=len(items),
            papers=paper_titles,
        )
    else:
        return CnkiExportResult(success=False, message=msg)
