"""FastAPI dependencies for the SaaS API: current user and role guards.

A SaaS request authenticates with a **user session token** in
``Authorization: Bearer <token>`` (distinct from the operator-level
``API_AUTH_TOKEN`` used to protect the benchmark API). These dependencies turn
that token into the live user row and enforce role requirements.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from . import rbac
from .auth import AuthError, AuthService

_auth_service = AuthService()


def _bearer_from_request(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return None


def get_current_user(request: Request) -> dict:
    """Resolve the authenticated user or raise 401.

    Stashes the user + org id on ``request.state`` so downstream handlers and
    future product repositories can scope by tenant.
    """
    token = _bearer_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        user = _auth_service.resolve(token)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    request.state.user = user
    request.state.org_id = user["org_id"]
    return user


def require_role(minimum: str):
    """Dependency factory: require the current user's role >= ``minimum``."""

    def _guard(user: dict = Depends(get_current_user)) -> dict:
        if not rbac.role_at_least(user["role"], minimum):
            raise HTTPException(
                status_code=403,
                detail=f"Requires {minimum} role or higher",
            )
        return user

    return _guard
