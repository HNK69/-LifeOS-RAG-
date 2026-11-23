"""
dependencies.py

FastAPI dependency providers for authorization, session resolution,
and request context propagation across the LifeOS API.
"""

import uuid
from typing import Any
from fastapi import Depends, Header, HTTPException, Request, status

from app.api.auth import AuthenticatedUser, authenticate_request
from app.workspace.models import WorkspaceProfile
from app.workspace.store import (
    get_workspace,
    init_workspace_store,
    is_user_authorized_for_workspace,
)

# In-memory session cache mapping (tenant_id, user_id, session_id) -> session dict
# Thread-safe in single-process async event loops
_session_cache: dict[tuple[str, str, str], dict[str, Any]] = {}


class RequestContext:
    """Holds request-scoped metadata and session state."""

    def __init__(
        self,
        request_id: str,
        user: AuthenticatedUser,
        session_id: str,
        session_data: dict[str, Any],
        workspace: WorkspaceProfile | None = None,
    ):
        self.request_id = request_id
        self.user = user
        self.session_id = session_id
        self.session_data = session_data
        self.workspace = workspace or WorkspaceProfile(workspace_id="default", name="Default Workspace")

    @property
    def user_id(self) -> str:
        return self.user.user_id

    @property
    def tenant_id(self) -> str:
        return self.user.tenant_id

    @property
    def workspace_id(self) -> str:
        return self.workspace.workspace_id


def require_auth(user: AuthenticatedUser = Depends(authenticate_request)) -> AuthenticatedUser:
    """Ensure request is authenticated with any valid user or admin key."""
    return user


def require_admin(user: AuthenticatedUser = Depends(authenticate_request)) -> AuthenticatedUser:
    """Ensure request is authenticated with elevated administrative privileges."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Administrative privileges required for this operation",
        )
    return user


def resolve_workspace(
    request: Request,
    user: AuthenticatedUser = Depends(require_auth),
    x_workspace_id: str | None = Header(None, alias="X-Workspace-ID"),
) -> WorkspaceProfile:
    """Resolve active workspace profile for the request and enforce row-level authorization."""
    init_workspace_store()
    ws_id = x_workspace_id or request.query_params.get("workspace_id") or "default"
    ws = get_workspace(ws_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{ws_id}' not found.",
        )

    is_admin = (user.role == "admin")
    if not is_user_authorized_for_workspace(user_id=user.user_id, workspace_id=ws_id, required_role="viewer", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: User '{user.user_id}' does not have access to workspace '{ws_id}'.",
        )

    return ws


def get_or_create_session(user: AuthenticatedUser, session_id: str) -> dict[str, Any]:
    """Retrieve or initialize an in-memory session state dict for a given user and session_id."""
    cache_key = (user.tenant_id, user.user_id, session_id)
    if cache_key not in _session_cache:
        # Bounded cleanup if session cache exceeds 10,000 entries
        if len(_session_cache) > 10000:
            # Evict oldest entries
            keys_to_remove = list(_session_cache.keys())[:2000]
            for k in keys_to_remove:
                _session_cache.pop(k, None)
        _session_cache[cache_key] = {}
    return _session_cache[cache_key]


def get_request_context(
    request: Request,
    user: AuthenticatedUser = Depends(authenticate_request),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
    x_workspace_id: str | None = Header(None, alias="X-Workspace-ID"),
) -> RequestContext:
    """
    Resolve request context, unique request_id, and isolated session dictionary.
    Guarantees concurrent requests cannot leak multi-turn conversation state.
    """
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    session_id = x_session_id or str(uuid.uuid4())
    session_data = get_or_create_session(user, session_id)

    # Workspace resolution for request context
    init_workspace_store()
    ws_id = x_workspace_id or request.query_params.get("workspace_id") or "default"
    ws = get_workspace(ws_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{ws_id}' not found.",
        )

    is_admin = (user.role == "admin")
    if not is_user_authorized_for_workspace(user_id=user.user_id, workspace_id=ws_id, required_role="viewer", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: User '{user.user_id}' does not have access to workspace '{ws_id}'.",
        )

    return RequestContext(
        request_id=request_id,
        user=user,
        session_id=session_id,
        session_data=session_data,
        workspace=ws,
    )
