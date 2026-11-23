"""
routing.py

REST API endpoints for Milestone 7: Hybrid Local-Cloud Routing Policy,
Privacy Gatekeeper Inspection, and Token/Spend Metrics.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import AuthenticatedUser
from app.api.dependencies import require_admin, require_auth
from app.api.schemas import (
    DetectedPIIItem,
    GatekeeperInspectRequest,
    GatekeeperInspectResponse,
    RoutingMetricsResponse,
    RoutingPolicyResponse,
    RoutingPolicyUpdateRequest,
)
from app.routing.gatekeeper import SurrogateVault
from app.routing.hybrid_router import get_hybrid_router
from app.routing.models import RoutingMode, SensitivityLevel
from app.security.audit import record_audit_event

try:
    from config import LOCAL_LLM_MODEL, LOCAL_LLM_URL
except ImportError:
    from app.config import LOCAL_LLM_MODEL, LOCAL_LLM_URL

logger = logging.getLogger("lifeos.api.routing")

router = APIRouter(prefix="/api/routing", tags=["Hybrid Routing & Privacy Gatekeeper"])


@router.get("/policy", response_model=RoutingPolicyResponse)
def get_routing_policy(
    user: AuthenticatedUser = Depends(require_auth),
) -> RoutingPolicyResponse:
    """Retrieve active hybrid routing policy and gatekeeper settings."""
    hr = get_hybrid_router()
    return RoutingPolicyResponse(
        routing_mode=hr.routing_mode.value,
        local_llm_url=LOCAL_LLM_URL,
        local_llm_model=LOCAL_LLM_MODEL,
        daily_budget_usd=hr.budget_manager.daily_budget_usd,
        pii_redaction_enabled=hr.pii_redaction_enabled,
    )


@router.put("/policy", response_model=RoutingPolicyResponse)
def update_routing_policy(
    body: RoutingPolicyUpdateRequest,
    user: AuthenticatedUser = Depends(require_admin),
) -> RoutingPolicyResponse:
    """Update hybrid routing mode and budget limits (Admin only)."""
    hr = get_hybrid_router()

    if body.routing_mode is not None:
        hr.routing_mode = RoutingMode(body.routing_mode)

    if body.daily_budget_usd is not None:
        hr.budget_manager.daily_budget_usd = body.daily_budget_usd

    if body.pii_redaction_enabled is not None:
        hr.pii_redaction_enabled = body.pii_redaction_enabled

    record_audit_event(
        event_type="routing_policy_update",
        actor=user.user_id,
        resource="routing_policy",
        purpose="policy_administration",
        status="success",
        details={
            "routing_mode": hr.routing_mode.value,
            "daily_budget_usd": hr.budget_manager.daily_budget_usd,
            "pii_redaction_enabled": hr.pii_redaction_enabled,
        },
    )

    return RoutingPolicyResponse(
        routing_mode=hr.routing_mode.value,
        local_llm_url=LOCAL_LLM_URL,
        local_llm_model=LOCAL_LLM_MODEL,
        daily_budget_usd=hr.budget_manager.daily_budget_usd,
        pii_redaction_enabled=hr.pii_redaction_enabled,
    )


@router.post("/gatekeeper/inspect", response_model=GatekeeperInspectResponse)
def inspect_text_privacy(
    body: GatekeeperInspectRequest,
    user: AuthenticatedUser = Depends(require_auth),
) -> GatekeeperInspectResponse:
    """Inspect text for PII entities, assess sensitivity, and preview de-identification."""
    hr = get_hybrid_router()
    entities = hr.gatekeeper.detect_pii(body.text)
    sensitivity = hr.gatekeeper.assess_sensitivity(entities)

    vault = SurrogateVault()
    try:
        scrubbed = vault.deidentify(body.text, entities)
        cloud_allowed = (sensitivity != SensitivityLevel.CRITICAL) and (hr.routing_mode != RoutingMode.LOCAL_ONLY)
        reason = (
            "Cloud egress blocked: critical secrets/credentials detected"
            if sensitivity == SensitivityLevel.CRITICAL
            else ("Cloud egress blocked: system configured as LOCAL_ONLY" if hr.routing_mode == RoutingMode.LOCAL_ONLY else "Cloud egress permitted with gatekeeper scrub")
        )

        detected_items = [
            DetectedPIIItem(
                category=e.category.value,
                start=e.start,
                end=e.end,
                placeholder=e.placeholder,
            )
            for e in entities
        ]

        return GatekeeperInspectResponse(
            entities=detected_items,
            sensitivity_level=sensitivity.value,
            scrubbed_text=scrubbed,
            cloud_egress_allowed=cloud_allowed,
            reason=reason,
        )
    finally:
        vault.clear()


@router.get("/metrics", response_model=RoutingMetricsResponse)
def get_routing_metrics(
    user: AuthenticatedUser = Depends(require_auth),
) -> RoutingMetricsResponse:
    """Retrieve daily token consumption and estimated expenditure metrics."""
    hr = get_hybrid_router()
    metrics = hr.budget_manager.get_metrics()
    return RoutingMetricsResponse(
        date=metrics["date"],
        total_tokens_in=metrics["total_tokens_in"],
        total_tokens_out=metrics["total_tokens_out"],
        total_cost_usd=metrics["total_cost_usd"],
        daily_budget_usd=metrics["daily_budget_usd"],
        request_count=metrics["request_count"],
        is_budget_exceeded=metrics["is_budget_exceeded"],
    )
