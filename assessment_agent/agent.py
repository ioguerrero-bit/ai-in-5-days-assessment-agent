"""Multi-agent orchestration graph for the AI in 5 Days Multilingual Assessment Agent.

Topology (Google ADK SequentialAgent + ParallelAgent):
  root_agent (SequentialAgent: AssessmentCoordinator)
    ├── 1. repo_ingestion_agent            [Gemini Flash — fast cloning, indexing & static triage]
    ├── 2. parallel_rubric_auditors        [ParallelAgent — concurrent specialist review panel]
    │     ├── tool_and_context_auditor     [Gemini Pro — Categories 1 & 2 (40 pts)]
    │     ├── orchestration_obs_auditor    [Gemini Pro — Categories 3 & 4 (40 pts)]
    │     └── infra_and_video_auditor      [Gemini Pro — Category 5 (15 pts) + EN/ES/PT video]
    ├── 3. self_eval_quality_gate_agent    [Gemini Flash — arithmetic check, citation check & HITL]
    └── 4. multilingual_coach_agent        [Gemini Pro — localized EN/ES/PT coaching & persistence]
"""

from __future__ import annotations

from typing import Any, Optional

from assessment_agent.config.settings import AssessmentSettings, get_settings
from assessment_agent.observability.tracing import init_distributed_tracing
from assessment_agent.prompts.constitutions import (
    INFRA_AND_VIDEO_AUDITOR_CONSTITUTION,
    MULTILINGUAL_COACH_CONSTITUTION,
    ORCHESTRATION_AND_OBS_AUDITOR_CONSTITUTION,
    REPO_INGESTION_CONSTITUTION,
    SELF_EVAL_GATE_CONSTITUTION,
    TOOL_AND_CONTEXT_AUDITOR_CONSTITUTION,
)
from assessment_agent.tools import (
    analyze_multilingual_walkthrough_video,
    audit_repo_for_hardcoded_secrets_and_risks,
    clone_and_index_public_github_repo,
    read_repo_file_line_range,
    request_human_certification_override,
    save_assessment_report_to_history,
    scan_repo_for_agentops_evidence,
)


def build_root_agent(settings: Optional[AssessmentSettings] = None) -> Any:
    """Construct the ADK multi-agent coordinator with strategic Flash/Pro model routing."""
    cfg = settings or get_settings()
    init_distributed_tracing(service_name=cfg.app_name)

    from google.adk.agents import (  # type: ignore[import-untyped]
        LlmAgent,
        ParallelAgent,
        SequentialAgent,
    )

    fast_model = cfg.model_routing.select_model("fast")
    reasoning_model = cfg.model_routing.select_model("reasoning")

    # Stage 1 (Fast tier: Gemini Flash): Deterministic cloning, indexing, and static scanning
    repo_ingestion_agent = LlmAgent(
        name="repo_ingestion_agent",
        model=fast_model,
        description=(
            "Safely clones, indexes, and runs static AgentOps & security reconnaissance on "
            "public root GitHub repositories."
        ),
        instruction=REPO_INGESTION_CONSTITUTION,
        tools=[
            clone_and_index_public_github_repo,
            scan_repo_for_agentops_evidence,
            audit_repo_for_hardcoded_secrets_and_risks,
        ],
        output_key="repo_recon_findings",
    )

    # Stage 2 (Reasoning tier: Gemini Pro): Concurrent 3-specialist rubric review panel
    tool_and_context_auditor = LlmAgent(
        name="tool_and_context_auditor",
        model=reasoning_model,
        description="Audits Category 1 (Tool & Interface Design) and Category 2 (Context & Memory).",
        instruction=TOOL_AND_CONTEXT_AUDITOR_CONSTITUTION,
        tools=[read_repo_file_line_range],
        output_key="scores_categories_1_2",
    )

    orchestration_obs_auditor = LlmAgent(
        name="orchestration_obs_auditor",
        model=reasoning_model,
        description=(
            "Audits Category 3 (Orchestration & Logic) and Category 4 (Observability & Tracing)."
        ),
        instruction=ORCHESTRATION_AND_OBS_AUDITOR_CONSTITUTION,
        tools=[read_repo_file_line_range],
        output_key="scores_categories_3_4",
    )

    infra_and_video_auditor = LlmAgent(
        name="infra_and_video_auditor",
        model=reasoning_model,
        description=(
            "Audits Category 5 (Infrastructure & CI/CD) and optional multilingual YouTube walkthroughs."
        ),
        instruction=INFRA_AND_VIDEO_AUDITOR_CONSTITUTION,
        tools=[read_repo_file_line_range, analyze_multilingual_walkthrough_video],
        output_key="scores_category_5_and_video",
    )

    parallel_rubric_auditors = ParallelAgent(
        name="parallel_rubric_auditors",
        description="Runs all three specialist rubric auditors concurrently to minimize latency.",
        sub_agents=[
            tool_and_context_auditor,
            orchestration_obs_auditor,
            infra_and_video_auditor,
        ],
    )

    # Stage 3 (Fast tier: Gemini Flash): Self-evaluation reflection gate & HITL trigger
    self_eval_quality_gate_agent = LlmAgent(
        name="self_eval_quality_gate_agent",
        model=fast_model,
        description=(
            "Verifies score math (<= 95), evidence citations, and triggers Human-in-the-Loop "
            "approval hooks on borderline scores or detected hardcoded secrets."
        ),
        instruction=SELF_EVAL_GATE_CONSTITUTION,
        tools=[request_human_certification_override],
        output_key="verified_rubric_decision",
    )

    # Stage 4 (Reasoning tier: Gemini Pro): Localized EN/ES/PT coaching & persistent history write
    multilingual_coach_agent = LlmAgent(
        name="multilingual_coach_agent",
        model=reasoning_model,
        description=(
            "Synthesizes the verified 95-point evaluation into an actionable EN/ES/PT coaching "
            "report and saves history for async memory consolidation."
        ),
        instruction=MULTILINGUAL_COACH_CONSTITUTION,
        tools=[save_assessment_report_to_history],
        output_key="final_assessment_report",
    )

    return SequentialAgent(
        name="ai_in_5_days_assessment_coordinator",
        description=(
            "End-to-end Multilingual AgentOps Assessment Coordinator grading ADK agent "
            "repositories against the 95-point AI in 5 Days Code Review Matrix."
        ),
        sub_agents=[
            repo_ingestion_agent,
            parallel_rubric_auditors,
            self_eval_quality_gate_agent,
            multilingual_coach_agent,
        ],
    )


try:
    root_agent = build_root_agent()
except ImportError:
    # Allows deterministic unit tests and golden evals to import package modules without google-adk.
    root_agent = None
