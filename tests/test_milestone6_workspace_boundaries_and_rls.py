"""
test_milestone6_workspace_boundaries_and_rls.py

Deterministic test suite for Milestone 6:
Multi-Profile Workspace Boundaries & Row-Level Authorization.

Tests cover:
1. Workspace profile persistence, retrieval, updating, and default workspace protection.
2. Membership management and Role-Level Authorization (owner > admin > member > viewer).
3. Storage-layer document registry isolation (workspace_id filtering).
4. Storage & Retrieval-layer ChromaDB vector chunk isolation (where={"workspace_id": ...}).
5. Context store partition isolation across workspaces without key collisions.
6. Ingestion queue workspace parameter persistence.
7. API endpoints (/api/workspaces, /api/documents, /api/context, /api/query) and RLS enforcement.
8. Header (X-Workspace-ID) resolution, unauthorized access rejection (403), and default workspace backward compatibility.
"""

from __future__ import annotations

import sys
from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.config import DEFAULT_WORKSPACE_ID
from app.context.context_store import (
    delete_context,
    get_all_context,
    get_context,
    set_context,
)
from app.knowledge.document_registry import (
    get_all_documents,
    initialize_registry,
    mark_success,
    search_documents,
)
from app.workspace.models import WorkspaceMembership, WorkspaceProfile
from app.workspace.store import (
    add_workspace_member,
    create_workspace,
    delete_workspace,
    get_workspace,
    get_workspace_membership,
    init_workspace_store,
    is_user_authorized_for_workspace,
    list_workspace_members,
    list_workspaces_for_user,
    remove_workspace_member,
    update_workspace,
)


@pytest.fixture
def workspace_temp_db(tmp_path: Path):
    """Isolated SQLite database for workspace tests."""
    db_file = tmp_path / "workspace_test.db"
    init_workspace_store(db_file)
    return db_file


@pytest.fixture
def doc_temp_env(tmp_path: Path, monkeypatch):
    """Isolated SQLite database and files for document registry tests."""
    db_file = tmp_path / "document_registry_test.db"
    import config
    monkeypatch.setattr(config, "REGISTRY_DB", db_file)
    for mod in ["knowledge.document_registry", "app.knowledge.document_registry"]:
        if mod in sys.modules:
            monkeypatch.setattr(sys.modules[mod], "REGISTRY_DB", db_file)
    initialize_registry()
    return tmp_path


@pytest.fixture
def context_temp_db(tmp_path: Path, monkeypatch):
    """Isolated SQLite database for context store tests."""
    db_file = tmp_path / "context_test.db"
    import config
    monkeypatch.setattr(config, "CONTEXT_DB", db_file)
    for mod in ["context.context_store", "app.context.context_store"]:
        if mod in sys.modules:
            monkeypatch.setattr(sys.modules[mod], "CONTEXT_DB", db_file)
    from app.context.context_store import initialize_context_store
    initialize_context_store()
    return db_file


# ============================================================
# 1. Workspace Profile Persistence & Lifecycles
# ============================================================

def test_workspace_initialization_seeds_default(workspace_temp_db: Path):
    """Verifies that init_workspace_store seeds the canonical default workspace."""
    default_ws = get_workspace(DEFAULT_WORKSPACE_ID, db_path=workspace_temp_db)
    assert default_ws is not None
    assert default_ws.workspace_id == "default"
    assert default_ws.profile_type == "personal"
    assert default_ws.is_active is True

    # Memberships for default users should be seeded
    members = list_workspace_members("default", db_path=workspace_temp_db)
    member_ids = {m.user_id for m in members}
    assert "default_user" in member_ids
    assert "admin" in member_ids


def test_create_and_get_workspace(workspace_temp_db: Path):
    """Verifies custom workspace creation and retrieval."""
    ws = create_workspace(
        name="Research Lab",
        profile_type="research",
        description="Confidential AI research projects",
        workspace_id="ws_research_01",
        owner_id="alice",
        settings={"retention_days": 90},
        db_path=workspace_temp_db,
    )
    assert ws.workspace_id == "ws_research_01"
    assert ws.name == "Research Lab"
    assert ws.settings.get("retention_days") == 90

    retrieved = get_workspace("ws_research_01", db_path=workspace_temp_db)
    assert retrieved is not None
    assert retrieved.name == "Research Lab"
    assert retrieved.profile_type == "research"
    assert retrieved.settings == {"retention_days": 90}

    # Verify alice is registered as owner
    membership = get_workspace_membership("ws_research_01", "alice", db_path=workspace_temp_db)
    assert membership is not None
    assert membership.role == "owner"


def test_update_workspace(workspace_temp_db: Path):
    """Verifies updating workspace attributes."""
    create_workspace(
        name="Initial Name",
        profile_type="personal",
        workspace_id="ws_updatable",
        owner_id="alice",
        db_path=workspace_temp_db,
    )

    updated = update_workspace(
        workspace_id="ws_updatable",
        name="Renamed Workspace",
        description="Updated description",
        settings={"color": "blue"},
        db_path=workspace_temp_db,
    )
    assert updated is not None
    assert updated.name == "Renamed Workspace"
    assert updated.description == "Updated description"
    assert updated.settings == {"color": "blue"}

    # Confirm persistence
    fetched = get_workspace("ws_updatable", db_path=workspace_temp_db)
    assert fetched.name == "Renamed Workspace"


def test_delete_default_workspace_protection(workspace_temp_db: Path):
    """Verifies that the canonical default workspace cannot be deleted."""
    deleted = delete_workspace("default", db_path=workspace_temp_db)
    assert deleted is False
    default_ws = get_workspace("default", db_path=workspace_temp_db)
    assert default_ws is not None


def test_delete_custom_workspace(workspace_temp_db: Path):
    """Verifies deletion of a custom workspace cascades memberships."""
    create_workspace(
        name="Temporary",
        workspace_id="ws_temp",
        owner_id="bob",
        db_path=workspace_temp_db,
    )
    assert get_workspace("ws_temp", db_path=workspace_temp_db) is not None

    deleted = delete_workspace("ws_temp", db_path=workspace_temp_db)
    assert deleted is True
    assert get_workspace("ws_temp", db_path=workspace_temp_db) is None
    assert list_workspace_members("ws_temp", db_path=workspace_temp_db) == []


# ============================================================
# 2. Membership Management & Role-Based Row-Level Authorization
# ============================================================

def test_membership_and_role_authorization_hierarchy(workspace_temp_db: Path):
    """
    Verifies role hierarchy: owner (4) > admin (3) > member (2) > viewer (1).
    """
    ws = create_workspace(name="Finance Workspace", workspace_id="ws_finance", owner_id="cfo", db_path=workspace_temp_db)

    # Add users with different roles
    add_workspace_member("ws_finance", "director", role="admin", db_path=workspace_temp_db)
    add_workspace_member("ws_finance", "analyst", role="member", db_path=workspace_temp_db)
    add_workspace_member("ws_finance", "auditor", role="viewer", db_path=workspace_temp_db)

    # 1. Owner authorization
    assert is_user_authorized_for_workspace("cfo", "ws_finance", required_role="viewer", db_path=workspace_temp_db)
    assert is_user_authorized_for_workspace("cfo", "ws_finance", required_role="member", db_path=workspace_temp_db)
    assert is_user_authorized_for_workspace("cfo", "ws_finance", required_role="admin", db_path=workspace_temp_db)
    assert is_user_authorized_for_workspace("cfo", "ws_finance", required_role="owner", db_path=workspace_temp_db)

    # 2. Admin authorization
    assert is_user_authorized_for_workspace("director", "ws_finance", required_role="viewer", db_path=workspace_temp_db)
    assert is_user_authorized_for_workspace("director", "ws_finance", required_role="member", db_path=workspace_temp_db)
    assert is_user_authorized_for_workspace("director", "ws_finance", required_role="admin", db_path=workspace_temp_db)
    assert not is_user_authorized_for_workspace("director", "ws_finance", required_role="owner", db_path=workspace_temp_db)

    # 3. Member authorization
    assert is_user_authorized_for_workspace("analyst", "ws_finance", required_role="viewer", db_path=workspace_temp_db)
    assert is_user_authorized_for_workspace("analyst", "ws_finance", required_role="member", db_path=workspace_temp_db)
    assert not is_user_authorized_for_workspace("analyst", "ws_finance", required_role="admin", db_path=workspace_temp_db)
    assert not is_user_authorized_for_workspace("analyst", "ws_finance", required_role="owner", db_path=workspace_temp_db)

    # 4. Viewer authorization
    assert is_user_authorized_for_workspace("auditor", "ws_finance", required_role="viewer", db_path=workspace_temp_db)
    assert not is_user_authorized_for_workspace("auditor", "ws_finance", required_role="member", db_path=workspace_temp_db)
    assert not is_user_authorized_for_workspace("auditor", "ws_finance", required_role="admin", db_path=workspace_temp_db)

    # 5. Non-member access
    assert not is_user_authorized_for_workspace("outsider", "ws_finance", required_role="viewer", db_path=workspace_temp_db)

    # 6. System admin bypass
    assert is_user_authorized_for_workspace("any_user", "ws_finance", required_role="owner", is_admin=True, db_path=workspace_temp_db)


def test_remove_workspace_member(workspace_temp_db: Path):
    """Verifies removing a member revokes their access."""
    create_workspace(name="Team A", workspace_id="ws_team_a", owner_id="alice", db_path=workspace_temp_db)
    add_workspace_member("ws_team_a", "bob", role="member", db_path=workspace_temp_db)

    assert is_user_authorized_for_workspace("bob", "ws_team_a", required_role="viewer", db_path=workspace_temp_db)

    removed = remove_workspace_member("ws_team_a", "bob", db_path=workspace_temp_db)
    assert removed is True
    assert not is_user_authorized_for_workspace("bob", "ws_team_a", required_role="viewer", db_path=workspace_temp_db)


def test_list_workspaces_for_user(workspace_temp_db: Path):
    """Verifies listing workspaces returns only workspaces the user has membership in."""
    create_workspace(name="Alpha", workspace_id="ws_alpha", owner_id="alice", db_path=workspace_temp_db)
    create_workspace(name="Beta", workspace_id="ws_beta", owner_id="bob", db_path=workspace_temp_db)

    # Alice should see 'default' and 'ws_alpha'
    alice_workspaces = list_workspaces_for_user("alice", is_admin=False, db_path=workspace_temp_db)
    alice_ws_ids = {w.workspace_id for w in alice_workspaces}
    assert "ws_alpha" in alice_ws_ids
    assert "ws_beta" not in alice_ws_ids

    # Admin should see all workspaces
    admin_workspaces = list_workspaces_for_user("admin", is_admin=True, db_path=workspace_temp_db)
    admin_ws_ids = {w.workspace_id for w in admin_workspaces}
    assert "default" in admin_ws_ids
    assert "ws_alpha" in admin_ws_ids
    assert "ws_beta" in admin_ws_ids


# ============================================================
# 3. Storage-Layer Document Registry Isolation
# ============================================================

def test_document_registry_workspace_row_level_isolation(doc_temp_env: Path):
    """
    Verifies that documents registered in workspace A are not visible
    when querying documents for workspace B in document_registry.db.
    """
    file_legal = doc_temp_env / "contract.pdf"
    file_legal.write_text("Confidential Non-Disclosure Agreement", encoding="utf-8")

    file_eng = doc_temp_env / "architecture.pdf"
    file_eng.write_text("System Architecture and Microservices", encoding="utf-8")

    # Register document in ws_legal
    mark_success(
        file_path=file_legal,
        file_hash="hash_contract",
        chunk_count=2,
        workspace_id="ws_legal",
        owner_id="lawyer",
    )

    # Register document in ws_engineering
    mark_success(
        file_path=file_eng,
        file_hash="hash_arch",
        chunk_count=3,
        workspace_id="ws_engineering",
        owner_id="engineer",
    )

    # Query ws_legal
    legal_docs = get_all_documents(workspace_id="ws_legal")
    assert len(legal_docs) == 1
    assert legal_docs[0]["filename"] == "contract.pdf"
    assert legal_docs[0]["workspace_id"] == "ws_legal"
    assert legal_docs[0]["owner_id"] == "lawyer"

    # Query ws_engineering
    eng_docs = get_all_documents(workspace_id="ws_engineering")
    assert len(eng_docs) == 1
    assert eng_docs[0]["filename"] == "architecture.pdf"
    assert eng_docs[0]["workspace_id"] == "ws_engineering"

    # Search isolated by workspace
    search_legal = search_documents("architecture", workspace_id="ws_legal")
    assert len(search_legal) == 0

    search_eng = search_documents("architecture", workspace_id="ws_engineering")
    assert len(search_eng) == 1
    assert search_eng[0]["filename"] == "architecture.pdf"


# ============================================================
# 4. Storage & Retrieval Vector Chunk Isolation
# ============================================================

def test_retriever_vector_workspace_filtering(monkeypatch):
    """
    Verifies that retrieve_chunks applies where={'workspace_id': ...} to ChromaDB queries.
    """
    import numpy as np
    import app.retrieval.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "generate_embeddings", lambda _: [np.zeros(128)])

    captured_kwargs = []

    class MockCollection:
        def query(self, **kwargs):
            captured_kwargs.append(kwargs)
            where = kwargs.get("where")
            if where and where.get("workspace_id") == "ws_finance":
                return {
                    "documents": [["Confidential Q3 Financial Summary"]],
                    "metadatas": [[{"source": "q3_report.pdf", "workspace_id": "ws_finance"}]],
                    "distances": [[0.1]],
                }
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    monkeypatch.setattr(retriever_mod, "collection", MockCollection())

    # Query with workspace_id="ws_finance"
    results = retriever_mod.retrieve_chunks("financial report", top_k=3, workspace_id="ws_finance")
    assert len(results) == 1
    assert results[0]["source"] == "q3_report.pdf"
    assert results[0]["workspace_id"] == "ws_finance"
    assert captured_kwargs[-1].get("where") == {"workspace_id": "ws_finance"}

    # Query with default workspace
    results_default = retriever_mod.retrieve_chunks("financial report", top_k=3, workspace_id="default")
    assert len(results_default) == 0
    assert captured_kwargs[-1].get("where") == {"workspace_id": "default"}


# ============================================================
# 5. Storage-Layer Context Store Partition Isolation
# ============================================================

def test_context_store_workspace_partitioning(context_temp_db: Path):
    """
    Verifies that personal context items are strictly partitioned by workspace_id,
    preventing cross-workspace leaks and key collisions.
    """
    # Set identical key 'active_project' with different values across workspaces
    set_context(
        key="active_project",
        value="Project Apollo",
        workspace_id="ws_alpha",
    )
    set_context(
        key="active_project",
        value="Project Zephyr",
        workspace_id="ws_beta",
    )

    # Retrieve from ws_alpha
    val_alpha = get_context("active_project", workspace_id="ws_alpha")
    assert val_alpha == "Project Apollo"

    # Retrieve from ws_beta
    val_beta = get_context("active_project", workspace_id="ws_beta")
    assert val_beta == "Project Zephyr"

    # Retrieve from default (should be absent)
    val_default = get_context("active_project", workspace_id="default")
    assert val_default is None

    # Verify list isolation
    alpha_items = get_all_context(workspace_id="ws_alpha")
    assert len(alpha_items) == 1
    assert "active_project" in alpha_items
    assert alpha_items["active_project"]["value"] == "Project Apollo"

    beta_items = get_all_context(workspace_id="ws_beta")
    assert len(beta_items) == 1
    assert "active_project" in beta_items
    assert beta_items["active_project"]["value"] == "Project Zephyr"

    # Delete in ws_alpha does not delete in ws_beta
    deleted = delete_context("active_project", workspace_id="ws_alpha")
    assert deleted is True
    assert get_context("active_project", workspace_id="ws_alpha") is None
    assert get_context("active_project", workspace_id="ws_beta") == "Project Zephyr"


# ============================================================
# 6. Ingestion Queue Workspace Parameter Persistence
# ============================================================

def test_queue_workspace_propagation(tmp_path: Path):
    """Verifies that enqueue_job records workspace_id in queue.db."""
    from app.ingestion.queue import enqueue_job, get_job, init_queue_db

    db_path = tmp_path / "queue_test.db"
    init_queue_db(db_path)

    job_id = enqueue_job(
        file_path="C:/docs/sample.txt",
        workspace_id="ws_custom_queue",
        db_path=db_path,
    )
    job = get_job(job_id, db_path=db_path)
    assert job is not None
    assert job["workspace_id"] == "ws_custom_queue"


# ============================================================
# 7. API Routes and Row-Level Authorization
# ============================================================

def test_api_workspace_lifecycle_and_rls(tmp_path: Path, monkeypatch):
    """
    Tests /api/workspaces endpoints:
    - List accessible workspaces.
    - Create new workspace profile.
    - Reject unauthorized access (403).
    - Protect default workspace against deletion (400).
    - Membership management endpoints.
    """
    ws_db = tmp_path / "api_ws.db"
    init_workspace_store(ws_db)

    # Configure store to point to isolated test db
    import config
    monkeypatch.setattr(config, "WORKSPACE_DB", ws_db)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "test-user-key")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "test-admin-key")
    import app.workspace.store as store_mod
    monkeypatch.setattr(store_mod, "WORKSPACE_DB", ws_db)

    app = create_app()
    client = TestClient(app)

    auth_alice = {"Authorization": "Bearer test-user-key", "X-User-ID": "alice"}
    auth_bob = {"Authorization": "Bearer test-user-key", "X-User-ID": "bob"}
    auth_admin = {"Authorization": "Bearer test-admin-key", "X-User-ID": "admin"}

    # 1. List workspaces for authenticated user
    resp = client.get("/api/workspaces", headers=auth_alice)
    assert resp.status_code == 200
    workspaces = resp.json()
    assert isinstance(workspaces, list)

    # 2. Create workspace as alice
    create_payload = {
        "name": "Alice Private Space",
        "profile_type": "personal",
        "description": "Alice's isolated notes",
        "settings": {"theme": "dark"},
    }
    create_resp = client.post(
        "/api/workspaces",
        json=create_payload,
        headers=auth_alice,
    )
    assert create_resp.status_code == 201
    created_data = create_resp.json()
    ws_id = created_data["workspace_id"]
    assert created_data["name"] == "Alice Private Space"

    # 3. Alice can retrieve her workspace
    alice_get = client.get(f"/api/workspaces/{ws_id}", headers=auth_alice)
    assert alice_get.status_code == 200
    assert alice_get.json()["workspace_id"] == ws_id

    def _err(r):
        return r.json().get("detail") or r.json().get("error", {}).get("message", "")

    # 4. Bob (unauthorized non-member) is rejected with 403 Forbidden
    bob_get = client.get(f"/api/workspaces/{ws_id}", headers=auth_bob)
    assert bob_get.status_code == 403
    assert "not authorized" in _err(bob_get)

    # 5. Add Bob as member to the workspace
    add_member_resp = client.post(
        f"/api/workspaces/{ws_id}/members",
        json={"user_id": "bob", "role": "viewer"},
        headers=auth_alice,
    )
    assert add_member_resp.status_code == 201
    assert add_member_resp.json()["user_id"] == "bob"
    assert add_member_resp.json()["role"] == "viewer"

    # 6. Now Bob can view the workspace
    bob_get_now = client.get(f"/api/workspaces/{ws_id}", headers=auth_bob)
    assert bob_get_now.status_code == 200

    # 7. Bob (viewer) cannot update the workspace (requires admin/owner)
    bob_update = client.put(
        f"/api/workspaces/{ws_id}",
        json={"name": "Hacked Name"},
        headers=auth_bob,
    )
    assert bob_update.status_code == 403

    # 8. Deleting 'default' workspace returns 400 Bad Request
    del_default = client.delete("/api/workspaces/default", headers=auth_admin)
    assert del_default.status_code == 400
    assert "Cannot delete the default workspace" in _err(del_default)

    # 9. Alice (owner) deletes her workspace
    del_alice = client.delete(f"/api/workspaces/{ws_id}", headers=auth_alice)
    assert del_alice.status_code == 200
    assert del_alice.json()["deleted"] is True


# ============================================================
# 8. Header (X-Workspace-ID) and Backward Compatibility
# ============================================================

def test_api_header_workspace_resolution_and_default_fallback(tmp_path: Path, monkeypatch):
    """
    Verifies that requests without X-Workspace-ID default to 'default',
    while valid custom X-Workspace-ID headers are respected and unauthorized ones rejected.
    """
    ws_db = tmp_path / "hdr_ws.db"
    init_workspace_store(ws_db)

    import config
    monkeypatch.setattr(config, "WORKSPACE_DB", ws_db)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "test-user-key")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "test-admin-key")
    import app.workspace.store as store_mod
    monkeypatch.setattr(store_mod, "WORKSPACE_DB", ws_db)

    # Create workspace owned by charlie
    create_workspace(name="Charlie Space", workspace_id="ws_charlie", owner_id="charlie", db_path=ws_db)

    app = create_app()
    client = TestClient(app)

    # 1. Unparameterized request defaults to 'default' workspace (200 OK)
    resp_default = client.get(
        "/api/documents",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "default_user"},
    )
    assert resp_default.status_code == 200

    # 2. Charlie specifying X-Workspace-ID: ws_charlie succeeds (200 OK)
    resp_charlie = client.get(
        "/api/documents",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "charlie", "X-Workspace-ID": "ws_charlie"},
    )
    assert resp_charlie.status_code == 200

    # 3. Dave specifying X-Workspace-ID: ws_charlie without membership fails with 403 Forbidden
    resp_dave = client.get(
        "/api/documents",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "dave", "X-Workspace-ID": "ws_charlie"},
    )
    assert resp_dave.status_code == 403
    err_text = resp_dave.json().get("detail") or resp_dave.json().get("error", {}).get("message", "")
    assert "Forbidden" in err_text or "not have access" in err_text


def test_api_query_workspace_body_authorization_and_header(tmp_path: Path, monkeypatch):
    """
    Verifies POST /api/query workspace authorization when workspace_id is sent in the
    request body (with or without X-Workspace-ID header), including admin bypass and
    non-existent/unauthorized workspace rejection (403, never 500).
    """
    ws_db = tmp_path / "query_ws.db"
    init_workspace_store(ws_db)

    import config
    monkeypatch.setattr(config, "WORKSPACE_DB", ws_db)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "test-user-key")
    monkeypatch.setattr(config, "API_ADMIN_KEY", "test-admin-key")
    import app.workspace.store as store_mod
    monkeypatch.setattr(store_mod, "WORKSPACE_DB", ws_db)

    create_workspace(name="Charlie Query Space", workspace_id="ws_charlie_q", owner_id="charlie", db_path=ws_db)

    from app import main
    captured_sessions = []

    def fake_handle_query(query: str, session=None):
        if session is not None:
            captured_sessions.append(dict(session))
            session["last_result"] = type("Result", (), {"answer_type": "rag", "data": []})()
        return f"Answered: {query}"

    monkeypatch.setattr(main, "handle_query", fake_handle_query)

    app = create_app()
    client = TestClient(app)

    # 1. Authorized owner querying via body workspace_id WITHOUT X-Workspace-ID header -> 200 OK
    res_charlie = client.post(
        "/api/query",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "charlie"},
        json={"query": "hello workspace", "session_id": "s-charlie", "workspace_id": "ws_charlie_q"},
    )
    assert res_charlie.status_code == 200
    assert res_charlie.json()["answer"] == "Answered: hello workspace"
    assert captured_sessions[-1]["workspace_id"] == "ws_charlie_q"

    # 2. Unauthorized user querying via body workspace_id WITHOUT X-Workspace-ID header -> 403 Forbidden
    res_dave = client.post(
        "/api/query",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "dave"},
        json={"query": "sneaky query", "session_id": "s-dave", "workspace_id": "ws_charlie_q"},
    )
    assert res_dave.status_code == 403

    # 3. Non-existent workspace_id in body (non-admin) -> 403 Forbidden (not 500)
    res_missing = client.post(
        "/api/query",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "dave"},
        json={"query": "missing ws", "session_id": "s-missing", "workspace_id": "ws_nonexistent_999"},
    )
    assert res_missing.status_code == 403

    # 4. Admin querying via body workspace_id WITHOUT X-Workspace-ID header -> 200 OK
    res_admin = client.post(
        "/api/query",
        headers={"Authorization": "Bearer test-admin-key", "X-User-ID": "admin"},
        json={"query": "admin check", "session_id": "s-admin", "workspace_id": "ws_charlie_q"},
    )
    assert res_admin.status_code == 200

    # 5. Authorized owner querying WITH X-Workspace-ID header -> 200 OK
    res_hdr = client.post(
        "/api/query",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "charlie", "X-Workspace-ID": "ws_charlie_q"},
        json={"query": "header check", "session_id": "s-hdr", "workspace_id": "ws_charlie_q"},
    )
    assert res_hdr.status_code == 200

    # 6. Unauthorized user WITH X-Workspace-ID header -> 403 Forbidden
    res_hdr_unauth = client.post(
        "/api/query",
        headers={"Authorization": "Bearer test-user-key", "X-User-ID": "dave", "X-Workspace-ID": "ws_charlie_q"},
        json={"query": "header unauth", "session_id": "s-hdr2", "workspace_id": "ws_charlie_q"},
    )
    assert res_hdr_unauth.status_code == 403
