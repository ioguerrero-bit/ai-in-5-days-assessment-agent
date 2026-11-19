"""Observability primitives: PII redaction, structured JSON logging, and OTel tracing."""

from assessment_agent.observability.logging import get_structured_logger, log_agent_event
from assessment_agent.observability.pii_redactor import PIIRedactor, redact_pii
from assessment_agent.observability.tracing import (
    get_tracer,
    init_distributed_tracing,
    traced_span,
)

__all__ = [
    "PIIRedactor",
    "get_structured_logger",
    "get_tracer",
    "init_distributed_tracing",
    "log_agent_event",
    "redact_pii",
    "traced_span",
]
