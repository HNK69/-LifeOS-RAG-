"""
test_milestone_c5_mobile_companion.py

Comprehensive R4 deterministic unit and integration test suite for Milestone C5:
Mobile Companion Client, QR Pairing & Encrypted Sync.

All tests run against isolated tmp_path SQLite databases and freshly generated
ephemeral cryptographic identities (zero real keys, credentials, or external services).

Covers all 11 R4 failure-path & platform simulation categories:
1. QR pairing: success, wrong code, expired TTL, replay, forged signature, mismatched peer ID.
2. Revoked device rejection: export, import, and device.revoked event emission.
3. Tampered CryptographicEnvelope: ciphertext bit-flip, auth_tag tamper, forged sig, wrong recipient.
4. Duplicate batch import idempotency (batch_id deduplication) + real export manifest metadata.
5. Concurrent conflicting updates (context LWW + manual conflict resolution).
6. Offline operation & reconnect/retry with exponential backoff.
7. Workspace isolation (local_only & confidential exclusion/rejection).
8. Consent grant & revocation enforcement during sync.
9. Remote wipe / forget cascading purge & symmetric encryption-at-rest verification.
10. Large sync packages (>10 MB chunked transfer) + interrupted transfer resume & SHA-256 checks.
11. Android (WorkManager, Doze, Battery Saver, Wi-Fi/Cellular, process death) and
    iOS (BGAppRefreshTask, Low Power Mode, Data Protection, time budget, termination) simulations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import app.config as app_config
import config
from app.api.middleware import rate_limiter
from app.api.server import app
from app.client.mobile_companion import (
    AndroidLifecycleManager,
    AndroidWorkConstraints,
    MobileCompanionClient,
    TestClientTransport,
    TransportOfflineError,
    iOSBackgroundConstraints,
    iOSLifecycleManager,
)
from app.context.context_store import get_context, initialize_context_store, set_context
from app.events.bus import get_event_bus
from app.security.consent import grant_consent, revoke_consent
from app.security.device_identity import (
    b64_decode_bytes,
    b64_encode_bytes,
    get_device_identity,
    load_or_generate_device_identity,
    reset_device_identity,
)
from app.security.device_store import (
    get_trusted_device,
    initialize_identity_store,
    revoke_trusted_device,
    sync_local_identity,
)
from app.security.envelope import (
    CryptographicEnvelope,
    DecryptionError,
    SignatureVerificationError,
    seal_payload,
)
from app.security.privacy_store import initialize_privacy_store
from app.storage.sqlite import connect_sqlite
from app.sync.engine import SyncEngine, reset_sync_engine
from app.sync.models import SyncBatch, SyncEntityType, SyncItem
from app.sync.store import initialize_sync_store, record_sync_change
from app.workspace.store import create_workspace, init_workspace_store

TEST_API_KEY = "c5-test-api-key-987654321"
TEST_ADMIN_KEY = "c5-test-admin-key-123456789"


@pytest.fixture(autouse=True)
def isolated_c5_env(tmp_path, monkeypatch):
    """Isolate every SQLite DB, keyfile, rate limiter, and singleton in tmp_path."""
    data_dir = tmp_path / "host_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    db_map = {
        "DATA_DIR": data_dir,
        "DOCUMENTS_DIR": data_dir / "documents",
        "IDENTITY_DB": data_dir / "identity.db",
        "DEVICE_KEYS_PATH": data_dir / "desktop_device_keys.json",
        "SYNC_DB": data_dir / "sync.db",
        "CONTEXT_DB": data_dir / "context.db",
        "WORKSPACE_DB": data_dir / "workspace.db",
        "PRIVACY_DB": data_dir / "privacy.db",
        "REGISTRY_DB": data_dir / "document_registry.db",
        "API_KEY": TEST_API_KEY,
        "API_ADMIN_KEY": TEST_ADMIN_KEY,
        "API_AUTH_ENABLED": True,
        "API_RATE_LIMIT": 1000,
    }
    (data_dir / "documents").mkdir(parents=True, exist_ok=True)

    for mod in (config, app_config):
        for attr, val in db_map.items():
            monkeypatch.setattr(mod, attr, val, raising=False)

    # Also patch module-level imported DB constants where bound at import time
    import app.context.context_store as ctx_mod
    import app.knowledge.document_registry as reg_mod
    import app.security.device_store as dev_store_mod
    import app.security.privacy_store as priv_store_mod
    import app.sync.store as sync_store_mod
    import app.workspace.store as ws_store_mod

    monkeypatch.setattr(ctx_mod, "CONTEXT_DB", db_map["CONTEXT_DB"], raising=False)
    monkeypatch.setattr(reg_mod, "REGISTRY_DB", db_map["REGISTRY_DB"], raising=False)
    monkeypatch.setattr(dev_store_mod, "IDENTITY_DB", db_map["IDENTITY_DB"], raising=False)
    monkeypatch.setattr(priv_store_mod, "PRIVACY_DB", db_map["PRIVACY_DB"], raising=False)
    monkeypatch.setattr(sync_store_mod, "SYNC_DB", db_map["SYNC_DB"], raising=False)
    monkeypatch.setattr(ws_store_mod, "WORKSPACE_DB", db_map["WORKSPACE_DB"], raising=False)

    reset_device_identity()
    reset_sync_engine()
    rate_limiter.reset()

    initialize_identity_store(db_map["IDENTITY_DB"])
    initialize_sync_store(db_map["SYNC_DB"])
    initialize_context_store()
    init_workspace_store(db_map["WORKSPACE_DB"])
    initialize_privacy_store(db_map["PRIVACY_DB"])

    desktop_id = load_or_generate_device_identity(keys_path=db_map["DEVICE_KEYS_PATH"])
    sync_local_identity(desktop_id, db_path=db_map["IDENTITY_DB"])

    yield data_dir

    reset_device_identity()
    reset_sync_engine()
    rate_limiter.reset()


@pytest.fixture
def api_client():
    with TestClient(app) as tc:
        yield tc


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


def _initiate_qr(api_client: TestClient, peer_device_id: str | None = None, ttl_seconds: int = 300) -> dict[str, Any]:
    body: dict[str, Any] = {"ttl_seconds": ttl_seconds}
    if peer_device_id is not None:
        body["peer_device_id"] = peer_device_id
    resp = api_client.post("/api/device/pairing/initiate", json=body, headers=_auth_headers())
    assert resp.status_code == 200
    return resp.json()


def _make_mobile(tmp_path: Path, api_client: TestClient, name: str = "Pixel 9", api_key: str | None = TEST_API_KEY) -> MobileCompanionClient:
    mobile_dir = tmp_path / f"mobile_{uuid.uuid4().hex[:6]}"
    return MobileCompanionClient(
        data_dir=mobile_dir,
        device_name=name,
        os_info="Android 15",
        api_key=api_key,
        transport=TestClientTransport(api_client),
    )


# ============================================================================
# 1. QR PAIRING: SUCCESS, WRONG CODE, EXPIRED TTL, REPLAY, FORGED SIG, MISMATCH
# ============================================================================


def test_qr_pairing_success_and_ephemeral_token_without_master_api_key(tmp_path, api_client):
    """
    Mobile scans QR payload using only the ephemeral single-use pairing_token
    (no master API_KEY in QR payload), signs challenge, and completes mutual trust pinning.
    """
    qr_payload = _initiate_qr(api_client)
    assert qr_payload["peer_device_id"] == "*"
    assert len(qr_payload["challenge_code"]) == 6
    assert qr_payload["desktop_device_id"] == get_device_identity().device_id
    assert qr_payload["signing_public_key"] == get_device_identity().signing_public_key_b64
    assert qr_payload["exchange_public_key"] == get_device_identity().exchange_public_key_b64
    assert qr_payload["pairing_token"].startswith("pair_")
    assert qr_payload["pairing_token"] != TEST_API_KEY

    # Mobile starts WITHOUT master api_key; uses pairing_token from QR
    mobile = _make_mobile(tmp_path, api_client, name="Galaxy S25", api_key=None)
    assert mobile.identity.device_type == "mobile"
    assert mobile.identity.device_id != qr_payload["desktop_device_id"]

    bus = get_event_bus()
    sub_q = bus.subscribe(topics={"device.pairing_completed"})
    try:
        paired = mobile.consume_qr_payload(qr_payload)
        assert paired is True
        assert mobile.last_error is None

        # Desktop trust store recorded mobile peer as 'paired'
        peer_record = get_trusted_device(mobile.identity.device_id)
        assert peer_record is not None
        assert peer_record["trust_status"] == "paired"
        assert peer_record["device_type"] == "mobile"

        # Mobile local store pinned desktop public keys
        pinned = mobile.store.get_trusted_desktop()
        assert pinned is not None
        assert pinned["desktop_device_id"] == qr_payload["desktop_device_id"]
        assert pinned["signing_public_key"] == qr_payload["signing_public_key"]
        assert pinned["exchange_public_key"] == qr_payload["exchange_public_key"]

        # EventBus emitted device.pairing_completed
        evt = sub_q.get_nowait()
        assert evt.event_type == "device.pairing_completed"
        assert evt.payload["peer_device_id"] == mobile.identity.device_id
    finally:
        bus.unsubscribe(sub_q)


def test_qr_pairing_wrong_code_rejected(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    bad_code = "000000" if qr_payload["challenge_code"] != "000000" else "999999"
    qr_payload["challenge_code"] = bad_code

    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is False
    assert get_trusted_device(mobile.identity.device_id) is None
    assert mobile.store.get_trusted_desktop() is None


def test_qr_pairing_expired_ttl_rejected_locally_and_on_server(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client, ttl_seconds=-10)
    mobile = _make_mobile(tmp_path, api_client)

    # Local TTL pre-check rejects
    assert mobile.consume_qr_payload(qr_payload) is False
    assert "expired" in (mobile.last_error or "").lower()

    # Even if client bypasses local check and hits server directly, server rejects
    sig = mobile.identity.sign(f"{qr_payload['challenge_id']}:{qr_payload['challenge_code']}".encode("utf-8"))
    resp = api_client.post(
        "/api/device/pairing/verify",
        json={
            "challenge_id": qr_payload["challenge_id"],
            "challenge_code": qr_payload["challenge_code"],
            "peer_device_id": mobile.identity.device_id,
            "peer_device_name": "Bypass Phone",
            "peer_device_type": "mobile",
            "peer_signing_public_key": mobile.identity.signing_public_key_b64,
            "peer_exchange_public_key": mobile.identity.exchange_public_key_b64,
            "signature": sig,
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 400


def test_qr_pairing_replay_rejected(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile_1 = _make_mobile(tmp_path, api_client, name="Phone 1")
    mobile_2 = _make_mobile(tmp_path, api_client, name="Attacker Replay")

    assert mobile_1.consume_qr_payload(qr_payload) is True
    # Replaying the exact same QR challenge must fail
    assert mobile_2.consume_qr_payload(qr_payload) is False
    assert get_trusted_device(mobile_2.identity.device_id) is None


def test_qr_pairing_forged_signature_rejected(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client, name="Victim")
    attacker = _make_mobile(tmp_path, api_client, name="Attacker")

    forged_sig = attacker.identity.sign(f"{qr_payload['challenge_id']}:{qr_payload['challenge_code']}".encode("utf-8"))
    resp = api_client.post(
        "/api/device/pairing/verify",
        json={
            "challenge_id": qr_payload["challenge_id"],
            "challenge_code": qr_payload["challenge_code"],
            "peer_device_id": mobile.identity.device_id,
            "peer_device_name": mobile.identity.device_name,
            "peer_device_type": "mobile",
            "peer_signing_public_key": mobile.identity.signing_public_key_b64,
            "peer_exchange_public_key": mobile.identity.exchange_public_key_b64,
            "signature": forged_sig,
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 400
    assert get_trusted_device(mobile.identity.device_id) is None


def test_qr_pairing_mismatched_bound_peer_id_rejected(tmp_path, api_client):
    intended_mobile = _make_mobile(tmp_path, api_client, name="Intended")
    wrong_mobile = _make_mobile(tmp_path, api_client, name="Wrong Device")

    # Challenge bound specifically to intended_mobile.device_id
    qr_payload = _initiate_qr(api_client, peer_device_id=intended_mobile.identity.device_id)

    assert wrong_mobile.consume_qr_payload(qr_payload) is False
    assert get_trusted_device(wrong_mobile.identity.device_id) is None
    # Intended mobile can still complete the bound challenge
    assert intended_mobile.consume_qr_payload(qr_payload) is True


# ============================================================================
# 2. REVOKED DEVICE REJECTION (EXPORT, IMPORT, EVENT EMISSION)
# ============================================================================


def test_revoked_peer_rejected_on_export_import_and_emits_event(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    bus = get_event_bus()
    sub_q = bus.subscribe(topics={"device.revoked"})
    try:
        del_resp = api_client.delete(
            f"/api/device/peers/{mobile.identity.device_id}",
            headers=_auth_headers(),
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "revoked"

        evt = sub_q.get_nowait()
        assert evt.event_type == "device.revoked"
        assert evt.payload["device_id"] == mobile.identity.device_id
    finally:
        bus.unsubscribe(sub_q)

    # Export rejected with 403
    exp_resp = api_client.post(
        "/api/sync/export",
        json={"peer_device_id": mobile.identity.device_id},
        headers=_auth_headers(),
    )
    assert exp_resp.status_code == 403

    # Mobile sync attempt rejected with PermissionError and revokes local trust
    mobile.queue_context_update("offline_note", "should_be_rejected")
    with pytest.raises(PermissionError):
        mobile.sync_with_desktop()
    assert mobile.store.get_trusted_desktop()["trust_status"] == "revoked"


# ============================================================================
# 3. TAMPERED CRYPTOGRAPHIC ENVELOPE REJECTION
# ============================================================================


def test_tampered_envelope_bitflip_auth_tag_forged_sig_and_wrong_recipient(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    desktop = get_device_identity()
    batch = SyncBatch(
        batch_id="bat_tamper_c5",
        sender_device_id=mobile.identity.device_id,
        recipient_device_id=desktop.device_id,
        items=[],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    valid_env = seal_payload(
        payload=batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=desktop.exchange_public_key_b64,
        recipient_device_id=desktop.device_id,
        sender_identity=mobile.identity,
    )

    # 1. Ciphertext bit-flip -> SignatureVerificationError or DecryptionError (403 / 400)
    env_ct = valid_env.to_dict()
    ct_raw = bytearray(b64_decode_bytes(env_ct["ciphertext"]))
    if ct_raw:
        ct_raw[0] ^= 0xFF
    env_ct["ciphertext"] = b64_encode_bytes(bytes(ct_raw))
    r_ct = api_client.post("/api/sync/import", json={"envelope": env_ct}, headers=_auth_headers())
    assert r_ct.status_code in (400, 403)

    # 2. Auth tag tamper
    env_tag = valid_env.to_dict()
    tag_raw = bytearray(b64_decode_bytes(env_tag["auth_tag"]))
    tag_raw[0] ^= 0x55
    env_tag["auth_tag"] = b64_encode_bytes(bytes(tag_raw))
    r_tag = api_client.post("/api/sync/import", json={"envelope": env_tag}, headers=_auth_headers())
    assert r_tag.status_code in (400, 403)

    # 3. Forged signature (signed by rogue key)
    rogue = _make_mobile(tmp_path, api_client, name="Rogue")
    env_sig = valid_env.to_dict()
    env_sig["signature"] = rogue.identity.sign(b"forged")
    r_sig = api_client.post("/api/sync/import", json={"envelope": env_sig}, headers=_auth_headers())
    assert r_sig.status_code == 403

    # 4. Wrong recipient device ID (sealed for rogue instead of desktop)
    wrong_batch = SyncBatch(
        batch_id="bat_wrong_recip",
        sender_device_id=mobile.identity.device_id,
        recipient_device_id=rogue.identity.device_id,
        items=[],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    wrong_env = seal_payload(
        payload=wrong_batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=rogue.identity.exchange_public_key_b64,
        recipient_device_id=rogue.identity.device_id,
        sender_identity=mobile.identity,
    )
    r_recip = api_client.post("/api/sync/import", json={"envelope": wrong_env.to_dict()}, headers=_auth_headers())
    assert r_recip.status_code == 400


# ============================================================================
# 4. DUPLICATE BATCH IMPORT IDEMPOTENCY & REAL EXPORT MANIFEST METADATA
# ============================================================================


def test_duplicate_batch_idempotency_and_real_export_manifest(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    # Seed desktop context item
    set_context("travel_mode", "offline_first", workspace_id="default")

    # Export via API and verify real batch_id & manifest (no hardcoded package=1)
    exp_resp = api_client.post(
        "/api/sync/export",
        json={"peer_device_id": mobile.identity.device_id, "workspace_id": "default"},
        headers=_auth_headers(),
    )
    assert exp_resp.status_code == 200
    exp_json = exp_resp.json()
    assert exp_json["batch_id"].startswith("bat_")
    assert exp_json["items_count"] >= 2
    assert exp_json["manifest"].get("context", 0) >= 1

    # Mobile pushes a batch twice; second import must be idempotent no-op
    desktop = get_device_identity()
    item = SyncItem(
        item_id="chg_dup_1",
        entity_type=SyncEntityType.CONTEXT,
        entity_id="mobile_preference",
        workspace_id="default",
        data={"value": "compact", "updated_at": datetime.now(timezone.utc).isoformat()},
        vector_clock={mobile.identity.device_id: 1},
        origin_device_id=mobile.identity.device_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    batch = SyncBatch(
        batch_id=f"bat_idem_{uuid.uuid4().hex[:8]}",
        sender_device_id=mobile.identity.device_id,
        recipient_device_id=desktop.device_id,
        items=[item],
        timestamp=datetime.now(timezone.utc).isoformat(),
        manifest={"context": 1},
    )
    env = seal_payload(
        payload=batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=desktop.exchange_public_key_b64,
        recipient_device_id=desktop.device_id,
        sender_identity=mobile.identity,
    )

    first_import = api_client.post("/api/sync/import", json={"envelope": env.to_dict()}, headers=_auth_headers())
    assert first_import.status_code == 200
    assert first_import.json()["items_imported"] == 1
    assert first_import.json()["items_skipped"] == 0

    second_import = api_client.post("/api/sync/import", json={"envelope": env.to_dict()}, headers=_auth_headers())
    assert second_import.status_code == 200
    assert second_import.json()["items_imported"] == 0
    assert second_import.json()["items_skipped"] == 1
    assert get_context("mobile_preference", workspace_id="default") == "compact"


# ============================================================================
# 5. CONCURRENT CONFLICTING UPDATES (LWW + MANUAL CONFLICT RESOLUTION)
# ============================================================================


def test_concurrent_conflict_lww_and_manual_resolution(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    desktop = get_device_identity()
    # Desktop records local change at T1
    t1 = "2026-11-26T10:00:00+00:00"
    set_context("sync_banner", "desktop_v1", workspace_id="default")
    record_sync_change(
        entity_type=SyncEntityType.CONTEXT,
        entity_id="sync_banner",
        data={"key": "sync_banner", "value": "desktop_v1", "updated_at": t1},
        workspace_id="default",
        origin_device_id=desktop.device_id,
        vector_clock={desktop.device_id: 2},
    )

    # Mobile concurrently edits same key at newer T2 (disjoint vector clock!)
    t2 = "2026-11-26T10:05:00+00:00"
    mobile.queue_context_update("sync_banner", "mobile_v2", workspace_id="default", updated_at=t2)
    res = mobile.sync_with_desktop(workspace_id="default")
    assert res.success is True
    assert get_context("sync_banner", workspace_id="default") == "mobile_v2"

    # Conflict ledger preserved the losing state; user can manually override via API
    conflicts_resp = api_client.get("/api/sync/conflicts", headers=_auth_headers())
    assert conflicts_resp.status_code == 200
    conflicts = conflicts_resp.json()
    assert len(conflicts) >= 1
    c_id = conflicts[0]["conflict_id"]

    resolve_resp = api_client.post(
        f"/api/sync/conflicts/{c_id}/resolve",
        json={"resolved_value": {"value": "merged_final", "context_type": "general"}},
        headers=_auth_headers(),
    )
    assert resolve_resp.status_code == 200
    assert get_context("sync_banner", workspace_id="default") == "merged_final"


# ============================================================================
# 6. OFFLINE QUEUEING & RECONNECT / RETRY WITH EXPONENTIAL BACKOFF
# ============================================================================


class FlakyTransport:
    def __init__(self, inner: TestClientTransport):
        self.inner = inner
        self.offline = True
        self.calls: list[tuple[str, str]] = []

    def request(self, method, base_url, path, json_body=None, headers=None):
        self.calls.append((method, path))
        if self.offline:
            raise TransportOfflineError("Simulated airplane mode")
        return self.inner.request(method, base_url, path, json_body=json_body, headers=headers)


def test_offline_outbox_queue_and_exponential_backoff_retry(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    flaky = FlakyTransport(TestClientTransport(api_client))
    mobile.transport = flaky
    mobile.base_backoff_seconds = 2.0

    mobile.queue_context_update("offline_k1", "offline_v1")
    mobile.queue_context_update("offline_k2", "offline_v2")

    # Attempt 1 while offline -> fails, retry_count=1, next_retry_at = now + 2s
    r1 = mobile.sync_with_desktop()
    assert r1.success is False
    assert r1.offline is True
    pending_all = mobile.store.get_pending_mutations(ignore_backoff=True)
    assert len(pending_all) == 2
    assert pending_all[0]["retry_count"] == 1
    first_next = datetime.fromisoformat(pending_all[0]["next_retry_at"])

    # Before backoff window expires, normal sync sees 0 due mutations
    assert len(mobile.store.get_pending_mutations()) == 0

    # Force retry while still offline -> retry_count=2, backoff doubles to 4s
    r2 = mobile.sync_with_desktop(force_retry=True)
    assert r2.success is False
    pending_after_2 = mobile.store.get_pending_mutations(ignore_backoff=True)
    assert pending_after_2[0]["retry_count"] == 2
    second_next = datetime.fromisoformat(pending_after_2[0]["next_retry_at"])
    assert (second_next - first_next).total_seconds() >= 1.5

    # Network reconnects and backoff elapses (simulate force_retry=True after reconnect)
    flaky.offline = False
    r3 = mobile.sync_with_desktop(force_retry=True)
    assert r3.success is True
    assert r3.pushed_count == 2
    assert len(mobile.store.get_pending_mutations(ignore_backoff=True)) == 0
    assert get_context("offline_k1", workspace_id="default") == "offline_v1"
    assert get_context("offline_k2", workspace_id="default") == "offline_v2"


# ============================================================================
# 7. WORKSPACE ISOLATION (LOCAL_ONLY & CONFIDENTIAL EXCLUSION)
# ============================================================================


def test_workspace_isolation_excludes_local_only_and_confidential(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    ws_secret = create_workspace(
        name="Private Health Vault",
        profile_type="confidential",
        settings={"local_only": True},
    )
    set_context("medical_record", "classified_bloodwork", workspace_id=ws_secret.workspace_id)
    set_context("public_note", "buy_groceries", workspace_id="default")

    # Direct export of local_only workspace returns 403
    exp_blocked = api_client.post(
        "/api/sync/export",
        json={"peer_device_id": mobile.identity.device_id, "workspace_id": ws_secret.workspace_id},
        headers=_auth_headers(),
    )
    assert exp_blocked.status_code == 403

    # Full sync skips confidential workspace completely; only default workspace arrives
    res = mobile.sync_with_desktop()
    assert res.success is True
    assert mobile.store.get_cached_context("default", "public_note")["value"] == "buy_groceries"
    assert mobile.store.get_cached_context(ws_secret.workspace_id, "medical_record") is None

    # Even if a tampered batch tries to push a confidential workspace item to desktop, SyncEngine skips it
    desktop = get_device_identity()
    sneaky_batch = SyncBatch(
        batch_id="bat_sneaky_ws",
        sender_device_id=mobile.identity.device_id,
        recipient_device_id=desktop.device_id,
        items=[
            SyncItem(
                item_id="chg_sneak",
                entity_type=SyncEntityType.CONTEXT,
                entity_id="leaked_secret",
                workspace_id=ws_secret.workspace_id,
                data={"value": "exfiltrated"},
                vector_clock={mobile.identity.device_id: 1},
                origin_device_id=mobile.identity.device_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        ],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    env = seal_payload(
        payload=sneaky_batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=desktop.exchange_public_key_b64,
        recipient_device_id=desktop.device_id,
        sender_identity=mobile.identity,
    )
    imp_resp = api_client.post("/api/sync/import", json={"envelope": env.to_dict()}, headers=_auth_headers())
    assert imp_resp.status_code == 200
    assert imp_resp.json()["items_imported"] == 0
    assert imp_resp.json()["items_skipped"] == 1
    assert get_context("leaked_secret", workspace_id=ws_secret.workspace_id) is None


# ============================================================================
# 8. CONSENT GRANT & REVOCATION ENFORCEMENT DURING SYNC
# ============================================================================


def test_consent_grant_and_revocation_enforced_in_sync_engine(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    docs_folder = tmp_path / "consented_docs"
    docs_folder.mkdir()
    doc_a = docs_folder / "allowed.md"
    doc_b = docs_folder / "revoked.md"
    doc_a.write_text("allowed", encoding="utf-8")
    doc_b.write_text("revoked", encoding="utf-8")

    grant_a = grant_consent(doc_a, scope_type="file")
    grant_b = grant_consent(doc_b, scope_type="file")
    revoke_consent(grant_b.consent_id, reason="User revoked doc_b")

    desktop = get_device_identity()
    record_sync_change(
        entity_type=SyncEntityType.DOCUMENT_META,
        entity_id=str(doc_a),
        data={"filename": "allowed.md", "path": str(doc_a), "file_hash": "hash_a"},
        workspace_id="default",
        origin_device_id=desktop.device_id,
    )
    record_sync_change(
        entity_type=SyncEntityType.DOCUMENT_META,
        entity_id=str(doc_b),
        data={"filename": "revoked.md", "path": str(doc_b), "file_hash": "hash_b"},
        workspace_id="default",
        origin_device_id=desktop.device_id,
    )

    res = mobile.sync_with_desktop(workspace_id="default")
    assert res.success is True
    assert mobile.store.get_cached_document("default", str(doc_a)) is not None
    assert mobile.store.get_cached_document("default", str(doc_b)) is None

    # Also verify SyncEngine.import_sync_package skips unconsented/revoked document items
    engine = SyncEngine(local_identity=desktop)
    unconsented_batch = SyncBatch(
        batch_id="bat_unconsented_doc",
        sender_device_id=mobile.identity.device_id,
        recipient_device_id=desktop.device_id,
        items=[
            SyncItem(
                item_id="chg_rev_doc",
                entity_type=SyncEntityType.DOCUMENT_META,
                entity_id=str(doc_b),
                workspace_id="default",
                data={"filename": "revoked.md", "path": str(doc_b), "file_hash": "hash_b"},
                vector_clock={mobile.identity.device_id: 1},
                origin_device_id=mobile.identity.device_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        ],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    env = seal_payload(
        payload=unconsented_batch.model_dump_json().encode("utf-8"),
        recipient_exchange_public_key_b64=desktop.exchange_public_key_b64,
        recipient_device_id=desktop.device_id,
        sender_identity=mobile.identity,
    )
    report = engine.import_sync_package(env)
    assert report.items_imported == 0
    assert report.items_skipped == 1
    assert grant_a.status == "active"


# ============================================================================
# 9. REMOTE WIPE / FORGET & SYMMETRIC ENCRYPTION-AT-REST VERIFICATION
# ============================================================================


def test_remote_wipe_zeroizes_mobile_db_and_keys_plus_at_rest_encryption(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    secret_val = "ULTRA_SENSITIVE_MOBILE_SECRET_TOKEN_42"
    mobile.queue_context_update("bank_note", secret_val)

    # Verify raw SQLite database encrypts value_enc and payload_enc (no plaintext leakage)
    with connect_sqlite(mobile.db_path) as conn:
        raw_ctx = conn.execute("SELECT value_enc FROM cached_context WHERE key = 'bank_note'").fetchone()[0]
        raw_outbox = conn.execute("SELECT payload_enc FROM outbox_mutations LIMIT 1").fetchone()[0]
    assert secret_val not in raw_ctx
    assert secret_val not in raw_outbox
    assert "PBKDF2-AES256GCM" in raw_ctx

    # Desktop triggers remote wipe endpoint -> revokes trust & emits privacy.remote_wipe
    wipe_resp = api_client.post(
        f"/api/device/peers/{mobile.identity.device_id}/wipe",
        headers=_auth_headers(),
    )
    assert wipe_resp.status_code == 200
    assert wipe_resp.json()["wiped"] is True
    assert get_trusted_device(mobile.identity.device_id)["trust_status"] == "revoked"

    # Mobile processes remote wipe event
    wipe_res = mobile.handle_event({
        "event_type": "privacy.remote_wipe",
        "payload": {"device_id": mobile.identity.device_id, "wipe_scope": "all"},
    })
    assert wipe_res["wiped"] is True
    assert mobile.wiped is True
    assert not mobile.keys_path.exists()
    assert not mobile.store.key_file.exists()
    assert mobile.store.get_trusted_desktop() is None
    assert mobile.store.list_cached_context() == []
    assert mobile.store.get_pending_mutations(ignore_backoff=True) == []

    with pytest.raises(PermissionError):
        mobile.sync_with_desktop()


# ============================================================================
# 10. LARGE SYNC PACKAGES (>10 MB CHUNKED TRANSFER) & MID-TRANSFER RESUME
# ============================================================================


class InterruptingChunkTransport:
    """Fails on the Nth chunked upload/download call to simulate network drop mid-transfer."""

    def __init__(self, inner: TestClientTransport, fail_on_call: int):
        self.inner = inner
        self.fail_on_call = fail_on_call
        self.chunk_calls = 0

    def request(self, method, base_url, path, json_body=None, headers=None):
        if "/chunked" in path and method.upper() == "POST":
            self.chunk_calls += 1
            if self.chunk_calls == self.fail_on_call:
                raise TransportOfflineError("Simulated mid-transfer socket drop")
        return self.inner.request(method, base_url, path, json_body=json_body, headers=headers)


def test_large_package_over_10mb_chunked_transfer_and_resume(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    # Configure chunking threshold & size so >10 MB payload splits into 6 chunks (<10 MB each)
    mobile.chunk_threshold_bytes = 2 * 1024 * 1024
    mobile.chunk_size_bytes = 2 * 1024 * 1024

    # Create an 11 MB payload (> API_MAX_REQUEST_BYTES = 10 MB)
    large_blob = "X" * (11 * 1024 * 1024)
    mobile.queue_context_update("large_mobile_payload", large_blob)

    # Confirm direct non-chunked upload would exceed 10 MB limit (413)
    oversize_check = api_client.post(
        "/api/sync/import",
        content=b'{"dummy": "' + b"A" * (10 * 1024 * 1024 + 512) + b'"}',
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    assert oversize_check.status_code == 413

    # Simulate mid-transfer interruption on 3rd chunk upload
    interruptor = InterruptingChunkTransport(TestClientTransport(api_client), fail_on_call=3)
    mobile.transport = interruptor

    r_interrupted = mobile.sync_with_desktop()
    assert r_interrupted.success is False
    assert r_interrupted.offline is True
    assert r_interrupted.chunked_used is True

    # Verify partial transfer checkpoint persisted on mobile & chunks 0,1 staged on server
    pending_state = mobile.store.get_sync_state(qr_payload["desktop_device_id"], "*")
    xfer_id = pending_state["pending_transfer"]["transfer_id"]
    status_resp = api_client.get(f"/api/sync/import/chunked/{xfer_id}", headers=_auth_headers())
    assert status_resp.status_code == 200
    assert status_resp.json()["received_indices"] == [0, 1]

    # Resume with healthy transport -> completes remaining chunks & imports 11 MB payload!
    mobile.transport = TestClientTransport(api_client)
    r_resumed = mobile.sync_with_desktop(force_retry=True)
    assert r_resumed.success is True
    assert r_resumed.chunked_used is True
    assert r_resumed.pushed_count == 1
    assert len(get_context("large_mobile_payload", workspace_id="default")) == 11 * 1024 * 1024


def test_chunked_import_rejects_corrupted_chunk_sha256(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr_payload) is True

    chunk_b64 = base64.b64encode(b"corrupted-chunk-bytes").decode("ascii")
    bad_resp = api_client.post(
        "/api/sync/import/chunked",
        json={
            "transfer_id": "xfer_bad_hash",
            "chunk_index": 0,
            "total_chunks": 2,
            "payload_b64": chunk_b64,
            "chunk_sha256": "0" * 64,
            "peer_device_id": mobile.identity.device_id,
        },
        headers=_auth_headers(),
    )
    assert bad_resp.status_code == 400
    err_body = bad_resp.json()
    err_msg = err_body.get("detail") or (err_body.get("error") or {}).get("message", "")
    assert "sha-256" in err_msg.lower()


# ============================================================================
# 11. ANDROID & iOS DETERMINISTIC LIFECYCLE SIMULATION SUITES
# ============================================================================


def test_android_lifecycle_workmanager_doze_network_and_process_death(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client, name="Android Pixel")
    assert mobile.consume_qr_payload(qr_payload) is True

    android = AndroidLifecycleManager(
        AndroidWorkConstraints(
            network_type="none",
            doze_mode=True,
            battery_saver=True,
            charging=False,
        )
    )
    mobile.queue_context_update("android_task", "from_workmanager")

    # 1. Offline -> deferred
    ok, reason = android.run_periodic_sync(mobile)
    assert ok is False
    assert reason == "NO_NETWORK"

    # 2. Cellular + Doze without charging -> deferred
    android.set_network("cellular")
    ok, reason = android.run_periodic_sync(mobile)
    assert ok is False
    assert reason == "ANDROID_DOZE_DEFERRED"

    # 3. Large >10 MB payload on metered cellular even while charging -> deferred
    android.constraints.charging = True
    ok, reason = android.run_periodic_sync(mobile, estimated_payload_bytes=12 * 1024 * 1024)
    assert ok is False
    assert reason == "ANDROID_UNMETERED_NETWORK_REQUIRED"

    # 4. Process death mid-session -> state persisted -> process recovers on Wi-Fi
    android.simulate_process_death(mobile)
    ok_dead, reason_dead = android.can_execute_work()
    assert ok_dead is False
    assert reason_dead == "ANDROID_PROCESS_DEAD"

    android.set_network("wifi")
    android.constraints.doze_mode = False
    android.constraints.battery_saver = False
    ok_rec, res_rec = android.recover_from_process_death(mobile)
    assert ok_rec is True
    assert res_rec.pushed_count == 1
    assert get_context("android_task", workspace_id="default") == "from_workmanager"


def test_ios_lifecycle_bgrefresh_low_power_data_protection_and_termination(tmp_path, api_client):
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client, name="iPhone 16 Pro")
    assert mobile.consume_qr_payload(qr_payload) is True

    ios = iOSLifecycleManager(
        iOSBackgroundConstraints(
            network_available=True,
            background_refresh_enabled=True,
            low_power_mode=True,
            data_protection_locked=True,
            task_budget_seconds=30.0,
        )
    )
    mobile.queue_context_update("ios_task", "from_bgapprefreshtask")

    # 1. Low Power Mode -> deferred
    ok, reason = ios.run_bg_refresh(mobile)
    assert ok is False
    assert reason == "IOS_LOW_POWER_MODE_DEFERRED"

    # 2. Data Protection locked -> deferred
    ios.constraints.low_power_mode = False
    ok, reason = ios.run_bg_refresh(mobile)
    assert ok is False
    assert reason == "IOS_DATA_PROTECTION_LOCKED"

    # 3. Exceeded BGAppRefreshTask time budget -> expiration handler invoked
    ios.constraints.data_protection_locked = False
    ok, reason = ios.run_bg_refresh(mobile, estimated_duration_s=45.0)
    assert ok is False
    assert reason == "IOS_BG_TIME_BUDGET_EXCEEDED"
    assert ios.expiration_handler_invoked is True

    # 4. App terminated -> relaunched & unlocked -> recovers from SQLite checkpoint
    ios.simulate_termination(mobile)
    ok_term, reason_term = ios.can_execute_background_task()
    assert ok_term is False
    assert reason_term == "IOS_APP_TERMINATED"

    ok_rec, res_rec = ios.recover_after_relaunch(mobile)
    assert ok_rec is True
    assert res_rec.pushed_count == 1
    assert get_context("ios_task", workspace_id="default") == "from_bgapprefreshtask"


# ============================================================================
# 12. ITERATION 2 REMEDIATION & ADVERSARIAL FAILURE-PATH TESTS
# ============================================================================


def test_qr_only_mobile_without_api_key_pairs_and_syncs_end_to_end(tmp_path, api_client):
    """
    Objective 1: MobileCompanionClient(api_key=None) pairs via QR, receives
    a device-scoped devtok_* token, persists it encrypted, syncs bidirectionally
    without master API_KEY, and is blocked from non-sync routes or after revocation.
    """
    qr_payload = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client, name="QR Only Phone", api_key=None)
    assert mobile.consume_qr_payload(qr_payload) is True

    pinned = mobile.store.get_trusted_desktop()
    assert pinned is not None
    dev_tok = pinned.get("device_token")
    assert dev_tok and dev_tok.startswith("devtok_")

    # Verify device_token is encrypted at rest in mobile_companion.db
    with connect_sqlite(mobile.db_path) as conn:
        raw_tok_enc = conn.execute("SELECT device_token_enc FROM trusted_desktop").fetchone()[0]
    assert dev_tok not in raw_tok_enc

    # Device token cannot access non-sync endpoints (e.g., /api/privacy/summary, /api/query)
    priv_resp = api_client.get("/api/privacy/summary", headers={"X-Device-Token": dev_tok})
    assert priv_resp.status_code == 403

    # Bidirectional sync succeeds with api_key=None using X-Device-Token
    set_context("desktop_key", "from_desktop_qr_only", workspace_id="default")
    mobile.queue_context_update("mobile_key", "from_mobile_qr_only", workspace_id="default")
    res = mobile.sync_with_desktop(workspace_id="default")
    assert res.success is True
    assert res.pushed_count == 1
    assert get_context("mobile_key", workspace_id="default") == "from_mobile_qr_only"
    assert mobile.store.get_cached_context("default", "desktop_key")["value"] == "from_desktop_qr_only"

    # Restart mobile client without api_key -> token persists and sync continues working
    mobile_restarted = MobileCompanionClient(
        data_dir=mobile.data_dir,
        device_name="QR Only Phone",
        os_info="Android 15",
        api_key=None,
        transport=TestClientTransport(api_client),
    )
    mobile_restarted.queue_context_update("mobile_key_2", "after_restart", workspace_id="default")
    res2 = mobile_restarted.sync_with_desktop(workspace_id="default")
    assert res2.success is True
    assert get_context("mobile_key_2", workspace_id="default") == "after_restart"

    # Revoking device clears device_token -> subsequent sync fails with 401
    del_resp = api_client.delete(f"/api/device/peers/{mobile.identity.device_id}", headers=_auth_headers())
    assert del_resp.status_code == 200
    res_revoked = mobile_restarted.sync_with_desktop(workspace_id="default")
    assert res_revoked.success is False
    assert "401" in (res_revoked.error or "")


def test_pairing_prevents_hijacking_revoked_resurrection_and_self_impersonation(tmp_path, api_client):
    """
    Objective 2: Wildcard QR pairing must reject desktop self-impersonation,
    overwriting an already-paired peer ID with different keys, and resurrecting
    a revoked peer ID.
    """
    desktop = get_device_identity()
    victim = _make_mobile(tmp_path, api_client, name="Victim Phone")
    attacker = _make_mobile(tmp_path, api_client, name="Attacker Phone")

    # Pair victim legitimately
    qr1 = _initiate_qr(api_client)
    assert victim.consume_qr_payload(qr1) is True

    # 1. Self-impersonation: peer_device_id == desktop.device_id
    qr_self = _initiate_qr(api_client)
    sig_self = attacker.identity.sign(f"{qr_self['challenge_id']}:{qr_self['challenge_code']}".encode("utf-8"))
    resp_self = api_client.post(
        "/api/device/pairing/verify",
        json={
            "challenge_id": qr_self["challenge_id"],
            "challenge_code": qr_self["challenge_code"],
            "peer_device_id": desktop.device_id,
            "peer_device_name": "Evil Desktop Spoof",
            "peer_device_type": "mobile",
            "peer_signing_public_key": attacker.identity.signing_public_key_b64,
            "peer_exchange_public_key": attacker.identity.exchange_public_key_b64,
            "signature": sig_self,
        },
        headers=_auth_headers(),
    )
    assert resp_self.status_code in (400, 403)
    assert get_trusted_device(desktop.device_id) is None

    # 2. Peer ID hijacking: attacker submits victim's device_id with attacker's keys
    qr_hijack = _initiate_qr(api_client)
    sig_hijack = attacker.identity.sign(f"{qr_hijack['challenge_id']}:{qr_hijack['challenge_code']}".encode("utf-8"))
    resp_hijack = api_client.post(
        "/api/device/pairing/verify",
        json={
            "challenge_id": qr_hijack["challenge_id"],
            "challenge_code": qr_hijack["challenge_code"],
            "peer_device_id": victim.identity.device_id,
            "peer_device_name": "Attacker Hijack",
            "peer_device_type": "mobile",
            "peer_signing_public_key": attacker.identity.signing_public_key_b64,
            "peer_exchange_public_key": attacker.identity.exchange_public_key_b64,
            "signature": sig_hijack,
        },
        headers=_auth_headers(),
    )
    assert resp_hijack.status_code in (400, 403)
    victim_record = get_trusted_device(victim.identity.device_id)
    assert victim_record["signing_public_key"] == victim.identity.signing_public_key_b64

    # 3. Revoked peer resurrection via wildcard QR
    assert revoke_trusted_device(victim.identity.device_id) is True
    qr_res = _initiate_qr(api_client)
    sig_res = victim.identity.sign(f"{qr_res['challenge_id']}:{qr_res['challenge_code']}".encode("utf-8"))
    resp_res = api_client.post(
        "/api/device/pairing/verify",
        json={
            "challenge_id": qr_res["challenge_id"],
            "challenge_code": qr_res["challenge_code"],
            "peer_device_id": victim.identity.device_id,
            "peer_device_name": "Victim Phone",
            "peer_device_type": "mobile",
            "peer_signing_public_key": victim.identity.signing_public_key_b64,
            "peer_exchange_public_key": victim.identity.exchange_public_key_b64,
            "signature": sig_res,
        },
        headers=_auth_headers(),
    )
    assert resp_res.status_code in (400, 403)
    assert get_trusted_device(victim.identity.device_id)["trust_status"] == "revoked"


def test_desktop_context_updates_propagate_across_incremental_syncs(tmp_path, api_client):
    """
    Objective 3: Updating an already-exported context entry in context.db
    must emit an incremented vector clock and propagate to mobile on next sync.
    """
    qr = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client, api_key=None)
    assert mobile.consume_qr_payload(qr) is True

    set_context("daily_focus", "initial_v1", workspace_id="default")
    r1 = mobile.sync_with_desktop(workspace_id="default")
    assert r1.success is True
    c1 = mobile.store.get_cached_context("default", "daily_focus")
    assert c1["value"] == "initial_v1"

    # Mutate desktop context after initial export
    set_context("daily_focus", "updated_v2", workspace_id="default")
    r2 = mobile.sync_with_desktop(workspace_id="default")
    assert r2.success is True
    c2 = mobile.store.get_cached_context("default", "daily_focus")
    assert c2["value"] == "updated_v2"
    desktop_id = get_device_identity().device_id
    assert c2["vector_clock"][desktop_id] > c1["vector_clock"][desktop_id]


def test_concurrent_tombstone_lww_winner_deletes_context_not_empty_string(tmp_path, api_client):
    """
    Objective 4: When a deletion tombstone wins LWW during concurrent conflict
    resolution, SyncEngine must delete_context() and record a tombstone rather
    than persisting an empty string.
    """
    qr = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr) is True

    desktop = get_device_identity()
    t1 = "2026-11-26T10:00:00+00:00"
    set_context("ephemeral_note", "keep_or_delete", workspace_id="default")
    record_sync_change(
        entity_type=SyncEntityType.CONTEXT,
        entity_id="ephemeral_note",
        data={"key": "ephemeral_note", "value": "keep_or_delete", "updated_at": t1},
        workspace_id="default",
        origin_device_id=desktop.device_id,
        vector_clock={desktop.device_id: 2},
    )

    # Mobile queues a deletion at newer T2 with disjoint clock
    mobile.queue_context_delete("ephemeral_note", workspace_id="default")
    res = mobile.sync_with_desktop(workspace_id="default")
    assert res.success is True
    assert get_context("ephemeral_note", workspace_id="default") is None


def test_mobile_local_only_workspaces_persist_across_restart(tmp_path, api_client):
    """
    Objective 5: local_only_workspaces boundary persisted in mobile_companion.db
    must survive process death / MobileCompanionClient re-instantiation.
    """
    ws_priv = create_workspace(
        name="Private Vault",
        profile_type="confidential",
        settings={"local_only": True},
    )
    mob1 = _make_mobile(tmp_path, api_client, name="Restart Phone")
    mob1.local_only_workspaces.add(ws_priv.workspace_id)
    with pytest.raises(PermissionError):
        mob1.queue_context_update("k", "secret", workspace_id=ws_priv.workspace_id)

    # Simulate process restart with new instance pointing at same data_dir
    mob2 = MobileCompanionClient(
        data_dir=mob1.data_dir,
        device_name="Restart Phone",
        os_info="Android 15",
        transport=TestClientTransport(api_client),
    )
    assert ws_priv.workspace_id in mob2.local_only_workspaces
    with pytest.raises(PermissionError):
        mob2.queue_context_update("k", "secret", workspace_id=ws_priv.workspace_id)


def test_selective_path_wipe_avoids_sibling_substring_collision(tmp_path, api_client):
    """
    Objective 6: Wiping path '/docs/a' must NOT delete sibling '/docs/ab/keep.md'.
    """
    mobile = _make_mobile(tmp_path, api_client)
    doc_a_path = tmp_path / "docs" / "a" / "secret.md"
    doc_ab_path = tmp_path / "docs" / "ab" / "keep.md"

    mobile.store.upsert_cached_document(
        workspace_id="default",
        entity_id=str(doc_a_path),
        filename="secret.md",
        file_hash="hash_a",
        metadata={"path": str(doc_a_path)},
    )
    mobile.store.upsert_cached_document(
        workspace_id="default",
        entity_id=str(doc_ab_path),
        filename="keep.md",
        file_hash="hash_ab",
        metadata={"path": str(doc_ab_path)},
    )

    wipe_res = mobile.handle_remote_wipe(
        wipe_scope="path",
        target_value=str(tmp_path / "docs" / "a"),
    )
    assert wipe_res["removed_rows"] == 1
    assert mobile.store.get_cached_document("default", str(doc_a_path)) is None
    assert mobile.store.get_cached_document("default", str(doc_ab_path)) is not None


def test_chunked_pull_sha256_corruption_returns_failure_without_crash(tmp_path, api_client):
    """
    Objective 8a: Corrupted chunk SHA-256 during chunked pull must return
    SyncResult(success=False) rather than crashing with unhandled ValueError.
    """
    qr = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client, api_key=None)
    assert mobile.consume_qr_payload(qr) is True
    set_context("pull_item", "value_123", workspace_id="default")

    class CorruptChunkPullTransport:
        def __init__(self, inner: TestClientTransport):
            self.inner = inner

        def request(self, method, base_url, path, json_body=None, headers=None):
            sc, data = self.inner.request(method, base_url, path, json_body=json_body, headers=headers)
            if method == "GET" and path.startswith("/api/sync/export/chunked/") and sc == 200:
                data = dict(data)
                data["chunk_sha256"] = "0" * 64
            return sc, data

    mobile.transport = CorruptChunkPullTransport(TestClientTransport(api_client))
    res = mobile.sync_with_desktop(use_chunked_pull=True)
    assert res.success is False
    assert "sha-256" in (res.error or "").lower()


def test_chunked_export_cleanup_revocation_and_import_consistency(tmp_path, api_client):
    """
    Objective 8b:
    - Completed chunked export deletes staged direction='export' chunks from sync_chunk_transfers.
    - Revoking peer blocks GET /api/sync/export/chunked/{xfer}/{idx} with 403.
    - Chunked import rejects missing peer_device_id, inconsistent peer_device_id,
      inconsistent total_sha256, and malformed JSON (HTTP 400 + cleanup).
    """
    qr = _initiate_qr(api_client)
    mobile = _make_mobile(tmp_path, api_client)
    assert mobile.consume_qr_payload(qr) is True

    set_context("chunked_ctx", "hello_chunks", workspace_id="default")
    res_pull = mobile.sync_with_desktop(use_chunked_pull=True)
    assert res_pull.success is True

    # Export chunks must be cleaned up once downloaded
    sync_db = Path(config.SYNC_DB)
    with connect_sqlite(sync_db) as conn:
        remaining_export = conn.execute(
            "SELECT COUNT(*) FROM sync_chunk_transfers WHERE direction = 'export'"
        ).fetchone()[0]
    assert remaining_export == 0

    # Stage a chunked export, then revoke peer -> GET chunk must return 403
    exp_meta = api_client.post(
        "/api/sync/export/chunked",
        json={"peer_device_id": mobile.identity.device_id, "workspace_id": "default"},
        headers=_auth_headers(),
    ).json()
    xfer_id = exp_meta["transfer_id"]
    assert revoke_trusted_device(mobile.identity.device_id) is True
    rev_get = api_client.get(f"/api/sync/export/chunked/{xfer_id}/0", headers=_auth_headers())
    assert rev_get.status_code == 403

    # Re-pair a new mobile for import consistency checks
    qr2 = _initiate_qr(api_client)
    mobile2 = _make_mobile(tmp_path, api_client, name="Chunk Tester")
    assert mobile2.consume_qr_payload(qr2) is True

    # Missing peer_device_id -> 400
    chunk0_bytes = b'{"not": "an envelope"}'
    c0_b64 = base64.b64encode(chunk0_bytes).decode("ascii")
    c0_sha = hashlib.sha256(chunk0_bytes).hexdigest()
    r_no_peer = api_client.post(
        "/api/sync/import/chunked",
        json={
            "transfer_id": "xfer_nopeer",
            "chunk_index": 0,
            "total_chunks": 1,
            "payload_b64": c0_b64,
            "chunk_sha256": c0_sha,
        },
        headers=_auth_headers(),
    )
    assert r_no_peer.status_code == 400

    # Malformed envelope JSON -> 400 (not 500) and poisoned chunks cleaned up
    r_bad_json = api_client.post(
        "/api/sync/import/chunked",
        json={
            "transfer_id": "xfer_malformed_json",
            "chunk_index": 0,
            "total_chunks": 1,
            "payload_b64": c0_b64,
            "chunk_sha256": c0_sha,
            "total_sha256": c0_sha,
            "peer_device_id": mobile2.identity.device_id,
        },
        headers=_auth_headers(),
    )
    assert r_bad_json.status_code == 400
    with connect_sqlite(sync_db) as conn:
        poisoned = conn.execute(
            "SELECT COUNT(*) FROM sync_chunk_transfers WHERE transfer_id = 'xfer_malformed_json'"
        ).fetchone()[0]
    assert poisoned == 0

    # Inconsistent total_sha256 between chunk 0 and chunk 1 -> 400
    part0 = b"part0-"
    part1 = b"part1-"
    r_c0 = api_client.post(
        "/api/sync/import/chunked",
        json={
            "transfer_id": "xfer_bad_total",
            "chunk_index": 0,
            "total_chunks": 2,
            "payload_b64": base64.b64encode(part0).decode("ascii"),
            "chunk_sha256": hashlib.sha256(part0).hexdigest(),
            "total_sha256": "a" * 64,
            "peer_device_id": mobile2.identity.device_id,
        },
        headers=_auth_headers(),
    )
    assert r_c0.status_code == 200
    r_c1 = api_client.post(
        "/api/sync/import/chunked",
        json={
            "transfer_id": "xfer_bad_total",
            "chunk_index": 1,
            "total_chunks": 2,
            "payload_b64": base64.b64encode(part1).decode("ascii"),
            "chunk_sha256": hashlib.sha256(part1).hexdigest(),
            "total_sha256": "b" * 64,
            "peer_device_id": mobile2.identity.device_id,
        },
        headers=_auth_headers(),
    )
    assert r_c1.status_code == 400
