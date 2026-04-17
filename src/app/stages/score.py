from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import structlog

from app.config import ThresholdConfig
from app.integrations.litellm_client import LiteLLMClient
from app.integrations.promptfoo_runner import PromptfooRunner
from app.schemas import (
    DimensionScores,
    Issue,
    NextAction,
    Scorecard,
    Validation,
    ValidationDecision,
)
from app.utils.text import extract_json_object

logger = structlog.get_logger("score")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCORER_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "scorer.md")


def _load_prompt() -> str:
    p = Path(SCORER_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {SCORER_PROMPT_FILE}")


def _clean_jinja(text: str) -> str:
    return re.sub(r"\{%.*?%\}", "", text).strip()


def _count_unresolved_critical(issues: list[Issue], validations: list[Validation]) -> int:
    valid_issue_ids = set()
    for v in validations:
        if v.decision == ValidationDecision.VALID:
            valid_issue_ids.add(v.issue_id)

    unresolved = 0
    for issue in issues:
        if issue.id in valid_issue_ids and issue.severity.value == "critical":
            if not any(v.should_auto_apply and v.issue_id == issue.id for v in validations):
                unresolved += 1
        elif issue.id in valid_issue_ids and issue.severity.value in ("critical", "high"):
            if not any(v.should_auto_apply and v.issue_id == issue.id for v in validations):
                unresolved += 1

    return unresolved


def _compute_gate_logic(
    dimension_scores: DimensionScores,
    threshold_config: ThresholdConfig,
    unresolved_critical: int,
) -> dict:
    scores_dict = dimension_scores.model_dump()
    weights = threshold_config.dimension_weights

    weighted_sum = 0.0
    weight_total = 0.0
    for dim, score in scores_dict.items():
        w = weights.get(dim, 1.0)
        weighted_sum += score * w
        weight_total += w

    overall_score = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0.0

    blocking_reasons: list[str] = []
    if overall_score < threshold_config.overall_threshold:
        blocking_reasons.append(f"Overall score {overall_score} below threshold {threshold_config.overall_threshold}")

    for dim in threshold_config.critical_dimensions:
        dim_score = scores_dict.get(dim, 0)
        if dim_score < threshold_config.critical_dimension_threshold:
            blocking_reasons.append(
                f"Critical dimension '{dim}' score {dim_score} below "
                f"threshold {threshold_config.critical_dimension_threshold}"
            )

    if unresolved_critical > 0:
        blocking_reasons.append(f"{unresolved_critical} unresolved critical/high issues remain")

    passed = len(blocking_reasons) == 0

    if passed:
        action = NextAction.IMPLEMENT
    elif overall_score >= threshold_config.overall_threshold - 2.0:
        action = NextAction.REVISE_AGAIN
    else:
        action = NextAction.HUMAN_REVIEW

    return {
        "overall_score": overall_score,
        "passed": passed,
        "blocking_reasons": blocking_reasons,
        "unresolved_critical_issues_count": unresolved_critical,
        "recommended_next_action": action,
    }


def score_document(
    client: LiteLLMClient,
    promptfoo_runner: Optional[PromptfooRunner],
    revised_content: str,
    document_type: str,
    original_content: str,
    issues: list[Issue],
    validations: list[Validation],
    threshold_config: ThresholdConfig,
    proxy_base_url: str = "",
    proxy_api_key: str = "",
) -> tuple[Scorecard, Optional[dict]]:
    unresolved_critical = _count_unresolved_critical(issues, validations)

    template = _load_prompt()
    prompt_text = (
        _clean_jinja(template)
        .replace("{{document_type}}", document_type)
        .replace("{{document_content}}", revised_content)
        .replace("{{original_content}}", original_content)
    )

    messages = [
        {"role": "system", "content": "You are a document quality scorer. Return ONLY valid JSON."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("scorer")
    logger.info("scoring_start", model=model)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
        stage="score",
    )

    content = response.get("content", "")
    parsed = extract_json_object(content)

    dimension_scores = DimensionScores()
    key_strengths: list[str] = []
    remaining_concerns: list[str] = []
    overall_assessment = ""
    confidence = 0.0

    if parsed:
        ds = parsed.get("dimension_scores", parsed)
        if isinstance(ds, dict):
            try:
                dimension_scores = DimensionScores(**{k: max(0.0, min(10.0, float(v))) for k, v in ds.items()})
            except Exception as e:
                logger.warning("dimension_score_parse_error", error=str(e))

        key_strengths = parsed.get("key_strengths", [])
        remaining_concerns = parsed.get("remaining_concerns", [])
        overall_assessment = parsed.get("overall_assessment", "")
        confidence = float(parsed.get("confidence_in_scoring", 0.0))

    gate = _compute_gate_logic(dimension_scores, threshold_config, unresolved_critical)

    promptfoo_result = None
    if promptfoo_runner:
        try:
            promptfoo_result = promptfoo_runner.run_evaluation(
                document_content=revised_content,
                document_type=document_type,
                proxy_base_url=proxy_base_url,
                proxy_api_key=proxy_api_key,
            )
        except Exception as e:
            logger.warning("promptfoo_eval_failed", error=str(e))

    scorecard = Scorecard(
        dimension_scores=dimension_scores,
        overall_score=gate["overall_score"],
        blocking_reasons=gate["blocking_reasons"],
        unresolved_critical_issues_count=gate["unresolved_critical_issues_count"],
        recommended_next_action=gate["recommended_next_action"],
        passed=gate["passed"],
        key_strengths=key_strengths,
        remaining_concerns=remaining_concerns,
        overall_assessment=overall_assessment,
        confidence_in_scoring=confidence,
    )

    logger.info(
        "scoring_done",
        overall_score=scorecard.overall_score,
        passed=scorecard.passed,
        action=scorecard.recommended_next_action.value,
    )
    return scorecard, promptfoo_result
