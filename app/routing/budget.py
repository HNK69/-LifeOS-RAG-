"""
budget.py

Cumulative daily token and spend accumulator for Milestone 7.
Enforces hard spend caps before cloud LLM egress and logs cryptographic audit events.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from config import DAILY_CLOUD_BUDGET_USD, ROUTING_BUDGET_FILE
except ImportError:
    from app.config import DAILY_CLOUD_BUDGET_USD, ROUTING_BUDGET_FILE

logger = logging.getLogger("lifeos.routing.budget")


class BudgetManager:
    """
    Manages daily cloud API budget enforcement and usage auditing.
    """

    def __init__(
        self,
        daily_budget_usd: Optional[float] = None,
        budget_file: Optional[Path | str] = None,
    ):
        self.daily_budget_usd = (
            daily_budget_usd
            if daily_budget_usd is not None
            else float(DAILY_CLOUD_BUDGET_USD)
        )
        self.budget_file = (
            Path(budget_file).resolve()
            if budget_file is not None
            else Path(ROUTING_BUDGET_FILE).resolve()
        )
        self._lock = threading.Lock()
        self._ensure_budget_file()

    def _get_today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_budget_file(self) -> None:
        self.budget_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.budget_file.exists():
            initial_state = {
                "date": self._get_today_str(),
                "total_tokens_in": 0,
                "total_tokens_out": 0,
                "total_cost_usd": 0.0,
                "request_count": 0,
            }
            try:
                self.budget_file.write_text(json.dumps(initial_state, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to initialize budget file: {e}")

    def _load_state(self) -> Dict[str, Any]:
        today = self._get_today_str()
        if self.budget_file.exists():
            try:
                data = json.loads(self.budget_file.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    return data
            except Exception as e:
                logger.warning(f"Error reading budget file: {e}")

        # Rollover to a new day
        return {
            "date": today,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_cost_usd": 0.0,
            "request_count": 0,
        }

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            self.budget_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to persist budget state: {e}")

    def is_budget_exceeded(self, additional_cost_usd: float = 0.0) -> bool:
        """Check if daily cloud spending exceeds the configured threshold."""
        with self._lock:
            state = self._load_state()
            current_spend = state.get("total_cost_usd", 0.0)
            return (current_spend + additional_cost_usd) >= self.daily_budget_usd

    def estimate_cost(
        self, tokens_in: int, tokens_out: int, model: Optional[str] = None
    ) -> float:
        """
        Estimate USD cost for a given token count.
        Default baseline rates: $0.0015 / 1k input tokens, $0.0020 / 1k output tokens.
        """
        input_cost = (tokens_in / 1000.0) * 0.0015
        output_cost = (tokens_out / 1000.0) * 0.0020
        return round(input_cost + output_cost, 6)

    def record_usage(
        self,
        tokens_in: int,
        tokens_out: int,
        cost_usd: Optional[float] = None,
        workspace_id: str = "default",
        user_id: str = "system",
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record usage, persist daily totals, and trigger cryptographic audit event."""
        if cost_usd is None:
            cost_usd = self.estimate_cost(tokens_in, tokens_out, model)

        with self._lock:
            state = self._load_state()
            state["total_tokens_in"] = state.get("total_tokens_in", 0) + tokens_in
            state["total_tokens_out"] = state.get("total_tokens_out", 0) + tokens_out
            state["total_cost_usd"] = round(state.get("total_cost_usd", 0.0) + cost_usd, 6)
            state["request_count"] = state.get("request_count", 0) + 1
            self._save_state(state)

        # Audit logging: strictly numeric token counts and model; ZERO raw prompt/PII
        try:
            from app.security.audit import record_audit_event
            record_audit_event(
                event_type="cloud_llm_egress",
                actor=user_id or "system",
                resource="cloud_llm",
                purpose="ai_inference",
                status="success",
                details={
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": round(cost_usd, 6),
                    "model": model or "",
                    "daily_total_cost_usd": round(state["total_cost_usd"], 6),
                },
                workspace_id=workspace_id,
            )
        except Exception as e:
            logger.warning(f"Audit event recording failed for budget usage: {e}")

        return state

    def get_metrics(self) -> Dict[str, Any]:
        """Return the current daily spending and token metrics."""
        with self._lock:
            state = self._load_state()
            return {
                "date": state.get("date"),
                "total_tokens_in": state.get("total_tokens_in", 0),
                "total_tokens_out": state.get("total_tokens_out", 0),
                "total_cost_usd": state.get("total_cost_usd", 0.0),
                "daily_budget_usd": self.daily_budget_usd,
                "request_count": state.get("request_count", 0),
                "is_budget_exceeded": state.get("total_cost_usd", 0.0) >= self.daily_budget_usd,
            }
