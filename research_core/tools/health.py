"""Health check / diagnostic tool for the MCP server.

Checks Zotero connectivity, vector index status, configuration, and
online API availability. Returns actionable guidance for each issue found.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

_DISCLAIMER = (
    "以上诊断结果仅供参考。如未能解决问题，请查看实际报错信息或查阅文档。"
    " / The diagnostics above are for reference only. If the issue persists, "
    "check the actual error message or consult the documentation."
)


@dataclass
class HealthReport:
    status: str  # "healthy" | "degraded" | "unhealthy"
    checks: list[dict] = field(default_factory=list)
    summary: str = ""
    disclaimer: str = _DISCLAIMER


def check_health(
    *,
    zot=None,
    retriever=None,
    verbose: bool = False,
) -> dict:
    """Run diagnostic checks and return a structured health report.

    Checks performed:
    1. Zotero local API connectivity
    2. Zotero write capability
    3. Vector index status (chunk count, paper count)
    4. Embedding model availability
    5. Online API accessibility (OpenAlex)
    6. Environment configuration completeness
    7. Chunk coverage (detect papers with PDFs but no indexed chunks)

    Args:
        zot: ZoteroClient instance (if available).
        retriever: Retriever instance (if available).
        verbose: Include extra details in output.

    Returns:
        Dict with status, individual check results, and guidance.
    """
    checks: list[dict] = []

    # 1. Zotero local API
    checks.append(_check_zotero_connection(zot))

    # 2. Write capability
    checks.append(_check_write_capability(zot))

    # 3. Vector index
    checks.append(_check_vector_index(retriever))

    # 4. Embedding model
    checks.append(_check_embedding_model())

    # 5. Online API
    checks.append(_check_online_api())

    # 6. Configuration
    checks.append(_check_configuration())

    # 7. Chunk coverage (detect missing papers)
    checks.append(_check_chunk_coverage(zot, retriever))

    # Determine overall status
    statuses = [c["status"] for c in checks]
    if all(s == "ok" for s in statuses):
        overall = "healthy"
        summary = "All systems operational. You can use all features normally."
    elif any(s == "error" for s in statuses):
        overall = "unhealthy"
        failed = [c["name"] for c in checks if c["status"] == "error"]
        summary = f"Critical issues found: {', '.join(failed)}. See details below."
    else:
        overall = "degraded"
        warned = [c["name"] for c in checks if c["status"] == "warning"]
        summary = f"Partially functional. Warnings: {', '.join(warned)}."

    report = HealthReport(status=overall, checks=checks, summary=summary)
    return {
        "status": report.status,
        "summary": report.summary,
        "checks": report.checks,
        "disclaimer": report.disclaimer,
    }


def _check_zotero_connection(zot) -> dict:
    """Check if Zotero local API is reachable."""
    name = "zotero_connection"
    if zot is None:
        return {
            "name": name,
            "status": "error",
            "message": "Zotero client not initialized.",
            "fix": "Check .env configuration (ZOTERO_LOCAL=true).",
        }
    try:
        resp = httpx.get("http://127.0.0.1:23119/api/", timeout=3)
        if resp.status_code == 200:
            try:
                zot.search_items("", limit=1)
                return {
                    "name": name,
                    "status": "ok",
                    "message": "Zotero local API is reachable and responding.",
                }
            except Exception as e:
                return {
                    "name": name,
                    "status": "warning",
                    "message": f"API reachable but library query failed: {e}",
                    "fix": "Verify Zotero has at least one item in the library.",
                }
        else:
            return {
                "name": name,
                "status": "error",
                "message": f"Zotero API returned status {resp.status_code}.",
                "fix": "Ensure Zotero 7 desktop is running and local API is enabled "
                       "(Edit → Settings → Advanced → Allow other applications...).",
            }
    except (httpx.ConnectError, httpx.TimeoutException):
        return {
            "name": name,
            "status": "error",
            "message": "Cannot connect to Zotero local API (http://127.0.0.1:23119).",
            "fix": (
                "1. 确认 Zotero 桌面版已打开 / Ensure Zotero desktop is running.\n"
                "2. 确认已开启本地 API / Enable local API: "
                "Edit → Settings → Advanced → 'Allow other applications on this "
                "computer to communicate with Zotero'.\n"
                "3. 如果刚安装，重启 Zotero / Restart Zotero if you just enabled the setting."
            ),
        }
    except Exception as e:
        return {
            "name": name,
            "status": "error",
            "message": f"Unexpected error checking Zotero: {e}",
            "fix": "Check if another application is blocking port 23119.",
        }


def _check_write_capability(zot) -> dict:
    """Check if write operations are available."""
    name = "write_capability"
    if zot is None:
        return {"name": name, "status": "warning", "message": "Cannot check (no client)."}

    if zot.can_write:
        return {
            "name": name,
            "status": "ok",
            "message": "Write operations available (API key configured).",
        }
    else:
        return {
            "name": name,
            "status": "warning",
            "message": "Write operations disabled (read-only mode).",
            "fix": (
                "To enable adding papers, notes, and tags: set ZOTERO_API_KEY and "
                "ZOTERO_LIBRARY_ID in .env. Get your key at "
                "https://www.zotero.org/settings/keys\n"
                "Read-only mode still supports search, reading, and citations."
            ),
        }


def _check_vector_index(retriever) -> dict:
    """Check vector index health."""
    name = "vector_index"
    if retriever is None:
        return {
            "name": name,
            "status": "error",
            "message": "Vector index not initialized.",
            "fix": "Check CHROMA_PERSIST_DIR in .env and ensure the directory is writable.",
        }
    try:
        count = retriever._collection.count()
        if count == 0:
            return {
                "name": name,
                "status": "warning",
                "message": "Vector index is empty (0 chunks indexed).",
                "fix": (
                    "你的索引为空，语义搜索功能暂不可用。\n"
                    "Your index is empty — semantic search "
                    "won't work yet.\n"
                    "解决方法 / Fix:\n"
                    "1. 确保 Zotero 中有带 PDF 附件的论文 / "
                    "Ensure papers have PDF attachments.\n"
                    '2. 对我说 "sync my index" / '
                    'Tell me to "sync my index".\n'
                    "3. 等待索引构建完成（首次可能需要几分钟）/ "
                    "Wait for indexing (may take minutes)."
                ),
            }
        else:
            return {
                "name": name,
                "status": "ok",
                "message": f"Index healthy: {count} chunks indexed.",
            }
    except Exception as e:
        return {
            "name": name,
            "status": "error",
            "message": f"Failed to query vector index: {e}",
            "fix": (
                "The .chroma_db directory may be corrupted. "
                "Try: sync_index(force_rebuild=True)."
            ),
        }


def _check_embedding_model() -> dict:
    """Check if the embedding model is loaded/available."""
    name = "embedding_model"
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    try:
        from research_core.rag.embedding import get_embedding_function
        ef = get_embedding_function()
        # Try a minimal embedding to verify it works
        result = ef([" test"])
        if result and len(result) > 0:
            return {
                "name": name,
                "status": "ok",
                "message": f"Embedding model loaded: {model_name}.",
            }
        else:
            return {
                "name": name,
                "status": "error",
                "message": "Embedding model returned empty result.",
                "fix": (
                    "Re-download the model: delete "
                    f"~/.cache/huggingface/hub/"
                    f"models--{model_name.replace('/', '--')}/"
                    " and restart."
                ),
            }
    except Exception as e:
        err_str = str(e)
        if "No such file or directory" in err_str or "not found" in err_str.lower():
            return {
                "name": name,
                "status": "error",
                "message": f"Embedding model not found: {model_name}.",
                "fix": (
                    "模型未下载完成 / Model not downloaded.\n"
                    "Fix: restart the MCP server (it downloads on first run, ~2.3GB).\n"
                    "国内用户 / China users: set HF_ENDPOINT=https://hf-mirror.com in .env."
                ),
            }
        return {
            "name": name,
            "status": "warning",
            "message": f"Could not verify embedding model: {e}",
            "fix": (
                "This may resolve after first use. "
                "If persistent, check disk space and model cache."
            ),
        }


def _check_online_api() -> dict:
    """Check if online academic APIs are reachable."""
    name = "online_api"
    try:
        resp = httpx.get(
            "https://api.openalex.org/works?filter=doi:10.1038/nature12373&per_page=1",
            timeout=8,
        )
        if resp.status_code == 200:
            return {
                "name": name,
                "status": "ok",
                "message": "Online APIs reachable (OpenAlex responded).",
            }
        else:
            return {
                "name": name,
                "status": "warning",
                "message": f"OpenAlex returned status {resp.status_code}.",
                "fix": "Online literature search may be degraded. Local search still works.",
            }
    except (httpx.ConnectError, httpx.TimeoutException):
        return {
            "name": name,
            "status": "warning",
            "message": "Cannot reach OpenAlex API (network issue or firewall).",
            "fix": (
                "在线文献检索功能不可用 / Online search unavailable.\n"
                "本地搜索仍然正常 / Local library search still works.\n"
                "如需在线检索 / For online search: check internet connection or proxy settings."
            ),
        }
    except Exception as e:
        return {
            "name": name,
            "status": "warning",
            "message": f"Error checking online API: {e}",
            "fix": "Online search may not work. Local features are unaffected.",
        }


def _check_configuration() -> dict:
    """Check key environment variables."""
    name = "configuration"
    issues: list[str] = []

    if not os.getenv("ZOTERO_LOCAL", "true").lower() == "true" and not os.getenv("ZOTERO_API_KEY"):
        issues.append("ZOTERO_LOCAL=false but no ZOTERO_API_KEY set")

    if os.getenv("ZOTERO_LOCAL", "true").lower() == "true":
        if os.getenv("ZOTERO_API_KEY") and not os.getenv("ZOTERO_LIBRARY_ID"):
            issues.append("ZOTERO_API_KEY set but ZOTERO_LIBRARY_ID missing (writes won't work)")

    chroma_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma_db")
    if not os.path.isabs(chroma_dir) and not os.path.exists(chroma_dir):
        issues.append(
            f"CHROMA_PERSIST_DIR='{chroma_dir}' not found "
            "(will be created on first sync)"
        )

    if issues:
        return {
            "name": name,
            "status": "warning",
            "message": "Configuration notes: " + "; ".join(issues),
            "fix": (
                "Review .env file. Most issues resolve "
                "after completing initial setup."
            ),
        }
    return {
        "name": name,
        "status": "ok",
        "message": "Configuration looks correct.",
    }


def _check_chunk_coverage(zot, retriever) -> dict:
    """Detect papers that should be indexed but are missing."""
    name = "chunk_coverage"
    if zot is None or retriever is None:
        return {
            "name": name,
            "status": "warning",
            "message": "Cannot check coverage (missing client).",
        }
    try:
        indexed_keys = retriever.list_indexed_items()
        if not indexed_keys:
            return {
                "name": name,
                "status": "warning",
                "message": "No papers indexed yet.",
                "fix": 'Run "sync index" to index your library.',
            }

        library_items = zot.search_items("", limit=200)
        library_keys = {item.key for item in library_items}

        missing = library_keys - indexed_keys
        extra = indexed_keys - library_keys

        if not missing and not extra:
            return {
                "name": name,
                "status": "ok",
                "message": (
                    f"Coverage good: {len(indexed_keys)} papers "
                    "indexed from library."
                ),
            }

        issues: list[str] = []
        if missing:
            sample = list(missing)[:5]
            issues.append(
                f"{len(missing)} papers in library but not "
                f"indexed (e.g. {', '.join(sample)})"
            )
        if extra:
            issues.append(
                f"{len(extra)} indexed papers no longer in "
                "library (stale entries)"
            )

        return {
            "name": name,
            "status": "warning",
            "message": "; ".join(issues),
            "fix": (
                "运行 sync_index 来同步索引。"
                " / Run sync_index to update. "
                "Missing papers may lack PDF attachments "
                "or have encrypted/scanned PDFs."
            ),
            "missing_count": len(missing),
            "stale_count": len(extra),
        }
    except Exception as e:
        return {
            "name": name,
            "status": "warning",
            "message": f"Coverage check failed: {e}",
        }
