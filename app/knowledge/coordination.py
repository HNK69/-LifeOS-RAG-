"""
coordination.py

Concurrency and mutation coordination between reindex, watcher, and ingestion.

Invariants:
1. Reindex exclusivity:
   At most ONE reindex operation may run at any time across all threads and processes.
2. Mutation pause during reindex:
   While reindexing is active, concurrent external mutations (ingest_file, remove_file,
   and watcher flushes) are deferred or rejected so they do not corrupt the reindex
   or have their changes wiped by a reindex rollback.
3. Internal reindex reentrancy:
   The active reindexer thread is permitted to call ingest_documents/ingest_file/remove_file.
4. Non-blocking reads:
   Search and query operations do not acquire mutation locks and are never blocked.
5. OS-level crash recovery:
   File locks automatically release upon process termination or crash, preventing stale lockouts.
"""

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from filelock import FileLock, Timeout

from config import DATA_DIR

logger = logging.getLogger(__name__)

REINDEX_LOCK_NAME = "reindex.lock"

# In-process coordination state
_process_lock = threading.Lock()
_active_reindex_thread_id: int | None = None
_active_file_lock: FileLock | None = None


def get_reindex_lock_path() -> Path:
    """Return the absolute path to the reindex lock file."""
    try:
        from config import DATA_DIR as CURRENT_DATA_DIR
        base_dir = Path(CURRENT_DATA_DIR)
    except Exception:
        base_dir = Path(DATA_DIR)

    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / REINDEX_LOCK_NAME


def is_reindexing_active(lock_path: Path | None = None) -> bool:
    """
    Check if a reindex operation is currently running.

    Returns False if the caller is the active reindexer thread itself
    (allowing internal reindex ingestion calls to proceed).
    Returns True if another thread or process is actively reindexing.
    """
    current_thread = threading.get_ident()

    with _process_lock:
        if _active_reindex_thread_id is not None:
            # Active reindexer thread is allowed to mutate
            if current_thread == _active_reindex_thread_id:
                return False
            # Another thread in this process is actively reindexing
            return True

    # Check cross-process via non-blocking file lock probe
    target_path = lock_path or get_reindex_lock_path()
    if not target_path.exists():
        return False

    probe_lock = FileLock(str(target_path))
    try:
        probe_lock.acquire(timeout=0)
        probe_lock.release()
        return False
    except Timeout:
        return True
    except Exception as exc:
        logger.warning("Error probing reindex lock %s: %s", target_path, exc)
        return False


@contextmanager
def acquire_reindex_lock(
    timeout: float = 0.0,
    lock_path: Path | None = None,
) -> Generator[None, None, None]:
    """
    Acquire exclusive reindex coordination across threads and processes.

    Raises:
        RuntimeError: If another reindex is already in progress.
    """
    global _active_reindex_thread_id, _active_file_lock

    target_lock_path = lock_path or get_reindex_lock_path()
    current_thread = threading.get_ident()

    with _process_lock:
        if _active_reindex_thread_id is not None:
            raise RuntimeError(
                f"Reindex already in progress (in-process thread {_active_reindex_thread_id})"
            )

    file_lock = FileLock(str(target_lock_path))
    try:
        file_lock.acquire(timeout=timeout)
    except Timeout:
        raise RuntimeError(
            f"Reindex already in progress (file lock held on {target_lock_path})"
        )

    with _process_lock:
        _active_reindex_thread_id = current_thread
        _active_file_lock = file_lock

    try:
        yield
    finally:
        with _process_lock:
            _active_reindex_thread_id = None
            _active_file_lock = None
        try:
            file_lock.release()
        except Exception as exc:
            logger.warning("Error releasing file lock %s: %s", target_lock_path, exc)
