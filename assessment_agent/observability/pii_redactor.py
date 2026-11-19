"""Active PII and secret redaction pipeline for logs, traces, and memory storage.

Combines deterministic local pattern scrubbing (always active, zero-latency) with
optional Google Cloud Data Loss Prevention (Cloud DLP) de-identification when
`ENABLE_CLOUD_DLP=TRUE` and a GCP project is configured.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from assessment_agent.config.settings import get_settings

# Deterministic patterns scrubbed before any persistence, logging, or tracing.
_REDACTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "[REDACTED_GCP_API_KEY]",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{30,45}\b"),
    ),
    (
        "[REDACTED_GITHUB_TOKEN]",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "[REDACTED_PRIVATE_KEY]",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    (
        "[REDACTED_BEARER_TOKEN]",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b"),
    ),
    (
        "[REDACTED_SECRET_ASSIGNMENT]",
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
    (
        "[REDACTED_SSN]",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "[REDACTED_CREDIT_CARD]",
        re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    ),
    (
        "[REDACTED_EMAIL]",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "[REDACTED_PHONE]",
        re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w)"),
    ),
)

_DLP_INFO_TYPES = (
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "CREDIT_CARD_NUMBER",
    "GCP_API_KEY",
    "AUTH_TOKEN",
)


class PIIRedactor:
    """Scrubs sensitive PII and credentials from text and nested JSON structures."""

    def __init__(
        self,
        enable_cloud_dlp: Optional[bool] = None,
        gcp_project_id: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._enable_cloud_dlp = (
            settings.enable_cloud_dlp if enable_cloud_dlp is None else enable_cloud_dlp
        )
        self._gcp_project_id = gcp_project_id or settings.gcp_project_id
        self._dlp_client = None

    def _get_dlp_client(self):
        if self._dlp_client is None:
            from google.cloud import dlp_v2  # type: ignore[import-untyped]

            self._dlp_client = dlp_v2.DlpServiceClient()
        return self._dlp_client

    def redact_text(self, text: str) -> str:
        """Redact PII and secret tokens from a string.

        Always executes deterministic local regex scrubbing first, then optionally
        applies Google Cloud DLP `deidentify_content` when enabled.
        """
        if not text:
            return text

        scrubbed = text
        for placeholder, pattern in _REDACTION_RULES:
            if placeholder == "[REDACTED_SECRET_ASSIGNMENT]":
                scrubbed = pattern.sub(rf"\1={placeholder}", scrubbed)
            else:
                scrubbed = pattern.sub(placeholder, scrubbed)

        if self._enable_cloud_dlp and self._gcp_project_id:
            scrubbed = self._redact_with_cloud_dlp(scrubbed)

        return scrubbed

    def _redact_with_cloud_dlp(self, text: str) -> str:
        """Apply Google Cloud DLP de-identification with safe fail-open to regex output."""
        try:
            client = self._get_dlp_client()
            parent = f"projects/{self._gcp_project_id}"
            inspect_config = {
                "info_types": [{"name": info_type} for info_type in _DLP_INFO_TYPES]
            }
            deidentify_config = {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "primitive_transformation": {
                                "replace_with_info_type_config": {}
                            }
                        }
                    ]
                }
            }
            response = client.deidentify_content(
                request={
                    "parent": parent,
                    "inspect_config": inspect_config,
                    "deidentify_config": deidentify_config,
                    "item": {"value": text},
                }
            )
            return response.item.value or text
        except Exception:  # noqa: BLE001 - regex scrubbing already applied
            return text

    def redact_payload(self, payload: Any) -> Any:
        """Recursively redact PII across dicts, lists, tuples, and strings."""
        if isinstance(payload, str):
            return self.redact_text(payload)
        if isinstance(payload, dict):
            return {
                str(k): self.redact_payload(v)
                for k, v in payload.items()
            }
        if isinstance(payload, list):
            return [self.redact_payload(item) for item in payload]
        if isinstance(payload, tuple):
            return tuple(self.redact_payload(item) for item in payload)
        return payload


_DEFAULT_REDACTOR = PIIRedactor()


def redact_pii(value: Any) -> Any:
    """Convenience function to scrub PII/secrets using the default PIIRedactor."""
    return _DEFAULT_REDACTOR.redact_payload(value)
