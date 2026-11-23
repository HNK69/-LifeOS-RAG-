"""
test_milestone5_event_driven_client_and_companion.py

Deterministic test suite for LifeOS Milestone 5:
- In-memory pub/sub EventBus with privacy redaction and slow-consumer isolation.
- Server-Sent Events (SSE) streaming API with Last-Event-ID replay and heartbeat comments.
- Full-duplex WebSocket API (/ws/events) with dynamic topic filtering and ping/pong.
- Subsystem event wiring (queue, device pairing, consent updates).
- Desktop Client Companion notification dispatch.
- Typed Python Client SDK.
- Cryptographic privacy audit verification for event streaming sessions.
"""

import asyncio
import json
import uuid
from pathlib import Path
import pytest
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.events.bus import EventBus, get_event_bus, reset_event_bus
from app.events.models import LifeOSEvent
from app.client.companion import DesktopNotification, LifeOSCompanion
from app.client.sdk import LifeOSClient
from app.security.privacy_store import initialize_privacy_store
from app.security.audit import verify_audit_log_integrity, query_audit_log
from app.security.consent import grant_consent, revoke_consent
from app.security.device_store import initialize_identity_store, create_pairing_challenge
from app.ingestion.queue import init_queue_db, enqueue_job

TEST_API_KEY = "test-api-key-12345"
TEST_ADMIN_KEY = "test-admin-key-99999"


@pytest.fixture(autouse=True)
def setup_m5_environment(tmp_path, monkeypatch):
    """Isolate databases, keys, event bus, and auth in tmp_path."""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_privacy_db = test_data / "privacy.db"
    test_identity_db = test_data / "identity.db"
    test_queue_db = test_data / "queue.db"

    monkeypatch.setattr(config, "DATA_DIR", test_data)
    monkeypatch.setattr(config, "PRIVACY_DB", test_privacy_db)
    monkeypatch.setattr("app.security.privacy_store.PRIVACY_DB", test_privacy_db)
    monkeypatch.setattr(config, "IDENTITY_DB", test_identity_db)
    monkeypatch.setattr(config, "QUEUE_DB", test_queue_db)
    monkeypatch.setattr(config, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)

    initialize_privacy_store(test_privacy_db)
    initialize_identity_store(test_identity_db)
    init_queue_db(test_queue_db)
    reset_event_bus()

    yield
    reset_event_bus()


# ============================================================================
# 1. EventBus Unit Tests
# ============================================================================

def test_event_bus_publish_and_subscribe():
    async def _run():
        bus = EventBus()
        queue = bus.subscribe()

        evt = LifeOSEvent(
            event_type="test.event",
            payload={"message": "hello event bus"},
            producer="unit_test",
        )

        await bus.publish(evt)
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.event_id == evt.event_id
        assert received.payload["message"] == "hello event bus"

        bus.unsubscribe(queue)
        assert len(bus._subscribers) == 0

    asyncio.run(_run())


def test_event_bus_topic_filtering():
    async def _run():
        bus = EventBus()
        # Subscribe only to ingestion events
        queue = bus.subscribe(topics={"ingestion.*"})

        evt_ingest = LifeOSEvent(event_type="ingestion.completed", payload={"doc_id": "123"})
        evt_query = LifeOSEvent(event_type="query.progress", payload={"step": 1})

        await bus.publish(evt_query)
        await bus.publish(evt_ingest)

        # Only ingestion event should be received
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.event_type == "ingestion.completed"
        assert queue.empty()

    asyncio.run(_run())


def test_event_bus_reconnection_buffer_replay():
    bus = EventBus(buffer_size=10)

    evt1 = LifeOSEvent(event_type="t.1", payload={"n": 1})
    evt2 = LifeOSEvent(event_type="t.2", payload={"n": 2})
    evt3 = LifeOSEvent(event_type="t.3", payload={"n": 3})

    bus.publish_sync(evt1)
    bus.publish_sync(evt2)
    bus.publish_sync(evt3)

    # Request events since evt1
    replayed = bus.get_events_since(evt1.event_id)
    assert len(replayed) == 2
    assert replayed[0].event_id == evt2.event_id
    assert replayed[1].event_id == evt3.event_id


def test_event_bus_queue_overflow_isolation():
    async def _run():
        bus = EventBus()
        queue = bus.subscribe(max_queue_size=2)

        evt1 = LifeOSEvent(event_type="t.1", payload={"n": 1})
        evt2 = LifeOSEvent(event_type="t.2", payload={"n": 2})
        evt3 = LifeOSEvent(event_type="t.3", payload={"n": 3})

        await bus.publish(evt1)
        await bus.publish(evt2)
        await bus.publish(evt3)

        # Queue size should be 2 (oldest evt1 was dropped)
        assert queue.qsize() == 2
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        assert items[0].event_id == evt2.event_id
        assert items[1].event_id == evt3.event_id

    asyncio.run(_run())


def test_event_bus_privacy_consent_redaction(tmp_path):
    bus = EventBus()
    secret_file = tmp_path / "secret" / "financial_data.xlsx"

    # Before consent is granted
    evt = LifeOSEvent(
        event_type="ingestion.queued",
        payload={"file_path": str(secret_file), "size": 1024},
        resource_path=str(secret_file),
    )

    filtered = bus._filter_privacy(evt)
    assert filtered.resource_path == "[REDACTED_BY_CONSENT]"
    assert filtered.payload["file_path"] == "[REDACTED_BY_CONSENT]"

    # Grant consent for secret directory
    grant_consent(resource_path=str(tmp_path / "secret"))

    # Now the path should be retained
    filtered_consented = bus._filter_privacy(evt)
    assert filtered_consented.resource_path == str(secret_file)
    assert filtered_consented.payload["file_path"] == str(secret_file)


# ============================================================================
# 2. Server-Sent Events (SSE) Streaming API Tests
# ============================================================================

def test_sse_unauthenticated_rejected():
    with TestClient(app) as client:
        res = client.get("/api/events/sse")
        assert res.status_code == 401


def test_sse_query_token_authentication():
    with TestClient(app) as client:
        bus = get_event_bus()
        evt = LifeOSEvent(event_type="test.preloaded", payload={"test": True})
        bus.publish_sync(evt)

        # Connect with ?token= query parameter and max_events=1 to terminate stream cleanly
        res = client.get(
            f"/api/events/sse?token={TEST_API_KEY}&last_event_id=0&max_events=1&timeout=0.1",
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        assert "test.preloaded" in res.text


def test_sse_event_delivery_and_replay():
    bus = get_event_bus()
    evt1 = LifeOSEvent(event_type="test.replay1", payload={"msg": "first"})
    evt2 = LifeOSEvent(event_type="test.replay2", payload={"msg": "second"})
    bus.publish_sync(evt1)
    bus.publish_sync(evt2)

    with TestClient(app) as client:
        res = client.get(
            f"/api/events/sse?last_event_id={evt1.event_id}&max_events=1&timeout=0.1",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        assert res.status_code == 200
        body = res.text
        # evt2 should be in the stream replay
        assert "event: test.replay2" in body
        assert "evt_" in body


# ============================================================================
# 3. WebSocket API (/ws/events) Tests
# ============================================================================

def test_ws_unauthenticated_rejected():
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/events") as ws:
                ws.receive_json()


def test_ws_authenticated_handshake_and_ping_pong():
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/events?token={TEST_API_KEY}") as ws:
            # 1. Welcome handshake
            welcome = ws.receive_json()
            assert welcome["type"] == "connection.accepted"
            assert "device_id" in welcome
            assert welcome["authenticated_user"] in ("user", "admin")

            # 2. Ping-Pong
            ws.send_text(json.dumps({"action": "ping", "timestamp": 12345}))
            pong = ws.receive_json()
            assert pong["type"] == "pong"
            assert pong["timestamp"] == 12345


def test_ws_dynamic_topic_subscription():
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/events?token={TEST_API_KEY}") as ws:
            ws.receive_json()  # Consume welcome

            # Subscribe to ingestion topics only
            ws.send_text(json.dumps({"action": "subscribe", "topics": ["ingestion.*"]}))
            sub_ack = ws.receive_json()
            assert sub_ack["type"] == "subscription.updated"
            assert "ingestion.*" in sub_ack["topics"]


# ============================================================================
# 4. Desktop Client Companion Tests
# ============================================================================

def test_companion_notification_dispatch():
    companion = LifeOSCompanion()

    # 1. Ingestion completed event
    evt_completed = LifeOSEvent(
        event_type="ingestion.completed",
        payload={"job_id": "job_123"},
    )
    notif1 = companion.handle_event(evt_completed)
    assert notif1 is not None
    assert notif1.title == "Document Ingested"
    assert "job_123" in notif1.message
    assert notif1.level == "info"

    # 2. Ingestion failure event
    evt_failed = LifeOSEvent(
        event_type="ingestion.failed",
        payload={"job_id": "job_456", "error": "Disk full"},
    )
    notif2 = companion.handle_event(evt_failed)
    assert notif2 is not None
    assert notif2.title == "Ingestion Alert"
    assert notif2.level == "warning"

    # 3. Device pairing event
    evt_pair = LifeOSEvent(
        event_type="device.pairing_requested",
        payload={"peer_device_id": "peer_tablet_01"},
    )
    notif3 = companion.handle_event(evt_pair)
    assert notif3 is not None
    assert notif3.title == "Device Pairing Request"
    assert notif3.level == "action_required"

    # 4. Consent change event
    evt_consent = LifeOSEvent(
        event_type="privacy.consent_changed",
        payload={"action": "granted", "resource_path": "/vault/docs"},
    )
    notif4 = companion.handle_event(evt_consent)
    assert notif4 is not None
    assert notif4.title == "Privacy Boundary Updated"

    assert len(companion.dispatched_notifications) == 4


# ============================================================================
# 5. Subsystem Event Wiring Integration Tests
# ============================================================================

def test_subsystem_wiring_enqueue_job(tmp_path):
    bus = get_event_bus()
    doc_path = tmp_path / "test.txt"
    doc_path.write_text("Hello LifeOS queue event.")

    job_id = enqueue_job(doc_path)
    assert job_id.startswith("job_")

    events = bus.get_events_since("non_existent")
    matching = [e for e in events if e.event_type == "ingestion.queued"]
    assert len(matching) >= 1
    assert matching[-1].payload["job_id"] == job_id


def test_subsystem_wiring_consent_and_pairing(tmp_path):
    bus = get_event_bus()

    # 1. Grant consent triggers event
    grant = grant_consent(resource_path=str(tmp_path))
    events = bus.get_events_since("non_existent")
    consent_events = [e for e in events if e.event_type == "privacy.consent_changed"]
    assert len(consent_events) >= 1
    assert consent_events[-1].payload["consent_id"] == grant.consent_id

    # 2. Pairing challenge triggers event
    chal = create_pairing_challenge(peer_device_id="test_peer_99")
    events2 = bus.get_events_since("non_existent")
    pair_events = [e for e in events2 if e.event_type == "device.pairing_requested"]
    assert len(pair_events) >= 1
    assert pair_events[-1].payload["challenge_id"] == chal["challenge_id"]


# ============================================================================
# 6. Audit Trail & Cryptographic Verification
# ============================================================================

def test_streaming_sessions_audit_trail():
    with TestClient(app) as client:
        # Connect SSE with max_events=1 and timeout=0.1 so stream closes
        client.get(
            f"/api/events/sse?last_event_id=none&max_events=1&timeout=0.1",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

        # Connect WebSocket
        with client.websocket_connect(f"/ws/events?token={TEST_API_KEY}") as ws:
            ws.receive_json()

    # Verify audit entries
    is_valid, count, err = verify_audit_log_integrity()
    assert is_valid is True
    assert count >= 2
    assert err is None

    audit_records = query_audit_log(limit=50)
    event_types = [r["event_type"] for r in audit_records]
    assert "sse_connected" in event_types
    assert "ws_connected" in event_types
