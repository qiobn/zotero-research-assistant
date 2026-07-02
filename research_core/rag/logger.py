"""Structured retrieval logging with trace-level observability.

Every search_papers / semantic search call emits a JSONL log entry capturing
the full retrieval chain: query → strategy → candidates → rerank → results.
This enables post-hoc debugging ("why did this paper rank 5th not 1st?")
and A/B comparison between search strategies.

Logs are appended to .chroma_db/_retrieval_log.jsonl (plain text, one JSON
object per line). A separate index file (_retrieval_log.idx) stores byte
offsets for fast random access by trace_id.

Usage:
    from research_core.rag.logger import RetrievalLogger, RetrievalLog
    logger = RetrievalLogger(".chroma_db")
    with logger.trace(query="urban green space", strategy="hybrid") as log:
        # ... run search ...
        log.set_results([...])
    # Automatically flushed on context exit
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class RetrievalLog:
    """A single retrieval trace entry."""
    trace_id: str = ""
    timestamp: str = ""
    query: str = ""
    strategy: str = "hybrid"  # hybrid / semantic / keyword / fallback
    parameters: dict = field(default_factory=dict)

    # Candidate counts per source
    candidate_keyword_n: int = 0
    candidate_semantic_n: int = 0
    candidate_merged_n: int = 0

    # Reranker
    reranker_enabled: bool = False
    reranker_model: str = ""
    reranker_pre_n: int = 0
    reranker_post_n: int = 0

    # Results
    results: list[dict] = field(default_factory=list)  # [{item_key, title, score, rank, source}]
    result_count: int = 0

    # Fallback
    fallback_triggered: bool = False
    fallback_count: int = 0

    # Latency (milliseconds)
    latency_keyword_ms: float = 0.0
    latency_semantic_ms: float = 0.0
    latency_rerank_ms: float = 0.0
    latency_total_ms: float = 0.0

    # Status
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Truncate long result lists for storage efficiency
        if len(d.get("results", [])) > 20:
            d["results"] = d["results"][:20]
            d["results_truncated"] = True
        return d


class RetrievalLogger:
    """Append-only JSONL retrieval logger with trace-ID index."""

    def __init__(self, persist_dir: str = ".chroma_db"):
        self._log_path = os.path.join(persist_dir, "_retrieval_log.jsonl")
        self._idx_path = os.path.join(persist_dir, "_retrieval_log.idx")
        self._enabled = os.getenv("ZRA_RETRIEVAL_LOG", "true").lower() == "true"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = value

    def log(self, entry: RetrievalLog) -> str:
        """Write a log entry. Returns the trace_id."""
        if not self._enabled:
            return entry.trace_id

        if not entry.trace_id:
            entry.trace_id = uuid.uuid4().hex[:12]
        if not entry.timestamp:
            entry.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

        # Ensure directory exists
        Path(self._log_path).parent.mkdir(parents=True, exist_ok=True)

        # Get current file offset for index
        try:
            offset = os.path.getsize(self._log_path)
        except OSError:
            offset = 0

        # Write log line
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        # Update index
        with open(self._idx_path, "a", encoding="utf-8") as f:
            f.write(f"{entry.trace_id}\t{offset}\n")

        return entry.trace_id

    @contextmanager
    def trace(
        self,
        query: str = "",
        strategy: str = "hybrid",
        parameters: dict | None = None,
    ) -> Iterator[RetrievalLog]:
        """Context manager for instrumenting a retrieval call.

        Usage:
            logger = RetrievalLogger()
            with logger.trace(query="urban green space", strategy="hybrid") as log:
                log.candidate_semantic_n = 20
                # ... execute search ...
                log.set_results(hits)
            # log is auto-flushed on context exit
        """
        entry = RetrievalLog(
            trace_id=uuid.uuid4().hex[:12],
            query=query,
            strategy=strategy,
            parameters=parameters or {},
        )
        t_start = time.time()
        try:
            yield entry
        except Exception as e:
            entry.success = False
            entry.error = str(e)[:200]
            raise
        finally:
            entry.latency_total_ms = (time.time() - t_start) * 1000
            self.log(entry)

    def get_recent(
        self, n: int = 20, strategy: str = "", success_only: bool = True
    ) -> list[dict]:
        """Read the most recent N log entries."""
        if not os.path.exists(self._log_path):
            return []

        entries: list[dict] = []
        with open(self._log_path, encoding="utf-8") as f:
            # Read last N lines efficiently by seeking from end
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            chunk_size = min(file_size, n * 2000)  # ~2KB per entry
            f.seek(max(0, file_size - chunk_size))
            lines = f.readlines()

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if strategy and entry.get("strategy") != strategy:
                continue
            if success_only and not entry.get("success", True):
                continue
            entries.append(entry)
            if len(entries) >= n:
                break

        return entries

    def get_by_trace_id(self, trace_id: str) -> dict | None:
        """Look up a specific log entry by trace_id."""
        # Read the index file to find offset
        if not os.path.exists(self._idx_path):
            return None

        offset = None
        with open(self._idx_path, encoding="utf-8") as f:
            for line in f:
                tid, off = line.strip().split("\t")
                if tid == trace_id:
                    offset = int(off)
                    break

        if offset is None:
            return None

        with open(self._log_path, encoding="utf-8") as f:
            f.seek(offset)
            line = f.readline()
            if line:
                return json.loads(line.strip())
        return None

    def stats(self) -> dict:
        """Quick stats on logged retrievals."""
        if not os.path.exists(self._log_path):
            return {"total_entries": 0}

        total = 0
        strategies: dict[str, int] = {}
        errors = 0
        avg_latency = 0.0

        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                strategies[entry.get("strategy", "?")] = (
                    strategies.get(entry.get("strategy", "?"), 0) + 1
                )
                if not entry.get("success", True):
                    errors += 1
                avg_latency += entry.get("latency_total_ms", 0)

        if total > 0:
            avg_latency /= total

        return {
            "total_entries": total,
            "strategies": strategies,
            "errors": errors,
            "avg_latency_ms": round(avg_latency, 1),
        }
