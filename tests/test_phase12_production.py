"""
test_phase12_production.py

Comprehensive deterministic tests for LifeOS Phase 12 — Production LifeOS.
Tests:
- Production configuration validation & fail-fast safeguards
- Single-worker constraint enforcement
- Background watcher lifecycle (start, stop, idempotency)
- Application lifespan and graceful shutdown
- Operational metrics collection & admin /metrics endpoint
- Liveness (/live), health (/health), readiness (/ready) probes
- Coordinated atomic point-in-time backup and restore with SQLite integrity
- Structured JSON logging formatter
"""

import json
import logging
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.knowledge.watcher import (
    is_watcher_running,
    start_background_watcher,
    stop_background_watcher,
)
from app.ops.logging import JSONFormatter
from app.ops.metrics import metrics_collector
from app.storage.backup import create_backup, restore_backup
from app.storage.sqlite import connect_sqlite

TEST_USER_KEY = "test-prod-user-key-12345"
TEST_ADMIN_KEY = "test-prod-admin-key-67890"


@pytest.fixture(autouse=True)
def reset_production_env(monkeypatch):
    """Ensure predictable testing environment for Phase 12 tests."""
    monkeypatch.setattr(config, "API_ENVIRONMENT", "testing")
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", TEST_USER_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_RATE_LIMIT", 100)
    monkeypatch.setattr(config, "API_RATE_WINDOW_SECONDS", 60)
    monkeypatch.setattr(config, "WATCHER_ENABLED", False)
    metrics_collector.reset()
    yield
    stop_background_watcher()


@pytest.fixture
def client():
    return TestClient(app)


# ============================================================
# 1. PRODUCTION CONFIGURATION VALIDATION
# ============================================================

def test_production_config_fails_when_multi_worker(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 4)
    with pytest.raises(ValueError, match="Multi-worker execution"):
        config.validate_config()


def test_production_config_fails_without_auth(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", False)
    with pytest.raises(ValueError, match="cannot be disabled in production"):
        config.validate_config()


def test_production_config_fails_when_short_user_key(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "short-key")
    with pytest.raises(ValueError, match="LIFEOS_API_KEY must be configured with at least 16 characters"):
        config.validate_config()


def test_production_config_fails_when_missing_or_short_admin_key(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "valid-user-key-at-least-16-chars")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "short")
    with pytest.raises(ValueError, match="LIFEOS_API_ADMIN_KEY must be configured with at least 16 characters"):
        config.validate_config()


def test_production_config_fails_when_admin_matches_user_key(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    shared_key = "identical-secret-key-16-chars"
    monkeypatch.setattr(config, "API_KEY", shared_key)
    monkeypatch.setattr(config, "API_ADMIN_KEY", shared_key)
    with pytest.raises(ValueError, match="must be distinct"):
        config.validate_config()


def test_production_config_fails_when_missing_openrouter_key(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "valid-user-key-at-least-16-chars")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "valid-admin-key-at-least-16-chars")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY must be configured"):
        config.validate_config()


def test_production_config_fails_with_wildcard_cors(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "valid-user-key-at-least-16-chars")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "valid-admin-key-at-least-16-chars")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(config, "API_CORS_ORIGINS", ["*"])
    with pytest.raises(ValueError, match=r"Wildcard '\*' in LIFEOS_API_CORS_ORIGINS"):
        config.validate_config()


def test_production_config_fails_with_wildcard_or_testserver_trusted_hosts(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "valid-user-key-at-least-16-chars")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "valid-admin-key-at-least-16-chars")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(config, "API_CORS_ORIGINS", ["https://my-lifeos.com"])
    monkeypatch.setattr(config, "API_TRUSTED_HOSTS", ["localhost", "testserver"])
    with pytest.raises(ValueError, match=r"Wildcard '\*' or 'testserver'"):
        config.validate_config()


def test_production_config_succeeds_when_fully_valid(monkeypatch):
    monkeypatch.setattr(config, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "API_WORKERS", 1)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "valid-user-key-at-least-16-chars")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "valid-admin-key-at-least-16-chars")
    monkeypatch.setenv("OPENROUTER_API_KEY", "valid-openrouter-live-key")
    monkeypatch.setattr(config, "API_CORS_ORIGINS", ["https://app.lifeos.internal"])
    monkeypatch.setattr(config, "API_TRUSTED_HOSTS", ["app.lifeos.internal"])
    # Must validate cleanly without exception
    config.validate_config()


# ============================================================
# 2. BACKGROUND WATCHER LIFECYCLE
# ============================================================

def test_background_watcher_starts_and_stops_cleanly(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    assert is_watcher_running() is False

    # Start watcher
    observer = start_background_watcher(documents_dir=docs_dir, run_initial_ingest=False)
    assert observer is not None
    assert is_watcher_running() is True

    # Idempotent re-invocation returns existing observer
    dup_observer = start_background_watcher(documents_dir=docs_dir, run_initial_ingest=False)
    assert dup_observer == observer

    # Stop watcher
    stopped = stop_background_watcher(timeout=2.0)
    assert stopped is True
    assert is_watcher_running() is False

    # Idempotent repeated stop returns False cleanly
    repeat_stop = stop_background_watcher(timeout=1.0)
    assert repeat_stop is False


# ============================================================
# 3. APPLICATION LIFESPAN & GRACEFUL SHUTDOWN
# ============================================================

def test_application_lifespan_startup_and_shutdown():
    with TestClient(app) as test_client:
        res = test_client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"


# ============================================================
# 4. METRICS RECORDING & ENDPOINT
# ============================================================

def test_metrics_collector_records_requests_and_events():
    metrics_collector.reset()

    metrics_collector.record_request("GET", "/health", 200, 10.0)
    metrics_collector.record_request("POST", "/api/query", 200, 25.0)
    metrics_collector.record_request("GET", "/api/documents", 401, 5.0)
    metrics_collector.record_request("POST", "/api/documents", 429, 2.0)
    metrics_collector.record_request("POST", "/api/query", 500, 50.0)

    metrics_collector.record_query(success=True)
    metrics_collector.record_query(success=False)
    metrics_collector.record_ingestion(success=True)
    metrics_collector.record_reindex(success=True)

    snapshot = metrics_collector.get_metrics()
    assert snapshot["http_requests"]["total"] == 5
    assert snapshot["http_requests"]["by_status"]["2xx"] == 2
    assert snapshot["http_requests"]["by_status"]["4xx"] == 2
    assert snapshot["http_requests"]["by_status"]["5xx"] == 1
    assert snapshot["http_requests"]["auth_failures"] == 1
    assert snapshot["http_requests"]["rate_limit_exceeded"] == 1

    assert snapshot["subsystems"]["queries"]["success"] == 1
    assert snapshot["subsystems"]["queries"]["failed"] == 1
    assert snapshot["subsystems"]["ingestions"]["success"] == 1
    assert snapshot["subsystems"]["reindexing"]["success"] == 1

    lat = snapshot["latency_ms"]
    assert lat["count"] == 5
    assert lat["average"] > 0


def test_metrics_endpoint_access_control(client):
    # 1. Unauthenticated request returns 401
    res_unauth = client.get("/metrics")
    assert res_unauth.status_code == 401

    # 2. Standard user token returns 403 Forbidden
    res_user = client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {TEST_USER_KEY}"},
    )
    assert res_user.status_code == 403

    # 3. Admin token returns 200 OK with metrics
    res_admin = client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "uptime_seconds" in data
    assert "http_requests" in data
    assert "subsystems" in data


# ============================================================
# 5. HEALTH & LIVENESS PROBES
# ============================================================

def test_live_probe_returns_200(client):
    res = client.get("/live")
    assert res.status_code == 200
    assert res.json()["status"] == "live"
    assert "timestamp" in res.json()


def test_health_probe_returns_200(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


# ============================================================
# 6. COORDINATED BACKUP AND RESTORE
# ============================================================

def test_create_and_restore_backup(tmp_path, monkeypatch):
    # Set up isolated directories
    backup_store = tmp_path / "backups"
    data_dir = tmp_path / "data"
    docs_dir = data_dir / "documents"
    chroma_dir = tmp_path / "chroma"

    for d in [backup_store, data_dir, docs_dir, chroma_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Monkeypatch paths
    monkeypatch.setattr(config, "BACKUP_DIR", backup_store)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DOCUMENTS_DIR", docs_dir)
    monkeypatch.setattr(config, "CHROMA_DIR", chroma_dir)

    reg_db = data_dir / "document_registry.db"
    ctx_db = data_dir / "context.db"
    ent_db = data_dir / "entities.db"
    ppl_db = data_dir / "people_registry.db"

    monkeypatch.setattr(config, "REGISTRY_DB", reg_db)
    monkeypatch.setattr(config, "CONTEXT_DB", ctx_db)
    monkeypatch.setattr(config, "ENTITY_DB", ent_db)
    monkeypatch.setattr(config, "PEOPLE_DB", ppl_db)

    # Populate sample SQLite data
    with connect_sqlite(reg_db) as conn:
        conn.execute("CREATE TABLE docs (name TEXT PRIMARY KEY, size INT)")
        conn.execute("INSERT INTO docs VALUES ('file1.txt', 100)")
        conn.commit()

    with connect_sqlite(ctx_db) as conn:
        conn.execute("CREATE TABLE ctx (key TEXT PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO ctx VALUES ('user_pref', 'dark_mode')")
        conn.commit()

    # Create dummy documents
    sample_doc = docs_dir / "sample.txt"
    sample_doc.write_text("Original document content", encoding="utf-8")

    # Create dummy chroma index file
    chroma_file = chroma_dir / "index.bin"
    chroma_file.write_bytes(b"chroma-index-bytes")

    # 1. Create backup
    archive_path = create_backup(destination_dir=backup_store, label="prod_snapshot")
    assert archive_path.exists()
    assert archive_path.name.endswith(".tar.gz")

    # Verify manifest inside tar
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        assert "manifest.json" in names
        assert "databases/document_registry.db" in names
        assert "documents/sample.txt" in names
        assert "chroma_db/index.bin" in names

    # 2. Corrupt/Modify original state
    sample_doc.write_text("CORRUPTED CONTENT", encoding="utf-8")
    with connect_sqlite(reg_db) as conn:
        conn.execute("DELETE FROM docs")
        conn.commit()

    # Verify state is modified
    with connect_sqlite(reg_db) as conn:
        rows = conn.execute("SELECT * FROM docs").fetchall()
        assert len(rows) == 0

    # 3. Restore backup
    restore_target_data = tmp_path / "restored_data"
    restore_target_chroma = tmp_path / "restored_chroma"

    summary = restore_backup(
        archive_path=archive_path,
        target_data_dir=restore_target_data,
        target_chroma_dir=restore_target_chroma,
    )

    assert summary["success"] is True
    assert "document_registry.db" in summary["restored_databases"]
    assert summary["restored_documents"] == 1
    assert summary["chroma_restored"] is True

    # 4. Verify restored databases pass integrity checks and contain original data
    restored_reg = restore_target_data / "document_registry.db"
    with connect_sqlite(restored_reg) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"
        rows = conn.execute("SELECT * FROM docs").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "file1.txt"

    # Verify restored document
    restored_doc = restore_target_data / "documents" / "sample.txt"
    assert restored_doc.read_text(encoding="utf-8") == "Original document content"

    # Verify restored chroma
    restored_chroma_file = restore_target_chroma / "index.bin"
    assert restored_chroma_file.read_bytes() == b"chroma-index-bytes"


# ============================================================
# 7. STRUCTURED JSON LOGGING
# ============================================================

def test_json_formatter_produces_valid_json():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="lifeos.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=100,
        msg="Test event occurred for %s",
        args=("service_x",),
        exc_info=None,
    )
    record.request_id = "req-123-abc"
    record.duration_ms = 42.5

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["service"] == "lifeos"
    assert parsed["logger"] == "lifeos.test"
    assert parsed["message"] == "Test event occurred for service_x"
    assert parsed["request_id"] == "req-123-abc"
    assert parsed["duration_ms"] == 42.5
    assert "timestamp" in parsed
