# LiteLLM Routing

## Model Groups

The LiteLLM Proxy config (`config/litellm/config.yaml`) defines model groups that the Python app references by name:

| Group Name | Underlying Model | Provider | Purpose |
|------------|-----------------|----------|---------|
| `cheap_large_context` | `zai/glm-4.5` | Z.AI | High-token stages (critic, reviser) |
| `cheap_large_context_alt` | `zai/glm-4.5-air` | Z.AI | Alternate critic perspective |
| `strong_judge` | `github/gpt-4o` | GitHub Models | Validation and scoring |
| `fallback_general` | `zai/glm-4.5-flash` | Z.AI | Free-tier fallback for all stages |

## Provider Mappings

### Z.AI Provider

- **Prefix**: `zai/`
- **Auth**: `ZAI_API_KEY` environment variable
- **Models**: glm-4.7, glm-4.6, glm-4.5, glm-4.5-air, glm-4.5-flash (free), etc.
- **Context**: 128K-200K tokens
- **Pricing**: glm-4.5-flash is free; glm-4.5-air is $0.20/$1.10 per million tokens

### GitHub Models Provider

- **Prefix**: `github/`
- **Auth**: `GITHUB_API_KEY` environment variable (personal access token)
- **Models**: gpt-4o, Llama-3.2-11B, Phi-4, etc.
- **Usage**: Access to GitHub Marketplace models

### GitHub Copilot Provider

- **Prefix**: `github_copilot/`
- **Auth**: OAuth device flow (automatic on first use)
- **Models**: gpt-4, gpt-4o (via Copilot Chat API)
- **Requirement**: Active GitHub Copilot subscription

## Fallback Rules

```yaml
router_settings:
  fallbacks:
    - cheap_large_context: [fallback_general]
    - cheap_large_context_alt: [fallback_general]
    - strong_judge: [cheap_large_context, fallback_general]
```

- If any Z.AI model fails, it falls back to `fallback_general` (glm-4.5-flash).
- If the GitHub model fails, it falls back to Z.AI models.
- Retries: 2 attempts before fallback.

## Budget/Routing Rules

```yaml
router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  timeout: 120
  allowed_fails: 3
```

- `simple-shuffle`: Weighted random selection when multiple deployments exist for a model name.
- `allowed_fails: 3`: A deployment is cooled down after 3 failures in a minute.
- `timeout: 120`: 120-second timeout per request.

To add per-provider budgets, add `provider_budget_config` to `router_settings`:

```yaml
router_settings:
  provider_budget_config:
    zai:
      budget_limit: 10.0
      time_period: 1d
    github:
      budget_limit: 5.0
      time_period: 1d
```

## How Copilot and Z.AI Are Routed Differently

The Python app does not know about providers. It uses model group names:

```python
model = client.resolve_model("critic_a")  # Returns "cheap_large_context"
response = client.chat_completion(model=model, messages=[...])
```

The LiteLLM Proxy maps `cheap_large_context` → `zai/glm-4.5` and `strong_judge` → `github/gpt-4o`.

To switch providers:
1. Edit `config/litellm/config.yaml` to change the underlying model
2. Edit `config/model_routing.yaml` to change the routing
3. Restart the LiteLLM Proxy

No Python code changes needed.

## Switching to GitHub Copilot

To use GitHub Copilot instead of GitHub Models for the `strong_judge` group:

```yaml
model_list:
  - model_name: strong_judge
    litellm_params:
      model: github_copilot/gpt-4
```

On first use, LiteLLM will prompt you to authenticate via GitHub's OAuth device flow.
