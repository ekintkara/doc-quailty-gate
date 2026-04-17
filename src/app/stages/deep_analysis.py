from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.utils.text import extract_json_array

logger = structlog.get_logger("deep_analysis")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEEP_ANALYST_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "deep_analyst.md")


def _load_prompt() -> str:
    p = Path(DEEP_ANALYST_PROMPT_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {DEEP_ANALYST_PROMPT_FILE}")


def run_deep_analysis(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    domain_context: str,
    codebase_context: str,
) -> dict:
    template = _load_prompt()

    prompt_text = (
        template.replace("{{domain_context}}", domain_context)
        .replace("{{codebase_context}}", codebase_context)
        .replace("{{document_type}}", document_type)
        .replace("{{document_content}}", document_content)
    )

    messages = [
        {"role": "system", "content": "You are a domain analysis expert. Return ONLY valid JSON."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model("critic_a")
    logger.info("deep_analysis_start", model=model, document_type=document_type)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=8192,
        stage="deep_analysis",
    )

    content = response.get("content", "")

    analysis = _parse_response(content)

    if analysis:
        logger.info(
            "deep_analysis_done",
            patterns=len(analysis.get("domain_patterns_found", [])),
            violations=len(analysis.get("domain_violations", [])),
            intentional=len(analysis.get("intentional_patterns", [])),
            risk=analysis.get("risk_assessment", {}).get("overall_risk", "unknown"),
        )
    else:
        logger.warning("deep_analysis_parse_failed")

    return analysis or {}


def _parse_response(content: str) -> Optional[dict]:
    text = content.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        start = 0
        end = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith("```") and start == 0:
                start = i + 1
            elif line.strip() == "```" and start > 0:
                end = i
                break
        text = "\n".join(lines[start:end])

    json_start = text.find("{")
    json_end = text.rfind("}") + 1

    if json_start >= 0 and json_end > json_start:
        try:
            parsed = json.loads(text[json_start:json_end])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    items = extract_json_array(text)
    if items and isinstance(items[0], dict):
        return items[0]

    return None


def format_analysis_for_validator(analysis: dict) -> str:
    if not analysis:
        return ""

    parts = ["# Deep Domain Analysis\n"]

    patterns = analysis.get("domain_patterns_found", [])
    if patterns:
        parts.append("## Patterns Correctly Followed")
        for p in patterns:
            parts.append(f"- {p}")
        parts.append("")

    violations = analysis.get("domain_violations", [])
    if violations:
        parts.append("## Domain Violations (genuine issues)")
        for v in violations:
            rule = v.get("rule", "")
            desc = v.get("description", "")
            evidence = v.get("evidence", "")
            existing = v.get("existing_pattern", "")
            parts.append(f"- **Rule {rule}**: {desc}")
            if evidence:
                parts.append(f"  - Evidence: {evidence}")
            if existing:
                parts.append(f"  - Should use: {existing}")
        parts.append("")

    intentional = analysis.get("intentional_patterns", [])
    if intentional:
        parts.append("## Intentional Domain Patterns (NOT issues)")
        for ip in intentional:
            pattern = ip.get("pattern", "")
            evidence = ip.get("domain_evidence", "")
            confidence = ip.get("confidence", 0.0)
            parts.append(f"- **{pattern}** (confidence: {confidence})")
            if evidence:
                parts.append(f"  - Domain evidence: {evidence}")
        parts.append("")

    risk = analysis.get("risk_assessment", {})
    if risk:
        parts.append("## Risk Assessment")
        parts.append(f"- Overall risk: {risk.get('overall_risk', 'unknown')}")
        for factor in risk.get("risk_factors", []):
            parts.append(f"- Risk factor: {factor}")
        for path in risk.get("critical_paths_affected", []):
            parts.append(f"- Critical path: {path}")
        parts.append("")

    infra = analysis.get("existing_infrastructure", {})
    if infra:
        parts.append("## Existing Infrastructure (reusable)")
        for key, items in infra.items():
            if items:
                parts.append(f"- {key}: {', '.join(str(i) for i in items)}")
        parts.append("")

    summary = analysis.get("analysis_summary", "")
    if summary:
        parts.append("## Analysis Summary")
        parts.append(summary)

    return "\n".join(parts)
