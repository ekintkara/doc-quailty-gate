from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

runner = CliRunner()


class TestCLI:
    def test_app_help(self):
        from app.cli import app

        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Doc Quality Gate" in result.output or "review" in result.output.lower()

    def test_review_help(self):
        from app.cli import app

        result = runner.invoke(app, ["review", "--help"])
        assert result.exit_code == 0
        assert "--type" in result.output
        assert "--config" in result.output

    def test_review_missing_file(self):
        from app.cli import app

        result = runner.invoke(app, ["review", "/nonexistent/file.md"])
        assert result.exit_code != 0

    def test_smoke_test_help(self):
        from app.cli import app

        result = runner.invoke(app, ["smoke-test", "--help"])
        assert result.exit_code == 0

    def test_demo_help(self):
        from app.cli import app

        result = runner.invoke(app, ["demo", "--help"])
        assert result.exit_code == 0

    def test_eval_only_help(self):
        from app.cli import app

        result = runner.invoke(app, ["eval-only", "--help"])
        assert result.exit_code == 0

    @patch("app.orchestrator.Orchestrator")
    def test_smoke_test_command(self, mock_orch):  # noqa: N803
        from app.cli import app

        mock_instance = MagicMock()
        mock_instance.smoke_test.return_value = {
            "proxy_health": {"status": "ok"},
            "model_cheap_large_context": {"status": "ok", "model": "zai/glm-4.5"},
            "model_strong_judge": {"status": "ok", "model": "github/gpt-4o"},
            "promptfoo": {"available": True, "version": "0.90.0"},
        }
        mock_orch.return_value = mock_instance

        result = runner.invoke(app, ["smoke-test"])
        assert result.exit_code == 0
        assert "Smoke Test" in result.output
