"""
queue.py

Persistent, SQLite-backed asynchronous ingestion queue for LifeOS Milestone 1.

Guarantees:
1. Durable Job Tracking:
   Jobs are stored in an isolated SQLite database (data/queue.db) using WAL mode
   and ManagedConnection with busy_timeout, surviving application restarts.
2. Atomic Lease Claims:
   Jobs are claimed atomically with an expiration lease, preventing double-processing
   and race conditions across multiple workers or concurrent invocations.
3. Reindex Exclusivity Coordination:
   Respects cross-process and cross-thread reindex locks via is_reindexing_active().
   When reindexing is active, queue execution pauses without consuming retries or
   falsely triggering poison-pill quarantine.
4. Retry with Exponential Backoff:
   Transient failures apply exponential backoff with jitter before the next attempt.
5. Poison-Pill Quarantine:
   Jobs exceeding maximum attempts are quarantined with the error message preserved.
6. Worker Lifecycle:
   In-process background worker thread with graceful stop, exception shielding,
   and health reporting.
"""

from __future__ import annotations

import logging
import random
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.storage.sqlite import connect_sqlite

try:
    from config import QUEUE_DB, QUEUE_POLL_INTERVAL
except ImportError:
    from app.config import QUEUE_DB, QUEUE_POLL_INTERVAL

try:
    from app.knowledge.coordination import is_reindexing_active
except ImportError:
    def is_reindexing_active() -> bool:
        return False

logger = logging.getLogger("lifeos.ingestion.queue")


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class IngestionJob:
    job_id: str
    file_path: str
    status: str  # queued, processing, completed, failed, quarantined
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    backoff_exponent: float = 2.0
    next_run_at: str | None = None
    lease_until: str | None = None
    created_at: str = ""
    updated_at: str = ""
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def _resolve_queue_db(db_path: Path | str | None = None) -> Path | str:
    if db_path is not None:
        return db_path
    try:
        import config
        return getattr(config, "QUEUE_DB", QUEUE_DB)
    except Exception:
        return QUEUE_DB


def init_queue_db(db_path: Path | str | None = None) -> None:
    """Initialize the ingestion_jobs schema and indices in the queue database."""
    target_db = _resolve_queue_db(db_path)
    with connect_sqlite(target_db, timeout=10.0, wal_mode=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_jobs (
                job_id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                backoff_exponent REAL DEFAULT 2.0,
                next_run_at TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_message TEXT,
                workspace_id TEXT DEFAULT 'default'
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(ingestion_jobs)").fetchall()
        }
        if "workspace_id" not in columns:
            conn.execute("ALTER TABLE ingestion_jobs ADD COLUMN workspace_id TEXT DEFAULT 'default'")

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_status_priority_next_run
            ON ingestion_jobs (status, priority DESC, next_run_at ASC, created_at ASC)
            """
        )


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


# ============================================================
# QUEUE OPERATIONS
# ============================================================

def enqueue_job(
    file_path: Path | str,
    priority: int = 0,
    max_attempts: int = 3,
    backoff_exponent: float = 2.0,
    workspace_id: str = "default",
    db_path: Path | str | None = None,
) -> str:
    """
    Enqueue a document file path for asynchronous processing.

    Returns:
        job_id (str)
    """
    target_db = _resolve_queue_db(db_path)
    init_queue_db(target_db)

    job_id = f"job_{uuid.uuid4().hex}"
    now_iso = datetime.now(timezone.utc).isoformat()
    path_str = str(Path(file_path).resolve())

    with connect_sqlite(target_db, timeout=10.0, wal_mode=True) as conn:
        conn.execute(
            """
            INSERT INTO ingestion_jobs (
                job_id, file_path, status, priority, attempts,
                max_attempts, backoff_exponent, next_run_at, lease_until,
                created_at, updated_at, error_message, workspace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                path_str,
                "queued",
                priority,
                0,
                max_attempts,
                backoff_exponent,
                now_iso,
                None,
                now_iso,
                now_iso,
                None,
                workspace_id,
            ),
        )

    logger.info(f"Enqueued document job '{job_id}' for file: {path_str}")

    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type="ingestion.queued",
            producer="queue",
            resource_path=path_str,
            payload={"job_id": job_id, "file_path": path_str, "priority": priority},
        ))
    except Exception:
        pass

    return job_id


def get_job(job_id: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Retrieve an ingestion job by ID."""
    target_db = _resolve_queue_db(db_path)
    init_queue_db(target_db)

    with connect_sqlite(target_db, timeout=5.0, wal_mode=True) as conn:
        cursor = conn.execute(
            "SELECT * FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_dict(row)


def claim_next_job(
    lease_duration_sec: float = 60.0,
    db_path: Path | str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any] | None:
    """
    Atomically claim the highest-priority ready job.

    Coordinates with active reindexing: returns None if reindexing is active.
    A job is eligible if:
      - status == 'queued' OR (status == 'failed' AND next_run_at <= now)
        OR (status == 'processing' AND lease_until < now)
      - AND (next_run_at IS NULL OR next_run_at <= now)
    """
    if is_reindexing_active():
        logger.debug("Reindexing active; deferring job claim.")
        return None

    target_db = _resolve_queue_db(db_path)
    init_queue_db(target_db)

    now = now_utc or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lease_until_iso = (now + timedelta(seconds=lease_duration_sec)).isoformat()

    with connect_sqlite(target_db, timeout=10.0, wal_mode=True) as conn:
        # 1. Find the highest priority candidate
        cursor = conn.execute(
            """
            SELECT job_id, file_path, attempts, max_attempts, backoff_exponent
            FROM ingestion_jobs
            WHERE (
                status = 'queued'
                OR (status = 'failed' AND next_run_at <= ?)
                OR (status = 'processing' AND lease_until < ?)
            )
            AND (next_run_at IS NULL OR next_run_at <= ?)
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            (now_iso, now_iso, now_iso),
        )
        candidate = cursor.fetchone()
        if candidate is None:
            return None

        candidate_id = candidate["job_id"]

        # 2. Atomically claim lease
        claim_cursor = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'processing',
                lease_until = ?,
                attempts = attempts + 1,
                updated_at = ?
            WHERE job_id = ?
              AND (
                  status = 'queued'
                  OR (status = 'failed' AND next_run_at <= ?)
                  OR (status = 'processing' AND lease_until < ?)
              )
            """,
            (lease_until_iso, now_iso, candidate_id, now_iso, now_iso),
        )

        if claim_cursor.rowcount != 1:
            # Race condition lost to another worker
            return None

        # 3. Retrieve claimed record
        row_cursor = conn.execute(
            "SELECT * FROM ingestion_jobs WHERE job_id = ?",
            (candidate_id,),
        )
        return _row_to_dict(row_cursor.fetchone())


def complete_job(job_id: str, db_path: Path | str | None = None) -> None:
    """Mark a job as successfully completed."""
    target_db = _resolve_queue_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()

    with connect_sqlite(target_db, timeout=10.0, wal_mode=True) as conn:
        conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'completed',
                lease_until = NULL,
                error_message = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (now_iso, job_id),
        )
    logger.info(f"Ingestion job '{job_id}' marked as completed.")

    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type="ingestion.completed",
            producer="queue",
            payload={"job_id": job_id, "status": "completed"},
        ))
    except Exception:
        pass


def fail_job(
    job_id: str,
    error_message: str,
    db_path: Path | str | None = None,
    now_utc: datetime | None = None,
) -> None:
    """
    Handle job failure with exponential backoff or poison-pill quarantine.
    """
    target_db = _resolve_queue_db(db_path)
    now = now_utc or datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with connect_sqlite(target_db, timeout=10.0, wal_mode=True) as conn:
        cursor = conn.execute(
            "SELECT attempts, max_attempts, backoff_exponent FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return

        attempts = row["attempts"]
        max_attempts = row["max_attempts"]
        backoff_exp = row["backoff_exponent"] or 2.0

        if attempts >= max_attempts:
            new_status = "quarantined"
            next_run_iso = None
            logger.warning(
                f"Ingestion job '{job_id}' reached max attempts ({attempts}/{max_attempts}). "
                f"Quarantined with error: {error_message}"
            )
        else:
            new_status = "failed"
            # Exponential backoff with random jitter (0 to 0.5s)
            delay = (backoff_exp ** attempts) + random.uniform(0.0, 0.5)
            next_run_iso = (now + timedelta(seconds=delay)).isoformat()
            logger.info(
                f"Ingestion job '{job_id}' attempt {attempts}/{max_attempts} failed. "
                f"Retrying in {delay:.2f}s at {next_run_iso}. Error: {error_message}"
            )

        conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?,
                lease_until = NULL,
                error_message = ?,
                next_run_at = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (new_status, str(error_message), next_run_iso, now_iso, job_id),
        )

    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type=f"ingestion.{new_status}",
            producer="queue",
            payload={"job_id": job_id, "status": new_status, "error": str(error_message)},
        ))
    except Exception:
        pass


def process_next_job(db_path: Path | str | None = None) -> bool:
    """
    Claim and execute one pending ingestion job.

    Returns:
        True if a job was processed (completed or failed), False if no job was available.
    """
    if is_reindexing_active():
        return False

    job = claim_next_job(db_path=db_path)
    if not job:
        return False

    job_id = job["job_id"]
    file_path = Path(job["file_path"])

    try:
        from app.ingestion.ingest import ingest_file
        import inspect
        sig = inspect.signature(ingest_file)
        params = sig.parameters
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if "workspace_id" in params or has_varkw:
            ingest_file(file_path, workspace_id=job.get("workspace_id", "default"))
        else:
            ingest_file(file_path)
        complete_job(job_id, db_path=db_path)
        return True
    except Exception as exc:
        logger.error(f"Failed processing job '{job_id}' for file '{file_path}': {exc}", exc_info=True)
        fail_job(job_id, str(exc), db_path=db_path)
        return True


# ============================================================
# BACKGROUND QUEUE WORKER
# ============================================================

class QueueWorker:
    """
    In-process background worker thread polling and executing ingestion jobs.
    """

    def __init__(self, poll_interval: float | None = None, db_path: Path | str | None = None):
        self.poll_interval = poll_interval or getattr(QUEUE_POLL_INTERVAL, "value", 0.5) if hasattr(QUEUE_POLL_INTERVAL, "value") else float(QUEUE_POLL_INTERVAL)
        self.db_path = db_path
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._healthy = True

    def start(self) -> None:
        if self._running and self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._running = True
        self._healthy = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="LifeOS-IngestionQueueWorker",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Ingestion queue worker started (poll_interval={self.poll_interval}s)")

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    did_work = process_next_job(db_path=self.db_path)
                    if not did_work:
                        self._stop_event.wait(timeout=self.poll_interval)
                except Exception as exc:
                    logger.error(f"Error in queue worker iteration: {exc}", exc_info=True)
                    self._stop_event.wait(timeout=self.poll_interval)
        finally:
            self._running = False
            if not self._stop_event.is_set():
                # Thread terminated unexpectedly
                self._healthy = False
                logger.error("Queue worker thread terminated unexpectedly!")

    def stop(self, timeout: float = 5.0) -> None:
        if not self._running and (not self._thread or not self._thread.is_alive()):
            return

        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._running = False
        logger.info("Ingestion queue worker stopped cleanly.")

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def is_healthy(self) -> bool:
        if not self._running:
            return True
        return self._healthy and self._thread is not None and self._thread.is_alive()


# ============================================================
# SINGLETON WORKER MANAGEMENT
# ============================================================

_worker_lock = threading.Lock()
_global_worker: QueueWorker | None = None


def get_queue_worker() -> QueueWorker:
    global _global_worker
    with _worker_lock:
        if _global_worker is None:
            _global_worker = QueueWorker()
        return _global_worker


def start_queue_worker(
    poll_interval: float | None = None,
    db_path: Path | str | None = None,
) -> QueueWorker:
    global _global_worker
    with _worker_lock:
        if _global_worker is None:
            _global_worker = QueueWorker(poll_interval=poll_interval, db_path=db_path)
        _global_worker.start()
        return _global_worker


def stop_queue_worker(timeout: float = 5.0) -> None:
    global _global_worker
    with _worker_lock:
        if _global_worker is not None:
            _global_worker.stop(timeout=timeout)


def is_worker_running() -> bool:
    global _global_worker
    with _worker_lock:
        if _global_worker is None:
            return False
        return _global_worker.is_running()


def is_worker_healthy() -> bool:
    global _global_worker
    with _worker_lock:
        if _global_worker is None:
            return True
        return _global_worker.is_healthy()
