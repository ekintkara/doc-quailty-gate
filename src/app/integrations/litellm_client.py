from __future__ import annotations

from typing import Any, Optional

import httpx
import structlog

from app.config import AppConfig

logger = structlog.get_logger("litellm_client")


class LiteLLMClient:
    def __init__(self, config: AppConfig):
        self.base_url = config.proxy_base_url.rstrip("/")
        self.api_key = config.proxy_api_key
        self.timeout = config.proxy_timeout_seconds
        self.model_aliases = config.model_aliases

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def resolve_model(self, stage: str) -> str:
        alias = self.model_aliases.get(stage, stage)
        return alias

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        url = f"{self.base_url}/chat/completions"

        logger.info("litellm_request", model=model, url=url, msg_count=len(messages))

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()

        content = ""
        usage = {}
        model_used = model
        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            model_used = data.get("model", model)
        except (KeyError, IndexError) as e:
            logger.warning("litellm_parse_warning", error=str(e), data_keys=list(data.keys()))

        logger.info(
            "litellm_response",
            model=model_used,
            content_length=len(content),
            tokens=usage.get("total_tokens", 0),
        )

        return {
            "content": content,
            "model": model_used,
            "usage": usage,
            "raw": data,
        }

    def health_check(self) -> dict[str, Any]:
        url = f"{self.base_url}/health"
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(url)
                response.raise_for_status()
                return {"status": "ok", "data": response.json()}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def test_model(self, model: str) -> dict[str, Any]:
        try:
            result = self.chat_completion(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=10,
                temperature=0.0,
            )
            return {
                "status": "ok",
                "model": result["model"],
                "content": result["content"][:100],
                "tokens": result["usage"].get("total_tokens", 0),
            }
        except Exception as e:
            return {"status": "error", "model": model, "error": str(e)}


def create_litellm_client(config: Optional[AppConfig] = None) -> LiteLLMClient:
    if config is None:
        from app.config import load_app_config

        config = load_app_config()
    return LiteLLMClient(config)
