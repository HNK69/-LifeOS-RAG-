"""
hybrid_router.py

Central orchestrator for Milestone 7: Hybrid Local-Cloud Routing & Privacy Gatekeeper.
Enforces the 5-step gating order before cloud egress:
1. Workspace local_only check
2. Consent verification
3. Daily budget cap check
4. Privacy Gatekeeper inspection (Critical secrets hard-block; PII de-identification)
5. Cloud dispatch with graceful local cascade
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.routing.budget import BudgetManager
from app.routing.gatekeeper import PrivacyGatekeeper, SurrogateVault
from app.routing.models import (
    PIICategory,
    ProviderResponse,
    ProviderType,
    RoutingDecision,
    RoutingMode,
    SensitivityLevel,
)
from app.routing.providers.base import BaseLLMProvider
from app.routing.providers.cloud import CloudOpenAIProvider
from app.routing.providers.local import LocalOllamaProvider
from app.workspace.store import get_workspace

try:
    from config import PII_REDACTION_ENABLED, ROUTING_MODE
except ImportError:
    from app.config import PII_REDACTION_ENABLED, ROUTING_MODE

logger = logging.getLogger("lifeos.routing.hybrid_router")


class HybridRouter:
    """
    Orchestrates routing policies, privacy checks, de-identification,
    budget enforcement, and multi-provider execution.
    """

    def __init__(
        self,
        routing_mode: Optional[RoutingMode | str] = None,
        cloud_provider: Optional[BaseLLMProvider] = None,
        local_provider: Optional[BaseLLMProvider] = None,
        gatekeeper: Optional[PrivacyGatekeeper] = None,
        budget_manager: Optional[BudgetManager] = None,
        pii_redaction_enabled: Optional[bool] = None,
    ):
        raw_mode = routing_mode or ROUTING_MODE
        self.routing_mode = (
            raw_mode if isinstance(raw_mode, RoutingMode) else RoutingMode(str(raw_mode))
        )
        self.cloud_provider = cloud_provider or CloudOpenAIProvider()
        self.local_provider = local_provider or LocalOllamaProvider()
        self.gatekeeper = gatekeeper or PrivacyGatekeeper()
        self.budget_manager = budget_manager or BudgetManager()
        self.pii_redaction_enabled = (
            pii_redaction_enabled
            if pii_redaction_enabled is not None
            else bool(PII_REDACTION_ENABLED)
        )

    def evaluate_route(
        self,
        prompt: str,
        workspace_id: str = "default",
        user_id: str = "system",
        vault: Optional[SurrogateVault] = None,
        **kwargs: Any,
    ) -> RoutingDecision:
        """
        Evaluate the 5-step gating pipeline to determine target provider and scrub prompt if necessary.
        """
        # 1. Workspace local_only check
        ws = get_workspace(workspace_id)
        if ws and ws.is_local_only:
            return RoutingDecision(
                target_provider=ProviderType.LOCAL,
                routing_mode=RoutingMode.LOCAL_ONLY,
                sensitivity_level=SensitivityLevel.LOW,
                reason=f"Workspace '{workspace_id}' is configured as local_only; cloud egress forbidden",
                scrubbed_prompt=prompt,
            )

        # 2. Granular consent check
        from app.security.consent import is_consent_granted
        has_consent = (
            is_consent_granted("cloud_llm", purpose="cloud_reasoning")
            or is_consent_granted("ai_inference", purpose="cloud_reasoning")
            or is_consent_granted("cloud_llm", purpose="reasoning")
            or is_consent_granted("ai_inference", purpose="reasoning")
        )
        if not has_consent:
            return RoutingDecision(
                target_provider=ProviderType.LOCAL,
                routing_mode=RoutingMode.LOCAL_ONLY,
                sensitivity_level=SensitivityLevel.LOW,
                reason="Explicit user consent for cloud_reasoning is not granted; routing locally",
                scrubbed_prompt=prompt,
            )

        # 3. Daily cloud budget cap check
        if self.budget_manager.is_budget_exceeded():
            return RoutingDecision(
                target_provider=ProviderType.LOCAL,
                routing_mode=RoutingMode.LOCAL_ONLY,
                sensitivity_level=SensitivityLevel.LOW,
                reason="Daily cloud budget cap exceeded; routing locally",
                scrubbed_prompt=prompt,
            )

        # 4. Privacy Gatekeeper inspection
        entities = self.gatekeeper.detect_pii(prompt)
        sensitivity = self.gatekeeper.assess_sensitivity(entities)

        if sensitivity == SensitivityLevel.CRITICAL:
            return RoutingDecision(
                target_provider=ProviderType.LOCAL,
                routing_mode=RoutingMode.LOCAL_ONLY,
                sensitivity_level=sensitivity,
                reason="Critical secrets/credentials detected in prompt; cloud egress blocked for privacy",
                scrubbed_prompt=prompt,
                pii_detected=entities,
            )

        # De-identification via request-scoped SurrogateVault if PII is detected
        scrubbed = prompt
        deidentified = False
        vault_id = None

        if entities and self.pii_redaction_enabled and vault is not None:
            scrubbed = vault.deidentify(prompt, entities)
            deidentified = True
            vault_id = vault.vault_id

        # 5. Routing Mode enforcement
        if self.routing_mode == RoutingMode.LOCAL_ONLY:
            return RoutingDecision(
                target_provider=ProviderType.LOCAL,
                routing_mode=RoutingMode.LOCAL_ONLY,
                sensitivity_level=sensitivity,
                reason="System routing mode is LOCAL_ONLY",
                scrubbed_prompt=scrubbed,
                pii_detected=entities,
                deidentified=deidentified,
                vault_id=vault_id,
            )

        return RoutingDecision(
            target_provider=ProviderType.CLOUD,
            routing_mode=self.routing_mode,
            sensitivity_level=sensitivity,
            reason="All privacy, workspace, consent, and budget gates passed; approved for cloud egress",
            scrubbed_prompt=scrubbed,
            pii_detected=entities,
            deidentified=deidentified,
            vault_id=vault_id,
        )

    def route_and_generate(
        self,
        prompt: str,
        workspace_id: str = "default",
        user_id: str = "system",
        **kwargs: Any,
    ) -> str:
        """
        Execute request-scoped routing and text generation, guaranteeing vault cleanup
        and graceful fallback cascade.
        """
        vault = SurrogateVault() if self.pii_redaction_enabled else None
        try:
            decision = self.evaluate_route(
                prompt=prompt,
                workspace_id=workspace_id,
                user_id=user_id,
                vault=vault,
                **kwargs,
            )

            if decision.target_provider == ProviderType.LOCAL:
                resp = self.local_provider.generate(decision.scrubbed_prompt, **kwargs)
                final_text = resp.text
                if decision.deidentified and vault:
                    final_text = vault.reidentify(final_text)
                return final_text

            # ProviderType.CLOUD
            try:
                resp = self.cloud_provider.generate(decision.scrubbed_prompt, **kwargs)
                self.budget_manager.record_usage(
                    tokens_in=resp.tokens_in,
                    tokens_out=resp.tokens_out,
                    cost_usd=resp.cost_usd,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    model=resp.model,
                )
                final_text = resp.text
                if decision.deidentified and vault:
                    final_text = vault.reidentify(final_text)
                return final_text
            except Exception as e:
                logger.warning(f"Cloud provider execution failed ({e}); cascading to local provider")
                resp = self.local_provider.generate(prompt, **kwargs)
                return resp.text
        finally:
            if vault:
                vault.clear()


# Global Singleton & Factory
_router_instance: Optional[HybridRouter] = None


def get_hybrid_router() -> HybridRouter:
    """Retrieve or lazily initialize the singleton HybridRouter instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = HybridRouter()
    return _router_instance


def reset_hybrid_router() -> None:
    """Reset the singleton HybridRouter instance (primarily for testing)."""
    global _router_instance
    _router_instance = None
