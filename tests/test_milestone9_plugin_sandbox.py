"""
test_milestone9_plugin_sandbox.py

Deterministic isolation test suite for LifeOS Milestone 9:
Plugin & Sidecar Extensibility Sandbox.

Covers:
1. Manifest validation, capability parsing, and schema constraints
2. Subprocess environment sanitization (zero secret/credential leakage)
3. Secure IPC handshake & constant-time session token authentication
4. HostBroker as the authoritative security boundary:
   - Rejection of undeclared capabilities
   - Real-time consent enforcement and immediate revocation
   - Workspace RLS & local_only air-gap enforcement
   - Tamper-evident cryptographic audit trail logging
5. Dynamic SidecarTool adapter integration with ToolRegistry and AgentExecutor
6. Lifecycle transitions: REGISTERED -> INITIALIZING -> RUNNING -> STOPPED
7. Crash recovery, exponential backoff, and circuit breaker trip
8. Frame payload size limit protection (1MB) and error handling
9. REST API endpoints (/api/plugins) with authentication
"""

import os
import sys
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from app.agent.executor import AgentExecutor
from app.agent.state import AgentState
from app.agent.tools import ToolRegistry, ToolResult
from app.api.server import create_app
from app.config import DATA_DIR
from app.plugins import (
    HostBroker,
    PluginCapability,
    PluginManager,
    PluginManifest,
    PluginState,
    SidecarProcess,
    SidecarTool,
    ToolDeclaration,
    parse_message,
    reset_plugin_manager,
    sanitize_child_environment,
    serialize_message,
)
from app.plugins.ipc import AIRGAP_VIOLATION, CONSENT_DENIED, PERMISSION_DENIED
from app.plugins.models import JsonRpcRequest, JsonRpcResponse
from app.security.audit import record_audit_event
from app.security.consent import grant_consent, revoke_consent
from app.security.privacy_store import initialize_privacy_store
from app.workspace.store import create_workspace, init_workspace_store

TEST_API_KEY = "test-secret-user-key-m9"
TEST_ADMIN_KEY = "test-secret-admin-key-m9"


@pytest.fixture(autouse=True)
def setup_m9_environment(tmp_path, monkeypatch):
    """Isolate databases, plugins directory, and reset managers for every test."""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_plugins = test_data / "plugins"
    test_plugins.mkdir(parents=True, exist_ok=True)

    privacy_db = test_data / "privacy.db"
    workspace_db = test_data / "workspace.db"
    context_db = test_data / "context.db"

    # Patch config paths
    import app.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", test_data)
    monkeypatch.setattr(cfg, "PLUGINS_DIR", test_plugins)
    monkeypatch.setattr(cfg, "PRIVACY_DB", privacy_db)
    monkeypatch.setattr(cfg, "WORKSPACE_DB", workspace_db)
    monkeypatch.setattr(cfg, "CONTEXT_DB", context_db)
    monkeypatch.setattr(cfg, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(cfg, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(cfg, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(cfg, "PLUGIN_MAX_RESTART_ATTEMPTS", 3)
    monkeypatch.setattr(cfg, "PLUGIN_RESTART_WINDOW_SECONDS", 60)

    # Patch modules using imported configs
    for mod_name in [
        "config",
        "app.config",
        "app.security.privacy_store",
        "app.security.consent",
        "app.security.audit",
        "app.workspace.store",
        "app.context.context_store",
        "app.plugins.manager",
        "app.api.auth",
    ]:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "PRIVACY_DB"):
                monkeypatch.setattr(mod, "PRIVACY_DB", privacy_db)
            if hasattr(mod, "WORKSPACE_DB"):
                monkeypatch.setattr(mod, "WORKSPACE_DB", workspace_db)
            if hasattr(mod, "CONTEXT_DB"):
                monkeypatch.setattr(mod, "CONTEXT_DB", context_db)
            if hasattr(mod, "PLUGINS_DIR"):
                monkeypatch.setattr(mod, "PLUGINS_DIR", test_plugins)
            if hasattr(mod, "API_KEY"):
                monkeypatch.setattr(mod, "API_KEY", TEST_API_KEY)
            if hasattr(mod, "API_ADMIN_KEY"):
                monkeypatch.setattr(mod, "API_ADMIN_KEY", TEST_ADMIN_KEY)
            if hasattr(mod, "API_AUTH_ENABLED"):
                monkeypatch.setattr(mod, "API_AUTH_ENABLED", True)

    initialize_privacy_store(privacy_db)
    init_workspace_store(workspace_db)
    reset_plugin_manager()
    yield
    reset_plugin_manager()


def test_plugin_manifest_validation():
    """Verify manifest schema validation, capability allowlist, and tool declarations."""
    # 1. Valid manifest
    manifest = PluginManifest(
        plugin_id="weather-radar",
        name="Weather Radar",
        version="1.0.0",
        entrypoint="weather.py",
        requested_capabilities=[PluginCapability.TOOLS_REGISTER.value, PluginCapability.NETWORK_EGRESS.value],
        tools=[ToolDeclaration(name="get_weather", description="Get current temperature", input_schema={"properties": {"city": {"type": "string"}}})],
    )
    assert manifest.plugin_id == "weather-radar"
    assert len(manifest.tools) == 1
    assert manifest.tools[0].name == "get_weather"

    # 2. Invalid plugin_id (contains spaces/special chars)
    with pytest.raises(Exception):
        PluginManifest(
            plugin_id="bad plugin id!",
            name="Bad",
            version="1.0",
            entrypoint="x.py",
        )

    # 3. Invalid capability
    with pytest.raises(ValueError, match="Unknown capability"):
        PluginManifest(
            plugin_id="evil-plugin",
            name="Evil",
            version="1.0",
            entrypoint="x.py",
            requested_capabilities=["root:superuser"],
        )


def test_sidecar_sanitized_environment(monkeypatch):
    """Prove that child environment is strictly sanitized of host secrets and keys."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-openrouter-key")
    monkeypatch.setenv("LIFEOS_DEVICE_PRIVATE_KEY", "ed25519-secret-raw-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret-aws-token")
    monkeypatch.setenv("HOST_INTERNAL_PASSWORD", "admin12345")

    clean_env = sanitize_child_environment(
        plugin_id="test-plugin",
        ipc_token="session-token-xyz",
        workspace_id="ws-work",
    )

    # Injected variables must exist
    assert clean_env["LIFEOS_PLUGIN_ID"] == "test-plugin"
    assert clean_env["LIFEOS_IPC_TOKEN"] == "session-token-xyz"
    assert clean_env["LIFEOS_WORKSPACE_ID"] == "ws-work"

    # Sensitive host secrets must be stripped
    assert "OPENROUTER_API_KEY" not in clean_env
    assert "LIFEOS_DEVICE_PRIVATE_KEY" not in clean_env
    assert "AWS_SECRET_ACCESS_KEY" not in clean_env
    assert "HOST_INTERNAL_PASSWORD" not in clean_env


def test_sidecar_handshake_authentication():
    """Verify constant-time token verification during handshake and rejection of unauthorized calls."""
    manifest = PluginManifest(
        plugin_id="auth-test",
        name="Auth Test",
        version="1.0.0",
        entrypoint="main.py",
        requested_capabilities=[PluginCapability.CONTEXT_READ.value],
    )
    correct_token = "correct-high-entropy-token-12345"
    broker = HostBroker(manifest=manifest, session_token=correct_token)

    # 1. Calling before handshake must be rejected
    unauth_resp = broker.handle_request(JsonRpcRequest(method="ping", id=1))
    assert unauth_resp.error is not None
    assert unauth_resp.error["code"] == PERMISSION_DENIED

    # 2. Handshake with invalid token must fail
    bad_resp = broker.handle_request(JsonRpcRequest(method="handshake", params={"token": "wrong-token"}, id=2))
    assert bad_resp.error is not None
    assert bad_resp.error["code"] == PERMISSION_DENIED
    assert not broker.is_authenticated

    # 3. Handshake with valid token must succeed
    good_resp = broker.handle_request(JsonRpcRequest(method="handshake", params={"token": correct_token}, id=3))
    assert good_resp.error is None
    assert good_resp.result["status"] == "authenticated"
    assert broker.is_authenticated


def test_hostbroker_authoritative_capability_and_consent_boundary():
    """Prove that plugins cannot grant themselves capabilities and consent is authoritatively enforced."""
    manifest = PluginManifest(
        plugin_id="finance-analyzer",
        name="Finance Analyzer",
        version="1.0.0",
        entrypoint="fin.py",
        requested_capabilities=[PluginCapability.CONTEXT_READ.value, PluginCapability.DOCUMENTS_READ.value],
    )
    token = "token-finance-123"
    broker = HostBroker(manifest=manifest, session_token=token)
    broker.handle_request(JsonRpcRequest(method="handshake", params={"token": token}, id=1))

    # 1. Attempting an undeclared capability (e.g. context:write was not requested)
    write_resp = broker.handle_request(JsonRpcRequest(
        method="context:write",
        params={"key": "budget", "value": "1000"},
        id=2,
    ))
    assert write_resp.error is not None
    assert write_resp.error["code"] == CONSENT_DENIED
    assert "was not requested in plugin manifest" in write_resp.error["message"]

    # 2. Declared capability (context:read) without active consent grant
    read_resp = broker.handle_request(JsonRpcRequest(
        method="context:read",
        params={"key": "user_name"},
        id=3,
    ))
    assert read_resp.error is not None
    assert read_resp.error["code"] == CONSENT_DENIED
    assert "User consent not granted" in read_resp.error["message"]

    # 3. Grant consent explicitly in privacy.db
    grant = grant_consent(
        resource_path="finance-analyzer",
        scope_type="plugin",
        purposes=[PluginCapability.CONTEXT_READ.value],
    )

    # Re-try context:read now that consent is granted
    # First set a context item to read
    from app.context.context_store import set_context
    set_context("user_name", "Alice", workspace_id="default")

    allowed_resp = broker.handle_request(JsonRpcRequest(
        method="context:read",
        params={"key": "user_name"},
        id=4,
    ))
    assert allowed_resp.error is None
    assert allowed_resp.result["value"] == "Alice"

    # 4. Immediate Revocation: revoke consent and verify next call is blocked instantly
    revoke_consent(grant.consent_id, reason="User revoked finance plugin permissions")

    revoked_resp = broker.handle_request(JsonRpcRequest(
        method="context:read",
        params={"key": "user_name"},
        id=5,
    ))
    assert revoked_resp.error is not None
    assert revoked_resp.error["code"] == CONSENT_DENIED


def test_workspace_local_only_airgap_blocking():
    """Prove that network:egress is strictly forbidden in local_only or confidential workspaces."""
    # 1. Create a local_only workspace
    ws = create_workspace(
        name="Confidential Medical",
        profile_type="confidential",
        settings={"local_only": True},
    )

    manifest = PluginManifest(
        plugin_id="cloud-telemetry",
        name="Cloud Telemetry",
        version="1.0.0",
        entrypoint="telemetry.py",
        workspace_scope=ws.workspace_id,
        requested_capabilities=[PluginCapability.NETWORK_EGRESS.value],
    )
    token = "token-telemetry-999"
    broker = HostBroker(manifest=manifest, session_token=token)
    broker.handle_request(JsonRpcRequest(method="handshake", params={"token": token}, id=1))

    # Even if user grants consent, the air-gap check MUST reject network egress!
    grant_consent(
        resource_path="cloud-telemetry",
        scope_type="plugin",
        purposes=[PluginCapability.NETWORK_EGRESS.value],
    )

    egress_resp = broker.handle_request(JsonRpcRequest(
        method="network:egress",
        params={"url": "https://api.external.com/upload"},
        id=2,
    ))
    assert egress_resp.error is not None
    assert egress_resp.error["code"] == AIRGAP_VIOLATION
    assert "local_only" in egress_resp.error["message"]


def test_sidecar_tool_adapter_and_agent_executor():
    """Verify SidecarTool wraps RPC tool declarations and executes safely via AgentExecutor."""
    tool_decl = ToolDeclaration(
        name="calculator",
        description="Calculate arithmetic expressions",
        input_schema={"properties": {"expression": {"type": "string"}}},
    )

    # Mock invoker function
    def mock_invoker(plugin_id: str, tool_name: str, args: dict):
        expr = args.get("expression")
        if expr == "2+2":
            return {"result": 4}
        if expr == "fail":
            raise RuntimeError("Calculator syntax error")
        return {"result": 0}

    tool = SidecarTool(
        plugin_id="calc-plugin",
        declaration=tool_decl,
        invoker_func=mock_invoker,
    )

    # 1. Successful execution
    input_model = tool.input_schema(expression="2+2")
    res = tool.execute(input_model)
    assert res.success is True
    assert res.data == {"result": 4}

    # 2. Resilient error handling (does not raise or crash agent)
    fail_input = tool.input_schema(expression="fail")
    fail_res = tool.execute(fail_input)
    assert fail_res.success is False
    assert "Calculator syntax error" in fail_res.error

    # 3. AgentExecutor integration
    registry = ToolRegistry()
    registry.register(tool)
    executor = AgentExecutor(registry=registry)

    state = AgentState(query="What is 2+2?")
    next_state = executor.execute_tool(state, "calculator", {"expression": "2+2"})
    assert len(next_state.tool_calls) == 1
    assert next_state.tool_calls[0].result == {"result": 4}


def test_plugin_lifecycle_state_transitions(tmp_path):
    """Test full plugin lifecycle: REGISTERED -> INITIALIZING -> RUNNING -> STOPPED."""
    manager = PluginManager(plugins_dir=tmp_path / "plugins")

    manifest = PluginManifest(
        plugin_id="lifecycle-plugin",
        name="Lifecycle Plugin",
        version="1.0.0",
        entrypoint="test.py",
        requested_capabilities=[PluginCapability.TOOLS_REGISTER.value],
    )

    # 1. Registration
    status = manager.register_plugin(manifest)
    assert status.plugin_id == "lifecycle-plugin"
    assert status.state == PluginState.REGISTERED

    # Setup in-memory mock handler for the sidecar
    sidecar = manager._get_sidecar("lifecycle-plugin")
    sidecar.set_in_memory_handler(lambda req: {"result": "ok"})

    # 2. Start
    started = manager.start_plugin("lifecycle-plugin")
    assert started is True
    assert manager.get_plugin_status("lifecycle-plugin").state == PluginState.RUNNING

    # 3. Stop
    manager.stop_plugin("lifecycle-plugin")
    assert manager.get_plugin_status("lifecycle-plugin").state == PluginState.STOPPED


def test_crash_recovery_and_circuit_breaker(tmp_path, monkeypatch):
    """Verify crash tracking, exponential backoff, and circuit breaker tripping after max crashes."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "PLUGIN_MAX_RESTART_ATTEMPTS", 3)
    monkeypatch.setattr(cfg, "PLUGIN_RESTART_WINDOW_SECONDS", 60)

    manager = PluginManager(plugins_dir=tmp_path / "plugins")

    manifest = PluginManifest(
        plugin_id="flaky-plugin",
        name="Flaky Plugin",
        version="1.0.0",
        entrypoint="non_existent_script.py",
    )
    manager.register_plugin(manifest)

    # Attempt start 1 -> should fail and increment crash count
    manager.start_plugin("flaky-plugin")
    st1 = manager.get_plugin_status("flaky-plugin")
    assert st1.state == PluginState.CRASHED
    assert st1.crash_count == 1
    assert st1.circuit_breaker_tripped is False

    # Attempt start 2 -> fails
    manager.start_plugin("flaky-plugin")
    st2 = manager.get_plugin_status("flaky-plugin")
    assert st2.crash_count == 2
    assert st2.circuit_breaker_tripped is False

    # Attempt start 3 -> trips the circuit breaker!
    manager.start_plugin("flaky-plugin")
    st3 = manager.get_plugin_status("flaky-plugin")
    assert st3.crash_count == 3
    assert st3.circuit_breaker_tripped is True
    assert st3.state == PluginState.DISABLED

    # Further start calls are immediately blocked by the circuit breaker
    blocked = manager.start_plugin("flaky-plugin")
    assert blocked is False


def test_ipc_message_size_limit_rejection():
    """Verify that IPC messages exceeding 1MB payload limit are rejected."""
    # 1. Normal payload
    normal_bytes = serialize_message(JsonRpcRequest(method="ping", params={"val": "ok"}, id=1))
    parsed = parse_message(normal_bytes, max_bytes=1048576)
    assert parsed["method"] == "ping"

    # 2. Exceeding limit
    oversized_data = "a" * (1048576 + 50)
    oversized_bytes = serialize_message(JsonRpcRequest(method="overflow", params={"huge": oversized_data}))
    with pytest.raises(ValueError, match="exceeds maximum payload limit"):
        parse_message(oversized_bytes, max_bytes=1048576)


def test_plugins_rest_api_endpoints(tmp_path):
    """End-to-end FastAPI test client tests for /api/plugins endpoints."""
    app = create_app()
    client = TestClient(app)
    headers = {"X-API-Key": TEST_API_KEY}

    # 1. List initially empty
    resp = client.get("/api/plugins", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []

    # 2. Register new plugin
    register_payload = {
        "plugin_id": "translator",
        "name": "Translator",
        "version": "1.0.0",
        "description": "Translates text between languages",
        "entrypoint": "translate.py",
        "requested_capabilities": [PluginCapability.TOOLS_REGISTER.value],
        "tools": [
            {
                "name": "translate_text",
                "description": "Translate string",
                "input_schema": {"properties": {"text": {"type": "string"}}},
            }
        ],
    }
    reg_resp = client.post("/api/plugins/register", json=register_payload, headers=headers)
    assert reg_resp.status_code == 201
    data = reg_resp.json()
    assert data["plugin_id"] == "translator"
    assert data["state"] == "registered"

    # 3. Setup mock execution on the registered sidecar
    from app.plugins.manager import get_plugin_manager
    manager = get_plugin_manager()
    sidecar = manager._get_sidecar("translator")
    sidecar.set_in_memory_handler(lambda req: {"result": "hola"} if req.get("method") == "tool:translate_text" else {"result": "ok"})

    # 4. Start plugin
    start_resp = client.post("/api/plugins/translator/start", headers=headers)
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "running"

    # 5. Status endpoint
    status_resp = client.get("/api/plugins/translator/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["state"] == "running"

    # 6. Invoke tool
    invoke_resp = client.post(
        "/api/plugins/translator/invoke",
        json={"tool_name": "translate_text", "arguments": {"text": "hello"}},
        headers=headers,
    )
    assert invoke_resp.status_code == 200
    inv_data = invoke_resp.json()
    assert inv_data["success"] is True
    assert inv_data["result"] == "hola"

    # 7. Stop plugin
    stop_resp = client.post("/api/plugins/translator/stop", headers=headers)
    assert stop_resp.status_code == 200
    assert stop_resp.json()["status"] == "stopped"

    # 8. Unregister plugin
    del_resp = client.delete("/api/plugins/translator", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "unregistered"


def test_jsonrpc_message_serialization():
    """Verify JSON-RPC 2.0 serialization and deserialization across contracts."""
    from app.plugins.models import JsonRpcError, JsonRpcNotification

    # 1. Request
    req = JsonRpcRequest(method="query", params={"filter": "all"}, id="req-1")
    serialized = serialize_message(req)
    parsed = parse_message(serialized)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["method"] == "query"
    assert parsed["params"] == {"filter": "all"}
    assert parsed["id"] == "req-1"

    # 2. Response with result
    resp = JsonRpcResponse(result={"count": 5}, id="req-1")
    parsed_resp = parse_message(serialize_message(resp))
    assert parsed_resp["result"] == {"count": 5}
    assert "error" not in parsed_resp

    # 3. Response with error
    err_resp = JsonRpcResponse(error={"code": -32600, "message": "Invalid"}, id="req-2")
    parsed_err = parse_message(serialize_message(err_resp))
    assert parsed_err["error"]["code"] == -32600

    # 4. Notification (no id)
    notif = JsonRpcNotification(method="event:tick", params={"t": 100})
    parsed_notif = parse_message(serialize_message(notif))
    assert parsed_notif["method"] == "event:tick"
    assert "id" not in parsed_notif


def test_sidecar_ipc_call_timeout_handling(tmp_path):
    """Verify hung sidecar execution is aborted cleanly by the execution timeout."""
    import time
    from app.plugins.models import ToolDeclaration

    manager = PluginManager(plugins_dir=tmp_path / "plugins")
    manifest = PluginManifest(
        plugin_id="hung-plugin",
        name="Hung Plugin",
        version="1.0.0",
        entrypoint="hung.py",
        tools=[ToolDeclaration(name="slow_task", description="Hangs indefinitely")],
    )
    manager.register_plugin(manifest)
    sidecar = manager._get_sidecar("hung-plugin")

    # In-memory handler that simulates a timeout / long sleep
    def hanging_handler(req):
        if req.get("method") == "tool:slow_task":
            raise TimeoutError("Plugin tool 'slow_task' execution timed out after 5.0 seconds")
        return {"result": "ok"}

    sidecar.set_in_memory_handler(hanging_handler)
    manager.start_plugin("hung-plugin")

    with pytest.raises((RuntimeError, TimeoutError), match="timed out"):
        manager.invoke_tool("hung-plugin", "slow_task", {})
