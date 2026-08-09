"""
watcher.py

Automatic document-folder watcher for LifeOS.

Responsibilities:
- Detect new files.
- Detect modified files.
- Detect deleted files.
- Wait for files to become stable before ingestion.
- Delegate all actual ingestion work to ingest_documents().
"""

import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import DOCUMENTS_DIR
from ingestion.ingest import ingest_documents


logger = logging.getLogger(__name__)

# How long the file must remain unchanged before ingestion.
STABILITY_SECONDS = 1.0

# How frequently to check file stability.
CHECK_INTERVAL_SECONDS = 0.25


def wait_for_file_stability(file_path):
    """
    Wait until a file exists, is non-empty, and its size remains
    unchanged for STABILITY_SECONDS.

    This prevents ingestion of empty or partially written files.
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
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # Never ingest an empty file.
        if current_size == 0:
            stable_since = None
            previous_size = 0
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # File size changed: restart the stability timer.
        if current_size != previous_size:
            previous_size = current_size
            stable_since = time.monotonic()
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # Size is unchanged and non-zero.
        if (
            stable_since is not None
            and time.monotonic() - stable_since
            >= STABILITY_SECONDS
        ):
            return True

        time.sleep(CHECK_INTERVAL_SECONDS)


class DocumentEventHandler(FileSystemEventHandler):
    """
    Filesystem event handler.

    The handler does not implement ingestion itself.
    It delegates to the existing ingestion pipeline.
    """

    def _process_file_event(self, event_type, file_path):
        """
        Wait for a file to stabilize and then run incremental
        ingestion.
        """

        path = Path(file_path)

        logger.info(
            "Filesystem change detected: %s: %s",
            event_type,
            path,
        )

        if event_type == "deleted":
            self._run_ingestion()
            return

        if not wait_for_file_stability(path):
            logger.warning(
                "File disappeared before becoming stable: %s",
                path,
            )
            return

        self._run_ingestion()

    @staticmethod
    def _run_ingestion():
        """Run the existing incremental ingestion pipeline."""

        try:
            ingest_documents()

        except Exception:
            logger.exception(
                "Automatic ingestion failed."
            )

    def on_created(self, event):

        if not event.is_directory:
            self._process_file_event(
                "created",
                event.src_path,
            )

    def on_modified(self, event):

        if not event.is_directory:
            self._process_file_event(
                "modified",
                event.src_path,
            )

    def on_deleted(self, event):

        if not event.is_directory:
            self._process_file_event(
                "deleted",
                event.src_path,
            )

    def on_moved(self, event):

        if not event.is_directory:
            self._process_file_event(
                "moved",
                event.dest_path,
            )


def start_watcher(documents_dir=DOCUMENTS_DIR):
    """
    Start the LifeOS document watcher.

    Performs one initial incremental ingestion and then
    continuously watches the document directory.
    """

    documents_dir = Path(documents_dir).resolve()

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

    event_handler = DocumentEventHandler()

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