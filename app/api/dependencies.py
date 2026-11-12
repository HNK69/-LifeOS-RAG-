"""
dependencies.py

FastAPI dependency providers for authorization, session resolution,
and request context propagation across the LifeOS API.
"""

import uuid
from typing import Any
from fastapi import Depends, Header, HTTPException, Request, status

from app.api.auth import AuthenticatedUser, authenticate_request

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
    ):
        self.request_id = request_id
        self.user = user
        self.session_id = session_id
        self.session_data = session_data

    @property
    def user_id(self) -> str:
        return self.user.user_id

    @property
    def tenant_id(self) -> str:
        return self.user.tenant_id


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


def get_request_context(
    request: Request,
    user: AuthenticatedUser = Depends(authenticate_request),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> RequestContext:
    """
    Resolve request context, unique request_id, and isolated session dictionary.
    Guarantees concurrent requests cannot leak multi-turn conversation state.
    """
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    session_id = x_session_id or str(uuid.uuid4())

    cache_key = (user.tenant_id, user.user_id, session_id)
    if cache_key not in _session_cache:
        # Bounded cleanup if session cache exceeds 10,000 entries
        if len(_session_cache) > 10000:
            # Evict oldest entries
            keys_to_remove = list(_session_cache.keys())[:2000]
            for k in keys_to_remove:
                _session_cache.pop(k, None)
        _session_cache[cache_key] = {}

    session_data = _session_cache[cache_key]

    return RequestContext(
        request_id=request_id,
        user=user,
        session_id=session_id,
        session_data=session_data,
    )
