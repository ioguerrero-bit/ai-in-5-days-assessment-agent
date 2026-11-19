"""Asynchronous background memory ETL pipeline (Extract -> Redact -> Consolidate).

Executes memory consolidation via `asyncio.create_task` so expensive
cross-session summarization and Vertex AI Memory Bank updates never block
interactive user responses.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from assessment_agent.memory.store import PersistentEvaluationStore
from assessment_agent.observability.logging import get_structured_logger, log_agent_event
from assessment_agent.observability.pii_redactor import redact_pii
from assessment_agent.observability.tracing import traced_span

_LOGGER = get_structured_logger("assessment_agent.memory.async")


class AsyncMemoryConsolidator:
    """Schedules non-blocking background memory extraction and consolidation tasks."""

    def __init__(self, store: PersistentEvaluationStore) -> None:
        self._store = store
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    def schedule_consolidation(
        self,
        *,
        learner_id: str,
        session_id: str,
        report_payload: dict[str, Any],
    ) -> asyncio.Task[dict[str, Any]]:
        """Fire-and-forget async background task for memory consolidation."""
        task = asyncio.create_task(
            self.consolidate_assessment_memory(
                learner_id=learner_id,
                session_id=session_id,
                report_payload=report_payload,
            ),
            name=f"memory_consolidate_{session_id}",
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def consolidate_assessment_memory(
        self,
        *,
        learner_id: str,
        session_id: str,
        report_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract recurring strengths/gaps, scrub PII, and persist long-term memories."""
        with traced_span(
            "memory.async_consolidate",
            {"session_id": session_id, "learner_id": learner_id},
        ):
            # Yield control to keep the event loop responsive for foreground traffic.
            await asyncio.sleep(0)

            scrubbed_report = redact_pii(report_payload)
            total_score = int(scrubbed_report.get("total_score", 0))
            language = str(scrubbed_report.get("report_language", "en"))
            categories = scrubbed_report.get("categories", [])

            weak_criteria: list[str] = []
            strong_criteria: list[str] = []
            for cat in categories:
                for crit in cat.get("criteria", []):
                    pts = int(crit.get("points", 0))
                    label = str(crit.get("criterion", "unknown"))
                    if pts < 4:
                        weak_criteria.append(f"{label} ({pts}/5)")
                    elif pts == 5:
                        strong_criteria.append(label)

            self._store.upsert_memory(
                learner_id=learner_id,
                memory_key="preferred_language",
                memory_text=f"Learner prefers coaching feedback in language: {language}.",
                confidence=1.0,
            )
            self._store.upsert_memory(
                learner_id=learner_id,
                memory_key="latest_score_summary",
                memory_text=(
                    f"Latest capstone evaluation scored {total_score}/95. "
                    f"Top strengths: {', '.join(strong_criteria[:5]) or 'none yet'}. "
                    f"Priority gaps: {', '.join(weak_criteria[:5]) or 'none — rubric complete'}."
                ),
                confidence=0.95,
            )

            summary = {
                "status": "consolidated",
                "total_score": total_score,
                "weak_count": len(weak_criteria),
                "strong_count": len(strong_criteria),
            }
            log_agent_event(
                _LOGGER,
                "memory_consolidated",
                "Completed asynchronous learner memory consolidation.",
                agent_name="async_memory_consolidator",
                session_id=session_id,
                metadata=summary,
            )
            return summary

    async def flush(self) -> None:
        """Await all in-flight background consolidation tasks (used in graceful shutdown/tests)."""
        if self._pending_tasks:
            await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)
