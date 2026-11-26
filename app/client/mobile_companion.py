"""
mobile_companion.py

Persistent, offline-first Mobile Companion Client for LifeOS (Milestone C5):
- Independent mobile DeviceIdentity (Ed25519 + X25519)
- QR pairing payload consumer with TTL check, key pinning, and Ed25519 challenge signing
- E2EE CryptographicEnvelope sync (push outbox + pull desktop updates)
- Chunked & resumable transfer for packages > 10 MB with SHA-256 verification
- Offline mutation outbox with exponential backoff retry scheduling
- Remote wipe & device revocation zeroization handler
- Deterministic AndroidLifecycleManager & iOSLifecycleManager simulation adapters
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519

from app.client.mobile_store import MobileCompanionStore
from app.security.device_identity import (
    DeviceIdentity,
    b64_decode_bytes,
    b64_encode_bytes,
)
from app.security.envelope import (
    CryptographicEnvelope,
    open_payload,
    seal_payload,
)
from app.sync.models import (
    ClockRelationship,
    SyncBatch,
    SyncChangeType,
    SyncEntityType,
    SyncItem,
)
from app.sync.vector_clock import VectorClock

logger = logging.getLogger("lifeos.client.mobile_companion")


class MobileCompanionError(Exception):
    """Base error for mobile companion operations."""


class TransportOfflineError(MobileCompanionError):
    """Raised when network transport is offline or unreachable."""


class TransportHttpError(MobileCompanionError):
    """Raised on non-2xx HTTP response from desktop server."""

    def __init__(self, status_code: int, detail: str = ""):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class CompanionTransport(Protocol):
    def request(
        self,
        method: str,
        base_url: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        ...


class UrllibTransport:
    """Dependency-free HTTP transport using stdlib urllib."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def request(
        self,
        method: str,
        base_url: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{base_url.rstrip('/')}{path}"
        req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req = Request(url, data=data, headers=req_headers, method=method.upper())
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return int(resp.status), (json.loads(raw) if raw else {})
        except HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            return int(err.code), {"detail": err_body}
        except URLError as err:
            raise TransportOfflineError(f"Network unreachable: {err.reason}") from err
        except Exception as err:
            raise TransportOfflineError(f"Transport error: {err}") from err


class TestClientTransport:
    """In-process adapter wrapping Starlette/FastAPI TestClient."""

    __test__ = False

    def __init__(self, test_client: Any):
        self.test_client = test_client

    def request(
        self,
        method: str,
        base_url: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        resp = self.test_client.request(
            method=method.upper(),
            url=path,
            json=json_body,
            headers=headers or {},
        )
        try:
            data = resp.json()
        except Exception:
            data = {"detail": getattr(resp, "text", "")}
        return int(resp.status_code), data


@dataclass
class SyncResult:
    success: bool
    offline: bool = False
    pushed_count: int = 0
    pulled_imported: int = 0
    pulled_skipped: int = 0
    conflicts_resolved: int = 0
    batch_id: str | None = None
    chunked_used: bool = False
    error: str | None = None


def generate_mobile_identity(
    keys_path: Path | str,
    device_name: str = "LifeOS Mobile Companion",
    os_info: str = "MobileOS",
    force_regenerate: bool = False,
) -> DeviceIdentity:
    """
    Load or generate an independent Ed25519 + X25519 DeviceIdentity with
    device_type='mobile', stored strictly isolated from desktop server keys.
    """
    target = Path(keys_path).resolve()
    if not force_regenerate and target.is_file():
        data = json.loads(target.read_text(encoding="utf-8"))
        sig_priv = ed25519.Ed25519PrivateKey.from_private_bytes(
            b64_decode_bytes(data["signing_private_key"])
        )
        ex_priv = x25519.X25519PrivateKey.from_private_bytes(
            b64_decode_bytes(data["exchange_private_key"])
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        return DeviceIdentity(
            device_id=data["device_id"],
            device_name=data.get("device_name", device_name),
            device_type="mobile",
            os_info=data.get("os_info", os_info),
            signing_public_key_b64=data["signing_public_key"],
            exchange_public_key_b64=data["exchange_public_key"],
            created_at=data.get("created_at", now_iso),
            last_seen_at=now_iso,
            _signing_private_key=sig_priv,
            _exchange_private_key=ex_priv,
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    device_id = str(uuid.uuid4())
    sig_priv = ed25519.Ed25519PrivateKey.generate()
    ex_priv = x25519.X25519PrivateKey.generate()

    sig_pub_b = sig_priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ex_pub_b = ex_priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    sig_priv_b = sig_priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    ex_priv_b = ex_priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())

    identity = DeviceIdentity(
        device_id=device_id,
        device_name=device_name,
        device_type="mobile",
        os_info=os_info,
        signing_public_key_b64=b64_encode_bytes(sig_pub_b),
        exchange_public_key_b64=b64_encode_bytes(ex_pub_b),
        created_at=now_iso,
        last_seen_at=now_iso,
        _signing_private_key=sig_priv,
        _exchange_private_key=ex_priv,
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "device_id": identity.device_id,
        "device_name": identity.device_name,
        "device_type": "mobile",
        "os_info": identity.os_info,
        "signing_public_key": identity.signing_public_key_b64,
        "exchange_public_key": identity.exchange_public_key_b64,
        "signing_private_key": b64_encode_bytes(sig_priv_b),
        "exchange_private_key": b64_encode_bytes(ex_priv_b),
        "created_at": now_iso,
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(target)
    return identity


class _PersistentWorkspaceSet(set):
    """Set subclass that persists local_only workspace boundaries to MobileCompanionStore on mutation."""

    def __init__(self, initial: set[str] | None = None, on_change: Callable[[set[str]], None] | None = None):
        super().__init__(initial or set())
        self._on_change = on_change

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(set(self))

    def add(self, element: str) -> None:
        super().add(element)
        self._notify()

    def update(self, *s: Any) -> None:
        super().update(*s)
        self._notify()

    def remove(self, element: str) -> None:
        super().remove(element)
        self._notify()

    def discard(self, element: str) -> None:
        super().discard(element)
        self._notify()

    def clear(self) -> None:
        super().clear()
        self._notify()


class MobileCompanionClient:
    """
    Offline-first, end-to-end encrypted Mobile Companion Client.
    """

    def __init__(
        self,
        data_dir: Path | str,
        device_name: str = "LifeOS Mobile Companion",
        os_info: str = "MobileOS",
        api_key: str | None = None,
        transport: CompanionTransport | None = None,
        storage_secret: str | None = None,
        chunk_threshold_bytes: int = 8 * 1024 * 1024,
        chunk_size_bytes: int = 2 * 1024 * 1024,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keys_path = self.data_dir / "mobile_device_keys.json"
        self.db_path = self.data_dir / "mobile_companion.db"
        self.api_key = api_key
        self.transport: CompanionTransport = transport or UrllibTransport()
        self.chunk_threshold_bytes = chunk_threshold_bytes
        self.chunk_size_bytes = chunk_size_bytes
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

        self.store = MobileCompanionStore(db_path=self.db_path, storage_secret=storage_secret)
        self.identity = generate_mobile_identity(
            keys_path=self.keys_path,
            device_name=device_name,
            os_info=os_info,
        )
        self.store.save_mobile_identity(self.identity)
        self.last_error: str | None = None
        self.wiped: bool = False
        self._local_only_workspaces: _PersistentWorkspaceSet = _PersistentWorkspaceSet(
            self.store.load_local_only_workspaces(),
            on_change=self.store.save_local_only_workspaces,
        )
        self._lamport_counter = 0

    @property
    def local_only_workspaces(self) -> set[str]:
        return self._local_only_workspaces

    @local_only_workspaces.setter
    def local_only_workspaces(self, value: set[str]) -> None:
        ws_set = set(value)
        self.store.save_local_only_workspaces(ws_set)
        self._local_only_workspaces = _PersistentWorkspaceSet(
            ws_set,
            on_change=self.store.save_local_only_workspaces,
        )

    def mark_workspace_local_only(self, workspace_id: str) -> None:
        """Persist a local_only workspace boundary across mobile app restarts."""
        self._local_only_workspaces.add(workspace_id)

    def _auth_headers(self, override_token: str | None = None) -> dict[str, str]:
        if override_token:
            if override_token.startswith("devtok_"):
                return {"X-Device-Token": override_token}
            return {"Authorization": f"Bearer {override_token}"}
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        desktop = self.store.get_trusted_desktop()
        dev_tok = desktop.get("device_token") if desktop else None
        if dev_tok:
            headers["X-Device-Token"] = str(dev_tok)
        return headers

    def _desktop_base_url(self, desktop: dict[str, Any]) -> str:
        host = desktop["host"]
        port = int(desktop["port"])
        return f"http://{host}:{port}"

    @staticmethod
    def compute_backoff_seconds(
        retry_count: int,
        base_seconds: float = 1.0,
        max_seconds: float = 60.0,
    ) -> float:
        return min(max_seconds, base_seconds * (2 ** max(0, retry_count)))

    # ------------------------------------------------------------------
    # QR Pairing Consumer
    # ------------------------------------------------------------------

    def consume_qr_payload(self, qr_data: dict[str, Any] | str) -> bool:
        """
        Parse QR pairing payload, enforce local TTL expiration, pin desktop public keys,
        sign `challenge_id:challenge_code` with mobile Ed25519 key, and submit
        `/api/device/pairing/verify`.
        """
        if self.wiped:
            self.last_error = "Client has been wiped; re-initialize before pairing."
            return False

        if isinstance(qr_data, str):
            try:
                payload = json.loads(qr_data)
            except Exception as exc:
                raise ValueError(f"Malformed QR JSON payload: {exc}") from exc
        elif isinstance(qr_data, dict):
            payload = dict(qr_data)
        else:
            raise ValueError("qr_data must be a dict or JSON string.")

        required = (
            "challenge_id",
            "challenge_code",
            "expires_at",
            "host",
            "port",
            "desktop_device_id",
            "signing_public_key",
            "exchange_public_key",
        )
        missing = [k for k in required if not payload.get(k)]
        if missing:
            self.last_error = f"Missing required QR fields: {', '.join(missing)}"
            return False

        # Local TTL expiration pre-check
        try:
            exp_dt = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                self.last_error = "QR pairing challenge has expired."
                return False
        except Exception:
            self.last_error = "Invalid expires_at timestamp in QR payload."
            return False

        challenge_id = str(payload["challenge_id"])
        challenge_code = str(payload["challenge_code"]).strip()
        signed_bytes = f"{challenge_id}:{challenge_code}".encode("utf-8")
        signature_b64 = self.identity.sign(signed_bytes)

        base_url = f"http://{payload['host']}:{int(payload['port'])}"
        verify_body = {
            "challenge_id": challenge_id,
            "challenge_code": challenge_code,
            "peer_device_id": self.identity.device_id,
            "peer_device_name": self.identity.device_name,
            "peer_device_type": "mobile",
            "peer_signing_public_key": self.identity.signing_public_key_b64,
            "peer_exchange_public_key": self.identity.exchange_public_key_b64,
            "signature": signature_b64,
        }

        pairing_tok = payload.get("pairing_token")
        headers = self._auth_headers(override_token=self.api_key or pairing_tok)

        try:
            status_code, resp_data = self.transport.request(
                "POST",
                base_url,
                "/api/device/pairing/verify",
                json_body=verify_body,
                headers=headers,
            )
        except TransportOfflineError as exc:
            self.last_error = str(exc)
            return False

        if status_code != 200 or not resp_data.get("success"):
            self.last_error = str(resp_data.get("detail") or resp_data.get("message") or f"HTTP {status_code}")
            return False

        self.store.pin_trusted_desktop(
            desktop_device_id=str(payload["desktop_device_id"]),
            host=str(payload["host"]),
            port=int(payload["port"]),
            signing_public_key=str(payload["signing_public_key"]),
            exchange_public_key=str(payload["exchange_public_key"]),
            metadata={"paired_via": "qr", "challenge_id": challenge_id},
            device_token=resp_data.get("device_token"),
        )
        self.last_error = None
        return True

    # ------------------------------------------------------------------
    # Offline Mutation Outbox
    # ------------------------------------------------------------------

    def _next_vector_clock(self, workspace_id: str, entity_key: str) -> tuple[dict[str, int], int]:
        self._lamport_counter += 1
        existing = self.store.get_cached_context(workspace_id, entity_key)
        vc = VectorClock.from_dict(existing["vector_clock"] if existing else {})
        vc = vc.increment(self.identity.device_id, 1)
        return vc.to_dict(), self._lamport_counter

    def queue_context_update(
        self,
        key: str,
        value: Any,
        workspace_id: str = "default",
        context_type: str = "general",
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        if self.wiped:
            raise PermissionError("Mobile companion has been wiped.")
        if workspace_id in self.local_only_workspaces:
            raise PermissionError(f"Workspace '{workspace_id}' is local_only; mobile outbox sync forbidden.")

        ts = updated_at or datetime.now(timezone.utc).isoformat()
        clock, lamport = self._next_vector_clock(workspace_id, key)
        self.store.upsert_cached_context(
            workspace_id=workspace_id,
            key=key,
            value=value,
            context_type=context_type,
            vector_clock=clock,
            updated_at=ts,
            is_tombstone=False,
        )
        data = {"key": key, "value": value, "context_type": context_type, "updated_at": ts}
        return self.store.enqueue_mutation(
            mutation_id=f"mut_{uuid.uuid4().hex[:12]}",
            entity_type=SyncEntityType.CONTEXT.value,
            entity_id=key,
            workspace_id=workspace_id,
            change_type=SyncChangeType.UPSERT.value,
            data=data,
            vector_clock=clock,
            lamport_clock=lamport,
        )

    def queue_context_delete(self, key: str, workspace_id: str = "default") -> dict[str, Any]:
        if self.wiped:
            raise PermissionError("Mobile companion has been wiped.")
        if workspace_id in self.local_only_workspaces:
            raise PermissionError(f"Workspace '{workspace_id}' is local_only; mobile outbox sync forbidden.")

        ts = datetime.now(timezone.utc).isoformat()
        clock, lamport = self._next_vector_clock(workspace_id, key)
        self.store.upsert_cached_context(
            workspace_id=workspace_id,
            key=key,
            value=None,
            vector_clock=clock,
            updated_at=ts,
            is_tombstone=True,
        )
        return self.store.enqueue_mutation(
            mutation_id=f"tmb_{uuid.uuid4().hex[:12]}",
            entity_type=SyncEntityType.CONTEXT.value,
            entity_id=key,
            workspace_id=workspace_id,
            change_type=SyncChangeType.DELETE.value,
            data={"deleted": True, "entity_id": key, "updated_at": ts},
            vector_clock=clock,
            lamport_clock=lamport,
        )

    def queue_document_meta(
        self,
        entity_id: str,
        filename: str,
        file_hash: str,
        workspace_id: str = "default",
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.wiped:
            raise PermissionError("Mobile companion has been wiped.")
        if workspace_id in self.local_only_workspaces:
            raise PermissionError(f"Workspace '{workspace_id}' is local_only; mobile outbox sync forbidden.")

        ts = datetime.now(timezone.utc).isoformat()
        self._lamport_counter += 1
        clock = VectorClock().increment(self.identity.device_id, 1).to_dict()
        meta = {
            "filename": filename,
            "file_hash": file_hash,
            "updated_at": ts,
            **(extra_metadata or {}),
        }
        self.store.upsert_cached_document(
            workspace_id=workspace_id,
            entity_id=entity_id,
            filename=filename,
            file_hash=file_hash,
            metadata=meta,
            updated_at=ts,
        )
        return self.store.enqueue_mutation(
            mutation_id=f"doc_{uuid.uuid4().hex[:12]}",
            entity_type=SyncEntityType.DOCUMENT_META.value,
            entity_id=entity_id,
            workspace_id=workspace_id,
            change_type=SyncChangeType.UPSERT.value,
            data=meta,
            vector_clock=clock,
            lamport_clock=self._lamport_counter,
        )

    # ------------------------------------------------------------------
    # Encrypted Sync (Direct + Chunked + Resumable + Outbox Backoff)
    # ------------------------------------------------------------------

    def _record_outbox_backoff(self, mutations: list[dict[str, Any]], error_msg: str) -> None:
        now = datetime.now(timezone.utc)
        for m in mutations:
            delay = self.compute_backoff_seconds(
                retry_count=int(m.get("retry_count", 0)),
                base_seconds=self.base_backoff_seconds,
                max_seconds=self.max_backoff_seconds,
            )
            next_at = (now + timedelta(seconds=delay)).isoformat()
            self.store.record_mutation_failure(m["mutation_id"], error=error_msg, next_retry_at=next_at)

    def _push_chunked_envelope(
        self,
        base_url: str,
        envelope: CryptographicEnvelope,
        desktop_device_id: str,
        transfer_id: str | None = None,
        mutation_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        env_json = envelope.to_json()
        raw_bytes = env_json.encode("utf-8")
        total_sha = hashlib.sha256(raw_bytes).hexdigest()
        chunks = [
            raw_bytes[i : i + self.chunk_size_bytes]
            for i in range(0, max(1, len(raw_bytes)), self.chunk_size_bytes)
        ]
        total_chunks = len(chunks)
        xfer_id = transfer_id or f"mxfer_{uuid.uuid4().hex[:12]}"

        # Check server for already received chunks (resume)
        already_received: set[int] = set()
        if transfer_id:
            st_code, st_body = self.transport.request(
                "GET",
                base_url,
                f"/api/sync/import/chunked/{xfer_id}",
                headers=self._auth_headers(),
            )
            if st_code == 200:
                already_received = {int(i) for i in st_body.get("received_indices", [])}

        self.store.update_sync_state(
            desktop_device_id=desktop_device_id,
            workspace_id="*",
            pending_transfer={
                "direction": "push",
                "transfer_id": xfer_id,
                "total_chunks": total_chunks,
                "total_sha256": total_sha,
                "envelope_json": env_json,
                "mutation_ids": mutation_ids or [],
            },
        )

        last_resp: dict[str, Any] = {}
        for idx, chunk_bytes in enumerate(chunks):
            if idx in already_received:
                continue
            chunk_sha = hashlib.sha256(chunk_bytes).hexdigest()
            payload_b64 = base64.b64encode(chunk_bytes).decode("ascii")
            status_code, resp_data = self.transport.request(
                "POST",
                base_url,
                "/api/sync/import/chunked",
                json_body={
                    "transfer_id": xfer_id,
                    "chunk_index": idx,
                    "total_chunks": total_chunks,
                    "payload_b64": payload_b64,
                    "chunk_sha256": chunk_sha,
                    "total_sha256": total_sha,
                    "peer_device_id": self.identity.device_id,
                },
                headers=self._auth_headers(),
            )
            err_detail = (
                resp_data.get("detail")
                or (resp_data.get("error") or {}).get("message")
                or "Chunk upload failed"
            )
            if status_code == 403:
                raise PermissionError(str(err_detail))
            if status_code != 200:
                raise TransportHttpError(status_code, str(err_detail))
            last_resp = resp_data

        # Clear pending transfer checkpoint once complete
        self.store.update_sync_state(
            desktop_device_id=desktop_device_id,
            workspace_id="*",
            pending_transfer=None,
        )
        return last_resp

    def _pull_chunked_envelope(
        self,
        base_url: str,
        desktop_device_id: str,
        workspace_id: str | None = None,
        resume_transfer_id: str | None = None,
        resume_total_chunks: int | None = None,
        resume_total_sha: str | None = None,
    ) -> CryptographicEnvelope:
        if resume_transfer_id and resume_total_chunks:
            xfer_id = resume_transfer_id
            total_chunks = resume_total_chunks
            total_sha = resume_total_sha or ""
        else:
            status_code, init_data = self.transport.request(
                "POST",
                base_url,
                "/api/sync/export/chunked",
                json_body={
                    "peer_device_id": self.identity.device_id,
                    "workspace_id": workspace_id,
                },
                headers=self._auth_headers(),
            )
            if status_code == 403:
                raise PermissionError(init_data.get("detail", "Peer revoked or workspace forbidden"))
            if status_code != 200:
                raise TransportHttpError(status_code, str(init_data.get("detail", "Chunked export failed")))
            xfer_id = init_data["transfer_id"]
            total_chunks = int(init_data["total_chunks"])
            total_sha = str(init_data["total_sha256"])

        state = self.store.get_sync_state(desktop_device_id, "*")
        cached_parts: dict[str, str] = {}
        if state and state.get("pending_transfer") and state["pending_transfer"].get("transfer_id") == xfer_id:
            cached_parts = dict(state["pending_transfer"].get("received_chunks", {}))

        for idx in range(total_chunks):
            if str(idx) in cached_parts:
                continue
            sc, ch_data = self.transport.request(
                "GET",
                base_url,
                f"/api/sync/export/chunked/{xfer_id}/{idx}",
                headers=self._auth_headers(),
            )
            if sc != 200:
                self.store.update_sync_state(
                    desktop_device_id=desktop_device_id,
                    workspace_id="*",
                    pending_transfer={
                        "direction": "pull",
                        "transfer_id": xfer_id,
                        "total_chunks": total_chunks,
                        "total_sha256": total_sha,
                        "received_chunks": cached_parts,
                    },
                )
                raise TransportHttpError(sc, str(ch_data.get("detail", f"Failed fetching chunk {idx}")))

            chunk_bytes = base64.b64decode(ch_data["payload_b64"].encode("ascii"))
            actual_sha = hashlib.sha256(chunk_bytes).hexdigest()
            if actual_sha != ch_data["chunk_sha256"]:
                raise ValueError(f"Chunk {idx} SHA-256 verification failed during pull.")
            cached_parts[str(idx)] = ch_data["payload_b64"]
            self.store.update_sync_state(
                desktop_device_id=desktop_device_id,
                workspace_id="*",
                pending_transfer={
                    "direction": "pull",
                    "transfer_id": xfer_id,
                    "total_chunks": total_chunks,
                    "total_sha256": total_sha,
                    "received_chunks": cached_parts,
                },
            )

        full_bytes = b"".join(base64.b64decode(cached_parts[str(i)].encode("ascii")) for i in range(total_chunks))
        if total_sha and hashlib.sha256(full_bytes).hexdigest() != total_sha:
            self.store.update_sync_state(
                desktop_device_id=desktop_device_id,
                workspace_id="*",
                pending_transfer=None,
            )
            raise ValueError("Reassembled pull package SHA-256 checksum mismatch.")

        self.store.update_sync_state(
            desktop_device_id=desktop_device_id,
            workspace_id="*",
            pending_transfer=None,
        )
        return CryptographicEnvelope.from_json(full_bytes.decode("utf-8"))

    def _apply_remote_batch(
        self,
        batch: SyncBatch,
        desktop_device_id: str,
        consent_checker: Callable[[SyncItem], bool] | None = None,
    ) -> tuple[int, int, int]:
        if self.store.is_batch_processed(batch.batch_id):
            return 0, len(batch.items), 0

        imported = 0
        skipped = 0
        conflicts_resolved = 0

        for item in batch.items:
            if item.workspace_id in self.local_only_workspaces:
                skipped += 1
                continue

            if (
                item.entity_type == SyncEntityType.WORKSPACE
                and (
                    item.data.get("profile_type") in ("confidential", "local_only")
                    or bool((item.data.get("settings") or {}).get("local_only", False))
                    or bool((item.data.get("settings") or {}).get("is_local_only", False))
                )
            ):
                self.local_only_workspaces.add(item.workspace_id)
                skipped += 1
                continue

            if item.entity_type in (SyncEntityType.DOCUMENT_META, SyncEntityType.DOCUMENT_CHUNK):
                if consent_checker is not None and not consent_checker(item):
                    skipped += 1
                    continue
                self.store.upsert_cached_document(
                    workspace_id=item.workspace_id,
                    entity_id=item.entity_id,
                    filename=item.data.get("filename") or Path(item.entity_id).name,
                    file_hash=item.data.get("file_hash", ""),
                    metadata=item.data,
                    updated_at=item.timestamp,
                    is_tombstone=item.is_tombstone,
                )
                imported += 1
                continue

            if item.entity_type == SyncEntityType.CONTEXT:
                existing = self.store.get_cached_context(item.workspace_id, item.entity_id)
                remote_clock = VectorClock.from_dict(item.vector_clock)
                if existing is None:
                    self.store.upsert_cached_context(
                        workspace_id=item.workspace_id,
                        key=item.entity_id,
                        value=item.data.get("value"),
                        context_type=item.data.get("context_type", "general"),
                        vector_clock=remote_clock.to_dict(),
                        updated_at=item.data.get("updated_at") or item.timestamp,
                        is_tombstone=item.is_tombstone,
                    )
                    imported += 1
                else:
                    local_clock = VectorClock.from_dict(existing["vector_clock"])
                    rel = local_clock.compare(remote_clock)
                    if rel == ClockRelationship.ANCESTOR:
                        self.store.upsert_cached_context(
                            workspace_id=item.workspace_id,
                            key=item.entity_id,
                            value=item.data.get("value"),
                            context_type=item.data.get("context_type", "general"),
                            vector_clock=remote_clock.to_dict(),
                            updated_at=item.data.get("updated_at") or item.timestamp,
                            is_tombstone=item.is_tombstone,
                        )
                        imported += 1
                    elif rel in (ClockRelationship.DESCENDANT, ClockRelationship.EQUAL):
                        skipped += 1
                    elif rel == ClockRelationship.CONCURRENT:
                        # LWW with deterministic device ID tie-break
                        local_ts = existing.get("updated_at") or ""
                        remote_ts = item.data.get("updated_at") or item.timestamp or ""
                        if remote_ts > local_ts or (remote_ts == local_ts and desktop_device_id > self.identity.device_id):
                            winner_val = item.data.get("value")
                            winner_ts = remote_ts
                            winner_tomb = item.is_tombstone
                        else:
                            winner_val = existing.get("value")
                            winner_ts = local_ts
                            winner_tomb = existing.get("is_tombstone", False)
                        merged_clock = local_clock.merge(remote_clock).to_dict()
                        self.store.upsert_cached_context(
                            workspace_id=item.workspace_id,
                            key=item.entity_id,
                            value=winner_val,
                            context_type=item.data.get("context_type", existing.get("context_type", "general")),
                            vector_clock=merged_clock,
                            updated_at=winner_ts,
                            is_tombstone=winner_tomb,
                        )
                        conflicts_resolved += 1
                        imported += 1

        self.store.mark_batch_processed(batch.batch_id, sender_device_id=desktop_device_id)
        return imported, skipped, conflicts_resolved

    def sync_with_desktop(
        self,
        workspace_id: str | None = None,
        use_chunked_pull: bool = False,
        force_retry: bool = False,
        ignore_backoff: bool = False,
        consent_checker: Callable[[SyncItem], bool] | None = None,
    ) -> SyncResult:
        """
        Execute bidirectional encrypted sync with the pinned trusted desktop host:
        1. Flush due outbox mutations sealed in CryptographicEnvelope (chunked if > threshold).
        2. Pull latest desktop batch (direct or chunked), verify & decrypt, and apply updates.
        """
        if self.wiped:
            raise PermissionError("Mobile companion has been wiped.")

        desktop = self.store.get_trusted_desktop()
        if not desktop:
            raise PermissionError("No paired desktop host found in mobile trust store.")
        if desktop.get("trust_status") != "paired":
            raise PermissionError(f"Desktop trust status is '{desktop.get('trust_status')}'; sync aborted.")

        base_url = self._desktop_base_url(desktop)
        desktop_id = desktop["desktop_device_id"]
        due_mutations = self.store.get_pending_mutations(ignore_backoff=(force_retry or ignore_backoff))
        pushed_count = 0
        chunked_used = False

        # 1. Push pending outbox mutations
        if due_mutations:
            mutation_ids = [m["mutation_id"] for m in due_mutations]
            state = self.store.get_sync_state(desktop_id, "*")
            pending_xfer = (state or {}).get("pending_transfer") or {}
            resume_id: str | None = None
            if (
                pending_xfer.get("direction") == "push"
                and pending_xfer.get("envelope_json")
                and pending_xfer.get("mutation_ids") == mutation_ids
            ):
                envelope = CryptographicEnvelope.from_json(pending_xfer["envelope_json"])
                resume_id = pending_xfer.get("transfer_id")
            else:
                sync_items: list[SyncItem] = []
                for m in due_mutations:
                    sync_items.append(
                        SyncItem(
                            item_id=m["mutation_id"],
                            entity_type=SyncEntityType(m["entity_type"]),
                            entity_id=m["entity_id"],
                            workspace_id=m["workspace_id"],
                            change_type=SyncChangeType(m["change_type"]),
                            vector_clock=m["vector_clock"],
                            lamport_clock=m["lamport_clock"],
                            origin_device_id=self.identity.device_id,
                            data=m["data"],
                            timestamp=m["updated_at"],
                            is_tombstone=(m["change_type"] == SyncChangeType.DELETE.value),
                        )
                    )
                manifest: dict[str, int] = {}
                for it in sync_items:
                    manifest[it.entity_type.value] = manifest.get(it.entity_type.value, 0) + 1

                push_batch = SyncBatch(
                    batch_id=f"mbat_{uuid.uuid4().hex[:12]}",
                    sender_device_id=self.identity.device_id,
                    recipient_device_id=desktop_id,
                    items=sync_items,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    manifest=manifest,
                )
                envelope = seal_payload(
                    payload=push_batch.model_dump_json().encode("utf-8"),
                    recipient_exchange_public_key_b64=desktop["exchange_public_key"],
                    recipient_device_id=desktop_id,
                    sender_identity=self.identity,
                )

            try:
                env_bytes = envelope.to_json().encode("utf-8")
                if len(env_bytes) > self.chunk_threshold_bytes:
                    chunked_used = True
                    self._push_chunked_envelope(
                        base_url,
                        envelope,
                        desktop_id,
                        transfer_id=resume_id,
                        mutation_ids=mutation_ids,
                    )
                else:
                    sc, resp = self.transport.request(
                        "POST",
                        base_url,
                        "/api/sync/import",
                        json_body={"envelope": envelope.to_dict()},
                        headers=self._auth_headers(),
                    )
                    if sc == 403:
                        self.store.revoke_trusted_desktop(desktop_id)
                        raise PermissionError(str(resp.get("detail", "Desktop rejected sync: peer revoked")))
                    if sc != 200:
                        raise TransportHttpError(sc, str(resp.get("detail", "Sync import rejected")))

                for m in due_mutations:
                    self.store.mark_mutation_sent(m["mutation_id"])
                pushed_count = len(due_mutations)

            except PermissionError:
                raise
            except (TransportOfflineError, TransportHttpError) as exc:
                self._record_outbox_backoff(due_mutations, str(exc))
                return SyncResult(
                    success=False,
                    offline=isinstance(exc, TransportOfflineError),
                    pushed_count=0,
                    chunked_used=chunked_used,
                    error=str(exc),
                )

        # 2. Pull updates from desktop
        try:
            state = self.store.get_sync_state(desktop_id, "*")
            pending_pull = (state or {}).get("pending_transfer") or {}
            if use_chunked_pull or pending_pull.get("direction") == "pull":
                chunked_used = True
                envelope = self._pull_chunked_envelope(
                    base_url=base_url,
                    desktop_device_id=desktop_id,
                    workspace_id=workspace_id,
                    resume_transfer_id=pending_pull.get("transfer_id") if pending_pull.get("direction") == "pull" else None,
                    resume_total_chunks=pending_pull.get("total_chunks") if pending_pull.get("direction") == "pull" else None,
                    resume_total_sha=pending_pull.get("total_sha256") if pending_pull.get("direction") == "pull" else None,
                )
            else:
                sc, resp = self.transport.request(
                    "POST",
                    base_url,
                    "/api/sync/export",
                    json_body={"peer_device_id": self.identity.device_id, "workspace_id": workspace_id},
                    headers=self._auth_headers(),
                )
                if sc == 403:
                    self.store.revoke_trusted_desktop(desktop_id)
                    raise PermissionError(str(resp.get("detail", "Peer trust revoked on desktop")))
                if sc != 200:
                    raise TransportHttpError(sc, str(resp.get("detail", "Sync export failed")))
                envelope = CryptographicEnvelope.from_dict(resp["envelope"])

            if envelope.sender_device_id != desktop_id:
                raise PermissionError(
                    f"Envelope sender '{envelope.sender_device_id}' does not match pinned desktop '{desktop_id}'."
                )

            plaintext = open_payload(
                envelope=envelope,
                recipient_identity=self.identity,
                sender_signing_public_key_b64=desktop["signing_public_key"],
            )
            batch = SyncBatch.model_validate_json(plaintext.decode("utf-8"))
            if batch.recipient_device_id != self.identity.device_id:
                raise ValueError("Batch recipient does not match this mobile device.")

            imported, skipped, conflicts_resolved = self._apply_remote_batch(
                batch=batch,
                desktop_device_id=desktop_id,
                consent_checker=consent_checker,
            )
            self.store.update_sync_state(
                desktop_device_id=desktop_id,
                workspace_id="*",
                vector_clock={},
                local_sequence=imported,
            )
            return SyncResult(
                success=True,
                offline=False,
                pushed_count=pushed_count,
                pulled_imported=imported,
                pulled_skipped=skipped,
                conflicts_resolved=conflicts_resolved,
                batch_id=batch.batch_id,
                chunked_used=chunked_used,
            )
        except PermissionError:
            raise
        except (TransportOfflineError, TransportHttpError, ValueError) as exc:
            return SyncResult(
                success=False,
                offline=isinstance(exc, TransportOfflineError),
                pushed_count=pushed_count,
                chunked_used=chunked_used,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Remote Wipe & Event Handling
    # ------------------------------------------------------------------

    def handle_remote_wipe(
        self,
        wipe_scope: str = "all",
        target_value: str | None = None,
    ) -> dict[str, Any]:
        """
        Zeroize local mobile companion storage and cryptographic key material
        upon remote wipe directive or device revocation.
        """
        if wipe_scope == "all":
            table_counts = self.store.wipe_all()
            if self.keys_path.is_file():
                try:
                    size = self.keys_path.stat().st_size
                    self.keys_path.write_bytes(b"\x00" * max(size, 128))
                    self.keys_path.unlink()
                except Exception:
                    pass
            self.wiped = True
            super(_PersistentWorkspaceSet, self._local_only_workspaces).clear()
            return {"wiped": True, "scope": "all", "purged_tables": table_counts}

        if wipe_scope == "workspace" and target_value:
            removed = self.store.wipe_by_path_or_workspace(workspace_id=target_value)
            return {"wiped": True, "scope": "workspace", "removed_rows": removed}

        if wipe_scope == "path" and target_value:
            removed = self.store.wipe_by_path_or_workspace(path_prefix=target_value)
            return {"wiped": True, "scope": "path", "removed_rows": removed}

        return {"wiped": False, "scope": wipe_scope, "removed_rows": 0}

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Handle real-time SSE/WebSocket events pushed from desktop host."""
        evt_type = event.get("event_type", "")
        payload = event.get("payload") or {}

        if evt_type == "device.revoked":
            target_dev = payload.get("device_id")
            if not target_dev or target_dev == self.identity.device_id:
                return self.handle_remote_wipe(wipe_scope="all")
        elif evt_type == "privacy.remote_wipe":
            target_dev = payload.get("device_id")
            if not target_dev or target_dev == self.identity.device_id:
                return self.handle_remote_wipe(
                    wipe_scope=payload.get("wipe_scope", "all"),
                    target_value=payload.get("target_value"),
                )
        elif evt_type == "privacy.consent_changed" and payload.get("action") == "revoked":
            res_path = payload.get("resource_path")
            if res_path:
                return self.handle_remote_wipe(wipe_scope="path", target_value=res_path)

        return {"handled": evt_type}


# ======================================================================
# Platform Simulation Adapters (Android & iOS)
# ======================================================================


@dataclass
class AndroidWorkConstraints:
    network_type: str = "wifi"  # "none" | "cellular" | "wifi" | "unmetered"
    requires_unmetered: bool = False
    doze_mode: bool = False
    battery_saver: bool = False
    charging: bool = True
    battery_percent: float = 85.0
    low_battery_threshold: float = 15.0


class AndroidLifecycleManager:
    """
    Deterministic simulation adapter for Android WorkManager, Doze/App Standby,
    Battery Saver, Wi-Fi/Cellular transitions, and process death recovery.
    """

    def __init__(self, constraints: AndroidWorkConstraints | None = None):
        self.constraints = constraints or AndroidWorkConstraints()
        self.deferred_reasons: list[str] = []
        self.process_alive: bool = True

    def can_execute_work(self, payload_bytes: int = 0) -> tuple[bool, str | None]:
        c = self.constraints
        if not self.process_alive:
            return False, "ANDROID_PROCESS_DEAD"
        if c.network_type == "none":
            return False, "NO_NETWORK"
        if c.doze_mode and not c.charging:
            return False, "ANDROID_DOZE_DEFERRED"
        if c.battery_saver and not c.charging:
            return False, "ANDROID_BATTERY_SAVER_DEFERRED"
        if c.battery_percent < c.low_battery_threshold and not c.charging:
            return False, "ANDROID_LOW_BATTERY_DEFERRED"
        if (c.requires_unmetered or payload_bytes > 10 * 1024 * 1024) and c.network_type == "cellular":
            return False, "ANDROID_UNMETERED_NETWORK_REQUIRED"
        return True, None

    def set_network(self, network_type: str) -> None:
        self.constraints.network_type = network_type

    def run_periodic_sync(
        self,
        client: MobileCompanionClient,
        workspace_id: str | None = None,
        estimated_payload_bytes: int = 0,
        use_chunked_pull: bool = False,
    ) -> tuple[bool, SyncResult | str]:
        allowed, reason = self.can_execute_work(payload_bytes=estimated_payload_bytes)
        client.store.save_lifecycle_checkpoint(
            "android",
            {
                "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                "allowed": allowed,
                "reason": reason,
                "workspace_id": workspace_id,
                "network_type": self.constraints.network_type,
                "pending_work": not allowed,
            },
        )
        if not allowed:
            self.deferred_reasons.append(reason or "DEFERRED")
            return False, reason or "DEFERRED"

        result = client.sync_with_desktop(
            workspace_id=workspace_id,
            use_chunked_pull=use_chunked_pull,
            ignore_backoff=True,
        )
        client.store.save_lifecycle_checkpoint(
            "android",
            {
                "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                "allowed": True,
                "pending_work": not result.success,
                "last_batch_id": result.batch_id,
                "workspace_id": workspace_id,
            },
        )
        return result.success, result

    def simulate_process_death(self, client: MobileCompanionClient) -> None:
        """Simulate OS killing the background Android process mid-session."""
        client.store.save_lifecycle_checkpoint(
            "android",
            {
                "process_killed": True,
                "pending_work": True,
                "killed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.process_alive = False

    def recover_from_process_death(self, client: MobileCompanionClient) -> tuple[bool, SyncResult | str]:
        """Restart worker process, restore checkpoint from mobile_companion.db, and resume."""
        self.process_alive = True
        chk = client.store.load_lifecycle_checkpoint("android") or {}
        if not chk.get("pending_work") and not client.store.get_pending_mutations(ignore_backoff=True):
            return True, "NO_PENDING_WORK"
        return self.run_periodic_sync(client)


@dataclass
class iOSBackgroundConstraints:
    network_available: bool = True
    background_refresh_enabled: bool = True
    low_power_mode: bool = False
    data_protection_locked: bool = False
    task_budget_seconds: float = 30.0


class iOSLifecycleManager:
    """
    Deterministic simulation adapter for iOS BGAppRefreshTask / BGProcessingTask
    time budgets, Low Power Mode, Data Protection lock state, and app termination recovery.
    """

    def __init__(self, constraints: iOSBackgroundConstraints | None = None):
        self.constraints = constraints or iOSBackgroundConstraints()
        self.deferred_reasons: list[str] = []
        self.terminated: bool = False
        self.expiration_handler_invoked: bool = False

    def can_execute_background_task(
        self,
        estimated_duration_s: float = 5.0,
        requires_unprotected_storage: bool = True,
    ) -> tuple[bool, str | None]:
        c = self.constraints
        if self.terminated:
            return False, "IOS_APP_TERMINATED"
        if not c.background_refresh_enabled:
            return False, "IOS_BG_REFRESH_DISABLED"
        if c.low_power_mode:
            return False, "IOS_LOW_POWER_MODE_DEFERRED"
        if c.data_protection_locked and requires_unprotected_storage:
            return False, "IOS_DATA_PROTECTION_LOCKED"
        if not c.network_available:
            return False, "NO_NETWORK"
        if estimated_duration_s > c.task_budget_seconds:
            return False, "IOS_BG_TIME_BUDGET_EXCEEDED"
        return True, None

    def run_bg_refresh(
        self,
        client: MobileCompanionClient,
        workspace_id: str | None = None,
        estimated_duration_s: float = 5.0,
        use_chunked_pull: bool = False,
    ) -> tuple[bool, SyncResult | str]:
        allowed, reason = self.can_execute_background_task(estimated_duration_s=estimated_duration_s)
        if reason == "IOS_BG_TIME_BUDGET_EXCEEDED":
            self.expiration_handler_invoked = True
        client.store.save_lifecycle_checkpoint(
            "ios",
            {
                "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                "allowed": allowed,
                "reason": reason,
                "workspace_id": workspace_id,
                "pending_refresh": not allowed,
            },
        )
        if not allowed:
            self.deferred_reasons.append(reason or "DEFERRED")
            return False, reason or "DEFERRED"

        result = client.sync_with_desktop(
            workspace_id=workspace_id,
            use_chunked_pull=use_chunked_pull,
            ignore_backoff=True,
        )
        client.store.save_lifecycle_checkpoint(
            "ios",
            {
                "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                "allowed": True,
                "pending_refresh": not result.success,
                "last_batch_id": result.batch_id,
                "workspace_id": workspace_id,
            },
        )
        return result.success, result

    def simulate_termination(self, client: MobileCompanionClient) -> None:
        """Simulate iOS terminating the suspended app due to memory pressure."""
        client.store.save_lifecycle_checkpoint(
            "ios",
            {
                "terminated": True,
                "pending_refresh": True,
                "terminated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.terminated = True

    def recover_after_relaunch(self, client: MobileCompanionClient) -> tuple[bool, SyncResult | str]:
        """Simulate app relaunch, unlock, and recovery from SQLite lifecycle checkpoint."""
        self.terminated = False
        self.constraints.data_protection_locked = False
        chk = client.store.load_lifecycle_checkpoint("ios") or {}
        if not chk.get("pending_refresh") and not client.store.get_pending_mutations(ignore_backoff=True):
            return True, "NO_PENDING_REFRESH"
        return self.run_bg_refresh(client)
