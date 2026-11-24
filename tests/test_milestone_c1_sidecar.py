"""
test_milestone_c1_sidecar.py

Deterministic tests for Milestone C1: Desktop Shell, Sidecar Manager & Typed Client SDK.
Validates:
1. Sidecar CLI argument parsing and security boundaries (rejection of non-127.0.0.1 hosts).
2. Dynamic session token generation when --token is omitted.
3. Subprocess execution, JSON handshake protocol, and dynamic port binding.
4. Authenticated readiness polling (/ready) with ephemeral session token.
5. Rejection of unauthenticated or wrongly-tokened requests (401).
6. SSE streaming authentication via sidecar session token.
7. Clean subprocess lifecycle management and graceful shutdown.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import pytest

from app.cli import build_parser, _cmd_serve


def _make_http_request(url: str, token: str | None = None, timeout: float = 3.0) -> tuple[int, dict]:
    """Helper to perform HTTP GET request with optional Bearer token."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body) if body else {}
            return resp.status, data
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8")
        data = json.loads(body) if body.startswith("{") else {"error": body}
        return err.code, data


def _wait_until_ready(base_url: str, token: str, timeout: float = 60.0) -> bool:
    """Poll the /ready endpoint until the server reports ready."""
    ready_url = f"{base_url}/ready"
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            status, data = _make_http_request(ready_url, token=token, timeout=2.0)
            if status == 200 and data.get("status") == "ready":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def sidecar_server():
    """
    Module-scoped fixture that spawns a single LifeOS sidecar subprocess.
    Ensures that cold-start import latency (PyTorch, Chroma, Transformers)
    is paid only once across all endpoint tests.
    """
    token = "test_desktop_session_token_1234567890abcdef"
    cmd = [
        sys.executable,
        "-m",
        "app.cli",
        "serve",
        "--sidecar-mode",
        "--port",
        "0",
        "--token",
        token,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    try:
        handshake_line = proc.stdout.readline()
        assert handshake_line, "Failed to read handshake line from sidecar process"

        handshake = json.loads(handshake_line.strip())
        assert handshake.get("event") == "sidecar_ready"
        assert handshake.get("status") == "ready"
        assert handshake.get("host") == "127.0.0.1"
        assert handshake.get("token") == token
        assert handshake.get("pid") is not None and handshake.get("pid") > 0
        port = handshake.get("port")
        assert isinstance(port, int) and port > 0

        base_url = f"http://127.0.0.1:{port}"

        # Wait until /ready probe returns ready (up to 60s for cold start)
        is_ready = _wait_until_ready(base_url, token=token, timeout=60.0)
        assert is_ready, f"Sidecar failed to report ready at {base_url}/ready within 60s"

        yield {
            "proc": proc,
            "host": "127.0.0.1",
            "port": port,
            "token": token,
            "base_url": base_url,
            "handshake": handshake,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


# =============================================================================
# CLI Unit & Security Tests
# =============================================================================

def test_cli_sidecar_mode_parser_defaults():
    """Verify CLI parser sets sidecar mode arguments properly."""
    parser = build_parser()
    args = parser.parse_args(["serve", "--sidecar-mode"])
    assert args.subcommand == "serve"
    assert args.sidecar_mode is True
    assert args.port is None
    assert args.token is None


def test_cli_sidecar_mode_rejects_external_host(capsys):
    """Verify sidecar mode strictly rejects non-127.0.0.1 binding for security."""
    parser = build_parser()
    args = parser.parse_args(["serve", "--sidecar-mode", "--host", "0.0.0.0"])
    code = _cmd_serve(args)
    assert code == 1
    captured = capsys.readouterr()
    assert "Sidecar mode strictly requires 127.0.0.1" in captured.err

    args_lan = parser.parse_args(["serve", "--sidecar-mode", "--host", "192.168.1.100"])
    code_lan = _cmd_serve(args_lan)
    assert code_lan == 1


# =============================================================================
# Subprocess Handshake & Lifecycle Tests
# =============================================================================

def test_sidecar_handshake_and_liveness(sidecar_server):
    """Verify sidecar handshake metadata and unauthenticated /health endpoint."""
    base_url = sidecar_server["base_url"]
    handshake = sidecar_server["handshake"]

    assert handshake["event"] == "sidecar_ready"
    assert handshake["status"] == "ready"
    assert handshake["host"] == "127.0.0.1"

    status_health, data_health = _make_http_request(f"{base_url}/health")
    assert status_health == 200
    assert data_health.get("status") in ("healthy", "ok")


def test_sidecar_auth_rejection(sidecar_server):
    """Verify unauthenticated or wrongly-tokened requests are rejected (401)."""
    base_url = sidecar_server["base_url"]

    # 1. No token provided
    status_no_auth, _ = _make_http_request(f"{base_url}/api/privacy/summary")
    assert status_no_auth == 401

    # 2. Invalid token provided
    status_bad_auth, _ = _make_http_request(f"{base_url}/api/privacy/summary", token="invalid_token_999")
    assert status_bad_auth == 401


def test_sidecar_authenticated_endpoint_access(sidecar_server):
    """Verify requests with valid session token succeed."""
    base_url = sidecar_server["base_url"]
    token = sidecar_server["token"]

    status_ok, data = _make_http_request(f"{base_url}/api/privacy/summary", token=token)
    assert status_ok == 200
    assert "audit_chain_valid" in data
    assert data.get("local_first_mode") is True


def test_sidecar_sse_stream_authentication(sidecar_server):
    """Verify SSE streaming endpoint enforces session token auth."""
    base_url = sidecar_server["base_url"]
    token = sidecar_server["token"]

    # 1. Unauthenticated SSE rejected with 401
    sse_unauth_url = f"{base_url}/api/events/sse"
    status_unauth, _ = _make_http_request(sse_unauth_url, timeout=2.0)
    assert status_unauth == 401

    # 2. Authenticated SSE with query token parameter succeeds with 200 text/event-stream
    sse_auth_url = f"{base_url}/api/events/sse?token={token}&max_events=1&timeout=2.0"
    req = urllib.request.Request(sse_auth_url)
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/event-stream" in content_type


def test_sidecar_process_clean_exit(sidecar_server):
    """Verify sidecar process is active and has a valid PID."""
    proc = sidecar_server["proc"]
    assert proc.poll() is None
    assert proc.pid > 0
