"""Opt-in API security primitives: token auth, rate limiting, input validation.

The API is open by default. Auth and rate limiting activate only when their
corresponding settings are configured (``API_AUTH_TOKEN`` / ``RATE_LIMIT_PER_MINUTE``),
so local development, tests, and the OpenEnv validator are unaffected unless an
operator turns them on.
"""

from __future__ import annotations

import re
import threading
import time

# Methods that mutate state and therefore require auth when a token is configured.
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Identifiers (episode ids, etc.) are validated against this to avoid surprises
# in lookups and any downstream path/handle usage.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def is_valid_identifier(value: str) -> bool:
    """True if ``value`` is a safe identifier (alphanumeric, _.-, max 128)."""
    return bool(value) and bool(_SAFE_ID_RE.match(value))


def extract_bearer_token(authorization: str | None, api_key_header: str | None) -> str | None:
    """Pull a token from an ``Authorization: Bearer <t>`` or ``X-API-Key`` header."""
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    if api_key_header:
        return api_key_header.strip()
    return None


def request_is_authorized(
    method: str,
    configured_token: str | None,
    authorization: str | None,
    api_key_header: str | None,
) -> bool:
    """Authorize a request.

    Open when no token is configured or the method is non-mutating; otherwise the
    presented token must match the configured one.
    """
    if not configured_token:
        return True
    if method.upper() not in MUTATING_METHODS:
        return True
    presented = extract_bearer_token(authorization, api_key_header)
    return presented is not None and presented == configured_token


# Tenant label assigned to the single ``API_AUTH_TOKEN`` (the non-multi-tenant
# path). Multi-tenant tokens carry their own configured label instead.
DEFAULT_TENANT = "default"


def resolve_auth(
    method: str,
    configured_token: str | None,
    tenant_tokens: dict[str, str] | None,
    authorization: str | None,
    api_key_header: str | None,
) -> tuple[bool, str | None]:
    """Authorize a request and resolve its tenant in one pass.

    Returns ``(authorized, tenant)``. ``tenant`` is the resolved tenant label
    for an authenticated request, or ``None`` when auth is open / not applicable
    (no credential was required) so callers can leave ``request.state`` untouched
    and preserve today's byte-identical default behavior.

    Auth is open when neither the single ``configured_token`` nor any
    ``tenant_tokens`` are configured, or when the method is non-mutating. When a
    credential is required, the presented token must match either the single
    ``configured_token`` (-> :data:`DEFAULT_TENANT`) or one of the configured
    ``tenant_tokens`` (-> its mapped label).

    NOTE: tenant resolution here only tags the request/response; it does NOT
    enforce per-tenant DB row isolation (no tenant column exists). That is a
    deliberate follow-up and out of scope.
    """
    tenant_tokens = tenant_tokens or {}
    if not configured_token and not tenant_tokens:
        return True, None
    if method.upper() not in MUTATING_METHODS:
        return True, None
    presented = extract_bearer_token(authorization, api_key_header)
    if presented is None:
        return False, None
    if configured_token and presented == configured_token:
        return True, DEFAULT_TENANT
    tenant = tenant_tokens.get(presented)
    if tenant is not None:
        return True, tenant
    return False, None


class FixedWindowRateLimiter:
    """Simple thread-safe fixed-window per-key rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (window_start_epoch_seconds, count)
        self._hits: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, limit_per_minute: int, now: float | None = None) -> bool:
        """Return True if a request for ``key`` is allowed under the limit.

        A non-positive limit disables rate limiting (always allowed).
        """
        if limit_per_minute <= 0:
            return True
        now = time.time() if now is None else now
        window = int(now // 60)
        with self._lock:
            start, count = self._hits.get(key, (window, 0))
            if start != window:
                start, count = window, 0
            count += 1
            self._hits[key] = (start, count)
            return count <= limit_per_minute

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# Shared limiter instance used by the API middleware.
rate_limiter = FixedWindowRateLimiter()
