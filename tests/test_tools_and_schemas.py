"""Tests for Category 1 (Tool & Interface Design) and static repository inspection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assessment_agent.schemas.models import (
    AssessmentReport,
    CategoryScore,
    CriterionScore,
    ValidationError,
)
from assessment_agent.tools.repo_tools import (
    audit_repo_for_hardcoded_secrets_and_risks,
    clone_and_index_public_github_repo,
    read_repo_file_line_range,
    scan_repo_for_agentops_evidence,
)
from assessment_agent.tools.video_tools import analyze_multilingual_walkthrough_video


class TestToolsAndSchemas(unittest.TestCase):
    """Validates strict JSON schemas, guided errors, and deterministic repo scanning."""

    def test_category_subtotal_validation_rejects_mismatch(self) -> None:
        with self.assertRaises(ValidationError):
            CategoryScore(
                category="Tool & Interface Design",
                max_points=20,
                awarded_points=19,  # Does not match sum of criteria (5)
                criteria=[
                    CriterionScore(
                        category="Tool & Interface Design",
                        criterion="Descriptive Naming",
                        points=5,
                        evidence_citations=["tools/repo_tools.py:1"],
                        rationale="Specific action verbs used on every tool.",
                    )
                ],
            )

    def test_assessment_report_enforces_grand_total_and_bounds(self) -> None:
        cat = CategoryScore(
            category="Infrastructure & CI/CD",
            max_points=15,
            awarded_points=5,
            criteria=[
                CriterionScore(
                    category="Infrastructure & CI/CD",
                    criterion="Infrastructure as Code",
                    points=5,
                    evidence_citations=["terraform/main.tf:1"],
                    rationale="Terraform provisions Cloud Run and Secret Manager.",
                )
            ],
        )
        report = AssessmentReport(
            repo_url="https://github.com/example/agent",
            report_language="es",
            total_score=5,
            categories=[cat],
            executive_summary="Evaluación inicial del agente completada correctamente.",
        )
        self.assertEqual(report.total_score, 5)
        schema = AssessmentReport.model_json_schema()
        self.assertIn("total_score", schema["properties"])

    def test_guided_error_on_subfolder_github_url(self) -> None:
        res = clone_and_index_public_github_repo(
            "https://github.com/example/monorepo/tree/main/subproject"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["error_code"], "SUBFOLDER_URL_NOT_ALLOWED")
        self.assertIn("root repository URL", res["recovery_instructions"])

    def test_path_traversal_blocked_with_guided_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "README.md").write_text("# Demo\n", encoding="utf-8")
            res = read_repo_file_line_range(tmp, "../../etc/passwd")
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["error_code"], "PATH_TRAVERSAL_BLOCKED")
            self.assertTrue(res["recovery_instructions"])

    def test_self_repo_hits_full_95_point_evidence_baseline(self) -> None:
        repo_root = str(Path(__file__).resolve().parent.parent)
        idx = clone_and_index_public_github_repo(repo_root)
        self.assertEqual(idx["status"], "ok")
        self.assertTrue(idx["is_root_project"])

        scan = scan_repo_for_agentops_evidence(idx["local_checkout_path"])
        self.assertEqual(scan["status"], "ok")
        self.assertEqual(len(scan["signals"]), 19)
        self.assertEqual(scan["estimated_baseline_score"], 95)

        sec = audit_repo_for_hardcoded_secrets_and_risks(idx["local_checkout_path"])
        self.assertEqual(sec["critical_secret_count"], 0)
        self.assertEqual(sec["raw_print_calls"], 0)

    def test_multilingual_video_analyzer_detects_spanish_and_advises_english(self) -> None:
        res = analyze_multilingual_walkthrough_video(
            video_url="https://youtu.be/example12345",
            transcript_excerpt=(
                "Presentamos el problema, la solución, la arquitectura en código ADK y la demo."
            ),
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["detected_language"], "es")
        self.assertTrue(res["covers_problem_and_solution"])
        self.assertTrue(res["covers_architecture_and_code"])
        self.assertTrue(res["covers_live_demo"])
        self.assertIn("English", res["english_submission_advisory"])


if __name__ == "__main__":
    unittest.main()
