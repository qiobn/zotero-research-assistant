"""LLM-based relevance judge for RAG recall evaluation.

Uses an external LLM API (OpenAI-compatible) to judge whether a paper is
relevant to a query. Batches all papers per query into a single API call
for efficiency.

Usage:
    judge = LLMJudge(model="openai-gpt-5-4")
    result = judge.judge_query(
        query="两步移动搜索法在可达性评估中的应用",
        papers=[
            {"key": "CVEATG7W", "title": "...", "tags": ["可达性", "两步移动搜索法"]},
            {"key": "5ES9KJCP", "title": "...", "tags": ["公共服务设施"]},
        ]
    )
    # result = {"CVEATG7W": True, "5ES9KJCP": False}
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from loguru import logger


@dataclass
class JudgeConfig:
    """Configuration for the LLM relevance judge.

    Reads from environment variables by default, or can be set explicitly.
    """
    api_base: str = ""
    api_key: str = ""
    model: str = "openai-gpt-5-4"
    max_retries: int = 3
    timeout_ms: int = 60000
    temperature: float = 0.0  # deterministic judgments
    batch_size: int = 60  # max papers per API call

    @classmethod
    def from_env(cls) -> JudgeConfig:
        return cls(
            api_base=os.getenv("EVA_JUDGE_API_BASE", ""),
            api_key=os.getenv("EVA_JUDGE_API_KEY", ""),
            model=os.getenv("EVA_JUDGE_MODEL", "openai-gpt-5-4"),
            max_retries=int(os.getenv("EVA_JUDGE_MAX_RETRIES", "3")),
            timeout_ms=int(os.getenv("EVA_JUDGE_TIMEOUT_MS", "60000")),
            temperature=float(os.getenv("EVA_JUDGE_TEMPERATURE", "0.0")),
        )


class LLMJudge:
    """Relevance judge that uses an LLM API to classify query-paper pairs."""

    def __init__(self, config: JudgeConfig | None = None) -> None:
        self.config = config or JudgeConfig.from_env()
        self._cache: dict[str, dict[str, bool]] = {}  # query_hash -> {paper_key: relevant}

    def judge_query(
        self,
        query: str,
        papers: list[dict],
        force: bool = False,
    ) -> dict[str, bool]:
        """Judge relevance of all papers for a single query.

        Args:
            query: The search query.
            papers: List of dicts with keys "key", "title", "tags" (optional: "abstract").
            force: If True, re-judge even if cached.

        Returns:
            Dict mapping paper_key -> True (relevant) / False (not relevant).
        """
        query_hash = self._hash_query(query)
        cache_key = f"{query_hash}|{self.config.model}"

        # Check cache
        if not force and cache_key in self._cache:
            cached = self._cache[cache_key]
            # Only return cached results for papers that are in the request
            requested_keys = {p["key"] for p in papers}
            if requested_keys.issubset(cached.keys()):
                return {k: cached[k] for k in requested_keys}

        # Prepare the batch request
        result = self._judge_batch(query, papers)

        # Update cache
        if cache_key in self._cache:
            self._cache[cache_key].update(result)
        else:
            self._cache[cache_key] = result

        return result

    def _judge_batch(self, query: str, papers: list[dict]) -> dict[str, bool]:
        """Send a batch of papers for a single query to the LLM judge.

        Returns dict of {paper_key: bool}.

        The API response includes token usage so we can track cost.
        """
        if not self.config.api_base or not self.config.api_key:
            logger.warning("EVA_JUDGE_API_BASE or EVA_JUDGE_API_KEY not set — "
                           "falling back to tag-based heuristic judge")
            return self._tag_heuristic_fallback(query, papers)

        prompt = self._build_judge_prompt(query, papers)

        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a strict relevance judge for an academic paper retrieval system "
                            "in the domain of urban planning, geography, and public service facilities.\n\n"
                            "## Task\n"
                            "For each paper, decide whether it is RELEVANT to the search query. "
                            "Be CONSERVATIVE — mark as relevant ONLY when there is a clear, substantive "
                            "connection. False positives inflate the pool and distort recall measurement.\n\n"
                            "## Relevance Criteria\n"
                            "A paper IS relevant ONLY if the query topic is a CORE focus of the paper:\n"
                            "- The paper's main research question directly addresses the query topic\n"
                            "- The query's methodology IS the paper's methodology (not just mentioned)\n"
                            "- The paper studies the SAME phenomenon, even if in a different city/region\n\n"
                            "A paper is NOT relevant if:\n"
                            "- It only shares one general keyword (e.g., both mention \"public facilities\" "
                            "but the paper is about sports facilities while the query is about medical facilities)\n"
                            "- It mentions the query topic only in a literature review sentence or background paragraph\n"
                            "- It belongs to the same broad field but a different sub-topic\n"
                            "- The overlap is only at the level of \"urban planning\" or \"geography\"\n\n"
                            "## Examples\n"
                            "Query: \"两步移动搜索法在医疗设施可达性中的应用\"\n"
                            "  RELEVANT: \"基于改进两步移动搜索法的上海就医可达性研究\" — same method + same facility type\n"
                            "  RELEVANT: \"Two-step floating catchment area method for healthcare accessibility in Beijing\" — same method + same topic\n"
                            "  NOT: \"城市公共体育设施的空间可达性分析\" — same concept (accessibility) but different facility type (sports vs medical), different method\n"
                            "  NOT: \"基于GIS的医疗设施空间分布特征研究\" — same facility type (medical) but different method (GIS spatial analysis vs 2SFCA)\n\n"
                            "Query: \"agent-based modeling urban social systems simulation\"\n"
                            "  RELEVANT: \"Agent-based modeling of urban neighborhood energy systems\" — same method (ABM), urban context\n"
                            "  NOT: \"Smart city solutions for waste management using IoT\" — different method (IoT), different topic (waste vs social systems)\n"
                            "  NOT: \"A review of social simulation methods\" — mentions ABM in passing as one of many methods, not the core focus\n\n"
                            "Query: \"武汉市江夏区纸坊街道的社区公共服务设施配置地方标准\"\n"
                            "  NOT: ANY paper — this is a hyper-specific local policy query. No academic paper in the library would study this exact street's facility standards.\n\n"
                            "## Output Format\n"
                            "Respond ONLY with:\n"
                            '{"relevance": [true, false, true, ...]}\n'
                            "Exactly N booleans, one per paper, same order as input."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": self.config.temperature,
                "max_tokens": 2048,
            }

            response = httpx.post(
                f"{self.config.api_base.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=self.config.timeout_ms / 1000,
            )
            response.raise_for_status()
            data = response.json()

            # Extract usage info
            usage = data.get("usage", {})
            if usage:
                logger.debug(
                    f"Judge [{self.config.model}] tokens: "
                    f"{usage.get('prompt_tokens', '?')} in + "
                    f"{usage.get('completion_tokens', '?')} out"
                )

            content = data["choices"][0]["message"]["content"]

            # Parse JSON response
            parsed = self._parse_response(content, len(papers))
            return dict(zip([p["key"] for p in papers], parsed))

        except Exception as e:
            logger.warning(f"Judge API call failed: {e}")
            logger.info("Falling back to tag-based heuristic judge")
            return self._tag_heuristic_fallback(query, papers)

    def _build_judge_prompt(self, query: str, papers: list[dict]) -> str:
        """Build a compact prompt with query + all papers."""
        lines = [f"Search Query: {query}", ""]
        lines.append("Papers to judge:")
        for i, p in enumerate(papers, 1):
            title = p.get("title", "(no title)").strip()
            tags = p.get("tags", [])
            tags_str = ", ".join(tags[:15]) if tags else "(no tags)"
            abstract = p.get("abstract", "")
            lines.append(f"{i}. [{p['key']}] {title}")
            lines.append(f"   Tags: {tags_str}")
            if abstract:
                # Truncate abstract to 200 chars
                abstract_short = abstract[:200] + ("..." if len(abstract) > 200 else "")
                lines.append(f"   Abstract: {abstract_short}")
        return "\n".join(lines)

    def _parse_response(self, content: str, expected_n: int) -> list[bool]:
        """Parse the LLM response into a list of booleans."""
        # Try to extract JSON block
        cleaned = content.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        # Find the JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

        try:
            parsed = json.loads(cleaned)
            relevance = parsed.get("relevance", parsed.get("judgments", []))
            if len(relevance) != expected_n:
                logger.warning(
                    f"Judge returned {len(relevance)} labels, expected {expected_n}. "
                    f"Truncating/padding."
                )
                while len(relevance) < expected_n:
                    relevance.append(False)
                relevance = relevance[:expected_n]
            return relevance
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse judge response: {e}")
            logger.debug(f"Raw response: {content[:500]}")
            return [True] * expected_n  # conservative: assume all relevant

    def _tag_heuristic_fallback(
        self, query: str, papers: list[dict]
    ) -> dict[str, bool]:
        """Heuristic relevance judgment using tag overlap when LLM is unavailable.

        This is a fallback — not as accurate as the LLM judge, but provides
        a reasonable baseline when no external API is configured.
        """
        query_lower = query.lower()
        # Extract significant terms from query (split on common separators)
        import re
        query_terms = set(
            re.sub(r"[的与和及或了在、，。；：""''（）()\s]", " ", query_lower).split()
        )
        query_terms = {t for t in query_terms if len(t) > 1}

        result: dict[str, bool] = {}
        for p in papers:
            title = p.get("title", "").lower()
            tags = [t.lower() for t in p.get("tags", [])]

            # Combine searchable text
            searchable = " ".join([title] + tags)

            # Count term matches
            matches = sum(1 for qt in query_terms if qt in searchable)
            # Bonus for exact tag matches
            tag_matches = sum(
                1 for qt in query_terms
                for t in tags
                if qt in t or t in qt
            )

            # Threshold: at least 30% of query terms match OR at least 2 tag-hits
            threshold = max(len(query_terms) * 0.3, 2)
            result[p["key"]] = (matches + tag_matches * 2) >= threshold

        return result

    @staticmethod
    def _hash_query(query: str) -> str:
        """Quick hash for cache key — not cryptographic."""
        return str(hash(query) & 0xFFFFFFFF)


# ── Convenience factory ──────────────────────────────────────────────────

_judge_instance: LLMJudge | None = None


def get_judge(model: str = "") -> LLMJudge:
    """Get or create the singleton LLM judge instance.

    Args:
        model: Override the model name. If empty, uses EVA_JUDGE_MODEL env var.
    """
    global _judge_instance
    if _judge_instance is None or model:
        config = JudgeConfig.from_env()
        if model:
            config.model = model
        _judge_instance = LLMJudge(config)
    return _judge_instance
