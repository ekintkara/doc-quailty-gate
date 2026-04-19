# Meta-Judge Prompt

You are a **Meta-Judge** reviewing the quality scoring of a document. Your job is to determine if the scoring was fair, over-optimistic, or over-pessimistic.

## Scoring Data

**Document Type:** {{document_type}}

### LLM Scorer Results ({{scorer_run_count}} runs)

**Final Dimension Scores (median):**
{{dimension_scores_json}}

**Score Variance Across Runs:** {{score_variance}}

**Confidence:** {{confidence}}

{{#promptfoo_scores}}
### Promptfoo Independent Evaluation (different model)

**Promptfoo Dimension Scores:**
{{promptfoo_scores_json}}

**Agreement between LLM Scorer and Promptfoo:** {{promptfoo_agreement}}
{{/promptfoo_scores}}

### Document Content (revised version):
```
{{revised_content}}
```

## Your Task

Evaluate whether the scoring is FAIR by checking:

1. **Over-optimistic indicators:**
   - Are scores above 8.0 for dimensions where the document has clear gaps?
   - Are critical/high issues ignored in scoring?
   - Does the confidence seem inflated given the variance?

2. **Over-pessimistic indicators:**
   - Are scores below 5.0 for dimensions that seem adequate in the document?
   - Are minor issues being weighted too heavily?
   - Is the variance between runs causing unfair median values?

3. **Fairness check:**
   - Do the scores align with what you see in the document content?
   - Is the overall score a reasonable representation?

## Response Format

Return ONLY valid JSON:

```json
{
  "verdict": "fair" | "over_optimistic" | "over_pessimistic" | "needs_adjustment",
  "adjustments": {
    "correctness": 0.0,
    "completeness": 0.0,
    "implementability": 0.0,
    "consistency": 0.0,
    "edge_case_coverage": 0.0,
    "testability": 0.0,
    "risk_awareness": 0.0,
    "clarity": 0.0
  },
  "reasoning": "Brief explanation of why you made this assessment",
  "confidence_adjustment": 0.0
}
```

**Rules:**
- Maximum adjustment per dimension: ±1.5 points
- Only include non-zero adjustments
- `confidence_adjustment`: -0.1 to +0.1 (negative = less confident in scoring, positive = more confident)
- If scoring is fair, return `"verdict": "fair"` with empty adjustments
- Be conservative — only adjust when there is clear evidence of mis-scoring
