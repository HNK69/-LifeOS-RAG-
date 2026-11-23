"""
models.py

Domain models and typed schemas for Milestone 7: Hybrid Local-Cloud Routing
and Privacy Gatekeeper.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class PIICategory(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    IP_ADDRESS = "ip_address"
    CREDENTIALS = "credentials"
    FINANCIAL = "financial"
    GOV_ID = "gov_id"
    SENSITIVE_NAME = "sensitive_name"


class SensitivityLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RoutingMode(str, Enum):
    LOCAL_ONLY = "local_only"
    LOCAL_FIRST = "local_first"
    CLOUD_WITH_GATEKEEPER = "cloud_with_gatekeeper"
    HYBRID_CASCADE = "hybrid_cascade"


class ProviderType(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    FALLBACK = "fallback"


class PIIEntity(BaseModel):
    category: PIICategory
    text: str
    start: int
    end: int
    placeholder: str = ""
    confidence: float = 1.0


class RoutingDecision(BaseModel):
    target_provider: ProviderType
    routing_mode: RoutingMode
    sensitivity_level: SensitivityLevel
    reason: str
    scrubbed_prompt: str
    pii_detected: List[PIIEntity] = Field(default_factory=list)
    deidentified: bool = False
    vault_id: Optional[str] = None


class ProviderResponse(BaseModel):
    text: str
    raw_text: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    provider_type: ProviderType = ProviderType.LOCAL
    latency_ms: float = 0.0
    reidentified: bool = False
    error: Optional[str] = None
