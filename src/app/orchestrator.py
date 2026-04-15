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
from app.stages.critic import run_critic_a, run_critic_b
from app.stages.cross_reference import run_cross_reference
from app.stages.dedupe import deduplicate_issues
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

logger = structlog.get_logger("orchestrator")


class Orchestrator:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_app_config()
        self.client = LiteLLMClient(self.config)
        self.promptfoo_runner = PromptfooRunner(self.config.config_dir)

    def run(self, file_path: str, doc_type: Optional[str] = None, project_path: Optional[str] = None) -> RunArtifacts:
        run_id, run_dir = create_run_dir(self.config.output_base_dir)

        logger.info("pipeline_start", run_id=run_id, file=file_path, doc_type=doc_type, project_path=project_path)

        model_aliases_used = dict(self.config.model_aliases)
        actual_models_used: dict[str, Optional[str]] = {}
        token_total = 0
        warnings: list[str] = []

        try:
            content, resolved_type = ingest_document(file_path, doc_type)
            write_text(run_dir / "original.md", content)

            threshold_config = load_threshold_config(self.config.config_dir, resolved_type.value)

            cross_ref_issues: list = []
            codebase_context: Optional[str] = None
            if project_path:
                logger.info("stage_cross_reference", run_id=run_id)
                cross_ref_issues, codebase_context = run_cross_reference(
                    self.client, content, resolved_type.value, project_path
                )
                actual_models_used["cross_ref"] = self.client.resolve_model("critic_a")
                if codebase_context:
                    write_text(run_dir / "codebase_context.md", codebase_context)
                    write_json(run_dir / "cross_ref_issues.json", [i.model_dump() for i in cross_ref_issues])
                    logger.info("cross_ref_issues_found", count=len(cross_ref_issues))
            else:
                logger.info("stage_cross_reference_skipped", reason="no_project_path")

            logger.info("stage_critic_a", run_id=run_id)
            issues_a = run_critic_a(self.client, content, resolved_type.value)
            actual_models_used["critic_a"] = self.client.resolve_model("critic_a")

            logger.info("stage_critic_b", run_id=run_id)
            issues_b = run_critic_b(self.client, content, resolved_type.value)
            actual_models_used["critic_b"] = self.client.resolve_model("critic_b")

            logger.info("stage_dedup", run_id=run_id)
            merged_issues = deduplicate_issues(issues_a, issues_b)

            all_issues = cross_ref_issues + merged_issues
            write_json(run_dir / "issues.json", [i.model_dump() for i in all_issues])

            logger.info("stage_validate", run_id=run_id)
            validations = validate_issues(self.client, all_issues, content)
            actual_models_used["validator"] = self.client.resolve_model("validator")
            write_json(run_dir / "validations.json", [v.model_dump() for v in validations])

            valid_issues = get_valid_issues(all_issues, validations)

            logger.info("stage_revise", run_id=run_id)
            revised = revise_document(self.client, content, resolved_type.value, valid_issues)
            actual_models_used["reviser"] = self.client.resolve_model("reviser")
            write_text(run_dir / "revised.md", revised)

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

            _status = "completed"  # noqa: F841
            logger.info(
                "pipeline_done",
                run_id=run_id,
                score=scorecard.overall_score,
                passed=scorecard.passed,
                action=scorecard.recommended_next_action.value,
            )

            return artifacts

        except Exception as e:
            logger.error("pipeline_error", run_id=run_id, error=str(e))
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
            import subprocess
            import shutil
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
