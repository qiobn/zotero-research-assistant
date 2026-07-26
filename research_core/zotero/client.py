"""Thin wrapper around pyzotero for local and web API access.

Supports hybrid mode: local API for fast reads, web API for writes.
Enabled automatically when ZOTERO_LOCAL=true AND ZOTERO_API_KEY is set.

Includes circuit breaker: after consecutive failures, the client enters a
"down" state and returns errors immediately for a cooldown period, avoiding
cascading timeouts when Zotero desktop is unavailable.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from functools import wraps
from typing import TypeVar
from urllib.parse import unquote, urlparse

import httpx
from loguru import logger
from pyzotero import zotero

from research_core.zotero.models import Annotation, Item

F = TypeVar("F")

_api_lock = threading.RLock()

_ZOTERO_TIMEOUT = int(os.getenv("ZRA_ZOTERO_TIMEOUT", "15"))
_CIRCUIT_BREAKER_THRESHOLD = 3
_CIRCUIT_BREAKER_COOLDOWN = 30

# Item types that are never standalone "papers" and must be excluded from
# indexing/diffing. NOTE: the Zotero local API mishandles the combined
# negation filter "-attachment || note" (it silently returns ALL items),
# so we enforce this exclusion client-side instead of trusting the server.
_NON_PAPER_TYPES = frozenset({"attachment", "note", "annotation"})

_failure_count = 0
_circuit_open_until = 0.0
_circuit_lock = threading.Lock()


class ZoteroUnavailableError(ConnectionError):
    """Raised when Zotero is detected as unavailable (circuit breaker open)."""


def _check_circuit() -> None:
    """Raise immediately if circuit breaker is open."""
    global _circuit_open_until
    if _circuit_open_until > time.time():
        raise ZoteroUnavailableError(
            f"Zotero API circuit breaker is OPEN (consecutive failures >= {_CIRCUIT_BREAKER_THRESHOLD}). "
            f"Will retry in {int(_circuit_open_until - time.time())}s. "
            "Ensure Zotero desktop is running."
        )


def _record_success() -> None:
    """Reset failure counter on successful call."""
    global _failure_count, _circuit_open_until
    with _circuit_lock:
        _failure_count = 0
        _circuit_open_until = 0.0


def _record_failure() -> None:
    """Increment failure counter; open circuit breaker if threshold reached."""
    global _failure_count, _circuit_open_until
    with _circuit_lock:
        _failure_count += 1
        if _failure_count >= _CIRCUIT_BREAKER_THRESHOLD:
            _circuit_open_until = time.time() + _CIRCUIT_BREAKER_COOLDOWN
            logger.warning(
                f"Zotero circuit breaker OPEN after {_failure_count} consecutive failures. "
                f"Cooldown: {_CIRCUIT_BREAKER_COOLDOWN}s."
            )


def _with_lock(func: F) -> F:
    """Serialize Zotero API calls with circuit breaker protection.

    Timeout is enforced at the transport level (requests.Session) rather than
    via thread pools, preserving RLock reentrancy for internal calls.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        _check_circuit()
        with _api_lock:
            try:
                result = func(*args, **kwargs)
                _record_success()
                return result
            except ZoteroUnavailableError:
                raise
            except (ConnectionError, OSError, TimeoutError) as exc:
                _record_failure()
                raise ConnectionError(
                    f"Zotero API call '{func.__name__}' failed: {exc}. "
                    "Ensure Zotero desktop is running and responsive."
                ) from exc
            except Exception:
                _record_failure()
                raise
    return wrapper  # type: ignore[return-value]


class ZoteroClient:
    """Unified Zotero client supporting local, web, and hybrid API modes."""

    def __init__(
        self,
        library_id: str = "",
        library_type: str = "user",
        api_key: str = "",
        local: bool = True,
    ):
        self.local = local
        self._library_id = library_id or "0"
        self._library_type = library_type
        self._api_key = api_key

        # ── Build httpx.Client with explicit HTTPTransport ──
        # Without explicit transport, httpx on Windows sends requests in a way
        # that causes Zotero's embedded HTTP server to return 502 Bad Gateway.
        _transport = httpx.HTTPTransport()
        _http_client = httpx.Client(
            transport=_transport,
            headers={
                "User-Agent": "ZoteroResearchAssistant/0.4.8",
                "Zotero-API-Version": "3",
            },
            timeout=_ZOTERO_TIMEOUT,
            follow_redirects=True,
        )

        if local:
            self._zot = zotero.Zotero(
                self._library_id, library_type, api_key="", local=True,
                client=_http_client,
            )
            # Override to 127.0.0.1 for reliability (avoids IPv4/v6 resolution variance).
            self._zot.endpoint = "http://127.0.0.1:23119/api"
        else:
            self._zot = zotero.Zotero(
                self._library_id, library_type, api_key,
                client=_http_client,
            )

        self._write_zot: zotero.Zotero | None = None
        if local and api_key and library_id:
            self._write_zot = zotero.Zotero(
                library_id, library_type, api_key,
                client=_http_client,
            )
            logger.info("Hybrid mode enabled: local reads + web API writes")

    @property
    def can_write(self) -> bool:
        """True if write operations are available (web API or hybrid mode)."""
        return not self.local or self._write_zot is not None

    @property
    def _writer(self) -> zotero.Zotero:
        """Return the pyzotero instance to use for write operations."""
        if self._write_zot is not None:
            return self._write_zot
        if not self.local:
            return self._zot
        raise PermissionError(
            "Zotero local API is read-only. To enable writes, set ZOTERO_API_KEY "
            "and ZOTERO_LIBRARY_ID in .env. Reads will stay local (fast), writes "
            "will go through the Zotero web API."
        )

    # ── Read: items ───────────────────────────────────────────

    @_with_lock
    def search_items(
        self,
        query: str,
        limit: int = 20,
        qmode: str = "everything",
        item_type: str = "-attachment || note",
        tag: list[str] | None = None,
        collection_key: str = "",
    ) -> list[Item]:
        """Keyword search via Zotero API. Supports tag and collection filters."""
        kwargs: dict = {"q": query, "qmode": qmode, "limit": limit, "itemType": item_type}
        if tag:
            kwargs["tag"] = tag
        if collection_key:
            raw = self._zot.collection_items(collection_key, **kwargs)
        else:
            raw = self._zot.items(**kwargs)
        return [self._to_item(r) for r in raw]

    @_with_lock
    def get_item(self, key: str) -> Item:
        raw = self._zot.item(key)
        return self._to_item(raw)

    def get_items_batch(self, keys: list[str]) -> list[Item]:
        """Fetch multiple items by key. Calls get_item internally (already locked)."""
        items: list[Item] = []
        for k in keys:
            try:
                items.append(self.get_item(k))
            except Exception:
                continue
        return items

    @_with_lock
    def get_collections(self) -> list[dict]:
        return self._zot.collections()

    @_with_lock
    def get_collection_items(
        self,
        collection_key: str,
        limit: int = 50,
        item_type: str = "-attachment || note",
    ) -> list[Item]:
        raw = self._zot.collection_items(collection_key, limit=limit, itemType=item_type)
        return [self._to_item(r) for r in raw]

    @_with_lock
    def get_item_children(self, key: str) -> list[dict]:
        return self._zot.children(key)

    @_with_lock
    def get_recent(self, limit: int = 10) -> list[Item]:
        raw = self._zot.items(
            sort="dateAdded",
            direction="desc",
            limit=limit,
            itemType="-attachment || note",
        )
        return [self._to_item(r) for r in raw]

    @_with_lock
    def get_tags(self) -> list[str]:
        raw = self._zot.tags()
        out: list[str] = []
        for t in raw:
            if isinstance(t, dict):
                out.append(t.get("tag", str(t)))
            else:
                out.append(str(t))
        return out

    @_with_lock
    def get_all_items_minimal(
        self,
        limit: int = 500,
        item_type: str = "-attachment || note",
    ) -> list[Item]:
        """Fetch all top-level papers (excludes attachments/notes/annotations).

        The Zotero local API ignores the combined negation filter, so we always
        drop non-paper types client-side regardless of what the server returns.
        """
        raw = self._zot.items(limit=limit, itemType=item_type)
        items = [self._to_item(r) for r in raw]
        return [it for it in items if it.item_type not in _NON_PAPER_TYPES]

    @_with_lock
    def get_item_versions(
        self,
        item_type: str = "-attachment || note",
    ) -> dict[str, int]:
        """Return {item_key: version} for top-level papers only.

        Excludes attachments/notes/annotations. Because the local API's
        combined negation filter ("-attachment || note") is broken and returns
        everything, we compute the exclusion client-side using *positive*
        per-type version queries, which the API handles correctly.
        """
        try:
            all_versions = dict(self._zot.item_versions())
            excluded_keys: set[str] = set()
            for t in _NON_PAPER_TYPES:
                try:
                    excluded_keys.update(self._zot.item_versions(itemType=t))
                except Exception:
                    # If a per-type query fails, fall back to type-aware filtering below.
                    raise
            return {k: v for k, v in all_versions.items() if k not in excluded_keys}
        except Exception:
            items = self.get_all_items_minimal(limit=5000, item_type=item_type)
            return {it.key: it.version for it in items}

    @_with_lock
    def get_pdf_paths_for_keys(
        self,
        item_keys: list[str],
    ) -> dict[str, str]:
        """Resolve local PDF paths only for the given item keys. Returns {key: pdf_path}."""
        result: dict[str, str] = {}
        for key in item_keys:
            try:
                for ch in self._zot.children(key):
                    ch_data = ch.get("data", ch)
                    if ch_data.get("contentType") != "application/pdf":
                        continue
                    ch_key = ch_data.get("key", "")
                    storage_dir = os.path.join(
                        os.path.expanduser("~/Zotero/storage"), ch_key
                    )
                    if os.path.isdir(storage_dir):
                        for f in os.listdir(storage_dir):
                            if f.lower().endswith(".pdf"):
                                result[key] = os.path.join(storage_dir, f)
                                break
                    if key not in result:
                        path = self.get_attachment_path(ch_key)
                        if path:
                            result[key] = path
                    if key in result:
                        break
            except Exception:
                continue
        return result

    # ── Read: annotations ─────────────────────────────────────

    @_with_lock
    def get_annotations(self, item_key: str) -> list[Annotation]:
        """Return user annotations on a paper's PDF attachments (highlights, notes, etc.)."""
        annotations: list[Annotation] = []
        for ch in self._zot.children(item_key):
            ch_data = ch.get("data", ch)
            if ch_data.get("contentType") != "application/pdf":
                continue
            try:
                anns = self._zot.children(ch_data.get("key", ""))
            except Exception:
                continue
            for a in anns:
                d = a.get("data", a)
                if d.get("itemType") != "annotation":
                    continue
                page_label = d.get("annotationPageLabel", "")
                page: int | None = None
                if page_label and page_label.isdigit():
                    page = int(page_label)
                annotations.append(
                    Annotation(
                        key=d.get("key", ""),
                        text=d.get("annotationText", ""),
                        comment=d.get("annotationComment", ""),
                        page=page,
                        color=d.get("annotationColor", ""),
                        annotation_type=d.get("annotationType", "highlight"),
                    )
                )
        return annotations

    @_with_lock
    def search_all_annotations(self, query: str, limit: int = 20) -> list[dict]:
        """Search annotations across ALL papers in the library by keyword.

        Paginates through the library (100 items per page) and stops as soon as
        `limit` matches are found. To protect very large libraries from an
        O(N*M) API storm (each paper triggers a children fetch), the scan is
        bounded by ZRA_ANNOTATION_SCAN_CAP papers (default 300).
        """
        query_lower = query.lower()
        results: list[dict] = []
        page_size = 100
        start = 0
        scan_cap = int(os.getenv("ZRA_ANNOTATION_SCAN_CAP", "300"))
        scanned = 0
        cap_hit = False
        while True:
            batch = self._zot.items(
                itemType="-attachment || note", limit=page_size, start=start,
            )
            if not batch:
                break
            for raw in batch:
                item = self._to_item(raw)
                # Local API ignores "-attachment || note"; skip non-papers here.
                if item.item_type in _NON_PAPER_TYPES:
                    continue
                if scanned >= scan_cap:
                    cap_hit = True
                    break
                scanned += 1
                for ch in self._zot.children(item.key):
                    ch_data = ch.get("data", ch)
                    if ch_data.get("contentType") != "application/pdf":
                        continue
                    try:
                        anns = self._zot.children(ch_data.get("key", ""))
                    except Exception:
                        continue
                    for a in anns:
                        d = a.get("data", a)
                        if d.get("itemType") != "annotation":
                            continue
                        text_combined = f"{d.get('annotationText', '')} {d.get('annotationComment', '')}".lower()
                        if query_lower in text_combined:
                            page_label = d.get("annotationPageLabel", "")
                            page: int | None = None
                            if page_label and page_label.isdigit():
                                page = int(page_label)
                            results.append({
                                "item_key": item.key,
                                "title": item.title,
                                "annotation_key": d.get("key", ""),
                                "type": d.get("annotationType", "highlight"),
                                "text": d.get("annotationText", ""),
                                "comment": d.get("annotationComment", ""),
                                "page": page,
                                "color": d.get("annotationColor", ""),
                            })
                            if len(results) >= limit:
                                return results
            if cap_hit or len(batch) < page_size:
                break
            start += page_size

        if cap_hit:
            logger.debug(
                f"search_all_annotations: scan cap ({scan_cap} papers) reached; "
                "results may be partial. Raise ZRA_ANNOTATION_SCAN_CAP if needed."
            )
        return results

    # ── Read: attachments ─────────────────────────────────────

    def get_attachment_path(self, attachment_key: str) -> str | None:
        """Resolve an attachment's local file path via the /file redirect."""
        base = self._zot.endpoint
        url = f"{base}/users/{self._zot.library_id}/items/{attachment_key}/file"
        try:
            r = httpx.get(url, follow_redirects=False, timeout=5)
            loc = r.headers.get("location", "")
            if loc.startswith("file://"):
                path = unquote(urlparse(loc).path)
                return path if os.path.isfile(path) else None
        except Exception:
            pass
        return None

    @_with_lock
    def get_pdf_items_with_paths(
        self,
        limit: int = 500,
    ) -> list[tuple[Item, str]]:
        """Return (Item, pdf_path) for top-level items that have a locally available PDF."""
        results: list[tuple[Item, str]] = []
        for raw in self._zot.items(itemType="-attachment || note", limit=limit):
            item = self._to_item(raw)
            for ch in self._zot.children(item.key):
                ch_data = ch.get("data", ch)
                if ch_data.get("contentType") != "application/pdf":
                    continue
                ch_key = ch_data.get("key", "")
                storage_dir = os.path.join(os.path.expanduser("~/Zotero/storage"), ch_key)
                if os.path.isdir(storage_dir):
                    for f in os.listdir(storage_dir):
                        if f.lower().endswith(".pdf"):
                            results.append((item, os.path.join(storage_dir, f)))
                            break
                else:
                    path = self.get_attachment_path(ch_key)
                    if path:
                        results.append((item, path))
                        break
        return results

    # ── Read: BibTeX export ──────────────────────────────────

    @_with_lock
    def get_bibtex(self, item_keys: list[str]) -> dict[str, str]:
        """Return {item_key: bibtex_entry}. Uses Better BibTeX format when available."""
        out: dict[str, str] = {}
        base = self._zot.endpoint
        for key in item_keys:
            url = f"{base}/users/{self._zot.library_id}/items/{key}"
            try:
                r = httpx.get(url, params={"format": "bibtex"}, timeout=10)
                if r.status_code == 200 and "@" in r.text:
                    out[key] = r.text.strip()
                    continue
            except Exception:
                pass
            try:
                item = self.get_item(key)
                out[key] = self._fallback_bibtex(item)
            except Exception:
                out[key] = ""
        return out

    @staticmethod
    def _fallback_bibtex(item: Item) -> str:
        cite_key = item.citation_key or f"{(item.authors[0].split()[-1] if item.authors else 'unknown').lower()}{item.date[:4] if item.date else ''}"
        authors_str = " and ".join(item.authors)
        year = item.date[:4] if item.date and item.date[:4].isdigit() else ""
        kind = "article" if item.item_type == "journalArticle" else "misc"
        lines = [f"@{kind}{{{cite_key},"]
        if item.title:
            lines.append(f"  title = {{{item.title}}},")
        if authors_str:
            lines.append(f"  author = {{{authors_str}}},")
        if year:
            lines.append(f"  year = {{{year}}},")
        if item.doi:
            lines.append(f"  doi = {{{item.doi}}},")
        lines.append("}")
        return "\n".join(lines)

    # ── Write: notes & tags ──────────────────────────────────

    @_with_lock
    def create_note(
        self,
        parent_item_key: str,
        note_text: str,
        tags: list[str] | None = None,
    ) -> dict:
        """Attach a note to an item. Returns the created note's data."""
        w = self._writer
        template = w.item_template("note")
        template["note"] = note_text
        template["parentItem"] = parent_item_key
        if tags:
            template["tags"] = [{"tag": t} for t in tags]
        return w.create_items([template])

    @_with_lock
    def update_item_tags(
        self,
        item_key: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict:
        """Add and/or remove tags on an item. Returns {added, removed, current}."""
        w = self._writer
        raw = w.item(item_key)
        data = raw.get("data", {})
        current = {t.get("tag", "") for t in data.get("tags", [])}
        add_set = set(add or [])
        remove_set = set(remove or [])
        new_tags = (current | add_set) - remove_set
        data["tags"] = [{"tag": t} for t in sorted(new_tags) if t]
        w.update_item(raw)
        return {
            "item_key": item_key,
            "added": sorted(add_set - current),
            "removed": sorted(remove_set & current),
            "current": sorted(new_tags),
        }

    # ── Write: create items ──────────────────────────────────

    @_with_lock
    def create_item(self, item_data: dict) -> dict:
        """Create a new top-level item in Zotero. Returns the API response."""
        return self._writer.create_items([item_data])

    @_with_lock
    def attach_file(self, parent_key: str, filepath: str) -> dict:
        """Upload a file as a child attachment of an existing item."""
        return self._writer.attachment_simple([filepath], parent_key)

    # ── Write: collections ───────────────────────────────────

    @_with_lock
    def create_collection(self, name: str, parent_key: str = "") -> dict:
        """Create a new collection. Returns the API response."""
        payload: dict = {"name": name}
        if parent_key:
            payload["parentCollection"] = parent_key
        return self._writer.create_collections([payload])

    @_with_lock
    def add_to_collection(self, collection_key: str, item_keys: list[str]) -> list[dict]:
        """Add items to a collection by updating each item's collections list."""
        results: list[dict] = []
        w = self._writer
        for key in item_keys:
            raw = w.item(key)
            data = raw.get("data", {})
            cols = set(data.get("collections", []))
            if collection_key in cols:
                results.append({"item_key": key, "status": "already_in_collection"})
                continue
            cols.add(collection_key)
            data["collections"] = sorted(cols)
            w.update_item(raw)
            results.append({"item_key": key, "status": "added"})
        return results

    @_with_lock
    def remove_from_collection(self, collection_key: str, item_keys: list[str]) -> list[dict]:
        """Remove items from a collection."""
        results: list[dict] = []
        w = self._writer
        for key in item_keys:
            raw = w.item(key)
            data = raw.get("data", {})
            cols = set(data.get("collections", []))
            if collection_key not in cols:
                results.append({"item_key": key, "status": "not_in_collection"})
                continue
            cols.discard(collection_key)
            data["collections"] = sorted(cols)
            w.update_item(raw)
            results.append({"item_key": key, "status": "removed"})
        return results

    @_with_lock
    def search_collections(self, query: str) -> list[dict]:
        """Search collections by name (case-insensitive substring match)."""
        query_lower = query.lower()
        results: list[dict] = []
        for c in self._zot.collections():
            data = c.get("data", c)
            name = data.get("name", "")
            if query_lower in name.lower():
                results.append({
                    "key": data.get("key", ""),
                    "name": name,
                    "parent": data.get("parentCollection", "") or "",
                })
        return results

    # ── Duplicate detection ──────────────────────────────────

    @_with_lock
    def find_duplicates(self, limit: int = 500) -> list[list[dict]]:
        """Find duplicate items by normalized title and/or DOI match.

        Returns groups of 2+ items that appear to be duplicates.
        """
        items = self.get_all_items_minimal(limit=limit)
        by_doi: dict[str, list[Item]] = {}
        by_title: dict[str, list[Item]] = {}
        for item in items:
            if item.doi:
                by_doi.setdefault(item.doi.lower().strip(), []).append(item)
            norm = self._normalize_title(item.title)
            if norm and len(norm) > 10:
                by_title.setdefault(norm, []).append(item)

        seen_groups: set[frozenset[str]] = set()
        groups: list[list[dict]] = []
        for cluster in list(by_doi.values()) + list(by_title.values()):
            if len(cluster) < 2:
                continue
            group_keys = frozenset(it.key for it in cluster)
            if group_keys in seen_groups:
                continue
            seen_groups.add(group_keys)
            groups.append([
                {
                    "key": it.key,
                    "title": it.title,
                    "authors": it.authors,
                    "year": self.parse_year(it.date),
                    "doi": it.doi,
                }
                for it in cluster
            ])
        return groups

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Lowercase, strip accents, remove non-alphanumeric for fuzzy matching."""
        t = unicodedata.normalize("NFKD", title.lower())
        t = "".join(c for c in t if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", t)

    # ── Write: annotations ─────────────────────────────────

    @_with_lock
    def create_annotation(
        self,
        attachment_key: str,
        page: int,
        text: str,
        comment: str = "",
        color: str = "#ffd400",
        tags: list[str] | None = None,
    ) -> dict:
        """Create a highlight annotation on a PDF attachment.

        Args:
            attachment_key: Key of the PDF attachment (not the parent item).
            page: 0-based page index where the annotation is placed.
            text: The highlighted text.
            comment: Optional comment on the highlight.
            color: Hex color for the highlight (default yellow).
            tags: Optional tags for the annotation.

        Returns the created annotation data from Zotero API.
        """
        w = self._writer
        annotation_data = {
            "itemType": "annotation",
            "parentItem": attachment_key,
            "annotationType": "highlight",
            "annotationText": text,
            "annotationComment": comment,
            "annotationColor": color,
            "annotationPageLabel": str(page + 1),
            "annotationSortIndex": f"{page:05d}|000000|00000",
            "annotationPosition": "{}",
            "tags": [{"tag": t} for t in (tags or [])],
        }
        return w.create_items([annotation_data])

    def get_pdf_attachment_key(self, item_key: str) -> str | None:
        """Find the first PDF attachment key for a parent item."""
        for ch in self._zot.children(item_key):
            ch_data = ch.get("data", ch)
            if ch_data.get("contentType") == "application/pdf":
                return ch_data.get("key", "")
        return None

    # ── Write: merge duplicates ────────────────────────────

    @_with_lock
    def merge_items(
        self,
        keeper_key: str,
        duplicate_keys: list[str],
    ) -> dict:
        """Merge duplicates into keeper: combine tags, collections, re-parent children, trash dups.

        Returns a summary dict with merged tags, collections, children counts.
        """
        w = self._writer
        keeper_raw = w.item(keeper_key)
        keeper_data = keeper_raw.get("data", {})

        keeper_tags = {t.get("tag", "") for t in keeper_data.get("tags", [])}
        keeper_cols = set(keeper_data.get("collections", []))
        keeper_children = w.children(keeper_key)
        keeper_att_sigs = set()
        for ch in keeper_children:
            cd = ch.get("data", ch)
            sig = (
                cd.get("contentType", ""),
                cd.get("filename", ""),
                cd.get("md5", ""),
            )
            keeper_att_sigs.add(sig)

        merged_tags: list[str] = []
        merged_collections: list[str] = []
        reparented = 0
        skipped_attachments = 0

        for dup_key in duplicate_keys:
            dup_raw = w.item(dup_key)
            dup_data = dup_raw.get("data", {})

            for t in dup_data.get("tags", []):
                tag = t.get("tag", "")
                if tag and tag not in keeper_tags:
                    keeper_tags.add(tag)
                    merged_tags.append(tag)

            for col in dup_data.get("collections", []):
                if col not in keeper_cols:
                    keeper_cols.add(col)
                    merged_collections.append(col)

            for ch in w.children(dup_key):
                cd = ch.get("data", ch)
                sig = (
                    cd.get("contentType", ""),
                    cd.get("filename", ""),
                    cd.get("md5", ""),
                )
                if sig in keeper_att_sigs and cd.get("itemType") == "attachment":
                    skipped_attachments += 1
                    continue
                cd["parentItem"] = keeper_key
                try:
                    w.update_item(ch)
                    reparented += 1
                    keeper_att_sigs.add(sig)
                except Exception:
                    pass

        keeper_raw = w.item(keeper_key)
        keeper_data = keeper_raw.get("data", {})
        keeper_data["tags"] = [{"tag": t} for t in sorted(keeper_tags) if t]
        keeper_data["collections"] = sorted(keeper_cols)
        w.update_item(keeper_raw)

        for dup_key in duplicate_keys:
            try:
                dup_raw = w.item(dup_key)
                dup_data = dup_raw.get("data", {})
                dup_data["deleted"] = 1
                w.update_item(dup_raw)
            except Exception:
                pass

        return {
            "keeper_key": keeper_key,
            "merged_tags": merged_tags,
            "merged_collections": merged_collections,
            "reparented_children": reparented,
            "skipped_duplicate_attachments": skipped_attachments,
            "trashed_duplicates": duplicate_keys,
        }

    # ── Internal ─────────────────────────────────────────────

    @staticmethod
    def parse_year(date_str: str) -> int:
        if not date_str:
            return 0
        m = re.search(r"\b(\d{4})\b", date_str)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _to_item(raw: dict) -> Item:
        data = raw.get("data", raw)
        creators = data.get("creators", [])
        authors = []
        for c in creators:
            name = c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            if name:
                authors.append(name)
        tags = [t["tag"] for t in data.get("tags", [])]
        return Item(
            key=data.get("key", ""),
            title=data.get("title", ""),
            abstract=data.get("abstractNote", ""),
            authors=authors,
            date=data.get("date", ""),
            doi=data.get("DOI", ""),
            url=data.get("url", ""),
            item_type=data.get("itemType", ""),
            tags=tags,
            collections=data.get("collections", []),
            citation_key=data.get("citationKey", ""),
            version=raw.get("version", 0) or data.get("version", 0),
        )
