def compute_final_score(dimension_scores: dict, dimension_weights: dict, unresolved_critical: int) -> dict:
    """
    Custom scoring function for Doc Quality Gate.

    Args:
        dimension_scores: dict of dimension_name -> score (0-10)
        dimension_weights: dict of dimension_name -> weight multiplier
        unresolved_critical: count of unresolved critical issues

    Returns:
        dict with overall_score, pass/fail, blocking_reasons, etc.
    """
    if not dimension_scores:
        return {
            "overall_score": 0.0,
            "pass": False,
            "blocking_reasons": ["No dimension scores available"],
            "unresolved_critical_issues_count": unresolved_critical,
            "recommended_next_action": "human_review",
        }

    weighted_sum = 0.0
    weight_total = 0.0
    for dim, score in dimension_scores.items():
        weight = dimension_weights.get(dim, 1.0)
        weighted_sum += score * weight
        weight_total += weight

    overall_score = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0.0

    blocking_reasons = []

    if overall_score < 8.0:
        blocking_reasons.append(f"Overall score {overall_score} below threshold 8.0")

    critical_dims = ["correctness", "completeness", "implementability"]
    for dim in critical_dims:
        score = dimension_scores.get(dim, 0)
        if score < 6.0:
            blocking_reasons.append(f"Critical dimension '{dim}' score {score} below threshold 6.0")

    if unresolved_critical > 0:
        blocking_reasons.append(f"{unresolved_critical} unresolved critical issues remain")

    passed = len(blocking_reasons) == 0

    if passed:
        recommended_action = "implement"
    elif overall_score >= 6.0:
        recommended_action = "revise_again"
    else:
        recommended_action = "human_review"

    return {
        "overall_score": overall_score,
        "pass": passed,
        "blocking_reasons": blocking_reasons,
        "unresolved_critical_issues_count": unresolved_critical,
        "recommended_next_action": recommended_action,
    }
