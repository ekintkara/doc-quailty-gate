from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import Issue, Validation, ValidationDecision
from app.utils.text import extract_json_array

logger = structlog.get_logger("validate")

VALIDATOR_PROMPT_FILE = "config/prompts/validator.md"


def _load_prompt() -> str:
    p = Path(VALIDATOR_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {VALIDATOR_PROMPT_FILE}")


def validate_issues(
    client: LiteLLMClient,
    issues: list[Issue],
    document_content: str,
) -> list[Validation]:
    if not issues:
        logger.info("no_issues_to_validate")
        return []

    template = _load_prompt()
    issues_json = json.dumps(
        [issue.model_dump() for issue in issues],
        indent=2,
        ensure_ascii=False,
    )

    prompt_text = template.replace("{{issues_json}}", issues_json).replace("{{document_content}}", document_content)

    messages = [
        {"role": "system", "content": "You are a validation judge. Return ONLY valid JSON."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("validator")
    logger.info("validation_start", model=model, issue_count=len(issues))

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
    )

    content = response.get("content", "")
    raw_validations = extract_json_array(content)

    validations = []
    for raw in raw_validations:
        try:
            decision_str = raw.get("decision", "uncertain").lower()
            decision = (
                ValidationDecision(decision_str)
                if decision_str in [d.value for d in ValidationDecision]
                else ValidationDecision.UNCERTAIN
            )

            confidence = float(raw.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            should_auto = raw.get("should_auto_apply", False)
            if decision != ValidationDecision.VALID or confidence < 0.8:
                should_auto = False

            validation = Validation(
                issue_id=raw.get("issue_id", ""),
                decision=decision,
                confidence=confidence,
                reason=raw.get("reason", ""),
                should_auto_apply=bool(should_auto),
            )
            validations.append(validation)
        except Exception as e:
            logger.warning("validation_parse_error", error=str(e), raw=raw)

    logger.info(
        "validation_done",
        total=len(validations),
        valid=sum(1 for v in validations if v.decision == ValidationDecision.VALID),
        invalid=sum(1 for v in validations if v.decision == ValidationDecision.INVALID),
        uncertain=sum(1 for v in validations if v.decision == ValidationDecision.UNCERTAIN),
    )
    return validations
