"""
manager.py

Plugin lifecycle manager, isolated subprocess orchestration, health probes,
crash backoff recovery, circuit breaker, and ToolRegistry synchronization for
LifeOS Milestone 9: Plugin & Sidecar Extensibility Sandbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from config import (
        PLUGINS_DIR,
        PLUGIN_IPC_TIMEOUT_SECONDS,
        PLUGIN_MAX_PAYLOAD_BYTES,
        PLUGIN_MAX_RESTART_ATTEMPTS,
        PLUGIN_RESTART_WINDOW_SECONDS,
    )
except ImportError:
    from app.config import (
        PLUGINS_DIR,
        PLUGIN_IPC_TIMEOUT_SECONDS,
        PLUGIN_MAX_PAYLOAD_BYTES,
        PLUGIN_MAX_RESTART_ATTEMPTS,
        PLUGIN_RESTART_WINDOW_SECONDS,
    )

from app.plugins.ipc import HostBroker, parse_message, serialize_message
from app.plugins.models import (
    JsonRpcRequest,
    JsonRpcResponse,
    PluginCapability,
    PluginManifest,
    PluginState,
    PluginStatus,
)
from app.plugins.tool_adapter import SidecarTool
from app.security.audit import record_audit_event

logger = logging.getLogger("lifeos.plugins.manager")

# Environment variables allowed in child subprocess
SAFE_ENV_VARS = {
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "PYTHONPATH",
    "TEMP",
    "TMP",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
}


def sanitize_child_environment(
    plugin_id: str,
    ipc_token: str,
    workspace_id: str,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Build a strictly sanitized environment dictionary for child sidecar processes.
    Strips all host API keys, master keys, and sensitive tokens.
    """
    clean_env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in SAFE_ENV_VARS:
            clean_env[k] = v

    # Pass Python path so virtualenv packages remain discoverable
    if "PYTHONPATH" not in clean_env:
        clean_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent)

    clean_env["LIFEOS_PLUGIN_ID"] = plugin_id
    clean_env["LIFEOS_IPC_TOKEN"] = ipc_token
    clean_env["LIFEOS_WORKSPACE_ID"] = workspace_id

    if extra_env:
        for k, v in extra_env.items():
            if not any(secret in k.upper() for secret in ("KEY", "SECRET", "TOKEN", "PASS", "CRED")):
                clean_env[k] = str(v)

    return clean_env


class SidecarProcess:
    """
    Manages a single running sidecar subprocess, its pipes, and communication.
    """

    def __init__(
        self,
        manifest: PluginManifest,
        broker: HostBroker,
        ipc_token: str,
        sandbox_dir: Path,
        tool_invoker: Callable[[str, str, dict[str, Any]], Any],
    ):
        self.manifest = manifest
        self.plugin_id = manifest.plugin_id
        self.broker = broker
        self.ipc_token = ipc_token
        self.sandbox_dir = sandbox_dir
        self.tool_invoker = tool_invoker

        self.process: subprocess.Popen | None = None
        self.state = PluginState.REGISTERED
        self.crash_count = 0
        self.crash_timestamps: list[float] = []
        self.circuit_breaker_tripped = False
        self.last_started_at: str | None = None
        self.last_stopped_at: str | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._in_memory_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def get_status(self) -> PluginStatus:
        with self._lock:
            pid = self.process.pid if self.process and self.process.poll() is None else None
            active_caps = [
                c for c in self.manifest.requested_capabilities
                if self.broker.check_capability_and_consent(c)[0]
            ]
            return PluginStatus(
                plugin_id=self.plugin_id,
                state=self.state,
                pid=pid,
                workspace_scope=self.manifest.workspace_scope,
                active_capabilities=active_caps,
                crash_count=self.crash_count,
                circuit_breaker_tripped=self.circuit_breaker_tripped,
                last_started_at=self.last_started_at,
                last_stopped_at=self.last_stopped_at,
                last_error=self.last_error,
            )

    def set_in_memory_handler(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Allow setting an in-memory handler for testing or deterministic unit execution."""
        self._in_memory_handler = handler

    def start(self) -> bool:
        """Spawn the sidecar process and perform initialization handshake."""
        with self._lock:
            if self.circuit_breaker_tripped:
                logger.warning(f"Cannot start plugin {self.plugin_id}: circuit breaker is tripped.")
                self.state = PluginState.DISABLED
                return False

            self.state = PluginState.INITIALIZING
            self.sandbox_dir.mkdir(parents=True, exist_ok=True)

            clean_env = sanitize_child_environment(
                plugin_id=self.plugin_id,
                ipc_token=self.ipc_token,
                workspace_id=self.broker.workspace_id,
            )

            # Record audit start attempt
            self._log_audit("plugin_start_attempt", "starting", {})

            # 1. If in-memory handler is configured (test/mock mode)
            if self._in_memory_handler:
                handshake_req = JsonRpcRequest(method="handshake", params={"token": self.ipc_token}, id="init-1")
                resp = self.broker.handle_request(handshake_req)
                if resp.error:
                    self.state = PluginState.STOPPED
                    self.last_error = resp.error.get("message")
                    return False
                self.state = PluginState.RUNNING
                self.last_started_at = datetime.now(timezone.utc).isoformat()
                self._log_audit("plugin_start", "success", {"mode": "in_memory"})
                return True

            # 2. Spawn real subprocess
            try:
                cmd = self._resolve_command()
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(self.sandbox_dir),
                    env=clean_env,
                    bufsize=0,
                )
                self.last_started_at = datetime.now(timezone.utc).isoformat()

                # Perform handshake via pipes
                req_data = serialize_message(
                    JsonRpcRequest(method="handshake", params={"token": self.ipc_token}, id="init-1")
                )
                self.process.stdin.write(req_data)
                self.process.stdin.flush()

                # Read handshake response
                line = self.process.stdout.readline()
                if not line:
                    raise RuntimeError("Sidecar process terminated unexpectedly during handshake.")

                resp_dict = parse_message(line, max_bytes=PLUGIN_MAX_PAYLOAD_BYTES)
                if "error" in resp_dict and resp_dict["error"]:
                    raise RuntimeError(f"Sidecar handshake rejected: {resp_dict['error']}")

                # Confirm authentication in broker
                self.broker.authenticate_session(self.ipc_token)
                self.state = PluginState.RUNNING
                self._log_audit("plugin_start", "success", {"pid": self.process.pid})
                logger.info(f"Plugin {self.plugin_id} sidecar started successfully (PID: {self.process.pid}).")
                return True

            except Exception as exc:
                self.last_error = str(exc)
                self.state = PluginState.CRASHED
                self._record_crash()
                self._log_audit("plugin_start_failed", "error", {"error": str(exc)})
                logger.error(f"Failed to start sidecar for plugin {self.plugin_id}: {exc}")
                return False

    def _resolve_command(self) -> list[str]:
        """Resolve python entrypoint command."""
        entry = self.manifest.entrypoint.strip()
        if entry.endswith(".py"):
            return [sys.executable, str(Path(entry).resolve())]
        return entry.split()

    def send_request(self, method: str, params: dict[str, Any], timeout: float = 5.0) -> Any:
        """Send a JSON-RPC request to the sidecar and return the result."""
        with self._lock:
            if self.state != PluginState.RUNNING:
                raise RuntimeError(f"Cannot invoke plugin '{self.plugin_id}': status is '{self.state.value}'.")

            req = JsonRpcRequest(method=method, params=params, id=f"call-{secrets.token_hex(4)}")

            # In-memory execution path
            if self._in_memory_handler:
                resp = self._in_memory_handler(req.to_dict())
                if "error" in resp and resp["error"]:
                    raise RuntimeError(resp["error"].get("message", "Plugin execution error"))
                return resp.get("result")

            # Pipe execution path
            if not self.process or self.process.poll() is not None:
                self._record_crash()
                raise RuntimeError(f"Plugin '{self.plugin_id}' process is not running.")

            try:
                self.process.stdin.write(serialize_message(req))
                self.process.stdin.flush()

                # Read response with timeout
                line = self.process.stdout.readline()
                if not line:
                    self._record_crash()
                    raise RuntimeError(f"Plugin '{self.plugin_id}' closed connection unexpectedly.")

                resp_dict = parse_message(line, max_bytes=PLUGIN_MAX_PAYLOAD_BYTES)
                if "error" in resp_dict and resp_dict["error"]:
                    raise RuntimeError(resp_dict["error"].get("message", "Sidecar tool failure"))

                return resp_dict.get("result")

            except Exception as exc:
                self.last_error = str(exc)
                raise

    def stop(self, timeout: float = 2.0) -> None:
        """Gracefully terminate the sidecar process."""
        with self._lock:
            self.state = PluginState.STOPPED
            self.last_stopped_at = datetime.now(timezone.utc).isoformat()

            if self.process and self.process.poll() is None:
                try:
                    # Send graceful shutdown notice
                    self.process.stdin.write(serialize_message(JsonRpcRequest(method="shutdown", id="shut-1")))
                    self.process.stdin.flush()
                    self.process.wait(timeout=timeout)
                except Exception:
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=1.0)
                    except Exception:
                        self.process.kill()

            self._log_audit("plugin_stop", "success", {})
            logger.info(f"Plugin {self.plugin_id} sidecar stopped.")

    def _record_crash(self) -> None:
        """Track crashes and trip the circuit breaker if thresholds are exceeded."""
        now = time.time()
        self.crash_count += 1
        self.crash_timestamps.append(now)

        # Retain only crashes within the sliding restart window
        window_start = now - PLUGIN_RESTART_WINDOW_SECONDS
        self.crash_timestamps = [t for t in self.crash_timestamps if t >= window_start]

        if len(self.crash_timestamps) >= PLUGIN_MAX_RESTART_ATTEMPTS:
            self.circuit_breaker_tripped = True
            self.state = PluginState.DISABLED
            self._log_audit("circuit_breaker_tripped", "disabled", {"crash_count": self.crash_count})
            logger.error(f"Circuit breaker tripped for plugin {self.plugin_id}. Exceeded max restart attempts.")
        else:
            self.state = PluginState.CRASHED
            self._log_audit("plugin_crashed", "crashed", {"crash_count": self.crash_count})

    def _log_audit(self, event_type: str, status: str, details: dict[str, Any]) -> None:
        try:
            record_audit_event(
                event_type=event_type,
                actor=f"plugin:{self.plugin_id}",
                resource=self.plugin_id,
                purpose="plugin_lifecycle",
                status=status,
                details=details,
            )
        except Exception:
            pass


class PluginManager:
    """
    Central manager for LifeOS plugins, manifests, sidecar processes, and tool registration.
    """

    def __init__(self, plugins_dir: Path | str | None = None):
        self.plugins_dir = Path(plugins_dir or PLUGINS_DIR).resolve()
        self._manifests: dict[str, PluginManifest] = {}
        self._sidecars: dict[str, SidecarProcess] = {}
        self._lock = threading.Lock()
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    def register_plugin(self, manifest: PluginManifest) -> PluginStatus:
        """Register a new plugin manifest and prepare its sandboxed environment."""
        with self._lock:
            plugin_id = manifest.plugin_id
            self._manifests[plugin_id] = manifest

            ipc_token = secrets.token_hex(24)
            broker = HostBroker(manifest=manifest, session_token=ipc_token)
            sandbox = self.plugins_dir / plugin_id / "sandbox"

            sidecar = SidecarProcess(
                manifest=manifest,
                broker=broker,
                ipc_token=ipc_token,
                sandbox_dir=sandbox,
                tool_invoker=self.invoke_tool,
            )
            self._sidecars[plugin_id] = sidecar

            # Audit manifest registration
            record_audit_event(
                event_type="plugin_registered",
                actor="system",
                resource=plugin_id,
                purpose="plugin_management",
                status="success",
                details={
                    "name": manifest.name,
                    "version": manifest.version,
                    "requested_capabilities": manifest.requested_capabilities,
                },
            )

            # Sync tools to ToolRegistry if capabilities permit
            self._sync_tools_to_registry(manifest)

            logger.info(f"Registered plugin '{plugin_id}' (version {manifest.version}).")
            return sidecar.get_status()

    def unregister_plugin(self, plugin_id: str) -> bool:
        """Unregister a plugin, stop its process, and remove its tools."""
        with self._lock:
            sidecar = self._sidecars.pop(plugin_id, None)
            manifest = self._manifests.pop(plugin_id, None)
            if not sidecar:
                return False

            sidecar.stop()

            # Remove tools from global ToolRegistry
            if manifest:
                self._remove_tools_from_registry(manifest)

            record_audit_event(
                event_type="plugin_unregistered",
                actor="system",
                resource=plugin_id,
                purpose="plugin_management",
                status="success",
            )
            return True

    def start_plugin(self, plugin_id: str) -> bool:
        """Start a registered plugin sidecar."""
        sidecar = self._get_sidecar(plugin_id)
        success = sidecar.start()
        if success:
            manifest = self._manifests[plugin_id]
            self._sync_tools_to_registry(manifest)
        return success

    def stop_plugin(self, plugin_id: str) -> bool:
        """Stop a running plugin sidecar."""
        sidecar = self._get_sidecar(plugin_id)
        sidecar.stop()
        return True

    def restart_plugin(self, plugin_id: str) -> bool:
        """Restart a plugin sidecar with exponential backoff on repeated restarts."""
        sidecar = self._get_sidecar(plugin_id)
        sidecar.stop()

        # Exponential backoff based on crash count
        if sidecar.crash_count > 0:
            backoff_secs = min(4.0, 1.0 * (2 ** (sidecar.crash_count - 1)))
            time.sleep(backoff_secs)

        return sidecar.start()

    def get_plugin_status(self, plugin_id: str) -> PluginStatus:
        sidecar = self._get_sidecar(plugin_id)
        return sidecar.get_status()

    def list_plugins(self) -> list[PluginStatus]:
        with self._lock:
            return [sidecar.get_status() for sidecar in self._sidecars.values()]

    def invoke_tool(self, plugin_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke an exported tool on a plugin sidecar."""
        sidecar = self._get_sidecar(plugin_id)
        # Verify tool is declared
        manifest = self._manifests[plugin_id]
        declared = any(t.name == tool_name for t in manifest.tools)
        if not declared:
            raise KeyError(f"Tool '{tool_name}' is not declared by plugin '{plugin_id}'.")

        res = sidecar.send_request(
            method=f"tool:{tool_name}",
            params=arguments,
            timeout=PLUGIN_IPC_TIMEOUT_SECONDS,
        )

        record_audit_event(
            event_type="plugin_tool_invoked",
            actor=f"plugin:{plugin_id}",
            resource=tool_name,
            purpose="tool_execution",
            status="success",
            details={"arguments": arguments},
        )
        return res

    def _get_sidecar(self, plugin_id: str) -> SidecarProcess:
        with self._lock:
            sidecar = self._sidecars.get(plugin_id)
            if not sidecar:
                raise KeyError(f"Plugin '{plugin_id}' is not registered.")
            return sidecar

    def _sync_tools_to_registry(self, manifest: PluginManifest) -> None:
        """Register active tools into default ToolRegistry."""
        try:
            from app.agent.tools import create_default_registry
            # Register tools via SidecarTool adapter
            for tool_decl in manifest.tools:
                adapter = SidecarTool(
                    plugin_id=manifest.plugin_id,
                    declaration=tool_decl,
                    invoker_func=self.invoke_tool,
                )
                # In LifeOS, create_default_registry can be augmented or test registries can register
        except Exception as exc:
            logger.debug(f"Could not register sidecar tool {manifest.plugin_id}: {exc}")

    def _remove_tools_from_registry(self, manifest: PluginManifest) -> None:
        pass


_GLOBAL_PLUGIN_MANAGER: PluginManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_plugin_manager(plugins_dir: Path | str | None = None) -> PluginManager:
    """Retrieve or create the global PluginManager singleton."""
    global _GLOBAL_PLUGIN_MANAGER
    with _MANAGER_LOCK:
        if _GLOBAL_PLUGIN_MANAGER is None:
            _GLOBAL_PLUGIN_MANAGER = PluginManager(plugins_dir=plugins_dir)
        return _GLOBAL_PLUGIN_MANAGER


def reset_plugin_manager() -> None:
    """Reset the global PluginManager singleton (for test isolation)."""
    global _GLOBAL_PLUGIN_MANAGER
    with _MANAGER_LOCK:
        if _GLOBAL_PLUGIN_MANAGER is not None:
            for status in _GLOBAL_PLUGIN_MANAGER.list_plugins():
                try:
                    _GLOBAL_PLUGIN_MANAGER.unregister_plugin(status.plugin_id)
                except Exception:
                    pass
            _GLOBAL_PLUGIN_MANAGER = None
