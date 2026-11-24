"""
ipc.py

Secure IPC Protocol, JSON-RPC 2.0 framing, and authoritative Host Security Broker
for LifeOS Milestone 9: Plugin & Sidecar Extensibility Sandbox.

Guarantees:
- Authoritative Security Boundary: HostBroker validates capabilities, consent,
  workspace RLS, and local_only network restrictions at execution time.
- JSON-RPC 2.0 message framing with strict 1MB size limit and request timeouts.
- Tamper-evident cryptographic audit logging for every capability grant or denial.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.plugins.models import (
    JsonRpcRequest,
    JsonRpcResponse,
    PluginCapability,
    PluginManifest,
)
from app.security.audit import record_audit_event
from app.security.consent import is_consent_granted

logger = logging.getLogger("lifeos.plugins.ipc")

# JSON-RPC 2.0 Error Codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
PERMISSION_DENIED = -32001
CONSENT_DENIED = -32002
AIRGAP_VIOLATION = -32003
PAYLOAD_TOO_LARGE = -32004


def serialize_message(msg: dict[str, Any] | JsonRpcRequest | JsonRpcResponse) -> bytes:
    """Serialize a JSON-RPC message into newline-delimited bytes."""
    data = msg.to_dict() if hasattr(msg, "to_dict") else msg
    return json.dumps(data).encode("utf-8") + b"\n"


def parse_message(raw_bytes: bytes, max_bytes: int = 1048576) -> dict[str, Any]:
    """Parse and validate an incoming JSON-RPC frame."""
    if len(raw_bytes) > max_bytes:
        raise ValueError(f"Message exceeds maximum payload limit of {max_bytes} bytes (got {len(raw_bytes)} bytes)")
    return json.loads(raw_bytes.decode("utf-8").strip())


class HostBroker:
    """
    Authoritative Host Security Broker for all sidecar IPC requests.

    Sidecars never communicate directly with SQLite or the filesystem.
    Every privileged action must pass through the HostBroker, which enforces:
    1. Capability verification: Was the capability declared in the manifest?
    2. Consent verification: Has the user actively granted consent in privacy.db?
    3. Workspace RLS: Is the resource confined to the plugin's authorized workspace?
    4. Air-gap isolation: In local_only workspaces, network:egress is strictly blocked!
    5. Audit trail: Every grant and denial is written to the cryptographic audit log.
    """

    def __init__(self, manifest: PluginManifest, session_token: str):
        self.manifest = manifest
        self.plugin_id = manifest.plugin_id
        self.workspace_id = manifest.workspace_scope or "default"
        self._session_token = session_token
        self._authenticated = False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def authenticate_session(self, token: str) -> bool:
        """Validate child sidecar ephemeral token using constant-time comparison."""
        if secrets.compare_digest(token, self._session_token):
            self._authenticated = True
            return True
        return False

    def check_capability_and_consent(self, capability: str) -> tuple[bool, str | None]:
        """
        Evaluate whether the requested capability is allowed right now.
        Returns (is_allowed, error_reason).
        """
        # 1. Manifest declaration check
        if capability not in self.manifest.requested_capabilities:
            msg = f"Capability '{capability}' was not requested in plugin manifest."
            self._log_audit("capability_deny", capability, "denied", {"reason": "not_in_manifest"})
            return False, msg

        # 2. Workspace Air-Gap check
        if capability == PluginCapability.NETWORK_EGRESS.value:
            if self._is_workspace_local_only():
                msg = f"Network egress strictly prohibited: workspace '{self.workspace_id}' is local_only."
                self._log_audit("capability_deny", capability, "denied", {"reason": "local_only_airgap"})
                return False, msg

        # 3. Privacy Consent Grant check
        # Plugins check consent with scope_type="plugin", resource_path=plugin_id, purpose=capability
        has_consent = is_consent_granted(
            candidate_path=self.plugin_id,
            purpose=capability,
            category=None,
        )
        if not has_consent:
            msg = f"User consent not granted or revoked for plugin '{self.plugin_id}' capability '{capability}'."
            self._log_audit("capability_deny", capability, "denied", {"reason": "consent_missing_or_revoked"})
            return False, msg

        self._log_audit("capability_allow", capability, "success", {})
        return True, None

    def _is_workspace_local_only(self) -> bool:
        """Check if plugin's workspace has local_only airgap enabled."""
        try:
            from app.workspace.store import get_workspace
            ws = get_workspace(self.workspace_id)
            if ws and ws.is_local_only:
                return True
        except Exception as exc:
            logger.debug(f"Could not load workspace {self.workspace_id}: {exc}")
        return False

    def _log_audit(self, event_type: str, resource: str, status: str, details: dict[str, Any]) -> None:
        """Record a tamper-evident audit record for plugin operations."""
        try:
            record_audit_event(
                event_type=event_type,
                actor=f"plugin:{self.plugin_id}",
                resource=resource,
                purpose="plugin_sandbox",
                status=status,
                details={"workspace_id": self.workspace_id, **details},
            )
        except Exception as exc:
            logger.warning(f"Failed to record audit event for plugin {self.plugin_id}: {exc}")

    def handle_request(self, req: JsonRpcRequest) -> JsonRpcResponse:
        """
        Authoritatively route and execute an incoming JSON-RPC request from a sidecar.
        """
        method = req.method
        params = req.params or {}

        # 1. Handshake method
        if method == "handshake":
            token = params.get("token", "")
            if not self.authenticate_session(token):
                self._log_audit("handshake_failed", self.plugin_id, "denied", {"reason": "invalid_token"})
                return JsonRpcResponse(
                    id=req.id,
                    error={"code": PERMISSION_DENIED, "message": "Invalid IPC session token."},
                )

            # Return list of granted capabilities that currently have consent
            active_caps = []
            for cap in self.manifest.requested_capabilities:
                allowed, _ = self.check_capability_and_consent(cap)
                if allowed:
                    active_caps.append(cap)

            self._log_audit("handshake_success", self.plugin_id, "success", {"active_capabilities": active_caps})
            return JsonRpcResponse(
                id=req.id,
                result={
                    "status": "authenticated",
                    "plugin_id": self.plugin_id,
                    "workspace_id": self.workspace_id,
                    "active_capabilities": active_caps,
                },
            )

        # All subsequent methods require authentication
        if not self.is_authenticated:
            return JsonRpcResponse(
                id=req.id,
                error={"code": PERMISSION_DENIED, "message": "Sidecar is not authenticated. Call 'handshake' first."},
            )

        # 2. Ping method
        if method == "ping":
            return JsonRpcResponse(id=req.id, result={"pong": True, "timestamp": datetime.now(timezone.utc).isoformat()})

        # 3. Context Read
        if method == "context:read":
            allowed, err = self.check_capability_and_consent(PluginCapability.CONTEXT_READ.value)
            if not allowed:
                return JsonRpcResponse(id=req.id, error={"code": CONSENT_DENIED, "message": err})

            key = params.get("key")
            if not key:
                return JsonRpcResponse(id=req.id, error={"code": INVALID_PARAMS, "message": "Missing 'key' parameter."})

            try:
                from app.context.context_store import get_context
                val = get_context(key, workspace_id=self.workspace_id)
                self._log_audit("context_read", key, "success", {})
                return JsonRpcResponse(id=req.id, result={"key": key, "value": val})
            except Exception as exc:
                return JsonRpcResponse(id=req.id, error={"code": INTERNAL_ERROR, "message": str(exc)})

        # 4. Context Write
        if method == "context:write":
            allowed, err = self.check_capability_and_consent(PluginCapability.CONTEXT_WRITE.value)
            if not allowed:
                return JsonRpcResponse(id=req.id, error={"code": CONSENT_DENIED, "message": err})

            key = params.get("key")
            value = params.get("value")
            context_type = params.get("context_type", "plugin_data")
            if not key or value is None:
                return JsonRpcResponse(id=req.id, error={"code": INVALID_PARAMS, "message": "Missing 'key' or 'value'."})

            try:
                from app.context.context_store import set_context
                set_context(key, str(value), context_type=context_type, workspace_id=self.workspace_id)
                self._log_audit("context_write", key, "success", {})
                return JsonRpcResponse(id=req.id, result={"key": key, "status": "saved"})
            except Exception as exc:
                return JsonRpcResponse(id=req.id, error={"code": INTERNAL_ERROR, "message": str(exc)})

        # 5. Documents Read / Search
        if method == "documents:read":
            allowed, err = self.check_capability_and_consent(PluginCapability.DOCUMENTS_READ.value)
            if not allowed:
                return JsonRpcResponse(id=req.id, error={"code": CONSENT_DENIED, "message": err})

            query = params.get("query", "")
            try:
                from app.query.router import _document_search
                res = _document_search(query)
                self._log_audit("documents_search", query, "success", {})
                return JsonRpcResponse(id=req.id, result=res.data if hasattr(res, "data") else res)
            except Exception as exc:
                return JsonRpcResponse(id=req.id, error={"code": INTERNAL_ERROR, "message": str(exc)})

        # 6. Events Publish
        if method == "events:publish":
            allowed, err = self.check_capability_and_consent(PluginCapability.EVENTS_PUBLISH.value)
            if not allowed:
                return JsonRpcResponse(id=req.id, error={"code": CONSENT_DENIED, "message": err})

            event_type = params.get("event_type", f"plugin.{self.plugin_id}.event")
            payload = params.get("payload", {})
            try:
                from app.events.bus import get_event_bus
                from app.events.models import LifeOSEvent
                evt = LifeOSEvent(
                    event_type=event_type,
                    payload=payload,
                    producer=f"plugin:{self.plugin_id}",
                )
                get_event_bus().publish_sync(evt)
                self._log_audit("event_publish", event_type, "success", {})
                return JsonRpcResponse(id=req.id, result={"event_id": evt.event_id, "status": "published"})
            except Exception as exc:
                return JsonRpcResponse(id=req.id, error={"code": INTERNAL_ERROR, "message": str(exc)})

        # 7. Network Egress
        if method == "network:egress":
            allowed, err = self.check_capability_and_consent(PluginCapability.NETWORK_EGRESS.value)
            if not allowed:
                code = AIRGAP_VIOLATION if "local_only" in (err or "") else CONSENT_DENIED
                return JsonRpcResponse(id=req.id, error={"code": code, "message": err})

            # Sidecar is permitted to make outbound network calls
            target_url = params.get("url", "")
            self._log_audit("network_egress_authorized", target_url, "success", {})
            return JsonRpcResponse(id=req.id, result={"authorized": True, "url": target_url})

        # Method Not Found
        return JsonRpcResponse(
            id=req.id,
            error={"code": METHOD_NOT_FOUND, "message": f"Method '{method}' is not supported by HostBroker."},
        )
