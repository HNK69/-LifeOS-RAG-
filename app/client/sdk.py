"""
sdk.py

Typed Python Client SDK for the LifeOS Personal AI Operating System.
Provides programmatic access to queries, document ingestion, device identity,
privacy dashboard controls, and real-time SSE/WebSocket event streams.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("lifeos.client.sdk")


class LifeOSClientError(Exception):
    """Raised when an API request to LifeOS fails."""
    def __init__(self, message: str, status_code: int = 500, response_body: str = ""):
        super().__init__(f"[{status_code}] {message}: {response_body}")
        self.status_code = status_code
        self.response_body = response_body


class LifeOSClient:
    """
    Lightweight, dependency-free Python client SDK for LifeOS HTTP and Event APIs.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request and parse JSON response."""
        url = f"{self.base_url}{path}"
        if params:
            query_str = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if query_str:
                url = f"{url}?{query_str}"

        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(url, data=data_bytes, headers=self._headers(), method=method)

        try:
            with urlopen(req, timeout=30.0) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as err:
            err_body = err.read().decode("utf-8")
            raise LifeOSClientError(err.reason, status_code=err.code, response_body=err_body) from err
        except Exception as exc:
            raise LifeOSClientError(str(exc)) from exc

    # =========================================================================
    # System & Health
    # =========================================================================

    def get_health(self) -> dict[str, Any]:
        """Retrieve system health and liveness status."""
        return self._request("GET", "/health")

    def get_device_identity(self) -> dict[str, Any]:
        """Retrieve local device public identity and key metadata."""
        return self._request("GET", "/api/device/identity")

    # =========================================================================
    # Query & Intelligence
    # =========================================================================

    def query(self, query_text: str) -> dict[str, Any]:
        """Submit a natural-language query to the LifeOS Intelligence Core."""
        return self._request("POST", "/api/query", payload={"query": query_text})

    # =========================================================================
    # Ingestion & Documents
    # =========================================================================

    def ingest_document_async(self, file_path: str | Path, priority: int = 0) -> dict[str, Any]:
        """Enqueue a document for background asynchronous ingestion."""
        return self._request(
            "POST",
            "/api/documents",
            payload={"path": str(Path(file_path).resolve())},
            params={"async": "true"},
        )

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Check status and retry progress of an ingestion queue job."""
        return self._request("GET", f"/api/documents/jobs/{job_id}")

    # =========================================================================
    # Privacy & Consent
    # =========================================================================

    def get_privacy_summary(self) -> dict[str, Any]:
        """Retrieve privacy posture, consented locations count, and audit chain integrity."""
        return self._request("GET", "/api/privacy/summary")

    def list_consents(self, status: str | None = None) -> list[dict[str, Any]]:
        """List active or revoked granular consent grants."""
        params = {"status": status} if status else None
        res = self._request("GET", "/api/privacy/consent", params=params)
        return res if isinstance(res, list) else []

    def grant_consent(
        self,
        resource_path: str,
        scope_type: str = "folder",
        categories: list[str] | None = None,
        purposes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Grant explicit consent to access and index a filesystem path or source."""
        payload: dict[str, Any] = {
            "resource_path": str(Path(resource_path).resolve()),
            "scope_type": scope_type,
        }
        if categories:
            payload["categories"] = categories
        if purposes:
            payload["purposes"] = purposes
        return self._request("POST", "/api/privacy/consent", payload=payload)

    def revoke_consent(self, consent_id: str, reason: str = "User revoked via SDK") -> dict[str, Any]:
        """Immediately revoke an active consent grant."""
        return self._request("DELETE", f"/api/privacy/consent/{consent_id}", payload={"reason": reason})

    def verify_audit_log(self) -> dict[str, Any]:
        """Verify the cryptographic SHA-256 hash chain of the privacy audit log."""
        return self._request("GET", "/api/privacy/audit/verify")
