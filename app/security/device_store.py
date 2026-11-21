"""
device_store.py

Persistent SQLite trust store for device identities, paired peers, and
cryptographic pairing handshakes in LifeOS (`data/identity.db`).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security.device_identity import DeviceIdentity, verify_signature
from app.storage.sqlite import connect_sqlite

try:
    from config import IDENTITY_DB
except ImportError:
    from app.config import IDENTITY_DB

logger = logging.getLogger("lifeos.security.store")


def _get_db_path(custom_path: Path | str | None = None) -> Path:
    if custom_path is not None:
        return Path(custom_path).resolve()
    try:
        from config import IDENTITY_DB
        return Path(IDENTITY_DB).resolve()
    except Exception:
        from app.config import IDENTITY_DB
        return Path(IDENTITY_DB).resolve()


def initialize_identity_store(db_path: Path | str | None = None) -> None:
    """Initialize the identity and trusted device tables in SQLite."""
    target_path = _get_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS local_identity (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                os_info TEXT NOT NULL,
                signing_public_key TEXT NOT NULL,
                exchange_public_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trusted_devices (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                signing_public_key TEXT NOT NULL,
                exchange_public_key TEXT NOT NULL,
                trust_status TEXT NOT NULL, -- 'paired', 'pending', 'revoked'
                paired_at TEXT NOT NULL,
                last_seen_at TEXT,
                metadata_json TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pairing_challenges (
                challenge_id TEXT PRIMARY KEY,
                peer_device_id TEXT NOT NULL,
                challenge_code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL -- 'pending', 'verified', 'expired'
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_trusted_status ON trusted_devices(trust_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenge_peer ON pairing_challenges(peer_device_id, status)")


def sync_local_identity(identity: DeviceIdentity, db_path: Path | str | None = None) -> None:
    """Ensure the local device identity record in identity.db matches active credentials."""
    target_path = _get_db_path(db_path)
    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO local_identity (
                device_id, device_name, device_type, os_info,
                signing_public_key, exchange_public_key, created_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_name = excluded.device_name,
                device_type = excluded.device_type,
                os_info = excluded.os_info,
                signing_public_key = excluded.signing_public_key,
                exchange_public_key = excluded.exchange_public_key,
                last_seen_at = excluded.last_seen_at
        """, (
            identity.device_id,
            identity.device_name,
            identity.device_type,
            identity.os_info,
            identity.signing_public_key_b64,
            identity.exchange_public_key_b64,
            identity.created_at,
            identity.last_seen_at,
        ))


def register_trusted_device(
    device_id: str,
    device_name: str,
    device_type: str,
    signing_public_key: str,
    exchange_public_key: str,
    trust_status: str = "paired",
    metadata: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Register or update a peer device in the trusted store."""
    target_path = _get_db_path(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    meta_str = json.dumps(metadata or {})

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO trusted_devices (
                device_id, device_name, device_type,
                signing_public_key, exchange_public_key,
                trust_status, paired_at, last_seen_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_name = excluded.device_name,
                device_type = excluded.device_type,
                signing_public_key = excluded.signing_public_key,
                exchange_public_key = excluded.exchange_public_key,
                trust_status = excluded.trust_status,
                last_seen_at = excluded.last_seen_at,
                metadata_json = excluded.metadata_json
        """, (
            device_id,
            device_name,
            device_type,
            signing_public_key,
            exchange_public_key,
            trust_status,
            now_iso,
            now_iso,
            meta_str,
        ))


def get_trusted_device(device_id: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Retrieve a trusted device record by device_id."""
    target_path = _get_db_path(db_path)
    with connect_sqlite(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM trusted_devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()

        if not row:
            return None

        data = dict(row)
        if data.get("metadata_json"):
            try:
                data["metadata"] = json.loads(data["metadata_json"])
            except Exception:
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        return data


def list_trusted_devices(
    status_filter: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List trusted devices, optionally filtered by status ('paired', 'pending', 'revoked')."""
    target_path = _get_db_path(db_path)
    with connect_sqlite(target_path) as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM trusted_devices WHERE trust_status = ? ORDER BY paired_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trusted_devices ORDER BY paired_at DESC"
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("metadata_json"):
                try:
                    d["metadata"] = json.loads(d["metadata_json"])
                except Exception:
                    d["metadata"] = {}
            else:
                d["metadata"] = {}
            results.append(d)
        return results


def revoke_trusted_device(device_id: str, db_path: Path | str | None = None) -> bool:
    """Revoke a peer device's trusted status."""
    target_path = _get_db_path(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with connect_sqlite(target_path) as conn:
        cursor = conn.execute(
            "UPDATE trusted_devices SET trust_status = 'revoked', last_seen_at = ? WHERE device_id = ?",
            (now_iso, device_id),
        )
        return cursor.rowcount > 0


def update_peer_last_seen(device_id: str, db_path: Path | str | None = None) -> None:
    """Update last_seen_at timestamp for a trusted device."""
    target_path = _get_db_path(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with connect_sqlite(target_path) as conn:
        conn.execute(
            "UPDATE trusted_devices SET last_seen_at = ? WHERE device_id = ?",
            (now_iso, device_id),
        )


def create_pairing_challenge(
    peer_device_id: str,
    ttl_seconds: int = 300,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Generate a high-entropy 6-digit numeric pairing code with a strict expiration window."""
    target_path = _get_db_path(db_path)
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc).isoformat()
    challenge_id = f"chal_{secrets.token_hex(12)}"
    code = f"{secrets.randbelow(1_000_000):06d}"

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO pairing_challenges (
                challenge_id, peer_device_id, challenge_code, expires_at, created_at, status
            ) VALUES (?, ?, ?, ?, ?, 'pending')
        """, (challenge_id, peer_device_id, code, expires_at, now.isoformat()))

    result = {
        "challenge_id": challenge_id,
        "peer_device_id": peer_device_id,
        "challenge_code": code,
        "expires_at": expires_at,
        "created_at": now.isoformat(),
    }

    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type="device.pairing_requested",
            producer="device",
            payload={"challenge_id": challenge_id, "peer_device_id": peer_device_id, "expires_at": expires_at},
        ))
    except Exception:
        pass

    return result


def verify_pairing_challenge(
    challenge_id: str,
    challenge_code: str,
    peer_device_id: str,
    peer_device_name: str,
    peer_device_type: str,
    peer_signing_pub_b64: str,
    peer_exchange_pub_b64: str,
    signature_b64: str,
    db_path: Path | str | None = None,
) -> bool:
    """
    Verify pairing challenge:
    1. Validates code match and expiration.
    2. Verifies cryptographic signature from peer signing key over challenge payload.
    3. Promotes peer to 'paired' status in trusted_devices.
    """
    target_path = _get_db_path(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()

    with connect_sqlite(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM pairing_challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()

        if not row:
            logger.warning(f"Pairing challenge {challenge_id} not found.")
            return False

        if row["status"] != "pending":
            logger.warning(f"Pairing challenge {challenge_id} is not pending (status: {row['status']}).")
            return False

        if row["expires_at"] < now_iso:
            conn.execute(
                "UPDATE pairing_challenges SET status = 'expired' WHERE challenge_id = ?",
                (challenge_id,),
            )
            logger.warning(f"Pairing challenge {challenge_id} has expired.")
            return False

        # Constant-time comparison of pairing code
        if not secrets.compare_digest(row["challenge_code"], challenge_code.strip()):
            logger.warning(f"Invalid pairing code for challenge {challenge_id}.")
            return False

        # Verify cryptographic signature of (challenge_id + challenge_code)
        challenge_bytes = f"{challenge_id}:{challenge_code.strip()}".encode("utf-8")
        if not verify_signature(peer_signing_pub_b64, challenge_bytes, signature_b64):
            logger.warning(f"Signature verification failed for peer {peer_device_id} on challenge {challenge_id}.")
            return False

        # Mark challenge verified
        conn.execute(
            "UPDATE pairing_challenges SET status = 'verified' WHERE challenge_id = ?",
            (challenge_id,),
        )

    # Register as trusted device
    register_trusted_device(
        device_id=peer_device_id,
        device_name=peer_device_name,
        device_type=peer_device_type,
        signing_public_key=peer_signing_pub_b64,
        exchange_public_key=peer_exchange_pub_b64,
        trust_status="paired",
        db_path=target_path,
    )
    logger.info(f"Successfully paired and trusted device {peer_device_id} ({peer_device_name})")
    return True
