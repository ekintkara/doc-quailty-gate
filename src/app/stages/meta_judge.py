from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import DimensionScores, MetaJudgeResult, Scorecard
from app.utils.text import extract_json_object

logger = structlog.get_logger("meta_judge")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
META_JUDGE_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "meta_judge.md")


def _load_prompt() -> str:
    p = Path(META_JUDGE_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {META_JUDGE_PROMPT_FILE}")


def _render_prompt(
    template: str,
    scorecard: Scorecard,
    revised_content: str,
    document_type: str,
) -> str:
    dim_scores_json = json.dumps(scorecard.dimension_scores.model_dump(), indent=2)

    promptfoo_section = ""
    if scorecard.promptfoo_dimension_scores:
        pf_json = json.dumps(scorecard.promptfoo_dimension_scores.model_dump(), indent=2)
        agreement = scorecard.promptfoo_agreement or "N/A"
        promptfoo_section = f"""### Promptfoo Independent Evaluation (different model)

**Promptfoo Dimension Scores:**
{pf_json}

**Agreement between LLM Scorer and Promptfoo:** {agreement}
"""

    prompt_text = (
        template.replace("{{document_type}}", document_type)
        .replace("{{scorer_run_count}}", str(scorecard.scorer_run_count))
        .replace("{{dimension_scores_json}}", dim_scores_json)
        .replace("{{score_variance}}", str(scorecard.scorer_score_variance))
        .replace("{{confidence}}", str(scorecard.confidence_in_scoring))
        .replace("{{revised_content}}", revised_content[:8000])
    )

    if "{{#promptfoo_scores}}" in prompt_text:
        start_marker = "{{#promptfoo_scores}}"
        end_marker = "{{/promptfoo_scores}}"
        start_idx = prompt_text.find(start_marker)
        end_idx = prompt_text.find(end_marker)
        if start_idx >= 0 and end_idx >= 0:
            block = prompt_text[start_idx + len(start_marker) : end_idx]
            if scorecard.promptfoo_dimension_scores:
                prompt_text = prompt_text[:start_idx] + block + prompt_text[end_idx + len(end_marker) :]
            else:
                prompt_text = prompt_text[:start_idx] + prompt_text[end_idx + len(end_marker) :]

    if "{{promptfoo_scores_json}}" in prompt_text:
        if scorecard.promptfoo_dimension_scores:
            prompt_text = prompt_text.replace(
                "{{promptfoo_scores_json}}",
                json.dumps(scorecard.promptfoo_dimension_scores.model_dump(), indent=2),
            )
        else:
            prompt_text = prompt_text.replace("{{promptfoo_scores_json}}", "Not available")

    if "{{promptfoo_agreement}}" in prompt_text:
        prompt_text = prompt_text.replace("{{promptfoo_agreement}}", scorecard.promptfoo_agreement or "N/A")

    return prompt_text


def run_meta_judge(
    client: LiteLLMClient,
    scorecard: Scorecard,
    revised_content: str,
    document_type: str,
) -> MetaJudgeResult:
    template = _load_prompt()
    prompt_text = _render_prompt(template, scorecard, revised_content, document_type)

    messages = [
        {
            "role": "system",
            "content": "You are a meta-judge evaluating document scoring fairness. Return ONLY valid JSON.",
        },
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("meta_judge")
    logger.info("meta_judge_start", model=model)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=2048,
        stage="meta_judge",
    )

    content = response.get("content", "")
    parsed = extract_json_object(content)

    if not parsed:
        logger.warning("meta_judge_parse_failed")
        return MetaJudgeResult(verdict="fair", reasoning="Failed to parse meta-judge response")

    verdict = parsed.get("verdict", "fair")
    if verdict not in ("fair", "over_optimistic", "over_pessimistic", "needs_adjustment"):
        verdict = "fair"

    adjustments = {}
    max_adj = 1.5
    for dim in [
        "correctness",
        "completeness",
        "implementability",
        "consistency",
        "edge_case_coverage",
        "testability",
        "risk_awareness",
        "clarity",
    ]:
        adj = parsed.get("adjustments", {}).get(dim, 0.0)
        adj = max(-max_adj, min(max_adj, float(adj)))
        if adj != 0.0:
            adjustments[dim] = adj

    confidence_adj = parsed.get("confidence_adjustment", 0.0)
    confidence_adj = max(-0.1, min(0.1, float(confidence_adj)))

    result = MetaJudgeResult(
        verdict=verdict,
        adjustments=adjustments,
        reasoning=parsed.get("reasoning", ""),
        confidence_adjustment=confidence_adj,
    )

    logger.info(
        "meta_judge_done",
        verdict=verdict,
        adjustments=len(adjustments),
        confidence_adj=confidence_adj,
    )
    return result


def apply_meta_judge_adjustments(
    scorecard: Scorecard,
    meta_result: MetaJudgeResult,
    threshold_config: "ThresholdConfig",
    unresolved_critical: int,
) -> Scorecard:
    if not meta_result.adjustments and meta_result.verdict == "fair":
        scorecard.meta_judge_result = meta_result
        return scorecard

    current = scorecard.dimension_scores.model_dump()

    for dim, adj in meta_result.adjustments.items():
        if dim in current:
            current[dim] = max(0.0, min(10.0, round(current[dim] + adj, 2)))

    new_dim_scores = DimensionScores(**current)

    weights = threshold_config.dimension_weights
    weighted_sum = 0.0
    weight_total = 0.0
    for dim, score in current.items():
        w = weights.get(dim, 1.0)
        weighted_sum += score * w
        weight_total += w
    new_overall = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0.0

    blocking_reasons: list[str] = []
    if new_overall < threshold_config.overall_threshold:
        blocking_reasons.append(f"Overall score {new_overall} below threshold {threshold_config.overall_threshold}")

    for dim in threshold_config.critical_dimensions:
        dim_score = current.get(dim, 0)
        if dim_score < threshold_config.critical_dimension_threshold:
            blocking_reasons.append(
                f"Critical dimension '{dim}' score {dim_score} below "
                f"threshold {threshold_config.critical_dimension_threshold}"
            )

    if unresolved_critical > 0:
        blocking_reasons.append(f"{unresolved_critical} unresolved critical/high issues remain")

    passed = new_overall >= threshold_config.overall_threshold
    from app.schemas import NextAction

    if passed:
        action = NextAction.IMPLEMENT
    elif new_overall >= threshold_config.overall_threshold - 2.0:
        action = NextAction.REVISE_AGAIN
    else:
        action = NextAction.HUMAN_REVIEW

    new_confidence = max(0.0, min(1.0, scorecard.confidence_in_scoring + meta_result.confidence_adjustment))

    adjusted = Scorecard(
        dimension_scores=new_dim_scores,
        overall_score=new_overall,
        blocking_reasons=blocking_reasons,
        unresolved_critical_issues_count=unresolved_critical,
        recommended_next_action=action,
        passed=passed,
        key_strengths=scorecard.key_strengths,
        remaining_concerns=scorecard.remaining_concerns,
        overall_assessment=scorecard.overall_assessment,
        confidence_in_scoring=new_confidence,
        scorer_run_count=scorecard.scorer_run_count,
        scorer_score_variance=scorecard.scorer_score_variance,
        promptfoo_dimension_scores=scorecard.promptfoo_dimension_scores,
        promptfoo_agreement=scorecard.promptfoo_agreement,
        meta_judge_result=meta_result,
    )

    logger.info(
        "meta_judge_applied",
        verdict=meta_result.verdict,
        old_score=scorecard.overall_score,
        new_score=new_overall,
        old_passed=scorecard.passed,
        new_passed=passed,
        adjustments=meta_result.adjustments,
    )

    return adjusted
