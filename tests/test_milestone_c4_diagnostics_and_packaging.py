"""
test_milestone_c4_diagnostics_and_packaging.py

Deterministic integration and security tests for Milestone C4:
Desktop Packaging, Auto-Update & Diagnostics Center.

Covers:
1. Authenticated /api/diagnostics and /api/diagnostics/governor endpoints (401 unauth, 200 auth)
2. In-process port verification (actively serving socket -> OK / HEALTHY; closed port -> WARN / DEGRADED / strict FAILED)
3. Simulated database corruption -> overall_status FAILED with non-empty fix_hint
4. HardwareGovernor telemetry endpoint across AC, battery, and high-CPU states
5. Tauri packaging security audit (strict loopback CSP, HTTPS updater endpoints, valid Minisign pubkey, valid icons)
6. Update manifest generation, Ed25519/Minisign signing, tamper rejection, and wrong-key rejection
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.api.server import app
from app.ops.doctor import _check_port_availability, run_diagnostics
from app.ops.governor import SystemMetrics, get_governor
from app.storage.sqlite import connect_sqlite
from scripts.package_desktop import (
    build_signed_update_manifest,
    generate_minisign_keypair,
    generate_update_manifest,
    sign_payload,
    validate_tauri_assets,
    verify_signature,
)

TEST_USER_KEY = "test-c4-user-key"
TEST_ADMIN_KEY = "test-c4-admin-key"


@pytest.fixture(autouse=True)
def isolated_c4_env(tmp_path, monkeypatch):
    """Isolate all LifeOS databases, directories, auth keys, and governor state per test."""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_docs = test_data / "documents"
    test_docs.mkdir(parents=True, exist_ok=True)
    test_backups = test_data / "backups"
    test_backups.mkdir(parents=True, exist_ok=True)
    test_plugins = test_data / "plugins"
    test_plugins.mkdir(parents=True, exist_ok=True)
    test_chroma = tmp_path / "chroma_db"
    test_chroma.mkdir(parents=True, exist_ok=True)

    dbs = {
        "REGISTRY_DB": test_data / "document_registry.db",
        "ENTITY_DB": test_data / "entities.db",
        "PEOPLE_DB": test_data / "people_registry.db",
        "CONTEXT_DB": test_data / "context.db",
        "PRIVACY_DB": test_data / "privacy.db",
        "IDENTITY_DB": test_data / "identity.db",
        "WORKSPACE_DB": test_data / "workspace.db",
        "SYNC_DB": test_data / "sync.db",
        "QUEUE_DB": test_data / "queue.db",
    }

    for p in dbs.values():
        with connect_sqlite(p, wal_mode=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS init_probe (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT OR REPLACE INTO init_probe (id, note) VALUES (1, 'ok')")

    with connect_sqlite(dbs["IDENTITY_DB"], wal_mode=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_identity (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                signing_public_key TEXT NOT NULL,
                exchange_public_key TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO local_identity (
                device_id, device_name, device_type, signing_public_key, exchange_public_key, created_at
            ) VALUES ('dev_c4_test', 'C4 Workstation', 'desktop', 'pub_sign', 'pub_exch', '2026-11-24T00:00:00Z')
            """
        )

    # Start an ephemeral loopback HTTP server so in-process port verification sees an active HTTP listener
    class _ProbeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"healthy"}')

        def log_message(self, format, *args):
            pass

    http_srv = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    active_port = http_srv.server_address[1]
    srv_thread = threading.Thread(target=http_srv.serve_forever, daemon=True)
    srv_thread.start()

    import app.config as cfg

    targets = [cfg]
    if "config" in sys.modules:
        targets.append(sys.modules["config"])

    for mod in targets:
        monkeypatch.setattr(mod, "DATA_DIR", test_data)
        monkeypatch.setattr(mod, "DOCUMENTS_DIR", test_docs)
        monkeypatch.setattr(mod, "BACKUP_DIR", test_backups)
        monkeypatch.setattr(mod, "PLUGINS_DIR", test_plugins)
        monkeypatch.setattr(mod, "CHROMA_DIR", test_chroma)
        monkeypatch.setattr(mod, "API_KEY", TEST_USER_KEY)
        monkeypatch.setattr(mod, "API_ADMIN_KEY", TEST_ADMIN_KEY)
        monkeypatch.setattr(mod, "API_AUTH_ENABLED", True)
        monkeypatch.setattr(mod, "API_PORT", active_port)
        for attr, path in dbs.items():
            monkeypatch.setattr(mod, attr, path)

    gov = get_governor()
    gov.set_override_metrics(None)

    try:
        yield {
            "active_port": active_port,
            "dbs": dbs,
            "http_srv": http_srv,
        }
    finally:
        gov.set_override_metrics(None)
        http_srv.shutdown()
        http_srv.server_close()
        srv_thread.join(timeout=2.0)


# ============================================================
# 1. DIAGNOSTICS API & DOCTOR ENGINE TESTS
# ============================================================


def test_diagnostics_endpoints_require_authentication():
    """Verify GET /api/diagnostics and /api/diagnostics/governor reject unauthenticated callers."""
    client = TestClient(app)

    r_diag = client.get("/api/diagnostics")
    assert r_diag.status_code == 401

    r_gov = client.get("/api/diagnostics/governor")
    assert r_gov.status_code == 401

    r_bad = client.get("/api/diagnostics", headers={"Authorization": "Bearer wrong-token"})
    assert r_bad.status_code == 401


def test_diagnostics_endpoint_healthy_and_strict_mode(isolated_c4_env):
    """Verify GET /api/diagnostics succeeds with active in-process port under normal and strict modes."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TEST_USER_KEY}"}
    port = isolated_c4_env["active_port"]

    resp = client.get("/api/diagnostics", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["overall_status"] == "HEALTHY"
    assert data["failed"] == 0
    assert data["warnings"] == 0
    assert data["passed"] == data["total_checks"]
    assert data["total_checks"] >= 15

    port_check = next((c for c in data["checks"] if c["name"] == f"port_{port}"), None)
    assert port_check is not None
    assert port_check["status"] == "OK"
    assert port_check["message"] == "API port actively serving traffic"

    # Strict mode should also remain HEALTHY when all subsystems pass
    resp_strict = client.get("/api/diagnostics?strict=true", headers=headers)
    assert resp_strict.status_code == 200
    assert resp_strict.json()["overall_status"] == "HEALTHY"


def test_diagnostics_in_process_closed_port_degraded_and_strict_failed():
    """Verify closed API port emits WARN (DEGRADED normal, FAILED in strict mode)."""
    # Find an unused ephemeral port and immediately close it
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]

    chk = _check_port_availability(closed_port, in_process=True)
    assert chk.status == "WARN"
    assert chk.remediation is not None

    import app.config as cfg

    orig_port = cfg.API_PORT
    cfg.API_PORT = closed_port
    if "config" in sys.modules:
        sys.modules["config"].API_PORT = closed_port
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {TEST_USER_KEY}"}

        r_normal = client.get("/api/diagnostics?strict=false", headers=headers)
        assert r_normal.status_code == 200
        assert r_normal.json()["overall_status"] == "DEGRADED"
        assert r_normal.json()["warnings"] >= 1

        r_strict = client.get("/api/diagnostics?strict=true", headers=headers)
        assert r_strict.status_code == 200
        assert r_strict.json()["overall_status"] == "FAILED"
    finally:
        cfg.API_PORT = orig_port
        if "config" in sys.modules:
            sys.modules["config"].API_PORT = orig_port


def test_diagnostics_detects_corrupt_database_with_fix_hint(isolated_c4_env):
    """Verify simulated database corruption produces FAILED overall status and remediation fix_hint."""
    sync_db = isolated_c4_env["dbs"]["SYNC_DB"]
    sync_db.write_bytes(b"SQLite format 3\x00corrupted-bytes-for-c4-test\x00")

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TEST_USER_KEY}"}
    resp = client.get("/api/diagnostics", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["overall_status"] == "FAILED"
    assert body["failed"] >= 1
    sync_check = next((c for c in body["checks"] if c["name"] == "sync.db"), None)
    assert sync_check is not None
    assert sync_check["status"] == "FAIL"
    assert sync_check["fix_hint"]
    assert "backup restore" in sync_check["fix_hint"].lower() or "permissions" in sync_check["fix_hint"].lower()


def test_diagnostics_detects_zero_byte_sqlite_database(isolated_c4_env):
    """Verify 0-byte empty/truncated SQLite database files report FAIL instead of WARN."""
    entities_db = isolated_c4_env["dbs"]["ENTITY_DB"]
    entities_db.write_bytes(b"")

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TEST_USER_KEY}"}
    resp = client.get("/api/diagnostics", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["overall_status"] == "FAILED"
    assert body["failed"] >= 1
    ent_check = next((c for c in body["checks"] if c["name"] == "entities.db"), None)
    assert ent_check is not None
    assert ent_check["status"] == "FAIL"
    assert ent_check["message"] == "Database file is 0 bytes (empty/truncated)"
    assert ent_check["fix_hint"] == "Re-initialize database store"


def test_diagnostics_detects_corrupt_chromadb(isolated_c4_env):
    """Verify corrupted ChromaDB vector store reports FAIL instead of silently succeeding."""
    import app.config as cfg

    chroma_dir = Path(cfg.CHROMA_DIR)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    (chroma_dir / "chroma.sqlite3").write_bytes(b"not-a-valid-sqlite-database-corrupted-chroma")

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TEST_USER_KEY}"}
    resp = client.get("/api/diagnostics", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["overall_status"] == "FAILED"
    assert body["failed"] >= 1
    chroma_check = next((c for c in body["checks"] if c["name"] == "chroma_vectordb"), None)
    assert chroma_check is not None
    assert chroma_check["status"] == "FAIL"
    assert "ChromaDB connection failed:" in chroma_check["message"]
    assert chroma_check["fix_hint"]
    assert "reindex" in chroma_check["fix_hint"].lower()


def test_live_sidecar_ephemeral_port_diagnostics_healthy(tmp_path):
    """
    Spawn a live sidecar with `--sidecar-mode --port 0` and verify GET /api/diagnostics?strict=true
    reports overall_status == HEALTHY against the ephemeral port with no port 8000 warning.
    """
    data_dir = tmp_path / "sidecar_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = data_dir / "documents"
    backups_dir = data_dir / "backups"
    plugins_dir = data_dir / "plugins"
    chroma_dir = tmp_path / "sidecar_chroma"
    for d in (docs_dir, backups_dir, plugins_dir, chroma_dir):
        d.mkdir(parents=True, exist_ok=True)

    db_env_map = {
        "LIFEOS_REGISTRY_DB": data_dir / "document_registry.db",
        "LIFEOS_ENTITY_DB": data_dir / "entities.db",
        "LIFEOS_PEOPLE_DB": data_dir / "people_registry.db",
        "LIFEOS_CONTEXT_DB": data_dir / "context.db",
        "LIFEOS_PRIVACY_DB": data_dir / "privacy.db",
        "LIFEOS_IDENTITY_DB": data_dir / "identity.db",
        "LIFEOS_WORKSPACE_DB": data_dir / "workspace.db",
        "LIFEOS_SYNC_DB": data_dir / "sync.db",
        "LIFEOS_QUEUE_DB": data_dir / "queue.db",
    }
    for db_path in db_env_map.values():
        with connect_sqlite(db_path, wal_mode=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS init_probe (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT OR REPLACE INTO init_probe (id, note) VALUES (1, 'ok')")

    env = os.environ.copy()
    env.update(
        {
            "LIFEOS_DATA_DIR": str(data_dir),
            "LIFEOS_DOCUMENTS_DIR": str(docs_dir),
            "LIFEOS_BACKUP_DIR": str(backups_dir),
            "LIFEOS_PLUGINS_DIR": str(plugins_dir),
            "LIFEOS_CHROMA_DIR": str(chroma_dir),
            "LIFEOS_DEVICE_KEYS_PATH": str(data_dir / "device_keys.json"),
            "LIFEOS_ROUTING_BUDGET_FILE": str(data_dir / "routing_budget.json"),
            "LIFEOS_API_ENVIRONMENT": "development",
        }
    )
    for k, v in db_env_map.items():
        env[k] = str(v)

    token = "test-c4-live-sidecar-ephemeral-token"
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
        env=env,
    )
    try:
        handshake_line = proc.stdout.readline()
        assert handshake_line, "Sidecar failed to emit JSON handshake"
        handshake = json.loads(handshake_line.strip())
        port = handshake["port"]
        assert isinstance(port, int) and port > 0
        base_url = f"http://127.0.0.1:{port}"

        ready = False
        deadline = time.time() + 60.0
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base_url}/ready", timeout=2.0) as resp:
                    if resp.status == 200 and json.loads(resp.read().decode("utf-8")).get("status") == "ready":
                        ready = True
                        break
            except Exception:
                pass
            time.sleep(0.5)
        assert ready, f"Live sidecar did not become ready on {base_url}"

        req = urllib.request.Request(f"{base_url}/api/diagnostics?strict=true")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            assert resp.status == 200
            report = json.loads(resp.read().decode("utf-8"))

        assert report["overall_status"] == "HEALTHY", f"Unexpected report: {report}"
        assert report["warnings"] == 0
        assert report["failed"] == 0

        port_check = next((c for c in report["checks"] if c["name"] == f"port_{port}"), None)
        assert port_check is not None
        assert port_check["status"] == "OK"
        if port != 8000:
            assert all(c["name"] != "port_8000" for c in report["checks"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


def test_governor_diagnostics_endpoint():
    """Verify GET /api/diagnostics/governor reports live and throttled telemetry accurately."""
    gov = get_governor()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TEST_USER_KEY}"}

    # 1. AC power, nominal load -> active
    gov.set_override_metrics(
        SystemMetrics(
            cpu_percent=18.5,
            ram_percent=42.0,
            on_battery=False,
            battery_percent=100.0,
            thermal_throttling=False,
            user_idle_seconds=120.0,
        )
    )
    r_ok = client.get("/api/diagnostics/governor", headers=headers)
    assert r_ok.status_code == 200
    d_ok = r_ok.json()
    assert d_ok["cpu_percent"] == 18.5
    assert d_ok["ram_percent"] == 42.0
    assert d_ok["on_battery"] is False
    assert d_ok["should_pause_ingestion"] is False
    assert d_ok["pause_reason"] is None

    # 2. On battery -> paused
    gov.set_override_metrics(
        SystemMetrics(
            cpu_percent=22.0,
            ram_percent=48.0,
            on_battery=True,
            battery_percent=64.0,
            thermal_throttling=False,
            user_idle_seconds=60.0,
        )
    )
    r_bat = client.get("/api/diagnostics/governor", headers=headers)
    assert r_bat.status_code == 200
    d_bat = r_bat.json()
    assert d_bat["on_battery"] is True
    assert d_bat["battery_percent"] == 64.0
    assert d_bat["should_pause_ingestion"] is True
    assert d_bat["pause_reason"] == "on_battery"

    # 3. High CPU on AC -> paused
    gov.set_override_metrics(
        SystemMetrics(
            cpu_percent=94.2,
            ram_percent=50.0,
            on_battery=False,
            battery_percent=100.0,
            thermal_throttling=False,
            user_idle_seconds=60.0,
        )
    )
    r_cpu = client.get("/api/diagnostics/governor", headers=headers)
    assert r_cpu.status_code == 200
    assert r_cpu.json()["should_pause_ingestion"] is True
    assert r_cpu.json()["pause_reason"] == "high_cpu"


# ============================================================
# 2. TAURI PACKAGING SECURITY & AUTO-UPDATER SIGNING TESTS
# ============================================================


def test_tauri_packaging_security_audit():
    """Verify desktop/src-tauri configuration passes all CSP, HTTPS, pubkey, and icon audits."""
    summary = validate_tauri_assets()
    assert summary["valid"] is True
    assert summary["identifier"] == "com.lifeos.desktop"
    assert len(summary["icons"]) >= 5
    assert all(ep.startswith("https://") for ep in summary["endpoints"])

    conf_path = Path("desktop/src-tauri/tauri.conf.json")
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    csp = conf["app"]["security"]["csp"]
    assert "http://127.0.0.1:*" in csp
    assert "ws://127.0.0.1:*" in csp
    assert "0.0.0.0" not in csp
    assert conf["bundle"]["createUpdaterArtifacts"] is True
    assert len(conf["plugins"]["updater"]["pubkey"].strip()) > 20
    assert "active" not in conf["plugins"]["updater"]
    assert "dialog" not in conf["plugins"]["updater"]

    main_rs = Path("desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    assert "tauri_plugin_updater::Builder::new().build()" in main_rs


def test_tauri_packaging_audit_rejects_insecure_config(tmp_path):
    """Verify validate_tauri_assets rejects HTTP updater endpoints, wildcard/non-loopback CSP, and v1 updater keys."""
    src_tauri = Path("desktop/src-tauri")
    bad_tauri = tmp_path / "src-tauri-bad"
    shutil.copytree(src_tauri, bad_tauri)

    conf_file = bad_tauri / "tauri.conf.json"
    conf = json.loads(conf_file.read_text(encoding="utf-8"))
    conf["plugins"]["updater"]["endpoints"] = ["http://insecure.example.com/updates"]
    conf_file.write_text(json.dumps(conf), encoding="utf-8")

    with pytest.raises(ValueError, match="HTTPS"):
        validate_tauri_assets(bad_tauri)

    # Restore valid HTTPS endpoint and test wildcard / non-loopback CSP rejections
    conf["plugins"]["updater"]["endpoints"] = ["https://releases.lifeos.local/updates/{{target}}/{{current_version}}"]

    bad_csps = [
        "default-src 'self' https://*",
        "default-src 'self' http://127.0.0.1:* ws://127.0.0.1:* *",
        "default-src 'self' http://127.0.0.1:* ws://*",
        "default-src 'self' http://127.0.0.1:* ws://127.0.0.1:* wss://*",
        "default-src 'self' http://127.0.0.1:* ws://127.0.0.1:* https://attacker.example",
        "default-src 'self' http://127.0.0.1:* ws://127.0.0.1:* http://192.168.1.50:8000",
    ]
    for bad_csp in bad_csps:
        conf["app"]["security"]["csp"] = bad_csp
        conf_file.write_text(json.dumps(conf), encoding="utf-8")
        with pytest.raises(ValueError, match="loopback"):
            validate_tauri_assets(bad_tauri)

    # Restore valid CSP and verify legacy Tauri v1 keys under plugins.updater are rejected
    conf["app"]["security"]["csp"] = "default-src 'self' 'unsafe-inline' http://127.0.0.1:* ws://127.0.0.1:*"
    conf["plugins"]["updater"]["active"] = True
    conf_file.write_text(json.dumps(conf), encoding="utf-8")
    with pytest.raises(ValueError, match="Tauri v1"):
        validate_tauri_assets(bad_tauri)


def test_update_manifest_signing_and_tamper_rejection(tmp_path):
    """Verify Ed25519/Minisign artifact signing, manifest schema, and tamper/wrong-key rejection."""
    priv_b64, pub_b64 = generate_minisign_keypair()
    _, other_pub_b64 = generate_minisign_keypair()

    artifact = tmp_path / "LifeOS_1.0.1_x64_en-US.msi"
    original_bytes = b"MZ-synthetic-installer-payload-v1.0.1" * 128
    artifact.write_bytes(original_bytes)

    manifest_path = tmp_path / "latest.json"
    manifest = build_signed_update_manifest(
        version="1.0.1",
        notes="Security and diagnostics release",
        artifacts={
            "windows-x86_64": (artifact, "https://releases.lifeos.local/v1.0.1/LifeOS_1.0.1_x64_en-US.msi"),
        },
        private_key_b64=priv_b64,
        output_path=manifest_path,
    )

    assert manifest_path.is_file()
    assert manifest["version"] == "1.0.1"
    win_entry = manifest["platforms"]["windows-x86_64"]
    sig = win_entry["signature"]

    # Valid signature passes
    assert verify_signature(original_bytes, sig, pub_b64) is True

    # Tampered payload is rejected
    tampered_bytes = original_bytes[:-1] + bytes([original_bytes[-1] ^ 0xFF])
    assert verify_signature(tampered_bytes, sig, pub_b64) is False

    # Wrong public key is rejected
    assert verify_signature(original_bytes, sig, other_pub_b64) is False

    # Non-HTTPS URL rejected by manifest builder
    with pytest.raises(ValueError, match="HTTPS"):
        generate_update_manifest(
            version="1.0.1",
            notes="Invalid URL test",
            platforms={"windows-x86_64": {"signature": sig, "url": "http://releases.lifeos.local/bad.msi"}},
        )

    # Valid ISO-8601 pub_date accepted
    valid_manifest = generate_update_manifest(
        version="1.0.1",
        notes="Valid ISO date test",
        platforms={"windows-x86_64": {"signature": sig, "url": "https://releases.lifeos.local/ok.msi"}},
        pub_date="2026-11-24T18:00:00Z",
    )
    assert valid_manifest["pub_date"] == "2026-11-24T18:00:00Z"

    # Malformed or timezone-less pub_date rejected
    for bad_date in ("not-an-iso-timestamp", "2026-11-24", "2026-11-24T18:00:00", ""):
        with pytest.raises(ValueError, match="pub_date"):
            generate_update_manifest(
                version="1.0.1",
                notes="Bad date test",
                platforms={"windows-x86_64": {"signature": sig, "url": "https://releases.lifeos.local/ok.msi"}},
                pub_date=bad_date,
            )
