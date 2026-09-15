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
from app.copilot.providers.demo import DEMO_PROVIDER_KEY, DemoProvider
from app.copilot.providers.fake import FakeProvider
from app.copilot.providers.gmail import GmailProvider
from app.copilot.providers.graph import MicrosoftGraphProvider

from . import oauth
from .crypto import DecryptionError, get_vault
from .repository import MailboxRepository

logger = logging.getLogger(__name__)


class BrokenConnectionError(Exception):
    """A real connection exists but cannot produce an authenticated provider.

    Raised instead of silently substituting fixture data: a user whose "Gmail"
    fills with invented messages has been lied to, and nothing on screen said
    so. Callers surface this as "reconnect the mailbox".
    """

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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


def _mark_broken(connection: dict, reason: str) -> BrokenConnectionError:
    """Flag the connection as errored, tell somebody, and return the exception.

    A revoked token is the failure mode that hurts most, because it looks like
    nothing: the worker correctly stops retrying a broken connection, the queue
    stops filling, and an inbox that has gone quiet is indistinguishable from a
    quiet week. So the transition is announced — in the audit log, and by mail
    to the people who can act on it.

    ``set_status`` returns true only on a *change*, which is what keeps this to
    one message: the worker re-derives this state on every sweep, and 96 mails
    a day about the same dead token is how a notification becomes a filter rule.
    """
    changed = MailboxRepository().set_status(connection["org_id"], connection["id"], "error")
    logger.warning("Connection %s marked broken: %s", connection.get("id"), reason)
    if changed:
        _announce_broken(connection, reason)
    return BrokenConnectionError(reason)


def _announce_broken(connection: dict, reason: str) -> None:
    """Audit the breakage and email the org's admins. Never raises."""
    org_id = connection["org_id"]
    account = connection.get("account_email") or "a mailbox"
    try:
        from .repository import AuditRepository

        AuditRepository().record(
            action="mailbox.broken",
            org_id=org_id,
            target=connection.get("id"),
            detail={
                "provider": connection.get("provider"),
                "account_email": account,
                "reason": reason,
            },
        )
    except Exception:  # noqa: BLE001 - the audit row is not worth a failed sync
        logger.warning("Could not audit the broken connection for org %s", org_id, exc_info=True)

    try:
        _email_admins_about(org_id, account, reason)
    except Exception:  # noqa: BLE001 - notification is best-effort
        logger.warning("Could not notify org %s about a broken mailbox", org_id, exc_info=True)


def _email_admins_about(org_id: str, account: str, reason: str) -> None:
    from app.core.config import get_settings

    from .email import send_email
    from .models_db import ROLE_ADMIN
    from .rbac import role_at_least
    from .repository import UserRepository

    recipients = [
        user["email"]
        for user in UserRepository().list_for_org(org_id)
        if user.get("email") and role_at_least(user.get("role", ""), ROLE_ADMIN)
    ]
    if not recipients:
        return

    link = f"{get_settings().resolved_app_public_url}/app/connect"
    body = (
        f"{account} has stopped syncing and needs to be reconnected.\n\n"
        f"{reason}\n\n"
        "Until it is reconnected, no new mail from that account is being "
        "triaged and no drafts are being prepared for it. Nothing already in "
        "your queue has been lost.\n\n"
        f"Reconnect it here: {link}\n"
    )
    for address in recipients:
        send_email(address, f"Action needed: {account} stopped syncing", body)


def build_provider(connection: dict) -> MailProvider:
    """Construct a provider for ``connection`` (a MailboxConnection ``to_dict``).

    Re-reads the encrypted tokens server-side. A *real* connection that cannot
    authenticate raises :class:`BrokenConnectionError` — it must never fall back
    to fixture data, because a sync would then report success while filling the
    user's mailbox with invented messages. FakeProvider remains only for
    provider keys the product cannot create (dev/test connections).
    """
    provider_key = connection.get("provider")
    if provider_key == DEMO_PROVIDER_KEY:
        return DemoProvider()
    if provider_key not in ("google", "microsoft"):
        return FakeProvider()

    full = MailboxRepository().get_with_tokens(connection["org_id"], connection["id"])
    if not full or not full.get("access_token_enc"):
        raise _mark_broken(
            connection,
            "This mailbox has no usable credentials. Reconnect it to continue syncing.",
        )

    vault = get_vault()
    try:
        access_token = vault.decrypt(full["access_token_enc"])
    except DecryptionError as exc:
        # Typically an AUTH_SECRET_KEY rotation: every stored token is now
        # unreadable. Say so, rather than 500-ing every sync from here on.
        raise _mark_broken(
            connection,
            "Stored mailbox credentials can no longer be decrypted "
            "(was the server's secret key rotated?). Reconnect the mailbox.",
        ) from exc
    refresher = _make_refresher(full) if full.get("refresh_token_enc") else None

    if provider_key == "google":
        return GmailProvider(access_token, token_refresher=refresher)
    return MicrosoftGraphProvider(access_token, token_refresher=refresher)
