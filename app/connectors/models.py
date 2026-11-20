"""
models.py

Pydantic data models for the LifeOS Extensible Connector Architecture.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ConnectorItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    source_type: str
    uri: str
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    discovered_at: str = ""


class ConnectorContent(BaseModel):
    model_config = ConfigDict(extra="allow")

    item_id: str
    raw_bytes: bytes | None = None
    text_content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SyncResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    connector_name: str
    source_type: str
    status: str = "success"  # success, partial, failed
    items_discovered: int = 0
    items_synced: int = 0
    items_failed: int = 0
    errors: list[str] = Field(default_factory=list)
    synced_at: str = ""


class ConnectorStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    source_type: str
    enabled: bool = True
    configured: bool = True
    last_sync_at: str | None = None
    item_count: int = 0
    description: str = ""
