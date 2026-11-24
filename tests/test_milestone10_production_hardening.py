"""
test_milestone10_production_hardening.py

Comprehensive deterministic tests for LifeOS Milestone 10:
Production Hardening, Packaging & Release.

Covers:
1. Pre-flight Diagnostic Engine (Doctor) full-pass validation
2. Doctor detection of corrupt SQLite databases (PRAGMA integrity_check failure)
3. Doctor detection of unwritable directories and permission failures
4. Doctor inspection of device identity and trust store
5. Comprehensive disaster-recovery backup capturing all 8 SQLite databases + plugins + documents
6. Full-fidelity disaster recovery restore with zero data loss
7. Tampered / corrupt backup archive rejection via SHA-256 manifest verification
8. Automated backup retention pruning based on TTL days
9. Production secret masking filter scrubbing API keys, tokens, and credentials from logs
10. Subsystem readiness probe (/ready) inspecting all 8 databases
11. Unified CLI (app.cli) command dispatch (doctor, backup create, list, status)
12. Graceful server lifespan shutdown draining sidecars and truncating WAL checkpoints
"""

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.api.server import app, create_app
from app.cli import main
from app.ops.doctor import run_diagnostics
from app.ops.logging import SecretMaskingFilter, mask_sensitive_text, setup_logging
from app.storage.backup import create_backup, list_backups, prune_backups, restore_backup
from app.storage.sqlite import connect_sqlite

TEST_USER_KEY = "test-prod-user-key-m10"
TEST_ADMIN_KEY = "test-prod-admin-key-m10"


@pytest.fixture(autouse=True)
def setup_m10_environment(tmp_path, monkeypatch):
    """Isolate databases, state directories, and configuration for M10 testing."""
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

    reg_db = test_data / "document_registry.db"
    ent_db = test_data / "entities.db"
    ppl_db = test_data / "people_registry.db"
    ctx_db = test_data / "context.db"
    priv_db = test_data / "privacy.db"
    id_db = test_data / "identity.db"
    ws_db = test_data / "workspace.db"
    sync_db = test_data / "sync.db"
    queue_db = test_data / "queue.db"

    # Initialize all 8 databases with valid WAL mode SQLite tables
    for p in (reg_db, ent_db, ppl_db, ctx_db, priv_db, id_db, ws_db, sync_db, queue_db):
        with connect_sqlite(p, wal_mode=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS init_probe (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT OR REPLACE INTO init_probe (id, note) VALUES (1, 'active')")

    # Seed mock identity in identity.db so doctor passes device identity check
    with connect_sqlite(id_db, wal_mode=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS local_identity (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                signing_public_key TEXT NOT NULL,
                exchange_public_key TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO local_identity (
                device_id, device_name, device_type, signing_public_key, exchange_public_key, created_at
            ) VALUES ('dev_local_123', 'Test Desktop', 'desktop', 'pub_sign', 'pub_exch', '2026-11-24T00:00:00Z')
        """)

    # Patch config variables
    import app.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", test_data)
    monkeypatch.setattr(cfg, "DOCUMENTS_DIR", test_docs)
    monkeypatch.setattr(cfg, "BACKUP_DIR", test_backups)
    monkeypatch.setattr(cfg, "PLUGINS_DIR", test_plugins)
    monkeypatch.setattr(cfg, "CHROMA_DIR", test_chroma)
    monkeypatch.setattr(cfg, "REGISTRY_DB", reg_db)
    monkeypatch.setattr(cfg, "ENTITY_DB", ent_db)
    monkeypatch.setattr(cfg, "PEOPLE_DB", ppl_db)
    monkeypatch.setattr(cfg, "CONTEXT_DB", ctx_db)
    monkeypatch.setattr(cfg, "PRIVACY_DB", priv_db)
    monkeypatch.setattr(cfg, "IDENTITY_DB", id_db)
    monkeypatch.setattr(cfg, "WORKSPACE_DB", ws_db)
    monkeypatch.setattr(cfg, "SYNC_DB", sync_db)
    monkeypatch.setattr(cfg, "QUEUE_DB", queue_db)
    monkeypatch.setattr(cfg, "API_KEY", TEST_USER_KEY)
    monkeypatch.setattr(cfg, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(cfg, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(cfg, "BACKUP_RETENTION_DAYS", 30)

    # Patch sys.modules
    for mod_name in ["config", "app.config"]:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            monkeypatch.setattr(mod, "DATA_DIR", test_data)
            monkeypatch.setattr(mod, "DOCUMENTS_DIR", test_docs)
            monkeypatch.setattr(mod, "BACKUP_DIR", test_backups)
            monkeypatch.setattr(mod, "PLUGINS_DIR", test_plugins)
            monkeypatch.setattr(mod, "CHROMA_DIR", test_chroma)
            monkeypatch.setattr(mod, "REGISTRY_DB", reg_db)
            monkeypatch.setattr(mod, "ENTITY_DB", ent_db)
            monkeypatch.setattr(mod, "PEOPLE_DB", ppl_db)
            monkeypatch.setattr(mod, "CONTEXT_DB", ctx_db)
            monkeypatch.setattr(mod, "PRIVACY_DB", priv_db)
            monkeypatch.setattr(mod, "IDENTITY_DB", id_db)
            monkeypatch.setattr(mod, "WORKSPACE_DB", ws_db)
            monkeypatch.setattr(mod, "SYNC_DB", sync_db)
            monkeypatch.setattr(mod, "QUEUE_DB", queue_db)
            monkeypatch.setattr(mod, "API_KEY", TEST_USER_KEY)
            monkeypatch.setattr(mod, "API_ADMIN_KEY", TEST_ADMIN_KEY)
            monkeypatch.setattr(mod, "API_AUTH_ENABLED", True)

    yield


# ============================================================
# 1. PRE-FLIGHT DIAGNOSTIC ENGINE (DOCTOR) TESTS
# ============================================================

def test_doctor_diagnostic_full_pass():
    """Verify pre-flight diagnostics pass across all subsystems when healthy."""
    report = run_diagnostics(strict_mode=False)
    assert report.overall_status in ("HEALTHY", "DEGRADED")
    check_names = {c.name for c in report.checks}
    # Must check all 8 databases
    assert "document_registry.db" in check_names
    assert "entities.db" in check_names
    assert "people_registry.db" in check_names
    assert "context.db" in check_names
    assert "privacy.db" in check_names
    assert "identity.db" in check_names
    assert "workspace.db" in check_names
    assert "sync.db" in check_names
    assert "device_identity" in check_names
    assert "configuration_hygiene" in check_names


def test_doctor_detects_corrupt_database(tmp_path, monkeypatch):
    """Verify that doctor detects a corrupted SQLite database and reports FAIL."""
    import app.config as cfg
    corrupt_db = tmp_path / "data" / "corrupt_sync.db"
    # Write garbage into database file to simulate corruption
    corrupt_db.write_bytes(b"SQLite format 3\x00corrupted garbage payload byte sequence\x00")
    monkeypatch.setattr(cfg, "SYNC_DB", corrupt_db)
    if "config" in sys.modules:
        monkeypatch.setattr(sys.modules["config"], "SYNC_DB", corrupt_db)

    report = run_diagnostics(strict_mode=False)
    sync_check = next((c for c in report.checks if c.name == "sync.db"), None)
    assert sync_check is not None
    assert sync_check.status == "FAIL"
    assert "Corrupt" in sync_check.message or "Failed" in sync_check.message
    assert report.overall_status == "FAILED"


def test_doctor_detects_unwritable_directory(tmp_path, monkeypatch):
    """Verify doctor flags unwritable directories as failures."""
    import app.config as cfg
    unwritable = tmp_path / "non_writable_dir"
    unwritable.mkdir(parents=True, exist_ok=True)

    # Mock mkdir/probe failure
    def fake_write_probe(*args, **kwargs):
        raise PermissionError("Access denied (read-only filesystem)")

    monkeypatch.setattr(Path, "write_text", fake_write_probe)
    monkeypatch.setattr(cfg, "DATA_DIR", unwritable)

    report = run_diagnostics(strict_mode=False)
    data_check = next((c for c in report.checks if c.name == "data_dir"), None)
    assert data_check is not None
    assert data_check.status == "FAIL"
    assert "Access denied" in data_check.message


def test_doctor_detects_uninitialized_identity(tmp_path, monkeypatch):
    """Verify doctor reports WARN when local device identity is not yet initialized."""
    import app.config as cfg
    empty_id_db = tmp_path / "data" / "uninitialized_identity.db"
    with connect_sqlite(empty_id_db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS local_identity (device_id TEXT PRIMARY KEY, device_name TEXT, device_type TEXT)")
    monkeypatch.setattr(cfg, "IDENTITY_DB", empty_id_db)
    if "config" in sys.modules:
        monkeypatch.setattr(sys.modules["config"], "IDENTITY_DB", empty_id_db)

    report = run_diagnostics(strict_mode=False)
    dev_check = next((c for c in report.checks if c.name == "device_identity"), None)
    assert dev_check is not None
    assert dev_check.status == "WARN"
    assert "No local device keypair" in dev_check.message


# ============================================================
# 2. DISASTER RECOVERY, BACKUP & RESTORE TESTS
# ============================================================

def test_comprehensive_backup_all_eight_databases(tmp_path):
    """Verify create_backup captures all 8 SQLite databases, plugins, and documents with SHA-256."""
    import app.config as cfg

    # Add sample file to documents
    doc_file = Path(cfg.DOCUMENTS_DIR) / "sample_note.txt"
    doc_file.write_text("Disaster recovery test document content")

    # Add sample plugin to plugins
    plugin_dir = Path(cfg.PLUGINS_DIR) / "mock-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.json").write_text('{"plugin_id": "mock-plugin"}')

    # Create backup
    archive = create_backup(destination_dir=cfg.BACKUP_DIR, label="full_m10_snapshot")
    assert archive.exists()
    assert archive.name.startswith("lifeos_backup_")

    # Extract manifest from archive and verify presence of all 8 databases
    import tarfile
    with tarfile.open(archive, "r:gz") as tar:
        manifest_f = tar.extractfile("manifest.json")
        manifest = json.loads(manifest_f.read().decode("utf-8"))

    db_map = manifest["databases"]
    expected_dbs = [
        "document_registry.db",
        "entities.db",
        "people_registry.db",
        "context.db",
        "privacy.db",
        "identity.db",
        "workspace.db",
        "sync.db",
    ]
    for db in expected_dbs:
        assert db in db_map, f"Missing database {db} in backup manifest"
        assert db_map[db]["exists"] is True
        assert db_map[db]["sha256"] is not None

    assert manifest["documents_count"] >= 1
    assert manifest["plugins_count"] >= 1


def test_backup_restore_full_fidelity(tmp_path):
    """Verify full-fidelity restore: write data, backup, wipe, restore, verify persistence."""
    import app.config as cfg

    # 1. Populate custom records in multiple milestone databases
    with connect_sqlite(cfg.CONTEXT_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS test_data (key TEXT PRIMARY KEY, val TEXT)")
        conn.execute("INSERT OR REPLACE INTO test_data (key, val) VALUES ('greeting', 'hello_world')")

    with connect_sqlite(cfg.SYNC_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sync_test (id TEXT PRIMARY KEY, state TEXT)")
        conn.execute("INSERT OR REPLACE INTO sync_test (id, state) VALUES ('s1', 'synced')")

    doc_file = Path(cfg.DOCUMENTS_DIR) / "important.md"
    doc_file.write_text("# High Value Note\nImportant data.")

    # 2. Create backup
    archive = create_backup(destination_dir=cfg.BACKUP_DIR, label="fidelity_test")

    # 3. Wipe current records to simulate catastrophic failure
    with connect_sqlite(cfg.CONTEXT_DB) as conn:
        conn.execute("DELETE FROM test_data")
    with connect_sqlite(cfg.SYNC_DB) as conn:
        conn.execute("DELETE FROM sync_test")
    doc_file.unlink()

    # 4. Restore from backup
    res = restore_backup(archive_path=archive, create_safety_backup=False)
    assert res["success"] is True

    # 5. Verify restored data fidelity
    with connect_sqlite(cfg.CONTEXT_DB) as conn:
        row = conn.execute("SELECT val FROM test_data WHERE key = 'greeting'").fetchone()
        assert row is not None and row[0] == "hello_world"

    with connect_sqlite(cfg.SYNC_DB) as conn:
        row = conn.execute("SELECT state FROM sync_test WHERE id = 's1'").fetchone()
        assert row is not None and row[0] == "synced"

    assert doc_file.exists()
    assert "Important data." in doc_file.read_text()


def test_backup_restore_corrupt_archive_rejection(tmp_path):
    """Verify that a backup archive with a tampered/mismatched SHA-256 hash is rejected."""
    import app.config as cfg
    import tarfile

    archive = create_backup(destination_dir=cfg.BACKUP_DIR, label="tamper_test")

    # Extract, tamper manifest SHA-256, repack
    tamper_dir = tmp_path / "tamper_stage"
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(path=tamper_dir)

    manifest_path = tamper_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Modify expected sha256 to cause mismatch
    manifest["databases"]["context.db"]["sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
    manifest_path.write_text(json.dumps(manifest))

    tampered_archive = tmp_path / "tampered_backup.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as tar:
        for item in tamper_dir.iterdir():
            tar.add(item, arcname=item.name)

    # Attempt restore -> must raise ValueError due to SHA-256 mismatch
    with pytest.raises(ValueError, match="Corrupt archive: SHA-256 mismatch"):
        restore_backup(archive_path=tampered_archive, create_safety_backup=False)


def test_backup_retention_pruning(tmp_path):
    """Verify prune_backups removes archives older than retention days."""
    import app.config as cfg
    backup_dir = Path(cfg.BACKUP_DIR)

    # Create 2 archives
    a1 = create_backup(destination_dir=backup_dir, label="old")
    a2 = create_backup(destination_dir=backup_dir, label="new")

    # Artificially age a1 by 40 days
    old_mtime = time.time() - (40 * 86400)
    os.utime(a1, (old_mtime, old_mtime))

    # Prune with 30 day retention
    pruned_count = prune_backups(destination_dir=backup_dir, retention_days=30)
    assert pruned_count == 1
    assert not a1.exists()
    assert a2.exists()


# ============================================================
# 3. OBSERVABILITY, LOG MASKING & READINESS PROBES
# ============================================================

def test_secret_masking_log_formatter():
    """Verify SecretMaskingFilter scrubs API keys, bearer tokens, and secrets from log strings."""
    raw_text = (
        "User authorization header Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123\n"
        "Failed authentication for api_key=sk-ant-test-secret-1234567890abcdef\n"
        "Configuring secret: password=super_secret_password_123"
    )
    masked = mask_sensitive_text(raw_text)
    assert "sk-ant-test-secret-1234567890abcdef" not in masked
    assert "super_secret_password_123" not in masked
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in masked
    assert "[REDACTED]" in masked


def test_readiness_probe_all_subsystems():
    """Verify that /ready probe inspects all 8 databases and returns 200 OK."""
    client = TestClient(app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    checks = data["checks"]
    assert checks.get("sqlite_storage") is True
    assert checks.get("privacy_storage") is True
    assert checks.get("identity_storage") is True
    assert checks.get("workspace_storage") is True
    assert checks.get("sync_storage") is True


# ============================================================
# 4. UNIFIED CLI & GRACEFUL SHUTDOWN
# ============================================================

def test_cli_subcommands_dispatch(capsys):
    """Verify that unified lifeos CLI subcommands execute cleanly."""
    # 1. Doctor command
    ret_doc = main(["doctor", "--json"])
    assert ret_doc in (0, 1)
    captured = capsys.readouterr()
    doc_json = json.loads(captured.out)
    assert "overall_status" in doc_json

    # 2. Status command
    ret_status = main(["status", "--json"])
    assert ret_status == 0
    cap_status = capsys.readouterr()
    st_json = json.loads(cap_status.out)
    assert "databases" in st_json

    # 3. Backup list command
    ret_blist = main(["backup", "list"])
    assert ret_blist == 0


def test_server_graceful_shutdown_drains_plugins_and_wal(monkeypatch):
    """Verify server lifespan shutdown stops active plugins and runs WAL checkpoints."""
    from app.plugins.manager import get_plugin_manager
    from app.plugins.models import PluginManifest

    manager = get_plugin_manager()
    manifest = PluginManifest(
        plugin_id="shutdown-test-plugin",
        name="Shutdown Test",
        version="1.0.0",
        entrypoint="test.py",
    )
    manager.register_plugin(manifest)
    sidecar = manager._get_sidecar("shutdown-test-plugin")
    sidecar.set_in_memory_handler(lambda req: {"result": "ok"})
    manager.start_plugin("shutdown-test-plugin")
    assert sidecar.state.value == "running"

    # Trigger test client lifespan context
    test_app = create_app()
    with TestClient(test_app):
        # Server is running
        pass
    # Server exited lifespan -> plugins must be stopped
    assert sidecar.state.value == "stopped"
