"""Gmail provider + provider factory tests. No network — transport is injected."""

from __future__ import annotations

import base64
import uuid

from fastapi.testclient import TestClient

from env.api import app
from env.product.providers.gmail import GmailProvider
from env.saas import oauth, provider_factory
from env.saas.crypto import get_vault
from env.saas.repository import MailboxRepository


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _full_message(mid: str) -> dict:
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "internalDate": "1690000000000",
        "snippet": "preview",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Alex Vance <alex@client.example>"},
                {"name": "Subject", "value": "Quarterly review"},
                {"name": "References", "value": ""},
            ],
            "body": {"data": _b64url("The full plain-text body.")},
        },
    }


class RecordingTransport:
    """Routes Gmail API calls to canned responses and records every call."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, dict]]):
        self._responses = responses
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, method, url, token, json_body):
        self.calls.append((method, url, token))
        for (m, needle), resp in self._responses.items():
            if m == method and needle in url:
                return resp
        return 200, {}


class TestGmailProvider:
    def test_fetch_maps_messages(self):
        transport = RecordingTransport(
            {
                ("GET", "/messages?"): (200, {"messages": [{"id": "g1"}]}),
                ("GET", "/messages/g1"): (200, _full_message("g1")),
            }
        )
        provider = GmailProvider("tok", transport=transport)
        msgs = provider.fetch_messages()
        assert len(msgs) == 1
        assert msgs[0].provider_message_id == "g1"
        assert msgs[0].sender == "alex@client.example"
        assert msgs[0].sender_name == "Alex Vance"
        assert msgs[0].subject == "Quarterly review"
        assert "full plain-text body" in msgs[0].body

    def test_401_triggers_single_refresh_and_retry(self):
        state = {"n": 0}

        def transport(method, url, token, json_body):
            state["n"] += 1
            # First call 401 with the stale token, then 200 with the fresh one.
            if state["n"] == 1:
                return 401, {"error": "invalid_credentials"}
            return 200, {"messages": []}

        refreshed = {"count": 0}

        def refresher():
            refreshed["count"] += 1
            return "fresh-token"

        provider = GmailProvider("stale", token_refresher=refresher, transport=transport)
        provider.fetch_messages()
        assert refreshed["count"] == 1
        assert provider._token == "fresh-token"

    def test_send_reply_hits_send_endpoint(self):
        transport = RecordingTransport(
            {
                ("GET", "/messages/g1"): (
                    200,
                    {
                        "threadId": "t-g1",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "boss@client.example"},
                                {"name": "Subject", "value": "Budget"},
                            ]
                        },
                    },
                ),
                ("POST", "/messages/send"): (200, {"id": "sent-1"}),
            }
        )
        provider = GmailProvider("tok", transport=transport)
        result = provider.send_reply("g1", "On it.")
        assert result.ok
        assert result.provider_ref == "sent-1"
        assert any(m == "POST" and "/messages/send" in u for m, u, _ in transport.calls)

    def test_create_draft_hits_drafts_endpoint(self):
        transport = RecordingTransport(
            {
                ("GET", "/messages/g1"): (
                    200,
                    {
                        "threadId": "t-g1",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "boss@client.example"},
                                {"name": "Subject", "value": "Budget"},
                            ]
                        },
                    },
                ),
                ("POST", "/drafts"): (200, {"id": "draft-1"}),
            }
        )
        provider = GmailProvider("tok", transport=transport)
        result = provider.create_draft("g1", "Escalating this.")
        assert result.ok
        assert result.provider_ref == "draft-1"
        assert any(m == "POST" and "/drafts" in u for m, u, _ in transport.calls)

    def test_archive_removes_inbox_label(self):
        transport = RecordingTransport({("POST", "/messages/g1/modify"): (200, {"id": "g1"})})
        provider = GmailProvider("tok", transport=transport)
        result = provider.archive("g1")
        assert result.ok
        assert any("/messages/g1/modify" in u for _, u, _ in transport.calls)

    def test_write_failure_returns_not_ok(self):
        # Bug 1b: a write that hits an API error returns WriteResult(ok=False),
        # it does NOT raise — so one failure can't abort a whole sync batch.
        transport = RecordingTransport({("POST", "/messages/g1/modify"): (500, {"error": "boom"})})
        provider = GmailProvider("tok", transport=transport)
        result = provider.add_label("g1", "urgent")
        assert result.ok is False
        assert result.detail  # carries the error message


class TestProviderFactory:
    def _signup_org(self, client) -> dict:
        resp = client.post(
            "/auth/signup",
            json={
                "email": f"o_{uuid.uuid4().hex[:10]}@example.com",
                "password": "hunter2pass",
                "full_name": "O",
                "org_name": "Acme",
            },
        )
        return resp.json()

    def test_build_provider_decrypts_and_returns_gmail(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
        client = TestClient(app)
        org = self._signup_org(client)
        vault = get_vault()
        conn = MailboxRepository().upsert_connection(
            org_id=org["organization"]["id"],
            provider="google",
            account_email="exec@acme.example",
            connected_by=org["user"]["id"],
            access_token_enc=vault.encrypt("access-A"),
            refresh_token_enc=vault.encrypt("refresh-A"),
            token_expires_at=None,
            scopes="https://www.googleapis.com/auth/gmail.modify",
        )
        provider = provider_factory.build_provider(conn)
        assert isinstance(provider, GmailProvider)
        assert provider._token == "access-A"

    def test_refresh_persists_new_token(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
        client = TestClient(app)
        org = self._signup_org(client)
        vault = get_vault()
        conn = MailboxRepository().upsert_connection(
            org_id=org["organization"]["id"],
            provider="google",
            account_email="exec2@acme.example",
            connected_by=org["user"]["id"],
            access_token_enc=vault.encrypt("access-old"),
            refresh_token_enc=vault.encrypt("refresh-A"),
            token_expires_at=None,
            scopes=None,
        )
        # Patch the network refresh to return a new access token.
        monkeypatch.setattr(
            oauth,
            "refresh_tokens",
            lambda provider, **kw: {"access_token": "access-new", "expires_in": 3600},
        )
        provider = provider_factory.build_provider(conn)
        new_token = provider._refresh()  # invoke the injected refresher
        assert new_token == "access-new"
        # Persisted (re-encrypted) in the DB.
        stored = MailboxRepository().get_with_tokens(conn["org_id"], conn["id"])
        assert vault.decrypt(stored["access_token_enc"]) == "access-new"

    def test_unconfigured_falls_back_to_fake(self):
        from env.product.providers.fake import FakeProvider

        conn = {"provider": "google", "org_id": "x", "id": "y"}
        # No such connection in DB -> no token -> FakeProvider.
        assert isinstance(provider_factory.build_provider(conn), FakeProvider)
