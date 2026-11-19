"""Human-in-the-Loop (HITL) approval hooks and persistent assessment report storage tools."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from assessment_agent.config.settings import (
    HITL_BORDERLINE_MAX,
    HITL_BORDERLINE_MIN,
    MAX_RUBRIC_SCORE,
    get_settings,
)
from assessment_agent.memory.async_consolidator import AsyncMemoryConsolidator
from assessment_agent.memory.store import PersistentEvaluationStore
from assessment_agent.observability.logging import get_structured_logger, log_agent_event
from assessment_agent.observability.pii_redactor import redact_pii
from assessment_agent.observability.tracing import traced_span
from assessment_agent.schemas.models import (
    AssessmentReport,
    HumanApprovalDecision,
    ToolErrorResult,
    ValidationError,
)

_LOGGER = get_structured_logger("assessment_agent.tools.hitl")


def request_human_certification_override(
    repo_url: str,
    proposed_score: int,
    trigger_reason: str,
    critical_secret_count: int = 0,
    tool_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Pause execution and request explicit Human-in-the-Loop (HITL) confirmation for high-stakes actions.

    Invoked whenever an evaluation crosses a high-stakes governance threshold:
    1. Critical hardcoded secrets were found in a public repository.
    2. The proposed score sits in the borderline certification zone (70-79 / 95).
    3. An official certification badge publish or manual score override is requested.

    Uses Google ADK's `tool_context.request_confirmation(...)` pause/resume hook so no
    high-stakes certification action completes without human review.

    Args:
        repo_url: Public GitHub URL of the evaluated capstone repository.
        proposed_score: Proposed rubric total between 0 and 95.
        trigger_reason: Clear plain-language explanation of why human review is required.
        critical_secret_count: Number of critical leaked credentials discovered in the repo.
        tool_context: ADK `ToolContext` injected automatically by the ADK runtime.

    Returns:
        A JSON-serializable dict validated against `HumanApprovalDecision`, or `ToolErrorResult`.
    """
    with traced_span(
        "tool.request_human_certification_override",
        {
            "repo_url": repo_url,
            "proposed_score": proposed_score,
            "critical_secret_count": critical_secret_count,
        },
    ):
        if proposed_score < 0 or proposed_score > MAX_RUBRIC_SCORE:
            return ToolErrorResult(
                error_code="SCORE_OUT_OF_RANGE",
                message=f"Proposed score {proposed_score} must be between 0 and {MAX_RUBRIC_SCORE}.",
                recovery_instructions=(
                    "Re-verify category sums so total_score falls within [0, 95] before requesting HITL."
                ),
            ).model_dump()

        scrubbed_reason = redact_pii(trigger_reason)
        is_borderline = HITL_BORDERLINE_MIN <= proposed_score <= HITL_BORDERLINE_MAX
        confirmation_prompt = (
            f"HITL Review Required for {redact_pii(repo_url)} "
            f"(Score: {proposed_score}/95, Secrets: {critical_secret_count}, "
            f"Borderline: {is_borderline}). Reason: {scrubbed_reason}. "
            "Approve or reject official certification decision?"
        )

        # Inspect ADK ToolContext confirmation state (pause if unconfirmed, resolve on resume).
        confirmation = getattr(tool_context, "tool_confirmation", None) if tool_context else None
        if not confirmation:
            if tool_context is not None and hasattr(tool_context, "request_confirmation"):
                try:
                    tool_context.request_confirmation(
                        hint=confirmation_prompt,
                        payload={
                            "repo_url": redact_pii(repo_url),
                            "proposed_score": proposed_score,
                            "critical_secret_count": critical_secret_count,
                            "trigger_reason": scrubbed_reason,
                        },
                    )
                except TypeError:
                    tool_context.request_confirmation(confirmation_prompt)

            log_agent_event(
                _LOGGER,
                "hitl_paused",
                "Paused workflow pending human reviewer confirmation.",
                agent_name="self_eval_gate_agent",
                metadata={
                    "proposed_score": proposed_score,
                    "critical_secret_count": critical_secret_count,
                },
            )
            return HumanApprovalDecision(
                status="pending_human_approval",
                repo_url=redact_pii(repo_url),
                trigger_reason=scrubbed_reason,
                reviewer_notes="Awaiting explicit human approval via ADK confirmation hook.",
                proposed_score=proposed_score,
            ).model_dump()

        approved = _extract_approval_flag(confirmation)
        reviewer_notes = _extract_reviewer_notes(confirmation)
        final_status = "approved" if approved else "rejected"

        log_agent_event(
            _LOGGER,
            "hitl_resolved",
            f"Human reviewer resolved confirmation checkpoint: {final_status}.",
            agent_name="self_eval_gate_agent",
            metadata={"decision": final_status, "proposed_score": proposed_score},
        )
        return HumanApprovalDecision(
            status=final_status,  # type: ignore[arg-type]
            repo_url=redact_pii(repo_url),
            trigger_reason=scrubbed_reason,
            reviewer_notes=redact_pii(reviewer_notes),
            proposed_score=proposed_score,
        ).model_dump()


def save_assessment_report_to_history(
    learner_id: str,
    report_payload: dict[str, Any],
    session_id: str = "default-session",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Validate, redact PII, persist an assessment report, and schedule async memory consolidation.

    Persists the evaluation to the SQLite evaluation history store so follow-up resubmissions
    can calculate score deltas, and triggers background `AsyncMemoryConsolidator` without
    blocking the foreground turn.

    Args:
        learner_id: Anonymized or pseudonymized learner handle (scrubbed before persistence).
        report_payload: Dictionary conforming strictly to the `AssessmentReport` schema.
        session_id: Active session identifier for telemetry correlation.
        db_path: Optional override path for the SQLite history database.

    Returns:
        A dict summarizing the saved run ID, previous score delta (if any), and async
        consolidation scheduling state, or `ToolErrorResult` on schema validation failure.
    """
    with traced_span(
        "tool.save_assessment_report_to_history",
        {"session_id": session_id, "learner_id": learner_id},
    ):
        try:
            validated = AssessmentReport.model_validate(report_payload)
        except ValidationError as exc:
            return ToolErrorResult(
                error_code="INVALID_REPORT_SCHEMA",
                message=f"AssessmentReport validation failed: {exc}",
                recovery_instructions=(
                    "Verify that all category awarded_points sum to total_score (<= 95) and "
                    "every required AssessmentReport field is populated, then retry."
                ),
            ).model_dump()

        settings = get_settings()
        store = PersistentEvaluationStore(db_path=db_path or settings.history_db_path)
        previous_runs = store.get_recent_runs(learner_id=learner_id, limit=1)
        previous_score = previous_runs[0]["total_score"] if previous_runs else None

        dumped = validated.model_dump()
        run_id = store.save_assessment_run(
            learner_id=learner_id,
            repo_url=validated.repo_url,
            report_language=validated.report_language,
            total_score=validated.total_score,
            hitl_required=validated.hitl_review_required,
            report_payload=dumped,
        )

        # Schedule non-blocking async background memory consolidation when inside an event loop.
        async_scheduled = False
        try:
            asyncio.get_running_loop()
            consolidator = AsyncMemoryConsolidator(store)
            consolidator.schedule_consolidation(
                learner_id=learner_id,
                session_id=session_id,
                report_payload=dumped,
            )
            async_scheduled = True
        except RuntimeError:
            # Synchronous caller (e.g., CLI script without running event loop)
            async_scheduled = False

        score_delta = (
            validated.total_score - previous_score if previous_score is not None else None
        )
        return {
            "status": "ok",
            "run_id": run_id,
            "total_score": validated.total_score,
            "previous_score": previous_score,
            "score_delta": score_delta,
            "async_memory_scheduled": async_scheduled,
        }


def _extract_approval_flag(confirmation: Any) -> bool:
    if isinstance(confirmation, dict):
        return bool(confirmation.get("confirmed", confirmation.get("approved", False)))
    return bool(getattr(confirmation, "confirmed", getattr(confirmation, "approved", False)))


def _extract_reviewer_notes(confirmation: Any) -> str:
    if isinstance(confirmation, dict):
        payload = confirmation.get("payload") or {}
        return str(confirmation.get("notes") or payload.get("notes") or "")
    payload = getattr(confirmation, "payload", None) or {}
    if isinstance(payload, dict):
        return str(payload.get("notes", ""))
    return ""
