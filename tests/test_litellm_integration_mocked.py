import json
from unittest.mock import MagicMock, patch

import pytest

from app.config import load_app_config
from app.integrations.litellm_client import LiteLLMClient
from app.schemas import (
    DimensionScores,
    Issue,
    NextAction,
    Severity,
    SourcePass,
    Validation,
    ValidationDecision,
)


def _make_mock_response(content: str, model: str = "mock-model", tokens: int = 100):
    return {
        "content": content,
        "model": model,
        "usage": {"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": tokens},
        "raw": {
            "choices": [{"message": {"content": content}}],
            "model": model,
            "usage": {"total_tokens": tokens},
        },
    }


def _make_critic_response(issues: list[dict]) -> str:
    return json.dumps(issues)


@pytest.fixture
def app_config():
    return load_app_config(config_dir="config")


@pytest.fixture
def mock_client(app_config):
    client = LiteLLMClient(app_config)
    client.chat_completion = MagicMock(return_value=_make_mock_response("[]"))
    return client


class TestLiteLLMClientMocked:
    def test_resolve_model(self, mock_client):
        model = mock_client.resolve_model("critic_a")
        assert model == "cheap_large_context"

    def test_resolve_model_validator(self, mock_client):
        model = mock_client.resolve_model("validator")
        assert model == "strong_judge"

    def test_resolve_model_unknown(self, mock_client):
        model = mock_client.resolve_model("unknown_stage")
        assert model == "unknown_stage"

    @patch("httpx.Client")
    def test_chat_completion_call(self, mock_httpx, app_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "test response"}}],
            "model": "zai/glm-4.5",
            "usage": {"total_tokens": 50},
        }
        mock_response.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_httpx.return_value.__enter__ = MagicMock(return_value=mock_http_client)
        mock_httpx.return_value.__exit__ = MagicMock(return_value=False)

        client = LiteLLMClient(app_config)
        result = client.chat_completion(
            model="cheap_large_context",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert result["content"] == "test response"

    def test_health_check_mocked(self, mock_client):
        mock_client.health_check = MagicMock(return_value={"status": "ok"})
        result = mock_client.health_check()
        assert result["status"] == "ok"


class TestCriticStage:
    def test_critic_a_returns_issues(self, mock_client):
        issues_json = [
            {
                "id": "A-001",
                "title": "Missing error handling",
                "severity": "high",
                "category": "incomplete_logic",
                "rationale": "No error handling for API failures",
                "evidence_quote": "Call the payment API",
                "affected_section": "Payment Processing",
                "proposed_fix": "Add try-catch and retry logic",
                "source_pass": "critic_a",
            }
        ]
        mock_client.chat_completion = MagicMock(return_value=_make_mock_response(json.dumps(issues_json)))

        from app.stages.critic import run_critic_a

        issues = run_critic_a(mock_client, "Test document content", "feature_spec")
        assert len(issues) == 1
        assert issues[0].id == "A-001"
        assert issues[0].source_pass == SourcePass.CRITIC_A

    def test_critic_b_returns_issues(self, mock_client):
        issues_json = [
            {
                "id": "B-001",
                "title": "No rollback plan",
                "severity": "critical",
                "category": "rollout_safety",
                "rationale": "No rollback described",
                "evidence_quote": "Deploy the changes",
                "affected_section": "Deployment",
                "proposed_fix": "Add rollback procedures",
                "source_pass": "critic_b",
            }
        ]
        mock_client.chat_completion = MagicMock(return_value=_make_mock_response(json.dumps(issues_json)))

        from app.stages.critic import run_critic_b

        issues = run_critic_b(mock_client, "Test document content", "implementation_plan")
        assert len(issues) == 1
        assert issues[0].source_pass == SourcePass.CRITIC_B

    def test_empty_response(self, mock_client):
        mock_client.chat_completion = MagicMock(return_value=_make_mock_response("No issues found. []"))

        from app.stages.critic import run_critic_a

        issues = run_critic_a(mock_client, "Perfect document", "feature_spec")
        assert len(issues) == 0


class TestDeduplication:
    def test_merge_overlapping(self):
        from app.stages.dedupe import deduplicate_issues

        issues_a = [
            Issue(
                id="A-001",
                title="Missing error handling",
                severity=Severity.HIGH,
                category="incomplete_logic",
                rationale="No error handling",
                evidence_quote="Call API",
                affected_section="Section 1",
                proposed_fix="Add try-catch",
                source_pass=SourcePass.CRITIC_A,
            )
        ]
        issues_b = [
            Issue(
                id="B-001",
                title="Missing error handling for API",
                severity=Severity.CRITICAL,
                category="testability",
                rationale="No error handling for API calls",
                evidence_quote="Call API",
                affected_section="Section 1",
                proposed_fix="Add error handling",
                source_pass=SourcePass.CRITIC_B,
            )
        ]

        merged = deduplicate_issues(issues_a, issues_b)
        assert len(merged) == 1
        assert merged[0].source_pass == SourcePass.BOTH
        assert merged[0].severity == Severity.CRITICAL

    def test_no_overlap(self):
        from app.stages.dedupe import deduplicate_issues

        issues_a = [
            Issue(
                id="A-001",
                title="Security vulnerability",
                severity=Severity.CRITICAL,
                category="security",
                rationale="SQL injection",
                evidence_quote="query",
                affected_section="Section 2",
                proposed_fix="Use parameters",
                source_pass=SourcePass.CRITIC_A,
            )
        ]
        issues_b = [
            Issue(
                id="B-001",
                title="Missing documentation",
                severity=Severity.LOW,
                category="clarity",
                rationale="No API docs",
                evidence_quote="endpoint",
                affected_section="Section 3",
                proposed_fix="Add docs",
                source_pass=SourcePass.CRITIC_B,
            )
        ]

        merged = deduplicate_issues(issues_a, issues_b)
        assert len(merged) == 2


class TestValidation:
    def test_validate_issues(self, mock_client):
        validations_json = [
            {
                "issue_id": "A-001",
                "decision": "valid",
                "confidence": 0.9,
                "reason": "Confirmed issue",
                "should_auto_apply": True,
            }
        ]
        mock_client.chat_completion = MagicMock(return_value=_make_mock_response(json.dumps(validations_json)))

        from app.stages.validate import validate_issues

        issues = [
            Issue(
                id="A-001",
                title="Test",
                severity=Severity.HIGH,
                category="test",
                rationale="r",
                evidence_quote="q",
                affected_section="s",
                proposed_fix="f",
                source_pass=SourcePass.CRITIC_A,
            )
        ]
        validations = validate_issues(mock_client, issues, "Document content")
        assert len(validations) == 1
        assert validations[0].decision == ValidationDecision.VALID
        assert validations[0].should_auto_apply is True

    def test_invalid_not_auto_applied(self, mock_client):
        validations_json = [
            {
                "issue_id": "A-001",
                "decision": "invalid",
                "confidence": 0.9,
                "reason": "False positive",
                "should_auto_apply": False,
            }
        ]
        mock_client.chat_completion = MagicMock(return_value=_make_mock_response(json.dumps(validations_json)))

        from app.stages.validate import validate_issues

        issues = [
            Issue(
                id="A-001",
                title="Test",
                severity=Severity.HIGH,
                category="test",
                rationale="r",
                evidence_quote="q",
                affected_section="s",
                proposed_fix="f",
                source_pass=SourcePass.CRITIC_A,
            )
        ]
        validations = validate_issues(mock_client, issues, "Document content")
        assert validations[0].should_auto_apply is False


class TestRevision:
    def test_get_valid_issues(self):
        from app.stages.revise import get_valid_issues

        issues = [
            Issue(
                id="A-001",
                title="Valid",
                severity=Severity.HIGH,
                category="test",
                rationale="r",
                evidence_quote="q",
                affected_section="s",
                proposed_fix="fix1",
                source_pass=SourcePass.CRITIC_A,
            ),
            Issue(
                id="A-002",
                title="Invalid",
                severity=Severity.LOW,
                category="test",
                rationale="r",
                evidence_quote="q",
                affected_section="s",
                proposed_fix="fix2",
                source_pass=SourcePass.CRITIC_A,
            ),
        ]
        validations = [
            Validation(
                issue_id="A-001", decision=ValidationDecision.VALID, confidence=0.9, reason="ok", should_auto_apply=True
            ),
            Validation(
                issue_id="A-002",
                decision=ValidationDecision.INVALID,
                confidence=0.8,
                reason="bad",
                should_auto_apply=False,
            ),
        ]
        valid = get_valid_issues(issues, validations)
        assert len(valid) == 1
        assert valid[0].id == "A-001"

    def test_no_valid_issues_returns_original(self, mock_client):
        from app.stages.revise import revise_document

        result = revise_document(mock_client, "Original content", "feature_spec", [])
        assert result == "Original content"


class TestScoring:
    def test_gate_logic_pass(self):
        from app.stages.score import _compute_gate_logic

        scores = DimensionScores(
            correctness=9.0,
            completeness=8.5,
            implementability=8.0,
            consistency=8.0,
            edge_case_coverage=8.0,
            testability=8.0,
            risk_awareness=8.0,
            clarity=9.0,
        )
        from app.config import load_threshold_config

        threshold = load_threshold_config(config_dir="config", doc_type="feature_spec")
        result = _compute_gate_logic(scores, threshold, 0)
        assert result["passed"] is True
        assert result["recommended_next_action"] == NextAction.IMPLEMENT

    def test_gate_logic_fail(self):
        from app.stages.score import _compute_gate_logic

        scores = DimensionScores(
            correctness=4.0,
            completeness=4.0,
            implementability=4.0,
            consistency=5.0,
            edge_case_coverage=5.0,
            testability=5.0,
            risk_awareness=5.0,
            clarity=5.0,
        )
        from app.config import load_threshold_config

        threshold = load_threshold_config(config_dir="config", doc_type="feature_spec")
        result = _compute_gate_logic(scores, threshold, 0)
        assert result["passed"] is False

    def test_unresolved_critical_blocks(self):
        from app.stages.score import _compute_gate_logic

        scores = DimensionScores(
            correctness=9.0,
            completeness=9.0,
            implementability=9.0,
            consistency=9.0,
            edge_case_coverage=9.0,
            testability=9.0,
            risk_awareness=9.0,
            clarity=9.0,
        )
        from app.config import load_threshold_config

        threshold = load_threshold_config(config_dir="config")
        result = _compute_gate_logic(scores, threshold, 3)
        assert result["passed"] is False
        assert result["unresolved_critical_issues_count"] == 3
