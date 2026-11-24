"""
models.py

Domain models, capability definitions, and JSON-RPC contracts for
LifeOS Milestone 9: Plugin & Sidecar Extensibility Sandbox.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class PluginCapability(str, Enum):
    """Fine-grained capabilities that a plugin may request."""
    TOOLS_REGISTER = "tools:register"
    CONTEXT_READ = "context:read"
    CONTEXT_WRITE = "context:write"
    DOCUMENTS_READ = "documents:read"
    EVENTS_SUBSCRIBE = "events:subscribe"
    EVENTS_PUBLISH = "events:publish"
    NETWORK_EGRESS = "network:egress"


class PluginState(str, Enum):
    """Lifecycle states for a plugin sidecar."""
    REGISTERED = "registered"
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPED = "stopped"
    CRASHED = "crashed"
    DISABLED = "disabled"


class ToolDeclaration(BaseModel):
    """Metadata describing a tool exported by a plugin sidecar."""
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class PluginManifest(BaseModel):
    """Declarative capability manifest for a LifeOS plugin."""
    plugin_id: str = Field(..., min_length=1, pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    description: str = ""
    author: str = ""
    entrypoint: str = Field(..., min_length=1)
    workspace_scope: str | None = None
    requested_capabilities: list[str] = Field(default_factory=list)
    tools: list[ToolDeclaration] = Field(default_factory=list)

    @field_validator("requested_capabilities")
    @classmethod
    def validate_capabilities(cls, caps: list[str]) -> list[str]:
        valid = {c.value for c in PluginCapability}
        for cap in caps:
            if cap not in valid:
                raise ValueError(f"Unknown capability '{cap}'. Allowed: {sorted(valid)}")
        return caps


@dataclass
class PluginStatus:
    """Runtime status and observability metrics for a plugin."""
    plugin_id: str
    state: PluginState
    pid: int | None = None
    workspace_scope: str | None = None
    active_capabilities: list[str] = field(default_factory=list)
    crash_count: int = 0
    circuit_breaker_tripped: bool = False
    last_started_at: str | None = None
    last_stopped_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value if isinstance(self.state, PluginState) else str(self.state)
        return d


# ============================================================
# JSON-RPC 2.0 PROTOCOL SCHEMAS
# ============================================================

@dataclass
class JsonRpcRequest:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method, "params": self.params}
        if self.id is not None:
            d["id"] = self.id
        return d


@dataclass
class JsonRpcError:
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclass
class JsonRpcNotification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": self.jsonrpc, "method": self.method, "params": self.params}


@dataclass
class JsonRpcResponse:
    result: Any = None
    error: dict[str, Any] | None = None
    id: str | int | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.id is not None:
            d["id"] = self.id
        if self.error is not None:
            d["error"] = self.error
        else:
            d["result"] = self.result
        return d
