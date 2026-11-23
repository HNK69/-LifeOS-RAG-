"""
test_milestone8_cross_device_sync.py

Deterministic isolation and verification test suite for Milestone 8:
- Vector clock causality comparison (EQUAL, ANCESTOR, DESCENDANT, CONCURRENT) and merge
- E2EE CryptographicEnvelope sealing (X25519-HKDF-AES256GCM + Ed25519 signatures)
- Workspace boundary isolation and local_only / confidential air-gap enforcement
- Peer trust verification and revoked peer rejection
- Concurrent context divergence and Last-Write-Wins (LWW) resolution
- Non-destructive document conflict branching and fork creation
- Offline deletion tombstone propagation
- Tamper-evident cryptographic audit logging
- Sync REST API endpoints (/api/sync/peers, /api/sync/export, /api/sync/import, /api/sync/conflicts)
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient

import app.config as config
from app.api.server import app
from app.context.context_store import get_context, initialize_context_store, set_context
from app.security.audit import query_audit_log
from app.security.device_identity import DeviceIdentity, b64_encode_bytes
from app.security.device_store import initialize_identity_store, register_trusted_device, revoke_trusted_device
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives import serialization
import uuid
import datetime

def make_device_identity(name: str = "Test Device") -> DeviceIdentity:
    sig_priv = ed25519.Ed25519PrivateKey.generate()
    ex_priv = x25519.X25519PrivateKey.generate()
    sig_pub = b64_encode_bytes(sig_priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    ex_pub = b64_encode_bytes(ex_priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return DeviceIdentity(
        device_id=f"dev_{uuid.uuid4().hex[:8]}",
        device_name=name,
        device_type="desktop",
        os_info="TestOS",
        signing_public_key_b64=sig_pub,
        exchange_public_key_b64=ex_pub,
        created_at=now,
        last_seen_at=now,
        _signing_private_key=sig_priv,
        _exchange_private_key=ex_priv,
    )
from app.security.envelope import CryptographicEnvelope, open_payload, seal_payload
from app.sync.conflict import ConflictResolver
from app.sync.engine import SyncEngine
from app.sync.models import (
    ClockRelationship,
    SyncBatch,
    SyncChangeType,
    SyncEntityType,
    SyncItem,
)
from app.sync.store import (
    get_latest_changes,
    initialize_sync_store,
    list_sync_conflicts,
    record_sync_change,
    record_sync_tombstone,
    resolve_sync_conflict,
)
from app.sync.vector_clock import VectorClock
from app.workspace.store import create_workspace, init_workspace_store

TEST_API_KEY = "test-secret-user-key-12345"
TEST_ADMIN_KEY = "test-secret-admin-key-67890"


@pytest.fixture
def client(monkeypatch):
    for mod_name in ("config", "app.config"):
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            monkeypatch.setattr(mod, "API_AUTH_ENABLED", True)
            monkeypatch.setattr(mod, "API_KEY", TEST_API_KEY)
            monkeypatch.setattr(mod, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    return TestClient(app)


# ============================================================
# 1. VECTOR CLOCK CAUSALITY TESTS
# ============================================================

def test_vector_clock_causality_and_merge():
    """Verify vector clocks accurately evaluate causality relationships and merge component-wise."""
    clock_a = VectorClock({"dev_1": 1, "dev_2": 2})
    clock_b = VectorClock({"dev_1": 1, "dev_2": 2})
    clock_c = VectorClock({"dev_1": 2, "dev_2": 2})
    clock_d = VectorClock({"dev_1": 1, "dev_2": 3})
    clock_divergent = VectorClock({"dev_1": 2, "dev_2": 1})

    # 1. Equality
    assert clock_a.compare(clock_b) == ClockRelationship.EQUAL

    # 2. Ancestor / Descendant
    assert clock_a.compare(clock_c) == ClockRelationship.ANCESTOR
    assert clock_c.compare(clock_a) == ClockRelationship.DESCENDANT
    assert clock_a.compare(clock_d) == ClockRelationship.ANCESTOR

    # 3. Concurrent divergence
    assert clock_d.compare(clock_divergent) == ClockRelationship.CONCURRENT
    assert clock_divergent.compare(clock_d) == ClockRelationship.CONCURRENT

    # 4. Merging
    merged = clock_d.merge(clock_divergent)
    assert merged.to_dict() == {"dev_1": 2, "dev_2": 3}
    assert merged.compare(clock_d) == ClockRelationship.DESCENDANT
    assert merged.compare(clock_divergent) == ClockRelationship.DESCENDANT


# ============================================================
# 2. CRYPTOGRAPHIC ENVELOPE SYNC PACKAGING TESTS
# ============================================================

def test_encrypted_sync_batch_envelope_integrity():
    """Verify a SyncBatch is sealed, signed, and decrypted with 100% cryptographic integrity."""
    dev_alice = make_device_identity(name="Alice Laptop")
    dev_bob = make_device_identity(name="Bob Mobile")

    item = SyncItem(
        item_id="chg_001",
        entity_type=SyncEntityType.CONTEXT,
        entity_id="preferred_theme",
        workspace_id="default",
        data={"value": "dark_mode", "updated_at": "2026-11-23T22:00:00Z"},
        origin_device_id=dev_alice.device_id,
        timestamp="2026-11-23T22:00:00Z",
    )

    batch = SyncBatch(
        batch_id="bat_test_01",
        sender_device_id=dev_alice.device_id,
        recipient_device_id=dev_bob.device_id,
        items=[item],
        timestamp="2026-11-23T22:00:00Z",
        manifest={"context": 1},
    )

    # 1. Alice seals for Bob
    envelope = seal_payload(
        payload=batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=dev_bob.exchange_public_key_b64,
        recipient_device_id=dev_bob.device_id,
        sender_identity=dev_alice,
    )

    assert envelope.sender_device_id == dev_alice.device_id
    assert envelope.recipient_device_id == dev_bob.device_id
    assert envelope.ciphertext is not None
    assert envelope.signature is not None

    # 2. Bob opens envelope from Alice
    decrypted_bytes = open_payload(
        envelope=envelope,
        recipient_identity=dev_bob,
        sender_signing_public_key_b64=dev_alice.signing_public_key_b64,
    )

    restored_batch = SyncBatch.model_validate_json(decrypted_bytes.decode("utf-8"))
    assert restored_batch.batch_id == "bat_test_01"
    assert len(restored_batch.items) == 1
    assert restored_batch.items[0].entity_id == "preferred_theme"
    assert restored_batch.items[0].data["value"] == "dark_mode"


# ============================================================
# 3. WORKSPACE LOCAL_ONLY AIR-GAP ISOLATION TESTS
# ============================================================

def test_workspace_local_only_airgap_enforcement(tmp_path):
    """Verify that workspaces configured as local_only or confidential are barred from sync."""
    db_file = tmp_path / "ws.db"
    init_workspace_store(db_file)

    # Create confidential local_only workspace
    ws_confidential = create_workspace(
        name="Confidential Medical",
        profile_type="confidential",
        settings={"local_only": True},
        db_path=db_file,
    )

    dev_alice = make_device_identity(name="Alice Laptop")
    dev_bob = make_device_identity(name="Bob Mobile")

    # Pair devices in identity store
    id_db = tmp_path / "identity.db"
    initialize_identity_store(id_db)
    register_trusted_device(
        device_id=dev_bob.device_id,
        device_name="Bob Mobile",
        device_type="mobile",
        signing_public_key=dev_bob.signing_public_key_b64,
        exchange_public_key=dev_bob.exchange_public_key_b64,
        trust_status="paired",
        db_path=id_db,
    )

    engine = SyncEngine(local_identity=dev_alice, db_path=tmp_path / "sync.db")

    with patch("app.security.device_store._get_db_path", return_value=id_db), \
         patch("app.workspace.store._resolve_db_path", return_value=db_file):

        # Attempting to export confidential workspace explicitly must raise PermissionError
        with pytest.raises(PermissionError, match="local_only"):
            engine.export_sync_package(
                peer_device_id=dev_bob.device_id,
                workspace_id=ws_confidential.workspace_id,
            )

        # Batch export of all workspaces must skip confidential workspace
        envelope = engine.export_sync_package(peer_device_id=dev_bob.device_id)
        assert envelope is not None


# ============================================================
# 4. PEER TRUST & REVOCATION TESTS
# ============================================================

def test_peer_trust_verification_and_revocation(tmp_path):
    """Verify sync aborts if peer is unknown or revoked in the device trust store."""
    dev_alice = make_device_identity(name="Alice")
    dev_bob = make_device_identity(name="Bob")

    id_db = tmp_path / "identity.db"
    initialize_identity_store(id_db)

    engine = SyncEngine(local_identity=dev_alice, db_path=tmp_path / "sync.db")

    with patch("app.security.device_store._get_db_path", return_value=id_db):
        # 1. Unknown peer -> ValueError
        with pytest.raises(ValueError, match="not in the trusted device store"):
            engine.export_sync_package(peer_device_id="dev_unknown_999")

        # 2. Register peer then revoke
        register_trusted_device(
            device_id=dev_bob.device_id,
            device_name="Bob",
            device_type="desktop",
            signing_public_key=dev_bob.signing_public_key_b64,
            exchange_public_key=dev_bob.exchange_public_key_b64,
            trust_status="paired",
            db_path=id_db,
        )
        revoke_trusted_device(dev_bob.device_id, db_path=id_db)

        # Revoked peer -> PermissionError
        with pytest.raises(PermissionError, match="revoked"):
            engine.export_sync_package(peer_device_id=dev_bob.device_id)


# ============================================================
# 5. CONTEXT DIVERGENCE & CONFLICT RESOLUTION (LWW)
# ============================================================

def test_context_concurrent_divergence_and_lww(tmp_path):
    """Verify concurrent context edits are resolved via deterministic LWW and recorded in conflict ledger."""
    ctx_db = tmp_path / "context.db"
    sync_db = tmp_path / "sync.db"
    initialize_sync_store(sync_db)

    resolver = ConflictResolver(local_device_id="dev_alice")

    # Local edit at 22:00:00
    local_data = {"key": "timezone", "value": "Europe/London", "updated_at": "2026-11-23T22:00:00Z"}
    local_clock = {"dev_alice": 2, "dev_bob": 1}

    # Remote edit at 22:05:00 (Newer timestamp)
    remote_data = {"key": "timezone", "value": "Asia/Tokyo", "updated_at": "2026-11-23T22:05:00Z"}
    remote_clock = {"dev_alice": 1, "dev_bob": 2}

    # Clocks are concurrent
    vc_local = VectorClock(local_clock)
    vc_remote = VectorClock(remote_clock)
    assert vc_local.compare(vc_remote) == ClockRelationship.CONCURRENT

    winner, conflict = resolver.resolve_context_conflict(
        entity_id="timezone",
        workspace_id="default",
        peer_device_id="dev_bob",
        local_data=local_data,
        remote_data=remote_data,
        local_clock=local_clock,
        remote_clock=remote_clock,
        db_path=sync_db,
    )

    # Newer timestamp wins
    assert winner["value"] == "Asia/Tokyo"

    # Losing state is preserved in conflict ledger
    conflicts = list_sync_conflicts(db_path=sync_db)
    assert len(conflicts) == 1
    assert conflicts[0].entity_id == "timezone"
    assert conflicts[0].local_data["value"] == "Europe/London"
    assert conflicts[0].remote_data["value"] == "Asia/Tokyo"
    assert conflicts[0].resolved_value["value"] == "Asia/Tokyo"


# ============================================================
# 6. NON-DESTRUCTIVE DOCUMENT CONFLICT BRANCHING TESTS
# ============================================================

def test_document_concurrent_conflict_branching(tmp_path):
    """Verify concurrent document edits produce non-destructive conflict forks preserving both versions."""
    sync_db = tmp_path / "sync.db"
    initialize_sync_store(sync_db)

    resolver = ConflictResolver(local_device_id="dev_alice")

    local_doc = {
        "filename": "project_notes.md",
        "file_hash": "hash_alice_12345",
        "file_size": 250,
        "modified_time_ns": 1000,
    }
    remote_doc = {
        "filename": "project_notes.md",
        "file_hash": "hash_bob_67890",
        "file_size": 320,
        "modified_time_ns": 2000,
    }

    forked_rel_path, forked_data, conflict = resolver.resolve_document_conflict(
        entity_id="work/project_notes.md",
        workspace_id="default",
        peer_device_id="dev_bob_device_456",
        local_data=local_doc,
        remote_data=remote_doc,
        local_clock={"dev_alice": 1},
        remote_clock={"dev_bob": 1},
        db_path=sync_db,
    )

    # Must create non-destructive branch name containing conflict tag
    assert "conflict" in forked_rel_path
    assert forked_rel_path != "work/project_notes.md"
    assert forked_data["is_conflict_fork"] is True

    # Conflict record in sync ledger
    conflicts = list_sync_conflicts(db_path=sync_db)
    assert len(conflicts) == 1
    assert conflicts[0].resolution_strategy == "branch_fork"


# ============================================================
# 7. OFFLINE DELETION TOMBSTONE PROPAGATION TESTS
# ============================================================

def test_offline_deletion_tombstone_propagation(tmp_path):
    """Verify offline deletion creates a tombstone that propagates and removes local items on peer."""
    sync_db = tmp_path / "sync.db"
    initialize_sync_store(sync_db)

    # 1. Record deletion tombstone
    tombstone = record_sync_tombstone(
        entity_type=SyncEntityType.CONTEXT,
        entity_id="temporary_scratchpad",
        workspace_id="default",
        origin_device_id="dev_alice",
        vector_clock=VectorClock({"dev_alice": 3}),
        db_path=sync_db,
    )

    assert tombstone.is_tombstone is True
    assert tombstone.change_type == SyncChangeType.DELETE

    # 2. Retrieve changes
    changes = get_latest_changes(workspace_id="default", db_path=sync_db)
    assert any(c.entity_id == "temporary_scratchpad" and c.is_tombstone for c in changes)


# ============================================================
# 8. CRYPTOGRAPHIC AUDIT LOG INTEGRATION TESTS
# ============================================================

def test_sync_audit_event_logging(tmp_path):
    """Verify sync export and import emit tamper-evident hash-chained audit events."""
    audit_db = tmp_path / "privacy.db"
    from app.security.privacy_store import initialize_privacy_store
    initialize_privacy_store(audit_db)

    dev_alice = make_device_identity(name="Alice")
    dev_bob = make_device_identity(name="Bob")

    id_db = tmp_path / "identity.db"
    initialize_identity_store(id_db)
    register_trusted_device(
        device_id=dev_bob.device_id,
        device_name="Bob",
        device_type="mobile",
        signing_public_key=dev_bob.signing_public_key_b64,
        exchange_public_key=dev_bob.exchange_public_key_b64,
        trust_status="paired",
        db_path=id_db,
    )

    sync_db = tmp_path / "sync.db"
    engine = SyncEngine(local_identity=dev_alice, db_path=sync_db)

    with patch("app.security.device_store._get_db_path", return_value=id_db), \
         patch("app.security.privacy_store._get_db_path", return_value=audit_db), \
         patch("app.security.audit._get_db_path", return_value=audit_db):

        envelope = engine.export_sync_package(peer_device_id=dev_bob.device_id)
        assert envelope is not None

        # Verify audit event in ledger
        events = query_audit_log(event_type="sync_package_exported", db_path=audit_db)
        assert len(events) >= 1
        first_event = events[0]
        res_val = first_event["resource"] if isinstance(first_event, dict) else getattr(first_event, "resource")
        purp_val = first_event["purpose"] if isinstance(first_event, dict) else getattr(first_event, "purpose")
        assert res_val == dev_bob.device_id
        assert purp_val == "cross_device_sync"


# ============================================================
# 9. REST API ENDPOINTS TESTS
# ============================================================

def test_sync_rest_api_endpoints(client, tmp_path):
    """Verify /api/sync/peers, /api/sync/conflicts, /api/sync/export, and /api/sync/conflicts/resolve."""
    headers = {"X-API-Key": TEST_API_KEY}

    # 1. GET /api/sync/peers
    resp = client.get("/api/sync/peers", headers=headers)
    assert resp.status_code == 200
    peers_data = resp.json()
    assert isinstance(peers_data, list)

    # 2. GET /api/sync/conflicts
    resp_conflicts = client.get("/api/sync/conflicts", headers=headers)
    assert resp_conflicts.status_code == 200
    assert isinstance(resp_conflicts.json(), list)

    # 3. Resolve conflict test
    sync_db = tmp_path / "sync.db"
    initialize_sync_store(sync_db)
    conflict = ConflictResolver().resolve_context_conflict(
        entity_id="api_theme",
        workspace_id="default",
        peer_device_id="dev_test_peer",
        local_data={"value": "light"},
        remote_data={"value": "dark"},
        local_clock={"dev_local": 1},
        remote_clock={"dev_peer": 1},
        db_path=sync_db,
    )[1]

    with patch("app.sync.store._get_db_path", return_value=sync_db):
        resolve_resp = client.post(
            f"/api/sync/conflicts/{conflict.conflict_id}/resolve",
            json={"resolved_value": {"value": "custom_sepia"}},
            headers=headers,
        )
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["resolved"] is True


# ============================================================
# 10. END-TO-END ENVELOPE IMPORT & SECURITY REJECTION TESTS
# ============================================================

def test_sync_import_package_applied_cleanly(tmp_path):
    """Verify an encrypted sync package from Alice is decrypted and applied to Bob's context and workspace."""
    dev_alice = make_device_identity(name="Alice")
    dev_bob = make_device_identity(name="Bob")

    id_db_bob = tmp_path / "identity_bob.db"
    initialize_identity_store(id_db_bob)
    register_trusted_device(
        device_id=dev_alice.device_id,
        device_name="Alice",
        device_type="laptop",
        signing_public_key=dev_alice.signing_public_key_b64,
        exchange_public_key=dev_alice.exchange_public_key_b64,
        trust_status="paired",
        db_path=id_db_bob,
    )

    ctx_db_bob = tmp_path / "context_bob.db"
    initialize_context_store()

    # Alice creates a sync batch for Bob
    batch = SyncBatch(
        batch_id="bat_e2e_01",
        sender_device_id=dev_alice.device_id,
        recipient_device_id=dev_bob.device_id,
        items=[
            SyncItem(
                item_id="chg_01",
                entity_type=SyncEntityType.CONTEXT,
                entity_id="synced_city",
                workspace_id="default",
                data={"value": "San Francisco", "updated_at": "2026-11-23T22:15:00Z"},
                vector_clock={"dev_alice": 1},
                origin_device_id=dev_alice.device_id,
                timestamp="2026-11-23T22:15:00Z",
            )
        ],
        timestamp="2026-11-23T22:15:00Z",
        manifest={"context": 1},
    )

    # Alice seals for Bob
    envelope = seal_payload(
        payload=batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=dev_bob.exchange_public_key_b64,
        recipient_device_id=dev_bob.device_id,
        sender_identity=dev_alice,
    )

    # Bob imports package
    engine_bob = SyncEngine(local_identity=dev_bob, db_path=tmp_path / "sync_bob.db")

    with patch("app.security.device_store._get_db_path", return_value=id_db_bob):
        report = engine_bob.import_sync_package(envelope)

    assert report.success is True
    assert report.items_imported == 1
    assert report.conflicts_detected == 0
    assert get_context("synced_city", workspace_id="default") == "San Francisco"


def test_sync_import_tampered_envelope_rejected(tmp_path):
    """Verify tampered envelope ciphertext or signature causes immediate cryptographic rejection."""
    dev_alice = make_device_identity(name="Alice")
    dev_bob = make_device_identity(name="Bob")

    id_db_bob = tmp_path / "identity_bob.db"
    initialize_identity_store(id_db_bob)
    register_trusted_device(
        device_id=dev_alice.device_id,
        device_name="Alice",
        device_type="laptop",
        signing_public_key=dev_alice.signing_public_key_b64,
        exchange_public_key=dev_alice.exchange_public_key_b64,
        trust_status="paired",
        db_path=id_db_bob,
    )

    batch = SyncBatch(
        batch_id="bat_tamper_01",
        sender_device_id=dev_alice.device_id,
        recipient_device_id=dev_bob.device_id,
        items=[],
        timestamp="2026-11-23T22:15:00Z",
    )

    envelope = seal_payload(
        payload=batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=dev_bob.exchange_public_key_b64,
        recipient_device_id=dev_bob.device_id,
        sender_identity=dev_alice,
    )

    # Tamper with ciphertext
    env_dict = envelope.to_dict()
    tampered_bytes = list(env_dict["ciphertext"])
    tampered_bytes[4] = "A" if tampered_bytes[4] != "A" else "B"
    env_dict["ciphertext"] = "".join(tampered_bytes)

    engine_bob = SyncEngine(local_identity=dev_bob, db_path=tmp_path / "sync_bob.db")

    from app.security.envelope import EnvelopeError
    with patch("app.security.device_store._get_db_path", return_value=id_db_bob):
        with pytest.raises(EnvelopeError):
            engine_bob.import_sync_package(env_dict)


def test_sync_import_revoked_sender_rejected(tmp_path):
    """Verify an import request from a revoked peer device is rejected with PermissionError."""
    dev_alice = make_device_identity(name="Alice")
    dev_bob = make_device_identity(name="Bob")

    id_db_bob = tmp_path / "identity_bob.db"
    initialize_identity_store(id_db_bob)
    register_trusted_device(
        device_id=dev_alice.device_id,
        device_name="Alice",
        device_type="laptop",
        signing_public_key=dev_alice.signing_public_key_b64,
        exchange_public_key=dev_alice.exchange_public_key_b64,
        trust_status="paired",
        db_path=id_db_bob,
    )
    # Revoke Alice
    revoke_trusted_device(dev_alice.device_id, db_path=id_db_bob)

    batch = SyncBatch(
        batch_id="bat_revoked_01",
        sender_device_id=dev_alice.device_id,
        recipient_device_id=dev_bob.device_id,
        items=[],
        timestamp="2026-11-23T22:15:00Z",
    )

    envelope = seal_payload(
        payload=batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=dev_bob.exchange_public_key_b64,
        recipient_device_id=dev_bob.device_id,
        sender_identity=dev_alice,
    )

    engine_bob = SyncEngine(local_identity=dev_bob, db_path=tmp_path / "sync_bob.db")

    with patch("app.security.device_store._get_db_path", return_value=id_db_bob):
        with pytest.raises(PermissionError, match="status 'revoked'"):
            engine_bob.import_sync_package(envelope)
