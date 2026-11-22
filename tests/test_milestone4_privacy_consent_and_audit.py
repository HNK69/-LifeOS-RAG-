"""
test_milestone4_privacy_consent_and_audit.py

Deterministic test suite for LifeOS Milestone 4:
- Granular consent tracking and real-time permission evaluation.
- FilesystemConnector synchronization with dynamic consent boundary.
- Tamper-evident SHA-256 hash-chain cryptographic audit trail.
- Verification and tamper detection on audit records.
- Privacy API routes (/api/privacy/summary, /consent, /audit, /audit/verify, /export, /forget).
- GDPR self-service data export and cascading Right to be Forgotten purge.
- Role-based access control (user vs admin vs unauthenticated).
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.security.privacy_store import (
    initialize_privacy_store,
    get_privacy_db_connection,
)
from app.security.audit import (
    record_audit_event,
    verify_audit_log_integrity,
    query_audit_log,
)
from app.security.consent import (
    grant_consent,
    revoke_consent,
    list_consents,
    get_consent,
    is_consent_granted,
    get_active_consented_paths,
)
from app.connectors.filesystem import FilesystemConnector

TEST_API_KEY = "test-api-key-12345"
TEST_ADMIN_KEY = "test-admin-key-99999"


@pytest.fixture(autouse=True)
def clean_privacy_db(tmp_path, monkeypatch):
    """Isolate privacy DB to a clean temporary file for each test."""
    temp_db = tmp_path / "test_privacy.db"
    monkeypatch.setattr(config, "PRIVACY_DB", temp_db)
    monkeypatch.setattr("app.security.privacy_store.PRIVACY_DB", temp_db)
    monkeypatch.setattr(config, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    initialize_privacy_store(temp_db)
    yield temp_db


@pytest.fixture
def auth_headers():
    """Provide standard user Authorization headers."""
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


@pytest.fixture
def admin_headers():
    """Provide admin Authorization headers."""
    return {"Authorization": f"Bearer {TEST_ADMIN_KEY}"}


# ============================================================================
# Unit Tests: Consent Engine & Permission Evaluation
# ============================================================================

def test_consent_grant_and_retrieval(tmp_path):
    """Verify granting consent stores a valid record and creates an audit entry."""
    target_dir = tmp_path / "photos"
    target_dir.mkdir()

    grant = grant_consent(
        resource_path=str(target_dir),
        scope_type="folder",
        categories=["image"],
        purposes=["indexing"],
        actor="test_user",
    )

    assert grant.consent_id.startswith("csnt_")
    assert grant.status == "active"
    assert "image" in grant.categories
    assert "indexing" in grant.purposes
    assert grant.resource_path == str(target_dir.resolve())
    assert grant.revoked_at is None

    fetched = get_consent(grant.consent_id)
    assert fetched is not None
    assert fetched.consent_id == grant.consent_id
    assert fetched.resource_path == str(target_dir.resolve())


def test_consent_revocation(tmp_path):
    """Verify revoking consent marks status revoked and updates revoked_at."""
    grant = grant_consent(
        resource_path=str(tmp_path),
        categories=["document"],
        purposes=["training"],
        actor="user_1",
    )
    assert grant.status == "active"

    revoked = revoke_consent(grant.consent_id, reason="testing revocation", actor="admin_1")
    assert revoked is True

    fetched = get_consent(grant.consent_id)
    assert fetched is not None
    assert fetched.status == "revoked"
    assert fetched.revoked_at is not None
    assert fetched.revocation_reason == "testing revocation"


def test_realtime_permission_evaluation_path_matching(tmp_path):
    """Verify real-time evaluation handles subpaths and unrelated paths correctly."""
    allowed_root = tmp_path / "allowed_vault"
    allowed_root.mkdir()
    sub_file = allowed_root / "subdir" / "note.txt"
    unrelated_file = tmp_path / "secret" / "secret.txt"

    # Before granting consent
    assert not is_consent_granted(sub_file, purpose="indexing")

    # Grant consent for allowed_root
    grant_consent(
        resource_path=str(allowed_root),
        scope_type="folder",
        purposes=["indexing"],
    )

    # Allowed root and its subdirectories should now be granted
    assert is_consent_granted(allowed_root, purpose="indexing")
    assert is_consent_granted(sub_file, purpose="indexing")

    # Unrelated path remains denied
    assert not is_consent_granted(unrelated_file, purpose="indexing")


def test_realtime_permission_evaluation_purpose_and_category(tmp_path):
    """Verify evaluation respects purpose and data_category filters."""
    grant_consent(
        resource_path=str(tmp_path),
        categories=["finance"],
        purposes=["tax_analysis"],
    )

    # Matching purpose & category
    assert is_consent_granted(tmp_path, purpose="tax_analysis", category="finance")

    # Wrong purpose
    assert not is_consent_granted(tmp_path, purpose="marketing", category="finance")

    # Wrong category
    assert not is_consent_granted(tmp_path, purpose="tax_analysis", category="medical")


def test_expired_consent_evaluation(tmp_path):
    """Verify expired grants are automatically evaluated as inactive."""
    expired_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    grant = grant_consent(
        resource_path=str(tmp_path),
        categories=["audio"],
        purposes=["indexing"],
        expires_at=expired_time,
    )
    assert grant.status == "active"  # Stored status

    # Evaluation dynamically checks expiration
    assert not is_consent_granted(tmp_path, purpose="indexing")


# ============================================================================
# Integration: Connector Authorization Synchronization
# ============================================================================

def test_connector_scans_governed_by_dynamic_consent(tmp_path, monkeypatch):
    """Verify FilesystemConnector dynamically permits scanning via granted consent."""
    external_dir = tmp_path / "external_drive"
    external_dir.mkdir()
    doc = external_dir / "statement.txt"
    doc.write_text("Account statement data.")

    # Restrict static roots to a dummy empty folder
    dummy_empty = tmp_path / "static_empty"
    dummy_empty.mkdir()
    monkeypatch.setattr(config, "AUTHORIZED_CONNECTOR_ROOTS", [dummy_empty])

    connector = FilesystemConnector(folder_path=external_dir)

    # 1. Without consent, connector rejects unauthorized external path
    with pytest.raises(PermissionError) as exc_info:
        connector.discover_items()
    assert "not within authorized connector roots" in str(exc_info.value).lower()

    # 2. Grant dynamic consent for external_dir
    grant = grant_consent(
        resource_path=str(external_dir),
        scope_type="folder",
        categories=["document"],
        purposes=["indexing"],
        actor="device_admin",
    )

    # 3. Scanning now succeeds
    discovered = connector.discover_items()
    assert len(discovered) == 1
    assert discovered[0].title == doc.name

    # 4. Revoking consent immediately blocks subsequent scans
    revoke_consent(grant.consent_id, actor="device_admin")
    with pytest.raises(PermissionError):
        connector.discover_items()


# ============================================================================
# Cryptographic Audit Log & Tamper-Evident Verification
# ============================================================================

def test_tamper_evident_audit_log_hash_chain(clean_privacy_db):
    """Verify sequential events link cryptographic SHA-256 hashes forming a valid chain."""
    evt1 = record_audit_event(
        event_type="consent_grant",
        actor="alice",
        resource="path:/data/notes",
        details={"scope": "notes"},
    )
    assert evt1.prev_hash == "0" * 64
    assert len(evt1.record_hash) == 64

    evt2 = record_audit_event(
        event_type="consent_revoke",
        actor="alice",
        resource="path:/data/notes",
        details={"reason": "user_opt_out"},
    )
    assert evt2.prev_hash == evt1.record_hash

    # Verify clean log integrity
    is_valid, count, err = verify_audit_log_integrity()
    assert is_valid is True
    assert count == 2
    assert err is None


def test_tamper_detection_in_audit_records(clean_privacy_db):
    """Verify tampering with an audit record in the database is detected immediately."""
    record_audit_event("consent_grant", "user1", "res1", details={})
    record_audit_event("consent_grant", "user2", "res2", details={})
    record_audit_event("consent_grant", "user3", "res3", details={})

    is_valid, count, err = verify_audit_log_integrity()
    assert is_valid is True
    assert count == 3

    # Maliciously modify row 2 actor in SQLite
    conn = get_privacy_db_connection()
    with conn:
        conn.execute("UPDATE privacy_audit_log SET actor = 'attacker' WHERE rowid = 2")
    conn.close()

    # Integrity verification must catch the discrepancy
    is_valid, failed_idx, err = verify_audit_log_integrity()
    assert is_valid is False
    assert failed_idx == 1  # 0-indexed row 1 is second row
    assert "Cryptographic tamper detected" in err or "hash" in err.lower()


def test_tamper_detection_broken_chain_pointer(clean_privacy_db):
    """Verify modifying a prev_hash pointer is caught by verification."""
    record_audit_event("consent_grant", "user1", "res1")
    record_audit_event("consent_grant", "user2", "res2")

    # Maliciously alter prev_hash of second record
    conn = get_privacy_db_connection()
    with conn:
        conn.execute("UPDATE privacy_audit_log SET prev_hash = 'badbadbad' WHERE rowid = 2")
    conn.close()

    is_valid, failed_idx, err = verify_audit_log_integrity()
    assert is_valid is False
    assert failed_idx == 1
    assert "Broken hash chain" in err


# ============================================================================
# API Endpoints: /api/privacy
# ============================================================================

def test_api_privacy_summary(auth_headers):
    """Verify /api/privacy/summary returns active counts and verified integrity."""
    with TestClient(app) as client:
        grant_consent(resource_path="/data/1", actor="user")
        grant_consent(resource_path="/data/2", actor="user")

        res = client.get("/api/privacy/summary", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["active_consented_sources"] == 2
        assert data["total_consent_grants"] == 2
        assert data["total_audit_events"] >= 2
        assert data["audit_chain_valid"] is True


def test_api_privacy_consent_crud(auth_headers):
    """Verify creating, listing, and revoking consent via REST endpoints."""
    with TestClient(app) as client:
        # 1. POST /consent
        payload = {
            "resource_path": "C:/Vault/Financials",
            "scope_type": "folder",
            "categories": ["document", "spreadsheet"],
            "purposes": ["indexing", "retrieval"],
        }
        create_res = client.post("/api/privacy/consent", json=payload, headers=auth_headers)
        assert create_res.status_code == 201
        created = create_res.json()
        assert created["consent_id"].startswith("csnt_")
        assert created["status"] == "active"
        consent_id = created["consent_id"]

        # 2. GET /consent
        list_res = client.get("/api/privacy/consent", headers=auth_headers)
        assert list_res.status_code == 200
        items = list_res.json()
        assert any(c["consent_id"] == consent_id for c in items)

        # 3. DELETE /consent/{id}
        del_res = client.delete(f"/api/privacy/consent/{consent_id}", headers=auth_headers)
        assert del_res.status_code == 200
        deleted = del_res.json()
        assert deleted["status"] == "revoked"

        # 4. Verify revocation in list
        list_res2 = client.get("/api/privacy/consent?status=revoked", headers=auth_headers)
        assert any(c["consent_id"] == consent_id for c in list_res2.json())


def test_api_privacy_audit_and_verify(auth_headers):
    """Verify audit log retrieval and real-time cryptographic audit verification API."""
    with TestClient(app) as client:
        # Trigger some actions
        grant_consent(resource_path="/path/a", actor="user_a")
        grant_consent(resource_path="/path/b", actor="user_b")

        # GET /api/privacy/audit
        audit_res = client.get("/api/privacy/audit", headers=auth_headers)
        assert audit_res.status_code == 200
        records = audit_res.json()
        assert len(records) >= 2
        assert "record_hash" in records[0]

        # GET /api/privacy/audit/verify
        verify_res = client.get("/api/privacy/audit/verify", headers=auth_headers)
        assert verify_res.status_code == 200
        vdata = verify_res.json()
        assert vdata["valid"] is True
        assert vdata["verified_records"] >= 2


def test_api_privacy_export_gdpr(auth_headers):
    """Verify GDPR self-service export creates comprehensive data archive bundle."""
    with TestClient(app) as client:
        grant_consent(resource_path="/vault/notes", actor="export_user")

        res = client.post(
            "/api/privacy/export",
            headers=auth_headers,
        )
        assert res.status_code == 200
        bundle = res.json()
        assert "exported_at" in bundle
        assert "device_id" in bundle
        assert "consents" in bundle
        assert len(bundle["consents"]) >= 1
        assert "audit_log" in bundle


def test_api_privacy_forget_cascading_purge(admin_headers, auth_headers, tmp_path):
    """Verify Right to be Forgotten cascading purge requires admin and purges target data."""
    with TestClient(app) as client:
        target_path = str(tmp_path / "to_delete")

        # Regular user attempting /forget gets 403 Forbidden
        forbidden_res = client.post(
            "/api/privacy/forget",
            json={"target_type": "path", "target_value": target_path},
            headers=auth_headers,
        )
        assert forbidden_res.status_code == 403

        # Admin executing /forget succeeds
        purge_res = client.post(
            "/api/privacy/forget",
            json={"target_type": "path", "target_value": target_path},
            headers=admin_headers,
        )
        assert purge_res.status_code == 200
        pdata = purge_res.json()
        assert pdata["success"] is True

        # An immutable data_purge audit entry must be recorded in the hash chain
        is_valid, _, _ = verify_audit_log_integrity()
        assert is_valid is True

        audit_events = query_audit_log(event_type="data_purge")
        assert len(audit_events) >= 1
        assert audit_events[0]["actor"] == "admin"


def test_api_privacy_unauthenticated_rejection():
    """Verify unauthenticated requests are rejected with 401 Unauthorized."""
    with TestClient(app) as client:
        endpoints = [
            ("GET", "/api/privacy/summary"),
            ("GET", "/api/privacy/consent"),
            ("POST", "/api/privacy/consent"),
            ("DELETE", "/api/privacy/consent/dummy"),
            ("GET", "/api/privacy/audit"),
            ("GET", "/api/privacy/audit/verify"),
            ("POST", "/api/privacy/export"),
            ("POST", "/api/privacy/forget"),
        ]

        for method, path in endpoints:
            if method == "GET":
                res = client.get(path)
            elif method == "DELETE":
                res = client.delete(path)
            else:
                res = client.post(path, json={})
            assert res.status_code == 401, f"{method} {path} did not return 401"
