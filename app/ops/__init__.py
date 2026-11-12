"""
app.ops: Operational tooling, observability, metrics, and structured logging for LifeOS Phase 12.
"""

from app.ops.metrics import metrics_collector
from app.ops.logging import setup_logging, JSONFormatter

__all__ = ["metrics_collector", "setup_logging", "JSONFormatter"]
