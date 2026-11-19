"""OpenTelemetry distributed tracing setup with Google Cloud Trace support.

Links spans end-to-end across user request -> coordinator -> specialist auditors
-> tool executions -> background memory consolidation. Falls back cleanly to a
local in-memory/noop span recorder when OpenTelemetry SDK packages are absent
in minimal offline test environments.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from assessment_agent.config.settings import get_settings
from assessment_agent.observability.pii_redactor import redact_pii

_CURRENT_TRACE_ID: ContextVar[str] = ContextVar("assessment_trace_id", default="")
_CURRENT_SPAN_ID: ContextVar[str] = ContextVar("assessment_span_id", default="")

_TRACING_INITIALIZED = False
_OTEL_AVAILABLE = False

try:
    from opentelemetry import trace as otel_trace  # type: ignore[import-untyped]
    from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    _OTEL_AVAILABLE = True
except ImportError:
    otel_trace = None  # type: ignore[assignment]


@dataclass
class RecordedSpan:
    """Lightweight span snapshot useful for tests and local telemetry inspection."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"


_LOCAL_SPAN_LOG: list[RecordedSpan] = []


def init_distributed_tracing(
    service_name: str = "ai-in-5-days-assessment-agent",
    enable_cloud_trace: Optional[bool] = None,
) -> bool:
    """Initialize OpenTelemetry TracerProvider with Cloud Trace or Console exporter."""
    global _TRACING_INITIALIZED
    if _TRACING_INITIALIZED:
        return _OTEL_AVAILABLE

    if not _OTEL_AVAILABLE:
        _TRACING_INITIALIZED = True
        return False

    settings = get_settings()
    use_cloud_trace = (
        settings.enable_cloud_trace
        if enable_cloud_trace is None
        else enable_cloud_trace
    )

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter_configured = False
    if use_cloud_trace and settings.gcp_project_id:
        try:
            from opentelemetry.exporter.cloud_trace import (  # type: ignore[import-untyped]
                CloudTraceSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(CloudTraceSpanExporter(project_id=settings.gcp_project_id))
            )
            exporter_configured = True
        except Exception:  # noqa: BLE001 - fall back to console exporter
            exporter_configured = False

    if not exporter_configured:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    otel_trace.set_tracer_provider(provider)
    _TRACING_INITIALIZED = True
    return True


def get_tracer(name: str = "assessment_agent"):
    """Return an OpenTelemetry tracer when installed, else None."""
    if _OTEL_AVAILABLE and otel_trace is not None:
        return otel_trace.get_tracer(name)
    return None


def get_current_trace_context() -> tuple[str, str]:
    """Return active (trace_id, span_id) from OpenTelemetry or ContextVar fallback."""
    if _OTEL_AVAILABLE and otel_trace is not None:
        span = otel_trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    return _CURRENT_TRACE_ID.get(), _CURRENT_SPAN_ID.get()


def get_recorded_spans() -> list[RecordedSpan]:
    """Return spans recorded by the local tracer helper (used by unit tests & evals)."""
    return list(_LOCAL_SPAN_LOG)


def clear_recorded_spans() -> None:
    """Clear the local span list between isolated test runs."""
    _LOCAL_SPAN_LOG.clear()


@contextmanager
def traced_span(
    span_name: str,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[RecordedSpan]:
    """Context manager creating a linked distributed span with PII-scrubbed attributes."""
    scrubbed_attrs = redact_pii(attributes or {})
    parent_trace_id, parent_span_id = get_current_trace_context()
    trace_id = parent_trace_id or uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]

    recorded = RecordedSpan(
        name=span_name,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id or None,
        attributes=dict(scrubbed_attrs),
    )

    trace_token = _CURRENT_TRACE_ID.set(trace_id)
    span_token = _CURRENT_SPAN_ID.set(span_id)
    tracer = get_tracer()

    try:
        if tracer is not None:
            with tracer.start_as_current_span(span_name) as otel_span:
                for key, value in scrubbed_attrs.items():
                    otel_span.set_attribute(str(key), str(value))
                yield recorded
        else:
            yield recorded
    except Exception:
        recorded.status = "ERROR"
        raise
    finally:
        _LOCAL_SPAN_LOG.append(recorded)
        _CURRENT_TRACE_ID.reset(trace_token)
        _CURRENT_SPAN_ID.reset(span_token)
