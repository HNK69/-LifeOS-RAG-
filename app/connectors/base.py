"""
base.py

Abstract Base Class for the LifeOS Extensible Connector Architecture.
Defines contracts for discovery, content retrieval, validation, and syncing.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.connectors.models import ConnectorContent, ConnectorItem, ConnectorStatus, SyncResult

logger = logging.getLogger("lifeos.connectors.base")

_authorized_roots: set[str] = set()


def authorize_connector_root(root_path: Path | str) -> None:
    """Explicitly authorize a filesystem root for connector scanning."""
    _authorized_roots.add(str(Path(root_path).resolve()))


def revoke_connector_root(root_path: Path | str) -> None:
    """Revoke authorization for a filesystem root."""
    _authorized_roots.discard(str(Path(root_path).resolve()))


def clear_authorized_roots() -> None:
    """Clear all in-process authorized roots (for testing)."""
    _authorized_roots.clear()


def is_authorized_connector_path(candidate_path: Path | str) -> bool:
    """
    Enforce security boundary: verify whether candidate_path resolves within
    config.AUTHORIZED_CONNECTOR_ROOTS or explicitly authorized connector roots.
    Rejects arbitrary host filesystem scanning.
    """
    import os
    cand = Path(candidate_path).resolve()
    cand_norm = Path(os.path.normcase(str(cand)))

    # 1. In-process dynamically authorized roots
    for r in _authorized_roots:
        r_norm = Path(os.path.normcase(str(Path(r).resolve())))
        if cand_norm == r_norm or cand_norm.is_relative_to(r_norm):
            return True

    # 2. Configured roots (defaults to DOCUMENTS_DIR)
    allowed: list[Path] = []
    try:
        import config
        roots = getattr(config, "AUTHORIZED_CONNECTOR_ROOTS", None)
        if roots:
            allowed.extend([Path(r).resolve() for r in roots])
        if hasattr(config, "DOCUMENTS_DIR"):
            allowed.append(Path(config.DOCUMENTS_DIR).resolve())
    except Exception:
        pass

    try:
        import app.config as app_cfg
        roots = getattr(app_cfg, "AUTHORIZED_CONNECTOR_ROOTS", None)
        if roots:
            allowed.extend([Path(r).resolve() for r in roots])
        if hasattr(app_cfg, "DOCUMENTS_DIR"):
            allowed.append(Path(app_cfg.DOCUMENTS_DIR).resolve())
    except Exception:
        pass

    for root in allowed:
        root_norm = Path(os.path.normcase(str(root)))
        if cand_norm == root_norm or cand_norm.is_relative_to(root_norm):
            return True

    # 3. Active granular consent grants from the Privacy & Consent engine
    try:
        from app.security.consent import is_consent_granted
        if is_consent_granted(cand, purpose="indexing"):
            return True
    except Exception:
        pass

    return False


class BaseConnector(ABC):
    """
    Abstract base class for all LifeOS connectors.

    Every external or internal data source (Filesystem, Cloud, Spreadsheets,
    Audio recorders, Notes, Calendar, etc.) implements this interface.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.last_sync_at: str | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier name for this connector instance."""
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Type of source (e.g. 'filesystem', 'spreadsheet', 'audio', 'cloud')."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of the connector."""
        return f"{self.name} ({self.source_type})"

    @abstractmethod
    def validate_config(self) -> bool:
        """Validate credentials, paths, and connectivity."""
        ...

    @abstractmethod
    def discover_items(self) -> list[ConnectorItem]:
        """Discover available resources from the connected source."""
        ...

    @abstractmethod
    def fetch_content(self, item_id: str) -> ConnectorContent:
        """Retrieve the payload (raw bytes or text) for a given item."""
        ...

    def get_status(self) -> ConnectorStatus:
        """Return the current operational status of the connector."""
        is_valid = False
        try:
            is_valid = self.validate_config()
        except Exception:
            is_valid = False

        return ConnectorStatus(
            name=self.name,
            source_type=self.source_type,
            enabled=True,
            configured=is_valid,
            last_sync_at=self.last_sync_at,
            description=self.description,
        )

    def sync(self, destination_dir: Path | None = None) -> SyncResult:
        """
        Synchronize items from this connector into LifeOS.

        Downloads/fetches discovered items and enqueues them for ingestion.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        errors: list[str] = []
        synced_count = 0
        failed_count = 0

        try:
            from config import DOCUMENTS_DIR
            target_dir = Path(destination_dir or DOCUMENTS_DIR).resolve()
        except ImportError:
            from app.config import DOCUMENTS_DIR
            target_dir = Path(destination_dir or DOCUMENTS_DIR).resolve()

        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            items = self.discover_items()
        except Exception as exc:
            logger.error(f"Discovery failed for connector '{self.name}': {exc}", exc_info=True)
            return SyncResult(
                connector_name=self.name,
                source_type=self.source_type,
                status="failed",
                items_discovered=0,
                items_synced=0,
                items_failed=1,
                errors=[str(exc)],
                synced_at=now_iso,
            )

        for item in items:
            try:
                content = self.fetch_content(item.id)
                dest_file = target_dir / Path(item.uri).name

                if content.raw_bytes is not None:
                    dest_file.write_bytes(content.raw_bytes)
                elif content.text_content is not None:
                    dest_file.write_text(content.text_content, encoding="utf-8")
                else:
                    raise ValueError(f"No bytes or text content returned for item '{item.id}'")

                # Enqueue for asynchronous or direct ingestion
                try:
                    from app.ingestion.queue import enqueue_job
                    enqueue_job(dest_file)
                except Exception:
                    from app.ingestion.ingest import ingest_file
                    ingest_file(dest_file)

                synced_count += 1
            except Exception as item_exc:
                err_msg = f"Failed to sync item '{item.id}': {item_exc}"
                logger.error(err_msg)
                errors.append(err_msg)
                failed_count += 1

        self.last_sync_at = now_iso
        overall_status = "success" if failed_count == 0 else ("partial" if synced_count > 0 else "failed")

        return SyncResult(
            connector_name=self.name,
            source_type=self.source_type,
            status=overall_status,
            items_discovered=len(items),
            items_synced=synced_count,
            items_failed=failed_count,
            errors=errors,
            synced_at=now_iso,
        )
