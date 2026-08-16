"""Browser session plumbing: the auth cookie and CSRF tokens.

The JSON API authenticates with ``Authorization: Bearer <token>``, which a
server-rendered page cannot send. This module carries the *same* session token
in an HttpOnly cookie instead, so both surfaces share one identity model and one
set of role checks — there is no second auth system to keep in sync.

Cookie auth reintroduces one risk bearer tokens do not have: the browser
attaches the cookie automatically, so a third-party site could trigger a state
change on the user's behalf. Two mitigations, both required:

- ``SameSite=Lax`` stops the cookie riding along on cross-site form posts.
- Every mutating form carries a signed, expiring CSRF token bound to the
  session, verified in constant time before the handler runs.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, Response

from app.core.config import get_settings
from app.saas import tokens
from app.saas.deps import SESSION_COOKIE

CSRF_FIELD = "csrf_token"
_CSRF_TTL_SECONDS = 8 * 60 * 60


# --------------------------------------------------------------------------- #
# Session cookie
# --------------------------------------------------------------------------- #
def set_session_cookie(response: Response, token: str) -> None:
    """Attach the session token as an HttpOnly cookie.

    ``Secure`` is off in development so the cookie works over plain http on
    localhost, and on everywhere else — a session cookie sent in the clear is a
    session anyone on the network can steal.
    """
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.environment.strip().lower() != "development",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def issue_csrf_token(request: Request) -> str:
    """Mint a CSRF token bound to the current session (or to the anonymous
    visitor, for the login and signup forms, which have no session yet)."""
    subject = session_token(request) or "anonymous"
    return tokens.encode(
        {"typ": "csrf", "sub": _fingerprint(subject)},
        get_settings().resolved_auth_secret,
        ttl_seconds=_CSRF_TTL_SECONDS,
    )


def verify_csrf(request: Request, submitted: str | None) -> None:
    """Raise 403 unless ``submitted`` is a live CSRF token for this session."""
    if not submitted:
        raise HTTPException(status_code=403, detail="Missing CSRF token. Reload the page.")
    try:
        claims = tokens.decode(submitted, get_settings().resolved_auth_secret)
    except tokens.TokenError as exc:
        raise HTTPException(status_code=403, detail="Invalid or expired form token.") from exc
    if claims.get("typ") != "csrf":
        raise HTTPException(status_code=403, detail="Invalid form token.")
    expected = _fingerprint(session_token(request) or "anonymous")
    # The token is signed, so this only needs to confirm it was minted for the
    # session presenting it — not to resist forgery.
    if claims.get("sub") != expected:
        raise HTTPException(status_code=403, detail="Form token does not match this session.")


def _fingerprint(value: str) -> str:
    """A short, stable digest of a session token.

    The CSRF token travels in page HTML, so it must not embed the session token
    itself — that would turn any HTML-injection or cached-page leak into full
    session disclosure.
    """
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
