"""
mock.py

Deterministic mock provider for unit testing routing policies and privacy gatekeeper.
"""

from __future__ import annotations

import time
from typing import Any, Callable, List, Optional

from app.routing.models import ProviderResponse, ProviderType
from app.routing.providers.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """Configurable mock provider recording calls and yielding deterministic responses."""

    def __init__(
        self,
        provider_type: ProviderType = ProviderType.LOCAL,
        default_response: str = "Mock LLM output response",
        model_name: str = "mock-model:v1",
        should_fail: bool = False,
        failure_exception: Optional[Exception] = None,
        custom_handler: Optional[Callable[[str], str]] = None,
    ):
        self.provider_type = provider_type
        self.default_response = default_response
        self.model_name = model_name
        self.should_fail = should_fail
        self.failure_exception = failure_exception or RuntimeError("Simulated provider failure")
        self.custom_handler = custom_handler
        self.calls: List[str] = []

    def generate(self, prompt: str, **kwargs: Any) -> ProviderResponse:
        self.calls.append(prompt)
        if self.should_fail:
            raise self.failure_exception

        start = time.perf_counter()
        if self.custom_handler:
            resp_text = self.custom_handler(prompt)
        else:
            resp_text = self.default_response

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        tokens_in = max(1, len(prompt.split()))
        tokens_out = max(1, len(resp_text.split()))

        return ProviderResponse(
            text=resp_text,
            raw_text=resp_text,
            model=self.model_name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=0.0001 if self.provider_type == ProviderType.CLOUD else 0.0,
            provider_type=self.provider_type,
            latency_ms=latency_ms,
        )
