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

from config import DOCUMENTS_DIR

from ingestion.ingest import (
    ingest_documents,
    ingest_file,
    remove_file,
)


logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# FILE STABILITY
# ------------------------------------------------------------

STABILITY_SECONDS = 1.0
CHECK_INTERVAL_SECONDS = 0.25


# ------------------------------------------------------------
# EVENT DEBOUNCING
# ------------------------------------------------------------

DEBOUNCE_SECONDS = 2.0


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
    def _wait_for_file_stability(file_path):

        path = Path(
            file_path
        )

        stable_since = None
        previous_size = None

        while True:

            if not path.exists() or not path.is_file():
                return False

            try:

                current_size = path.stat().st_size

            except OSError:

                time.sleep(
                    CHECK_INTERVAL_SECONDS
                )

                continue

            if current_size == 0:

                stable_since = None
                previous_size = 0

                time.sleep(
                    CHECK_INTERVAL_SECONDS
                )

                continue

            if current_size != previous_size:

                previous_size = current_size
                stable_since = time.monotonic()

                time.sleep(
                    CHECK_INTERVAL_SECONDS
                )

                continue

            if (
                stable_since is not None
                and (
                    time.monotonic()
                    - stable_since
                ) >= STABILITY_SECONDS
            ):

                return True

            time.sleep(
                CHECK_INTERVAL_SECONDS
            )

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

                stable = (
                    self._wait_for_file_stability(
                        path
                    )
                )

                if not stable:

                    logger.warning(
                        "File disappeared before becoming stable: %s",
                        path,
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


def start_watcher(
    documents_dir=DOCUMENTS_DIR
):
    """
    Start the LifeOS document watcher.

    Performs one initial incremental synchronization,
    then switches to targeted filesystem processing.
    """

    documents_dir = Path(
        documents_dir
    ).resolve()

    documents_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger.info(
        "Running initial incremental ingestion..."
    )

    ingest_documents(
        documents_dir=documents_dir
    )

    event_handler = DocumentEventHandler(
        documents_dir
    )

    observer = Observer()

    observer.schedule(
        event_handler,
        str(documents_dir),
        recursive=True,
    )

    observer.start()

    logger.info(
        "LifeOS document watcher started: %s",
        documents_dir,
    )

    try:

        while True:
            time.sleep(1)

    except KeyboardInterrupt:

        logger.info(
            "Stopping LifeOS document watcher..."
        )

        observer.stop()

    finally:

        observer.join()

        logger.info(
            "LifeOS document watcher stopped."
        )


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    start_watcher()