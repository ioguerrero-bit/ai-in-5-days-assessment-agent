"""Repository cloning, indexing, AgentOps evidence scanning, and static security tools.

All tools follow Google ADK tool design best practices:
- Descriptive action-oriented names
- Comprehensive Google-style docstrings describing purpose, arguments, and outputs
- Strict Pydantic JSON schema validation
- Guided error handling with actionable `recovery_instructions`
- Concise bounded outputs to prevent context window bloat
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from assessment_agent.config.secrets import get_secret_value
from assessment_agent.config.settings import RUBRIC_CATEGORIES, get_settings
from assessment_agent.observability.pii_redactor import redact_pii
from assessment_agent.observability.tracing import traced_span
from assessment_agent.plugins.security_guardrails import (
    detect_prompt_injection,
    validate_public_repo_url,
)
from assessment_agent.schemas.models import (
    EvidenceSignal,
    RepoEvidenceScanResult,
    RepoFileEntry,
    RepoIndexResult,
    SecurityAuditResult,
    SecurityFinding,
    ToolErrorResult,
)

_IGNORED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".terraform"})
_TEXT_EXTENSIONS = frozenset(
    {
        ".py",
        ".go",
        ".java",
        ".md",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".tf",
        ".tfvars",
        ".txt",
        ".sh",
        ".dockerfile",
        "",
    }
)
_ROOT_MANIFESTS = frozenset(
    {"readme.md", "pyproject.toml", "requirements.txt", "go.mod", "pom.xml", "build.gradle"}
)

# Deterministic evidence matchers mapped to the exact 19 AgentOps Code Review Matrix criteria.
_CRITERION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "Comprehensive Tool Docstrings": (
        re.compile(r'"""[\s\S]{30,}?Args:[\s\S]*?"""'),
        re.compile(r'"""[\s\S]{30,}?Returns:[\s\S]*?"""'),
    ),
    "Descriptive Naming": (
        re.compile(r"\bdef\s+[a-z]+_[a-z]+_[a-z_]+\s*\("),
    ),
    "Explicit JSON Schemas": (
        re.compile(r"\bclass\s+\w+\(BaseModel\)|ConfigDict\(|model_json_schema|output_schema"),
    ),
    "Guided Error Handling": (
        re.compile(r"recovery_instructions|recovery_hint|isError|ToolErrorResult"),
    ),
    "Robust System Instructions": (
        re.compile(r"instruction\s*=|CONSTITUTION|system_instruction"),
    ),
    "History Compaction": (
        re.compile(r"EventsCompactionConfig|ContextCacheConfig|SlidingWindow|compaction_interval"),
    ),
    "Persistent Session State": (
        re.compile(r"DatabaseSessionService|VertexAiSessionService|sqlite3|VertexAiSearch|PersistentEvaluationStore"),
    ),
    "Async Memory Operations": (
        re.compile(r"asyncio\.create_task|VertexAiMemoryBankService|AsyncMemoryConsolidator"),
    ),
    "Multi-Agent Patterns": (
        re.compile(r"SequentialAgent|ParallelAgent|LoopAgent|sub_agents\s*="),
    ),
    "Strategic Model Routing": (
        re.compile(r"gemini-2\.5-flash|gemini-3\.5-flash"),
        re.compile(r"gemini-2\.5-pro|gemini-3\.1-pro|ModelRoutingConfig"),
    ),
    "Guardrails & Policy Plugins": (
        re.compile(r"BasePlugin|before_tool_callback|before_model_callback|Model Armor|model_armor"),
    ),
    "Human-in-the-Loop Hooks": (
        re.compile(r"request_confirmation|tool_confirmation|human_in_the_loop|hitl"),
    ),
    "Structured JSON Logging": (
        re.compile(r"StructuredJsonFormatter|json\.dumps\(|google\.cloud\.logging"),
    ),
    "Intent vs. Outcome Capture": (
        re.compile(r"tool_intent|intended_tool"),
        re.compile(r"tool_outcome|outcome_status"),
    ),
    "Distributed Tracing": (
        re.compile(r"opentelemetry|CloudTraceSpanExporter|start_as_current_span|enable_tracing\s*=\s*True"),
    ),
    "PII Redaction": (
        re.compile(r"PIIRedactor|redact_pii|deidentify_content|dlp_v2"),
    ),
    "Automated Evaluation Suites": (
        re.compile(r"golden_dataset|adk\s+eval|run_eval|AgentEvaluator"),
    ),
    "Infrastructure as Code": (
        re.compile(r'resource\s+"google_|terraform|google-agents-cli'),
    ),
    "Secure Secret Management": (
        re.compile(r"SecretManagerServiceClient|access_secret_version|google_secret_manager_secret"),
    ),
}

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Hardcoded GCP API Key", re.compile(r"\bAIza[0-9A-Za-z\-_]{30,45}\b")),
    (
        "Hardcoded GitHub Token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    ("Private Key Material", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "Hardcoded Secret Assignment",
        re.compile(
            r"(?i)\b(?:api_key|secret_key|access_token|password)\s*=\s*['\"](?!your-|example|placeholder|<)[A-Za-z0-9_\-]{12,}['\"]"
        ),
    ),
)

_RAW_PRINT_RE = re.compile(r"^\s*print\s*\(")


def clone_and_index_public_github_repo(
    repo_url: str,
    max_files: int = 120,
) -> dict[str, Any]:
    """Validate and index a public root GitHub repository (or local checkout path for offline tests).

    Clones public HTTPS GitHub repositories shallowly (`--depth 1`) into an isolated
    temporary directory without executing any repository code, then returns a bounded
    manifest of source files, detected ADK languages, and root-project verification.

    Args:
        repo_url: Public root GitHub HTTPS URL (e.g., https://github.com/owner/repo) or
            local repository directory path during offline evaluation runs.
        max_files: Maximum number of indexed files to return to prevent context bloat
            (default 120, capped at 250).

    Returns:
        A JSON-serializable dict validated against `RepoIndexResult` on success, or
        `ToolErrorResult` with step-by-step `recovery_instructions` on failure.
    """
    with traced_span("tool.clone_and_index_public_github_repo", {"repo_url": repo_url}):
        validation_error = validate_public_repo_url(repo_url)
        if validation_error is not None:
            return validation_error.model_dump()

        bounded_max = max(1, min(max_files, 250))
        cleaned = repo_url.strip()

        if cleaned.startswith("https://"):
            target_dir = Path(tempfile.mkdtemp(prefix="agent_assess_repo_"))
            clone_url = cleaned
            settings = get_settings()
            # Optional read-only GitHub token injected via Secret Manager (never hardcoded).
            token = get_secret_value(
                settings.github_token_secret_name,
                fallback_env_var="GITHUB_READONLY_TOKEN",
                required=False,
            )
            if token and clone_url.startswith("https://github.com/"):
                clone_url = clone_url.replace(
                    "https://github.com/", f"https://x-access-token:{token}@github.com/", 1
                )

            try:
                proc = subprocess.run(
                    ["git", "clone", "--depth", "1", "--quiet", clone_url, str(target_dir)],
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return ToolErrorResult(
                    error_code="GIT_CLONE_TIMEOUT_OR_EXEC_ERROR",
                    message=f"Unable to clone repository: {redact_pii(str(exc))}",
                    recovery_instructions=(
                        "Verify the repository is public and reachable, or ask the learner to "
                        "confirm the exact https://github.com/<owner>/<repo> URL."
                    ),
                ).model_dump()

            if proc.returncode != 0:
                return ToolErrorResult(
                    error_code="GIT_CLONE_FAILED",
                    message=f"Git clone failed: {redact_pii(proc.stderr.strip()[:300])}",
                    recovery_instructions=(
                        "Check that the GitHub repository is set to PUBLIC (private repos cannot "
                        "be cloned by the Assessment Agent) and that the URL points to the repo root."
                    ),
                ).model_dump()
            repo_root = target_dir
        else:
            repo_root = Path(cleaned).expanduser().resolve()
            if not repo_root.exists() or not repo_root.is_dir():
                return ToolErrorResult(
                    error_code="LOCAL_REPO_NOT_FOUND",
                    message=f"Local path '{repo_root}' does not exist or is not a directory.",
                    recovery_instructions=(
                        "Provide either an existing local repository directory path or a public "
                        "https://github.com/<owner>/<repo> URL."
                    ),
                ).model_dump()

        entries: list[RepoFileEntry] = []
        detected_langs: set[str] = set()
        root_filenames = {p.name.lower() for p in repo_root.iterdir() if p.is_file()}
        is_root_project = bool(root_filenames & _ROOT_MANIFESTS) or any(
            (repo_root / name).is_dir() for name in ("src", "tests", "terraform")
        )

        for current_root, dirs, files in os.walk(repo_root):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)
            for fname in sorted(files):
                fpath = Path(current_root) / fname
                rel = fpath.relative_to(repo_root).as_posix()
                ext = fpath.suffix.lower()
                if ext == ".py":
                    detected_langs.add("Python")
                elif ext == ".go":
                    detected_langs.add("Go")
                elif ext == ".java":
                    detected_langs.add("Java")
                elif ext == ".tf":
                    detected_langs.add("Terraform")

                if ext in _TEXT_EXTENSIONS or fname.lower() == "dockerfile":
                    try:
                        size = fpath.stat().st_size
                    except OSError:
                        size = 0
                    entries.append(
                        RepoFileEntry(relative_path=rel, size_bytes=size, extension=ext)
                    )
                    if len(entries) >= bounded_max:
                        break
            if len(entries) >= bounded_max:
                break

        result = RepoIndexResult(
            repo_url=cleaned,
            local_checkout_path=str(repo_root),
            is_root_project=is_root_project,
            total_files=len(entries),
            detected_languages=sorted(detected_langs),
            files=entries,
        )
        return result.model_dump()


def scan_repo_for_agentops_evidence(local_checkout_path: str) -> dict[str, Any]:
    """Scan an indexed repository for deterministic code evidence across all 19 rubric criteria.

    Inspects source, test, eval, and Terraform files without executing any repository code,
    mapping verifiable file matches to every criterion in the 95-point AgentOps Code Review Matrix.

    Args:
        local_checkout_path: Local filesystem path returned by `clone_and_index_public_github_repo`.

    Returns:
        A JSON-serializable dict validated against `RepoEvidenceScanResult`, or `ToolErrorResult`
        with `recovery_instructions` if the directory is missing.
    """
    with traced_span("tool.scan_repo_for_agentops_evidence", {"repo_path": local_checkout_path}):
        root = Path(local_checkout_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return ToolErrorResult(
                error_code="REPO_CHECKOUT_NOT_FOUND",
                message=f"Checkout path '{local_checkout_path}' does not exist.",
                recovery_instructions=(
                    "Call `clone_and_index_public_github_repo` first and pass its returned "
                    "`local_checkout_path` to this tool."
                ),
            ).model_dump()

        file_contents: dict[str, str] = {}
        for current_root, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)
            for fname in sorted(files):
                fpath = Path(current_root) / fname
                if fpath.suffix.lower() not in _TEXT_EXTENSIONS and fname.lower() != "dockerfile":
                    continue
                try:
                    if fpath.stat().st_size > 250_000:
                        continue
                    rel = fpath.relative_to(root).as_posix()
                    file_contents[rel] = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

        signals: list[EvidenceSignal] = []
        estimated_score = 0

        for category, criteria in RUBRIC_CATEGORIES.items():
            for criterion in criteria:
                patterns = _CRITERION_PATTERNS.get(criterion, ())
                matched_files: list[str] = []
                pattern_hits = 0

                for pat in patterns:
                    hit_this_pattern = False
                    for rel_path, content in file_contents.items():
                        # Avoid self-matching regex constants inside test fixtures when scanning real repos
                        if rel_path.startswith("evals/golden_dataset"):
                            continue
                        if pat.search(content):
                            hit_this_pattern = True
                            if rel_path not in matched_files:
                                matched_files.append(rel_path)
                    if hit_this_pattern:
                        pattern_hits += 1

                if patterns and pattern_hits == len(patterns):
                    confidence = "high"
                    estimated_score += 5
                    notes = f"Matched all {len(patterns)} deterministic signal(s)."
                elif pattern_hits > 0:
                    confidence = "medium"
                    estimated_score += 3
                    notes = f"Matched {pattern_hits}/{len(patterns)} deterministic signal(s)."
                else:
                    confidence = "missing"
                    notes = "No deterministic code evidence found; verify manually via file slice."

                signals.append(
                    EvidenceSignal(
                        category=category,
                        criterion=criterion,
                        matched_files=matched_files[:6],
                        confidence=confidence,  # type: ignore[arg-type]
                        notes=notes,
                    )
                )

        result = RepoEvidenceScanResult(
            repo_path=str(root),
            signals=signals,
            estimated_baseline_score=min(estimated_score, 95),
        )
        return result.model_dump()


def read_repo_file_line_range(
    local_checkout_path: str,
    relative_file_path: str,
    start_line: int = 1,
    end_line: int = 120,
) -> dict[str, Any]:
    """Read a bounded, PII-scrubbed slice of lines from a repository file to verify citations.

    Prevents path traversal outside `local_checkout_path` and caps output at 150 lines
    to protect the LLM context window from bloat.

    Args:
        local_checkout_path: Root directory returned by `clone_and_index_public_github_repo`.
        relative_file_path: POSIX path relative to the repository root (e.g., `agent.py`).
        start_line: 1-based inclusive starting line number (default 1).
        end_line: 1-based inclusive ending line number (max 150 lines per read).

    Returns:
        A dict containing numbered, PII-redacted source lines wrapped as untrusted data,
        or a `ToolErrorResult` with `recovery_instructions`.
    """
    with traced_span(
        "tool.read_repo_file_line_range",
        {"file": relative_file_path, "start_line": start_line, "end_line": end_line},
    ):
        root = Path(local_checkout_path).expanduser().resolve()
        candidate = (root / relative_file_path).resolve()

        try:
            candidate.relative_to(root)
        except ValueError:
            return ToolErrorResult(
                error_code="PATH_TRAVERSAL_BLOCKED",
                message="Requested file path escapes the repository checkout root.",
                recovery_instructions=(
                    "Pass a repo-relative POSIX path from the manifest returned by "
                    "`clone_and_index_public_github_repo` without `..` segments."
                ),
            ).model_dump()

        if not candidate.exists() or not candidate.is_file():
            return ToolErrorResult(
                error_code="FILE_NOT_FOUND",
                message=f"File '{relative_file_path}' does not exist in repository.",
                recovery_instructions=(
                    "Inspect the `files` list returned by `clone_and_index_public_github_repo` "
                    "and retry with an exact relative path."
                ),
            ).model_dump()

        if start_line < 1 or end_line < start_line:
            return ToolErrorResult(
                error_code="INVALID_LINE_RANGE",
                message=f"Invalid line range [{start_line}, {end_line}].",
                recovery_instructions="Ensure start_line >= 1 and end_line >= start_line.",
            ).model_dump()

        clamped_end = min(end_line, start_line + 149)
        raw_lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
        selected = raw_lines[start_line - 1 : clamped_end]
        numbered = [
            f"{idx}: {redact_pii(line)}"
            for idx, line in enumerate(selected, start=start_line)
        ]
        return {
            "status": "ok",
            "relative_file_path": relative_file_path,
            "start_line": start_line,
            "end_line": min(clamped_end, len(raw_lines)),
            "total_lines": len(raw_lines),
            "untrusted_content": "\n".join(numbered),
        }


def audit_repo_for_hardcoded_secrets_and_risks(local_checkout_path: str) -> dict[str, Any]:
    """Audit a repository for hardcoded credentials, raw print() logging, and prompt injections.

    All returned excerpts are scrubbed via `PIIRedactor` so leaked keys are never echoed
    into logs, traces, or LLM context windows.

    Args:
        local_checkout_path: Local directory returned by `clone_and_index_public_github_repo`.

    Returns:
        A JSON-serializable dict validated against `SecurityAuditResult`, or `ToolErrorResult`.
    """
    with traced_span(
        "tool.audit_repo_for_hardcoded_secrets_and_risks", {"repo_path": local_checkout_path}
    ):
        root = Path(local_checkout_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return ToolErrorResult(
                error_code="REPO_CHECKOUT_NOT_FOUND",
                message=f"Checkout path '{local_checkout_path}' does not exist.",
                recovery_instructions=(
                    "Call `clone_and_index_public_github_repo` first to obtain a valid checkout path."
                ),
            ).model_dump()

        findings: list[SecurityFinding] = []
        critical_secrets = 0
        prompt_injections = 0
        raw_prints = 0

        for current_root, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)
            for fname in sorted(files):
                fpath = Path(current_root) / fname
                rel = fpath.relative_to(root).as_posix()
                if fpath.suffix.lower() not in _TEXT_EXTENSIONS:
                    continue
                if rel.startswith(("tests/", "evals/")):
                    continue
                try:
                    lines = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue

                for line_no, line in enumerate(lines, start=1):
                    # Skip regex definitions themselves when auditing security scanner modules
                    if "re.compile(" in line:
                        continue
                    for label, pattern in _SECRET_PATTERNS:
                        if pattern.search(line):
                            critical_secrets += 1
                            findings.append(
                                SecurityFinding(
                                    severity="critical",
                                    finding_type=label,
                                    file_path=rel,
                                    line_number=line_no,
                                    redacted_snippet=redact_pii(line.strip()[:120]),
                                    remediation=(
                                        "Revoke the exposed credential immediately and load it "
                                        "via Google Cloud Secret Manager."
                                    ),
                                )
                            )
                    if detect_prompt_injection(line):
                        prompt_injections += 1
                        findings.append(
                            SecurityFinding(
                                severity="high",
                                finding_type="Suspected Prompt Injection in Repository",
                                file_path=rel,
                                line_number=line_no,
                                redacted_snippet=redact_pii(line.strip()[:120]),
                                remediation=(
                                    "Remove adversarial instruction overrides from repository text."
                                ),
                            )
                        )
                    if fpath.suffix.lower() == ".py" and _RAW_PRINT_RE.match(line):
                        raw_prints += 1
                        if raw_prints <= 10:
                            findings.append(
                                SecurityFinding(
                                    severity="low",
                                    finding_type="Raw print() Statement",
                                    file_path=rel,
                                    line_number=line_no,
                                    redacted_snippet=redact_pii(line.strip()[:120]),
                                    remediation=(
                                        "Replace print() with structured JSON logging containing "
                                        "trace_id and session metadata."
                                    ),
                                )
                            )

        result = SecurityAuditResult(
            repo_path=str(root),
            critical_secret_count=critical_secrets,
            prompt_injection_signals=prompt_injections,
            raw_print_calls=raw_prints,
            findings=findings[:30],
        )
        return result.model_dump()
