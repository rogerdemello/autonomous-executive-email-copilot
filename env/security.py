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
