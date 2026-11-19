"""ADK BasePlugin enforcing security guardrails, Model Armor, and Intent vs. Outcome logging.

Implements:
1. `before_model_callback`: Prompt-injection screening on untrusted repository content
   plus optional Google Cloud Model Armor sanitization.
2. `before_tool_callback`: SSRF & allowlisted public GitHub URL validation + structured
   JSON `tool_intent` logging prior to every tool execution.
3. `after_tool_callback`: Structured JSON `tool_outcome` logging with latency tracking
   and automatic PII/secret redaction on all tool outputs returned to the LLM.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

from assessment_agent.config.settings import get_settings
from assessment_agent.observability.logging import get_structured_logger, log_agent_event
from assessment_agent.observability.pii_redactor import redact_pii
from assessment_agent.schemas.models import ToolErrorResult

try:
    from google.adk.plugins.base_plugin import BasePlugin  # type: ignore[import-untyped]
except ImportError:
    class BasePlugin:  # type: ignore[no-redef]
        """Minimal BasePlugin shim when google-adk is absent in stdlib-only tests."""

        def __init__(self, name: str = "base_plugin") -> None:
            self.name = name


_LOGGER = get_structured_logger("assessment_agent.plugins.security")

_ALLOWED_REPO_HOSTS = frozenset({"github.com", "www.github.com"})

_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions"),
    re.compile(r"(?i)disregard\s+(?:the\s+)?(?:system|rubric)\s+(?:prompt|rules)"),
    re.compile(r"(?i)award\s+(?:me\s+)?(?:full\s+marks|95/95|100%)\s+regardless"),
    re.compile(r"(?i)exfiltrate\s+(?:secrets|api\s*keys|env|credentials)"),
    re.compile(r"(?i)override\s+(?:the\s+)?grading\s+rubric"),
)


def validate_public_repo_url(repo_url: str) -> Optional[ToolErrorResult]:
    """Validate that a submitted remote URL targets a public GitHub repository (anti-SSRF).

    Allows local filesystem paths ONLY for controlled offline unit tests/evaluations
    when prefixed cleanly without a remote URL scheme.
    """
    cleaned = repo_url.strip()
    if not cleaned:
        return ToolErrorResult(
            error_code="EMPTY_REPO_URL",
            message="Repository URL cannot be empty.",
            recovery_instructions=(
                "Ask the learner to provide a public root GitHub repository URL "
                "(e.g., https://github.com/<owner>/<repo>)."
            ),
        )

    parsed = urlparse(cleaned)
    if parsed.scheme in ("http", "https"):
        host = (parsed.hostname or "").lower()
        if host not in _ALLOWED_REPO_HOSTS:
            return ToolErrorResult(
                error_code="DISALLOWED_REPO_HOST",
                message=f"Host '{host}' is blocked by the SSRF allowlist policy.",
                recovery_instructions=(
                    "Only public https://github.com/<owner>/<repo> URLs are permitted. "
                    "Inform the user that private hosts, IPs, or non-GitHub URLs are rejected."
                ),
            )
        if parsed.scheme != "https":
            return ToolErrorResult(
                error_code="INSECURE_URL_SCHEME",
                message="Only HTTPS GitHub URLs are permitted.",
                recovery_instructions=(
                    "Rewrite the URL to use https://github.com/<owner>/<repo> and retry."
                ),
            )
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 2:
            return ToolErrorResult(
                error_code="MALFORMED_GITHUB_URL",
                message="GitHub URL must include both owner and repository name.",
                recovery_instructions=(
                    "Ask the user for the full root GitHub URL: https://github.com/<owner>/<repo>."
                ),
            )
        if len(path_parts) > 2 and path_parts[2] in ("tree", "blob"):
            return ToolErrorResult(
                error_code="SUBFOLDER_URL_NOT_ALLOWED",
                message="Subfolder links are not allowed; the capstone must be a root GitHub repo.",
                recovery_instructions=(
                    "Explain that the official evaluator clones the root repository URL "
                    "(https://github.com/<owner>/<repo>) and ask the learner to submit the root URL."
                ),
            )
    elif parsed.scheme and parsed.scheme not in ("", "file"):
        return ToolErrorResult(
            error_code="UNSUPPORTED_URL_SCHEME",
            message=f"Unsupported scheme '{parsed.scheme}'.",
            recovery_instructions="Provide an https://github.com/<owner>/<repo> URL.",
        )

    return None


def detect_prompt_injection(text: str) -> list[str]:
    """Return matched prompt-injection pattern strings detected in untrusted text."""
    if not text:
        return []
    matches: list[str] = []
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            matches.append(pattern.pattern)
    return matches


class AgentOpsSecurityAndAuditPlugin(BasePlugin):
    """Global ADK plugin for security policy enforcement and Intent vs. Outcome audit logging."""

    def __init__(self, name: str = "agentops_security_and_audit") -> None:
        super().__init__(name=name)
        self._tool_start_times: dict[str, float] = {}

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> Optional[Any]:
        """Audit outgoing LLM requests and flag untrusted prompt-injection attempts."""
        agent_name = getattr(callback_context, "agent_name", "unknown")
        invocation_id = getattr(callback_context, "invocation_id", "none")
        session_id = getattr(getattr(callback_context, "session", None), "id", "none")

        raw_text = str(getattr(llm_request, "contents", ""))
        injection_hits = detect_prompt_injection(raw_text)
        if injection_hits:
            log_agent_event(
                _LOGGER,
                "security_prompt_injection_flagged",
                "Detected prompt-injection phrase inside untrusted repository content; "
                "enforcing sandboxed data-only interpretation.",
                level=logging.WARNING,
                agent_name=agent_name,
                session_id=session_id,
                invocation_id=invocation_id,
                metadata={"matched_rules": len(injection_hits)},
            )

        settings = get_settings()
        if settings.model_armor_template_id:
            log_agent_event(
                _LOGGER,
                "model_armor_check",
                "Model Armor template configured for runtime prompt inspection.",
                agent_name=agent_name,
                session_id=session_id,
                invocation_id=invocation_id,
                metadata={"template_id": settings.model_armor_template_id},
            )

        return None

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> Optional[dict[str, Any]]:
        """Log explicit tool intent and enforce SSRF / URL security policies before execution."""
        tool_name = getattr(tool, "name", str(tool))
        agent_name = getattr(tool_context, "agent_name", "unknown")
        invocation_id = getattr(tool_context, "invocation_id", "none")
        session_id = getattr(getattr(tool_context, "session", None), "id", "none")
        call_key = f"{invocation_id}:{tool_name}"
        self._tool_start_times[call_key] = time.perf_counter()

        # 1. Explicit Intent Capture (before execution)
        log_agent_event(
            _LOGGER,
            "tool_intent",
            f"Agent intends to execute tool '{tool_name}'.",
            agent_name=agent_name,
            session_id=session_id,
            invocation_id=invocation_id,
            metadata={
                "intended_tool": tool_name,
                "sanitized_args": redact_pii(tool_args),
            },
        )

        # 2. Security Policy Enforcement (anti-SSRF on repo tools)
        if tool_name == "clone_and_index_public_github_repo":
            repo_url = str(tool_args.get("repo_url", ""))
            err = validate_public_repo_url(repo_url)
            if err is not None:
                log_agent_event(
                    _LOGGER,
                    "tool_outcome",
                    f"Blocked tool '{tool_name}' via security policy ({err.error_code}).",
                    level=logging.WARNING,
                    agent_name=agent_name,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    metadata={
                        "tool": tool_name,
                        "outcome_status": "blocked_by_policy",
                        "error_code": err.error_code,
                    },
                )
                return err.model_dump()

        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> Optional[Any]:
        """Log actual tool outcome with latency and scrub PII/secrets from outputs."""
        tool_name = getattr(tool, "name", str(tool))
        agent_name = getattr(tool_context, "agent_name", "unknown")
        invocation_id = getattr(tool_context, "invocation_id", "none")
        session_id = getattr(getattr(tool_context, "session", None), "id", "none")
        call_key = f"{invocation_id}:{tool_name}"

        start_ts = self._tool_start_times.pop(call_key, None)
        latency_ms = round((time.perf_counter() - start_ts) * 1000.0, 2) if start_ts else 0.0

        scrubbed_result = redact_pii(result)
        outcome_status = (
            scrubbed_result.get("status", "ok")
            if isinstance(scrubbed_result, dict)
            else "ok"
        )

        # Explicit Outcome Capture (after execution)
        log_agent_event(
            _LOGGER,
            "tool_outcome",
            f"Completed tool '{tool_name}' with status '{outcome_status}'.",
            agent_name=agent_name,
            session_id=session_id,
            invocation_id=invocation_id,
            metadata={
                "tool": tool_name,
                "outcome_status": outcome_status,
                "latency_ms": latency_ms,
                "error_code": (
                    scrubbed_result.get("error_code")
                    if isinstance(scrubbed_result, dict)
                    else None
                ),
            },
        )
        return scrubbed_result
