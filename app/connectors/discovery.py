"""
discovery.py

Consent-First Source Discovery Daemon for LifeOS Milestone C2.
Scans configured candidate top-level directories (e.g. ~/Documents, ~/Downloads)
to detect potential personal knowledge folders that have not yet been authorized.

Strict Security Boundaries:
- NEVER crawls directory trees recursively without prior consent.
- NEVER enqueues, reads, or indexes documents from unconsented directories.
- Dispatches 'privacy.discovery_detected' events over the EventBus to request
  explicit user authorization via the Desktop UI / Consent Prompt.
- Preserves connector path authorization boundaries (is_authorized_connector_path).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.events.bus import get_event_bus
from app.events.models import LifeOSEvent
from app.security.consent import is_consent_granted

logger = logging.getLogger("lifeos.connectors.discovery")


class SourceDiscoveryDaemon:
    """
    Background source discovery daemon monitoring top-level directories
    for user-prompted consent and enrollment.
    """

    def __init__(
        self,
        candidate_roots: list[str | Path] | None = None,
        scan_interval: float | None = None,
        notification_cooldown: float = 3600.0,
    ):
        try:
            import config as cfg
        except ImportError:
            import app.config as cfg

        if candidate_roots is not None:
            self.candidate_roots = [Path(r).resolve() for r in candidate_roots]
        else:
            configured = getattr(cfg, "DISCOVERY_CANDIDATE_ROOTS", None)
            if configured:
                self.candidate_roots = [Path(r).resolve() for r in configured]
            else:
                self.candidate_roots = [
                    (Path.home() / "Documents").resolve(),
                    (Path.home() / "Downloads").resolve(),
                ]

        self.scan_interval = scan_interval or getattr(cfg, "DISCOVERY_INTERVAL_SECONDS", 60.0)
        self.notification_cooldown = notification_cooldown
        self.enabled = getattr(cfg, "DISCOVERY_ENABLED", True)

        self._discovered_notified: dict[str, float] = {}  # path_str -> timestamp
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()

    def scan_once(self) -> list[dict[str, Any]]:
        """
        Execute a single discovery pass across candidate roots.
        Returns a list of unconsented sources detected during this pass.
        """
        if not self.enabled:
            return []

        now = time.time()
        detected_sources: list[dict[str, Any]] = []

        for candidate in self.candidate_roots:
            try:
                cand_path = Path(candidate).resolve()
                if not cand_path.exists() or not cand_path.is_dir():
                    continue

                # Check if explicit consent is already granted for this directory
                if is_consent_granted(cand_path, purpose="indexing"):
                    continue

                path_str = str(cand_path)
                with self._lock:
                    last_notified = self._discovered_notified.get(path_str, 0.0)
                    if now - last_notified < self.notification_cooldown:
                        continue  # Cooldown active, avoid duplicate spam

                # Shallow scan ONLY (top-level directory entries only, strictly non-recursive)
                file_count = 0
                try:
                    for entry in cand_path.iterdir():
                        if entry.is_file():
                            file_count += 1
                except (PermissionError, OSError) as pe:
                    logger.debug(f"Permission denied inspecting candidate '{cand_path}': {pe}")
                    continue

                detection_info = {
                    "resource_path": path_str,
                    "scope_type": "folder",
                    "suggested_name": cand_path.name,
                    "file_count": file_count,
                    "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                }

                # Record notification timestamp
                with self._lock:
                    self._discovered_notified[path_str] = now

                # Dispatch event over EventBus for client UI
                self._emit_discovery_event(detection_info)
                detected_sources.append(detection_info)
                logger.info(f"Discovered unconsented source folder: '{path_str}' ({file_count} candidate files)")

            except Exception as exc:
                logger.error(f"Error during source discovery on candidate '{candidate}': {exc}", exc_info=True)

        return detected_sources

    def _emit_discovery_event(self, info: dict[str, Any]) -> None:
        """Publish discovery event to EventBus."""
        try:
            bus = get_event_bus()
            bus.publish_sync(LifeOSEvent(
                event_type="privacy.discovery_detected",
                producer="connectors.discovery",
                resource_path=info["resource_path"],
                payload=info,
            ))
        except Exception as exc:
            logger.debug(f"Failed to publish discovery event: {exc}")

    def start(self) -> None:
        """Start the background discovery scanner thread."""
        with self._lock:
            if self._running and self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="LifeOS-SourceDiscoveryDaemon",
                daemon=True,
            )
            self._thread.start()
            logger.info(f"Source discovery daemon started (interval={self.scan_interval}s)")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                logger.error(f"Error in discovery loop: {exc}", exc_info=True)
            self._stop_event.wait(timeout=self.scan_interval)
        self._running = False
        logger.info("Source discovery daemon stopped cleanly.")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background discovery scanner."""
        with self._lock:
            if not self._running and (not self._thread or not self._thread.is_alive()):
                return
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=timeout)
            self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running and self._thread is not None and self._thread.is_alive()


# Global Singleton
_global_discovery: SourceDiscoveryDaemon | None = None
_discovery_lock = threading.Lock()


def get_discovery_daemon() -> SourceDiscoveryDaemon:
    """Retrieve the global singleton SourceDiscoveryDaemon instance."""
    global _global_discovery
    with _discovery_lock:
        if _global_discovery is None:
            _global_discovery = SourceDiscoveryDaemon()
        return _global_discovery
