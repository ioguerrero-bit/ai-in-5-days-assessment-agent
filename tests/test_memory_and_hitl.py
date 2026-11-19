"""Tests for History Compaction, Persistent Vector Store, Async Memory, and HITL Hooks."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from assessment_agent.memory.async_consolidator import AsyncMemoryConsolidator
from assessment_agent.memory.compaction import SlidingWindowHistoryCompactor
from assessment_agent.memory.store import PersistentEvaluationStore
from assessment_agent.tools.hitl_tools import (
    request_human_certification_override,
    save_assessment_report_to_history,
)


class _MockToolContext:
    """Minimal ADK ToolContext double testing pause/resume confirmation behavior."""

    def __init__(self, confirmation: Any = None) -> None:
        self.tool_confirmation = confirmation
        self.requested_hints: list[str] = []

    def request_confirmation(self, *, hint: str, payload: dict[str, Any]) -> None:
        self.requested_hints.append(hint)


class TestMemoryAndHITL(unittest.TestCase):
    """Validates Category 2 (Context & Memory) and Category 3 (Human-in-the-Loop Hooks)."""

    def test_sliding_window_compactor_condenses_long_history_and_redacts_pii(self) -> None:
        compactor = SlidingWindowHistoryCompactor(max_tokens=80, retain_recent_turns=2)
        history = [
            {
                "role": "user",
                "content": f"Turn {i}: auditing repo for learner{i}@example.com " + ("x" * 120),
            }
            for i in range(6)
        ]
        result = compactor.compact(history)
        self.assertEqual(result.summarized_count, 4)
        self.assertEqual(len(result.compacted_messages), 3)  # 1 summary + 2 recent
        self.assertNotIn("learner0@example.com", result.compacted_messages[0]["content"])
        self.assertIn("[REDACTED_EMAIL]", result.compacted_messages[0]["content"])

    def test_persistent_store_and_async_memory_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "history.db")
            store = PersistentEvaluationStore(db_path=db_path)

            report_payload = {
                "repo_url": "https://github.com/example/demo-agent",
                "report_language": "es",
                "total_score": 85,
                "max_score": 95,
                "categories": [
                    {
                        "category": "Infrastructure & CI/CD",
                        "max_points": 15,
                        "awarded_points": 10,
                        "criteria": [
                            {
                                "category": "Infrastructure & CI/CD",
                                "criterion": "Infrastructure as Code",
                                "points": 5,
                                "evidence_citations": ["terraform/main.tf:1"],
                                "rationale": "Terraform modules present.",
                            },
                            {
                                "category": "Infrastructure & CI/CD",
                                "criterion": "Automated Evaluation Suites",
                                "points": 5,
                                "evidence_citations": ["evals/run_eval.py:1"],
                                "rationale": "Golden dataset present.",
                            },
                            {
                                "category": "Infrastructure & CI/CD",
                                "criterion": "Secure Secret Management",
                                "points": 0,
                                "evidence_citations": [],
                                "rationale": "Missing Secret Manager wiring.",
                            },
                        ],
                    }
                ],
                "hitl_review_required": False,
                "executive_summary": "Buen avance en IaC; falta conectar Secret Manager.",
                "prioritized_action_plan": ["Migrar API keys a Google Cloud Secret Manager."],
            }

            # Verify validation + persistence via tool (adjust total to match single category)
            report_payload["total_score"] = 10
            saved = save_assessment_report_to_history(
                learner_id="learner-spla-01",
                report_payload=report_payload,
                db_path=db_path,
            )
            self.assertEqual(saved["status"], "ok")
            self.assertEqual(saved["total_score"], 10)

            async def _run_async_etl() -> None:
                consolidator = AsyncMemoryConsolidator(store)
                task = consolidator.schedule_consolidation(
                    learner_id="learner-spla-01",
                    session_id="sess-async-1",
                    report_payload=report_payload,
                )
                self.assertFalse(task.done())  # Non-blocking background scheduling
                await consolidator.flush()

            asyncio.run(_run_async_etl())

            hits = store.search_memories("learner-spla-01", "Spanish language coaching preference")
            self.assertGreaterEqual(len(hits), 1)
            combined_memories = " ".join(m["memory_text"] for m in hits)
            self.assertIn("es", combined_memories)

    def test_hitl_hook_pauses_then_resumes_on_human_decision(self) -> None:
        # Phase 1: Unconfirmed -> pauses and invokes ToolContext.request_confirmation
        ctx_pause = _MockToolContext(confirmation=None)
        paused = request_human_certification_override(
            repo_url="https://github.com/example/borderline-agent",
            proposed_score=75,
            trigger_reason="Borderline score (75/95) requires instructor verification.",
            tool_context=ctx_pause,
        )
        self.assertEqual(paused["status"], "pending_human_approval")
        self.assertEqual(len(ctx_pause.requested_hints), 1)

        # Phase 2: Resumed with explicit human approval
        ctx_approved = _MockToolContext(
            confirmation={"confirmed": True, "notes": "Reviewed code manually; approved."}
        )
        approved = request_human_certification_override(
            repo_url="https://github.com/example/borderline-agent",
            proposed_score=75,
            trigger_reason="Borderline score (75/95) requires instructor verification.",
            tool_context=ctx_approved,
        )
        self.assertEqual(approved["status"], "approved")
        self.assertIn("Reviewed code manually", approved["reviewer_notes"])


if __name__ == "__main__":
    unittest.main()
