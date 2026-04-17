from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from app.integrations.litellm_client import LiteLLMClient
from app.schemas import Issue, SourcePass
from app.utils.text import extract_json_array, normalize_severity

logger = structlog.get_logger("critic")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CRITIC_A_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "critic_a.md")
CRITIC_B_PROMPT_FILE = str(_PROJECT_ROOT / "config" / "prompts" / "critic_b.md")
DEFAULT_NUM_RUNS = 3


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
    run_index: int | None = None,
) -> list[Issue]:
    template = _load_prompt(prompt_file)
    prompt_text = _render_prompt(template, document_content, document_type)

    messages = [
        {"role": "system", "content": "You are a technical document reviewer. Return ONLY valid JSON."},
        {"role": "user", "content": prompt_text},
    ]

    model = client.resolve_model(model_stage)
    run_label = f"{pass_name}_run{run_index}" if run_index is not None else pass_name
    logger.info("critic_pass_start", pass_name=run_label, model=model)

    response = client.chat_completion(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=8192,
        stage=f"critic_{pass_name}" + (f"_run{run_index}" if run_index is not None else ""),
    )

    content = response.get("content", "")
    raw_issues = extract_json_array(content)

    source = SourcePass.CRITIC_A if pass_name == "critic_a" else SourcePass.CRITIC_B
    prefix = source.value[0].upper()
    issues = []
    for idx, raw in enumerate(raw_issues):
        try:
            severity = normalize_severity(raw.get("severity", "medium"))
            base_id = raw.get("id", f"{prefix}-{idx + 1:03d}")
            issue_id = f"{prefix}-{run_index}-{base_id.split('-')[-1]}" if run_index is not None else base_id
            issue = Issue(
                id=issue_id,
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

    logger.info("critic_pass_done", pass_name=run_label, issues_found=len(issues))
    return issues


def run_critic_multi(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    pass_name: str,
    prompt_file: str,
    model_stage: str,
    n_runs: int = DEFAULT_NUM_RUNS,
    max_workers: int = 1,
    delay_seconds: float = 5.0,
) -> list[list[Issue]]:
    logger.info("critic_multi_start", pass_name=pass_name, n_runs=n_runs, max_workers=max_workers)

    runs: list[list[Issue]] = [None] * n_runs  # type: ignore[list-item]

    def _single_run(run_index: int) -> tuple[int, list[Issue]]:
        if run_index > 0 and delay_seconds > 0:
            logger.info("critic_delay", pass_name=pass_name, run_index=run_index, delay=delay_seconds)
            time.sleep(delay_seconds)
        issues = run_critic_pass(
            client=client,
            document_content=document_content,
            document_type=document_type,
            pass_name=pass_name,
            prompt_file=prompt_file,
            model_stage=model_stage,
            run_index=run_index,
        )
        return run_index, issues

    effective_workers = min(max_workers, n_runs)
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {executor.submit(_single_run, i): i for i in range(n_runs)}
        for future in as_completed(futures):
            run_index, issues = future.result()
            runs[run_index] = issues

    total = sum(len(r) for r in runs)
    logger.info("critic_multi_done", pass_name=pass_name, total_issues_across_runs=total)

    return runs


def run_critic_a_multi(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    n_runs: int = DEFAULT_NUM_RUNS,
    max_workers: int = 1,
    delay_seconds: float = 5.0,
) -> list[list[Issue]]:
    return run_critic_multi(
        client=client,
        document_content=document_content,
        document_type=document_type,
        pass_name="critic_a",
        prompt_file=CRITIC_A_PROMPT_FILE,
        model_stage="critic_a",
        n_runs=n_runs,
        max_workers=max_workers,
        delay_seconds=delay_seconds,
    )


def run_critic_b_multi(
    client: LiteLLMClient,
    document_content: str,
    document_type: str,
    n_runs: int = DEFAULT_NUM_RUNS,
    max_workers: int = 1,
    delay_seconds: float = 5.0,
) -> list[list[Issue]]:
    return run_critic_multi(
        client=client,
        document_content=document_content,
        document_type=document_type,
        pass_name="critic_b",
        prompt_file=CRITIC_B_PROMPT_FILE,
        model_stage="critic_b",
        n_runs=n_runs,
        max_workers=max_workers,
        delay_seconds=delay_seconds,
    )


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
