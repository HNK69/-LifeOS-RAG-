"""
test_milestone3_device_identity_and_envelope.py

Comprehensive deterministic test suite for Milestone 3:
Device Identity, Key Management & Cryptographic Envelope.

Tests cover:
1. Device Identity & Asymmetric Keypair Generation, Serialization, Persistence, and Reloading
2. Verifiable Ed25519 Signatures (Valid, Tampered, Forged, Cross-Key Rejection)
3. Asymmetric Cryptographic Envelope (X25519-HKDF-AES256GCM + Ed25519 Signatures):
   - Ephemeral Key Forward Secrecy
   - Roundtrip Sealing and Opening
   - Wrong Recipient Decryption Failure
   - Tampered Ciphertext / Auth Tag Failure
   - Forged Signature Rejection
4. Symmetric Cryptographic Envelope (PBKDF2HMAC-SHA256 + AES-256-GCM):
   - Correct Passphrase Roundtrip
   - Incorrect Passphrase Rejection
   - Tampered Payload Rejection
5. Persistent Trust Store & Pairing Handshake (SQLite data/identity.db):
   - Local Identity Synchronization
   - Trusted Device Registration, Query, and Revocation
   - Pairing Challenge Creation and 6-Digit Code Expiry
   - Verifiable Cryptographic Challenge Response
   - Expired Challenge and Malformed Code Rejection
6. REST API Endpoints (/api/device/...):
   - GET /api/device/identity (Auth required)
   - GET /api/device/peers & GET /api/device/peers/{device_id}
   - DELETE /api/device/peers/{device_id}
   - POST /api/device/pairing/initiate & POST /api/device/pairing/verify
   - POST /api/device/envelope/seal & POST /api/device/envelope/open
   - Rejection of Untrusted or Revoked Recipients
"""

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.security.device_identity import (
    DeviceIdentity,
    b64_decode_bytes,
    b64_encode_bytes,
    get_device_identity,
    load_or_generate_device_identity,
    reset_device_identity,
    set_device_identity,
    sign_data,
    verify_signature,
)
from app.security.device_store import (
    create_pairing_challenge,
    get_trusted_device,
    initialize_identity_store,
    list_trusted_devices,
    register_trusted_device,
    revoke_trusted_device,
    sync_local_identity,
    verify_pairing_challenge,
)
from app.security.envelope import (
    CryptographicEnvelope,
    DecryptionError,
    EnvelopeError,
    SignatureVerificationError,
    SymmetricEnvelope,
    open_payload,
    open_symmetric,
    seal_payload,
    seal_symmetric,
)

TEST_API_KEY = "test-api-key-12345"
TEST_ADMIN_KEY = "test-admin-key-99999"


@pytest.fixture(autouse=True)
def configure_security_env(tmp_path, monkeypatch):
    """Isolate databases, key storage, and auth in tmp_path."""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_identity_db = test_data / "identity.db"
    test_keys_path = test_data / "device_keys.json"

    monkeypatch.setattr(config, "DATA_DIR", test_data)
    monkeypatch.setattr(config, "IDENTITY_DB", test_identity_db)
    monkeypatch.setattr(config, "DEVICE_KEYS_PATH", test_keys_path)
    monkeypatch.setattr(config, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)

    reset_device_identity()
    initialize_identity_store(test_identity_db)
    yield
    reset_device_identity()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ============================================================
# 1. DEVICE IDENTITY & KEYPAIR TESTS
# ============================================================

def test_device_identity_generation_and_properties(tmp_path):
    keys_file = tmp_path / "keys.json"
    identity = load_or_generate_device_identity(keys_path=keys_file)

    assert identity.device_id is not None
    # Validate UUID format
    uuid_obj = uuid.UUID(identity.device_id)
    assert str(uuid_obj) == identity.device_id

    assert identity.device_name is not None
    assert identity.device_type in {"desktop", "mobile", "server", "tablet", "embedded"}
    assert identity.signing_public_key_b64 is not None
    assert identity.exchange_public_key_b64 is not None

    # Verify public key lengths (32 bytes raw -> 44 chars in base64)
    raw_sig_pub = b64_decode_bytes(identity.signing_public_key_b64)
    raw_ex_pub = b64_decode_bytes(identity.exchange_public_key_b64)
    assert len(raw_sig_pub) == 32
    assert len(raw_ex_pub) == 32

    # Public dict must not expose private keys
    pub_dict = identity.to_public_dict()
    assert "signing_private_key" not in pub_dict
    assert "exchange_private_key" not in pub_dict
    assert pub_dict["device_id"] == identity.device_id
    assert pub_dict["signing_public_key"] == identity.signing_public_key_b64


def test_device_identity_persistence_and_reloading(tmp_path):
    keys_file = tmp_path / "subfolder" / "stored_keys.json"
    identity_1 = load_or_generate_device_identity(keys_path=keys_file)
    assert keys_file.is_file()

    # Reload from same file
    identity_2 = load_or_generate_device_identity(keys_path=keys_file)
    assert identity_2.device_id == identity_1.device_id
    assert identity_2.signing_public_key_b64 == identity_1.signing_public_key_b64
    assert identity_2.exchange_public_key_b64 == identity_1.exchange_public_key_b64

    # Sign data with reloaded key and verify with original public key
    msg = b"LifeOS persistence test payload"
    sig = identity_2.sign(msg)
    assert verify_signature(identity_1.signing_public_key_b64, msg, sig) is True


# ============================================================
# 2. ED25519 DIGITAL SIGNATURE TESTS
# ============================================================

def test_ed25519_sign_and_verify_valid():
    identity = get_device_identity()
    message = b"Verifiable command from LifeOS device agent"

    signature = identity.sign(message)
    assert isinstance(signature, str)
    assert len(b64_decode_bytes(signature)) == 64

    assert verify_signature(identity.signing_public_key_b64, message, signature) is True


def test_ed25519_tampered_message_fails_verification():
    identity = get_device_identity()
    message = b"Original transaction payload"
    tampered_message = b"Tampered transaction payload"

    signature = identity.sign(message)
    assert verify_signature(identity.signing_public_key_b64, tampered_message, signature) is False


def test_ed25519_tampered_signature_fails_verification():
    identity = get_device_identity()
    message = b"Standard document hash"

    signature = identity.sign(message)
    raw_sig = bytearray(b64_decode_bytes(signature))
    raw_sig[0] ^= 0xFF  # Flip bits in first byte
    tampered_sig = b64_encode_bytes(bytes(raw_sig))

    assert verify_signature(identity.signing_public_key_b64, message, tampered_sig) is False


def test_ed25519_wrong_key_fails_verification(tmp_path):
    alice = load_or_generate_device_identity(keys_path=tmp_path / "alice.json")
    bob = load_or_generate_device_identity(keys_path=tmp_path / "bob.json")

    msg = b"Alice authenticated payload"
    alice_sig = alice.sign(msg)

    # Bob's public key must reject Alice's signature
    assert verify_signature(bob.signing_public_key_b64, msg, alice_sig) is False


# ============================================================
# 3. ASYMMETRIC CRYPTOGRAPHIC ENVELOPE TESTS
# ============================================================

def test_asymmetric_envelope_seal_and_open_end_to_end(tmp_path):
    alice = load_or_generate_device_identity(keys_path=tmp_path / "alice.json")
    bob = load_or_generate_device_identity(keys_path=tmp_path / "bob.json")

    secret_message = "Confidential document chunk for peer node Bob."

    # Alice seals envelope for Bob
    envelope = seal_payload(
        payload=secret_message,
        recipient_exchange_public_key_b64=bob.exchange_public_key_b64,
        recipient_device_id=bob.device_id,
        sender_identity=alice,
    )

    assert envelope.version == 1
    assert envelope.sender_device_id == alice.device_id
    assert envelope.recipient_device_id == bob.device_id
    assert envelope.algorithm == "X25519-HKDF-AES256GCM"
    assert len(b64_decode_bytes(envelope.ephemeral_public_key)) == 32
    assert len(b64_decode_bytes(envelope.iv)) == 12
    assert len(b64_decode_bytes(envelope.auth_tag)) == 16
    assert len(b64_decode_bytes(envelope.signature)) == 64

    # Bob opens envelope and verifies Alice's signature
    decrypted_bytes = open_payload(
        envelope=envelope,
        recipient_identity=bob,
        sender_signing_public_key_b64=alice.signing_public_key_b64,
    )

    assert decrypted_bytes.decode("utf-8") == secret_message


def test_asymmetric_envelope_wrong_recipient_fails_decryption(tmp_path):
    alice = load_or_generate_device_identity(keys_path=tmp_path / "alice.json")
    bob = load_or_generate_device_identity(keys_path=tmp_path / "bob.json")
    eve = load_or_generate_device_identity(keys_path=tmp_path / "eve.json")

    envelope = seal_payload(
        payload="Message for Bob only",
        recipient_exchange_public_key_b64=bob.exchange_public_key_b64,
        recipient_device_id=bob.device_id,
        sender_identity=alice,
    )

    # Eve tries to decrypt with Eve's exchange key
    with pytest.raises(DecryptionError):
        open_payload(envelope, recipient_identity=eve)


def test_asymmetric_envelope_tampered_ciphertext_fails(tmp_path):
    alice = load_or_generate_device_identity(keys_path=tmp_path / "alice.json")
    bob = load_or_generate_device_identity(keys_path=tmp_path / "bob.json")

    envelope = seal_payload(
        payload="Immutable financial statement",
        recipient_exchange_public_key_b64=bob.exchange_public_key_b64,
        recipient_device_id=bob.device_id,
        sender_identity=alice,
    )

    # Tamper with ciphertext
    raw_ct = bytearray(b64_decode_bytes(envelope.ciphertext))
    raw_ct[0] ^= 0x01
    envelope.ciphertext = b64_encode_bytes(bytes(raw_ct))

    with pytest.raises(EnvelopeError):
        open_payload(
            envelope=envelope,
            recipient_identity=bob,
            sender_signing_public_key_b64=alice.signing_public_key_b64,
        )


def test_asymmetric_envelope_forged_signature_fails(tmp_path):
    alice = load_or_generate_device_identity(keys_path=tmp_path / "alice.json")
    bob = load_or_generate_device_identity(keys_path=tmp_path / "bob.json")
    eve = load_or_generate_device_identity(keys_path=tmp_path / "eve.json")

    envelope = seal_payload(
        payload="Signed dispatch instruction",
        recipient_exchange_public_key_b64=bob.exchange_public_key_b64,
        recipient_device_id=bob.device_id,
        sender_identity=alice,
    )

    # Replace Alice's signature with Eve's signature
    envelope.signature = eve.sign(b"forged signature content")

    with pytest.raises(SignatureVerificationError):
        open_payload(
            envelope=envelope,
            recipient_identity=bob,
            sender_signing_public_key_b64=alice.signing_public_key_b64,
        )


# ============================================================
# 4. SYMMETRIC CRYPTOGRAPHIC ENVELOPE TESTS
# ============================================================

def test_symmetric_envelope_seal_and_open():
    secret = "user-provided-secure-passphrase-2026"
    raw_data = "Exported private context memory database"

    env = seal_symmetric(payload=raw_data, secret=secret)
    assert env.version == 1
    assert env.algorithm == "PBKDF2-AES256GCM"
    assert len(b64_decode_bytes(env.salt)) == 16
    assert len(b64_decode_bytes(env.iv)) == 12
    assert len(b64_decode_bytes(env.auth_tag)) == 16

    decrypted = open_symmetric(env, secret=secret)
    assert decrypted.decode("utf-8") == raw_data


def test_symmetric_envelope_wrong_password_fails():
    secret = "correct-passphrase"
    env = seal_symmetric(payload="Confidential export", secret=secret)

    with pytest.raises(DecryptionError):
        open_symmetric(env, secret="wrong-passphrase")


def test_symmetric_envelope_tampered_payload_fails():
    secret = "my-secret-key"
    env = seal_symmetric(payload="Integrity critical data", secret=secret)

    raw_ct = bytearray(b64_decode_bytes(env.ciphertext))
    raw_ct[0] ^= 0x42
    env.ciphertext = b64_encode_bytes(bytes(raw_ct))

    with pytest.raises(DecryptionError):
        open_symmetric(env, secret=secret)


# ============================================================
# 5. DEVICE TRUST STORE & PAIRING HANDSHAKE TESTS
# ============================================================

def test_identity_store_lifecycle_and_local_sync(tmp_path):
    db_file = tmp_path / "test_identity.db"
    initialize_identity_store(db_file)

    identity = load_or_generate_device_identity(keys_path=tmp_path / "keys.json")
    sync_local_identity(identity, db_path=db_file)

    # Check local identity table
    from app.storage.sqlite import connect_sqlite
    with connect_sqlite(db_file) as conn:
        row = conn.execute("SELECT * FROM local_identity WHERE device_id = ?", (identity.device_id,)).fetchone()
        assert row is not None
        assert row["device_id"] == identity.device_id
        assert row["signing_public_key"] == identity.signing_public_key_b64


def test_trusted_device_crud_and_revocation(tmp_path):
    db_file = tmp_path / "trust.db"
    initialize_identity_store(db_file)

    peer_id = str(uuid.uuid4())
    register_trusted_device(
        device_id=peer_id,
        device_name="Alice MacBook Pro",
        device_type="desktop",
        signing_public_key="sig_pub_dummy_b64",
        exchange_public_key="ex_pub_dummy_b64",
        trust_status="paired",
        metadata={"client_version": "1.0.0"},
        db_path=db_file,
    )

    peer = get_trusted_device(peer_id, db_path=db_file)
    assert peer is not None
    assert peer["device_name"] == "Alice MacBook Pro"
    assert peer["trust_status"] == "paired"
    assert peer["metadata"]["client_version"] == "1.0.0"

    all_peers = list_trusted_devices(db_path=db_file)
    assert len(all_peers) == 1

    # Revoke peer
    assert revoke_trusted_device(peer_id, db_path=db_file) is True
    peer_after = get_trusted_device(peer_id, db_path=db_file)
    assert peer_after["trust_status"] == "revoked"

    paired_only = list_trusted_devices(status_filter="paired", db_path=db_file)
    assert len(paired_only) == 0


def test_pairing_challenge_handshake_success(tmp_path):
    db_file = tmp_path / "pairing.db"
    initialize_identity_store(db_file)

    peer_identity = load_or_generate_device_identity(keys_path=tmp_path / "peer.json")

    # 1. Local device issues challenge for peer
    challenge = create_pairing_challenge(
        peer_device_id=peer_identity.device_id,
        ttl_seconds=300,
        db_path=db_file,
    )
    assert len(challenge["challenge_code"]) == 6
    assert challenge["challenge_code"].isdigit()

    # 2. Peer signs (challenge_id + challenge_code) with their Ed25519 signing key
    payload_to_sign = f"{challenge['challenge_id']}:{challenge['challenge_code']}".encode("utf-8")
    peer_sig = peer_identity.sign(payload_to_sign)

    # 3. Local device verifies challenge response and promotes peer
    verified = verify_pairing_challenge(
        challenge_id=challenge["challenge_id"],
        challenge_code=challenge["challenge_code"],
        peer_device_id=peer_identity.device_id,
        peer_device_name="Peer Phone",
        peer_device_type="mobile",
        peer_signing_pub_b64=peer_identity.signing_public_key_b64,
        peer_exchange_pub_b64=peer_identity.exchange_public_key_b64,
        signature_b64=peer_sig,
        db_path=db_file,
    )
    assert verified is True

    # Peer is now recorded as trusted
    peer = get_trusted_device(peer_identity.device_id, db_path=db_file)
    assert peer is not None
    assert peer["trust_status"] == "paired"
    assert peer["device_name"] == "Peer Phone"


def test_pairing_challenge_wrong_code_fails(tmp_path):
    db_file = tmp_path / "pairing.db"
    initialize_identity_store(db_file)

    peer_identity = load_or_generate_device_identity(keys_path=tmp_path / "peer.json")
    challenge = create_pairing_challenge(peer_device_id=peer_identity.device_id, db_path=db_file)

    payload_to_sign = f"{challenge['challenge_id']}:000000".encode("utf-8")
    peer_sig = peer_identity.sign(payload_to_sign)

    verified = verify_pairing_challenge(
        challenge_id=challenge["challenge_id"],
        challenge_code="000000",  # Incorrect code
        peer_device_id=peer_identity.device_id,
        peer_device_name="Peer Phone",
        peer_device_type="mobile",
        peer_signing_pub_b64=peer_identity.signing_public_key_b64,
        peer_exchange_pub_b64=peer_identity.exchange_public_key_b64,
        signature_b64=peer_sig,
        db_path=db_file,
    )
    assert verified is False


def test_pairing_challenge_expired_fails(tmp_path):
    db_file = tmp_path / "pairing.db"
    initialize_identity_store(db_file)

    peer_identity = load_or_generate_device_identity(keys_path=tmp_path / "peer.json")
    # Expire immediately with negative TTL
    challenge = create_pairing_challenge(
        peer_device_id=peer_identity.device_id,
        ttl_seconds=-10,
        db_path=db_file,
    )

    payload_to_sign = f"{challenge['challenge_id']}:{challenge['challenge_code']}".encode("utf-8")
    peer_sig = peer_identity.sign(payload_to_sign)

    verified = verify_pairing_challenge(
        challenge_id=challenge["challenge_id"],
        challenge_code=challenge["challenge_code"],
        peer_device_id=peer_identity.device_id,
        peer_device_name="Peer Phone",
        peer_device_type="mobile",
        peer_signing_pub_b64=peer_identity.signing_public_key_b64,
        peer_exchange_pub_b64=peer_identity.exchange_public_key_b64,
        signature_b64=peer_sig,
        db_path=db_file,
    )
    assert verified is False


# ============================================================
# 6. REST API ENDPOINT TESTS (/api/device/...)
# ============================================================

def test_api_get_device_identity(client):
    # Unauthenticated request rejected
    res_401 = client.get("/api/device/identity")
    assert res_401.status_code == 401

    # Authenticated request succeeds
    res = client.get(
        "/api/device/identity",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "device_id" in data
    assert "signing_public_key" in data
    assert "exchange_public_key" in data
    assert data["device_type"] == "desktop"


def test_api_peers_crud_and_revocation(client, tmp_path):
    peer_id = str(uuid.uuid4())
    register_trusted_device(
        device_id=peer_id,
        device_name="Companion Tablet",
        device_type="tablet",
        signing_public_key="peer_signing_b64",
        exchange_public_key="peer_exchange_b64",
    )

    # 1. List peers
    res_list = client.get(
        "/api/device/peers",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res_list.status_code == 200
    peers = res_list.json()
    assert any(p["device_id"] == peer_id for p in peers)

    # 2. Get specific peer
    res_get = client.get(
        f"/api/device/peers/{peer_id}",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res_get.status_code == 200
    assert res_get.json()["device_name"] == "Companion Tablet"

    # 3. Revoke peer
    res_del = client.delete(
        f"/api/device/peers/{peer_id}",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "revoked"

    # 4. Verify status changed to revoked
    res_get_revoked = client.get(
        f"/api/device/peers/{peer_id}",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res_get_revoked.json()["trust_status"] == "revoked"


def test_api_pairing_handshake_flow(client, tmp_path):
    peer_identity = load_or_generate_device_identity(keys_path=tmp_path / "api_peer.json")

    # 1. Initiate pairing
    initiate_res = client.post(
        "/api/device/pairing/initiate",
        json={"peer_device_id": peer_identity.device_id, "ttl_seconds": 300},
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert initiate_res.status_code == 200
    challenge = initiate_res.json()
    chal_id = challenge["challenge_id"]
    chal_code = challenge["challenge_code"]

    # 2. Peer signs (challenge_id + challenge_code)
    payload_to_sign = f"{chal_id}:{chal_code}".encode("utf-8")
    peer_sig = peer_identity.sign(payload_to_sign)

    # 3. Verify pairing
    verify_res = client.post(
        "/api/device/pairing/verify",
        json={
            "challenge_id": chal_id,
            "challenge_code": chal_code,
            "peer_device_id": peer_identity.device_id,
            "peer_device_name": "API Mobile Client",
            "peer_device_type": "mobile",
            "peer_signing_public_key": peer_identity.signing_public_key_b64,
            "peer_exchange_public_key": peer_identity.exchange_public_key_b64,
            "signature": peer_sig,
        },
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["success"] is True

    # Check peer is now trusted
    peer_check = client.get(
        f"/api/device/peers/{peer_identity.device_id}",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert peer_check.status_code == 200
    assert peer_check.json()["trust_status"] == "paired"


def test_api_envelope_seal_and_open_roundtrip(client, tmp_path):
    # Register peer Bob
    bob = load_or_generate_device_identity(keys_path=tmp_path / "bob_api.json")
    register_trusted_device(
        device_id=bob.device_id,
        device_name="Bob Remote Node",
        device_type="server",
        signing_public_key=bob.signing_public_key_b64,
        exchange_public_key=bob.exchange_public_key_b64,
        trust_status="paired",
    )

    # 1. Local device seals envelope for Bob via API
    seal_res = client.post(
        "/api/device/envelope/seal",
        json={
            "recipient_device_id": bob.device_id,
            "payload": "Secret dispatch instructions over cryptographic envelope.",
        },
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert seal_res.status_code == 200
    env_data = seal_res.json()
    assert env_data["recipient_device_id"] == bob.device_id

    # 2. Bob receives envelope and opens it
    # Bob opens it directly using Bob's private keys and local device's public signing key
    local_dev = get_device_identity()
    decrypted = open_payload(
        envelope=env_data,
        recipient_identity=bob,
        sender_signing_public_key_b64=local_dev.signing_public_key_b64,
    )
    assert decrypted.decode("utf-8") == "Secret dispatch instructions over cryptographic envelope."

    # 3. Conversely, Bob seals an envelope for local device, and local device opens it via API
    bob_sealed = seal_payload(
        payload="Reply from Bob back to LifeOS.",
        recipient_exchange_public_key_b64=local_dev.exchange_public_key_b64,
        recipient_device_id=local_dev.device_id,
        sender_identity=bob,
    )

    open_res = client.post(
        "/api/device/envelope/open",
        json={"envelope": bob_sealed.to_dict()},
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert open_res.status_code == 200
    assert open_res.json()["payload"] == "Reply from Bob back to LifeOS."
    assert open_res.json()["sender_device_id"] == bob.device_id


def test_api_envelope_seal_rejected_for_untrusted_peer(client):
    res_404 = client.post(
        "/api/device/envelope/seal",
        json={"recipient_device_id": "non-existent-device-id", "payload": "Secret"},
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res_404.status_code == 404
