"""ChromaDB embedded server lifecycle management.

On Windows, ChromaDB's PersistentClient has a known cross-process HNSW bug
(github.com/chroma-core/chroma/issues/3058). The ChromaDB team recommends
client-server mode: a single long-running server owns the database files, and
all clients connect via HTTP.

This module starts a `chroma run` subprocess on MCP server startup and stops
it on shutdown. If a server is already listening on the configured port, it
is reused without starting a second process.

Usage:
    # In MCP server lifespan:
    from research_core.rag.chroma_server import start_server, stop_server
    start_server(persist_dir)
    ...
    stop_server()
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

import httpx
from loguru import logger

_DEFAULT_PORT = 18000
_DEFAULT_HOST = "127.0.0.1"

_server_process: subprocess.Popen | None = None


def _port_in_use(port: int, host: str = _DEFAULT_HOST) -> bool:
    """Check if a TCP port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _find_chroma_binary() -> str:
    """Find the `chroma` CLI binary."""
    python_dir = os.path.dirname(sys.executable)
    candidates = [
        os.path.join(python_dir, "chroma.exe"),
        os.path.join(python_dir, "chroma"),
        os.path.join(python_dir, "Scripts", "chroma.exe"),
        os.path.join(python_dir, "Scripts", "chroma"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "chroma"


def is_server_running(port: int | None = None) -> bool:
    """Check if a ChromaDB server is listening and responsive."""
    p = port or _DEFAULT_PORT
    try:
        resp = httpx.get(f"http://{_DEFAULT_HOST}:{p}/api/v2/heartbeat", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def start_server(
    persist_dir: str,
    port: int | None = None,
    host: str | None = None,
) -> bool:
    """Start a ChromaDB server subprocess.

    Idempotent — reuses existing server if one is already listening.

    Returns True if the server is running after this call.
    """
    global _server_process

    _port = port or int(os.getenv("ZRA_CHROMA_PORT", str(_DEFAULT_PORT)))
    _host = host or os.getenv("ZRA_CHROMA_HOST", _DEFAULT_HOST)

    # Already running from a previous call in this process?
    if _server_process is not None and _server_process.poll() is None:
        return True

    # Already running externally?
    if is_server_running(_port):
        logger.info(f"Reusing existing ChromaDB server at {_host}:{_port}")
        return True

    # Kill any leftover process on our port
    if _port_in_use(_port, _host):
        logger.warning(f"Port {_host}:{_port} in use by non-ChromaDB process, trying...")

    binary = _find_chroma_binary()
    cmd = [
        binary, "run",
        "--path", persist_dir,
        "--host", _host,
        "--port", str(_port),
    ]

    logger.info(f"Starting ChromaDB server: {' '.join(cmd)}")
    try:
        # Don't use subprocess.DEVNULL — the server needs a working console
        # on Windows. Redirect stderr to stdout and capture via a pipe so we
        # can detect early startup errors.
        _server_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0
            ),
        )
    except FileNotFoundError:
        logger.error(
            f"ChromaDB CLI not found at '{binary}'. "
            "Reinstall chromadb: pip install chromadb"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to start ChromaDB server: {e}")
        return False

    # Wait for server to be ready
    deadline = time.time() + 30
    while time.time() < deadline:
        if _server_process.poll() is not None:
            logger.error(
                f"ChromaDB server exited immediately "
                f"(code {_server_process.returncode})"
            )
            _server_process = None
            return False
        if is_server_running(_port):
            logger.info(f"ChromaDB server ready at {_host}:{_port}")
            return True
        time.sleep(0.5)

    logger.error("ChromaDB server did not become ready within 30s")
    return False


def stop_server() -> None:
    """Stop the ChromaDB server subprocess (if we started it)."""
    global _server_process
    if _server_process is None:
        return
    if _server_process.poll() is not None:
        _server_process = None
        return

    logger.info("Stopping ChromaDB server...")
    try:
        if sys.platform == "win32":
            _server_process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            _server_process.terminate()
        _server_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("ChromaDB server did not stop gracefully, killing")
        _server_process.kill()
        _server_process.wait(timeout=5)
    except Exception as e:
        logger.debug(f"Error stopping ChromaDB server: {e}")
        try:
            _server_process.kill()
        except Exception:
            pass
    _server_process = None
    logger.info("ChromaDB server stopped")


def get_http_client(port: int | None = None):
    """Return a chromadb.HttpClient connected to the server."""
    import chromadb

    p = port or int(os.getenv("ZRA_CHROMA_PORT", str(_DEFAULT_PORT)))
    return chromadb.HttpClient(host=_DEFAULT_HOST, port=str(p))
