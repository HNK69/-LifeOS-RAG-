"""
watcher.py

Automatic document-folder watcher for LifeOS.

Responsibilities:
- Detect new files.
- Detect modified files.
- Detect deleted files.
- Wait for files to become stable.
- Debounce bursts of filesystem events.
- Run one incremental ingestion pass per burst.
"""

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import DOCUMENTS_DIR
from ingestion.ingest import ingest_documents


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


def wait_for_file_stability(file_path):
    """
    Wait until a file exists, is non-empty, and its size remains
    unchanged for STABILITY_SECONDS.
    """

    path = Path(file_path)

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
            and time.monotonic() - stable_since
            >= STABILITY_SECONDS
        ):
            return True

        time.sleep(
            CHECK_INTERVAL_SECONDS
        )


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
        ONE ingestion pass
    """

    def __init__(self, documents_dir):

        super().__init__()

        self.documents_dir = Path(
            documents_dir
        ).resolve()

        self._pending_paths = set()

        self._lock = threading.Lock()

        self._timer = None

    # --------------------------------------------------------
    # EVENT COLLECTION
    # --------------------------------------------------------

    def _queue_event(self, event_type, file_path):

        path = Path(file_path).resolve()

        logger.info(
            "Filesystem change detected: %s: %s",
            event_type,
            path,
        )

        with self._lock:

            self._pending_paths.add(
                str(path)
            )

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

            paths = set(
                self._pending_paths
            )

            self._pending_paths.clear()

            self._timer = None

        if not paths:
            return

        logger.info(
            "Processing %d filesystem changes",
            len(paths),
        )

        # Wait for newly created/modified files to finish writing.
        for path_string in paths:

            path = Path(path_string)

            if not path.exists():
                continue

            if not path.is_file():
                continue

            wait_for_file_stability(
                path
            )

        self._run_ingestion()

    # --------------------------------------------------------
    # INGESTION
    # --------------------------------------------------------

    def _run_ingestion(self):

        try:

            ingest_documents(
                documents_dir=self.documents_dir
            )

        except Exception:

            logger.exception(
                "Automatic ingestion failed."
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

        # The destination is what now exists.
        self._queue_event(
            "moved",
            event.dest_path,
        )


def start_watcher(
    documents_dir=DOCUMENTS_DIR
):
    """
    Start the LifeOS document watcher.

    Performs one initial incremental ingestion and then
    continuously watches the document directory.
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