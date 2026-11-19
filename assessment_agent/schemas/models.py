"""Strict Pydantic v2 JSON schemas constraining tool I/O and rubric scoring outputs."""

from __future__ import annotations

from typing import Literal, Optional

from assessment_agent.config.settings import MAX_RUBRIC_SCORE, SupportedLanguage
from assessment_agent.schemas._compat import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)


class ToolErrorResult(BaseModel):
    """Guided error response returned to the LLM with actionable recovery steps."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["error"] = "error"
    error_code: str = Field(
        ...,
        min_length=2,
        description="Machine-readable error identifier (e.g., INVALID_GITHUB_URL).",
    )
    message: str = Field(..., min_length=5, description="Human-readable failure summary.")
    recovery_instructions: str = Field(
        ...,
        min_length=10,
        description="Step-by-step guidance instructing the LLM how to recover or prompt the user.",
    )


class RepoFileEntry(BaseModel):
    """Indexed repository file metadata."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(..., min_length=1, description="Repo-relative POSIX path.")
    size_bytes: int = Field(..., ge=0, description="File size in bytes.")
    extension: str = Field(default="", description="Lowercase file suffix.")


class RepoIndexResult(BaseModel):
    """Structured output of `clone_and_index_public_github_repo`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    repo_url: str = Field(..., min_length=1, description="Canonical repository URL or local path.")
    local_checkout_path: str = Field(..., min_length=1, description="Sandboxed local checkout path.")
    is_root_project: bool = Field(
        ...,
        description="True when root contains standard project manifests (README/pyproject/agent).",
    )
    total_files: int = Field(..., ge=0, description="Total indexed text/source files.")
    detected_languages: list[str] = Field(
        default_factory=list, description="Detected ADK/programming languages."
    )
    files: list[RepoFileEntry] = Field(
        default_factory=list, description="Bounded manifest of indexed source files."
    )


class EvidenceSignal(BaseModel):
    """Single static-analysis signal mapped to one of the 19 rubric criteria."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=3, description="Rubric category name.")
    criterion: str = Field(..., min_length=3, description="Exact rubric criterion name.")
    matched_files: list[str] = Field(
        default_factory=list, description="Files showing verifiable code patterns."
    )
    confidence: Literal["high", "medium", "low", "missing"] = Field(
        ..., description="Deterministic evidence confidence tier."
    )
    notes: str = Field(default="", description="Concise evidence summary.")


class RepoEvidenceScanResult(BaseModel):
    """Structured output of `scan_repo_for_agentops_evidence`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    repo_path: str = Field(..., min_length=1)
    signals: list[EvidenceSignal] = Field(default_factory=list)
    estimated_baseline_score: int = Field(..., ge=0, le=MAX_RUBRIC_SCORE)


class SecurityFinding(BaseModel):
    """Hardcoded credential or prompt-injection risk discovered in a repository."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "high", "medium", "low"]
    finding_type: str = Field(..., min_length=3)
    file_path: str = Field(..., min_length=1)
    line_number: int = Field(..., ge=1)
    redacted_snippet: str = Field(
        ..., description="PII/secret-redacted excerpt (never echoes raw credentials)."
    )
    remediation: str = Field(..., min_length=5)


class SecurityAuditResult(BaseModel):
    """Structured output of `audit_repo_for_hardcoded_secrets_and_risks`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    repo_path: str = Field(..., min_length=1)
    critical_secret_count: int = Field(..., ge=0)
    prompt_injection_signals: int = Field(..., ge=0)
    raw_print_calls: int = Field(..., ge=0)
    findings: list[SecurityFinding] = Field(default_factory=list)


class VideoWalkthroughResult(BaseModel):
    """Structured output of `analyze_multilingual_walkthrough_video`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    video_url: str = Field(..., min_length=1)
    detected_language: SupportedLanguage
    covers_problem_and_solution: bool
    covers_architecture_and_code: bool
    covers_live_demo: bool
    english_submission_advisory: str = Field(
        ...,
        description="Notes whether an English version is needed for the official Starfleet evaluator.",
    )
    feedback_summary: str = Field(..., min_length=5)


class CriterionScore(BaseModel):
    """Individual rubric score (0 to 5 points) with mandatory code citations."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=3)
    criterion: str = Field(..., min_length=3)
    points: int = Field(..., ge=0, le=5, description="Score from 0 to 5.")
    evidence_citations: list[str] = Field(
        default_factory=list, description="Exact file paths and line references."
    )
    rationale: str = Field(..., min_length=5)
    recommended_fix: str = Field(default="")


class CategoryScore(BaseModel):
    """Aggregated rubric score for one of the five AgentOps categories."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=3)
    max_points: int = Field(..., ge=15, le=20)
    awarded_points: int = Field(..., ge=0, le=20)
    criteria: list[CriterionScore] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_subtotal(self) -> "CategoryScore":
        if self.criteria:
            computed = sum(c.points for c in self.criteria)
            if computed != self.awarded_points:
                raise ValidationError(
                    f"Category '{self.category}' awarded_points ({self.awarded_points}) "
                    f"does not match sum of criteria ({computed})."
                )
        if self.awarded_points > self.max_points:
            raise ValidationError(
                f"Category '{self.category}' awarded_points ({self.awarded_points}) "
                f"exceeds max_points ({self.max_points})."
            )
        return self


class AssessmentReport(BaseModel):
    """Complete 95-point AgentOps evaluation report returned to the learner."""

    model_config = ConfigDict(extra="forbid")

    repo_url: str = Field(..., min_length=1)
    report_language: SupportedLanguage = "en"
    total_score: int = Field(..., ge=0, le=MAX_RUBRIC_SCORE)
    max_score: int = Field(default=MAX_RUBRIC_SCORE, ge=MAX_RUBRIC_SCORE, le=MAX_RUBRIC_SCORE)
    categories: list[CategoryScore] = Field(default_factory=list)
    hitl_review_required: bool = False
    hitl_reason: Optional[str] = None
    executive_summary: str = Field(..., min_length=10)
    prioritized_action_plan: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_grand_total(self) -> "AssessmentReport":
        if self.categories:
            computed_total = sum(cat.awarded_points for cat in self.categories)
            if computed_total != self.total_score:
                raise ValidationError(
                    f"AssessmentReport total_score ({self.total_score}) does not match "
                    f"sum of categories ({computed_total})."
                )
        return self


class HumanApprovalDecision(BaseModel):
    """Structured response of the Human-in-the-Loop approval gate."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending_human_approval", "approved", "rejected"]
    repo_url: str = Field(..., min_length=1)
    trigger_reason: str = Field(..., min_length=5)
    reviewer_notes: str = Field(default="")
    proposed_score: int = Field(..., ge=0, le=MAX_RUBRIC_SCORE)
