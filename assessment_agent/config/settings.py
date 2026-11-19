"""Application settings and strategic Gemini model routing definitions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

SupportedLanguage = Literal["en", "es", "pt"]

RUBRIC_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Tool & Interface Design": (
        "Comprehensive Tool Docstrings",
        "Descriptive Naming",
        "Explicit JSON Schemas",
        "Guided Error Handling",
    ),
    "Context & Memory": (
        "Robust System Instructions",
        "History Compaction",
        "Persistent Session State",
        "Async Memory Operations",
    ),
    "Orchestration & Logic": (
        "Multi-Agent Patterns",
        "Strategic Model Routing",
        "Guardrails & Policy Plugins",
        "Human-in-the-Loop Hooks",
    ),
    "Observability & Tracing": (
        "Structured JSON Logging",
        "Intent vs. Outcome Capture",
        "Distributed Tracing",
        "PII Redaction",
    ),
    "Infrastructure & CI/CD": (
        "Automated Evaluation Suites",
        "Infrastructure as Code",
        "Secure Secret Management",
    ),
}

MAX_RUBRIC_SCORE = 95
POINTS_PER_CRITERION = 5
HITL_BORDERLINE_MIN = 70
HITL_BORDERLINE_MAX = 79


@dataclass(frozen=True)
class ModelRoutingConfig:
    """Routes lightweight tasks to Gemini Flash and deep architectural audits to Gemini Pro."""

    fast_model: str = "gemini-2.5-flash"
    reasoning_model: str = "gemini-2.5-pro"

    def select_model(self, task_complexity: Literal["fast", "reasoning"]) -> str:
        """Select the optimal model tier for a given agent role."""
        if task_complexity == "reasoning":
            return self.reasoning_model
        return self.fast_model


@dataclass(frozen=True)
class AssessmentSettings:
    """Centralized, non-secret runtime settings loaded from environment variables."""

    app_name: str = "ai_in_5_days_assessment_agent"
    gcp_project_id: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "")
    )
    gcp_location: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    )
    use_vertex_ai: bool = field(
        default_factory=lambda: os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE").upper() == "TRUE"
    )
    vertex_agent_engine_id: str = field(
        default_factory=lambda: os.getenv("VERTEX_AGENT_ENGINE_ID", "")
    )
    gemini_api_key_secret_name: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_API_KEY_SECRET_NAME", "assessment-agent-gemini-api-key"
        )
    )
    github_token_secret_name: str = field(
        default_factory=lambda: os.getenv(
            "GITHUB_TOKEN_SECRET_NAME", "assessment-agent-github-readonly-token"
        )
    )
    model_routing: ModelRoutingConfig = field(
        default_factory=lambda: ModelRoutingConfig(
            fast_model=os.getenv("ASSESSMENT_FAST_MODEL", "gemini-2.5-flash"),
            reasoning_model=os.getenv("ASSESSMENT_REASONING_MODEL", "gemini-2.5-pro"),
        )
    )
    session_db_url: str = field(
        default_factory=lambda: os.getenv(
            "ASSESSMENT_SESSION_DB_URL", "sqlite+aiosqlite:///./data/sessions.db"
        )
    )
    history_db_path: str = field(
        default_factory=lambda: os.getenv(
            "ASSESSMENT_HISTORY_DB_PATH", "./data/assessment_history.db"
        )
    )
    enable_cloud_dlp: bool = field(
        default_factory=lambda: os.getenv("ENABLE_CLOUD_DLP", "FALSE").upper() == "TRUE"
    )
    enable_cloud_trace: bool = field(
        default_factory=lambda: os.getenv("ENABLE_CLOUD_TRACE", "FALSE").upper() == "TRUE"
    )
    model_armor_template_id: str = field(
        default_factory=lambda: os.getenv("MODEL_ARMOR_TEMPLATE_ID", "")
    )
    default_language: SupportedLanguage = field(
        default_factory=lambda: _parse_language(os.getenv("DEFAULT_REPORT_LANGUAGE", "en"))
    )
    compaction_interval: int = 3
    compaction_overlap_size: int = 1
    context_cache_min_tokens: int = 2048
    context_cache_ttl_seconds: int = 600


def _parse_language(raw: str) -> SupportedLanguage:
    normalized = raw.strip().lower()
    if normalized in ("en", "es", "pt"):
        return normalized  # type: ignore[return-value]
    return "en"


@lru_cache(maxsize=1)
def get_settings() -> AssessmentSettings:
    """Return cached runtime settings."""
    return AssessmentSettings()
