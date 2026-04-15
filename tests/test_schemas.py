import pytest

from app.schemas import (
    DimensionScores,
    DocumentType,
    Issue,
    NextAction,
    RunMetadata,
    Scorecard,
    Severity,
    SourcePass,
    Validation,
    ValidationDecision,
)


class TestDocumentType:
    def test_all_types_defined(self):
        expected = [
            "feature_spec",
            "implementation_plan",
            "architecture_change",
            "refactor_plan",
            "migration_plan",
            "incident_action_plan",
            "custom",
        ]
        for t in expected:
            assert DocumentType(t) is not None

    def test_custom_type(self):
        assert DocumentType.CUSTOM.value == "custom"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            DocumentType("nonexistent")


class TestSeverity:
    def test_severity_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"


class TestIssue:
    def test_valid_issue(self):
        issue = Issue(
            id="A-001",
            title="Test issue",
            severity=Severity.HIGH,
            category="contradiction",
            rationale="Test rationale",
            evidence_quote="Test quote",
            affected_section="Section 1",
            proposed_fix="Fix it",
            source_pass=SourcePass.CRITIC_A,
        )
        assert issue.id == "A-001"
        assert issue.severity == Severity.HIGH

    def test_issue_serialization(self):
        issue = Issue(
            id="A-001",
            title="Test",
            severity=Severity.LOW,
            category="test",
            rationale="r",
            evidence_quote="q",
            affected_section="s",
            proposed_fix="f",
            source_pass=SourcePass.BOTH,
        )
        d = issue.model_dump()
        assert d["id"] == "A-001"
        assert d["severity"] == "low"

    def test_issue_deserialization(self):
        data = {
            "id": "B-001",
            "title": "Test",
            "severity": "critical",
            "category": "test",
            "rationale": "r",
            "evidence_quote": "q",
            "affected_section": "s",
            "proposed_fix": "f",
            "source_pass": "critic_b",
        }
        issue = Issue(**data)
        assert issue.severity == Severity.CRITICAL
        assert issue.source_pass == SourcePass.CRITIC_B


class TestValidation:
    def test_valid_validation(self):
        v = Validation(
            issue_id="A-001",
            decision=ValidationDecision.VALID,
            confidence=0.9,
            reason="Test",
            should_auto_apply=True,
        )
        assert v.decision == ValidationDecision.VALID
        assert v.should_auto_apply is True

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            Validation(
                issue_id="A-001",
                decision=ValidationDecision.VALID,
                confidence=1.5,
                reason="Test",
            )

    def test_confidence_negative(self):
        with pytest.raises(Exception):
            Validation(
                issue_id="A-001",
                decision=ValidationDecision.VALID,
                confidence=-0.1,
                reason="Test",
            )


class TestDimensionScores:
    def test_valid_scores(self):
        scores = DimensionScores(
            correctness=8.0,
            completeness=7.5,
            implementability=9.0,
            consistency=6.5,
            edge_case_coverage=7.0,
            testability=8.0,
            risk_awareness=7.5,
            clarity=9.0,
        )
        assert scores.correctness == 8.0
        assert scores.model_dump()["correctness"] == 8.0

    def test_score_bounds(self):
        with pytest.raises(Exception):
            DimensionScores(
                correctness=11.0,
                completeness=7.0,
                implementability=7.0,
                consistency=7.0,
                edge_case_coverage=7.0,
                testability=7.0,
                risk_awareness=7.0,
                clarity=7.0,
            )

    def test_score_negative(self):
        with pytest.raises(Exception):
            DimensionScores(
                correctness=-1.0,
                completeness=7.0,
                implementability=7.0,
                consistency=7.0,
                edge_case_coverage=7.0,
                testability=7.0,
                risk_awareness=7.0,
                clarity=7.0,
            )


class TestScorecard:
    def test_full_scorecard(self):
        scores = DimensionScores(
            correctness=9.0,
            completeness=8.0,
            implementability=8.5,
            consistency=7.5,
            edge_case_coverage=7.0,
            testability=8.0,
            risk_awareness=7.5,
            clarity=9.0,
        )
        sc = Scorecard(
            dimension_scores=scores,
            overall_score=8.2,
            blocking_reasons=[],
            unresolved_critical_issues_count=0,
            recommended_next_action=NextAction.IMPLEMENT,
            passed=True,
        )
        assert sc.passed is True
        assert sc.overall_score == 8.2
        assert sc.recommended_next_action == NextAction.IMPLEMENT

    def test_failed_scorecard(self):
        scores = DimensionScores(
            correctness=5.0,
            completeness=5.0,
            implementability=5.0,
            consistency=5.0,
            edge_case_coverage=5.0,
            testability=5.0,
            risk_awareness=5.0,
            clarity=5.0,
        )
        sc = Scorecard(
            dimension_scores=scores,
            overall_score=5.0,
            blocking_reasons=["Score too low"],
            unresolved_critical_issues_count=2,
            recommended_next_action=NextAction.HUMAN_REVIEW,
            passed=False,
        )
        assert sc.passed is False
        assert len(sc.blocking_reasons) == 1


class TestRunMetadata:
    def test_metadata_creation(self):
        m = RunMetadata(
            timestamp="2026-01-01T00:00:00Z",
            document_type=DocumentType.FEATURE_SPEC,
            execution_status="completed",
        )
        assert m.document_type == DocumentType.FEATURE_SPEC
        assert m.warnings == []
