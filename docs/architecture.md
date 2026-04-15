# Architecture

## Why LiteLLM Proxy

LiteLLM Proxy serves as a single model gateway that abstracts away provider differences. Instead of the Python app needing to know how to call Z.AI, GitHub Models, GitHub Copilot, or any future provider, it makes one stable API call to the local LiteLLM Proxy endpoint.

Benefits:
- **Provider swapping without code changes**: Change `config/litellm/config.yaml` to use a different provider; no Python code changes needed.
- **Fallback routing**: If the primary model is unavailable, LiteLLM automatically falls back to a configured alternative.
- **Budget controls**: Per-provider and per-model budget limits prevent runaway costs.
- **Unified interface**: All providers use the OpenAI-compatible `/chat/completions` API.

## Why Promptfoo

Promptfoo provides a battle-tested framework for rubric-based LLM evaluation. Rather than hand-rolling rubric logic, Promptfoo gives us:
- **`llm-rubric` assertions**: Model-graded evaluation with configurable rubrics.
- **Per-dimension scoring**: Each dimension is evaluated independently with its own assertion.
- **Threshold enforcement**: Built-in pass/fail logic with score thresholds.
- **Reproducibility**: Evaluation results are deterministic given the same inputs and model.

The Python app invokes Promptfoo via subprocess during the scoring stage. If Promptfoo is not available, the app falls back to direct LLM-based scoring (still functional, just without Promptfoo's structured output).

## Why Orchestration Stays in Python

Python is the right choice for the orchestrator because:
1. **Pydantic schemas** give us strict validation of all pipeline data structures (issues, validations, scores, thresholds).
2. **httpx** provides a clean HTTP client for calling LiteLLM Proxy.
3. **Jinja2** generates clean HTML reports.
4. **Typer** provides a pleasant CLI experience with Rich formatting.
5. The team is proficient in Python and the workflow is fundamentally sequential.

The orchestrator does not need a graph execution engine, state machines, or complex branching. It's a linear pipeline: ingest → criticize → deduplicate → validate → revise → score → report. Python handles this well.

## Why LangGraph Is Intentionally Not Used in V1

LangGraph adds complexity that this application does not need:
- The workflow is linear, not a complex state machine with cycles or branching.
- There is no human-in-the-loop interaction during execution.
- There is no need for persistent graph state or checkpointing.
- Adding LangGraph would introduce a dependency that makes the system harder to debug and inspect.

If V2 needs iterative refinement loops (e.g., revise → re-criticize → re-revise), parallel execution of independent stages, or human-in-the-loop approval gates, LangGraph can be reconsidered at that point.

## Data Flow

```
Document File
     │
     ▼
┌─────────┐
│ Ingest  │ → Detect or accept document type
└────┬────┘
     │
     ▼
┌─────────┐
│Critic A │ → Z.AI (cheap_large_context)
└────┬────┘
     │
     ▼
┌─────────┐
│Critic B │ → Z.AI (cheap_large_context_alt)
└────┬────┘
     │
     ▼
┌─────────┐
│ Dedupe  │ → Merge overlapping issues
└────┬────┘
     │
     ▼
┌──────────┐
│Validate  │ → GitHub/Copilot (strong_judge)
└────┬─────┘
     │
     ▼
┌─────────┐
│ Revise  │ → Z.AI (cheap_large_context)
└────┬────┘
     │
     ▼
┌─────────┐
│  Score  │ → GitHub/Copilot (strong_judge) + Promptfoo
└────┬────┘
     │
     ▼
┌─────────┐
│ Report  │ → Markdown + HTML generation
└─────────┘
```

## Model Routing Strategy

| Stage | Model Group | Provider | Rationale |
|-------|-------------|----------|-----------|
| Critic A | cheap_large_context | Z.AI glm-4.5 | High token count, lower stakes |
| Critic B | cheap_large_context_alt | Z.AI glm-4.5-air | Alternate perspective, high token count |
| Reviser | cheap_large_context | Z.AI glm-4.5 | High token output needed |
| Validator | strong_judge | GitHub gpt-4o | Judgment quality matters |
| Scorer | strong_judge | GitHub gpt-4o | Scoring accuracy matters |
| Fallback | fallback_general | Z.AI glm-4.5-flash | Free tier safety net |
