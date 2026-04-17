from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("promptfoo_runner")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class PromptfooRunner:
    def __init__(self, config_dir: str = ""):
        if not config_dir:
            config_dir = str(_PROJECT_ROOT / "config")
        self.config_dir = Path(config_dir)
        self.promptfoo_config = self.config_dir / "promptfoo" / "promptfooconfig.yaml"

    def run_evaluation(
        self,
        document_content: str,
        document_type: str,
        proxy_base_url: str = "",
        proxy_api_key: str = "",
    ) -> dict[str, Any]:
        rubric_path = self._get_rubric_path(document_type)
        rubric_content = self._load_rubric(rubric_path)

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

            logger.info("promptfoo_eval_start", cmd=" ".join(cmd))

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
            except FileNotFoundError:
                logger.warning("promptfoo_not_found")
                return self._fallback_scoring(document_content, document_type)
            except subprocess.TimeoutExpired:
                logger.error("promptfoo_timeout")
                return self._fallback_scoring(document_content, document_type)

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
            }

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
                    "id": "openai:strong_judge",
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
            return "Evaluate this document on a scale of 0-10 for overall quality."
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("rubric", "")

    def _fallback_scoring(self, document_content: str, doc_type: str) -> dict:
        logger.info("using_fallback_scoring", doc_type=doc_type)
        return {
            "raw": {},
            "returncode": -1,
            "stdout": "",
            "stderr": "Promptfoo not available, using fallback scoring",
            "method": "fallback",
        }


def create_promptfoo_runner(config_dir: str = "") -> PromptfooRunner:
    return PromptfooRunner(config_dir=config_dir)
