"""CNKI → Zotero export tool.

Calls CNKI's internal GetExport API via browser to get full metadata,
then pushes to Zotero's local Connector API (localhost:23119).
No DOI required — works for all Chinese papers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from research_core.sources.cnki.browser import cnki_page
from research_core.sources.cnki.exceptions import CnkiCaptchaError
from research_core.sources.cnki.zotero_export import (
    CnkiExportResult,
    export_cnki_to_zotero,
)


@dataclass
class CnkiZoteroResult:
    """Result of adding CNKI paper(s) to Zotero."""

    success: bool
    message: str
    papers_saved: int = 0
    papers: list[dict] = field(default_factory=list)


def cnki_add_to_zotero(
    export_ids: list[str],
) -> CnkiZoteroResult:
    """Add CNKI papers to Zotero using their export_ids from search results.

    Each export_id corresponds to a paper returned by search_cnki_literature.
    The export_id field is available on every search result hit.

    This function:
    1. Opens a CNKI browser session
    2. Calls CNKI's internal export API to retrieve full metadata
    3. Pushes the metadata to Zotero via its local Connector API

    No DOI needed. Works for all Chinese papers including theses.
    Requires Zotero desktop to be running.
    """
    if not export_ids:
        return CnkiZoteroResult(success=False, message="No export_ids provided.")

    with cnki_page() as page:
        page.goto("https://kns.cnki.net/kns8s/search", wait_until="domcontentloaded")

        cap = page.evaluate("""() => {
            const el = document.querySelector('#tcaptcha_transform_dy');
            return el && el.getBoundingClientRect().top >= 0;
        }""")
        if cap:
            raise CnkiCaptchaError(
                "CNKI captcha detected. Open Chrome, solve the slider captcha, then retry."
            )

        result: CnkiExportResult = export_cnki_to_zotero(export_ids, page)

    return CnkiZoteroResult(
        success=result.success,
        message=result.message,
        papers_saved=result.papers_saved,
        papers=result.papers,
    )
