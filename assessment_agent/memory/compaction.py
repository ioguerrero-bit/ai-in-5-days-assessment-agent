"""History compaction and Google Cloud context caching configuration.

Combines native ADK `EventsCompactionConfig` and `ContextCacheConfig` with a
deterministic `SlidingWindowHistoryCompactor` so large multi-turn repository
audits stay within token budgets without suffering Context Rot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from assessment_agent.config.settings import AssessmentSettings, get_settings
from assessment_agent.observability.pii_redactor import redact_pii


def build_adk_compaction_config(settings: Optional[AssessmentSettings] = None) -> Any:
    """Build native ADK EventsCompactionConfig for automatic sliding-window summarization."""
    cfg = settings or get_settings()
    try:
        from google.adk.apps.app import EventsCompactionConfig  # type: ignore[import-untyped]

        return EventsCompactionConfig(
            compaction_interval=cfg.compaction_interval,
            overlap_size=cfg.compaction_overlap_size,
        )
    except ImportError:
        return {
            "compaction_interval": cfg.compaction_interval,
            "overlap_size": cfg.compaction_overlap_size,
        }


def build_adk_context_cache_config(settings: Optional[AssessmentSettings] = None) -> Any:
    """Build native ADK ContextCacheConfig to cache static rubric constitutions on Vertex AI."""
    cfg = settings or get_settings()
    try:
        from google.adk.agents.context_cache_config import (  # type: ignore[import-untyped]
            ContextCacheConfig,
        )

        return ContextCacheConfig(
            min_tokens=cfg.context_cache_min_tokens,
            ttl_seconds=cfg.context_cache_ttl_seconds,
        )
    except ImportError:
        return {
            "min_tokens": cfg.context_cache_min_tokens,
            "ttl_seconds": cfg.context_cache_ttl_seconds,
        }


@dataclass
class CompactionResult:
    """Output of deterministic sliding-window history compaction."""

    compacted_messages: list[dict[str, str]]
    summarized_count: int
    estimated_tokens: int


class SlidingWindowHistoryCompactor:
    """Deterministic token-budget compactor that summarizes older turns and retains recent context."""

    def __init__(
        self,
        max_tokens: int = 4000,
        retain_recent_turns: int = 4,
    ) -> None:
        self._max_tokens = max_tokens
        self._retain_recent_turns = retain_recent_turns

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Fast heuristic token estimator (~4 chars per token)."""
        return max(1, (len(text) + 3) // 4)

    def compact(self, history: list[dict[str, str]]) -> CompactionResult:
        """Compact conversation history when estimated tokens exceed `max_tokens`.

        Scrubs PII before generating the condensed summary header and preserves the
        most recent `retain_recent_turns` verbatim for conversational continuity.
        """
        scrubbed_history = [
            {
                "role": msg.get("role", "user"),
                "content": redact_pii(msg.get("content", "")),
            }
            for msg in history
        ]
        total_tokens = sum(self.estimate_tokens(m["content"]) for m in scrubbed_history)
        if total_tokens <= self._max_tokens or len(scrubbed_history) <= self._retain_recent_turns:
            return CompactionResult(
                compacted_messages=scrubbed_history,
                summarized_count=0,
                estimated_tokens=total_tokens,
            )

        older = scrubbed_history[: -self._retain_recent_turns]
        recent = scrubbed_history[-self._retain_recent_turns :]

        highlights = [
            f"- [{m['role']}] {m['content'][:160].replace(chr(10), ' ')}"
            for m in older
        ]
        summary_block = (
            f"Compacted summary of {len(older)} earlier turns:\n" + "\n".join(highlights)
        )
        # Enforce hard ceiling on summary text when history is very long.
        max_summary_chars = max(400, self._max_tokens * 2)
        if len(summary_block) > max_summary_chars:
            summary_block = summary_block[:max_summary_chars] + "... [truncated]"

        compacted = [{"role": "system", "content": summary_block}, *recent]
        new_tokens = sum(self.estimate_tokens(m["content"]) for m in compacted)
        return CompactionResult(
            compacted_messages=compacted,
            summarized_count=len(older),
            estimated_tokens=new_tokens,
        )
