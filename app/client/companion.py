"""
companion.py

Desktop Client Companion for LifeOS.
Acts as a reactive background desktop companion daemon that listens to real-time
LifeOS events (ingestion status, device pairing requests, and consent mutations)
and triggers local user notifications and automation hooks.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

from app.events.bus import EventBus, get_event_bus
from app.events.models import LifeOSEvent

logger = logging.getLogger("lifeos.client.companion")


class DesktopNotification:
    """Represents a normalized user notification dispatched to the desktop environment."""
    def __init__(self, title: str, message: str, level: str = "info", metadata: dict[str, Any] | None = None):
        self.title = title
        self.message = message
        self.level = level  # 'info', 'warning', 'error', 'action_required'
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "level": self.level,
            "metadata": self.metadata,
        }


class LifeOSCompanion:
    """
    Desktop Client Companion daemon for LifeOS.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        notification_handler: Callable[[DesktopNotification], None] | None = None,
    ):
        self._bus = event_bus or get_event_bus()
        self._notification_handler = notification_handler or self._default_notification_sink
        self._notifications: list[DesktopNotification] = []
        self._running = False
        self._listener_queue: asyncio.Queue[LifeOSEvent] | None = None
        self._task: asyncio.Task | None = None
        self._lock = threading.Lock()

    def _default_notification_sink(self, notif: DesktopNotification) -> None:
        """Default fallback notification printer."""
        logger.info(f"[{notif.level.upper()}] {notif.title}: {notif.message}")
        with self._lock:
            self._notifications.append(notif)

    @property
    def dispatched_notifications(self) -> list[DesktopNotification]:
        with self._lock:
            return list(self._notifications)

    def handle_event(self, event: LifeOSEvent) -> DesktopNotification | None:
        """
        Process an incoming event and translate it into a desktop user notification.
        """
        evt_type = event.event_type
        payload = event.payload

        notif: DesktopNotification | None = None

        if evt_type == "ingestion.completed":
            job_id = payload.get("job_id", "unknown")
            notif = DesktopNotification(
                title="Document Ingested",
                message=f"Document job '{job_id}' successfully indexed into knowledge base.",
                level="info",
                metadata=payload,
            )
        elif evt_type in ("ingestion.failed", "ingestion.quarantined"):
            job_id = payload.get("job_id", "unknown")
            err = payload.get("error", "Unknown error")
            notif = DesktopNotification(
                title="Ingestion Alert",
                message=f"Job '{job_id}' failed: {err}",
                level="warning" if evt_type == "ingestion.failed" else "error",
                metadata=payload,
            )
        elif evt_type == "device.pairing_requested":
            peer_id = payload.get("peer_device_id", "Unknown Peer")
            notif = DesktopNotification(
                title="Device Pairing Request",
                message=f"Peer '{peer_id}' has requested pairing. Complete challenge verification to authorize.",
                level="action_required",
                metadata=payload,
            )
        elif evt_type == "privacy.consent_changed":
            action = payload.get("action", "modified")
            res_path = payload.get("resource_path", "")
            notif = DesktopNotification(
                title="Privacy Boundary Updated",
                message=f"Consent {action} for '{res_path}'.",
                level="info",
                metadata=payload,
            )

        if notif:
            self._notification_handler(notif)

        return notif

    async def start(self, topics: set[str] | None = None) -> None:
        """Start listening to real-time event bus asynchronously."""
        self._running = True
        self._listener_queue = self._bus.subscribe(topics=topics)

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(self._listener_queue.get(), timeout=1.0)
                    self.handle_event(event)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            if self._listener_queue:
                self._bus.unsubscribe(self._listener_queue)

    def stop(self) -> None:
        """Signal the companion listener to stop."""
        self._running = False
