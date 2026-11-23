"""
base.py

Abstract base class for all LLM inference providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.routing.models import ProviderResponse, ProviderType


class BaseLLMProvider(ABC):
    """Abstract interface for LifeOS LLM execution providers."""

    provider_type: ProviderType = ProviderType.LOCAL

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> ProviderResponse:
        """Execute text generation against the underlying model."""
        pass
