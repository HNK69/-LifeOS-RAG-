"""
models.py

Event data contracts and typed representations for the LifeOS Event-Driven Architecture.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class LifeOSEvent:
    """
    Represents an atomic, typed event broadcast across LifeOS.
    """
    event_type: str                         # e.g. 'ingestion.queued', 'ingestion.completed', 'watcher.change', etc.
    payload: dict[str, Any]                 # Event-specific payload data
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    producer: str = "system"                # 'queue', 'watcher', 'query', 'privacy', 'device', etc.
    resource_path: str | None = None        # Associated filesystem path for privacy-filtering

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_sse(self) -> str:
        """Serialize event into standard Server-Sent Event (SSE) wire format."""
        data_json = json.dumps(self.to_dict(), separators=(",", ":"))
        return f"id: {self.event_id}\nevent: {self.event_type}\ndata: {data_json}\n\n"
