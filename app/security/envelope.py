"""
envelope.py

Cryptographic envelope encryption and sealing for LifeOS.

Supports:
1. Asymmetric Envelope (X25519-HKDF-AES256GCM + Ed25519 Signatures):
   - Ephemeral key-exchange providing forward secrecy
   - Recipient public key encryption
   - Authenticated encryption with associated data (AEAD)
   - Verifiable digital signature ensuring sender authenticity and non-repudiation
2. Symmetric Envelope (PBKDF2HMAC-SHA256 + AES256GCM):
   - Password/passphrase derived key with 100,000 PBKDF2 iterations
   - Salted and authenticated for local backups, sensitive caches, and credential vaults
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.security.device_identity import (
    DeviceIdentity,
    b64_decode_bytes,
    b64_encode_bytes,
    get_device_identity,
    verify_signature,
)

logger = logging.getLogger("lifeos.security.envelope")

ENVELOPE_VERSION = 1
ENVELOPE_INFO = b"LifeOS-Envelope-v1"
PBKDF2_ITERATIONS = 100_000


class EnvelopeError(Exception):
    """Base exception for envelope cryptographic failures."""
    pass


class DecryptionError(EnvelopeError):
    """Raised when ciphertext authentication or decryption fails."""
    pass


class SignatureVerificationError(EnvelopeError):
    """Raised when envelope digital signature verification fails."""
    pass


@dataclass
class CryptographicEnvelope:
    """Standard authenticated asymmetric envelope container."""

    version: int
    sender_device_id: str
    recipient_device_id: str
    algorithm: str
    ephemeral_public_key: str  # Base64 raw X25519 public key (32 bytes)
    iv: str                    # Base64 12 bytes IV
    ciphertext: str            # Base64 encrypted payload
    auth_tag: str              # Base64 16 bytes authentication tag
    signature: str             # Base64 64 bytes Ed25519 signature
    timestamp: str             # ISO UTC string

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CryptographicEnvelope:
        return cls(
            version=int(data["version"]),
            sender_device_id=data["sender_device_id"],
            recipient_device_id=data["recipient_device_id"],
            algorithm=data.get("algorithm", "X25519-HKDF-AES256GCM"),
            ephemeral_public_key=data["ephemeral_public_key"],
            iv=data["iv"],
            ciphertext=data["ciphertext"],
            auth_tag=data["auth_tag"],
            signature=data["signature"],
            timestamp=data["timestamp"],
        )

    @classmethod
    def from_json(cls, json_str: str) -> CryptographicEnvelope:
        return cls.from_dict(json.loads(json_str))


@dataclass
class SymmetricEnvelope:
    """Standard password/passphrase authenticated symmetric container."""

    version: int
    algorithm: str
    salt: str        # Base64 16 bytes
    iv: str          # Base64 12 bytes
    ciphertext: str  # Base64 encrypted payload
    auth_tag: str    # Base64 16 bytes
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SymmetricEnvelope:
        return cls(
            version=int(data["version"]),
            algorithm=data.get("algorithm", "PBKDF2-AES256GCM"),
            salt=data["salt"],
            iv=data["iv"],
            ciphertext=data["ciphertext"],
            auth_tag=data["auth_tag"],
            timestamp=data["timestamp"],
        )

    @classmethod
    def from_json(cls, json_str: str) -> SymmetricEnvelope:
        return cls.from_dict(json.loads(json_str))


def _compute_envelope_aad(version: int, sender_id: str, recipient_id: str, timestamp: str) -> bytes:
    """Compute Associated Authenticated Data (AAD) bound to the envelope metadata."""
    return f"{version}:{sender_id}:{recipient_id}:{timestamp}".encode("utf-8")


def _compute_signature_payload(
    ephemeral_pub_bytes: bytes,
    recipient_id: str,
    iv_bytes: bytes,
    ciphertext_bytes: bytes,
    auth_tag_bytes: bytes,
    timestamp: str,
) -> bytes:
    """Compute canonical binary payload for digital signature verification."""
    return (
        ephemeral_pub_bytes
        + recipient_id.encode("utf-8")
        + iv_bytes
        + ciphertext_bytes
        + auth_tag_bytes
        + timestamp.encode("utf-8")
    )


def seal_payload(
    payload: bytes | str,
    recipient_exchange_public_key_b64: str,
    recipient_device_id: str,
    sender_identity: DeviceIdentity | None = None,
) -> CryptographicEnvelope:
    """
    Encrypt and sign a payload for a target recipient device using asymmetric
    ephemeral X25519 Diffie-Hellman + AES-256-GCM + Ed25519 digital signature.
    """
    if sender_identity is None:
        sender_identity = get_device_identity()

    raw_payload = payload.encode("utf-8") if isinstance(payload, str) else payload
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Parse recipient's public exchange key
    try:
        recip_pub_bytes = b64_decode_bytes(recipient_exchange_public_key_b64)
        recip_pub_key = x25519.X25519PublicKey.from_public_bytes(recip_pub_bytes)
    except Exception as exc:
        raise ValueError(f"Invalid recipient public exchange key: {exc}") from exc

    # 2. Generate ephemeral X25519 keypair for forward secrecy
    ephemeral_priv = x25519.X25519PrivateKey.generate()
    ephemeral_pub = ephemeral_priv.public_key()
    ephemeral_pub_bytes = ephemeral_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # 3. Perform Diffie-Hellman key exchange and derive AES key via HKDF
    shared_secret = ephemeral_priv.exchange(recip_pub_key)
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=ENVELOPE_INFO,
    ).derive(shared_secret)

    # 4. Generate 12-byte IV and encrypt with AES-256-GCM
    iv = os.urandom(12)
    aad = _compute_envelope_aad(ENVELOPE_VERSION, sender_identity.device_id, recipient_device_id, now_iso)

    aesgcm = AESGCM(aes_key)
    encrypted_blob = aesgcm.encrypt(iv, raw_payload, aad)
    # cryptography AESGCM appends the 16-byte tag to the ciphertext
    ciphertext = encrypted_blob[:-16]
    auth_tag = encrypted_blob[-16:]

    # 5. Sign the envelope components with the sender's Ed25519 private key
    sig_payload = _compute_signature_payload(
        ephemeral_pub_bytes,
        recipient_device_id,
        iv,
        ciphertext,
        auth_tag,
        now_iso,
    )
    signature_bytes = sender_identity.signing_private_key.sign(sig_payload)

    return CryptographicEnvelope(
        version=ENVELOPE_VERSION,
        sender_device_id=sender_identity.device_id,
        recipient_device_id=recipient_device_id,
        algorithm="X25519-HKDF-AES256GCM",
        ephemeral_public_key=b64_encode_bytes(ephemeral_pub_bytes),
        iv=b64_encode_bytes(iv),
        ciphertext=b64_encode_bytes(ciphertext),
        auth_tag=b64_encode_bytes(auth_tag),
        signature=b64_encode_bytes(signature_bytes),
        timestamp=now_iso,
    )


def open_payload(
    envelope: CryptographicEnvelope | dict[str, Any],
    recipient_identity: DeviceIdentity | None = None,
    sender_signing_public_key_b64: str | None = None,
) -> bytes:
    """
    Open and decrypt a cryptographic envelope using the recipient's private exchange key,
    verifying AEAD integrity and optional Ed25519 signature authenticity.
    """
    if isinstance(envelope, dict):
        envelope = CryptographicEnvelope.from_dict(envelope)

    if recipient_identity is None:
        recipient_identity = get_device_identity()

    if envelope.version != ENVELOPE_VERSION:
        raise EnvelopeError(f"Unsupported envelope version: {envelope.version}")

    try:
        ephemeral_pub_bytes = b64_decode_bytes(envelope.ephemeral_public_key)
        iv = b64_decode_bytes(envelope.iv)
        ciphertext = b64_decode_bytes(envelope.ciphertext)
        auth_tag = b64_decode_bytes(envelope.auth_tag)
        signature = b64_decode_bytes(envelope.signature)
    except Exception as exc:
        raise EnvelopeError(f"Corrupt base64 in envelope: {exc}") from exc

    # 1. Verify digital signature if sender public key provided
    if sender_signing_public_key_b64:
        sig_payload = _compute_signature_payload(
            ephemeral_pub_bytes,
            envelope.recipient_device_id,
            iv,
            ciphertext,
            auth_tag,
            envelope.timestamp,
        )
        if not verify_signature(sender_signing_public_key_b64, sig_payload, envelope.signature):
            raise SignatureVerificationError("Envelope digital signature verification failed: invalid or forged signature.")

    # 2. Reconstruct shared secret via Diffie-Hellman
    try:
        ephemeral_pub = x25519.X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)
        shared_secret = recipient_identity.exchange_private_key.exchange(ephemeral_pub)
    except Exception as exc:
        raise DecryptionError(f"Diffie-Hellman key exchange failed: {exc}") from exc

    # 3. Derive AES key via HKDF
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=ENVELOPE_INFO,
    ).derive(shared_secret)

    # 4. Decrypt and authenticate AEAD ciphertext
    aad = _compute_envelope_aad(envelope.version, envelope.sender_device_id, envelope.recipient_device_id, envelope.timestamp)
    encrypted_blob = ciphertext + auth_tag

    try:
        aesgcm = AESGCM(aes_key)
        plaintext = aesgcm.decrypt(iv, encrypted_blob, aad)
        return plaintext
    except Exception as exc:
        raise DecryptionError(f"AES-GCM decryption failed: ciphertext corrupted or unauthorized recipient ({exc})") from exc


def seal_symmetric(
    payload: bytes | str,
    secret: bytes | str,
) -> SymmetricEnvelope:
    """
    Encrypt a payload symmetrically using PBKDF2HMAC-SHA256 (100,000 iterations) + AES-256-GCM.
    """
    raw_payload = payload.encode("utf-8") if isinstance(payload, str) else payload
    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    now_iso = datetime.now(timezone.utc).isoformat()

    salt = os.urandom(16)
    iv = os.urandom(12)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    aes_key = kdf.derive(raw_secret)

    aesgcm = AESGCM(aes_key)
    encrypted_blob = aesgcm.encrypt(iv, raw_payload, None)
    ciphertext = encrypted_blob[:-16]
    auth_tag = encrypted_blob[-16:]

    return SymmetricEnvelope(
        version=ENVELOPE_VERSION,
        algorithm="PBKDF2-AES256GCM",
        salt=b64_encode_bytes(salt),
        iv=b64_encode_bytes(iv),
        ciphertext=b64_encode_bytes(ciphertext),
        auth_tag=b64_encode_bytes(auth_tag),
        timestamp=now_iso,
    )


def open_symmetric(
    envelope: SymmetricEnvelope | dict[str, Any],
    secret: bytes | str,
) -> bytes:
    """
    Decrypt an authenticated symmetric envelope using the shared secret.
    """
    if isinstance(envelope, dict):
        envelope = SymmetricEnvelope.from_dict(envelope)

    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret

    try:
        salt = b64_decode_bytes(envelope.salt)
        iv = b64_decode_bytes(envelope.iv)
        ciphertext = b64_decode_bytes(envelope.ciphertext)
        auth_tag = b64_decode_bytes(envelope.auth_tag)
    except Exception as exc:
        raise EnvelopeError(f"Corrupt base64 in symmetric envelope: {exc}") from exc

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    aes_key = kdf.derive(raw_secret)

    encrypted_blob = ciphertext + auth_tag
    try:
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(iv, encrypted_blob, None)
    except Exception as exc:
        raise DecryptionError(f"Symmetric decryption failed: incorrect secret or corrupted ciphertext ({exc})") from exc
