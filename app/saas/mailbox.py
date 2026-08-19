"""Mailbox connection service: OAuth connect -> encrypted, tenant-scoped link.

Ties together the OAuth flow (:mod:`app.saas.oauth`), the at-rest token vault
(:mod:`app.saas.crypto`), and the tenant-scoped repository. Route handlers call
``start_connect`` and ``complete_callback``; all the crypto and persistence
lives here.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone

from . import oauth, tokens
from .crypto import get_vault
from .repository import AuditRepository, MailboxRepository

logger = logging.getLogger(__name__)


class MailboxError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _decode_jwt_email(id_token: str) -> str | None:
    """Extract the ``email`` claim from an OIDC id_token WITHOUT verifying it.

    The id_token comes straight from the provider's token endpoint over TLS, so
    it is trusted for the narrow purpose of labelling the connection with its
    account email. (It is never used as an auth credential here.)
    """
    if not id_token or id_token.count(".") != 2:
        return None
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return None
    email = payload.get("email") or payload.get("preferred_username") or payload.get("upn")
    return str(email) if email else None


def _expires_at_iso(token_response: dict) -> str | None:
    expires_in = token_response.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()


class MailboxService:
    def __init__(self) -> None:
        self.repo = MailboxRepository()
        self.audit = AuditRepository()

    def list_connections(self, org_id: str) -> list[dict]:
        return self.repo.list_for_org(org_id)

    def start_connect(
        self,
        *,
        org_id: str,
        user_id: str,
        provider_key: str,
        request_base_url: str | None = None,
    ) -> str:
        """Return the provider consent URL to redirect the user to.

        ``request_base_url`` matters: without ``OAUTH_REDIRECT_BASE_URL`` set,
        the callback URL is derived from the request — the same derivation
        :meth:`complete_callback` uses, so the two legs of the flow always
        present the identical redirect_uri to the provider.
        """
        provider = oauth.get_provider(provider_key)
        if provider is None:
            raise MailboxError(f"Unknown provider: {provider_key}", 404)
        if not oauth.provider_available(provider_key):
            raise MailboxError(
                f"{provider.name} is not configured on this server. "
                "An operator must set its OAuth client id/secret.",
                400,
            )
        client_id, _secret = oauth.provider_credentials(provider_key)
        state = oauth.sign_state(org_id=org_id, user_id=user_id, provider=provider_key)
        return oauth.build_authorize_url(
            provider,
            client_id=client_id or "",
            redirect=oauth.redirect_uri(request_base_url),
            state=state,
        )

    def complete_callback(
        self, *, state: str, code: str, request_base_url: str | None = None
    ) -> dict:
        """Handle the OAuth redirect: verify state, exchange code, persist link."""
        try:
            claims = oauth.verify_state(state)
        except tokens.TokenError as exc:
            raise MailboxError("Invalid or expired connect request.", 400) from exc

        org_id = claims["org"]
        user_id = claims["sub"]
        provider_key = claims["prov"]
        provider = oauth.get_provider(provider_key)
        if provider is None or not oauth.provider_available(provider_key):
            raise MailboxError("Provider is no longer available.", 400)

        client_id, client_secret = oauth.provider_credentials(provider_key)
        try:
            token_response = oauth.exchange_code(
                provider,
                code=code,
                client_id=client_id or "",
                client_secret=client_secret or "",
                redirect=oauth.redirect_uri(request_base_url),
            )
        except oauth.OAuthExchangeError as exc:
            raise MailboxError(f"Could not connect the mailbox: {exc}", 502) from exc

        account_email = _decode_jwt_email(token_response.get("id_token", "")) or "unknown"
        vault = get_vault()
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")

        conn = self.repo.upsert_connection(
            org_id=org_id,
            provider=provider_key,
            account_email=account_email,
            connected_by=user_id,
            access_token_enc=vault.encrypt(access_token) if access_token else None,
            refresh_token_enc=vault.encrypt(refresh_token) if refresh_token else None,
            token_expires_at=_expires_at_iso(token_response),
            scopes=" ".join(provider.scopes),
        )
        self.audit.record(
            action="mailbox.connect",
            org_id=org_id,
            actor_user_id=user_id,
            target=conn["id"],
            detail={"provider": provider_key, "account_email": account_email},
        )
        return conn

    def disconnect(self, *, org_id: str, user_id: str, connection_id: str) -> bool:
        conn = self.repo.get(org_id, connection_id)
        if not conn:
            return False
        removed = self.repo.delete(org_id, connection_id) or {}
        self.audit.record(
            action="mailbox.disconnect",
            org_id=org_id,
            actor_user_id=user_id,
            target=connection_id,
            detail={
                "provider": conn["provider"],
                "account_email": conn["account_email"],
                "messages_removed": removed.get("messages", 0),
                "actions_removed": removed.get("actions", 0),
            },
        )
        return True
