"""
local.py

Local LLM provider connecting to local inference endpoints (Ollama / LocalAI).
Falls back gracefully to deterministic fallback if local service daemon is not running.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Any, Optional
from urllib.parse import urlparse

from app.routing.models import ProviderResponse, ProviderType
from app.routing.providers.base import BaseLLMProvider
from app.routing.providers.fallback import DeterministicFallbackProvider

try:
    from config import LOCAL_LLM_URL, LOCAL_LLM_MODEL
except ImportError:
    from app.config import LOCAL_LLM_URL, LOCAL_LLM_MODEL

logger = logging.getLogger("lifeos.routing.providers.local")


def _is_server_reachable(base_url: str, timeout: float = 0.25) -> bool:
    """Rapid TCP socket probe to verify endpoint reachability without retry latency."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


class LocalOllamaProvider(BaseLLMProvider):
    """Local inference provider communicating with Ollama or OpenAI-compatible local endpoints."""

    provider_type: ProviderType = ProviderType.LOCAL

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 10.0,
        fallback_provider: Optional[BaseLLMProvider] = None,
        cascade_on_error: bool = True,
    ):
        self.base_url = (base_url or LOCAL_LLM_URL).rstrip("/")
        self.model = model or LOCAL_LLM_MODEL
        self.timeout = timeout
        self.fallback = fallback_provider or DeterministicFallbackProvider()
        self.cascade_on_error = cascade_on_error
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.base_url,
                api_key="ollama",
                timeout=self.timeout,
                max_retries=1,
            )
        return self._client

    def generate(self, prompt: str, **kwargs: Any) -> ProviderResponse:
        start_time = time.perf_counter()
        temperature = kwargs.get("temperature", 0.2)

        # Fast path reachability check if client is not pre-configured
        if self._client is None and not _is_server_reachable(self.base_url):
            logger.info("Local Ollama endpoint %s is unreachable. Invoking fallback.", self.base_url)
            if self.cascade_on_error and self.fallback:
                return self.fallback.generate(prompt, **kwargs)
            raise ConnectionError(f"Local Ollama provider at {self.base_url} is unreachable")

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            content = response.choices[0].message.content or ""

            tokens_in = getattr(response, "usage", None) and getattr(response.usage, "prompt_tokens", None)
            tokens_out = getattr(response, "usage", None) and getattr(response.usage, "completion_tokens", None)

            if tokens_in is None:
                tokens_in = max(1, len(prompt.split()))
            if tokens_out is None:
                tokens_out = max(1, len(content.split()))

            return ProviderResponse(
                text=content,
                raw_text=content,
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=0.0,
                provider_type=self.provider_type,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.info(f"Local Ollama provider unavailable ({e}). Invoking fallback.")
            if self.cascade_on_error and self.fallback:
                return self.fallback.generate(prompt, **kwargs)
            raise
