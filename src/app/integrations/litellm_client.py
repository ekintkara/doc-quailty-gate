from __future__ import annotations

import time
from typing import Any, Optional

import httpx
import structlog

from app.config import AppConfig

logger = structlog.get_logger("litellm_client")

_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 5.0


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
        stage: str = "",
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

        logger.info("litellm_request", model=model, url=url, msg_count=len(messages), stage=stage)

        start = time.monotonic()
        data = None

        for attempt in range(_MAX_RETRIES + 1):
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._headers())

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY_SECONDS * (2**attempt)
                    logger.warning(
                        "litellm_retry",
                        status=response.status_code,
                        attempt=attempt + 1,
                        max_retries=_MAX_RETRIES,
                        delay_seconds=delay,
                        stage=stage,
                    )
                    time.sleep(delay)
                    continue
                else:
                    response.raise_for_status()

            if response.status_code == 402:
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY_SECONDS * (2**attempt) * 1.5
                    logger.warning(
                        "litellm_payment_retry",
                        status=402,
                        attempt=attempt + 1,
                        max_retries=_MAX_RETRIES,
                        delay_seconds=delay,
                        stage=stage,
                    )
                    time.sleep(delay)
                    continue
                else:
                    response.raise_for_status()

            response.raise_for_status()
            data = response.json()
            break

        elapsed_ms = (time.monotonic() - start) * 1000

        content = ""
        usage = {}
        model_used = model
        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            model_used = data.get("model", model)
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("litellm_parse_warning", error=str(e))

        logger.info(
            "litellm_response",
            model=model_used,
            content_length=len(content),
            tokens=usage.get("total_tokens", 0),
            duration_ms=round(elapsed_ms),
            stage=stage,
        )

        try:
            from app.web.log_stream import LogBroadcaster

            LogBroadcaster.get().push_llm_call(
                stage=stage or model,
                model_group=model,
                model_used=model_used,
                messages=messages,
                response_content=content,
                usage=usage,
                duration_ms=elapsed_ms,
            )
        except Exception:
            pass

        return {
            "content": content,
            "model": model_used,
            "usage": usage,
            "raw": data,
        }

    def health_check(self) -> dict[str, Any]:
        url = f"{self.base_url}/health/liveliness"
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
                return {"status": "ok", "data": response.text}
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
