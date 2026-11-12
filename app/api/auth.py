"""
auth.py

Authentication and identity resolution for the LifeOS Phase 11 API.
Supports constant-time API key verification, role segregation (user vs. admin),
and strict protection against credential leakage in logs or exceptions.
"""

import secrets
from typing import NamedTuple
from fastapi import Header, HTTPException, status


class AuthenticatedUser(NamedTuple):
    user_id: str
    tenant_id: str
    role: str  # "user" | "admin"


def _extract_token(
    authorization: str | None = None,
    x_api_key: str | None = None,
) -> str | None:
    """Extract raw bearer token or API key from HTTP headers."""
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        # Non-bearer or malformed authorization header
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed Authorization header. Format must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if x_api_key:
        return x_api_key.strip()
    return None


def authenticate_request(
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    x_user_id: str | None = Header(None, alias="X-User-ID"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> AuthenticatedUser:
    """
    Authenticate an incoming request and return an AuthenticatedUser identity.
    Uses constant-time comparison to prevent timing side-channel attacks.
    """
    try:
        import config
    except ImportError:
        from app import config

    auth_enabled = getattr(config, "API_AUTH_ENABLED", True)
    cfg_api_key = getattr(config, "API_KEY", "")
    cfg_admin_key = getattr(config, "API_ADMIN_KEY", "")

    # 1. Bypassed when auth is explicitly disabled in local test environment
    if not auth_enabled:
        return AuthenticatedUser(
            user_id=x_user_id or "dev_user",
            tenant_id=x_tenant_id or "default_tenant",
            role="admin",
        )

    # 2. Extract credential token
    token = _extract_token(authorization=authorization, x_api_key=x_api_key)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide 'Authorization: Bearer <key>' or 'X-API-Key'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Verify against configured admin key (elevated permissions)
    if cfg_admin_key and secrets.compare_digest(token, cfg_admin_key):
        return AuthenticatedUser(
            user_id=x_user_id or "admin",
            tenant_id=x_tenant_id or "default_tenant",
            role="admin",
        )

    # 4. Verify against standard client API key
    if cfg_api_key and secrets.compare_digest(token, cfg_api_key):
        # If API_ADMIN_KEY is not configured, API_KEY inherits admin role
        role = "admin" if not cfg_admin_key else "user"
        return AuthenticatedUser(
            user_id=x_user_id or "user",
            tenant_id=x_tenant_id or "default_tenant",
            role=role,
        )

    # 5. Invalid credentials (never log or leak token in exception)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key or credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
