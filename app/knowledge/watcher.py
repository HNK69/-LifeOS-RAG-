"""
watcher.py

Automatic document-folder watcher for LifeOS.

Responsibilities:
- Detect new files.
- Detect modified files.
- Detect deleted files.
- Wait for files to become stable.
- Debounce bursts of filesystem events.
- Process ONLY affected files.
"""

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import (
    DOCUMENTS_DIR,
    WATCHER_DEBOUNCE_SECONDS,
    WATCHER_STABILITY_SECONDS,
    WATCHER_CHECK_INTERVAL_SECONDS,
    WATCHER_MAX_STABILITY_WAIT_SECONDS,
)

try:
    from ingestion.guardrails import validate_document_path
except ImportError:
    from app.ingestion.guardrails import validate_document_path

from ingestion.ingest import (
    ingest_documents,
    ingest_file,
    remove_file,
)


logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# FILE STABILITY
# ------------------------------------------------------------

STABILITY_SECONDS = WATCHER_STABILITY_SECONDS
CHECK_INTERVAL_SECONDS = WATCHER_CHECK_INTERVAL_SECONDS
MAX_STABILITY_WAIT_SECONDS = WATCHER_MAX_STABILITY_WAIT_SECONDS


# ------------------------------------------------------------
# EVENT DEBOUNCING
# ------------------------------------------------------------

DEBOUNCE_SECONDS = WATCHER_DEBOUNCE_SECONDS


class DocumentEventHandler(FileSystemEventHandler):
    """
    Collect filesystem events and debounce them.

    Example:

        WhatsApp downloads 20 files
                    ↓
        20 filesystem events
                    ↓
        debounce window
                    ↓
        process only those 20 files
    """

    def __init__(self, documents_dir):

        super().__init__()

        self.documents_dir = Path(
            documents_dir
        ).resolve()

        self._pending_events = {}

        self._lock = threading.Lock()

        self._timer = None

    # --------------------------------------------------------
    # FILE STABILITY
    # --------------------------------------------------------

    @staticmethod
    def _wait_for_file_stability(
        file_path,
        stability_duration=STABILITY_SECONDS,
        check_interval=CHECK_INTERVAL_SECONDS,
        max_wait_seconds=MAX_STABILITY_WAIT_SECONDS,
        sleep_func=time.sleep,
        time_func=time.monotonic,
    ):
        path = Path(file_path)
        start_time = time_func()
        stable_since = None
        previous_size = None

        while True:
            # Enforce bounded maximum wait time to prevent worker deadlocks
            elapsed = time_func() - start_time
            if elapsed >= max_wait_seconds:
                logger.warning(
                    "File stability timeout reached for %s (elapsed %.2fs >= %.2fs, previous_size=%s)",
                    path.name,
                    elapsed,
                    max_wait_seconds,
                    previous_size,
                )
                return False

            if not path.exists() or not path.is_file():
                return False

            try:
                current_size = path.stat().st_size
            except OSError:
                sleep_func(check_interval)
                continue

            # 0-byte file: incomplete writes may start empty.
            # Wait boundedly up to max_wait_seconds for data to arrive.
            if current_size == 0:
                previous_size = 0
                stable_since = None
                sleep_func(check_interval)
                continue

            # Size changed
            if current_size != previous_size:
                previous_size = current_size
                stable_since = time_func()
                sleep_func(check_interval)
                continue

            # Size is unchanged and > 0 bytes
            if (
                stable_since is not None
                and (time_func() - stable_since) >= stability_duration
            ):
                return True

            sleep_func(check_interval)

    # --------------------------------------------------------
    # EVENT COLLECTION
    # --------------------------------------------------------

    def _queue_event(
        self,
        event_type,
        file_path,
    ):

        path = Path(
            file_path
        ).resolve()

        logger.info(
            "Filesystem change detected: %s: %s",
            event_type,
            path,
        )

        with self._lock:

            existing_event = (
                self._pending_events.get(
                    str(path)
                )
            )

            # Preserve a deletion if it happens after a create
            # or modification during the debounce window.
            if (
                existing_event == "deleted"
                or event_type == "deleted"
            ):

                self._pending_events[
                    str(path)
                ] = "deleted"

            else:

                self._pending_events[
                    str(path)
                ] = event_type

            if self._timer is not None:

                self._timer.cancel()

            self._timer = threading.Timer(
                DEBOUNCE_SECONDS,
                self._flush_events,
            )

            self._timer.daemon = True

            self._timer.start()

    # --------------------------------------------------------
    # EVENT FLUSH
    # --------------------------------------------------------

    def _flush_events(self):

        try:
            from knowledge.coordination import is_reindexing_active
            if is_reindexing_active():
                logger.info("Reindex in progress; deferring watcher event flush.")
                with self._lock:
                    self._timer = threading.Timer(
                        DEBOUNCE_SECONDS,
                        self._flush_events,
                    )
                    self._timer.daemon = True
                    self._timer.start()
                return
        except Exception:
            pass

        with self._lock:

            events = dict(
                self._pending_events
            )

            self._pending_events.clear()

            self._timer = None

        if not events:
            return

        logger.info(
            "Processing %d affected files",
            len(events),
        )

        for path_string, event_type in events.items():

            path = Path(
                path_string
            )

            try:
                # Security boundary check: ensure event path resolves strictly beneath documents_dir
                try:
                    path = validate_document_path(
                        path,
                        base_dir=self.documents_dir,
                        must_exist=False,
                    )
                except ValueError as err:
                    logger.warning(
                        "Rejecting watcher event for invalid path %s: %s",
                        path_string,
                        err,
                    )
                    continue

                # ------------------------------------------------
                # DELETED
                # ------------------------------------------------

                if event_type == "deleted":

                    remove_file(
                        path
                    )

                    continue

                # ------------------------------------------------
                # CREATED / MODIFIED / MOVED
                # ------------------------------------------------

                if (
                    not path.exists()
                    or not path.is_file()
                ):

                    continue

                # Filter temporary office lock files or hidden files
                if path.name.startswith("~$") or path.name.startswith("."):
                    continue

                stable = (
                    self._wait_for_file_stability(
                        path
                    )
                )

                if not stable:

                    logger.warning(
                        "File %s did not stabilize or remained empty; skipping.",
                        path.name,
                    )

                    continue

                ingest_file(
                    path
                )

            except Exception:

                logger.exception(
                    "Failed processing filesystem event for %s",
                    path,
                )

    # --------------------------------------------------------
    # WATCHDOG EVENTS
    # --------------------------------------------------------

    def on_created(self, event):

        if event.is_directory:
            return

        self._queue_event(
            "created",
            event.src_path,
        )

    def on_modified(self, event):

        if event.is_directory:
            return

        self._queue_event(
            "modified",
            event.src_path,
        )

    def on_deleted(self, event):

        if event.is_directory:
            return

        self._queue_event(
            "deleted",
            event.src_path,
        )

    def on_moved(self, event):

        if event.is_directory:
            return

        # A move is represented as:
        #
        # old path → delete
        # new path → create
        #
        # Both are handled inside the same debounce window.

        self._queue_event(
            "deleted",
            event.src_path,
        )

        self._queue_event(
            "created",
            event.dest_path,
        )


# ------------------------------------------------------------
# LIFECYCLE MANAGEMENT
# ------------------------------------------------------------

_active_observer: Observer | None = None
_observer_lock = threading.Lock()


def is_watcher_running() -> bool:
    """Check if the background observer is actively running."""
    with _observer_lock:
        return _active_observer is not None and _active_observer.is_alive()


def start_background_watcher(
    documents_dir: Path | str | None = None,
    run_initial_ingest: bool = True,
) -> Observer:
    """
    Start the LifeOS document watcher asynchronously in the background.
    Thread-safe and idempotent (returns existing observer if already running).
    """
    global _active_observer
    with _observer_lock:
        if _active_observer is not None and _active_observer.is_alive():
            logger.info("Background watcher is already running.")
            return _active_observer

        target_dir = Path(documents_dir or DOCUMENTS_DIR).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        if run_initial_ingest:
            logger.info("Running initial incremental ingestion for watcher...")
            try:
                ingest_documents(documents_dir=target_dir)
            except Exception as exc:
                logger.warning("Initial ingestion encountered an issue: %s", exc)

        event_handler = DocumentEventHandler(target_dir)
        observer = Observer()
        observer.schedule(event_handler, str(target_dir), recursive=True)
        observer.daemon = True
        observer.start()

        _active_observer = observer
        logger.info("LifeOS background document watcher started: %s", target_dir)
        return _active_observer


def stop_background_watcher(timeout: float = 5.0) -> bool:
    """
    Stop the active background watcher cleanly and join observer threads.
    Idempotent and safe to invoke multiple times during graceful shutdown.
    """
    global _active_observer
    with _observer_lock:
        if _active_observer is None:
            return False

        observer = _active_observer
        _active_observer = None

    try:
        if observer.is_alive():
            observer.stop()
            observer.join(timeout=timeout)
        logger.info("LifeOS background document watcher stopped.")
        return True
    except Exception as exc:
        logger.warning("Error stopping background watcher: %s", exc)
        return False


def start_watcher(
    documents_dir=DOCUMENTS_DIR
):
    """
    Start the LifeOS document watcher in blocking mode (CLI).
    """
    start_background_watcher(documents_dir=documents_dir, run_initial_ingest=True)
    try:
        while is_watcher_running():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping LifeOS document watcher...")
    finally:
        stop_background_watcher()
        logger.info("LifeOS document watcher stopped.")


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    start_watcher()