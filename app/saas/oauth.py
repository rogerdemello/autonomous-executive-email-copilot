"""OAuth 2.0 authorization-code flow for connecting real mailboxes.

Supports Gmail (Google) and Microsoft 365 (Microsoft Entra). The provider
registry is data-driven so adding a provider is a table entry, not new control
flow. The flow:

1. ``/mailbox/connect/{provider}`` builds a provider consent URL with a **signed
   state** (CSRF token that also carries the initiating org/user) and redirects.
2. The provider redirects back to ``/mailbox/oauth/callback`` with ``code`` +
   ``state``. We verify the state signature, exchange the code for tokens, and
   persist an encrypted :class:`MailboxConnection`.

A provider is only *available* when its client id AND secret are configured, so
the feature stays fully opt-in. The token exchange (the one impure step) uses
``httpx``; everything else is pure and unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from app.core.config import get_settings

from . import tokens

# State token lifetime: the round-trip to a consent screen and back.
_STATE_TTL_SECONDS = 900


@dataclass(frozen=True)
class OAuthProvider:
    key: str
    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    # Whether the token endpoint should receive ``access_type=offline`` etc.
    extra_authorize_params: dict[str, str] = field(default_factory=dict)


def _providers() -> dict[str, OAuthProvider]:
    """Provider registry. Microsoft's URLs depend on the configured tenant."""
    settings = get_settings()
    tenant = settings.microsoft_oauth_tenant or "common"
    return {
        "google": OAuthProvider(
            key="google",
            name="Gmail",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            scopes=(
                # readonly kept for backward-compatible scope assertions; modify +
                # compose grant the write surface (label/archive, drafts, send).
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.compose",
                "openid",
                "email",
            ),
            extra_authorize_params={
                "access_type": "offline",
                "prompt": "consent",
            },
        ),
        "microsoft": OAuthProvider(
            key="microsoft",
            name="Microsoft 365",
            authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            scopes=(
                "https://graph.microsoft.com/Mail.Read",
                "https://graph.microsoft.com/Mail.ReadWrite",
                "https://graph.microsoft.com/Mail.Send",
                "offline_access",
                "openid",
                "email",
            ),
        ),
    }


def get_provider(key: str) -> OAuthProvider | None:
    return _providers().get(key)


def provider_credentials(key: str) -> tuple[str | None, str | None]:
    """Return ``(client_id, client_secret)`` for a provider from settings."""
    settings = get_settings()
    if key == "google":
        return settings.google_oauth_client_id, settings.google_oauth_client_secret
    if key == "microsoft":
        return settings.microsoft_oauth_client_id, settings.microsoft_oauth_client_secret
    return None, None


def provider_available(key: str) -> bool:
    client_id, client_secret = provider_credentials(key)
    return bool(client_id and client_secret and get_provider(key))


def available_providers() -> list[dict]:
    """List providers with their configured/available status (for the UI)."""
    out = []
    for key, prov in _providers().items():
        out.append({"key": key, "name": prov.name, "available": provider_available(key)})
    return out


def redirect_uri(request_base_url: str | None = None) -> str:
    """The registered OAuth callback URL.

    Prefers ``OAUTH_REDIRECT_BASE_URL``; otherwise derives from the request's
    base URL. Must exactly match what's registered with the provider.
    """
    settings = get_settings()
    base = (settings.oauth_redirect_base_url or request_base_url or "").rstrip("/")
    return f"{base}/mailbox/oauth/callback"


def sign_state(*, org_id: str, user_id: str, provider: str, now: float | None = None) -> str:
    """Signed, expiring CSRF state carrying who initiated the connect."""
    return tokens.encode(
        {"typ": "oauth_state", "org": org_id, "sub": user_id, "prov": provider},
        get_settings().resolved_auth_secret,
        ttl_seconds=_STATE_TTL_SECONDS,
        now=now,
    )


def verify_state(state: str, *, now: float | None = None) -> dict:
    """Verify a state token; return its claims or raise ``tokens.TokenError``."""
    claims = tokens.decode(state, get_settings().resolved_auth_secret, now=now)
    if claims.get("typ") != "oauth_state":
        raise tokens.TokenError("not an oauth state token")
    return claims


def build_authorize_url(
    provider: OAuthProvider, *, client_id: str, redirect: str, state: str
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        **provider.extra_authorize_params,
    }
    return f"{provider.authorize_url}?{urlencode(params)}"


def exchange_code(
    provider: OAuthProvider,
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect: str,
) -> dict:
    """Exchange an authorization code for tokens. Returns the provider's JSON.

    Raises ``OAuthExchangeError`` on a non-2xx response. Network call — patched
    out in tests.
    """
    import httpx

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
    }
    try:
        resp = httpx.post(provider.token_url, data=data, timeout=15.0)
    except httpx.HTTPError as exc:  # pragma: no cover - network failure path
        raise OAuthExchangeError(f"token endpoint unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise OAuthExchangeError(f"token exchange failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def refresh_tokens(
    provider: OAuthProvider,
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Exchange a refresh token for a fresh access token. Returns provider JSON.

    Symmetrical with :func:`exchange_code` (same ``token_url``, patched in tests).
    Providers may or may not return a new ``refresh_token``; callers keep the old
    one if absent.
    """
    import httpx

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        resp = httpx.post(provider.token_url, data=data, timeout=15.0)
    except httpx.HTTPError as exc:  # pragma: no cover - network failure path
        raise OAuthExchangeError(f"token endpoint unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise OAuthExchangeError(f"token refresh failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


class OAuthExchangeError(Exception):
    """Raised when the provider token exchange fails."""
