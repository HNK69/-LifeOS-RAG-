"""
metrics.py

Operational metrics endpoint for LifeOS Phase 12.
Requires administrative privileges to prevent unauthorized disclosure of
performance metrics or traffic profiles.
"""

from typing import Any
from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin
from app.ops.metrics import metrics_collector

router = APIRouter(prefix="", tags=["System"])


@router.get(
    "/metrics",
    summary="Retrieve Operational Metrics",
    description="Returns performance, request counts, error distributions, and subsystem states. Admin required.",
)
async def get_system_metrics(_admin=Depends(require_admin)) -> dict[str, Any]:
    return metrics_collector.get_metrics()
