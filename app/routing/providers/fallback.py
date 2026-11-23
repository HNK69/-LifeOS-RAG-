"""
fallback.py

Zero-dependency deterministic local extractive and heuristic LLM fallback provider.
Guarantees a clean, structured response when neither cloud nor local Ollama daemons are reachable.
"""

from __future__ import annotations

import time
from typing import Any

from app.routing.models import ProviderResponse, ProviderType
from app.routing.providers.base import BaseLLMProvider


class DeterministicFallbackProvider(BaseLLMProvider):
    """
    Offline local deterministic fallback generator.
    Never throws network errors, requires zero external services.
    """

    provider_type: ProviderType = ProviderType.FALLBACK

    def __init__(self, model_name: str = "lifeos-deterministic-fallback:v1"):
        self.model_name = model_name

    def generate(self, prompt: str, **kwargs: Any) -> ProviderResponse:
        start_time = time.perf_counter()
        
        # Extractive heuristic response formulation
        lines = [line.strip() for line in prompt.splitlines() if line.strip()]
        user_query = lines[-1] if lines else prompt
        
        # Check if json output format was requested in prompt
        if "valid json" in prompt.lower() or "return only valid json" in prompt.lower() or "json schema" in prompt.lower():
            response_text = '{"status": "success", "summary": "Deterministic local fallback response", "details": []}'
        else:
            response_text = (
                f"[Local Fallback Response] Processed query: '{user_query[:120]}'. "
                f"Generated local fallback context."
            )

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        tokens_in = max(1, len(prompt.split()))
        tokens_out = max(1, len(response_text.split()))

        return ProviderResponse(
            text=response_text,
            raw_text=response_text,
            model=self.model_name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=0.0,
            provider_type=self.provider_type,
            latency_ms=latency_ms,
        )
