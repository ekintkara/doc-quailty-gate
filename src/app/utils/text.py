from __future__ import annotations

import json
import re
from typing import Any


def extract_json_array(text: str) -> list[dict]:
    candidates = _extract_json_blocks(text)
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict) and any(isinstance(v, list) for v in candidate.values()):
            for v in candidate.values():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    return v
    return []


def extract_json_object(text: str) -> dict:
    candidates = _extract_json_blocks(text)
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def _extract_json_blocks(text: str) -> list[Any]:
    results = []

    fenced = re.findall(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    for block in fenced:
        parsed = _try_parse_json(block.strip())
        if parsed is not None:
            results.append(parsed)

    if results:
        return results

    parsed = _try_parse_json(text.strip())
    if parsed is not None:
        results.append(parsed)

    return results


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def normalize_severity(severity: str) -> str:
    severity = severity.lower().strip()
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
        "minor": "low",
        "major": "high",
        "blocker": "critical",
    }
    return mapping.get(severity, "medium")


def truncate_text(text: str, max_length: int = 500) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."
