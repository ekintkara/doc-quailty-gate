from unittest.mock import patch
import tempfile


class TestPromptfooIntegration:
    def test_promptfoo_runner_init(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        assert runner.config_dir is not None

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

    def test_fallback_scoring(self):
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        result = runner._fallback_scoring("Test content", "feature_spec")
        assert result["method"] == "fallback"
        assert result["returncode"] == -1

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

    @patch("subprocess.run")
    def test_run_evaluation_promptfoo_not_found(self, mock_run):
        from app.integrations.promptfoo_runner import PromptfooRunner

        mock_run.side_effect = FileNotFoundError("npx not found")
        runner = PromptfooRunner(config_dir="config")
        result = runner.run_evaluation(
            document_content="Test doc",
            document_type="feature_spec",
        )
        assert result["method"] == "fallback"

    @patch("subprocess.run")
    def test_run_evaluation_timeout(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="npx", timeout=300)
        from app.integrations.promptfoo_runner import PromptfooRunner

        runner = PromptfooRunner(config_dir="config")
        result = runner.run_evaluation(
            document_content="Test doc",
            document_type="feature_spec",
        )
        assert result["method"] == "fallback"
