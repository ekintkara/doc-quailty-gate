from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class AppConfig(BaseModel):
    proxy_base_url: str = os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000")
    proxy_api_key: str = os.environ.get("LITELLM_PROXY_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
    proxy_timeout_seconds: int = 300
    output_base_dir: str = "outputs/runs"
    config_dir: str = "config"
    log_level: str = "INFO"
    critic_max_workers: int = 1
    critic_delay_seconds: float = 5.0

    model_aliases: dict[str, str] = {
        "critic_a": "cheap_large_context",
        "critic_b": "cheap_large_context_alt",
        "critic_judge": "cheap_large_context",
        "validator": "strong_judge",
        "reviser": "cheap_large_context",
        "scorer": "strong_judge",
        "fallback": "fallback_general",
    }


class ThresholdConfig(BaseModel):
    overall_threshold: float = 8.0
    critical_dimension_threshold: float = 6.0
    critical_dimensions: list[str] = ["correctness", "completeness", "implementability"]
    dimension_weights: dict[str, float] = {
        "correctness": 1.0,
        "completeness": 1.0,
        "implementability": 1.0,
        "consistency": 1.0,
        "edge_case_coverage": 1.0,
        "testability": 1.0,
        "risk_awareness": 1.0,
        "clarity": 1.0,
    }


class ModelGroupConfig(BaseModel):
    provider: str
    model: str
    description: str = ""


class ModelRoutingConfig(BaseModel):
    model_groups: dict[str, ModelGroupConfig] = {}
    routing: dict[str, str] = {}


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_env(value: str) -> str:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        if ":" in env_key:
            env_key, default = env_key.split(":", 1)
            return os.environ.get(env_key, default)
        return os.environ.get(env_key, value)
    return value


def _resolve_config_dir(config_dir: Optional[str] = None) -> str:
    if config_dir is None:
        config_dir = os.environ.get("DQG_CONFIG_DIR", "config")
    p = Path(config_dir)
    if not p.is_absolute():
        if p.exists():
            return str(p.resolve())
        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / config_dir
            if candidate.exists():
                return str(candidate.resolve())
        candidate = _PROJECT_ROOT / config_dir
        if candidate.exists():
            return str(candidate.resolve())
    return str(p.resolve())


def load_app_config(config_dir: Optional[str] = None) -> AppConfig:
    config_dir = _resolve_config_dir(config_dir)
    app_yaml = Path(config_dir) / "app.yaml"
    if app_yaml.exists():
        with open(app_yaml) as f:
            raw = yaml.safe_load(f) or {}
        _app = raw.get("app", {})
        proxy = raw.get("proxy", {})
        output = raw.get("output", {})
        logging_cfg = raw.get("logging", {})
        model_aliases = raw.get("model_aliases", {})
        pipeline = raw.get("pipeline", {})

        return AppConfig(
            proxy_base_url=_resolve_env(
                proxy.get("base_url", os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000"))
            ),
            proxy_api_key=_resolve_env(
                proxy.get("api_key", os.environ.get("LITELLM_PROXY_API_KEY", os.environ.get("LITELLM_MASTER_KEY", "")))
            ),
            proxy_timeout_seconds=proxy.get("timeout_seconds", 120),
            output_base_dir=_resolve_env(output.get("base_dir", "outputs/runs")),
            config_dir=config_dir,
            log_level=_resolve_env(logging_cfg.get("level", "INFO")),
            model_aliases=model_aliases,
            critic_max_workers=int(_resolve_env(str(pipeline.get("critic_max_workers", 1)))),
            critic_delay_seconds=float(_resolve_env(str(pipeline.get("critic_delay_seconds", 5.0)))),
        )
    return AppConfig()


def load_threshold_config(config_dir: Optional[str] = None, doc_type: Optional[str] = None) -> ThresholdConfig:
    config_dir = _resolve_config_dir(config_dir)
    thresholds_yaml = Path(config_dir) / "thresholds.yaml"
    if not thresholds_yaml.exists():
        return ThresholdConfig()

    with open(thresholds_yaml) as f:
        raw = yaml.safe_load(f) or {}

    defaults = raw.get("defaults", {})
    per_type = raw.get("per_type", {})

    type_config = per_type.get(doc_type, {}) if doc_type else {}

    overall_threshold = type_config.get("overall_threshold", defaults.get("overall_threshold", 8.0))
    critical_threshold = type_config.get(
        "critical_dimension_threshold", defaults.get("critical_dimension_threshold", 6.0)
    )
    critical_dims = defaults.get("critical_dimensions", ["correctness", "completeness", "implementability"])
    weights = type_config.get("dimension_weights", defaults.get("dimension_weights", {}))

    all_weights = {
        "correctness": 1.0,
        "completeness": 1.0,
        "implementability": 1.0,
        "consistency": 1.0,
        "edge_case_coverage": 1.0,
        "testability": 1.0,
        "risk_awareness": 1.0,
        "clarity": 1.0,
    }
    all_weights.update(weights)

    return ThresholdConfig(
        overall_threshold=overall_threshold,
        critical_dimension_threshold=critical_threshold,
        critical_dimensions=critical_dims,
        dimension_weights=all_weights,
    )


def load_model_routing(config_dir: Optional[str] = None) -> ModelRoutingConfig:
    config_dir = _resolve_config_dir(config_dir)
    routing_yaml = Path(config_dir) / "model_routing.yaml"
    if not routing_yaml.exists():
        return ModelRoutingConfig()

    with open(routing_yaml) as f:
        raw = yaml.safe_load(f) or {}

    groups = {}
    for name, group_data in raw.get("model_groups", {}).items():
        groups[name] = ModelGroupConfig(
            provider=group_data.get("provider", ""),
            model=group_data.get("model", ""),
            description=group_data.get("description", ""),
        )

    return ModelRoutingConfig(
        model_groups=groups,
        routing=raw.get("routing", {}),
    )
