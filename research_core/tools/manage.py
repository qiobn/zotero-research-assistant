"""Write tools — add notes, edit tags, manage collections, add papers.

All destructive operations default to dry-run for safety.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from research_core.sources import http_client as _http
from research_core.utils import WRITE_PREVIEW_HINT, escape_html
from research_core.zotero.client import ZoteroClient

_WRITE_DISABLED_MSG = (
    "Write operations are not available. To enable writes, add ZOTERO_API_KEY "
    "and ZOTERO_LIBRARY_ID to your .env file (get them at "
    "https://www.zotero.org/settings/keys). Reads will stay local and fast."
)


@dataclass
class WriteResult:
    confirmed: bool
    preview: dict
    result: dict | None = None
    error: str = ""


# ── add_note ─────────────────────────────────────────────────


def add_note(
    item_key: str,
    title: str,
    content: str,
    zot: ZoteroClient,
    tags: list[str] | None = None,
    confirm: bool = False,
) -> WriteResult:
    """Attach a note to a paper. Returns a preview unless confirm=True."""
    note_html = f"<h1>{escape_html(title)}</h1>\n{content}" if title else content
    preview = {
        "action": "create_note",
        "parent_item_key": item_key,
        "title": title,
        "content_preview": content[:300] + ("..." if len(content) > 300 else ""),
        "tags": tags or [],
        "note_html_length": len(note_html),
    }
    if not confirm:
        preview["next_step"] = WRITE_PREVIEW_HINT
        if not zot.can_write:
            preview["warning"] = _WRITE_DISABLED_MSG
        return WriteResult(confirmed=False, preview=preview)

    if not zot.can_write:
        return WriteResult(confirmed=False, preview=preview, error=_WRITE_DISABLED_MSG)

    try:
        resp = zot.create_note(item_key, note_html, tags=tags)
        return WriteResult(confirmed=True, preview=preview, result={"zotero_response": resp})
    except Exception as e:
        return WriteResult(confirmed=False, preview=preview, error=str(e))


# ── edit_tags ────────────────────────────────────────────────


def edit_tags(
    item_keys: list[str],
    zot: ZoteroClient,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    confirm: bool = False,
) -> WriteResult:
    """Add and/or remove tags on one or more items. Returns a diff preview unless confirm=True."""
    add = add or []
    remove = remove or []
    if not add and not remove:
        return WriteResult(
            confirmed=False,
            preview={"error": "No tags to add or remove."},
            error="empty operation",
        )

    diffs: list[dict] = []
    for key in item_keys:
        try:
            item = zot.get_item(key)
            current = set(item.tags)
            new_tags = (current | set(add)) - set(remove)
            diffs.append({
                "item_key": key,
                "title": item.title[:80],
                "current": sorted(current),
                "to_add": sorted(set(add) - current),
                "to_remove": sorted(set(remove) & current),
                "after": sorted(new_tags),
            })
        except Exception as e:
            diffs.append({"item_key": key, "error": str(e)})

    preview = {"action": "edit_tags", "add": add, "remove": remove, "items": diffs}
    if not confirm:
        preview["next_step"] = WRITE_PREVIEW_HINT
        if not zot.can_write:
            preview["warning"] = _WRITE_DISABLED_MSG
        return WriteResult(confirmed=False, preview=preview)

    if not zot.can_write:
        return WriteResult(confirmed=False, preview=preview, error=_WRITE_DISABLED_MSG)

    applied: list[dict] = []
    for key in item_keys:
        try:
            applied.append(zot.update_item_tags(key, add=add, remove=remove))
        except Exception as e:
            applied.append({"item_key": key, "error": str(e)})
    return WriteResult(confirmed=True, preview=preview, result={"applied": applied})


# ── manage_collections ───────────────────────────────────────


def manage_collections(
    action: str,
    zot: ZoteroClient,
    name: str = "",
    parent_key: str = "",
    collection_key: str = "",
    item_keys: list[str] | None = None,
    confirm: bool = False,
) -> WriteResult:
    """Create collections or add/remove items from them. Defaults to dry-run."""
    item_keys = item_keys or []

    if action == "create":
        if not name:
            return WriteResult(confirmed=False, preview={}, error="Collection name is required.")
        preview = {"action": "create_collection", "name": name, "parent_key": parent_key}
        if not confirm:
            preview["next_step"] = WRITE_PREVIEW_HINT
            if not zot.can_write:
                preview["warning"] = _WRITE_DISABLED_MSG
            return WriteResult(confirmed=False, preview=preview)
        if not zot.can_write:
            return WriteResult(confirmed=False, preview=preview, error=_WRITE_DISABLED_MSG)
        try:
            resp = zot.create_collection(name, parent_key=parent_key)
            return WriteResult(confirmed=True, preview=preview, result={"zotero_response": resp})
        except Exception as e:
            return WriteResult(confirmed=False, preview=preview, error=str(e))

    if action == "add_items":
        if not collection_key or not item_keys:
            return WriteResult(
                confirmed=False, preview={},
                error="collection_key and item_keys are required for add_items.",
            )
        preview = {
            "action": "add_items_to_collection",
            "collection_key": collection_key,
            "item_keys": item_keys,
        }
        if not confirm:
            preview["next_step"] = WRITE_PREVIEW_HINT
            if not zot.can_write:
                preview["warning"] = _WRITE_DISABLED_MSG
            return WriteResult(confirmed=False, preview=preview)
        if not zot.can_write:
            return WriteResult(confirmed=False, preview=preview, error=_WRITE_DISABLED_MSG)
        try:
            resp = zot.add_to_collection(collection_key, item_keys)
            return WriteResult(confirmed=True, preview=preview, result={"applied": resp})
        except Exception as e:
            return WriteResult(confirmed=False, preview=preview, error=str(e))

    if action == "remove_items":
        if not collection_key or not item_keys:
            return WriteResult(
                confirmed=False, preview={},
                error="collection_key and item_keys are required for remove_items.",
            )
        preview = {
            "action": "remove_items_from_collection",
            "collection_key": collection_key,
            "item_keys": item_keys,
        }
        if not confirm:
            preview["next_step"] = WRITE_PREVIEW_HINT
            if not zot.can_write:
                preview["warning"] = _WRITE_DISABLED_MSG
            return WriteResult(confirmed=False, preview=preview)
        if not zot.can_write:
            return WriteResult(confirmed=False, preview=preview, error=_WRITE_DISABLED_MSG)
        try:
            resp = zot.remove_from_collection(collection_key, item_keys)
            return WriteResult(confirmed=True, preview=preview, result={"applied": resp})
        except Exception as e:
            return WriteResult(confirmed=False, preview=preview, error=str(e))

    return WriteResult(confirmed=False, preview={}, error=f"Unknown action: {action}")


# ── add_paper ────────────────────────────────────────────────


_DOI_RE = re.compile(r"10\.\d{4,}/[^\s]+")
_ARXIV_RE = re.compile(r"(?:arxiv\.org/abs/|arxiv:)(\d{4}\.\d{4,}(?:v\d+)?)", re.IGNORECASE)
_ISBN_RE = re.compile(r"(?:ISBN[:\s-]*)?(\d{13}|\d{9}[\dXx])", re.IGNORECASE)


@dataclass
class AddPaperResult:
    success: bool
    item_key: str = ""
    title: str = ""
    doi: str = ""
    pdf_attached: bool = False
    error: str = ""
    metadata: dict | None = None


def _parse_identifier(identifier: str) -> tuple[str, str]:
    """Parse identifier into (type, value). Types: 'doi', 'arxiv', 'isbn', 'bibtex', 'url'.

    URL detection must come before ISBN to avoid false positives (e.g. ScienceDirect
    PII numbers contain 13+ consecutive digits that match ISBN-13 patterns).
    """
    identifier = identifier.strip()
    if identifier.startswith("@") or identifier.startswith("{"):
        return "bibtex", identifier
    if identifier.startswith("http"):
        return "url", identifier
    arxiv_match = _ARXIV_RE.search(identifier)
    if arxiv_match:
        return "arxiv", arxiv_match.group(1)
    doi_match = _DOI_RE.search(identifier)
    if doi_match:
        return "doi", doi_match.group(0).rstrip(".")
    isbn_match = _ISBN_RE.search(identifier)
    if isbn_match:
        return "isbn", isbn_match.group(1).replace("-", "")
    return "doi", identifier


def _fetch_metadata_crossref(doi: str) -> dict | None:
    """Fetch metadata from CrossRef by DOI."""
    try:
        r = _http.get(
            f"https://api.crossref.org/works/{doi}",
            timeout=15,
        )
        if r.status_code != 200:
            return None
        msg = r.json().get("message", {})
        authors = []
        for a in msg.get("author", []):
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)
        title_parts = msg.get("title", [])
        title = title_parts[0] if title_parts else ""
        date_parts = msg.get("published-print", msg.get("published-online", {})).get("date-parts", [[]])
        year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""
        return {
            "itemType": msg.get("type", "journalArticle").replace("-", ""),
            "title": title,
            "creators": [{"creatorType": "author", "name": n} for n in authors],
            "date": year,
            "DOI": doi,
            "abstractNote": msg.get("abstract", ""),
            "url": msg.get("URL", ""),
            "publicationTitle": (msg.get("container-title", [""]) or [""])[0],
        }
    except Exception as e:
        logger.debug(f"CrossRef lookup failed for {doi}: {e}")
        return None


def _fetch_metadata_arxiv(arxiv_id: str) -> dict | None:
    """Fetch metadata from arXiv API."""
    try:
        r = _http.get(
            f"https://export.arxiv.org/api/query?id_list={arxiv_id}",
            timeout=15,
        )
        if r.status_code != 200:
            return None
        import xml.etree.ElementTree as ET

        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return None
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
        authors = []
        for author_el in entry.findall("atom:author", ns):
            name = (author_el.findtext("atom:name", "", ns) or "").strip()
            if name:
                authors.append(name)
        published = entry.findtext("atom:published", "", ns) or ""
        year = published[:4] if len(published) >= 4 else ""

        doi_el = entry.find("{http://arxiv.org/schemas/atom}doi")
        doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""

        return {
            "itemType": "preprint",
            "title": title,
            "creators": [{"creatorType": "author", "name": n} for n in authors],
            "date": year,
            "DOI": doi,
            "abstractNote": abstract,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        }
    except Exception as e:
        logger.debug(f"arXiv lookup failed for {arxiv_id}: {e}")
        return None


def _fetch_metadata_isbn(isbn: str) -> dict | None:
    """Fetch metadata from Open Library by ISBN."""
    try:
        r = _http.get(
            f"https://openlibrary.org/isbn/{isbn}.json",
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        title = data.get("title", "")
        authors = []
        for a in data.get("authors", []):
            author_key = a.get("key", "")
            if author_key:
                try:
                    ar = _http.get(
                        f"https://openlibrary.org{author_key}.json",
                        timeout=10,
                    )
                    if ar.status_code == 200:
                        authors.append(ar.json().get("name", ""))
                except Exception:
                    pass
        publish_date = data.get("publish_date", "")
        year_match = re.search(r"\d{4}", publish_date)
        year = year_match.group(0) if year_match else ""
        return {
            "itemType": "book",
            "title": title,
            "creators": [{"creatorType": "author", "name": n} for n in authors if n],
            "date": year,
            "ISBN": isbn,
            "publisher": (data.get("publishers", [""]) or [""])[0] if isinstance(data.get("publishers"), list) else "",
            "numPages": str(data.get("number_of_pages", "")),
        }
    except Exception as e:
        logger.debug(f"ISBN lookup failed for {isbn}: {e}")
        return None


def _parse_bibtex(bibtex_str: str) -> dict | None:
    """Parse a single BibTeX entry into Zotero item metadata."""
    bibtex_str = bibtex_str.strip()
    type_match = re.match(r"@(\w+)\s*\{", bibtex_str)
    if not type_match:
        return None

    bib_type = type_match.group(1).lower()
    type_map = {
        "article": "journalArticle",
        "inproceedings": "conferencePaper",
        "conference": "conferencePaper",
        "book": "book",
        "incollection": "bookSection",
        "phdthesis": "thesis",
        "mastersthesis": "thesis",
        "techreport": "report",
        "misc": "document",
        "unpublished": "manuscript",
    }
    item_type = type_map.get(bib_type, "journalArticle")

    fields: dict[str, str] = {}
    for m in re.finditer(r"(\w+)\s*=\s*[{\"](.+?)[}\"]", bibtex_str, re.DOTALL):
        fields[m.group(1).lower()] = m.group(2).strip()

    authors = []
    if "author" in fields:
        for name in re.split(r"\s+and\s+", fields["author"]):
            name = name.strip().strip("{}")
            if name:
                authors.append(name)

    metadata: dict = {
        "itemType": item_type,
        "title": fields.get("title", "").strip("{}"),
        "creators": [{"creatorType": "author", "name": n} for n in authors],
        "date": fields.get("year", ""),
        "DOI": fields.get("doi", ""),
        "url": fields.get("url", ""),
        "abstractNote": fields.get("abstract", ""),
        "publicationTitle": fields.get("journal", fields.get("booktitle", "")),
        "volume": fields.get("volume", ""),
        "issue": fields.get("number", ""),
        "pages": fields.get("pages", ""),
        "publisher": fields.get("publisher", ""),
    }
    return {k: v for k, v in metadata.items() if v}


_PII_RE = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)
_URL_DOI_PATTERNS = [
    re.compile(r"link\.springer\.com/(?:article|chapter)/(?:10\.\d{4,}/[^\s?#]+)"),
    re.compile(r"onlinelibrary\.wiley\.com/doi/(10\.\d{4,}/[^\s?#]+)"),
    re.compile(r"nature\.com/articles/(10\.\d{4,}/[^\s?#]+)"),
    re.compile(r"mdpi\.com/\d{4}-\d{4}/\d+/\d+/\d+"),
]


def _resolve_doi_from_url(url: str) -> str | None:
    """Try to extract a DOI from a publisher URL via multiple strategies.

    Strategy 1: Extract DOI from known URL patterns (Springer, Wiley, Nature).
    Strategy 2: For ScienceDirect, extract PII and search CrossRef.
    Strategy 3: Fetch page and parse DOI from HTML meta tags.
    """
    doi_in_url = re.search(r"(?:doi\.org/|/doi/|/article/)(10\.\d{4,}/[^\s?#]+)", url)
    if doi_in_url:
        return doi_in_url.group(1).rstrip(".")

    pii_match = _PII_RE.search(url)
    if pii_match:
        pii = pii_match.group(1)
        doi = _search_crossref_by_pii(pii)
        if doi:
            return doi

    try:
        r = _http.get(
            url,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        text = r.text[:50000]
        for pattern in [
            r'<meta[^>]+name=["\'](?:citation_doi|DC\.identifier|doi|DC\.Identifier)["\'][^>]+content=["\'](?:doi:?\s*)?(10\.\d{4,}/[^"\'<>\s]+)["\']',
            r'<meta[^>]+content=["\'](?:doi:?\s*)?(10\.\d{4,}/[^"\'<>\s]+)["\'][^>]+name=["\'](?:citation_doi|DC\.identifier|doi|DC\.Identifier)["\']',
            r'"doi"\s*:\s*"(10\.\d{4,}/[^"]+)"',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(1).rstrip(".")
    except Exception as e:
        logger.debug(f"DOI resolution from URL failed: {e}")
    return None


def _search_crossref_by_pii(pii: str) -> str | None:
    """Search CrossRef for a DOI using a ScienceDirect PII identifier."""
    try:
        r = _http.get(
            "https://api.crossref.org/works",
            params={"filter": f"alternative-id:{pii}", "rows": "1"},
            timeout=15,
        )
        if r.status_code == 200:
            items = r.json().get("message", {}).get("items", [])
            if items:
                return items[0].get("DOI")
    except Exception as e:
        logger.debug(f"CrossRef PII search failed for {pii}: {e}")
    return None


def _save_pdf_content(content: bytes) -> str | None:
    """Write PDF content to a temp file if it looks valid. Returns path or None."""
    if len(content) < 1000:
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


def _download_pdf_from_url(url: str) -> str | None:
    """Download a PDF from URL; return temp path or None."""
    if not url:
        return None
    try:
        r = _http.get(
            url,
            timeout=30,
        )
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "").lower()
        if "pdf" in ct or url.lower().rstrip("/").endswith(".pdf"):
            return _save_pdf_content(r.content)
    except Exception as e:
        logger.debug(f"PDF download failed for {url}: {e}")
    return None


def _unpaywall_email() -> str:
    import os

    return os.getenv("UNPAYWALL_EMAIL", "dev@example.com")


def _try_arxiv_pdf(arxiv_id: str) -> str | None:
    """Stage 1: Direct arXiv PDF download."""
    if not arxiv_id:
        return None
    return _download_pdf_from_url(f"https://arxiv.org/pdf/{arxiv_id}.pdf")


def _collect_unpaywall_pdf_urls(data: dict) -> list[str]:
    """Collect PDF URLs from Unpaywall response (best + all OA locations)."""
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    best = data.get("best_oa_location") or {}
    add(best.get("url_for_pdf"))
    add(best.get("url"))
    for loc in data.get("oa_locations") or []:
        add(loc.get("url_for_pdf"))
        add(loc.get("url"))
    return urls


def _try_unpaywall(doi: str) -> str | None:
    """Stage 2: Unpaywall — try best and all OA locations."""
    if not doi:
        return None
    try:
        r = _http.get(
            f"https://api.unpaywall.org/v2/{doi}?email={_unpaywall_email()}",
            timeout=10,
        )
        if r.status_code != 200:
            return None
        for pdf_url in _collect_unpaywall_pdf_urls(r.json()):
            result = _download_pdf_from_url(pdf_url)
            if result:
                return result
    except Exception as e:
        logger.debug(f"Unpaywall download failed for {doi}: {e}")
    return None


def _try_semantic_scholar(doi: str) -> str | None:
    """Stage 3: Semantic Scholar open-access PDF."""
    if not doi:
        return None
    try:
        r = _http.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "isOpenAccess,openAccessPdf"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("isOpenAccess"):
            return None
        oa = data.get("openAccessPdf") or {}
        return _download_pdf_from_url(oa.get("url"))
    except Exception as e:
        logger.debug(f"Semantic Scholar download failed for {doi}: {e}")
    return None


def _try_pmc(doi: str) -> str | None:
    """Stage 4: PubMed Central free full text."""
    if not doi:
        return None
    try:
        r = _http.get(
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
            params={"ids": doi, "format": "json", "tool": "zra", "email": "dev@example.com"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        records = r.json().get("records", [])
        pmcid = None
        for rec in records:
            pmcid = rec.get("pmcid")
            if pmcid:
                break
        if not pmcid:
            return None
        pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
        return _download_pdf_from_url(pdf_url)
    except Exception as e:
        logger.debug(f"PMC download failed for {doi}: {e}")
    return None


def _try_openalex_pdf(doi: str) -> str | None:
    """Stage 5: OpenAlex primary_location / open_access PDF URL."""
    import os

    if not doi:
        return None
    mailto = os.getenv("OPENALEX_MAILTO", os.getenv("UNPAYWALL_EMAIL", "dev@example.com"))
    try:
        r = _http.get(
            f"https://api.openalex.org/works/doi:{doi}",
            params={"mailto": mailto},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        work = r.json()
        primary = work.get("primary_location") or {}
        for url in (
            primary.get("pdf_url"),
            (work.get("open_access") or {}).get("oa_url"),
            (work.get("best_oa_location") or {}).get("pdf_url"),
        ):
            result = _download_pdf_from_url(url)
            if result:
                return result
    except Exception as e:
        logger.debug(f"OpenAlex PDF lookup failed for {doi}: {e}")
    return None


def _try_core(doi: str) -> str | None:
    """Stage 6: CORE repository full text (requires CORE_API_KEY)."""
    import os

    api_key = os.getenv("CORE_API_KEY", "").strip()
    if not api_key or not doi:
        return None
    try:
        r = _http.get(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": f'doi:"{doi}"', "limit": 1},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results") or []
        if not results:
            return None
        work = results[0]
        for url in (work.get("downloadUrl"), work.get("fullTextLink")):
            result = _download_pdf_from_url(url)
            if result:
                return result
    except Exception as e:
        logger.debug(f"CORE download failed for {doi}: {e}")
    return None


def _try_download_pdf(doi: str, arxiv_id: str = "") -> str | None:
    """Try to download an open-access PDF via multi-stage waterfall.

    Order: arXiv → Unpaywall (all OA locations) → Semantic Scholar → PMC
           → OpenAlex → CORE.
    Returns local temp file path or None.
    """
    for fn in [
        lambda: _try_arxiv_pdf(arxiv_id),
        lambda: _try_unpaywall(doi),
        lambda: _try_semantic_scholar(doi),
        lambda: _try_pmc(doi),
        lambda: _try_openalex_pdf(doi),
        lambda: _try_core(doi),
    ]:
        result = fn()
        if result:
            return result
    return None


def add_paper(
    identifier: str,
    zot: ZoteroClient,
    collection_key: str = "",
    tags: list[str] | None = None,
    confirm: bool = False,
) -> AddPaperResult:
    """Add a paper to Zotero by DOI, arXiv ID, ISBN, BibTeX string, or URL.

    Fetches metadata from CrossRef/arXiv/OpenLibrary, optionally downloads
    open-access PDF, and creates the item in Zotero via the web API.
    """
    id_type, id_value = _parse_identifier(identifier)

    metadata: dict | None = None
    arxiv_id = ""
    doi = ""

    if id_type == "bibtex":
        metadata = _parse_bibtex(id_value)
        if metadata:
            doi = metadata.get("DOI", "")
    elif id_type == "isbn":
        metadata = _fetch_metadata_isbn(id_value)
    elif id_type == "arxiv":
        arxiv_id = id_value
        metadata = _fetch_metadata_arxiv(arxiv_id)
        if metadata and metadata.get("DOI"):
            doi = metadata["DOI"]
    elif id_type == "doi":
        doi = id_value
        metadata = _fetch_metadata_crossref(doi)
    else:
        if "arxiv.org" in id_value:
            m = _ARXIV_RE.search(id_value)
            if m:
                arxiv_id = m.group(1)
                metadata = _fetch_metadata_arxiv(arxiv_id)
        elif "doi.org" in id_value:
            doi_match = _DOI_RE.search(id_value)
            if doi_match:
                doi = doi_match.group(0).rstrip(".")
                metadata = _fetch_metadata_crossref(doi)
        else:
            resolved_doi = _resolve_doi_from_url(id_value)
            if resolved_doi:
                doi = resolved_doi
                metadata = _fetch_metadata_crossref(doi)

    if not metadata:
        return AddPaperResult(
            success=False,
            error=f"Could not fetch metadata for '{identifier}'. "
            "Check the DOI/URL is correct and try again.",
        )

    title = metadata.get("title", "")
    if tags:
        metadata["tags"] = [{"tag": t} for t in tags]
    if collection_key:
        metadata["collections"] = [collection_key]

    if not confirm:
        return AddPaperResult(
            success=False,
            title=title,
            doi=doi or metadata.get("DOI", ""),
            metadata=metadata,
            error=f"Preview only. {WRITE_PREVIEW_HINT}",
        )

    if not zot.can_write:
        return AddPaperResult(
            success=False,
            title=title,
            doi=doi or metadata.get("DOI", ""),
            metadata=metadata,
            error=_WRITE_DISABLED_MSG,
        )

    item_type = metadata.get("itemType", "journalArticle")
    if item_type == "preprint":
        item_type = "journalArticle"
    metadata["itemType"] = item_type

    try:
        resp = zot.create_item(metadata)
        success_items = resp.get("successful") or resp.get("success", {})
        if isinstance(success_items, dict):
            first_key = next(iter(success_items.values()), {})
            if isinstance(first_key, dict):
                item_key = first_key.get("data", {}).get("key", "")
            else:
                item_key = str(first_key)
        else:
            item_key = ""
    except Exception as e:
        return AddPaperResult(success=False, title=title, doi=doi, error=f"Create failed: {e}")

    if not item_key:
        return AddPaperResult(
            success=True,
            title=title,
            doi=doi,
            error="Item created but could not extract key from response.",
        )

    pdf_attached = False
    pdf_path = _try_download_pdf(doi, arxiv_id)
    if pdf_path:
        try:
            zot.attach_file(item_key, pdf_path)
            pdf_attached = True
        except Exception as e:
            logger.warning(f"PDF attach failed for {item_key}: {e}")
        finally:
            Path(pdf_path).unlink(missing_ok=True)

    return AddPaperResult(
        success=True,
        item_key=item_key,
        title=title,
        doi=doi,
        pdf_attached=pdf_attached,
        metadata=metadata,
    )
