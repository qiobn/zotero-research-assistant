"""Playwright browser session for CNKI (CDP or local Chrome profile)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from research_core.sources.cnki.exceptions import CnkiConfigError

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page


def _cnki_enabled() -> bool:
    return os.getenv("CNKI_ENABLED", "false").lower() in ("1", "true", "yes")


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except ImportError as exc:
        raise CnkiConfigError(
            "Playwright is required for CNKI search. Install with: "
            "uv pip install 'zotero-research-assistant[cnki]' && playwright install chromium"
        ) from exc


@contextmanager
def cnki_page():
    """Yield a Playwright page connected to Chrome CDP or a local browser."""
    if not _cnki_enabled():
        raise CnkiConfigError(
            "CNKI search is disabled. Set CNKI_ENABLED=true and configure CNKI_CDP_URL "
            "(recommended) or CNKI_STORAGE_STATE in .env. See README."
        )

    sync_playwright = _require_playwright()
    cdp_url = os.getenv("CNKI_CDP_URL", "").strip()
    timeout_ms = int(os.getenv("CNKI_TIMEOUT_MS", "45000"))

    with sync_playwright() as playwright:
        browser: Browser
        owns_browser = False
        context: BrowserContext | None = None

        if cdp_url:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            owns_browser = True
            launch_kwargs: dict = {
                "headless": os.getenv("CNKI_HEADLESS", "false").lower() == "true",
            }
            if os.getenv("CNKI_USE_CHROME", "true").lower() != "false":
                launch_kwargs["channel"] = "chrome"
            browser = playwright.chromium.launch(**launch_kwargs)
            storage_state = os.getenv("CNKI_STORAGE_STATE", "").strip()
            if storage_state and os.path.isfile(storage_state):
                context = browser.new_context(storage_state=storage_state)
            else:
                context = browser.new_context()

        assert context is not None
        page: Page = context.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            yield page
        finally:
            page.close()
            if owns_browser:
                context.close()
                browser.close()
