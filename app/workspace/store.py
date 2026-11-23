"""
store.py

Persistent SQLite database and authorization engine for Multi-Profile Workspace Boundaries
and Row-Level Authorization (`data/workspace.db`).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage.sqlite import connect_sqlite
from app.workspace.models import WorkspaceMembership, WorkspaceProfile

try:
    from config import DEFAULT_WORKSPACE_ID, WORKSPACE_DB
except ImportError:
    from app.config import DEFAULT_WORKSPACE_ID, WORKSPACE_DB

logger = logging.getLogger("lifeos.workspace.store")

ROLE_HIERARCHY = {
    "viewer": 1,
    "member": 2,
    "admin": 3,
    "owner": 4,
}


def _resolve_db_path(db_path: Path | str | None = None) -> Path:
    if db_path is not None:
        return Path(db_path).resolve()
    try:
        from config import WORKSPACE_DB
        return Path(WORKSPACE_DB).resolve()
    except Exception:
        from app.config import WORKSPACE_DB
        return Path(WORKSPACE_DB).resolve()


def init_workspace_store(db_path: Path | str | None = None) -> None:
    """Initialize workspace and membership tables, seeding the canonical default workspace."""
    target_path = _resolve_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                profile_type TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                settings_json TEXT DEFAULT '{}'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_memberships (
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                role TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (user_id, workspace_id),
                FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_memberships_user ON workspace_memberships(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memberships_workspace ON workspace_memberships(workspace_id)")

        # Seed canonical default workspace if not present
        row = conn.execute("SELECT workspace_id FROM workspaces WHERE workspace_id = ?", ("default",)).fetchone()
        if not row:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO workspaces (
                    workspace_id, name, profile_type, description,
                    created_at, updated_at, is_active, settings_json
                ) VALUES (?, ?, ?, ?, ?, ?, 1, '{}')
            """, (
                "default",
                "Default Workspace",
                "personal",
                "Canonical personal workspace for default documents and context.",
                now_iso,
                now_iso,
            ))

            # Default memberships
            for uid in ("default_user", "user", "admin"):
                conn.execute("""
                    INSERT OR IGNORE INTO workspace_memberships (
                        user_id, workspace_id, role, joined_at
                    ) VALUES (?, 'default', 'owner', ?)
                """, (uid, now_iso))


def create_workspace(
    name: str,
    profile_type: str = "personal",
    description: str | None = None,
    workspace_id: str | None = None,
    owner_id: str = "default_user",
    settings: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> WorkspaceProfile:
    """Create a new isolated workspace profile and register the creator as owner."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    ws_id = workspace_id or f"ws_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    settings_str = json.dumps(settings or {})

    with connect_sqlite(target_path, wal_mode=True) as conn:
        conn.execute("""
            INSERT INTO workspaces (
                workspace_id, name, profile_type, description,
                created_at, updated_at, is_active, settings_json
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """, (ws_id, name, profile_type, description, now_iso, now_iso, settings_str))

        conn.execute("""
            INSERT OR REPLACE INTO workspace_memberships (
                user_id, workspace_id, role, joined_at
            ) VALUES (?, ?, 'owner', ?)
        """, (owner_id, ws_id, now_iso))

    return WorkspaceProfile(
        workspace_id=ws_id,
        name=name,
        profile_type=profile_type,
        description=description,
        created_at=now_iso,
        updated_at=now_iso,
        is_active=True,
        settings=settings or {},
    )


def get_workspace(workspace_id: str, db_path: Path | str | None = None) -> WorkspaceProfile | None:
    """Retrieve workspace profile by ID."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        row = conn.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        if not row:
            return None

        settings = {}
        if row["settings_json"]:
            try:
                settings = json.loads(row["settings_json"])
            except Exception:
                pass

        return WorkspaceProfile(
            workspace_id=row["workspace_id"],
            name=row["name"],
            profile_type=row["profile_type"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_active=bool(row["is_active"]),
            settings=settings,
        )


def update_workspace(
    workspace_id: str,
    name: str | None = None,
    profile_type: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
    settings: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> WorkspaceProfile | None:
    """Update workspace attributes."""
    existing = get_workspace(workspace_id, db_path=db_path)
    if not existing:
        return None

    target_path = _resolve_db_path(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    new_name = name if name is not None else existing.name
    new_profile_type = profile_type if profile_type is not None else existing.profile_type
    new_description = description if description is not None else existing.description
    new_is_active = is_active if is_active is not None else existing.is_active
    new_settings = settings if settings is not None else existing.settings

    with connect_sqlite(target_path, wal_mode=True) as conn:
        conn.execute("""
            UPDATE workspaces
            SET name = ?, profile_type = ?, description = ?, is_active = ?, settings_json = ?, updated_at = ?
            WHERE workspace_id = ?
        """, (
            new_name,
            new_profile_type,
            new_description,
            1 if new_is_active else 0,
            json.dumps(new_settings),
            now_iso,
            workspace_id,
        ))

    return WorkspaceProfile(
        workspace_id=workspace_id,
        name=new_name,
        profile_type=new_profile_type,
        description=new_description,
        created_at=existing.created_at,
        updated_at=now_iso,
        is_active=new_is_active,
        settings=new_settings,
    )


def list_workspaces_for_user(
    user_id: str = "default_user",
    is_admin: bool = False,
    db_path: Path | str | None = None,
) -> list[WorkspaceProfile]:
    """List workspaces accessible to the user (or all workspaces if admin)."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        if is_admin:
            rows = conn.execute("SELECT * FROM workspaces WHERE is_active = 1 ORDER BY created_at ASC").fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT w.* FROM workspaces w
                LEFT JOIN workspace_memberships m ON w.workspace_id = m.workspace_id
                WHERE (w.workspace_id = 'default' OR m.user_id = ?) AND w.is_active = 1
                ORDER BY w.created_at ASC
            """, (user_id,)).fetchall()

        results = []
        for r in rows:
            settings = {}
            if r["settings_json"]:
                try:
                    settings = json.loads(r["settings_json"])
                except Exception:
                    pass
            results.append(
                WorkspaceProfile(
                    workspace_id=r["workspace_id"],
                    name=r["name"],
                    profile_type=r["profile_type"],
                    description=r["description"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    is_active=bool(r["is_active"]),
                    settings=settings,
                )
            )
        return results


def delete_workspace(workspace_id: str, db_path: Path | str | None = None) -> bool:
    """Soft-delete or purge a workspace profile (cannot delete 'default')."""
    if workspace_id == "default":
        logger.warning("Attempted to delete protected default workspace.")
        return False

    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        cursor = conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
        conn.execute("DELETE FROM workspace_memberships WHERE workspace_id = ?", (workspace_id,))
        return cursor.rowcount > 0


def add_workspace_member(
    workspace_id: str,
    user_id: str,
    role: str = "member",
    db_path: Path | str | None = None,
) -> WorkspaceMembership:
    """Grant or update a user's membership and role in a workspace."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)
    now_iso = datetime.now(timezone.utc).isoformat()

    with connect_sqlite(target_path, wal_mode=True) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO workspace_memberships (
                user_id, workspace_id, role, joined_at
            ) VALUES (?, ?, ?, ?)
        """, (user_id, workspace_id, role, now_iso))

    return WorkspaceMembership(
        user_id=user_id,
        workspace_id=workspace_id,
        role=role,
        joined_at=now_iso,
    )


def remove_workspace_member(
    workspace_id: str,
    user_id: str,
    db_path: Path | str | None = None,
) -> bool:
    """Remove a user from a workspace."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        cursor = conn.execute("""
            DELETE FROM workspace_memberships
            WHERE workspace_id = ? AND user_id = ?
        """, (workspace_id, user_id))
        return cursor.rowcount > 0


def get_workspace_membership(
    workspace_id: str,
    user_id: str,
    db_path: Path | str | None = None,
) -> WorkspaceMembership | None:
    """Retrieve membership details for a specific user and workspace."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        row = conn.execute("""
            SELECT * FROM workspace_memberships
            WHERE workspace_id = ? AND user_id = ?
        """, (workspace_id, user_id)).fetchone()

        if not row:
            return None

        return WorkspaceMembership(
            user_id=row["user_id"],
            workspace_id=row["workspace_id"],
            role=row["role"],
            joined_at=row["joined_at"],
        )


def list_workspace_members(
    workspace_id: str,
    db_path: Path | str | None = None,
) -> list[WorkspaceMembership]:
    """List all members belonging to a workspace."""
    target_path = _resolve_db_path(db_path)
    init_workspace_store(target_path)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        rows = conn.execute("""
            SELECT * FROM workspace_memberships
            WHERE workspace_id = ?
            ORDER BY joined_at ASC
        """, (workspace_id,)).fetchall()

        return [
            WorkspaceMembership(
                user_id=r["user_id"],
                workspace_id=r["workspace_id"],
                role=r["role"],
                joined_at=r["joined_at"],
            )
            for r in rows
        ]


def is_user_authorized_for_workspace(
    user_id: str,
    workspace_id: str,
    required_role: str = "viewer",
    is_admin: bool = False,
    db_path: Path | str | None = None,
) -> bool:
    """
    Enforce Row-Level Authorization: verify user has at least required_role in the specified workspace.
    """
    if is_admin:
        return True

    # Default workspace allows standard user access
    if workspace_id == "default":
        return True

    membership = get_workspace_membership(workspace_id=workspace_id, user_id=user_id, db_path=db_path)
    if not membership:
        return False

    user_level = ROLE_HIERARCHY.get(membership.role, 0)
    required_level = ROLE_HIERARCHY.get(required_role, 1)
    return user_level >= required_level
