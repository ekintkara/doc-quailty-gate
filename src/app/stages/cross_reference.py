from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import Issue, SourcePass
from app.stages.codebase_context import build_context_string, scan_project
from app.utils.text import extract_json_array, normalize_severity

logger = structlog.get_logger("cross_reference")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CROSS_REF_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "cross_reference.md")


def _load_prompt() -> str:
    p = Path(CROSS_REF_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {CROSS_REF_PROMPT_FILE}")


def run_cross_reference(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    project_path: str,
) -> tuple[list[Issue], Optional[str]]:
    logger.info("cross_reference_start", project_path=project_path)

    context = scan_project(project_path)
    context_str = build_context_string(context)

    logger.info(
        "codebase_context_built", routes=len(context.get("api_routes", [])), models=len(context.get("db_models", []))
    )

    template = _load_prompt()
    prompt_text = (
        template.replace("{{codebase_context}}", context_str)
        .replace("{{document_content}}", document_content)
        .replace("{{document_type}}", document_type)
    )

    messages = [
        {"role": "system", "content": "You are a cross-reference analysis expert. Return ONLY valid JSON."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("critic_a")
    logger.info("cross_reference_llm_request", model=model)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=8192,
        stage="cross_reference",
    )

    content = response.get("content", "")
    raw_issues = extract_json_array(content)

    issues = []
    for idx, raw in enumerate(raw_issues):
        try:
            severity = normalize_severity(raw.get("severity", "medium"))
            issue = Issue(
                id=raw.get("id", f"XR-{idx + 1:03d}"),
                title=raw.get("title", f"Cross-ref issue {idx + 1}"),
                severity=severity,
                category=raw.get("category", "unknown"),
                rationale=raw.get("rationale", ""),
                evidence_quote=raw.get("evidence_quote", ""),
                affected_section=raw.get("affected_section", ""),
                proposed_fix=raw.get("proposed_fix", ""),
                source_pass=SourcePass.CROSS_REF,
            )
            issues.append(issue)
        except Exception as e:
            logger.warning("cross_ref_parse_error", idx=idx, error=str(e))

    logger.info("cross_reference_done", issues_found=len(issues))
    return issues, context_str
