from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import structlog

from app.schemas import DimensionScores

logger = structlog.get_logger("promptfoo_runner")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class PromptfooEvaluationError(Exception):
    pass


class PromptfooRunner:
    def __init__(self, config_dir: str = "", model_alias: str = "fallback_general"):
        if not config_dir:
            config_dir = str(_PROJECT_ROOT / "config")
        self.config_dir = Path(config_dir)
        self.model_alias = model_alias

    def run_evaluation(
        self,
        document_content: str,
        document_type: str,
        proxy_base_url: str = "",
        proxy_api_key: str = "",
    ) -> dict[str, Any]:
        rubric_path = self._get_rubric_path(document_type)
        rubric_content = self._load_rubric(rubric_path)

        if not rubric_content:
            logger.warning("promptfoo_no_rubric", doc_type=document_type)
            rubric_content = "Evaluate this document on a scale of 0-10 for overall quality."

        with tempfile.TemporaryDirectory(prefix="dqg_promptfoo_") as tmpdir:
            prompt_file = Path(tmpdir) / "prompt.txt"
            prompt_file.write_text(document_content, encoding="utf-8")

            config = self._build_eval_config(
                prompt_file=str(prompt_file),
                rubric=rubric_content,
                proxy_base_url=proxy_base_url,
                proxy_api_key=proxy_api_key,
            )

            config_file = Path(tmpdir) / "promptfooconfig.yaml"
            import yaml

            config_file.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")

            output_file = Path(tmpdir) / "output.json"

            cmd = [
                "npx",
                "promptfoo",
                "eval",
                "-c",
                str(config_file),
                "--output",
                str(output_file),
                "--no-cache",
            ]

            env = os.environ.copy()
            env["OPENAI_API_KEY"] = proxy_api_key
            env["OPENAI_BASE_URL"] = proxy_base_url

            logger.info("promptfoo_eval_start", cmd=" ".join(cmd), model=self.model_alias)

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env,
                    shell=(sys.platform == "win32"),
                )
                logger.info(
                    "promptfoo_eval_done",
                    returncode=result.returncode,
                    stdout_len=len(result.stdout),
                    stderr_len=len(result.stderr),
                )
                if result.returncode != 0:
                    raise PromptfooEvaluationError(
                        f"promptfoo exited with code {result.returncode}: {result.stderr[:500]}"
                    )
            except FileNotFoundError:
                raise PromptfooEvaluationError("promptfoo not found. Install with: npm install -g promptfoo")
            except subprocess.TimeoutExpired:
                raise PromptfooEvaluationError("promptfoo evaluation timed out after 300s")

            raw_output = {}
            if output_file.exists():
                try:
                    raw_output = json.loads(output_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("promptfoo_output_parse_error", error=str(e))

            return {
                "raw": raw_output,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "method": "promptfoo",
                "model_alias": self.model_alias,
            }

    def parse_dimension_scores(self, promptfoo_result: Optional[dict]) -> Optional[DimensionScores]:
        if not promptfoo_result:
            return None

        raw = promptfoo_result.get("raw", {})
        if not raw:
            return None

        results = raw.get("results", {})
        evaluations = results.get("evaluations", [])

        dimension_map = {
            "correctness": "correctness",
            "completeness": "completeness",
            "implementability": "implementability",
            "consistency": "consistency",
            "edge_case_coverage": "edge case coverage",
            "edge case": "edge_case_coverage",
            "testability": "testability",
            "risk_awareness": "risk awareness",
            "risk": "risk_awareness",
            "clarity": "clarity",
        }

        scores: dict[str, float] = {}

        for evaluation in evaluations:
            for assertion in evaluation.get("assertionResults", []):
                metric_raw = (assertion.get("metric") or "").lower().replace("_", " ")
                score = assertion.get("score", 0.0)

                if isinstance(score, (int, float)):
                    score = max(0.0, min(10.0, float(score) * 10.0)) if float(score) <= 1.0 else float(score)
                else:
                    score = 0.0

                for dim_key, search_term in dimension_map.items():
                    if search_term in metric_raw and dim_key not in scores:
                        scores[dim_key] = round(score, 2)
                        break

        if not scores:
            graded_result = raw.get("result", "")
            if isinstance(graded_result, str):
                try:
                    from app.utils.text import extract_json_object

                    parsed = extract_json_object(graded_result)
                    if parsed and "dimension_scores" in parsed:
                        ds = parsed["dimension_scores"]
                        return DimensionScores(**{k: max(0.0, min(10.0, float(v))) for k, v in ds.items()})
                except Exception:
                    pass
            return None

        return DimensionScores(**scores)

    def _build_eval_config(
        self,
        prompt_file: str,
        rubric: str,
        proxy_base_url: str,
        proxy_api_key: str,
    ) -> dict:
        return {
            "description": "Doc Quality Gate Evaluation",
            "providers": [
                {
                    "id": f"openai:{self.model_alias}",
                    "config": {
                        "basePath": proxy_base_url,
                        "apiKey": proxy_api_key,
                    },
                }
            ],
            "prompts": [prompt_file],
            "tests": [
                {
                    "description": "Document quality scoring",
                    "assert": [
                        {
                            "type": "llm-rubric",
                            "value": rubric,
                            "metric": dim,
                            "threshold": 0.5,
                        }
                        for dim in [
                            "correctness",
                            "completeness",
                            "implementability",
                            "consistency",
                            "edge_case_coverage",
                            "testability",
                            "risk_awareness",
                            "clarity",
                        ]
                    ],
                }
            ],
        }

    def _get_rubric_path(self, doc_type: str) -> Path:
        rubric_file = self.config_dir / "promptfoo" / "rubrics" / f"{doc_type}.yaml"
        if not rubric_file.exists():
            rubric_file = self.config_dir / "promptfoo" / "rubrics" / "generic.yaml"
        return rubric_file

    def _load_rubric(self, path: Path) -> str:
        if not path.exists():
            return ""
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("rubric", "")


def create_promptfoo_runner(config_dir: str = "", model_alias: str = "fallback_general") -> PromptfooRunner:
    return PromptfooRunner(config_dir=config_dir, model_alias=model_alias)
