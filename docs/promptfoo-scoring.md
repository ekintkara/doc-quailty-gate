# Promptfoo Scoring

## Overview

Promptfoo is used as the evaluation layer for scoring revised documents. It provides structured, rubric-based evaluation with `llm-rubric` assertions.

## Rubrics

Each document type has its own rubric file in `config/promptfoo/rubrics/`:

- `feature_spec.yaml` — Functional requirements, acceptance criteria
- `implementation_plan.yaml` — Implementation steps, testing strategy
- `architecture_change.yaml` — Architecture decisions, migration path
- `refactor_plan.yaml` — Refactoring scope, regression safety
- `migration_plan.yaml` — Data migration, cutover strategy
- `incident_action_plan.yaml` — Incident response, remediation
- `generic.yaml` — General quality assessment (fallback)

Each rubric defines evaluation criteria for the 8 scoring dimensions:
correctness, completeness, implementability, consistency, edge_case_coverage, testability, risk_awareness, clarity.

## Model-Graded Assertions

The Promptfoo config uses `llm-rubric` assertions for each dimension:

```yaml
tests:
  - description: "Score document quality across dimensions"
    assert:
      - type: llm-rubric
        value: |
          Evaluate the document on CORRECTNESS (0-10):
          - Are there any factual errors or contradictions?
          - Are assumptions stated clearly and are they reasonable?
        metric: correctness
        threshold: 0.6
```

The `llm-rubric` assertion sends the document and rubric to an LLM judge, which returns a JSON object:
```json
{
  "reason": "Analysis of the rubric and the output",
  "score": 0.8,
  "pass": true
}
```

- `score` ranges from 0.0 to 1.0 (mapped from the 0-10 scale)
- `threshold` sets the minimum score to pass
- `metric` labels the result for tracking

## Custom Scoring Function

The file `config/promptfoo/scoring.py` contains `compute_final_score()` which implements gate logic:

1. **Weighted average**: Each dimension score is multiplied by its weight (from `thresholds.yaml`)
2. **Critical dimension check**: correctness, completeness, implementability must each meet their minimum
3. **Unresolved issues check**: If critical/high issues remain unresolved, the gate fails
4. **Overall threshold check**: The weighted average must meet the overall threshold

```python
def compute_final_score(dimension_scores, dimension_weights, unresolved_critical):
    # Calculate weighted average
    # Check critical dimension thresholds
    # Check unresolved issues
    # Determine pass/fail and recommended next action
```

## Pass/Fail Logic

A document **passes** if and only if ALL of these are true:
1. Weighted overall score >= overall threshold (default: 8.0)
2. Every critical dimension >= critical dimension threshold (default: 6.0)
3. No unresolved critical/high issues remain

If any condition fails, the document **fails** with specific blocking reasons.

### Recommended Next Actions

| Condition | Action |
|-----------|--------|
| All conditions met | `implement` |
| Score within 2 points of threshold | `revise_again` |
| Score far below threshold | `human_review` |

## Integration Architecture

The Python app calls Promptfoo via subprocess:

```
Python Orchestrator
       │
       ├── Direct LLM scoring (always runs)
       │     Uses LiteLLM Proxy for dimension scoring
       │
       └── Promptfoo evaluation (if available)
             npx promptfoo eval -c <config> --output <file>
             Results parsed from JSON output
```

If Promptfoo is not available (not installed, timeout, error), the app uses the direct LLM scoring results. This ensures the pipeline always produces a scorecard.

## Per-Dimension Thresholds

Each `llm-rubric` assertion has a `threshold` property. The current default is 0.5 (which corresponds to a 5/10). However, the gate logic in `compute_final_score` applies stricter thresholds from `config/thresholds.yaml`:

- Overall: 8.0 (weighted)
- Critical dimensions: 6.0 each
- Other dimensions: no individual threshold (only contribute to overall)

This means the Promptfoo assertion thresholds are intentionally permissive — the real gate logic lives in the Python code.
