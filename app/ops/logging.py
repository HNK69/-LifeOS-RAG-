"""
logging.py

Production structured logging for LifeOS Phase 12.
Supports standard readable text format and structured JSON logging.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats Python log records into structured, machine-readable JSON strings."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": "lifeos",
            "message": record.getMessage(),
        }

        # Include request_id and metadata if available
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id

        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms

        if hasattr(record, "client_ip"):
            log_entry["client_ip"] = record.client_ip

        if record.exc_info and record.exc_text:
            log_entry["exception"] = record.exc_text

        return json.dumps(log_entry)


def setup_logging(log_format: str = "text") -> None:
    """Configure application logging format ('text' or 'json')."""
    root_logger = logging.getLogger()
    handler = None
    for h in root_logger.handlers:
        if isinstance(h, logging.StreamHandler):
            handler = h
            break

    if handler is None:
        handler = logging.StreamHandler()
        root_logger.addHandler(handler)

    if log_format.lower() == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
