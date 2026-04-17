from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.utils.text import extract_json_array

logger = structlog.get_logger("domain_context")

_SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".gradle",
    "target",
    "bin",
    "obj",
    ".idea",
    ".vscode",
    ".vs",
    "vendor",
    ".terraform",
}

_HIGH_PRIORITY_SUBDIRS = {
    ".context",
    "context",
    "docs",
}

_RELEVANCE_KEYWORDS = [
    "adr",
    "architecture",
    "convention",
    "standard",
    "guideline",
    "pattern",
    "design",
    "domain",
    "style",
    "coding",
    "best-practice",
    "principle",
    "constraint",
    "requirement",
    "spec",
    "roadmap",
    "glossary",
    "naming",
    "stack",
    "tech-stack",
    "infrastructure",
    "guide",
    "bus",
    "flight",
    "hotel",
    "rentacar",
    "sea",
]


def _scan_md_files(root: Path, max_files: int = 50, max_size_kb: int = 200) -> list[dict]:
    candidates: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if not f.lower().endswith(".md"):
                continue
            fpath = Path(dirpath) / f
            try:
                size_kb = fpath.stat().st_size / 1024
                if size_kb > max_size_kb:
                    continue
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                rel_path = str(fpath.relative_to(root))
                fname_lower = f.lower()
                pre_score = sum(1 for kw in _RELEVANCE_KEYWORDS if kw in fname_lower)
                candidates.append(
                    {
                        "path": rel_path,
                        "filename": f,
                        "content": content,
                        "pre_score": pre_score,
                        "content_preview": content[:2000],
                    }
                )
            except Exception:
                continue
            if len(candidates) >= max_files:
                break
    candidates.sort(key=lambda x: x["pre_score"], reverse=True)
    return candidates


def _load_structured_context(context_path: Path) -> str:
    if not context_path.exists() or not context_path.is_dir():
        return ""

    parts = [f"# Domain Context (from {context_path.name})\n"]

    priority_order = [
        ("architecture.md", "Architecture"),
        ("conventions.md", "Conventions"),
        ("glossary.md", "Glossary"),
        ("prd.md", "Product Requirements"),
    ]

    loaded_paths: set[str] = set()

    for filename, section_title in priority_order:
        fpath = context_path / filename
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            parts.append(f"## {section_title}\n")
            parts.append(content)
            parts.append("\n---\n")
            loaded_paths.add(filename)

    for subdir in ["domain", "guides", "infrastructure"]:
        sub_path = context_path / subdir
        if sub_path.exists() and sub_path.is_dir():
            for md_file in sorted(sub_path.glob("*.md")):
                rel = f"{subdir}/{md_file.name}"
                if rel not in loaded_paths:
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    parts.append(f"## {rel}\n")
                    parts.append(content)
                    parts.append("\n---\n")
                    loaded_paths.add(rel)

    for md_file in sorted(context_path.glob("*.md")):
        if md_file.name not in loaded_paths:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            parts.append(f"## {md_file.name}\n")
            parts.append(content)
            parts.append("\n---\n")

    return "\n".join(parts) if len(parts) > 1 else ""


def _find_context_dir(project_path: str) -> Optional[Path]:
    root = Path(project_path).resolve()
    for subdir in _HIGH_PRIORITY_SUBDIRS:
        candidate = root / subdir
        if candidate.exists() and candidate.is_dir():
            md_files = list(candidate.glob("**/*.md"))
            if md_files:
                return candidate
    return None


def _classify_documents_llm(
    client: LiteLLMClient,
    documents: list[dict],
    document_type: str,
) -> list[dict]:
    if not documents:
        return []

    batch = documents[:10]

    prompt_parts = [
        (
            "You are a document relevance classifier. Given a list of project documents, "
            "determine which ones contain domain-specific rules, conventions, architecture "
            "decisions, or design constraints.\n"
        ),
        f"Target document type: {document_type}\n",
        "For each document, classify its relevance:\n",
        (
            "- RELEVANT: Contains domain rules, architecture decisions, conventions, "
            "tech stack constraints, naming standards, coding guidelines, or design "
            "patterns that the project follows\n"
        ),
        "- NOT_RELEVANT: Generic readme, changelog, license, todo, or unrelated content\n\n",
        "Documents:\n",
    ]

    for idx, doc in enumerate(batch):
        prompt_parts.append(f"### Document {idx + 1}: {doc['path']}")
        prompt_parts.append(f"```markdown\n{doc['content_preview']}\n```\n")

    prompt_parts.append(
        'Return a JSON array. Each item: {"path": "relative/path", '
        '"relevant": true/false, "reason": "brief explanation"}\n'
        "Return ONLY the JSON array."
    )

    messages = [
        {"role": "system", "content": "You are a document classifier. Return ONLY valid JSON."},
        {"role": "user", "content": "\n".join(prompt_parts)},
    ]

    model = client.resolve_model("critic_a")
    logger.info("domain_classify_start", model=model, doc_count=len(batch))

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=4096,
        stage="domain_classify",
    )

    content = response.get("content", "")
    classifications = extract_json_array(content)

    path_to_relevance = {item.get("path", ""): item.get("relevant", False) for item in classifications}

    relevant = [doc for doc in batch if path_to_relevance.get(doc["path"], False)]

    logger.info("domain_classify_done", total=len(batch), relevant=len(relevant))
    return relevant


def _build_fallback_context(relevant_docs: list[dict]) -> str:
    if not relevant_docs:
        return ""

    parts = ["# Project Domain Context\n"]
    parts.append("The following project documents describe domain-specific rules, conventions, and design decisions.\n")
    for doc in relevant_docs:
        parts.append(f"## {doc['path']}\n")
        parts.append(doc["content"])
        parts.append("\n---\n")
    return "\n".join(parts)


def extract_domain_context(
    client: LiteLLMClient,
    project_path: str,
    document_type: str,
    context_path: Optional[str] = None,
) -> tuple[str, list[dict]]:
    logger.info(
        "domain_context_start",
        project_path=project_path,
        context_path=context_path,
    )

    domain_context_str = ""
    source_meta: list[dict] = []

    # Priority 1: explicit --context-path
    if context_path:
        cp = Path(context_path).resolve()
        context_str = _load_structured_context(cp)
        if context_str:
            domain_context_str = context_str
            source_meta.append({"source": "context_path", "path": str(cp)})
            logger.info("domain_context_from_cli_path", path=str(cp))
            return domain_context_str, source_meta
        logger.warning("context_path_empty", path=str(cp))

    # Priority 2: .context/ or context/ in project directory
    project_ctx_dir = _find_context_dir(project_path)
    if project_ctx_dir:
        context_str = _load_structured_context(project_ctx_dir)
        if context_str:
            domain_context_str = context_str
            source_meta.append({"source": "project_context_dir", "path": str(project_ctx_dir)})
            logger.info("domain_context_from_project", path=str(project_ctx_dir))
            return domain_context_str, source_meta

    # Priority 3: scan all .md files, classify via LLM
    candidates = _scan_md_files(Path(project_path).resolve())
    logger.info("domain_scan_done", candidates=len(candidates))

    if not candidates:
        return "", []

    high_confidence = [d for d in candidates if d["pre_score"] >= 2]
    needs_classification = [d for d in candidates if d["pre_score"] < 2][:10]

    relevant = list(high_confidence)
    if needs_classification:
        classified = _classify_documents_llm(client, needs_classification, document_type)
        relevant.extend(classified)

    relevant.sort(key=lambda x: x["pre_score"], reverse=True)
    relevant = relevant[:5]

    domain_context_str = _build_fallback_context(relevant)
    source_meta = [{"source": "md_scan", "path": d["path"], "pre_score": d["pre_score"]} for d in relevant]

    logger.info("domain_context_done", source="md_scan", relevant_count=len(relevant))
    return domain_context_str, source_meta
