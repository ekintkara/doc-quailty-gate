from __future__ import annotations

from pathlib import Path

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import Issue, SourcePass
from app.utils.text import extract_json_array, normalize_severity

logger = structlog.get_logger("critic")

CRITIC_A_PROMPT_FILE = "config/prompts/critic_a.md"
CRITIC_B_PROMPT_FILE = "config/prompts/critic_b.md"


def _load_prompt(path: str) -> str:
    p = Path(path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {path}")


def _render_prompt(template: str, document_content: str, document_type: str) -> str:
    return template.replace("{{document_content}}", document_content).replace("{{document_type}}", document_type)


def run_critic_pass(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    pass_name: str,
    prompt_file: str,
    model_stage: str = "critic_a",
) -> list[Issue]:
    template = _load_prompt(prompt_file)
    prompt_text = _render_prompt(template, document_content, document_type)

    messages = [
        {"role": "system", "content": "You are a technical document reviewer. Return ONLY valid JSON."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model(model_stage)
    logger.info("critic_pass_start", pass_name=pass_name, model=model)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=8192,
    )

    content = response.get("content", "")
    raw_issues = extract_json_array(content)

    source = SourcePass.CRITIC_A if pass_name == "critic_a" else SourcePass.CRITIC_B
    issues = []
    for idx, raw in enumerate(raw_issues):
        try:
            severity = normalize_severity(raw.get("severity", "medium"))
            issue = Issue(
                id=raw.get("id", f"{source.value[0].upper()}-{idx + 1:03d}"),
                title=raw.get("title", f"Issue {idx + 1}"),
                severity=severity,
                category=raw.get("category", "unknown"),
                rationale=raw.get("rationale", ""),
                evidence_quote=raw.get("evidence_quote", ""),
                affected_section=raw.get("affected_section", ""),
                proposed_fix=raw.get("proposed_fix", ""),
                source_pass=source,
            )
            issues.append(issue)
        except Exception as e:
            logger.warning("issue_parse_error", idx=idx, error=str(e))

    logger.info("critic_pass_done", pass_name=pass_name, issues_found=len(issues))
    return issues


def run_critic_a(client: LiteLLMClient, document_content: str, document_type: str) -> list[Issue]:
    return run_critic_pass(
        client=client,
        document_content=document_content,
        document_type=document_type,
        pass_name="critic_a",
        prompt_file=CRITIC_A_PROMPT_FILE,
        model_stage="critic_a",
    )


def run_critic_b(client: LiteLLMClient, document_content: str, document_type: str) -> list[Issue]:
    return run_critic_pass(
        client=client,
        document_content=document_content,
        document_type=document_type,
        pass_name="critic_b",
        prompt_file=CRITIC_B_PROMPT_FILE,
        model_stage="critic_b",
    )
