import pytest
from unittest.mock import patch
import tempfile


class TestPromptfooIntegration:
    def test_promptfoo_runner_init(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        assert runner.config_dir is not None
        assert runner.model_alias == "fallback_general"

    def test_promptfoo_runner_custom_model(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config", model_alias="strong_judge")
        assert runner.model_alias == "strong_judge"

    def test_rubric_loading(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        rubric = runner._load_rubric(runner._get_rubric_path("feature_spec"))
        assert "FEATURE SPECIFICATION" in rubric or "feature" in rubric.lower()

    def test_rubric_fallback_to_generic(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        rubric = runner._load_rubric(runner._get_rubric_path("nonexistent_type"))
        assert len(rubric) > 0

    def test_build_eval_config(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        config = runner._build_eval_config(
            prompt_file=str(tempfile.gettempdir()) + "/test.txt",
            rubric="Test rubric",
            proxy_base_url="http://localhost:4000/v1",
            proxy_api_key="test-key",
        )
        assert "providers" in config
        assert "tests" in config
        assert config["tests"][0]["description"] == "Document quality scoring"
        assert len(config["tests"][0]["assert"]) == 8

    def test_parse_dimension_scores_none(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        assert runner.parse_dimension_scores(None) is None
        assert runner.parse_dimension_scores({}) is None

    def test_parse_dimension_scores_from_evaluations(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        result = {
            "raw": {
                "results": {
                    "evaluations": [
                        {
                            "assertionResults": [
                                {"metric": "correctness", "score": 0.85},
                                {"metric": "completeness", "score": 0.7},
                                {"metric": "implementability", "score": 0.9},
                                {"metric": "consistency", "score": 0.8},
                                {"metric": "edge_case_coverage", "score": 0.6},
                                {"metric": "testability", "score": 0.75},
                                {"metric": "risk_awareness", "score": 0.65},
                                {"metric": "clarity", "score": 0.8},
                            ]
                        }
                    ]
                }
            }
        }
        scores = runner.parse_dimension_scores(result)
        assert scores is not None
        assert scores.correctness == 8.5
        assert scores.completeness == 7.0

    @patch("subprocess.run")
    def test_run_evaluation_promptfoo_not_found_raises(self, mock_run):
        from app.integrations.promptfoo_runner import PromptfooRunner, PromptfooEvaluationError

        mock_run.side_effect = FileNotFoundError("npx not found")
        runner = PromptfooRunner(config_dir="config")
        with pytest.raises(PromptfooEvaluationError, match="promptfoo not found"):
            runner.run_evaluation(
                document_content="Test doc",
                document_type="feature_spec",
            )

    @patch("subprocess.run")
    def test_run_evaluation_timeout_raises(self, mock_run):
        import subprocess
        from app.integrations.promptfoo_runner import PromptfooRunner, PromptfooEvaluationError

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="npx", timeout=300)
        runner = PromptfooRunner(config_dir="config")
        with pytest.raises(PromptfooEvaluationError, match="timed out"):
            runner.run_evaluation(
                document_content="Test doc",
                document_type="feature_spec",
            )

    @patch("subprocess.run")
    def test_run_evaluation_nonzero_exit_raises(self, mock_run):
        from unittest.mock import MagicMock
        from app.integrations.promptfoo_runner import PromptfooRunner, PromptfooEvaluationError

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: something failed"
        mock_run.return_value = mock_result

        runner = PromptfooRunner(config_dir="config")
        with pytest.raises(PromptfooEvaluationError, match="exited with code 1"):
            runner.run_evaluation(
                document_content="Test doc",
                document_type="feature_spec",
            )
