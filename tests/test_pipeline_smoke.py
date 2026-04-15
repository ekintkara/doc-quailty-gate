import json
from unittest.mock import MagicMock

import pytest

from app.config import load_app_config
from app.integrations.litellm_client import LiteLLMClient
from app.schemas import (
    DimensionScores,
    DocumentType,
    Issue,
    RunArtifacts,
    RunMetadata,
    Scorecard,
    Severity,
    SourcePass,
    Validation,
    ValidationDecision,
)


def _mock_chat_completion(content: str):
    return {
        "content": content,
        "model": "mock/model",
        "usage": {"total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50},
        "raw": {},
    }


@pytest.fixture
def mock_orchestrator_client():
    config = load_app_config(config_dir="config")
    client = LiteLLMClient(config)

    critic_a_issues = json.dumps(
        [
            {
                "id": "A-001",
                "title": "Missing error handling",
                "severity": "high",
                "category": "incomplete_logic",
                "rationale": "No error handling",
                "evidence_quote": "Call API",
                "affected_section": "API Calls",
                "proposed_fix": "Add try-catch",
                "source_pass": "critic_a",
            }
        ]
    )
    critic_b_issues = json.dumps(
        [
            {
                "id": "B-001",
                "title": "No monitoring",
                "severity": "medium",
                "category": "observability",
                "rationale": "No metrics defined",
                "evidence_quote": "Process data",
                "affected_section": "Processing",
                "proposed_fix": "Add metrics",
                "source_pass": "critic_b",
            }
        ]
    )
    validations = json.dumps(
        [
            {
                "issue_id": "A-001",
                "decision": "valid",
                "confidence": 0.9,
                "reason": "Confirmed",
                "should_auto_apply": True,
            },
            {
                "issue_id": "B-001",
                "decision": "valid",
                "confidence": 0.85,
                "reason": "Confirmed",
                "should_auto_apply": True,
            },
        ]
    )
    scoring = json.dumps(
        {
            "dimension_scores": {
                "correctness": 8.0,
                "completeness": 7.5,
                "implementability": 8.0,
                "consistency": 7.0,
                "edge_case_coverage": 7.0,
                "testability": 7.5,
                "risk_awareness": 7.0,
                "clarity": 8.0,
            },
            "overall_assessment": "Good quality document",
            "key_strengths": ["Clear structure"],
            "remaining_concerns": ["Some edge cases missing"],
            "confidence_in_scoring": 0.85,
        }
    )

    call_count = [0]
    responses = [critic_a_issues, critic_b_issues, validations, "Revised document content", scoring]

    def mock_completion(**kwargs):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return _mock_chat_completion(responses[idx])

    client.chat_completion = MagicMock(side_effect=mock_completion)
    return client


class TestPipelineSmoke:
    def test_full_pipeline_with_fixtures(self, mock_orchestrator_client, tmp_path):
        doc_content = (
            "# Feature Spec: Test Feature\n\n"
            "## Overview\nA test feature.\n\n"
            "## Requirements\n- Do thing A\n- Do thing B\n"
        )
        doc_file = tmp_path / "test_doc.md"
        doc_file.write_text(doc_content, encoding="utf-8")

        from app.config import load_threshold_config
        from app.stages.critic import run_critic_a, run_critic_b
        from app.stages.dedupe import deduplicate_issues
        from app.stages.revise import get_valid_issues, revise_document
        from app.stages.score import score_document
        from app.stages.validate import validate_issues

        content, doc_type = doc_content, "feature_spec"

        issues_a = run_critic_a(mock_orchestrator_client, content, doc_type)
        assert len(issues_a) >= 1

        issues_b = run_critic_b(mock_orchestrator_client, content, doc_type)
        assert len(issues_b) >= 1

        merged = deduplicate_issues(issues_a, issues_b)
        assert len(merged) >= 1

        validations = validate_issues(mock_orchestrator_client, merged, content)
        assert len(validations) >= 1

        valid_issues = get_valid_issues(merged, validations)
        assert len(valid_issues) >= 1

        revised = revise_document(mock_orchestrator_client, content, doc_type, valid_issues)
        assert len(revised) > 0

        threshold_config = load_threshold_config(config_dir="config", doc_type=doc_type)
        scorecard, promptfoo_raw = score_document(
            client=mock_orchestrator_client,
            promptfoo_runner=None,
            revised_content=revised,
            document_type=doc_type,
            original_content=content,
            issues=merged,
            validations=validations,
            threshold_config=threshold_config,
        )

        assert scorecard is not None
        assert scorecard.overall_score > 0
        assert scorecard.dimension_scores.correctness > 0
        assert isinstance(scorecard.passed, bool)
        assert scorecard.recommended_next_action is not None

    def test_report_generation(self, tmp_path):
        from app.config import load_threshold_config
        from app.stages.report import generate_reports

        scores = DimensionScores(
            correctness=8.0,
            completeness=7.5,
            implementability=8.0,
            consistency=7.0,
            edge_case_coverage=7.0,
            testability=7.5,
            risk_awareness=7.0,
            clarity=8.0,
        )
        artifacts = RunArtifacts(
            run_id="test-run-001",
            output_dir=str(tmp_path),
            original_content="# Original\nContent",
            revised_content="# Revised\nContent",
            issues=[
                Issue(
                    id="A-001",
                    title="Test issue",
                    severity=Severity.HIGH,
                    category="test",
                    rationale="r",
                    evidence_quote="q",
                    affected_section="s",
                    proposed_fix="f",
                    source_pass=SourcePass.CRITIC_A,
                ),
            ],
            validations=[
                Validation(
                    issue_id="A-001",
                    decision=ValidationDecision.VALID,
                    confidence=0.9,
                    reason="ok",
                    should_auto_apply=True,
                ),
            ],
            scorecard=Scorecard(
                dimension_scores=scores,
                overall_score=7.6,
                passed=False,
                blocking_reasons=["Score below threshold"],
                unresolved_critical_issues_count=0,
                recommended_next_action="revise_again",
            ),
            metadata=RunMetadata(
                timestamp="2026-01-01T00:00:00Z",
                document_type=DocumentType.FEATURE_SPEC,
                execution_status="completed",
                model_aliases_used={"critic_a": "cheap_large_context"},
            ),
        )

        threshold = load_threshold_config(config_dir="config", doc_type="feature_spec")
        md_report, html_report = generate_reports(artifacts, threshold)

        assert "Quality Gate Report" in md_report
        assert "FAIL" in md_report
        assert "revise_again" in md_report
        assert "<!DOCTYPE html>" in html_report
        assert "test-run-001" in html_report

    def test_artifacts_written(self, tmp_path):
        from app.utils.files import write_json, write_text

        write_text(tmp_path / "original.md", "# Original")
        write_text(tmp_path / "revised.md", "# Revised")
        write_json(tmp_path / "issues.json", [{"id": "A-001", "title": "Test"}])
        write_json(tmp_path / "scorecard.json", {"overall_score": 8.0})
        write_json(tmp_path / "metadata.json", {"timestamp": "2026-01-01"})

        assert (tmp_path / "original.md").exists()
        assert (tmp_path / "revised.md").exists()
        assert (tmp_path / "issues.json").exists()
        assert (tmp_path / "scorecard.json").exists()
        assert (tmp_path / "metadata.json").exists()
