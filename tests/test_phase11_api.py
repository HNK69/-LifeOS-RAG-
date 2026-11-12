"""
test_phase11_api.py

Comprehensive deterministic test suite for LifeOS Phase 11 — Product / API / Security Layer.
Tests:
- Authentication & Authorization (API keys, Admin keys, 401, 403)
- Query endpoint with session isolation and error shielding
- Document upload, validation, listing, and deletion
- Context CRUD operations
- People endpoints and privacy (no face vectors)
- Administrative reindex lifecycle & 409 conflict
- Health and readiness probes
- Security headers, CORS, request IDs, rate limiting, and size limits
"""

import io
import json
import uuid
import pytest
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.api.middleware import rate_limiter

TEST_API_KEY = "test-secret-user-key-12345"
TEST_ADMIN_KEY = "test-secret-admin-key-67890"


@pytest.fixture(autouse=True)
def configure_api_env(monkeypatch):
    """Ensure predictable API keys and rate limit state for tests."""
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_RATE_LIMIT", 100)
    monkeypatch.setattr(config, "API_RATE_WINDOW_SECONDS", 60)
    rate_limiter.limit = 100
    rate_limiter.window_seconds = 60
    rate_limiter.reset()


@pytest.fixture
def client():
    """TestClient instance for API tests."""
    return TestClient(app)


# ============================================================
# 1. AUTHENTICATION & AUTHORIZATION
# ============================================================

def test_missing_authentication_returns_401(client):
    res = client.get("/api/documents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_invalid_api_key_returns_401(client):
    res = client.get(
        "/api/documents",
        headers={"Authorization": "Bearer invalid-wrong-key"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_malformed_authorization_header_returns_401(client):
    res = client.get(
        "/api/documents",
        headers={"Authorization": "NotBearer some-key"},
    )
    assert res.status_code == 401
    assert "Malformed Authorization header" in res.json()["error"]["message"]


def test_valid_user_key_authenticates_successfully(client):
    res = client.get(
        "/api/documents",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 200
    assert "documents" in res.json()


def test_x_api_key_header_authenticates_successfully(client):
    res = client.get(
        "/api/documents",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert res.status_code == 200


def test_admin_operation_forbidden_for_normal_user(client):
    # Triggering reindex requires admin privileges
    res = client.post(
        "/api/admin/reindex",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"


def test_admin_operation_allowed_for_admin_key(client, monkeypatch):
    import app.api.routes.admin as admin_route
    monkeypatch.setattr(admin_route, "is_reindexing_active", lambda: False)
    monkeypatch.setattr(
        admin_route,
        "reindex",
        lambda *args, **kwargs: {
            "success": True,
            "processed_count": 5,
            "error_count": 0,
            "duration_seconds": 1.2,
        },
    )

    res = client.post(
        "/api/admin/reindex",
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["processed_count"] == 5


# ============================================================
# 2. QUERY ENDPOINT & SESSION ISOLATION
# ============================================================

def test_query_validation_rejects_empty_query(client):
    res = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"query": ""},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


def test_query_validation_rejects_whitespace_query(client):
    res = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"query": "   \n\t  "},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"
    assert "whitespace" in res.json()["error"]["message"]


def test_query_validation_rejects_oversized_query(client):
    res = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"query": "A" * 5000},
    )
    assert res.status_code == 422


def test_query_success_and_request_id_propagation(client, monkeypatch):
    from app import main

    def fake_handle_query(query, session=None):
        if session is not None:
            session["last_result"] = type("Result", (), {"answer_type": "time", "data": {"time": "12:00"}})()
        return "It is 12:00 PM."

    monkeypatch.setattr(main, "handle_query", fake_handle_query)

    res = client.post(
        "/api/query",
        headers={
            "Authorization": f"Bearer {TEST_API_KEY}",
            "X-Request-ID": "custom-req-id-1234",
            "X-Session-ID": "session-abc",
        },
        json={"query": "What time is it?"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["answer"] == "It is 12:00 PM."
    assert data["answer_type"] == "time"
    assert data["session_id"] == "session-abc"
    assert data["request_id"] == "custom-req-id-1234"
    assert res.headers["X-Request-ID"] == "custom-req-id-1234"


def test_query_session_isolation_across_clients(client, monkeypatch):
    """Verify concurrent sessions maintain separate session state."""
    from app import main

    def stateful_handle_query(query, session=None):
        count = session.get("query_count", 0) + 1 if session is not None else 1
        if session is not None:
            session["query_count"] = count
            session["last_result"] = type("Result", (), {"answer_type": "test", "data": {"count": count}})()
        return f"Count: {count}"

    monkeypatch.setattr(main, "handle_query", stateful_handle_query)

    # Session 1 - request 1
    res1_1 = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}", "X-Session-ID": "user-session-1"},
        json={"query": "query 1"},
    )
    assert res1_1.json()["answer"] == "Count: 1"

    # Session 2 - request 1 (should be 1, not 2)
    res2_1 = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}", "X-Session-ID": "user-session-2"},
        json={"query": "query 1"},
    )
    assert res2_1.json()["answer"] == "Count: 1"

    # Session 1 - request 2 (should advance to 2)
    res1_2 = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}", "X-Session-ID": "user-session-1"},
        json={"query": "query 2"},
    )
    assert res1_2.json()["answer"] == "Count: 2"

    # Session 3 - via body payload (no X-Session-ID header)
    res3_1 = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"query": "query 1", "session_id": "body-session-3"},
    )
    assert res3_1.json()["answer"] == "Count: 1"
    assert res3_1.json()["session_id"] == "body-session-3"

    res3_2 = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"query": "query 2", "session_id": "body-session-3"},
    )
    assert res3_2.json()["answer"] == "Count: 2"


# ============================================================
# 3. DOCUMENTS ENDPOINT
# ============================================================

def test_document_list_and_upload_lifecycle(client, monkeypatch):
    import app.api.routes.documents as doc_route

    monkeypatch.setattr(
        doc_route,
        "ingest_file",
        lambda p: {"status": "success", "chunks": 4},
    )

    fake_file_content = b"This is a test document for API upload."
    res = client.post(
        "/api/documents",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        files={"file": ("test_upload_doc.txt", io.BytesIO(fake_file_content), "text/plain")},
    )
    assert res.status_code == 201
    assert res.json()["status"] == "ingested"
    assert res.json()["chunks"] == 4


def test_document_upload_rejects_unsupported_extension(client):
    res = client.post(
        "/api/documents",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        files={"file": ("malicious.exe", io.BytesIO(b"binary"), "application/octet-stream")},
    )
    assert res.status_code == 400
    assert "Unsupported file format" in res.json()["error"]["message"]


def test_document_delete_requires_admin(client):
    res = client.delete(
        "/api/documents/some_file.txt",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 403


def test_document_delete_success_with_admin(client, monkeypatch):
    import app.api.routes.documents as doc_route

    # Create dummy file
    target = config.DOCUMENTS_DIR / "dummy_delete.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("to delete", encoding="utf-8")

    monkeypatch.setattr(doc_route, "remove_file", lambda p: True)

    res = client.delete(
        "/api/documents/dummy_delete.txt",
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"


# ============================================================
# 4. CONTEXT ENDPOINTS
# ============================================================

def test_context_crud_endpoints(client):
    # 1. Set context
    set_res = client.post(
        "/api/context",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={
            "key": "api_test_project",
            "value": "LifeOS RAG System",
            "context_type": "project",
        },
    )
    assert set_res.status_code == 200
    assert set_res.json()["status"] == "success"

    # 2. Get context list
    get_res = client.get(
        "/api/context",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert get_res.status_code == 200
    assert "api_test_project" in get_res.json()["context"]

    # 3. Delete context
    del_res = client.delete(
        "/api/context/api_test_project",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"

    # 4. Delete nonexistent key returns 404
    del_missing = client.delete(
        "/api/context/api_test_project",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert del_missing.status_code == 404

    # 5. Setting whitespace key returns 422
    ws_res = client.post(
        "/api/context",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={
            "key": "   ",
            "value": "invalid",
        },
    )
    assert ws_res.status_code == 422
    assert "whitespace" in ws_res.json()["error"]["message"]


# ============================================================
# 5. PEOPLE ENDPOINTS & PRIVACY
# ============================================================

def test_people_endpoint_never_exposes_embeddings(client, monkeypatch):
    import app.api.routes.people as people_route

    monkeypatch.setattr(
        people_route,
        "get_all_people",
        lambda: [
            {
                "person_id": "p123",
                "label": "Alice",
                "status": "known",
                "is_user": False,
                "created_at": "2026-11-12T00:00:00Z",
                "_embedding_json": "[0.12, 0.45, 0.78]",  # internal field
            }
        ],
    )

    res = client.get(
        "/api/people",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["people"]) == 1
    person = data["people"][0]
    assert person["person_id"] == "p123"
    assert person["label"] == "Alice"
    assert "_embedding_json" not in person
    assert "embedding" not in person


def test_people_confirm_identity(client, monkeypatch):
    import app.api.routes.people as people_route

    monkeypatch.setattr(
        people_route,
        "get_person",
        lambda pid: {"person_id": pid, "label": None, "status": "unknown"} if pid == "valid-pid" else None,
    )
    monkeypatch.setattr(people_route, "confirm_person_identity", lambda person_id, label: None)

    # Confirm valid person
    res = client.post(
        "/api/people/valid-pid/confirm",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"name": "Professor David"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"
    assert res.json()["label"] == "Professor David"

    # Confirm missing person returns 404
    res_404 = client.post(
        "/api/people/missing-pid/confirm",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"name": "Ghost"},
    )
    assert res_404.status_code == 404

    # Confirm whitespace name returns 422
    res_ws = client.post(
        "/api/people/valid-pid/confirm",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"name": "   "},
    )
    assert res_ws.status_code == 422
    assert "whitespace" in res_ws.json()["error"]["message"]


# ============================================================
# 6. ADMIN REINDEX LIFECYCLE & CONFLICTS
# ============================================================

def test_admin_reindex_conflict_when_already_running(client, monkeypatch):
    import app.api.routes.admin as admin_route

    monkeypatch.setattr(admin_route, "is_reindexing_active", lambda: True)

    res = client.post(
        "/api/admin/reindex",
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "conflict"


def test_admin_reindex_status(client):
    res = client.get(
        "/api/admin/reindex/status",
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert res.status_code == 200
    assert "is_active" in res.json()
    assert res.json()["status"] in {"idle", "running"}


# ============================================================
# 7. HEALTH & READINESS (ZERO LLM / ZERO MODEL SIDE EFFECTS)
# ============================================================

def test_health_check_returns_200(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_readiness_check_verifies_storage(client):
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"
    checks = res.json()["checks"]
    assert checks["data_directory"] is True
    assert checks["documents_directory"] is True
    assert checks["sqlite_storage"] is True


# ============================================================
# 8. SECURITY HEADERS & CORS
# ============================================================

def test_security_headers_injected(client):
    res = client.get("/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in res.headers["Cache-Control"]
    assert "X-Request-ID" in res.headers


def test_cors_options_preflight(client):
    res = client.options(
        "/api/query",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ============================================================
# 9. RATE LIMITING & RETRY-AFTER
# ============================================================

def test_rate_limiting_enforcement(client, monkeypatch):
    # Set limit to 3 requests per 60s
    monkeypatch.setattr(config, "API_RATE_LIMIT", 3)
    monkeypatch.setattr(config, "API_RATE_WINDOW_SECONDS", 60)
    rate_limiter.limit = 3
    rate_limiter.window_seconds = 60
    rate_limiter.reset()

    headers = {"Authorization": f"Bearer {TEST_API_KEY}"}

    # Requests 1, 2, 3 should succeed
    for _ in range(3):
        res = client.get("/api/documents", headers=headers)
        assert res.status_code == 200

    # Request 4 should be rate limited (429)
    res_blocked = client.get("/api/documents", headers=headers)
    assert res_blocked.status_code == 429
    assert res_blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in res_blocked.headers
    assert int(res_blocked.headers["Retry-After"]) > 0


def test_rate_limiting_enforcement_via_x_api_key(client, monkeypatch):
    monkeypatch.setattr(config, "API_RATE_LIMIT", 2)
    monkeypatch.setattr(config, "API_RATE_WINDOW_SECONDS", 60)
    rate_limiter.limit = 2
    rate_limiter.window_seconds = 60
    rate_limiter.reset()

    headers = {"X-API-Key": TEST_API_KEY}
    assert client.get("/api/documents", headers=headers).status_code == 200
    assert client.get("/api/documents", headers=headers).status_code == 200

    res_blocked = client.get("/api/documents", headers=headers)
    assert res_blocked.status_code == 429
    assert res_blocked.json()["error"]["code"] == "rate_limit_exceeded"


# ============================================================
# 10. ERROR SHIELDING (ZERO LEAKAGE OF STACK TRACES OR SECRETS)
# ============================================================

def test_internal_error_shields_stack_traces_and_secrets(monkeypatch):
    from app import main

    def exploding_handler(query, session=None):
        raise RuntimeError(f"Database error at C:/sensitive/path with secret key {TEST_API_KEY}")

    monkeypatch.setattr(main, "handle_query", exploding_handler)

    safe_client = TestClient(app, raise_server_exceptions=False)
    res = safe_client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        json={"query": "trigger error"},
    )
    assert res.status_code == 500
    err_json = res.json()["error"]
    assert err_json["code"] == "internal_server_error"
    # Never leak sensitive internal path or secret
    assert "sensitive" not in err_json["message"]
    assert TEST_API_KEY not in err_json["message"]
    assert "traceback" not in res.text.lower()
