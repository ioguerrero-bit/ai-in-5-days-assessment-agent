"""Structured JSON logging compatible with Google Cloud Logging.

All log records are emitted as single-line JSON payloads containing RFC3339
timestamps, severity, OpenTelemetry trace/span correlation IDs, session metadata,
and automatic PII redaction prior to emission.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from assessment_agent.observability.pii_redactor import redact_pii
from assessment_agent.observability.tracing import get_current_trace_context


class StructuredJsonFormatter(logging.Formatter):
    """Formats Python log records as Cloud Logging-compatible JSON with PII scrubbing."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = get_current_trace_context()
        base_payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": redact_pii(record.getMessage()),
            "trace_id": getattr(record, "trace_id", None) or trace_id,
            "span_id": getattr(record, "span_id", None) or span_id,
        }

        extra_fields = getattr(record, "structured_fields", None)
        if isinstance(extra_fields, dict):
            base_payload.update(redact_pii(extra_fields))

        if record.exc_info:
            base_payload["exception"] = redact_pii(self.formatException(record.exc_info))

        return json.dumps(base_payload, ensure_ascii=False, default=str)


def get_structured_logger(name: str = "assessment_agent") -> logging.Logger:
    """Return a configured structured JSON logger without duplicate handlers."""
    logger = logging.getLogger(name)
    if not getattr(logger, "_structured_configured", False):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._structured_configured = True  # type: ignore[attr-defined]
    return logger


def log_agent_event(
    logger: logging.Logger,
    event_type: str,
    message: str,
    *,
    level: int = logging.INFO,
    agent_name: Optional[str] = None,
    session_id: Optional[str] = None,
    invocation_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Emit a structured JSON event and return the scrubbed event payload."""
    fields: dict[str, Any] = {
        "event_type": event_type,
        "agent_name": agent_name or "unknown",
        "session_id": session_id or "none",
        "invocation_id": invocation_id or "none",
    }
    if metadata:
        fields["metadata"] = redact_pii(metadata)

    logger.log(level, message, extra={"structured_fields": fields})
    return fields
