"""
connectors package

Extensible Connector Architecture for LifeOS Milestone 2.
"""

from app.connectors.audio import AudioConnector
from app.connectors.base import BaseConnector
from app.connectors.filesystem import FilesystemConnector
from app.connectors.models import (
    ConnectorContent,
    ConnectorItem,
    ConnectorStatus,
    SyncResult,
)
from app.connectors.registry import (
    ConnectorRegistry,
    get_connector,
    get_connector_registry,
    list_connectors,
    register_connector,
    sync_connector,
)
from app.connectors.spreadsheet import SpreadsheetConnector

__all__ = [
    "BaseConnector",
    "ConnectorItem",
    "ConnectorContent",
    "SyncResult",
    "ConnectorStatus",
    "ConnectorRegistry",
    "FilesystemConnector",
    "SpreadsheetConnector",
    "AudioConnector",
    "get_connector_registry",
    "register_connector",
    "get_connector",
    "list_connectors",
    "sync_connector",
]
