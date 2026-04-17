from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    FEATURE_SPEC = "feature_spec"
    IMPLEMENTATION_PLAN = "implementation_plan"
    ARCHITECTURE_CHANGE = "architecture_change"
    REFACTOR_PLAN = "refactor_plan"
    MIGRATION_PLAN = "migration_plan"
    INCIDENT_ACTION_PLAN = "incident_action_plan"
    CUSTOM = "custom"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CriticACategory(str, Enum):
    CONTRADICTION = "contradiction"
    INCORRECT_ASSUMPTION = "incorrect_assumption"
    MISSING_REQUIREMENT = "missing_requirement"
    INCOMPLETE_LOGIC = "incomplete_logic"
    SEQUENCING_GAP = "sequencing_gap"
    DEPENDENCY_GAP = "dependency_gap"


class CriticBCategory(str, Enum):
    IMPLEMENTABILITY = "implementability"
    TESTABILITY = "testability"
    ROLLOUT_SAFETY = "rollout_safety"
    OBSERVABILITY = "observability"
    EDGE_CASE = "edge_case"
    MIGRATION_RISK = "migration_risk"
    OPERATIONAL_RISK = "operational_risk"
    MAINTAINABILITY = "maintainability"


class SourcePass(str, Enum):
    CRITIC_A = "critic_a"
    CRITIC_B = "critic_b"
    CROSS_REF = "cross_ref"
    BOTH = "both"


class ValidationDecision(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


class NextAction(str, Enum):
    IMPLEMENT = "implement"
    REVISE_AGAIN = "revise_again"
    HUMAN_REVIEW = "human_review"


class Issue(BaseModel):
    id: str
    title: str
    severity: Severity
    category: str
    rationale: str
    evidence_quote: str
    affected_section: str
    proposed_fix: str
    source_pass: SourcePass
    consensus_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    run_origins: list[str] = Field(default_factory=list)


class Validation(BaseModel):
    issue_id: str
    decision: ValidationDecision
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    should_auto_apply: bool = False


class DimensionScores(BaseModel):
    correctness: float = Field(default=0.0, ge=0.0, le=10.0)
    completeness: float = Field(default=0.0, ge=0.0, le=10.0)
    implementability: float = Field(default=0.0, ge=0.0, le=10.0)
    consistency: float = Field(default=0.0, ge=0.0, le=10.0)
    edge_case_coverage: float = Field(default=0.0, ge=0.0, le=10.0)
    testability: float = Field(default=0.0, ge=0.0, le=10.0)
    risk_awareness: float = Field(default=0.0, ge=0.0, le=10.0)
    clarity: float = Field(default=0.0, ge=0.0, le=10.0)


class Scorecard(BaseModel):
    dimension_scores: DimensionScores
    overall_score: float = Field(ge=0.0, le=10.0)
    blocking_reasons: list[str] = Field(default_factory=list)
    unresolved_critical_issues_count: int = 0
    recommended_next_action: NextAction = NextAction.HUMAN_REVIEW
    passed: bool = False
    key_strengths: list[str] = Field(default_factory=list)
    remaining_concerns: list[str] = Field(default_factory=list)
    overall_assessment: str = ""
    confidence_in_scoring: float = Field(default=0.0, ge=0.0, le=1.0)


class RunMetadata(BaseModel):
    timestamp: str
    document_type: DocumentType
    model_aliases_used: dict[str, str] = Field(default_factory=dict)
    actual_models_used: dict[str, Optional[str]] = Field(default_factory=dict)
    proxy_base_url: str = ""
    execution_status: str = "pending"
    token_usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class RunArtifacts(BaseModel):
    run_id: str
    output_dir: str
    original_content: str = ""
    revised_content: str = ""
    issues: list[Issue] = Field(default_factory=list)
    validations: list[Validation] = Field(default_factory=list)
    scorecard: Optional[Scorecard] = None
    promptfoo_raw: Optional[dict] = None
    metadata: Optional[RunMetadata] = None
