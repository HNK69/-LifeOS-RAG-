"""
device_identity.py

Cryptographic device identity and asymmetric key management for LifeOS.

Features:
- Unique canonical device identifier (UUIDv4)
- Ed25519 asymmetric signing keypair (identity & tamper-proof signatures)
- X25519 asymmetric key-exchange keypair (Diffie-Hellman envelope encryption)
- Secure local keypair persistence and atomic loading
- Public identity serialization for peer discovery and pairing
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519

try:
    from config import DEVICE_KEYS_PATH, DEVICE_NAME, DEVICE_TYPE
except ImportError:
    from app.config import DEVICE_KEYS_PATH, DEVICE_NAME, DEVICE_TYPE

logger = logging.getLogger("lifeos.security.identity")

_active_device_identity: DeviceIdentity | None = None


@dataclass
class DeviceIdentity:
    """Represents the local device's cryptographic identity and key material."""

    device_id: str
    device_name: str
    device_type: str
    os_info: str
    signing_public_key_b64: str
    exchange_public_key_b64: str
    created_at: str
    last_seen_at: str
    _signing_private_key: ed25519.Ed25519PrivateKey
    _exchange_private_key: x25519.X25519PrivateKey

    @property
    def signing_private_key(self) -> ed25519.Ed25519PrivateKey:
        return self._signing_private_key

    @property
    def exchange_private_key(self) -> x25519.X25519PrivateKey:
        return self._exchange_private_key

    def sign(self, data: bytes) -> str:
        """Sign binary data using Ed25519 private key, returning base64 signature."""
        sig = self._signing_private_key.sign(data)
        return base64.b64encode(sig).decode("ascii")

    def to_public_dict(self) -> dict[str, Any]:
        """Return public verifiable identity without private keys."""
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "os_info": self.os_info,
            "signing_public_key": self.signing_public_key_b64,
            "exchange_public_key": self.exchange_public_key_b64,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }


def b64_encode_bytes(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def b64_decode_bytes(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def sign_data(data: bytes, private_key: ed25519.Ed25519PrivateKey | None = None) -> str:
    """Sign data with device signing key (or specified private key)."""
    if private_key is None:
        device = get_device_identity()
        private_key = device.signing_private_key
    sig = private_key.sign(data)
    return b64_encode_bytes(sig)


def verify_signature(public_key_b64: str, data: bytes, signature_b64: str) -> bool:
    """
    Verify an Ed25519 digital signature against data.
    Returns True if valid, False if invalid or malformed.
    """
    try:
        raw_pub = b64_decode_bytes(public_key_b64)
        raw_sig = b64_decode_bytes(signature_b64)
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(raw_pub)
        pub_key.verify(raw_sig, data)
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def load_or_generate_device_identity(
    keys_path: Path | str | None = None,
    force_regenerate: bool = False,
) -> DeviceIdentity:
    """
    Load existing device identity and key material, or generate a new keypair
    and persist it securely to disk.
    """
    target_path = Path(keys_path or DEVICE_KEYS_PATH).resolve()

    if not force_regenerate and target_path.is_file():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            sig_priv_bytes = b64_decode_bytes(data["signing_private_key"])
            ex_priv_bytes = b64_decode_bytes(data["exchange_private_key"])

            signing_priv = ed25519.Ed25519PrivateKey.from_private_bytes(sig_priv_bytes)
            exchange_priv = x25519.X25519PrivateKey.from_private_bytes(ex_priv_bytes)

            now_iso = datetime.now(timezone.utc).isoformat()

            identity = DeviceIdentity(
                device_id=data["device_id"],
                device_name=data.get("device_name") or DEVICE_NAME or socket.gethostname(),
                device_type=data.get("device_type") or DEVICE_TYPE,
                os_info=data.get("os_info") or f"{platform.system()} {platform.release()}",
                signing_public_key_b64=data["signing_public_key"],
                exchange_public_key_b64=data["exchange_public_key"],
                created_at=data.get("created_at") or now_iso,
                last_seen_at=now_iso,
                _signing_private_key=signing_priv,
                _exchange_private_key=exchange_priv,
            )
            return identity
        except Exception as exc:
            logger.warning(f"Failed to read device keys from {target_path}: {exc}. Regenerating...")

    # Generate new identity and keypairs
    now_iso = datetime.now(timezone.utc).isoformat()
    device_id = str(uuid.uuid4())
    device_name = DEVICE_NAME or socket.gethostname()
    device_type = DEVICE_TYPE
    os_info = f"{platform.system()} {platform.release()}"

    signing_priv = ed25519.Ed25519PrivateKey.generate()
    exchange_priv = x25519.X25519PrivateKey.generate()

    sig_pub_bytes = signing_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    ex_pub_bytes = exchange_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    sig_priv_bytes = signing_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ex_priv_bytes = exchange_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    sig_pub_b64 = b64_encode_bytes(sig_pub_bytes)
    ex_pub_b64 = b64_encode_bytes(ex_pub_bytes)
    sig_priv_b64 = b64_encode_bytes(sig_priv_bytes)
    ex_priv_b64 = b64_encode_bytes(ex_priv_bytes)

    identity = DeviceIdentity(
        device_id=device_id,
        device_name=device_name,
        device_type=device_type,
        os_info=os_info,
        signing_public_key_b64=sig_pub_b64,
        exchange_public_key_b64=ex_pub_b64,
        created_at=now_iso,
        last_seen_at=now_iso,
        _signing_private_key=signing_priv,
        _exchange_private_key=exchange_priv,
    )

    # Persist securely
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "device_id": device_id,
        "device_name": device_name,
        "device_type": device_type,
        "os_info": os_info,
        "signing_public_key": sig_pub_b64,
        "exchange_public_key": ex_pub_b64,
        "signing_private_key": sig_priv_b64,
        "exchange_private_key": ex_priv_b64,
        "created_at": now_iso,
    }

    tmp_target = target_path.with_suffix(".tmp")
    with open(tmp_target, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Restrict permissions where supported
    try:
        os.chmod(tmp_target, 0o600)
    except Exception:
        pass

    tmp_target.replace(target_path)
    logger.info(f"Initialized new device identity: {device_id} ({device_name}) at {target_path}")
    return identity


def get_device_identity() -> DeviceIdentity:
    """Get or initialize the current process's active device identity."""
    global _active_device_identity
    if _active_device_identity is None:
        _active_device_identity = load_or_generate_device_identity()
    return _active_device_identity


def set_device_identity(identity: DeviceIdentity | None) -> None:
    """Set the active device identity (primarily for testing)."""
    global _active_device_identity
    _active_device_identity = identity


def reset_device_identity() -> None:
    """Clear cached device identity."""
    global _active_device_identity
    _active_device_identity = None
