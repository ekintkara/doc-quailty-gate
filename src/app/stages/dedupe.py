from __future__ import annotations

import structlog

from app.schemas import Issue, SourcePass

logger = structlog.get_logger("dedupe")


def _similarity_score(a: str, b: str) -> float:
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    intersection = a_words & b_words
    union = a_words | b_words
    return len(intersection) / len(union)


def deduplicate_issues(issues_a: list[Issue], issues_b: list[Issue]) -> list[Issue]:
    merged: list[Issue] = []
    used_b: set[int] = set()

    for issue_a in issues_a:
        best_match_idx: int | None = None
        best_score: float = 0.0

        for idx, issue_b in enumerate(issues_b):
            if idx in used_b:
                continue
            title_sim = _similarity_score(issue_a.title, issue_b.title)
            rationale_sim = _similarity_score(issue_a.rationale, issue_b.rationale)
            combined = (title_sim + rationale_sim) / 2

            if combined > best_score and combined >= 0.5:
                best_score = combined
                best_match_idx = idx

        if best_match_idx is not None:
            matched = issues_b[best_match_idx]
            used_b.add(best_match_idx)
            merged_issue = Issue(
                id=f"{issue_a.id}+{matched.id}",
                title=issue_a.title,
                severity=max(
                    issue_a.severity, matched.severity, key=lambda s: ["low", "medium", "high", "critical"].index(s)
                ),
                category=issue_a.category,
                rationale=f"[Critic A] {issue_a.rationale}\n[Critic B] {matched.rationale}",
                evidence_quote=issue_a.evidence_quote or matched.evidence_quote,
                affected_section=issue_a.affected_section or matched.affected_section,
                proposed_fix=issue_a.proposed_fix or matched.proposed_fix,
                source_pass=SourcePass.BOTH,
            )
            merged.append(merged_issue)
            logger.debug("issues_merged", id_a=issue_a.id, id_b=matched.id, score=best_score)
        else:
            merged.append(issue_a)

    for idx, issue_b in enumerate(issues_b):
        if idx not in used_b:
            merged.append(issue_b)

    logger.info("deduplication_done", input_a=len(issues_a), input_b=len(issues_b), output=len(merged))
    return merged
