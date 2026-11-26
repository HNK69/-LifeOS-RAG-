"""
auth.py

Authentication and identity resolution for the LifeOS Phase 11 API.
Supports constant-time API key verification, role segregation (user vs. admin),
and strict protection against credential leakage in logs or exceptions.
"""

import secrets
from typing import NamedTuple
from fastapi import Header, HTTPException, Request, status


class AuthenticatedUser(NamedTuple):
    user_id: str
    tenant_id: str
    role: str  # "user" | "admin" | "device"


_DEVICE_TOKEN_PREFIX = "devtok_"


def _is_device_scoped_route_allowed(path: str, method: str, device_id: str) -> bool:
    """Restrict device-scoped bearer tokens strictly to sync, envelope, identity, and event stream routes."""
    if path.startswith("/api/sync"):
        return True
    if path == "/api/device/identity" and method == "GET":
        return True
    if path.startswith("/api/device/envelope/"):
        return True
    if path.startswith("/api/events"):
        return True
    if path == f"/api/device/peers/{device_id}" and method == "GET":
        return True
    return False


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
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    x_device_token: str | None = Header(None, alias="X-Device-Token"),
    x_user_id: str | None = Header(None, alias="X-User-ID"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> AuthenticatedUser:
    """
    Authenticate an incoming request and return an AuthenticatedUser identity.
    Uses constant-time comparison to prevent timing side-channel attacks.
    Supports admin/user API keys as well as paired device-scoped tokens (`devtok_*`).
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
    dev_token = x_device_token.strip() if x_device_token else None
    if not token and not dev_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide 'Authorization: Bearer <key>', 'X-API-Key', or 'X-Device-Token'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Verify against configured admin key (elevated permissions)
    if token and cfg_admin_key and secrets.compare_digest(token, cfg_admin_key):
        return AuthenticatedUser(
            user_id=x_user_id or "admin",
            tenant_id=x_tenant_id or "default_tenant",
            role="admin",
        )

    # 4. Verify against standard client API key
    if token and cfg_api_key and secrets.compare_digest(token, cfg_api_key):
        # If API_ADMIN_KEY is not configured, API_KEY inherits admin role
        role = "admin" if not cfg_admin_key else "user"
        return AuthenticatedUser(
            user_id=x_user_id or "user",
            tenant_id=x_tenant_id or "default_tenant",
            role=role,
        )

    # 5. Verify paired device-scoped token (X-Device-Token or Authorization: Bearer devtok_*)
    candidate_dev_tok = dev_token or (
        token if token and token.startswith(_DEVICE_TOKEN_PREFIX) else None
    )
    if candidate_dev_tok:
        from app.security.device_store import get_trusted_device_by_token
        peer = get_trusted_device_by_token(candidate_dev_tok)
        if peer:
            if request is not None and not _is_device_scoped_route_allowed(
                request.url.path, request.method.upper(), peer["device_id"]
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: Device token is scoped only to device sync operations.",
                )
            return AuthenticatedUser(
                user_id=f"device:{peer['device_id']}",
                tenant_id=x_tenant_id or "default_tenant",
                role="device",
            )

    # 6. Invalid credentials (never log or leak token in exception)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key or credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_token(token: str | None, user_id: str | None = None, tenant_id: str | None = None) -> AuthenticatedUser | None:
    """
    Verify raw token string against configured keys or paired device tokens.
    Returns AuthenticatedUser on success, or None on failure.
    """
    try:
        import config
    except ImportError:
        from app import config

    auth_enabled = getattr(config, "API_AUTH_ENABLED", True)
    if not auth_enabled:
        return AuthenticatedUser(user_id=user_id or "dev_user", tenant_id=tenant_id or "default_tenant", role="admin")

    if not token:
        return None

    cfg_admin_key = getattr(config, "API_ADMIN_KEY", "")
    cfg_api_key = getattr(config, "API_KEY", "")

    if cfg_admin_key and secrets.compare_digest(token, cfg_admin_key):
        return AuthenticatedUser(user_id=user_id or "admin", tenant_id=tenant_id or "default_tenant", role="admin")

    if cfg_api_key and secrets.compare_digest(token, cfg_api_key):
        role = "admin" if not cfg_admin_key else "user"
        return AuthenticatedUser(user_id=user_id or "user", tenant_id=tenant_id or "default_tenant", role=role)

    if token.startswith(_DEVICE_TOKEN_PREFIX):
        from app.security.device_store import get_trusted_device_by_token
        peer = get_trusted_device_by_token(token)
        if peer:
            return AuthenticatedUser(
                user_id=f"device:{peer['device_id']}",
                tenant_id=tenant_id or "default_tenant",
                role="device",
            )

    return None
