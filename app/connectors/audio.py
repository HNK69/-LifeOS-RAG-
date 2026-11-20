"""
audio.py

Audio connector for local recordings, voice memos, and podcasts (.wav, .mp3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, is_authorized_connector_path
from app.connectors.models import ConnectorContent, ConnectorItem

AUDIO_EXTENSIONS = {".wav", ".mp3"}


class AudioConnector(BaseConnector):
    """
    Dedicated connector for voice memos and audio recordings.
    Enforces authorization boundary: rejects arbitrary host filesystem scanning.
    """

    def __init__(self, folder_path: Path | str, name: str = "audio_connector", config: dict[str, Any] | None = None):
        super().__init__(config=config)
        self._folder_path = Path(folder_path).resolve()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def source_type(self) -> str:
        return "audio"

    @property
    def description(self) -> str:
        return f"Audio & Voice Recording Connector at {self._folder_path}"

    def validate_config(self) -> bool:
        if not is_authorized_connector_path(self._folder_path):
            return False
        return self._folder_path.exists() and self._folder_path.is_dir()

    def discover_items(self) -> list[ConnectorItem]:
        if not is_authorized_connector_path(self._folder_path):
            raise PermissionError(f"Access denied: Path '{self._folder_path}' is not within authorized connector roots.")
        if not self._folder_path.exists() or not self._folder_path.is_dir():
            raise FileNotFoundError(f"Folder not found: {self._folder_path}")

        items: list[ConnectorItem] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for p in self._folder_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
                stat = p.stat()
                items.append(
                    ConnectorItem(
                        id=f"audio_{p.name}_{stat.st_mtime_ns}",
                        title=p.name,
                        source_type="audio",
                        uri=str(p),
                        mime_type=f"audio/{p.suffix.lstrip('.')}",
                        size_bytes=stat.st_size,
                        metadata={
                            "extension": p.suffix.lower(),
                            "path": str(p),
                            "format": "audio",
                        },
                        discovered_at=now_iso,
                    )
                )

        return items

    def fetch_content(self, item_id: str) -> ConnectorContent:
        for p in self._folder_path.rglob("*"):
            if p.is_file() and item_id.startswith(f"audio_{p.name}_"):
                return ConnectorContent(
                    item_id=item_id,
                    raw_bytes=p.read_bytes(),
                    metadata={"path": str(p), "extension": p.suffix.lower()},
                )

        raise FileNotFoundError(f"Audio item '{item_id}' not found under {self._folder_path}")
