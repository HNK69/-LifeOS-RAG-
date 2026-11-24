"""
logging.py

Production structured logging with automated secret masking for LifeOS.
Supports standard readable text format and structured JSON logging.
Protects against credential leakage (API keys, bearer tokens, passwords, private keys).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

# Regex patterns for detecting credentials and sensitive tokens in log text
SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|password|passwd|token|ipc_token|private[_-]?key)\b\s*[:=]\s*['\"]?([^\s'\"]+)['\"]?"),
    re.compile(r"(?i)\b(bearer)\s+([a-zA-Z0-9_\-\.]+)"),
    re.compile(r"(?i)\b(sk-[a-zA-Z0-9_\-]{16,})"),
]


def mask_sensitive_text(text: str) -> str:
    """Mask known credential and secret patterns from a string."""
    if not text:
        return text
    masked = text
    for pattern in SECRET_PATTERNS:
        def _replace_match(m):
            if m.lastindex == 2:
                prefix = m.group(1)
                full = m.group(0)
                if ":" in full:
                    return f"{prefix}: [REDACTED]"
                elif "=" in full:
                    return f"{prefix}=[REDACTED]"
                else:
                    return f"{prefix} [REDACTED]"
            return "[REDACTED]"
        masked = pattern.sub(_replace_match, masked)
    return masked


class SecretMaskingFilter(logging.Filter):
    """Logging filter that scrubs secrets, credentials, and tokens from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_sensitive_text(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (mask_sensitive_text(str(v)) if isinstance(v, str) else v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_sensitive_text(str(arg)) if isinstance(arg, str) else arg for arg in record.args)

        if record.exc_text:
            record.exc_text = mask_sensitive_text(record.exc_text)

        return True


class JSONFormatter(logging.Formatter):
    """Formats Python log records into structured, machine-readable JSON strings."""

    def format(self, record: logging.LogRecord) -> str:
        msg = mask_sensitive_text(record.getMessage())
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": "lifeos",
            "message": msg,
        }

        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id

        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms

        if hasattr(record, "client_ip"):
            log_entry["client_ip"] = record.client_ip

        if record.exc_info and record.exc_text:
            log_entry["exception"] = mask_sensitive_text(record.exc_text)

        return json.dumps(log_entry)


def setup_logging(log_format: str = "text", mask_secrets: bool = True) -> None:
    """Configure application logging format ('text' or 'json') with secret masking."""
    root_logger = logging.getLogger()
    handler = None
    for h in root_logger.handlers:
        if isinstance(h, logging.StreamHandler):
            handler = h
            break

    if handler is None:
        handler = logging.StreamHandler()
        root_logger.addHandler(handler)

    if mask_secrets:
        if not any(isinstance(f, SecretMaskingFilter) for f in handler.filters):
            handler.addFilter(SecretMaskingFilter())

    if log_format.lower() == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
