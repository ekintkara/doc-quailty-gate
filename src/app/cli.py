from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.config import load_app_config
from app.orchestrator import Orchestrator
from app.utils.logging import setup_logging

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

app = typer.Typer(
    name="dqg",
    help="Doc Quality Gate - Review, validate, revise, and score implementation documents",
    add_completion=False,
)
console = Console()


def _ensure_env():
    candidates = [Path(".env"), _PROJECT_ROOT / ".env"]
    for env_file in candidates:
        if env_file.exists():
            from dotenv import load_dotenv

            load_dotenv(env_file)
            return


@app.command()
def review(
    file: str = typer.Argument(..., help="Path to the document to review"),
    type: Optional[str] = typer.Option(None, "--type", "-t", help="Document type (auto-detected if not specified)"),
    project: Optional[str] = typer.Option(
        None, "--project", "-p", help="Path to project directory for cross-reference analysis"
    ),
    context_path: Optional[str] = typer.Option(
        None,
        "--context-path",
        "--cp",
        help="Path to structured domain context directory (e.g. obiletcontext/). "
        "Overrides auto-discovery. Contains architecture.md, conventions.md, domain/ etc.",
    ),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config directory"),
):
    """Run the full document quality gate pipeline."""
    _ensure_env()
    app_config = load_app_config(config)
    setup_logging(app_config.log_level)

    console.print("\n[bold blue]Doc Quality Gate[/bold blue]")
    console.print(f"File: {file}")
    console.print(f"Type: {type or 'auto-detect'}")
    if project:
        console.print(f"Project: {project}")
    if context_path:
        console.print(f"Context: {context_path}")
    console.print()

    try:
        orch = Orchestrator(app_config)
        artifacts = orch.run(file, type, project_path=project, context_path=context_path)

        scorecard = artifacts.scorecard
        if scorecard:
            status = "[bold green]PASS[/bold green]" if scorecard.passed else "[bold red]FAIL[/bold red]"
            console.print(
                Panel(
                    f"Score: {scorecard.overall_score}/10 | {status}\n"
                    f"Action: {scorecard.recommended_next_action.value}",
                    title="Gate Decision",
                )
            )

            dim_table = Table(title="Dimension Scores")
            dim_table.add_column("Dimension")
            dim_table.add_column("Score", justify="right")
            for dim, score in scorecard.dimension_scores.model_dump().items():
                color = "green" if score >= 8 else ("yellow" if score >= 6 else "red")
                dim_table.add_row(dim.replace("_", " ").title(), f"[{color}]{score}[/{color}]")
            console.print(dim_table)

        console.print(f"\nArtifacts saved to: {artifacts.output_dir}")
        console.print("  - original.md")
        console.print("  - revised.md")
        console.print("  - issues.json")
        console.print("  - validations.json")
        console.print("  - scorecard.json")
        console.print("  - report.md")
        console.print("  - report.html")
        console.print("  - metadata.json")
        if project:
            console.print("  - domain_context.md")
            console.print("  - domain_analysis.md")
            console.print("  - codebase_context.md")

    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def smoke_test(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config directory"),
):
    """Verify LiteLLM Proxy connectivity and Promptfoo integration."""
    _ensure_env()
    app_config = load_app_config(config)
    setup_logging(app_config.log_level)

    console.print("\n[bold blue]Doc Quality Gate - Smoke Test[/bold blue]\n")

    try:
        orch = Orchestrator(app_config)
        results = orch.smoke_test()

        table = Table(title="Smoke Test Results")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Details")

        for check_name, result in results.items():
            status = result.get("status", "unknown")
            if status == "ok" or result.get("available", False):
                status_str = "[green]OK[/green]"
            elif status == "error":
                status_str = "[red]ERROR[/red]"
            else:
                status_str = "[yellow]UNKNOWN[/yellow]"

            details = ""
            if "error" in result:
                details = str(result["error"])[:80]
            elif "model" in result:
                details = result.get("model", "")
            elif "version" in result:
                details = f"v{result['version']}" if result.get("version") else "N/A"

            table.add_row(check_name, status_str, details)

        console.print(table)
        console.print()

    except Exception as e:
        console.print(f"[red]Smoke test failed: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def demo(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config directory"),
):
    """Run a full demo with example documents."""
    _ensure_env()
    app_config = load_app_config(config)
    setup_logging(app_config.log_level)

    console.print("\n[bold blue]Doc Quality Gate - Demo[/bold blue]\n")

    examples = {
        "feature_spec": str(_PROJECT_ROOT / "examples" / "feature_spec" / "sample.md"),
        "implementation_plan": str(_PROJECT_ROOT / "examples" / "implementation_plan" / "sample.md"),
        "architecture_change": str(_PROJECT_ROOT / "examples" / "architecture_change" / "sample.md"),
    }

    for doc_type, example_path in examples.items():
        if not Path(example_path).exists():
            console.print(f"[yellow]Example not found: {example_path}[/yellow]")
            continue

        console.print(f"[bold]Running demo: {doc_type}[/bold]")
        console.print(f"File: {example_path}\n")

        try:
            orch = Orchestrator(app_config)
            artifacts = orch.run(example_path, doc_type)

            scorecard = artifacts.scorecard
            if scorecard:
                status = "PASS" if scorecard.passed else "FAIL"
                console.print(f"  Result: {status} (Score: {scorecard.overall_score}/10)")
                console.print(f"  Action: {scorecard.recommended_next_action.value}")
                console.print(f"  Issues found: {len(artifacts.issues)}")
                console.print(f"  Artifacts: {artifacts.output_dir}\n")

        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]\n")

    console.print("[bold green]Demo complete![/bold green]")


@app.command()
def eval_only(
    run_id: str = typer.Argument(..., help="Run ID to re-evaluate"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config directory"),
):
    """Re-run Promptfoo scoring on an existing run."""
    _ensure_env()
    app_config = load_app_config(config)
    setup_logging(app_config.log_level)

    console.print("\n[bold blue]Doc Quality Gate - Eval Only[/bold blue]")
    console.print(f"Run ID: {run_id}\n")

    try:
        orch = Orchestrator(app_config)
        artifacts = orch.run_eval_only(run_id)

        scorecard = artifacts.scorecard
        if scorecard:
            status = "[bold green]PASS[/bold green]" if scorecard.passed else "[bold red]FAIL[/bold red]"
            console.print(
                Panel(
                    f"Score: {scorecard.overall_score}/10 | {status}\n"
                    f"Action: {scorecard.recommended_next_action.value}",
                    title="Re-evaluation Result",
                )
            )

        console.print(f"\nUpdated artifacts: {artifacts.output_dir}")

    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def web(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
):
    """Start the web UI."""
    _ensure_env()
    setup_logging("INFO", enable_websocket=True)

    import uvicorn

    console.print("\n[bold blue]Doc Quality Gate Web UI[/bold blue]")
    console.print(f"Opening http://localhost:{port}\n")

    uvicorn.run(
        "app.web.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    app()
