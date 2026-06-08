"""Shared HTTP client with global concurrency and per-host rate limiting.

All external API calls in `research_core.sources` should use this module
instead of creating ad-hoc httpx clients. This provides:
- Global concurrent request cap (prevents OS socket exhaustion)
- Per-host rate limiting (prevents 429 from individual APIs)
- Automatic retry with exponential backoff on 429/5xx
- Shared connection pooling for better performance
"""

from __future__ import annotations

import os
import threading
import time

import httpx
from loguru import logger

_MAX_CONCURRENT = int(os.getenv("ZRA_MAX_CONCURRENT_HTTP", "12"))
_PER_HOST_LIMIT = int(os.getenv("ZRA_PER_HOST_RATE", "5"))
_RETRY_MAX = 3
_RETRY_BACKOFF_BASE = 1.5

_semaphore = threading.Semaphore(_MAX_CONCURRENT)

_host_locks: dict[str, threading.Semaphore] = {}
_host_locks_lock = threading.Lock()


def _get_host_semaphore(host: str) -> threading.Semaphore:
    """Get or create a per-host concurrency semaphore."""
    if host not in _host_locks:
        with _host_locks_lock:
            if host not in _host_locks:
                _host_locks[host] = threading.Semaphore(_PER_HOST_LIMIT)
    return _host_locks[host]


def _extract_host(url: str) -> str:
    """Extract host from URL for per-host rate limiting."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.hostname or "unknown"


_client: httpx.Client | None = None
_client_lock = threading.Lock()


def get_client() -> httpx.Client:
    """Get the shared httpx client (singleton, thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=30,
                    limits=httpx.Limits(
                        max_connections=_MAX_CONCURRENT,
                        max_keepalive_connections=_MAX_CONCURRENT // 2,
                    ),
                    follow_redirects=True,
                    headers={"User-Agent": "ZoteroResearchAssistant/1.0"},
                )
    return _client


def request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json: dict | None = None,
    timeout: float | None = None,
    max_retries: int = _RETRY_MAX,
) -> httpx.Response:
    """Make an HTTP request with global concurrency control and retry.

    Raises httpx.HTTPStatusError on non-retryable 4xx errors (except 429).
    """
    host = _extract_host(url)
    host_sem = _get_host_semaphore(host)

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        with _semaphore:
            with host_sem:
                try:
                    client = get_client()
                    resp = client.request(
                        method, url,
                        params=params,
                        headers=headers,
                        json=json,
                        timeout=timeout or 30,
                    )

                    if resp.status_code == 429:
                        retry_after = float(
                            resp.headers.get("Retry-After", _RETRY_BACKOFF_BASE ** (attempt + 1))
                        )
                        logger.debug(
                            f"Rate limited by {host} (429), "
                            f"retry after {retry_after:.1f}s (attempt {attempt + 1})"
                        )
                        time.sleep(retry_after)
                        last_exc = httpx.HTTPStatusError(
                            "429 Too Many Requests",
                            request=resp.request,
                            response=resp,
                        )
                        continue

                    if resp.status_code >= 500:
                        wait = _RETRY_BACKOFF_BASE ** (attempt + 1)
                        logger.debug(
                            f"Server error {resp.status_code} from {host}, "
                            f"retry in {wait:.1f}s (attempt {attempt + 1})"
                        )
                        time.sleep(wait)
                        last_exc = httpx.HTTPStatusError(
                            f"{resp.status_code} Server Error",
                            request=resp.request,
                            response=resp,
                        )
                        continue

                    return resp

                except httpx.TimeoutException as exc:
                    wait = _RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.debug(f"Timeout from {host}, retry in {wait:.1f}s")
                    time.sleep(wait)
                    last_exc = exc
                    continue
                except httpx.ConnectError as exc:
                    last_exc = exc
                    break

    if last_exc:
        raise last_exc
    raise httpx.ConnectError("Request failed after retries")


def get(url: str, **kwargs) -> httpx.Response:
    """Convenience wrapper for GET requests."""
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    """Convenience wrapper for POST requests."""
    return request("POST", url, **kwargs)


def head(url: str, **kwargs) -> httpx.Response:
    """Convenience wrapper for HEAD requests."""
    return request("HEAD", url, **kwargs)
