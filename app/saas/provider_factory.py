"""Build an authenticated MailProvider for a connected mailbox.

This is the ONLY module that decrypts stored OAuth tokens and performs token
refresh — keeping crypto in one auditable place. Given a connection, it decrypts
the access token, wires a refresh closure (which re-encrypts and persists the new
token on use), and constructs the matching provider. Unconfigured providers or
token-less connections fall back to the in-memory :class:`FakeProvider`, so local
dev and tests work with zero credentials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.copilot.providers.base import MailProvider
from app.copilot.providers.fake import FakeProvider
from app.copilot.providers.gmail import GmailProvider
from app.copilot.providers.graph import MicrosoftGraphProvider

from . import oauth
from .crypto import get_vault
from .repository import MailboxRepository

logger = logging.getLogger(__name__)


def _expiry_iso(token_response: dict) -> str | None:
    expires_in = token_response.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()


def _make_refresher(connection: dict):
    """Return a zero-arg callable that refreshes and persists the access token."""
    org_id = connection["org_id"]
    provider_key = connection["provider"]
    account_email = connection["account_email"]
    connected_by = connection.get("connected_by")
    scopes = " ".join(connection.get("scopes", [])) if connection.get("scopes") else None
    refresh_token_enc = connection["refresh_token_enc"]

    def _refresh() -> str:
        vault = get_vault()
        provider_obj = oauth.get_provider(provider_key)
        client_id, client_secret = oauth.provider_credentials(provider_key)
        if provider_obj is None or not client_id or not client_secret:
            raise oauth.OAuthExchangeError(f"{provider_key} is not configured for refresh")
        resp = oauth.refresh_tokens(
            provider_obj,
            refresh_token=vault.decrypt(refresh_token_enc),
            client_id=client_id,
            client_secret=client_secret,
        )
        new_access = resp.get("access_token")
        if not new_access:
            raise oauth.OAuthExchangeError("refresh returned no access_token")
        new_refresh = resp.get("refresh_token")
        MailboxRepository().upsert_connection(
            org_id=org_id,
            provider=provider_key,
            account_email=account_email,
            connected_by=connected_by,
            access_token_enc=vault.encrypt(new_access),
            # Keep the existing refresh token when the provider doesn't rotate it.
            refresh_token_enc=vault.encrypt(new_refresh) if new_refresh else None,
            token_expires_at=_expiry_iso(resp),
            scopes=scopes,
        )
        return new_access

    return _refresh


def build_provider(connection: dict) -> MailProvider:
    """Construct a provider for ``connection`` (a MailboxConnection ``to_dict``).

    Re-reads the encrypted tokens server-side. Falls back to FakeProvider when the
    provider isn't a real one or no token is stored (e.g. dev/test connections).
    """
    provider_key = connection.get("provider")
    if provider_key not in ("google", "microsoft"):
        return FakeProvider()

    full = MailboxRepository().get_with_tokens(connection["org_id"], connection["id"])
    if not full or not full.get("access_token_enc"):
        logger.warning(
            "Connection %s has no stored token; using FakeProvider", connection.get("id")
        )
        return FakeProvider()

    vault = get_vault()
    access_token = vault.decrypt(full["access_token_enc"])
    refresher = _make_refresher(full) if full.get("refresh_token_enc") else None

    if provider_key == "google":
        return GmailProvider(access_token, token_refresher=refresher)
    if provider_key == "microsoft":
        return MicrosoftGraphProvider(access_token, token_refresher=refresher)
    return FakeProvider()
