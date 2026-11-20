"""
registry.py

Central registry for managing LifeOS connectors.
Provides registration, lookup, enumeration, and orchestrated synchronization.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.connectors.base import BaseConnector
from app.connectors.models import ConnectorStatus, SyncResult

logger = logging.getLogger("lifeos.connectors.registry")


class ConnectorRegistry:
    """
    Thread-safe registry for LifeOS data source connectors.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._connectors: dict[str, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> None:
        """Register a connector instance."""
        with self._lock:
            name = connector.name
            if name in self._connectors:
                logger.warning(f"Overwriting existing connector registration for '{name}'")
            self._connectors[name] = connector
            logger.info(f"Registered connector '{name}' ({connector.source_type})")

    def unregister(self, name: str) -> bool:
        """Unregister a connector by name."""
        with self._lock:
            if name in self._connectors:
                del self._connectors[name]
                logger.info(f"Unregistered connector '{name}'")
                return True
            return False

    def get(self, name: str) -> BaseConnector | None:
        """Lookup a connector by name."""
        with self._lock:
            return self._connectors.get(name)

    def list_connectors(self) -> list[ConnectorStatus]:
        """Return status for all registered connectors."""
        with self._lock:
            connectors = list(self._connectors.values())
        return [c.get_status() for c in connectors]

    def sync_connector(self, name: str) -> SyncResult:
        """Trigger synchronization for a specific connector."""
        connector = self.get(name)
        if connector is None:
            raise KeyError(f"Connector '{name}' is not registered.")
        return connector.sync()

    def sync_all(self) -> dict[str, SyncResult]:
        """Trigger synchronization across all registered connectors."""
        with self._lock:
            connectors = list(self._connectors.values())

        results: dict[str, SyncResult] = {}
        for c in connectors:
            try:
                results[c.name] = c.sync()
            except Exception as exc:
                logger.error(f"Sync failed for connector '{c.name}': {exc}", exc_info=True)
        return results

    def clear(self) -> None:
        """Clear all registered connectors (useful for test isolation)."""
        with self._lock:
            self._connectors.clear()


# Global Singleton
_global_registry = ConnectorRegistry()


def get_connector_registry() -> ConnectorRegistry:
    return _global_registry


def register_connector(connector: BaseConnector) -> None:
    _global_registry.register(connector)


def get_connector(name: str) -> BaseConnector | None:
    return _global_registry.get(name)


def list_connectors() -> list[ConnectorStatus]:
    return _global_registry.list_connectors()


def sync_connector(name: str) -> SyncResult:
    return _global_registry.sync_connector(name)
