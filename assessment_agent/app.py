"""Google ADK App container wiring Compaction, Context Caching, Persistent Sessions, Memory Bank & Plugins.

Supports both:
- Local / Cloud Run execution (`DatabaseSessionService` backed by SQLite/PostgreSQL)
- Managed Vertex AI Agent Engine (`VertexAiSessionService`, `VertexAiMemoryBankService`,
  and `vertexai.agent_engines.AdkApp(enable_tracing=True)`).
"""

from __future__ import annotations

from typing import Any, Optional

from assessment_agent.agent import root_agent
from assessment_agent.config.settings import AssessmentSettings, get_settings
from assessment_agent.memory.compaction import (
    build_adk_compaction_config,
    build_adk_context_cache_config,
)
from assessment_agent.observability.tracing import init_distributed_tracing
from assessment_agent.plugins.security_guardrails import AgentOpsSecurityAndAuditPlugin


def build_session_service(settings: Optional[AssessmentSettings] = None) -> Any:
    """Create a persistent ADK session service (Vertex AI in cloud, SQLite/Postgres locally)."""
    cfg = settings or get_settings()
    if cfg.use_vertex_ai and cfg.gcp_project_id and cfg.vertex_agent_engine_id:
        from google.adk.sessions import VertexAiSessionService  # type: ignore[import-untyped]

        return VertexAiSessionService(
            project=cfg.gcp_project_id,
            location=cfg.gcp_location,
            agent_engine_id=cfg.vertex_agent_engine_id,
        )

    from google.adk.sessions import DatabaseSessionService  # type: ignore[import-untyped]

    return DatabaseSessionService(db_url=cfg.session_db_url)


def build_memory_service(settings: Optional[AssessmentSettings] = None) -> Any:
    """Create a persistent ADK Memory Bank service when deployed on Vertex AI Agent Engine."""
    cfg = settings or get_settings()
    if cfg.use_vertex_ai and cfg.gcp_project_id and cfg.vertex_agent_engine_id:
        from google.adk.memory import VertexAiMemoryBankService  # type: ignore[import-untyped]

        return VertexAiMemoryBankService(
            project=cfg.gcp_project_id,
            location=cfg.gcp_location,
            agent_engine_id=cfg.vertex_agent_engine_id,
        )

    from google.adk.memory import InMemoryMemoryService  # type: ignore[import-untyped]

    return InMemoryMemoryService()


def build_adk_app(settings: Optional[AssessmentSettings] = None) -> Any:
    """Assemble the production ADK `App` with compaction, caching, and global security plugin."""
    cfg = settings or get_settings()
    init_distributed_tracing(service_name=cfg.app_name)

    from google.adk.apps import App  # type: ignore[import-untyped]

    return App(
        name=cfg.app_name,
        root_agent=root_agent,
        plugins=[AgentOpsSecurityAndAuditPlugin()],
        events_compaction_config=build_adk_compaction_config(cfg),
        context_cache_config=build_adk_context_cache_config(cfg),
    )


def build_vertex_agent_engine_app(settings: Optional[AssessmentSettings] = None) -> Any:
    """Wrap the ADK App inside Vertex AI `agent_engines.AdkApp` with Cloud Trace enabled."""
    cfg = settings or get_settings()
    from vertexai import agent_engines  # type: ignore[import-untyped]

    adk_app = build_adk_app(cfg)
    return agent_engines.AdkApp(
        agent=adk_app,
        enable_tracing=True,
        session_service_builder=lambda **_: build_session_service(cfg),
        memory_service_builder=lambda **_: build_memory_service(cfg),
    )


try:
    app = build_adk_app()
except ImportError:
    app = None
