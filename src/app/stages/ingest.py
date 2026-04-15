from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog

from app.schemas import DocumentType

logger = structlog.get_logger("ingest")

DOCUMENT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "feature_spec": ["feature", "user story", "specification", "requirements", "acceptance criteria"],
    "implementation_plan": ["implementation", "plan", "milestone", "sprint", "task breakdown", "development plan"],
    "architecture_change": ["architecture", "design", "system design", "component", "refactor architecture", "adr"],
    "refactor_plan": ["refactor", "restructure", "cleanup", "technical debt", "code quality"],
    "migration_plan": ["migration", "migrate", "data migration", "platform migration", "cutover"],
    "incident_action_plan": ["incident", "outage", "post-mortem", "remediation", "action plan", "sev1", "sev2"],
}


def detect_document_type(content: str) -> DocumentType:
    content_lower = content.lower()
    scores: dict[str, int] = {}
    for doc_type, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in content_lower)
        scores[doc_type] = score

    best_type = max(scores, key=scores.get, default="custom")
    if scores.get(best_type, 0) == 0:
        logger.info("no_type_detected_defaulting_custom")
        return DocumentType.CUSTOM

    logger.info("type_detected", doc_type=best_type, score=scores[best_type])
    return DocumentType(best_type)


def ingest_document(file_path: str, doc_type: Optional[str] = None) -> tuple[str, DocumentType]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {file_path}")

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError("Document is empty")

    if doc_type:
        resolved_type = DocumentType(doc_type)
    else:
        resolved_type = detect_document_type(content)

    logger.info("document_ingested", file=str(path), type=resolved_type.value, length=len(content))
    return content, resolved_type
