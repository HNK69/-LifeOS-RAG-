"""
cloud.py

Cloud LLM provider wrapping OpenRouter / OpenAI client.
Preserves existing client configuration and monkeypatch compatibility.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.routing.models import ProviderResponse, ProviderType
from app.routing.providers.base import BaseLLMProvider

try:
    from config import OPENROUTER_MODEL, LLM_TIMEOUT_SECONDS
except ImportError:
    from app.config import OPENROUTER_MODEL, LLM_TIMEOUT_SECONDS

logger = logging.getLogger("lifeos.routing.providers.cloud")


class CloudOpenAIProvider(BaseLLMProvider):
    """Provider communicating with cloud LLM endpoint (OpenRouter/OpenAI)."""

    provider_type: ProviderType = ProviderType.CLOUD

    def __init__(
        self,
        client: Optional[Any] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self._custom_client = client
        self.model = model or OPENROUTER_MODEL
        self.timeout = timeout if timeout is not None else float(LLM_TIMEOUT_SECONDS)

    @property
    def client(self) -> Any:
        """Resolve client dynamically to honor monkeypatches on app.llm.generator.client."""
        if self._custom_client is not None:
            return self._custom_client
        try:
            import app.llm.generator as gen
            return gen.client
        except Exception:
            from openai import OpenAI
            from app.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
            return OpenAI(
                api_key=OPENROUTER_API_KEY or "dummy-api-key",
                base_url=OPENROUTER_BASE_URL,
                timeout=self.timeout,
            )

    def generate(self, prompt: str, **kwargs: Any) -> ProviderResponse:
        start_time = time.perf_counter()
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens")

        create_kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if max_tokens:
            create_kwargs["max_tokens"] = max_tokens

        try:
            response = self.client.chat.completions.create(**create_kwargs)
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            content = response.choices[0].message.content or ""

            tokens_in = getattr(response, "usage", None) and getattr(response.usage, "prompt_tokens", None)
            tokens_out = getattr(response, "usage", None) and getattr(response.usage, "completion_tokens", None)

            if tokens_in is None:
                tokens_in = max(1, len(prompt.split()))
            if tokens_out is None:
                tokens_out = max(1, len(content.split()))

            # Cost estimation ($0.0015 / 1k input, $0.0020 / 1k output)
            cost_usd = round(((tokens_in / 1000.0) * 0.0015) + ((tokens_out / 1000.0) * 0.0020), 6)

            return ProviderResponse(
                text=content,
                raw_text=content,
                model=getattr(response, "model", self.model),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                provider_type=self.provider_type,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.error(f"Cloud LLM invocation failed: {e}")
            raise
