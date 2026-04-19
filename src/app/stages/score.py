from __future__ import annotations

import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import structlog

from app.config import ThresholdConfig
from app.integrations.litellm_client import LiteLLMClient
from app.integrations.promptfoo_runner import PromptfooRunner
from app.schemas import (
    DimensionScores,
    Issue,
    MetaJudgeResult,
    NextAction,
    Scorecard,
    Validation,
    ValidationDecision,
)
from app.utils.text import extract_json_object

logger = structlog.get_logger("score")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCORER_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "scorer.md")

DEFAULT_SCORER_RUNS = 3
DEFAULT_SCORER_MAX_WORKERS = 3


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

    passed = overall_score >= threshold_config.overall_threshold

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


def score_single(
    client: LiteLLMClient,
    revised_content: str,
    document_type: str,
    original_content: str,
    run_index: int = 0,
) -> dict:
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
    logger.info("scorer_single_run", model=model, run_index=run_index)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
        stage=f"score_run{run_index}",
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
                logger.warning("dimension_score_parse_error", run_index=run_index, error=str(e))

        key_strengths = parsed.get("key_strengths", [])
        remaining_concerns = parsed.get("remaining_concerns", [])
        overall_assessment = parsed.get("overall_assessment", "")
        confidence = float(parsed.get("confidence_in_scoring", 0.0))

    return {
        "dimension_scores": dimension_scores,
        "key_strengths": key_strengths,
        "remaining_concerns": remaining_concerns,
        "overall_assessment": overall_assessment,
        "confidence": confidence,
        "run_index": run_index,
    }


def run_scorer_multi(
    client: LiteLLMClient,
    revised_content: str,
    document_type: str,
    original_content: str,
    n_runs: int = DEFAULT_SCORER_RUNS,
    max_workers: int = DEFAULT_SCORER_MAX_WORKERS,
) -> list[dict]:
    logger.info("scorer_multi_start", n_runs=n_runs, max_workers=max_workers)

    runs: list[Optional[dict]] = [None] * n_runs

    def _single_run(run_index: int) -> tuple[int, dict]:
        result = score_single(
            client=client,
            revised_content=revised_content,
            document_type=document_type,
            original_content=original_content,
            run_index=run_index,
        )
        return run_index, result

    effective_workers = min(max_workers, n_runs)
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {executor.submit(_single_run, i): i for i in range(n_runs)}
        for future in as_completed(futures):
            run_index, result = future.result()
            runs[run_index] = result

    runs = [r for r in runs if r is not None]
    logger.info("scorer_multi_done", successful_runs=len(runs))
    return runs


def aggregate_scores(runs: list[dict]) -> tuple[DimensionScores, float, float, list[str], list[str], str]:
    if not runs:
        return DimensionScores(), 0.0, 1.0, [], [], ""

    dim_names = [
        "correctness",
        "completeness",
        "implementability",
        "consistency",
        "edge_case_coverage",
        "testability",
        "risk_awareness",
        "clarity",
    ]

    median_scores = {}
    per_dim_variance = {}

    for dim in dim_names:
        values = []
        for run in runs:
            ds = run.get("dimension_scores", DimensionScores())
            values.append(getattr(ds, dim, 0.0))
        median_scores[dim] = round(statistics.median(values), 2)
        if len(values) > 1:
            per_dim_variance[dim] = round(statistics.variance(values), 4)
        else:
            per_dim_variance[dim] = 0.0

    avg_variance = statistics.mean(per_dim_variance.values()) if per_dim_variance else 0.0
    max_possible_variance = 25.0
    confidence = max(0.0, min(1.0, 1.0 - (avg_variance / max_possible_variance)))

    all_strengths = []
    all_concerns = []
    all_assessments = []
    all_confidences = []

    for run in runs:
        all_strengths.extend(run.get("key_strengths", []))
        all_concerns.extend(run.get("remaining_concerns", []))
        if run.get("overall_assessment"):
            all_assessments.append(run["overall_assessment"])
        all_confidences.append(run.get("confidence", 0.0))

    strength_counts = {}
    for s in all_strengths:
        key = s.strip().lower()
        strength_counts[key] = strength_counts.get(key, 0) + 1
    merged_strengths = sorted(strength_counts.keys(), key=lambda k: strength_counts[k], reverse=True)[:5]

    concern_counts = {}
    for c in all_concerns:
        key = c.strip().lower()
        concern_counts[key] = concern_counts.get(key, 0) + 1
    merged_concerns = sorted(concern_counts.keys(), key=lambda k: concern_counts[k], reverse=True)[:5]

    merged_assessment = all_assessments[0] if all_assessments else ""

    dimension_scores = DimensionScores(**median_scores)

    return dimension_scores, avg_variance, confidence, merged_strengths, merged_concerns, merged_assessment


def merge_scorer_and_promptfoo(
    llm_scores: DimensionScores,
    promptfoo_scores: Optional[DimensionScores],
    llm_confidence: float,
) -> tuple[DimensionScores, float, Optional[str]]:
    if promptfoo_scores is None:
        return llm_scores, llm_confidence, None

    llm_weight = 0.6
    pf_weight = 0.4

    dim_names = [
        "correctness",
        "completeness",
        "implementability",
        "consistency",
        "edge_case_coverage",
        "testability",
        "risk_awareness",
        "clarity",
    ]

    merged = {}
    llm_pass_dims = 0
    pf_pass_dims = 0
    agree_dims = 0
    threshold = 6.0

    for dim in dim_names:
        llm_val = getattr(llm_scores, dim, 0.0)
        pf_val = getattr(promptfoo_scores, dim, 0.0)
        merged[dim] = round(llm_val * llm_weight + pf_val * pf_weight, 2)

        if llm_val >= threshold:
            llm_pass_dims += 1
        if pf_val >= threshold:
            pf_pass_dims += 1
        if (llm_val >= threshold) == (pf_val >= threshold):
            agree_dims += 1

    agreement_ratio = agree_dims / len(dim_names) if dim_names else 0.0

    if agreement_ratio >= 0.875:
        agreement_label = "agree"
    elif agreement_ratio >= 0.625:
        agreement_label = "partial"
    else:
        agreement_label = "disagree"

    if agreement_label == "disagree":
        confidence_penalty = 0.15
    elif agreement_label == "partial":
        confidence_penalty = 0.08
    else:
        confidence_penalty = 0.0

    adjusted_confidence = max(0.0, min(1.0, llm_confidence - confidence_penalty))

    return DimensionScores(**merged), adjusted_confidence, agreement_label


def score_document(
    client: LiteLLMClient,
    promptfoo_runner: PromptfooRunner,
    revised_content: str,
    document_type: str,
    original_content: str,
    issues: list[Issue],
    validations: list[Validation],
    threshold_config: ThresholdConfig,
    proxy_base_url: str = "",
    proxy_api_key: str = "",
    scorer_runs: int = DEFAULT_SCORER_RUNS,
    scorer_max_workers: int = DEFAULT_SCORER_MAX_WORKERS,
) -> tuple[Scorecard, Optional[dict]]:
    unresolved_critical = _count_unresolved_critical(issues, validations)

    runs = run_scorer_multi(
        client=client,
        revised_content=revised_content,
        document_type=document_type,
        original_content=original_content,
        n_runs=scorer_runs,
        max_workers=scorer_max_workers,
    )

    dimension_scores, avg_variance, confidence, key_strengths, remaining_concerns, overall_assessment = (
        aggregate_scores(runs)
    )

    promptfoo_result = None
    promptfoo_dim_scores = None
    try:
        promptfoo_result = promptfoo_runner.run_evaluation(
            document_content=revised_content,
            document_type=document_type,
            proxy_base_url=proxy_base_url,
            proxy_api_key=proxy_api_key,
        )
        promptfoo_dim_scores = promptfoo_runner.parse_dimension_scores(promptfoo_result)
    except Exception as e:
        logger.warning("promptfoo_eval_failed", error=str(e))

    final_scores, adjusted_confidence, agreement = merge_scorer_and_promptfoo(
        dimension_scores,
        promptfoo_dim_scores,
        confidence,
    )

    gate = _compute_gate_logic(final_scores, threshold_config, unresolved_critical)

    scorecard = Scorecard(
        dimension_scores=final_scores,
        overall_score=gate["overall_score"],
        blocking_reasons=gate["blocking_reasons"],
        unresolved_critical_issues_count=gate["unresolved_critical_issues_count"],
        recommended_next_action=gate["recommended_next_action"],
        passed=gate["passed"],
        key_strengths=key_strengths,
        remaining_concerns=remaining_concerns,
        overall_assessment=overall_assessment,
        confidence_in_scoring=adjusted_confidence,
        scorer_run_count=len(runs),
        scorer_score_variance=avg_variance,
        promptfoo_dimension_scores=promptfoo_dim_scores,
        promptfoo_agreement=agreement,
    )

    logger.info(
        "scoring_done",
        overall_score=scorecard.overall_score,
        passed=scorecard.passed,
        action=scorecard.recommended_next_action.value,
        scorer_runs=len(runs),
        promptfoo_agreement=agreement,
        confidence=adjusted_confidence,
    )
    return scorecard, promptfoo_result
