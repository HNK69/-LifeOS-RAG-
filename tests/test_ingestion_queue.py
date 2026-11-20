"""
test_ingestion_queue.py

Comprehensive, deterministic test suite for Milestone 1:
Durable Ingestion Task Queue & Job Tracking.

Tests cover:
1. Queue Database & Schema Initialization (data/queue.db)
2. Queue Operations (enqueue, get_job, atomic claim_next_job, complete_job)
3. Retry with Exponential Backoff & Poison-Pill Quarantine
4. Reindexing Coordination & Exclusivity (pausing without burning retries)
5. Background Queue Worker Lifecycle (start, execute, stop, health reporting)
6. API Endpoints:
   - POST /api/documents (default synchronous HTTP 201)
   - POST /api/documents?async=true (opt-in asynchronous HTTP 202)
   - POST /api/documents/jobs (dedicated async endpoint HTTP 202)
   - GET /api/documents/jobs/{job_id} (status polling HTTP 200 & 404)
7. Health & Readiness Probe Integration (GET /ready includes queue_storage and queue_worker)
"""

import io
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.ingestion.queue import (
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
    init_queue_db,
    is_worker_healthy,
    is_worker_running,
    process_next_job,
    start_queue_worker,
    stop_queue_worker,
    QueueWorker,
)

TEST_API_KEY = "test-api-key-12345"
TEST_ADMIN_KEY = "test-admin-key-99999"


@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    """Isolate queue database, documents, and auth in tmp_path."""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_docs = test_data / "documents"
    test_docs.mkdir(parents=True, exist_ok=True)
    test_queue_db = test_data / "queue.db"

    monkeypatch.setattr(config, "DATA_DIR", test_data)
    monkeypatch.setattr(config, "DOCUMENTS_DIR", test_docs)
    monkeypatch.setattr(config, "QUEUE_DB", test_queue_db)
    monkeypatch.setattr(config, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "QUEUE_WORKER_ENABLED", False)

    init_queue_db(test_queue_db)
    yield test_queue_db


@pytest.fixture
def client():
    """TestClient for API verification."""
    with TestClient(app) as test_client:
        yield test_client


# ============================================================
# 1. QUEUE SCHEMA & DIRECT OPERATIONS
# ============================================================

def test_queue_enqueue_and_get_job(tmp_path):
    queue_db = tmp_path / "queue.db"
    dummy_file = tmp_path / "sample.txt"
    dummy_file.write_text("Hello world", encoding="utf-8")

    job_id = enqueue_job(dummy_file, priority=5, db_path=queue_db)
    assert job_id.startswith("job_")

    job = get_job(job_id, db_path=queue_db)
    assert job is not None
    assert job["job_id"] == job_id
    assert job["file_path"] == str(dummy_file.resolve())
    assert job["status"] == "queued"
    assert job["priority"] == 5
    assert job["attempts"] == 0
    assert job["max_attempts"] == 3
    assert job["error_message"] is None


def test_queue_atomic_claim_and_completion(tmp_path):
    queue_db = tmp_path / "queue.db"
    dummy_file = tmp_path / "sample.txt"
    dummy_file.write_text("content", encoding="utf-8")

    job_id = enqueue_job(dummy_file, priority=1, db_path=queue_db)

    # Claim job
    claimed = claim_next_job(lease_duration_sec=30.0, db_path=queue_db)
    assert claimed is not None
    assert claimed["job_id"] == job_id
    assert claimed["status"] == "processing"
    assert claimed["attempts"] == 1
    assert claimed["lease_until"] is not None

    # Immediate second claim should return None (already leased)
    assert claim_next_job(db_path=queue_db) is None

    # Mark completed
    complete_job(job_id, db_path=queue_db)
    updated = get_job(job_id, db_path=queue_db)
    assert updated["status"] == "completed"
    assert updated["lease_until"] is None


def test_queue_priority_ordering(tmp_path):
    queue_db = tmp_path / "queue.db"
    file_low = tmp_path / "low.txt"
    file_high = tmp_path / "high.txt"
    file_med = tmp_path / "med.txt"
    file_low.touch()
    file_high.touch()
    file_med.touch()

    id_low = enqueue_job(file_low, priority=1, db_path=queue_db)
    id_high = enqueue_job(file_high, priority=10, db_path=queue_db)
    id_med = enqueue_job(file_med, priority=5, db_path=queue_db)

    first = claim_next_job(db_path=queue_db)
    assert first["job_id"] == id_high

    second = claim_next_job(db_path=queue_db)
    assert second["job_id"] == id_med

    third = claim_next_job(db_path=queue_db)
    assert third["job_id"] == id_low


# ============================================================
# 2. RETRY WITH BACKOFF & POISON-PILL QUARANTINE
# ============================================================

def test_queue_retry_and_exponential_backoff(tmp_path):
    queue_db = tmp_path / "queue.db"
    dummy_file = tmp_path / "faulty.txt"
    dummy_file.touch()

    job_id = enqueue_job(dummy_file, max_attempts=3, backoff_exponent=2.0, db_path=queue_db)

    # Claim attempt 1
    claim_next_job(db_path=queue_db)
    fail_job(job_id, error_message="Network glitch", db_path=queue_db)

    job_after_fail = get_job(job_id, db_path=queue_db)
    assert job_after_fail["status"] == "failed"
    assert job_after_fail["attempts"] == 1
    assert job_after_fail["error_message"] == "Network glitch"
    assert job_after_fail["next_run_at"] is not None


def test_queue_poison_pill_quarantine_after_max_attempts(tmp_path):
    queue_db = tmp_path / "queue.db"
    dummy_file = tmp_path / "corrupt.txt"
    dummy_file.touch()

    job_id = enqueue_job(dummy_file, max_attempts=2, db_path=queue_db)

    # Attempt 1
    claim_next_job(db_path=queue_db)
    fail_job(job_id, error_message="Parse error 1", db_path=queue_db)
    assert get_job(job_id, db_path=queue_db)["status"] == "failed"

    # Attempt 2: advance time past backoff delay
    future_time = datetime.now(timezone.utc) + timedelta(seconds=10)
    claimed2 = claim_next_job(db_path=queue_db, now_utc=future_time)
    assert claimed2 is not None
    fail_job(job_id, error_message="Parse error 2 (fatal)", db_path=queue_db, now_utc=future_time)

    # After attempt 2 >= max_attempts, status must be quarantined
    quarantined = get_job(job_id, db_path=queue_db)
    assert quarantined["status"] == "quarantined"
    assert quarantined["attempts"] == 2
    assert "fatal" in quarantined["error_message"]

    # Claim should no longer pick it up even in the future
    far_future = future_time + timedelta(days=1)
    assert claim_next_job(db_path=queue_db, now_utc=far_future) is None


# ============================================================
# 3. REINDEXING COORDINATION
# ============================================================

def test_queue_pauses_during_active_reindex(tmp_path, monkeypatch):
    queue_db = tmp_path / "queue.db"
    dummy_file = tmp_path / "reindex_doc.txt"
    dummy_file.touch()

    job_id = enqueue_job(dummy_file, db_path=queue_db)

    # Simulate reindexing active in coordination module
    import app.ingestion.queue as queue_mod
    monkeypatch.setattr(queue_mod, "is_reindexing_active", lambda: True)

    # While reindexing is active, claim_next_job returns None and process_next_job returns False
    assert claim_next_job(db_path=queue_db) is None
    assert process_next_job(db_path=queue_db) is False

    # Verify attempt count was NOT incremented (no false retry consumption)
    job = get_job(job_id, db_path=queue_db)
    assert job["status"] == "queued"
    assert job["attempts"] == 0


# ============================================================
# 4. BACKGROUND WORKER LIFECYCLE
# ============================================================

def test_queue_worker_execution_lifecycle(tmp_path, monkeypatch):
    queue_db = tmp_path / "queue.db"
    doc_path = tmp_path / "doc.txt"
    doc_path.write_text("Worker integration test document.", encoding="utf-8")

    # Mock ingest_file to avoid spinning up heavy embedding model
    ingest_mock_called = []

    def mock_ingest(file_p):
        ingest_mock_called.append(str(file_p))
        return {"chunks": 2, "status": "success"}

    import app.ingestion.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest_file", mock_ingest)

    worker = QueueWorker(poll_interval=0.05, db_path=queue_db)
    assert not worker.is_running()

    worker.start()
    assert worker.is_running()
    assert worker.is_healthy()

    job_id = enqueue_job(doc_path, db_path=queue_db)

    # Wait for worker loop to process
    for _ in range(50):
        job = get_job(job_id, db_path=queue_db)
        if job and job["status"] == "completed":
            break
        time.sleep(0.05)

    worker.stop(timeout=2.0)
    assert not worker.is_running()

    final_job = get_job(job_id, db_path=queue_db)
    assert final_job["status"] == "completed"
    assert len(ingest_mock_called) == 1


# ============================================================
# 5. API ENDPOINTS: SYNCHRONOUS & ASYNCHRONOUS INGESTION
# ============================================================

def test_api_upload_synchronous_default(client, monkeypatch):
    """Verify POST /api/documents remains synchronous HTTP 201 by default."""
    import app.api.routes.documents as doc_route
    monkeypatch.setattr(doc_route, "ingest_file", lambda p: {"chunks": 4, "status": "success"})

    fake_content = b"Synchronous ingestion test file."
    res = client.post(
        "/api/documents",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        files={"file": ("sync_test.txt", io.BytesIO(fake_content), "text/plain")},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "ingested"
    assert data["filename"] == "sync_test.txt"
    assert data["chunks"] == 4


def test_api_upload_async_query_param(client, monkeypatch):
    """Verify POST /api/documents?async=true returns HTTP 202 with job_id."""
    fake_content = b"Asynchronous ingestion test file."
    res = client.post(
        "/api/documents?async=true",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        files={"file": ("async_query_test.txt", io.BytesIO(fake_content), "text/plain")},
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "queued"
    assert data["filename"] == "async_query_test.txt"
    assert "job_id" in data
    assert data["job_id"].startswith("job_")

    # Poll status via GET /api/documents/jobs/{job_id}
    job_res = client.get(
        f"/api/documents/jobs/{data['job_id']}",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert job_data["job_id"] == data["job_id"]
    assert job_data["status"] == "queued"


def test_api_upload_dedicated_jobs_endpoint(client, monkeypatch):
    """Verify POST /api/documents/jobs returns HTTP 202."""
    fake_content = b"Dedicated jobs endpoint test file."
    res = client.post(
        "/api/documents/jobs?priority=3",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        files={"file": ("dedicated_job.txt", io.BytesIO(fake_content), "text/plain")},
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "queued"
    assert data["filename"] == "dedicated_job.txt"
    assert "job_id" in data

    # Poll status
    job_res = client.get(
        f"/api/documents/jobs/{data['job_id']}",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert job_res.status_code == 200
    assert job_res.json()["priority"] == 3


def test_api_get_job_not_found(client):
    """Verify GET /api/documents/jobs/{non_existent} returns 404."""
    res = client.get(
        "/api/documents/jobs/job_nonexistent_12345",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 404
    assert "not found" in res.json()["error"]["message"]


# ============================================================
# 6. HEALTH & READINESS PROBE
# ============================================================

def test_readiness_probe_includes_queue_storage(client):
    """Verify GET /ready checks include queue_storage and queue_worker."""
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"
    checks = res.json()["checks"]
    assert checks["data_directory"] is True
    assert checks["documents_directory"] is True
    assert checks["sqlite_storage"] is True
    assert checks["queue_storage"] is True
    assert checks["queue_worker"] is True


# ============================================================
# 7. CONTINUOUS 100-JOB & MEMORY STABILITY VERIFICATION
# ============================================================

def test_100_job_continuous_ingestion_and_memory_stability(tmp_path, monkeypatch):
    """
    Stress test verifying:
    - 100 concurrent/continuous ingestion jobs are executed reliably
    - All 100 jobs transition to 'completed' with 0 dropped jobs
    - No SQLite lock contention or timeout failures
    - Bounded memory growth (no memory leaks across jobs)
    """
    import tracemalloc
    queue_db = tmp_path / "queue.db"

    processed = []

    def mock_ingest(file_p):
        processed.append(str(file_p))
        return {"chunks": 1, "status": "success"}

    import app.ingestion.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest_file", mock_ingest)

    # Generate 100 test document files
    doc_paths = []
    for i in range(100):
        p = tmp_path / f"batch_doc_{i:03d}.txt"
        p.write_text(f"Synthetic document content {i}", encoding="utf-8")
        doc_paths.append(p)

    worker = QueueWorker(poll_interval=0.01, db_path=queue_db)
    worker.start()

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    job_ids = []
    for p in doc_paths:
        jid = enqueue_job(p, db_path=queue_db)
        job_ids.append(jid)

    # Wait for all 100 jobs to complete
    max_wait_seconds = 20.0
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        if len(processed) >= 100:
            break
        time.sleep(0.05)

    worker.stop(timeout=3.0)
    assert len(processed) == 100

    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    # Verify all jobs are completed in DB with exactly 1 attempt
    for jid in job_ids:
        job = get_job(jid, db_path=queue_db)
        assert job is not None
        assert job["status"] == "completed"
        assert job["attempts"] == 1

    # Verify memory stability: net growth < 15MB across 100 jobs
    stats = snapshot_after.compare_to(snapshot_before, "lineno")
    total_diff_kb = sum(stat.size_diff for stat in stats) / 1024
    assert total_diff_kb < 15360, f"Excessive memory growth detected: {total_diff_kb:.2f} KB"


# ============================================================
# 8. EXCEPTION SHIELDING & RUNTIME STABILITY
# ============================================================

def test_queue_worker_unhandled_exception_shielding(tmp_path, monkeypatch):
    """
    Verify that unhandled exceptions during file ingestion
    never escape to crash the worker thread or terminate the process.
    """
    queue_db = tmp_path / "queue.db"
    doc_path = tmp_path / "crash_test.txt"
    doc_path.write_text("Corrupt crash payload", encoding="utf-8")

    def mock_exploding_ingest(file_p):
        raise RuntimeError("Catastrophic simulated parser explosion!")

    import app.ingestion.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest_file", mock_exploding_ingest)

    worker = QueueWorker(poll_interval=0.02, db_path=queue_db)
    worker.start()

    job_id = enqueue_job(doc_path, max_attempts=1, db_path=queue_db)

    # Wait for worker to handle the exploding job
    for _ in range(50):
        job = get_job(job_id, db_path=queue_db)
        if job and job["status"] in {"quarantined", "failed"}:
            break
        time.sleep(0.05)

    # Worker must still be alive and healthy despite the exception
    assert worker.is_running()
    assert worker.is_healthy()

    worker.stop(timeout=2.0)

    # Job is quarantined with explosion message preserved
    job = get_job(job_id, db_path=queue_db)
    assert job["status"] == "quarantined"
    assert "Catastrophic simulated parser explosion" in job["error_message"]

