"""
diagnostics.py

Diagnostics Center and Resource Governor telemetry API endpoints (Milestone C4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_auth
from app.api.schemas import (
    DiagnosticCheckResponse,
    DiagnosticReportResponse,
    GovernorMetricsResponse,
)
from app.ops.doctor import run_diagnostics
from app.ops.governor import get_governor

router = APIRouter(prefix="/api/diagnostics", tags=["Diagnostics"])


@router.get(
    "",
    response_model=DiagnosticReportResponse,
    summary="Run Pre-Flight System Diagnostics",
    description="Executes pre-flight diagnostic checks across databases, storage, security, vector store, config, and network.",
)
async def get_diagnostics_report(
    request: Request,
    strict: bool = Query(False, description="Treat warnings as failures when computing overall_status"),
    _user=Depends(require_auth),
) -> DiagnosticReportResponse:
    port_override = request.url.port if request.url.port else None
    report = run_diagnostics(strict_mode=strict, in_process=True, port_override=port_override)
    checks = [
        DiagnosticCheckResponse(
            name=c.name,
            category=c.category,
            status=c.status,
            message=c.message,
            details=c.details,
            fix_hint=c.remediation,
        )
        for c in report.checks
    ]
    passed = sum(1 for c in checks if c.status == "OK")
    warnings = sum(1 for c in checks if c.status == "WARN")
    failed = sum(1 for c in checks if c.status == "FAIL")

    return DiagnosticReportResponse(
        checks=checks,
        overall_status=report.overall_status,
        total_checks=len(checks),
        passed=passed,
        warnings=warnings,
        failed=failed,
        timestamp=report.timestamp,
    )


@router.get(
    "/governor",
    response_model=GovernorMetricsResponse,
    summary="Get Resource Governor Telemetry",
    description="Samples current CPU, RAM, battery state, and background ingestion throttling status.",
)
async def get_governor_diagnostics(
    _user=Depends(require_auth),
) -> GovernorMetricsResponse:
    gov = get_governor()
    metrics = gov.sample_metrics()
    should_pause, pause_reason = gov.should_pause_ingestion()

    return GovernorMetricsResponse(
        cpu_percent=round(float(metrics.cpu_percent), 2),
        ram_percent=round(float(metrics.ram_percent), 2),
        on_battery=bool(metrics.on_battery),
        battery_percent=metrics.battery_percent,
        should_pause_ingestion=bool(should_pause),
        pause_reason=pause_reason,
    )
