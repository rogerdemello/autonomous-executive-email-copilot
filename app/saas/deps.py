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

# Named here rather than imported from app.web to keep the dependency pointing
# one way (web -> saas); the cookie name is shared vocabulary, not behaviour.
SESSION_COOKIE = "ec_session"

_auth_service = AuthService()


def _bearer_from_request(request: Request) -> str | None:
    """The caller's session token, from the Authorization header or the cookie.

    API clients send ``Authorization: Bearer <token>``; the server-rendered
    pages cannot, so they carry the same token in an HttpOnly cookie. Taking
    both here means one identity model and one set of role checks across both
    surfaces. The header wins when present, so an explicit credential is never
    silently overridden by a stale cookie left in the browser.
    """
    authorization = request.headers.get("Authorization")
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    cookie = request.cookies.get(SESSION_COOKIE)
    return cookie.strip() if cookie else None


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


def reject_shared_demo_account(user: dict) -> None:
    """Block the shared demo login from destructive/administrative actions.

    When the login page advertises the demo credential (``demo_login_active``),
    anyone on the internet holds that session — so the demo owner must not be
    able to change the password (locking out the next sales call), delete or
    export the workspace, manage members, activate licenses, or disconnect the
    mailbox. Triage itself (approve / reject / sync) stays allowed: that IS the
    demo. Inert when the demo login is not advertised, so a private deployment
    that happens to reuse the demo email is unaffected.
    """
    from app.core.config import get_settings

    from .demo_seed import DEMO_OWNER_EMAIL

    if not get_settings().demo_login_active:
        return
    if (user.get("email") or "").lower() == DEMO_OWNER_EMAIL.lower():
        raise HTTPException(
            status_code=403,
            detail="The shared demo account can't do this. Sign up for your own workspace.",
        )


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
