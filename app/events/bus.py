"""
bus.py

Centralized, thread-safe, asynchronous In-Memory Event Bus for LifeOS.
Connects subsystem event producers (ingestion queue, filesystem watcher, query runner,
device pairing, and consent updates) to reactive client endpoints (SSE and WebSockets).

Guarantees:
- Real-time event broadcasting to multiple concurrent subscribers.
- Non-blocking synchronous publishing from background threads.
- Privacy & consent filtering: payloads containing unconsented resource paths are redacted.
- Reconnection buffering: maintains a rolling buffer of recent events for Last-Event-ID replay.
- Slow-consumer isolation: bounded queues drop oldest messages on overflow to preserve memory.
"""

from __future__ import annotations

import asyncio
import collections
import copy
import fnmatch
import logging
import threading
from pathlib import Path
from typing import Any

from app.events.models import LifeOSEvent

logger = logging.getLogger("lifeos.events.bus")

_GLOBAL_EVENT_BUS: EventBus | None = None
_BUS_LOCK = threading.Lock()


class EventBus:
    """
    In-memory asynchronous pub/sub event bus with buffer replay and privacy enforcement.
    """

    def __init__(self, buffer_size: int = 100):
        self._subscribers: set[asyncio.Queue[LifeOSEvent]] = set()
        self._subscriber_filters: dict[asyncio.Queue[LifeOSEvent], set[str] | None] = {}
        self._buffer: collections.deque[LifeOSEvent] = collections.deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind or update the active asyncio event loop."""
        self._loop = loop

    def get_loop(self) -> asyncio.AbstractEventLoop | None:
        """Retrieve the currently bound asyncio event loop if running."""
        if self._loop and self._loop.is_running():
            return self._loop
        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            return loop
        except RuntimeError:
            return None

    def subscribe(
        self,
        topics: set[str] | None = None,
        max_queue_size: int = 200,
    ) -> asyncio.Queue[LifeOSEvent]:
        """
        Create a new subscriber queue listening to specified topics (or all if topics is None).
        Topic wildcards like 'ingestion.*' or '*' are supported.
        """
        queue: asyncio.Queue[LifeOSEvent] = asyncio.Queue(maxsize=max_queue_size)
        with self._lock:
            self._subscribers.add(queue)
            self._subscriber_filters[queue] = topics
        logger.debug(f"Subscriber registered (topics={topics}). Total subscribers: {len(self._subscribers)}")
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LifeOSEvent]) -> None:
        """Unregister an existing subscriber queue."""
        with self._lock:
            self._subscribers.discard(queue)
            self._subscriber_filters.pop(queue, None)
        logger.debug(f"Subscriber unregistered. Total subscribers: {len(self._subscribers)}")

    def _filter_privacy(self, event: LifeOSEvent) -> LifeOSEvent:
        """
        Evaluate real-time privacy consent against event resource_path.
        If consent is not granted or has expired, redact path information.
        Discovery and consent mutation events are exempt as they represent user prompts/updates.
        """
        if not event.resource_path:
            return event

        # Consent discovery and mutation events must preserve the proposed path for user decision
        if event.event_type in ("privacy.discovery_detected", "privacy.consent_changed"):
            return event

        try:
            from app.security.consent import is_consent_granted
            if not is_consent_granted(event.resource_path):
                # Redact sensitive paths from event clone
                sanitized = copy.deepcopy(event)
                sanitized.resource_path = "[REDACTED_BY_CONSENT]"
                if "path" in sanitized.payload:
                    sanitized.payload["path"] = "[REDACTED_BY_CONSENT]"
                if "file_path" in sanitized.payload:
                    sanitized.payload["file_path"] = "[REDACTED_BY_CONSENT]"
                return sanitized
        except Exception as exc:
            logger.debug(f"Privacy consent check skipped or failed during event filter: {exc}")

        return event

    def _matches_topics(self, event_type: str, filter_topics: set[str] | None) -> bool:
        """Check if event_type matches subscribed topic filters (supports wildcards)."""
        if not filter_topics or "*" in filter_topics:
            return True
        for pattern in filter_topics:
            if pattern == event_type or fnmatch.fnmatch(event_type, pattern):
                return True
        return False

    async def publish(self, event: LifeOSEvent) -> None:
        """
        Asynchronously broadcast an event to all matching subscribers and append to replay buffer.
        """
        # Privacy filter
        filtered_event = self._filter_privacy(event)

        with self._lock:
            self._buffer.append(filtered_event)
            active_subscribers = list(self._subscribers)

        for queue in active_subscribers:
            topics = self._subscriber_filters.get(queue)
            if not self._matches_topics(filtered_event.event_type, topics):
                continue

            try:
                if queue.full():
                    # Drop oldest event to prevent blocking / slow consumer memory leaks
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(filtered_event)
            except Exception as exc:
                logger.warning(f"Failed to deliver event {filtered_event.event_id} to subscriber: {exc}")

    def publish_sync(self, event: LifeOSEvent) -> None:
        """
        Thread-safe synchronous publisher for background threads (e.g. queue worker, watcher).
        Dispatches publication onto the running asyncio event loop if available.
        """
        loop = self.get_loop()
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.publish(event), loop)
        else:
            # Fallback for synchronous test environments without an active loop
            with self._lock:
                filtered_event = self._filter_privacy(event)
                self._buffer.append(filtered_event)
                subs = list(self._subscribers)
            for queue in subs:
                topics = self._subscriber_filters.get(queue)
                if self._matches_topics(filtered_event.event_type, topics):
                    try:
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except Exception:
                                pass
                        queue.put_nowait(filtered_event)
                    except Exception as exc:
                        logger.warning(f"Failed to deliver event {filtered_event.event_id} to subscriber: {exc}")

    def get_events_since(self, last_event_id: str) -> list[LifeOSEvent]:
        """
        Retrieve buffered events that occurred strictly after the specified last_event_id.
        Used for SSE reconnection replay.
        """
        with self._lock:
            events = list(self._buffer)

        found_idx = -1
        for idx, evt in enumerate(events):
            if evt.event_id == last_event_id:
                found_idx = idx
                break

        if found_idx >= 0:
            return events[found_idx + 1:]
        # If last_event_id is too old or unknown, return all buffered events
        return events

    def clear(self) -> None:
        """Clear all active subscribers and buffered events."""
        with self._lock:
            self._subscribers.clear()
            self._subscriber_filters.clear()
            self._buffer.clear()


def get_event_bus() -> EventBus:
    """Retrieve the global singleton EventBus instance."""
    global _GLOBAL_EVENT_BUS
    if _GLOBAL_EVENT_BUS is None:
        with _BUS_LOCK:
            if _GLOBAL_EVENT_BUS is None:
                _GLOBAL_EVENT_BUS = EventBus()
    return _GLOBAL_EVENT_BUS


def reset_event_bus() -> None:
    """Reset the global event bus (used in test isolation)."""
    global _GLOBAL_EVENT_BUS
    with _BUS_LOCK:
        if _GLOBAL_EVENT_BUS is not None:
            _GLOBAL_EVENT_BUS.clear()
        _GLOBAL_EVENT_BUS = EventBus()
