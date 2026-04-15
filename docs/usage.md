# Usage Guide

## CLI Commands

### review

Run the full document quality gate pipeline.

```bash
dqg review <file> [--type <type>] [--config <config_dir>]
```

**Arguments:**
- `file` — Path to the markdown document to review

**Options:**
- `--type, -t` — Document type. One of: `feature_spec`, `implementation_plan`, `architecture_change`, `refactor_plan`, `migration_plan`, `incident_action_plan`, `custom`. If not specified, type is auto-detected from document content.
- `--config, -c` — Path to config directory (default: `config`)

**Example:**
```bash
dqg review examples/feature_spec/sample.md --type feature_spec
```

### smoke-test

Verify LiteLLM Proxy connectivity and Promptfoo integration.

```bash
dqg smoke-test [--config <config_dir>]
```

Checks:
1. LiteLLM Proxy health endpoint
2. Z.AI model route
3. GitHub model route (if configured)
4. Promptfoo availability

### demo

Run the full pipeline against all example documents.

```bash
dqg demo [--config <config_dir>]
```

Processes each example document in `examples/` and generates full artifact sets.

### eval-only

Re-run scoring on an existing run without redoing the full pipeline.

```bash
dqg eval-only <run_id> [--config <config_dir>]
```

**Arguments:**
- `run_id` — The run directory name (timestamp) under `outputs/runs/`

**Example:**
```bash
dqg eval-only 20260115T120000Z
```

## Output Artifacts

Each run creates a directory under `outputs/runs/<timestamp>/`:

| File | Format | Description |
|------|--------|-------------|
| `original.md` | Markdown | The original document as submitted |
| `revised.md` | Markdown | The revised document addressing valid issues |
| `issues.json` | JSON | All issues found by both critics, with deduplication |
| `validations.json` | JSON | Validation results for each issue (valid/invalid/uncertain) |
| `scorecard.json` | JSON | Dimension scores, overall score, gate decision |
| `promptfoo_raw.json` | JSON | Raw Promptfoo evaluation output (if available) |
| `report.md` | Markdown | Human-readable report with tables and summaries |
| `report.html` | HTML | Styled HTML report for browser viewing |
| `metadata.json` | JSON | Run metadata: timestamp, models used, tokens, warnings |

## Programmatic Usage

You can also use the orchestrator directly from Python:

```python
from app.config import load_app_config
from app.orchestrator import Orchestrator

config = load_app_config()
orch = Orchestrator(config)

# Full pipeline
artifacts = orch.run("path/to/document.md", doc_type="feature_spec")
print(f"Score: {artifacts.scorecard.overall_score}/10")
print(f"Passed: {artifacts.scorecard.passed}")

# Re-evaluate existing run
artifacts = orch.run_eval_only("20260115T120000Z")

# Smoke test
results = orch.smoke_test()
```

## Document Type Auto-Detection

If you don't specify `--type`, the app scans the document for keywords:

- "feature", "user story", "specification" → `feature_spec`
- "implementation", "plan", "milestone" → `implementation_plan`
- "architecture", "design", "component" → `architecture_change`
- "refactor", "restructure", "technical debt" → `refactor_plan`
- "migration", "migrate", "cutover" → `migration_plan`
- "incident", "outage", "post-mortem" → `incident_action_plan`
- No match → `custom`
