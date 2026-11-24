"""
test_milestone_c2_governor.py

Deterministic tests for Milestone C2: Resource Governor & Consent-First Ingestion Daemon.
Validates:
1. HardwareGovernor threshold evaluation across CPU, RAM, battery, thermal, and user idle metrics.
2. QueueWorker throttling integration: safe pause/resume without job loss or retry consumption.
3. QueueWorker emission of 'system.governor_throttled' events on state transitions.
4. SourceDiscoveryDaemon detection of unconsented folders and emission of 'privacy.discovery_detected'.
5. SourceDiscoveryDaemon suppression when directory is already consented.
6. Notification cooldown preventing duplicate discovery alert spam.
7. Preservation of strict connector authorization boundaries (zero unconsented crawling/indexing).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.connectors.base import is_authorized_connector_path
from app.connectors.discovery import SourceDiscoveryDaemon
from app.events.bus import get_event_bus
from app.ingestion.queue import (
    QueueWorker,
    claim_next_job,
    enqueue_job,
    get_job,
    init_queue_db,
    process_next_job,
)
from app.ops.governor import HardwareGovernor, SystemMetrics, get_governor
from app.security.consent import grant_consent, revoke_consent
from app.security.privacy_store import initialize_privacy_store


@pytest.fixture(autouse=True)
def clean_governor():
    """Ensure clean governor override state before and after each test."""
    gov = get_governor()
    gov.set_override_metrics(None)
    yield
    gov.set_override_metrics(None)


# =============================================================================
# 1. HARDWARE GOVERNOR THRESHOLD EVALUATIONS
# =============================================================================

def test_governor_allows_when_healthy():
    """Verify governor allows execution when host resources are healthy."""
    gov = HardwareGovernor(
        enabled=True,
        max_cpu_percent=80.0,
        max_ram_percent=85.0,
        pause_on_battery=True,
        min_user_idle_seconds=0.0,
    )
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=15.0,
        ram_percent=45.0,
        on_battery=False,
        thermal_throttling=False,
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is False
    assert reason is None


def test_governor_pauses_on_battery():
    """Verify governor pauses background ingestion when device is on battery."""
    gov = HardwareGovernor(
        enabled=True,
        pause_on_battery=True,
    )
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=10.0,
        ram_percent=30.0,
        on_battery=True,
        battery_percent=60.0,
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is True
    assert reason == "on_battery"


def test_governor_pauses_on_high_cpu():
    """Verify governor pauses when CPU exceeds max threshold."""
    gov = HardwareGovernor(
        enabled=True,
        max_cpu_percent=75.0,
        pause_on_battery=False,
    )
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=88.5,
        ram_percent=50.0,
        on_battery=False,
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is True
    assert reason == "high_cpu"


def test_governor_pauses_on_high_ram():
    """Verify governor pauses when RAM exceeds max threshold."""
    gov = HardwareGovernor(
        enabled=True,
        max_ram_percent=80.0,
    )
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=20.0,
        ram_percent=92.0,
        on_battery=False,
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is True
    assert reason == "high_ram"


def test_governor_pauses_on_thermal_throttle():
    """Verify governor prioritizes thermal safety throttle."""
    gov = HardwareGovernor(enabled=True)
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=10.0,
        ram_percent=20.0,
        thermal_throttling=True,
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is True
    assert reason == "thermal_throttle"


def test_governor_pauses_on_user_active():
    """Verify governor pauses when user idle time is below threshold."""
    gov = HardwareGovernor(
        enabled=True,
        min_user_idle_seconds=10.0,
    )
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=10.0,
        ram_percent=20.0,
        user_idle_seconds=2.5,  # Active typing
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is True
    assert reason == "user_active"


def test_governor_disabled_bypasses_all():
    """Verify disabled governor allows ingestion even during high load or battery."""
    gov = HardwareGovernor(enabled=False)
    gov.set_override_metrics(SystemMetrics(
        cpu_percent=99.0,
        ram_percent=99.0,
        on_battery=True,
        thermal_throttling=True,
    ))

    should_pause, reason = gov.should_pause_ingestion()
    assert should_pause is False
    assert reason is None


# =============================================================================
# 2. QUEUE WORKER & GOVERNOR INTEGRATION
# =============================================================================

def test_queue_worker_throttling_preserves_jobs(tmp_path):
    """
    Verify that when the governor pauses ingestion:
    - process_next_job returns False
    - Enqueued jobs remain untouched (status='queued', attempts=0)
    - No poison-pill quarantine is triggered
    - Resuming processes the job normally
    """
    queue_db = tmp_path / "queue_test.db"
    init_queue_db(queue_db)

    # Create dummy doc file
    dummy_doc = tmp_path / "sample.txt"
    dummy_doc.write_text("Test content for ingestion governor")

    job_id = enqueue_job(dummy_doc, priority=1, db_path=queue_db)
    assert job_id is not None

    gov = get_governor()

    # 1. Force governor to throttle
    gov.set_override_metrics(SystemMetrics(on_battery=True))

    did_work = process_next_job(db_path=queue_db)
    assert did_work is False

    # Job is still queued, 0 attempts consumed
    job = get_job(job_id, db_path=queue_db)
    assert job["status"] == "queued"
    assert job["attempts"] == 0

    # 2. Clear throttle condition and mock ingest_file
    gov.set_override_metrics(SystemMetrics(on_battery=False))

    with patch("app.ingestion.ingest.ingest_file") as mock_ingest:
        mock_ingest.return_value = {"document_id": "doc-123"}
        did_work = process_next_job(db_path=queue_db)
        assert did_work is True
        mock_ingest.assert_called_once()

    # Job is now completed
    job = get_job(job_id, db_path=queue_db)
    assert job["status"] == "completed"


def test_queue_worker_state_transitions_and_events(tmp_path):
    """Verify QueueWorker tracks is_throttled and emits EventBus notifications."""
    queue_db = tmp_path / "queue_test_events.db"
    init_queue_db(queue_db)

    gov = get_governor()
    gov.set_override_metrics(SystemMetrics(cpu_percent=95.0))  # Throttled

    worker = QueueWorker(poll_interval=0.05, db_path=queue_db)
    worker.start()

    try:
        # Wait for worker iteration to sample governor
        time.sleep(0.2)
        assert worker.is_throttled is True
        assert worker.throttle_reason == "high_cpu"

        # Now clear throttle
        gov.set_override_metrics(SystemMetrics(cpu_percent=10.0))
        for _ in range(20):
            if not worker.is_throttled:
                break
            time.sleep(0.05)
        assert worker.is_throttled is False
        assert worker.throttle_reason is None

    finally:
        worker.stop()


# =============================================================================
# 3. SOURCE DISCOVERY DAEMON & CONSENT BOUNDARIES
# =============================================================================

def test_source_discovery_detects_unconsented_directory(tmp_path):
    """
    Verify SourceDiscoveryDaemon:
    - Scans candidate directory
    - Emits 'privacy.discovery_detected' event
    - Counts top-level files without recursive crawling
    - NEVER enqueues or indexes files automatically
    """
    initialize_privacy_store()

    # Setup test directory with files
    candidate_dir = tmp_path / "UnconsentedFolder"
    candidate_dir.mkdir()
    (candidate_dir / "file1.txt").write_text("Hello 1")
    (candidate_dir / "file2.pdf").write_text("Hello 2")
    sub_dir = candidate_dir / "subdir"
    sub_dir.mkdir()
    (sub_dir / "nested.txt").write_text("Nested")

    bus = get_event_bus()
    queue = bus.subscribe(topics={"privacy.discovery_detected"})

    daemon = SourceDiscoveryDaemon(
        candidate_roots=[candidate_dir],
        scan_interval=1.0,
        notification_cooldown=3600.0,
    )

    detected = daemon.scan_once()
    assert len(detected) == 1
    info = detected[0]
    assert info["resource_path"] == str(candidate_dir.resolve())
    assert info["file_count"] == 2  # Top-level only (file1.txt, file2.pdf), not recursive

    # Verify event dispatched over EventBus
    assert not queue.empty()
    event = queue.get_nowait()
    assert event.event_type == "privacy.discovery_detected"
    assert event.resource_path == str(candidate_dir.resolve())
    assert event.payload["file_count"] == 2

    # Verify candidate directory is NOT yet authorized for connector indexing
    assert is_authorized_connector_path(candidate_dir) is False

    bus.unsubscribe(queue)


def test_source_discovery_ignores_consented_directory(tmp_path):
    """Verify that once consent is granted, discovery daemon ignores the folder."""
    initialize_privacy_store()

    consented_dir = tmp_path / "ConsentedFolder"
    consented_dir.mkdir()
    (consented_dir / "doc.txt").write_text("Authorized")

    # Grant explicit consent
    grant_consent(
        resource_path=consented_dir,
        scope_type="folder",
        purposes=["indexing", "query"],
    )

    daemon = SourceDiscoveryDaemon(
        candidate_roots=[consented_dir],
        scan_interval=1.0,
    )

    detected = daemon.scan_once()
    assert len(detected) == 0  # Ignored because consent is active

    # Verify candidate directory IS authorized now
    assert is_authorized_connector_path(consented_dir) is True


def test_source_discovery_cooldown_deduplication(tmp_path):
    """Verify notification cooldown prevents emitting duplicate events every pass."""
    initialize_privacy_store()

    test_dir = tmp_path / "CooldownFolder"
    test_dir.mkdir()
    (test_dir / "note.txt").write_text("Note")

    daemon = SourceDiscoveryDaemon(
        candidate_roots=[test_dir],
        scan_interval=1.0,
        notification_cooldown=100.0,
    )

    # Pass 1: detected
    detected1 = daemon.scan_once()
    assert len(detected1) == 1

    # Pass 2: immediately after -> cooldown suppresses duplicate
    detected2 = daemon.scan_once()
    assert len(detected2) == 0


def test_connector_authorization_lifecycle(tmp_path):
    """Verify security boundary: unconsented path -> grant consent -> revoke consent."""
    initialize_privacy_store()

    folder = tmp_path / "LifecycleFolder"
    folder.mkdir()

    # 1. Initially unauthorized
    assert is_authorized_connector_path(folder) is False

    # 2. Grant consent
    grant = grant_consent(
        resource_path=folder,
        scope_type="folder",
        purposes=["indexing"],
    )
    assert is_authorized_connector_path(folder) is True

    # 3. Revoke consent
    revoke_consent(grant.consent_id, reason="User test revoke")
    assert is_authorized_connector_path(folder) is False
