from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from app.config import (
    AppConfig,
    load_app_config,
    load_model_routing,
    load_threshold_config,
)
from app.integrations.litellm_client import LiteLLMClient
from app.integrations.promptfoo_runner import PromptfooRunner
from app.schemas import (
    RunArtifacts,
    RunMetadata,
)
from app.stages.critic import run_critic_a_multi, run_critic_b_multi
from app.stages.critic_judge import judge_critic_runs
from app.stages.cross_reference import run_cross_reference
from app.stages.dedupe import deduplicate_issues
from app.stages.deep_analysis import format_analysis_for_validator, run_deep_analysis
from app.stages.domain_context import extract_domain_context
from app.stages.ingest import ingest_document
from app.stages.report import generate_reports
from app.stages.revise import get_valid_issues, revise_document
from app.stages.score import score_document
from app.stages.validate import validate_issues
from app.utils.files import (
    create_run_dir,
    write_json,
    write_text,
)
from app.web.log_stream import LogBroadcaster

logger = structlog.get_logger("orchestrator")


def _broadcast_stage(run_id: str, stage: str, status: str, detail: str = ""):
    try:
        LogBroadcaster.get().push_pipeline_stage(run_id, stage, status, detail)
    except Exception:
        pass


def _broadcast_done(run_id: str, score=None, passed=None, turkish_summary=""):
    try:
        LogBroadcaster.get().push_pipeline_done(run_id, score, passed, turkish_summary)
    except Exception:
        pass


def _generate_turkish_summary(
    client: "LiteLLMClient",
    scorecard: "Scorecard",
    issues: list,
    validations: list,
    document_content: str,
) -> str:
    try:
        from app.schemas import ValidationDecision

        valid_count = sum(1 for v in validations if v.decision == ValidationDecision.VALID)
        critical_count = sum(1 for i in issues if i.severity.value == "critical")
        high_count = sum(1 for i in issues if i.severity.value == "high")
        ds = scorecard.dimension_scores.model_dump()
        weakest_dims = sorted(ds.items(), key=lambda x: x[1])[:3]
        weakest_str = ", ".join(f"{k.replace('_', ' ')}: {v}/10" for k, v in weakest_dims)

        issue_titles = [f"- [{i.severity.value}] {i.title}" for i in issues[:10]]

        prompt = f"""Bu bir doküman kalite değerlendirme raporunun verileridir. Bunu Türkçe olarak, kısa ve öz bir şekilde özetle.

SKOR: {scorecard.overall_score}/10
SONUÇ: {"GEÇTİ" if scorecard.passed else "KALDI"}
SONRAKİ ADIM: {scorecard.recommended_next_action.value}
TOPLAM SORUN: {len(issues)}
GEÇERLİ SORUNLAR: {valid_count}
KRİTİK: {critical_count}, YÜKSEK: {high_count}
EN ZAYIF BOYUTLAR: {weakest_str}

SONUÇLAR:
{chr(10).join(issue_titles)}

Engelleyici nedenler: {", ".join(scorecard.blocking_reasons) if scorecard.blocking_reasons else "Yok"}

Lütfen şunu yaz:
1. Tek cümlede genel durum (geçti/kaldı, skor)
2. En önemli 3-5 sorunu madde olarak
3. Ne yapılması gerektiğini bir cümleyle

Sadece Türkçe yaz, İngilizce kelime kullanma."""

        model = client.resolve_model("critic_a")
        response = client.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": "Sen bir doküman kalite uzmanısın. Türkçe kısa özetler yazarsın."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
            stage="turkish_summary",
        )
        summary = response.get("content", "").strip()
        if summary:
            return summary
    except Exception as e:
        logger.warning("turkish_summary_failed", error=str(e))
    score = scorecard.overall_score
    passed = "GEÇTİ" if scorecard.passed else "KALDI"
    return f"Skor: {score}/10 - {passed} | {len(issues)} sorun bulundu | {scorecard.recommended_next_action.value}"


class Orchestrator:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_app_config()
        self.client = LiteLLMClient(self.config)
        self.promptfoo_runner = PromptfooRunner(self.config.config_dir)

    def run(
        self,
        file_path: str,
        doc_type: Optional[str] = None,
        project_path: Optional[str] = None,
        context_path: Optional[str] = None,
    ) -> RunArtifacts:
        run_id, run_dir = create_run_dir(self.config.output_base_dir)

        logger.info(
            "pipeline_start",
            run_id=run_id,
            file=file_path,
            doc_type=doc_type,
            project_path=project_path,
            context_path=context_path,
        )

        model_aliases_used = dict(self.config.model_aliases)
        actual_models_used: dict[str, Optional[str]] = {}
        token_total = 0
        warnings: list[str] = []

        try:
            _broadcast_stage(run_id, "ingest", "running")
            content, resolved_type = ingest_document(file_path, doc_type)
            write_text(run_dir / "original.md", content)
            _broadcast_stage(run_id, "ingest", "done")

            threshold_config = load_threshold_config(self.config.config_dir, resolved_type.value)

            cross_ref_issues: list = []
            codebase_context: Optional[str] = None
            domain_context_str: str = ""
            domain_analysis_str: str = ""
            if project_path:
                _broadcast_stage(run_id, "domain_context", "running")
                logger.info("stage_domain_context", run_id=run_id)
                domain_context_str, domain_docs = extract_domain_context(
                    self.client,
                    project_path,
                    resolved_type.value,
                    context_path=context_path,
                )
                if domain_context_str:
                    write_text(run_dir / "domain_context.md", domain_context_str)
                    write_json(run_dir / "domain_docs.json", domain_docs)
                    logger.info("domain_context_found", docs=len(domain_docs))
                actual_models_used["domain_context"] = self.client.resolve_model("critic_a")
                _broadcast_stage(run_id, "domain_context", "done", f"{len(domain_docs)} docs")

                _broadcast_stage(run_id, "cross_reference", "running")
                logger.info("stage_cross_reference", run_id=run_id)
                cross_ref_issues, codebase_context = run_cross_reference(
                    self.client, content, resolved_type.value, project_path
                )
                actual_models_used["cross_ref"] = self.client.resolve_model("critic_a")
                if codebase_context:
                    write_text(run_dir / "codebase_context.md", codebase_context)
                    write_json(run_dir / "cross_ref_issues.json", [i.model_dump() for i in cross_ref_issues])
                    logger.info("cross_ref_issues_found", count=len(cross_ref_issues))
                _broadcast_stage(run_id, "cross_reference", "done", f"{len(cross_ref_issues)} issues")

                if domain_context_str:
                    _broadcast_stage(run_id, "deep_analysis", "running")
                    logger.info("stage_deep_analysis", run_id=run_id)
                    analysis_raw = run_deep_analysis(
                        self.client,
                        content,
                        resolved_type.value,
                        domain_context_str,
                        codebase_context or "",
                    )
                    if analysis_raw:
                        write_json(run_dir / "domain_analysis.json", analysis_raw)
                        domain_analysis_str = format_analysis_for_validator(analysis_raw)
                        write_text(run_dir / "domain_analysis.md", domain_analysis_str)
                    actual_models_used["deep_analysis"] = self.client.resolve_model("critic_a")
                    _broadcast_stage(
                        run_id,
                        "deep_analysis",
                        "done",
                        f"{len(analysis_raw.get('domain_violations', []))} violations" if analysis_raw else "empty",
                    )
            else:
                logger.info("stage_cross_reference_skipped", reason="no_project_path")
                _broadcast_stage(run_id, "cross_reference", "skipped", "no project path")

            _broadcast_stage(run_id, "critic_a_multi", "running")
            logger.info("stage_critic_a_multi", run_id=run_id)
            runs_a = run_critic_a_multi(
                self.client,
                content,
                resolved_type.value,
                max_workers=self.config.critic_max_workers,
                delay_seconds=self.config.critic_delay_seconds,
            )
            actual_models_used["critic_a"] = self.client.resolve_model("critic_a")
            _broadcast_stage(run_id, "critic_a_multi", "done")

            _broadcast_stage(run_id, "critic_a_judge", "running")
            logger.info("stage_critic_a_judge", run_id=run_id)
            issues_a = judge_critic_runs(self.client, runs_a, content, resolved_type.value, "critic_a")
            actual_models_used["critic_judge_a"] = self.client.resolve_model("critic_judge")
            _broadcast_stage(run_id, "critic_a_judge", "done", f"{len(issues_a)} issues")

            _broadcast_stage(run_id, "critic_b_multi", "running")
            logger.info("stage_critic_b_multi", run_id=run_id)
            runs_b = run_critic_b_multi(
                self.client,
                content,
                resolved_type.value,
                max_workers=self.config.critic_max_workers,
                delay_seconds=self.config.critic_delay_seconds,
            )
            actual_models_used["critic_b"] = self.client.resolve_model("critic_b")
            _broadcast_stage(run_id, "critic_b_multi", "done")

            _broadcast_stage(run_id, "critic_b_judge", "running")
            logger.info("stage_critic_b_judge", run_id=run_id)
            issues_b = judge_critic_runs(self.client, runs_b, content, resolved_type.value, "critic_b")
            actual_models_used["critic_judge_b"] = self.client.resolve_model("critic_judge")
            _broadcast_stage(run_id, "critic_b_judge", "done", f"{len(issues_b)} issues")

            _broadcast_stage(run_id, "dedup", "running")
            logger.info("stage_dedup", run_id=run_id)
            merged_issues = deduplicate_issues(issues_a, issues_b)
            _broadcast_stage(run_id, "dedup", "done", f"{len(merged_issues)} merged")

            all_issues = cross_ref_issues + merged_issues
            write_json(run_dir / "issues.json", [i.model_dump() for i in all_issues])

            _broadcast_stage(run_id, "validate", "running")
            logger.info("stage_validate", run_id=run_id)
            validations = validate_issues(
                self.client,
                all_issues,
                content,
                domain_context=domain_context_str,
                codebase_context=codebase_context or "",
                domain_analysis=domain_analysis_str,
            )
            actual_models_used["validator"] = self.client.resolve_model("validator")
            write_json(run_dir / "validations.json", [v.model_dump() for v in validations])
            valid_issues = get_valid_issues(all_issues, validations)
            _broadcast_stage(run_id, "validate", "done", f"{len(valid_issues)} valid")

            _broadcast_stage(run_id, "revise", "running")
            logger.info("stage_revise", run_id=run_id)
            revised = revise_document(self.client, content, resolved_type.value, valid_issues)
            actual_models_used["reviser"] = self.client.resolve_model("reviser")
            write_text(run_dir / "revised.md", revised)
            _broadcast_stage(run_id, "revise", "done")

            _broadcast_stage(run_id, "score", "running")
            logger.info("stage_score", run_id=run_id)
            proxy_url = f"{self.config.proxy_base_url}/v1"
            scorecard, promptfoo_raw = score_document(
                client=self.client,
                promptfoo_runner=self.promptfoo_runner,
                revised_content=revised,
                document_type=resolved_type.value,
                original_content=content,
                issues=all_issues,
                validations=validations,
                threshold_config=threshold_config,
                proxy_base_url=proxy_url,
                proxy_api_key=self.config.proxy_api_key,
            )
            actual_models_used["scorer"] = self.client.resolve_model("scorer")
            write_json(run_dir / "scorecard.json", scorecard.model_dump())

            if promptfoo_raw:
                write_json(run_dir / "promptfoo_raw.json", promptfoo_raw)
            _broadcast_stage(run_id, "score", "done", f"{scorecard.overall_score}/10")

            _broadcast_stage(run_id, "report", "running")
            logger.info("stage_report", run_id=run_id)
            artifacts = RunArtifacts(
                run_id=run_id,
                output_dir=str(run_dir),
                original_content=content,
                revised_content=revised,
                issues=all_issues,
                validations=validations,
                scorecard=scorecard,
                promptfoo_raw=promptfoo_raw,
                metadata=RunMetadata(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    document_type=resolved_type,
                    model_aliases_used=model_aliases_used,
                    actual_models_used=actual_models_used,
                    proxy_base_url=self.config.proxy_base_url,
                    execution_status="completed",
                    token_usage={"total": token_total},
                    estimated_cost=0.0,
                    warnings=warnings,
                ),
            )

            md_report, html_report = generate_reports(artifacts, threshold_config)
            write_text(run_dir / "report.md", md_report)
            write_text(run_dir / "report.html", html_report)
            write_json(run_dir / "metadata.json", artifacts.metadata.model_dump())

            _broadcast_stage(run_id, "report", "done")

            _status = "completed"  # noqa: F841
            logger.info(
                "pipeline_done",
                run_id=run_id,
                score=scorecard.overall_score,
                passed=scorecard.passed,
                action=scorecard.recommended_next_action.value,
            )

            turkish_summary = _generate_turkish_summary(self.client, scorecard, all_issues, validations, content)

            _broadcast_done(run_id, scorecard.overall_score, scorecard.passed, turkish_summary)

            return artifacts

        except Exception as e:
            logger.error("pipeline_error", run_id=run_id, error=str(e))
            _broadcast_done(run_id)
            raise

    def run_eval_only(self, run_id: str) -> RunArtifacts:
        run_dir = Path(self.config.output_base_dir) / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {run_id}")

        original = (run_dir / "original.md").read_text(encoding="utf-8") if (run_dir / "original.md").exists() else ""
        revised = (
            (run_dir / "revised.md").read_text(encoding="utf-8") if (run_dir / "revised.md").exists() else original
        )

        import json

        issues_data = json.loads((run_dir / "issues.json").read_text()) if (run_dir / "issues.json").exists() else []
        from app.schemas import Issue, Validation

        issues = [Issue(**i) for i in issues_data]
        validations_data = (
            json.loads((run_dir / "validations.json").read_text()) if (run_dir / "validations.json").exists() else []
        )
        validations = [Validation(**v) for v in validations_data]

        metadata_data = (
            json.loads((run_dir / "metadata.json").read_text()) if (run_dir / "metadata.json").exists() else {}
        )
        doc_type = metadata_data.get("document_type", "custom")

        threshold_config = load_threshold_config(self.config.config_dir, doc_type)

        logger.info("eval_only_start", run_id=run_id)
        proxy_url = f"{self.config.proxy_base_url}/v1"

        scorecard, promptfoo_raw = score_document(
            client=self.client,
            promptfoo_runner=self.promptfoo_runner,
            revised_content=revised,
            document_type=doc_type,
            original_content=original,
            issues=issues,
            validations=validations,
            threshold_config=threshold_config,
            proxy_base_url=proxy_url,
            proxy_api_key=self.config.proxy_api_key,
        )

        write_json(run_dir / "scorecard.json", scorecard.model_dump())
        if promptfoo_raw:
            write_json(run_dir / "promptfoo_raw.json", promptfoo_raw)

        artifacts = RunArtifacts(
            run_id=run_id,
            output_dir=str(run_dir),
            original_content=original,
            revised_content=revised,
            issues=issues,
            validations=validations,
            scorecard=scorecard,
            promptfoo_raw=promptfoo_raw,
            metadata=RunMetadata(
                timestamp=datetime.now(timezone.utc).isoformat(),
                document_type=doc_type,
                execution_status="eval_only_completed",
            ),
        )

        md_report, html_report = generate_reports(artifacts, threshold_config)
        write_text(run_dir / "report.md", md_report)
        write_text(run_dir / "report.html", html_report)
        write_json(run_dir / "metadata.json", artifacts.metadata.model_dump())

        logger.info("eval_only_done", run_id=run_id, score=scorecard.overall_score)
        return artifacts

    def smoke_test(self) -> dict:
        results: dict[str, dict] = {}

        logger.info("smoke_test_start")

        health = self.client.health_check()
        results["proxy_health"] = health
        logger.info("smoke_proxy", status=health.get("status"))

        routing = load_model_routing(self.config.config_dir)

        for group_name, group_config in routing.model_groups.items():
            logger.info("smoke_testing_group", group=group_name, model=group_config.model)
            test_result = self.client.test_model(group_config.model)
            results[f"model_{group_name}"] = test_result

        promptfoo_available = False
        try:
            import shutil
            import subprocess
            import sys

            npx_cmd = shutil.which("npx")
            if npx_cmd:
                r = subprocess.run(
                    [npx_cmd, "promptfoo", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    shell=(sys.platform == "win32"),
                )
                promptfoo_available = r.returncode == 0
            results["promptfoo"] = {
                "available": promptfoo_available,
                "version": r.stdout.strip() if promptfoo_available else None,
            }
        except Exception as e:
            results["promptfoo"] = {"available": False, "error": str(e)}

        logger.info("smoke_test_done")
        return results
