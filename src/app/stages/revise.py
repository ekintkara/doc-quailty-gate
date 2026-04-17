from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import Issue, Validation, ValidationDecision

logger = structlog.get_logger("revise")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
REVISER_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "reviser.md")


def _load_prompt() -> str:
    p = Path(REVISER_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {REVISER_PROMPT_FILE}")


def get_valid_issues(issues: list[Issue], validations: list[Validation]) -> list[Issue]:
    valid_ids = set()
    for v in validations:
        if v.should_auto_apply and v.decision == ValidationDecision.VALID:
            valid_ids.add(v.issue_id)

    valid = []
    for issue in issues:
        if issue.id in valid_ids:
            valid.append(issue)
        elif "+" in issue.id:
            parts = issue.id.split("+")
            if any(p in valid_ids for p in parts):
                valid.append(issue)

    logger.info("valid_issues_selected", total=len(issues), valid=len(valid))
    return valid


def revise_document(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    valid_issues: list[Issue],
) -> str:
    if not valid_issues:
        logger.info("no_valid_issues_returning_original")
        return document_content

    template = _load_prompt()
    valid_issues_json = json.dumps(
        [issue.model_dump() for issue in valid_issues],
        indent=2,
        ensure_ascii=False,
    )

    prompt_text = (
        template.replace("{{document_type}}", document_type)
        .replace("{{document_content}}", document_content)
        .replace("{{valid_issues_json}}", valid_issues_json)
    )

    messages = [
        {"role": "system", "content": "You are a document reviser. Output ONLY the revised markdown document."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("reviser")
    logger.info("revision_start", model=model, issues_to_apply=len(valid_issues))

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=16384,
        stage="revise",
    )

    revised = response.get("content", document_content)

    if revised.strip().startswith("```"):
        lines = revised.strip().split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        revised = "\n".join(lines)

    logger.info("revision_done", original_length=len(document_content), revised_length=len(revised))
    return revised
