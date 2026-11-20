"""
filesystem.py

Filesystem connector implementation for local consented directories.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, is_authorized_connector_path
from app.connectors.models import ConnectorContent, ConnectorItem

try:
    from config import SUPPORTED_EXTENSIONS
except ImportError:
    from app.config import SUPPORTED_EXTENSIONS


class FilesystemConnector(BaseConnector):
    """
    Connector for scanning and syncing local filesystem folders.
    Enforces authorization boundary: rejects arbitrary host filesystem scanning.
    """

    def __init__(self, folder_path: Path | str, name: str = "local_filesystem", config: dict[str, Any] | None = None):
        super().__init__(config=config)
        self._folder_path = Path(folder_path).resolve()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def source_type(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return f"Local Filesystem Connector at {self._folder_path}"

    def validate_config(self) -> bool:
        if not is_authorized_connector_path(self._folder_path):
            return False
        return self._folder_path.exists() and self._folder_path.is_dir()

    def discover_items(self) -> list[ConnectorItem]:
        if not is_authorized_connector_path(self._folder_path):
            raise PermissionError(f"Access denied: Path '{self._folder_path}' is not within authorized connector roots.")
        if not self._folder_path.exists() or not self._folder_path.is_dir():
            raise FileNotFoundError(f"Configured folder does not exist: {self._folder_path}")

        items: list[ConnectorItem] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for p in self._folder_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                stat = p.stat()
                items.append(
                    ConnectorItem(
                        id=f"fs_{p.name}_{stat.st_mtime_ns}",
                        title=p.name,
                        source_type="filesystem",
                        uri=str(p),
                        mime_type=f"application/{p.suffix.lstrip('.')}",
                        size_bytes=stat.st_size,
                        metadata={
                            "extension": p.suffix.lower(),
                            "path": str(p),
                            "modified_time_ns": stat.st_mtime_ns,
                        },
                        discovered_at=now_iso,
                    )
                )

        return items

    def fetch_content(self, item_id: str) -> ConnectorContent:
        for p in self._folder_path.rglob("*"):
            if p.is_file() and item_id.startswith(f"fs_{p.name}_"):
                return ConnectorContent(
                    item_id=item_id,
                    raw_bytes=p.read_bytes(),
                    metadata={"path": str(p), "size": p.stat().st_size},
                )

        raise FileNotFoundError(f"Filesystem item '{item_id}' not found under {self._folder_path}")
