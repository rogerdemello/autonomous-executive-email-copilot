"""A cold workspace, a real OAuth connect, and one mailbox for both providers.

Everything here goes over HTTP into the app. The workspace is created by posting
the signup form; the mailbox is attached by posting the connect form, following
the state token the app itself minted, and letting the provider "redirect" back
to ``/mailbox/oauth/callback``. ``TestClient`` runs background tasks before it
returns a response, so by the time the callback answers, the first sync has run
through the fake wire — which makes steps 1 to 3 of ``LAUNCH_CHECKLIST.md``'s
end-to-end test a fixture.

The same five messages are handed to both wires. A Gmail account and a Microsoft
365 account containing identical mail must produce identical rows, and the
places where they did not were the bugs.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core.db import migrate_db
from app.main import app
from app.saas.repository import (
    CommitmentRepository,
    MailboxRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
)
from app.saas.repository import UserRepository as _UserRepository

from . import wire as w

ACCOUNT_EMAIL = "alex@northwind.example"

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

_ENV_PREFIX = {"google": "GOOGLE", "microsoft": "MICROSOFT"}


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "every page with a form must embed a CSRF token"
    return match.group(1)


# --------------------------------------------------------------------------- #
# The mailbox
# --------------------------------------------------------------------------- #
def mailbox() -> list[w.WireMessage]:
    """Five messages, chosen so that one sweep exercises every routing outcome.

    Between them: a reply held for a human, an escalation to a role, two
    auto-filed defers and a spam verdict. None of the bodies is the plain text
    the demo mailbox is made of — the two that matter most are HTML-only, which
    is what a real inbox mostly is.

    Deliberately listed out of order. Both wires sort on ``received_at``, so
    "newest first" is a property of the fetch and not of this list.
    """
    return [
        # Internal, and the one message with a plain-text alternative: the
        # sender's own wrapping must win over the HTML rendering of it.
        w.message(
            id="m-notes",
            sender="priya@northwind.example",
            sender_name="Priya Raman",
            subject="Notes from Tuesday",
            received_at="2026-09-24T16:05:00+00:00",
            text=(
                "Alex — my notes from Tuesday are attached below.\n"
                "I'll send the consolidated summary on Friday."
            ),
            html=(
                "<div><p>Alex &mdash; my notes from Tuesday are attached below.</p>"
                "<p>I&rsquo;ll send the consolidated summary on Friday.</p></div>"
            ),
        ),
        # A vendor notification: HTML only, no plain part, no part structure at
        # all — a single top-level text/html body, the way most machines send.
        w.message(
            id="m-status",
            sender="no-reply@statuspage.example",
            sender_name="Platform Status",
            subject="Weekly platform summary",
            received_at="2026-09-22T06:00:00+00:00",
            html=(
                "<html><head><style>.x{color:#f00}</style></head><body>"
                "<table><tr><td>All systems nominal this week.</td></tr>"
                "<tr><td>Next summary lands Monday.</td></tr></table></body></html>"
            ),
        ),
        # The message the product exists for. HTML only, from a client, with a
        # Reply-To that is not the From — a ticket system, which is where the
        # approved reply has to land.
        w.message(
            id="m-launch",
            sender="dana.reyes@gmail.com",
            sender_name="Dana Reyes",
            subject="Urgent: launch date is slipping",
            received_at="2026-09-25T09:41:00+00:00",
            reply_to="tickets+dana@helpdesk.example",
            references="<earlier-note@mail.example>",
            html=(
                '<div dir="ltr" style="font-family:Arial"><p>Hi Alex,</p>'
                "<p>The cutover rehearsal <b>slipped</b> again and my board is "
                "asking for a date.</p>"
                '<p>Please confirm the revised timeline by <a href="https://cal.example">'
                "Thursday</a>.</p><p>Dana</p></div>"
            ),
            categories=("Clients",),
        ),
        # Spam that contains a perfectly well-formed promise. It must be filed
        # as spam and it must not reach the follow-up list.
        w.message(
            id="m-deal",
            sender="deals@marketingblast.example",
            sender_name="Marketing Blast",
            subject="Limited deal for your team",
            received_at="2026-09-21T11:20:00+00:00",
            text=(
                "Subscribe now and we will register your team at a permanent discount.\n"
                "This offer closes Friday."
            ),
        ),
        # Legal risk: escalates to a role, not to a person, and never to Dana.
        w.message(
            id="m-contract",
            sender="counsel@vendorlegal.example",
            sender_name="R. Okonjo",
            subject="Contract liability clause mismatch",
            received_at="2026-09-25T08:15:00+00:00",
            html=(
                "<p>Counsel here. The liability cap in the signed contract does not "
                "match the schedule you countersigned.</p>"
                "<p>We need your position before we file anything.</p>"
            ),
            categories=("Legal hold",),
        ),
    ]


def google_wire(messages: list[w.WireMessage] | None = None) -> w.GoogleWire:
    return w.GoogleWire(mailbox() if messages is None else messages, account_email=ACCOUNT_EMAIL)


def microsoft_wire(messages: list[w.WireMessage] | None = None) -> w.MicrosoftWire:
    return w.MicrosoftWire(mailbox() if messages is None else messages, account_email=ACCOUNT_EMAIL)


# --------------------------------------------------------------------------- #
# The workspace
# --------------------------------------------------------------------------- #
def pin_sweep_to_workspace(monkeypatch, workspace) -> None:
    """Limit the background worker's work list to this workspace's mailboxes.

    ``list_all_connected`` is deliberately cross-tenant — the worker is a system
    actor — and this suite shares one database, so an unpinned sweep also picks
    up mailboxes other tests left behind, whose fake wires are long gone. The
    ``connected`` filter is kept, because "a broken mailbox drops out of the
    sweep" is one of the properties under test.
    """
    org_id = workspace.org_id

    def _work_list(self) -> list[dict]:
        return [c for c in MailboxRepository().list_for_org(org_id) if c["status"] == "connected"]

    monkeypatch.setattr(MailboxRepository, "list_all_connected", _work_list, raising=True)
    # The same applies to the retry half of a sweep, which is cross-tenant for
    # the same reason. Left unpinned it re-dispatches whatever failed sends other
    # tests left in the shared database, through mailboxes whose wires are gone —
    # which today happens to stop at "no escalation contact" before reaching the
    # network, and would stop being harmless the first time a test leaves a
    # failed *reply* behind.
    monkeypatch.setattr(
        ProposedActionRepository,
        "orgs_with_failed_sends",
        lambda self, max_retries: [org_id],
        raising=True,
    )


class Workspace:
    """A signed-in workspace, driven the way its owner would drive it."""

    def __init__(self, client: TestClient, email: str, org_id: str, user_id: str) -> None:
        self.client = client
        self.email = email
        self.org_id = org_id
        self.user_id = user_id
        self.messages = ProcessedMessageRepository()
        self.actions = ProposedActionRepository()
        self.commitments = CommitmentRepository()
        self.mailboxes = MailboxRepository()

    # -- browsing ------------------------------------------------------------
    def csrf(self) -> str:
        return csrf_from(self.client.get("/app/connect").text)

    def page(self, path: str) -> str:
        response = self.client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        return response.text

    def inbox(self, **params) -> str:
        response = self.client.get("/app/inbox", params=params)
        assert response.status_code == 200
        return response.text

    # -- acting --------------------------------------------------------------
    def sync_now(self):
        """Press Sync. Returns the response so a failure can be asserted on."""
        return self.client.post("/app/sync", data={"csrf_token": self.csrf()})

    def approve(self, action_id: str, content: str | None = None):
        data = {"csrf_token": self.csrf()}
        if content is not None:
            data["content"] = content
        return self.client.post(f"/app/actions/{action_id}/approve", data=data)

    def reject(self, action_id: str):
        return self.client.post(
            f"/app/actions/{action_id}/reject", data={"csrf_token": self.csrf()}
        )

    def set_escalation_contact(self, role: str, email: str):
        response = self.client.post(
            "/app/settings/escalation",
            data={"csrf_token": self.csrf(), "role": role, "email": email},
        )
        assert response.status_code == 303, response.text[:400]
        return response

    # -- reading the state it produced ---------------------------------------
    def rows(self) -> list[dict]:
        return self.messages.list_for_org(self.org_id)["messages"]

    def row(self, provider_message_id: str) -> dict:
        found = [r for r in self.rows() if r["provider_message_id"] == provider_message_id]
        assert found, f"{provider_message_id} was never synced"
        return found[0]

    def action_list(self, **kwargs) -> list[dict]:
        return self.actions.list_for_org(self.org_id, **kwargs)["actions"]

    def held(self, action_type: str) -> dict:
        found = [a for a in self.action_list(status="proposed") if a["action_type"] == action_type]
        assert found, f"no {action_type} is waiting for a human"
        return found[0]

    def connection(self, provider: str) -> dict:
        found = [c for c in self.mailboxes.list_for_org(self.org_id) if c["provider"] == provider]
        assert found, f"no {provider} mailbox is connected"
        return found[0]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _schema():
    migrate_db()


@pytest.fixture
def client() -> TestClient:
    # Redirects are not followed: the connect leg redirects to a provider
    # consent URL that must never actually be requested.
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def workspace(client) -> Workspace:
    """Signed up cold, over the signup form, as a stranger would."""
    email = f"alex_{uuid.uuid4().hex[:10]}@northwind.example"
    page = client.get("/signup").text
    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_from(page),
            "org_name": "Northwind Industries",
            "full_name": "Alex Chen",
            "email": email,
            "password": "a-strong-password",
        },
    )
    assert response.status_code == 303, response.text[:400]
    user = _UserRepository().get_by_email_global(email)
    assert user
    return Workspace(client, email, user["org_id"], user["id"])


class _Connector:
    """Attaches mailboxes by running the product's own OAuth flow.

    Wires accumulate: connecting Gmail and then Microsoft 365 to one workspace
    leaves both installed, so a call to either API is served and a call to
    anything else still lands in the unrouted log.
    """

    def __init__(self, monkeypatch, workspace: Workspace) -> None:
        self._monkeypatch = monkeypatch
        self._workspace = workspace
        self._wires: list[w._Wire] = []
        self.unrouted_logs: list[list[tuple[str, str]]] = []

    def __call__(self, wire, provider_key: str, *, expect_first_sync: bool = True) -> dict:
        self._wires.append(wire)
        self.unrouted_logs.append(w.install(self._monkeypatch, *self._wires))

        prefix = _ENV_PREFIX[provider_key]
        self._monkeypatch.setenv(f"{prefix}_OAUTH_CLIENT_ID", f"{provider_key}-client-id")
        self._monkeypatch.setenv(f"{prefix}_OAUTH_CLIENT_SECRET", f"{provider_key}-secret")

        client = self._workspace.client
        started = client.post(
            f"/app/connect/{provider_key}", data={"csrf_token": self._workspace.csrf()}
        )
        assert started.status_code == 303, started.text[:400]
        consent_url = started.headers["location"]
        state = parse_qs(urlsplit(consent_url).query)["state"][0]

        # The provider hands the browser back with the code it granted.
        returned = client.get(
            "/mailbox/oauth/callback", params={"code": wire.authorize(), "state": state}
        )
        assert returned.status_code == 303, returned.text[:400]

        connection = self._workspace.connection(provider_key)
        if expect_first_sync:
            assert connection["last_synced_at"], (
                "the first sync ran in the background and failed silently — "
                "the callback swallows the exception on purpose, so run "
                "InboxSyncService().sync directly to see it"
            )
        return connection


@pytest.fixture
def connect(monkeypatch, workspace) -> _Connector:
    connector = _Connector(monkeypatch, workspace)
    yield connector
    for log in connector.unrouted_logs:
        assert log == [], (
            f"a provider call went to a URL no fake wire serves: {log}. "
            "Either the URL is wrong or the wire needs to learn that route."
        )
