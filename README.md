# Doc Quality Gate

Implementation Document Quality Gate — review, validate, revise, and score software implementation documents before coding starts.

## What It Does

Doc Quality Gate takes a software implementation document (feature spec, implementation plan, architecture change, etc.) and runs it through a multi-pass review pipeline:

1. **Two independent critic passes** with different perspectives find issues
2. **Deduplication** merges overlapping findings from both passes
3. **Validation** classifies each issue as valid/invalid/uncertain
4. **Revision** rewrites the document addressing valid issues only
5. **Scoring** evaluates the revised document across 8 dimensions (0-10)
6. **Gate decision** applies pass/fail thresholds with blocking rules

The output is a complete artifact set: revised document, issue list, scorecard, and both Markdown/HTML reports.

## Architecture

- **LiteLLM Proxy**: Single model gateway for all LLM calls (Z.AI + GitHub-backed models)
- **Promptfoo**: Rubric-based evaluation and scoring layer
- **Python Orchestrator**: Workflow logic with Typer CLI
- **No LangGraph, no custom framework**: Transparent, inspectable, boring

## Quick Start

### One-Command Setup

```bash
git clone <repo-url> doc-quality-gate
cd doc-quality-gate
bash scripts/setup.sh
```

This automatically:
- Creates venv and installs all Python + Node dependencies
- Configures `.env` (prompts for Z.AI API key)
- Installs the `/dqg` slash command globally for opencode
- Verifies everything works

### Manual Setup

<details>
<summary>Click to expand manual setup steps</summary>

```bash
cd doc-quality-gate

# Create venv
uv venv .venv --python 3.12
source .venv/bin/activate

# Install deps
uv pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your Z.AI API key

# Start LiteLLM proxy
litellm --config config/litellm/config.yaml --port 4000
```

</details>

### Prerequisites

- Python 3.11+
- Node.js 18+ (for Promptfoo)
- Z.AI API key (from https://z.ai)
- GitHub Copilot subscription (optional, for stronger judge model)

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ZAI_API_KEY` | Yes | Z.AI API key from https://z.ai dashboard |
| `LITELLM_PROXY_URL` | No | Proxy URL (default: `http://localhost:4000`) |
| `LITELLM_MASTER_KEY` | No | Proxy auth key (default: `sk-dqg-local`) |
| `DQG_LOG_LEVEL` | No | Log level (default: `INFO`) |

GitHub Copilot uses OAuth device flow — no API key needed, just an active subscription. On first use, you'll be prompted to authenticate in the browser.

### Model Routing

LiteLLM proxy config (`config/litellm/config.yaml`):

| Stage | Model Group | Actual Model | Provider |
|-------|-----------|--------------|----------|
| critic_a, cross_ref, reviser | `cheap_large_context` | zai/glm-4.5 | Z.AI |
| critic_b | `cheap_large_context_alt` | zai/glm-4.5-air | Z.AI |
| validator, scorer | `strong_judge` | github_copilot/gpt-4o | GitHub Copilot |
| fallback | `fallback_general` | zai/glm-4.5-flash | Z.AI |

## Usage

### opencode Integration (Recommended)

In any project, type:
```
/dqg path/to/document.md
```

This runs the full review pipeline including cross-reference against the project codebase.

### CLI

```bash
source .venv/bin/activate

# Review with cross-reference to project code
python -m app.cli review path/to/document.md --project /path/to/project

# Review document only (no cross-reference)
python -m app.cli review path/to/document.md

# Auto-detect document type
python -m app.cli review path/to/document.md

# Specify document type
python -m app.cli review path/to/document.md -t implementation_plan --project .
```

### Wrapper Script (auto-starts proxy)

```bash
bash scripts/dqg-review.sh path/to/document.md feature_spec /path/to/project
```

### Web UI

```bash
python -m app.cli web --port 8080
# Open http://localhost:8080
```

### Smoke Test

```bash
python -m app.cli smoke-test
```
```

Runs the full pipeline against all example documents in `examples/`.

### Re-evaluate Existing Run

```bash
dqg eval-only outputs/runs/20260115T120000Z
```

Re-runs scoring on an existing run without re-doing the full pipeline.

## Example Commands

```bash
# Review a feature spec
dqg review examples/feature_spec/sample.md --type feature_spec

# Review an implementation plan
dqg review examples/implementation_plan/sample.md

# Check everything is working
dqg smoke-test

# Run all demos
dqg demo
```

## Output Artifacts

Each run creates `outputs/runs/<timestamp>/` with:

| File | Description |
|------|-------------|
| `original.md` | The original document |
| `revised.md` | The revised document |
| `issues.json` | All issues found with metadata |
| `validations.json` | Validation results per issue |
| `scorecard.json` | Dimension scores and gate decision |
| `promptfoo_raw.json` | Raw Promptfoo output (if available) |
| `report.md` | Human-readable Markdown report |
| `report.html` | Styled HTML report |
| `metadata.json` | Run metadata (models used, tokens, status) |

## Scoring Logic

Documents are scored 0-10 on 8 dimensions:
- **correctness** — factual accuracy, sound assumptions
- **completeness** — all required sections, no gaps
- **implementability** — can a developer build from this?
- **consistency** — internal consistency, uniform terminology
- **edge_case_coverage** — boundary conditions, error paths
- **testability** — can the implementation be tested?
- **risk_awareness** — risks identified with mitigations
- **clarity** — well-organized, precise language

## Threshold Logic

- Default overall threshold: **8.0** (configurable per document type)
- Critical dimensions (correctness, completeness, implementability) must each meet their own minimum: **6.0**
- **Fail** if any blocking condition is met:
  - Overall score below threshold
  - Any critical dimension below its minimum
  - Unresolved critical/high issues remain
- **Pass** requires ALL conditions to be satisfied

The gate decision is not based on overall score alone.

## Supported Document Types

| Type | Flag |
|------|------|
| Feature Specification | `feature_spec` |
| Implementation Plan | `implementation_plan` |
| Architecture Change | `architecture_change` |
| Refactor Plan | `refactor_plan` |
| Migration Plan | `migration_plan` |
| Incident Action Plan | `incident_action_plan` |
| Custom | `custom` |

## Adding a New Document Type

1. Add the type to `config/app.yaml` under `document_types`
2. Add threshold config in `config/thresholds.yaml` under `per_type`
3. Create a rubric file: `config/promptfoo/rubrics/<type_name>.yaml`
4. (Optional) Add a sample document in `examples/<type_name>/`

## Changing Model Routing

Edit `config/model_routing.yaml`:

```yaml
routing:
  critic_a: cheap_large_context
  critic_b: cheap_large_context_alt
  reviser: cheap_large_context
  validator: strong_judge
  scorer: strong_judge
```

And update the corresponding model groups and LiteLLM proxy config.

## Disabling a Provider

To disable Z.AI: Remove Z.AI model groups from `config/model_routing.yaml` and point all stages to GitHub-backed models.

To disable GitHub: Point `strong_judge` to a Z.AI model like `zai/glm-4.7`.

## Known Limitations

- Requires running LiteLLM Proxy as a separate process
- Promptfoo integration requires Node.js and npm
- Scoring quality depends on the underlying LLM model capability
- Large documents may exceed model context windows
- Deduplication uses word-overlap heuristics (not embedding-based)
- No database — all state is file-based in the output directory

## Project Structure

```
doc-quality-gate/
  config/           # YAML configs for app, thresholds, routing, LiteLLM, Promptfoo
  docs/             # Architecture and design docs
  examples/         # Sample documents for each type
  src/app/          # Python source code
    cli.py          # Typer CLI commands
    config.py       # Configuration loading
    schemas.py      # Pydantic models
    orchestrator.py # Pipeline orchestrator
    integrations/   # LiteLLM client, Promptfoo runner
    stages/         # Pipeline stages (ingest, critic, dedupe, validate, revise, score, report)
    utils/          # File I/O, text processing, logging
  tests/            # pytest test suite
  outputs/          # Generated run artifacts
```

## License

MIT
