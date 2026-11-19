"""Automated Golden Dataset Regression Evaluation Harness for CI/CD.

Validates deterministic AgentOps rubric coverage, security finding accuracy,
multilingual video classification, SSRF policy blocking, and HITL trigger logic
against `evals/golden_dataset.json`. Exits non-zero on any regression.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure repo root is importable when invoked as `python3 evals/run_eval.py`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from assessment_agent.observability.logging import get_structured_logger, log_agent_event
from assessment_agent.observability.tracing import traced_span
from assessment_agent.tools.repo_tools import (
    audit_repo_for_hardcoded_secrets_and_risks,
    clone_and_index_public_github_repo,
    scan_repo_for_agentops_evidence,
)
from assessment_agent.tools.video_tools import analyze_multilingual_walkthrough_video

_LOGGER = get_structured_logger("assessment_agent.evals")


def _materialize_synthetic_repo(files: dict[str, str]) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="golden_eval_repo_"))
    for rel_path, content in files.items():
        target = tmp_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_dir


def run_golden_eval_suite(dataset_path: Path | None = None) -> dict[str, Any]:
    """Execute all golden evaluation cases and return a structured pass/fail summary."""
    path = dataset_path or (_REPO_ROOT / "evals" / "golden_dataset.json")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    passed = 0

    with traced_span("eval.golden_suite", {"suite": dataset["suite_name"]}):
        for case in dataset["cases"]:
            case_id = case["id"]
            mode = case["mode"]
            errors: list[str] = []

            if mode in ("local_repo_self_audit", "synthetic_repo"):
                repo_dir = (
                    _REPO_ROOT
                    if mode == "local_repo_self_audit"
                    else _materialize_synthetic_repo(case["files"])
                )
                index_res = clone_and_index_public_github_repo(str(repo_dir))
                if index_res.get("status") != "ok":
                    errors.append(f"Index failed: {index_res}")
                else:
                    scan_res = scan_repo_for_agentops_evidence(index_res["local_checkout_path"])
                    sec_res = audit_repo_for_hardcoded_secrets_and_risks(
                        index_res["local_checkout_path"]
                    )
                    score = scan_res["estimated_baseline_score"]
                    if not (case["expected_min_score"] <= score <= case["expected_max_score"]):
                        missing = [
                            s["criterion"]
                            for s in scan_res["signals"]
                            if s["confidence"] != "high"
                        ]
                        errors.append(
                            f"Score {score} outside [{case['expected_min_score']}, "
                            f"{case['expected_max_score']}]; non-high criteria: {missing}"
                        )
                    if sec_res["critical_secret_count"] < case["expected_critical_secrets"]:
                        errors.append(
                            f"Expected >= {case['expected_critical_secrets']} critical secrets, "
                            f"got {sec_res['critical_secret_count']}"
                        )
                    if (
                        case["expected_critical_secrets"] == 0
                        and sec_res["critical_secret_count"] != 0
                    ):
                        errors.append(
                            f"Expected 0 critical secrets, found {sec_res['critical_secret_count']}: "
                            f"{sec_res['findings']}"
                        )
                    if "expected_prompt_injections" in case:
                        if sec_res["prompt_injection_signals"] < case["expected_prompt_injections"]:
                            errors.append(
                                f"Expected >= {case['expected_prompt_injections']} prompt injection "
                                f"signals, got {sec_res['prompt_injection_signals']}"
                            )
                    if "expected_raw_prints" in case:
                        if case["expected_raw_prints"] == 0 and sec_res["raw_print_calls"] != 0:
                            errors.append(
                                f"Expected 0 raw print() calls, found {sec_res['raw_print_calls']}"
                            )
                        elif sec_res["raw_print_calls"] < case["expected_raw_prints"]:
                            errors.append(
                                f"Expected >= {case['expected_raw_prints']} raw print() calls, "
                                f"got {sec_res['raw_print_calls']}"
                            )

            elif mode == "video_walkthrough":
                video_res = analyze_multilingual_walkthrough_video(
                    video_url=case["video_url"],
                    transcript_excerpt=case["transcript_excerpt"],
                )
                if video_res.get("status") != "ok":
                    errors.append(f"Video analysis failed: {video_res}")
                else:
                    if video_res["detected_language"] != case["expected_language"]:
                        errors.append(
                            f"Expected language '{case['expected_language']}', "
                            f"got '{video_res['detected_language']}'"
                        )
                    all_covered = (
                        video_res["covers_problem_and_solution"]
                        and video_res["covers_architecture_and_code"]
                        and video_res["covers_live_demo"]
                    )
                    if all_covered != case["expected_all_sections_covered"]:
                        errors.append(f"Unexpected coverage flags: {video_res}")

            elif mode == "url_policy_check":
                res = clone_and_index_public_github_repo(case["repo_url"])
                if res.get("error_code") != case["expected_error_code"]:
                    errors.append(
                        f"Expected error_code '{case['expected_error_code']}', got '{res}'"
                    )

            else:
                errors.append(f"Unknown eval case mode: {mode}")

            ok = len(errors) == 0
            if ok:
                passed += 1
            results.append({"case_id": case_id, "passed": ok, "errors": errors})

    summary = {
        "suite_name": dataset["suite_name"],
        "total_cases": len(results),
        "passed_cases": passed,
        "pass_rate": round(passed / max(1, len(results)), 4),
        "results": results,
    }
    log_agent_event(
        _LOGGER,
        "golden_eval_completed",
        f"Golden evaluation suite finished: {passed}/{len(results)} passed.",
        agent_name="eval_harness",
        metadata=summary,
    )
    return summary


if __name__ == "__main__":
    report = run_golden_eval_suite()
    if report["passed_cases"] != report["total_cases"]:
        sys.exit(1)
