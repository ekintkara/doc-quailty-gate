from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import Issue, Severity, SourcePass
from app.utils.text import extract_json_array, normalize_severity

logger = structlog.get_logger("critic_judge")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CRITIC_JUDGE_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "critic_judge.md")


def _load_prompt() -> str:
    p = Path(CRITIC_JUDGE_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {CRITIC_JUDGE_PROMPT_FILE}")


def _build_runs_json(runs: list[list[Issue]], critic_name: str) -> str:
    runs_data = []
    for run_idx, run_issues in enumerate(runs):
        run_data = {
            "run_index": run_idx,
            "run_label": f"run_{run_idx}",
            "issue_count": len(run_issues),
            "issues": [
                {
                    "id": issue.id,
                    "title": issue.title,
                    "severity": issue.severity.value,
                    "category": issue.category,
                    "rationale": issue.rationale,
                    "evidence_quote": issue.evidence_quote,
                    "affected_section": issue.affected_section,
                    "proposed_fix": issue.proposed_fix,
                }
                for issue in run_issues
            ],
        }
        runs_data.append(run_data)
    return json.dumps(runs_data, indent=2, ensure_ascii=False)


def judge_critic_runs(
    client: LiteLLMClient,
    runs: list[list[Issue]],
    document_content: str,
    document_type: str,
    critic_name: str,
) -> list[Issue]:
    if not runs:
        logger.info("judge_no_runs", critic=critic_name)
        return []

    total_input = sum(len(r) for r in runs)
    if total_input == 0:
        logger.info("judge_all_runs_empty", critic=critic_name)
        return []

    template = _load_prompt()
    num_runs = len(runs)
    runs_json = _build_runs_json(runs, critic_name)

    prompt_text = (
        template.replace("{{num_runs}}", str(num_runs))
        .replace("{{critic_name}}", critic_name)
        .replace("{{runs_json}}", runs_json)
        .replace("{{document_content}}", document_content)
    )

    messages = [
        {
            "role": "system",
            "content": "You are a Critic Judge consolidating multiple review runs. Return ONLY valid JSON.",
        },
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("critic_judge")
    logger.info(
        "critic_judge_start", critic=critic_name, model=model, num_runs=num_runs, total_input_issues=total_input
    )

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=16384,
        stage=f"critic_{critic_name}_judge",
    )

    content = response.get("content", "")
    raw_judged = extract_json_array(content)

    source = SourcePass.CRITIC_A if "a" in critic_name.lower() else SourcePass.CRITIC_B
    prefix = source.value[0].upper()

    kept_issues: list[Issue] = []
    rejected_count = 0
    inferred_count = 0

    for idx, raw in enumerate(raw_judged):
        decision = raw.get("judge_decision", "keep").lower()
        if decision.startswith("rejected"):
            rejected_count += 1
            continue

        if decision == "inferred":
            inferred_count += 1

        try:
            severity = normalize_severity(raw.get("severity", "medium"))
            run_origins = raw.get("run_origins", [])
            if isinstance(run_origins, str):
                run_origins = [run_origins]

            consensus_score = raw.get("consensus_score")
            if consensus_score is not None:
                consensus_score = max(0.0, min(1.0, float(consensus_score)))

            issue = Issue(
                id=f"{prefix}-{idx + 1:03d}",
                title=raw.get("title", f"Issue {idx + 1}"),
                severity=severity,
                category=raw.get("category", "unknown"),
                rationale=raw.get("rationale", ""),
                evidence_quote=raw.get("evidence_quote", ""),
                affected_section=raw.get("affected_section", ""),
                proposed_fix=raw.get("proposed_fix", ""),
                source_pass=source,
                consensus_score=consensus_score,
                run_origins=run_origins,
            )
            kept_issues.append(issue)
        except Exception as e:
            logger.warning("judge_parse_error", idx=idx, error=str(e))

    logger.info(
        "critic_judge_done",
        critic=critic_name,
        input_issues=total_input,
        kept=len(kept_issues),
        rejected=rejected_count,
        inferred=inferred_count,
    )

    return kept_issues
