"""Background sync worker: cadence, failure isolation, batched label writes.

No network and no real timers — the sweep is called directly with a controlled
clock, and provider I/O is the in-memory FakeProvider or a recording stub.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.copilot.providers.base import WriteResult
from app.copilot.providers.fake import FakeProvider
from app.core.config import get_settings
from app.main import app
from app.saas.repository import (
    MailboxRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
)
from app.saas.sync_service import InboxSyncService
from app.saas.sync_worker import BackgroundSyncWorker


def _signup_org(client) -> dict:
    resp = client.post(
        "/auth/signup",
        json={
            "email": f"w_{uuid.uuid4().hex[:10]}@example.com",
            "password": "hunter2pass",
            "full_name": "W",
            "org_name": "Worker Org",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _dev_connection(org: dict) -> dict:
    """A connection whose provider key resolves to the in-memory FakeProvider."""
    return MailboxRepository().upsert_connection(
        org_id=org["organization"]["id"],
        provider="imap-dev",
        account_email=f"exec-{uuid.uuid4().hex[:8]}@worker.example",
        connected_by=org["user"]["id"],
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )


def _empty_summary_keys():
    return {"checked", "synced", "messages", "proposed", "errors", "retried", "recovered"}


def test_worker_is_opt_in():
    """Tests, scripts and one-shot commands must never grow surprise threads."""
    assert get_settings().sync_worker_enabled is False


class TestCadence:
    def _conn(self, connection_id="c-1", last_synced_at=None) -> dict:
        return {"id": connection_id, "last_synced_at": last_synced_at}

    def test_never_synced_is_due_immediately(self):
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.2)
        assert worker.is_due(self._conn(last_synced_at=None))

    def test_recent_sync_is_not_due(self):
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.2)
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=30)).isoformat()
        assert not worker.is_due(self._conn(last_synced_at=recent), now=now)

    def test_old_sync_is_due(self):
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.2)
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=3600)).isoformat()
        assert worker.is_due(self._conn(last_synced_at=old), now=now)

    def test_jitter_is_stable_and_bounded(self):
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.2)
        j1 = worker._jitter("conn-abc")
        j2 = worker._jitter("conn-abc")
        assert j1 == j2
        assert 0.0 <= j1 < 0.2
        # Different connections generally land on different offsets.
        others = {worker._jitter(f"conn-{i}") for i in range(20)}
        assert len(others) > 1

    def test_jitter_delays_the_exact_interval_boundary(self):
        """At exactly interval seconds old, a connection with non-zero jitter
        is NOT yet due — the fleet doesn't sync in lockstep."""
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.2)
        conn_id = next(f"c-{i}" for i in range(100) if worker._jitter(f"c-{i}") > 0.01)
        now = datetime.now(timezone.utc)
        at_boundary = (now - timedelta(seconds=300)).isoformat()
        assert not worker.is_due(self._conn(conn_id, at_boundary), now=now)

    def test_failed_connection_backs_off_a_full_interval(self):
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.0)
        now = datetime.now(timezone.utc)
        conn = self._conn("c-backoff", last_synced_at=None)
        worker._retry_after["c-backoff"] = now + timedelta(seconds=300)
        assert not worker.is_due(conn, now=now)
        assert worker.is_due(conn, now=now + timedelta(seconds=301))


class TestSweep:
    def test_syncs_a_due_connection_end_to_end(self, monkeypatch):
        client = TestClient(app)
        org = _signup_org(client)
        conn = _dev_connection(org)
        monkeypatch.setattr(
            MailboxRepository, "list_all_connected", lambda self: [conn], raising=True
        )
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.0)
        summary = worker.sync_due_connections()
        assert set(summary) == _empty_summary_keys()
        assert summary["synced"] == 1
        assert summary["messages"] == 4  # the FakeProvider fixture inbox
        assert summary["errors"] == 0
        # The sync persisted messages and advanced the cadence clock.
        stored = MailboxRepository().get(conn["org_id"], conn["id"])
        assert stored["last_synced_at"]
        inbox = ProcessedMessageRepository().list_for_org(conn["org_id"])
        assert len(inbox) == 4

        # A second sweep sees a fresh last_synced_at and does nothing.
        fresh = MailboxRepository().get(conn["org_id"], conn["id"])
        monkeypatch.setattr(
            MailboxRepository, "list_all_connected", lambda self: [fresh], raising=True
        )
        again = worker.sync_due_connections()
        assert again["synced"] == 0

    def test_one_broken_connection_does_not_stop_the_others(self, monkeypatch):
        client = TestClient(app)
        org_a = _signup_org(client)
        org_b = _signup_org(client)
        conn_a = _dev_connection(org_a)
        conn_b = _dev_connection(org_b)
        monkeypatch.setattr(
            MailboxRepository,
            "list_all_connected",
            lambda self: [conn_a, conn_b],
            raising=True,
        )

        from app.saas import provider_factory

        real_build = provider_factory.build_provider

        def flaky_build(connection):
            if connection["id"] == conn_a["id"]:
                raise RuntimeError("provider exploded")
            return real_build(connection)

        monkeypatch.setattr(provider_factory, "build_provider", flaky_build)
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.0)
        summary = worker.sync_due_connections()
        assert summary["errors"] == 1
        assert summary["synced"] == 1  # conn_b still synced
        # The failure backs off instead of retrying every poll.
        assert conn_a["id"] in worker._retry_after

    def test_lapsed_plan_is_skipped_not_crashed(self, monkeypatch):
        client = TestClient(app)
        org = _signup_org(client)
        conn = _dev_connection(org)
        monkeypatch.setattr(
            MailboxRepository, "list_all_connected", lambda self: [conn], raising=True
        )

        from app.saas.billing import BillingError, BillingService

        def lapsed(self, org_id):
            raise BillingError("Plan expired", 402)

        monkeypatch.setattr(BillingService, "require_active", lapsed)
        worker = BackgroundSyncWorker(interval_seconds=300, jitter_fraction=0.0)
        summary = worker.sync_due_connections()
        assert summary["errors"] == 1
        assert summary["synced"] == 0
        assert conn["id"] in worker._retry_after


def test_start_and_stop_cleanly():
    """The lifespan contract: start() schedules the loop, stop() ends it."""

    async def scenario():
        worker = BackgroundSyncWorker(interval_seconds=300, poll_seconds=0.01)
        passes = []

        def fake_pass(now=None):
            passes.append(1)
            return {"checked": 0, "synced": 0, "messages": 0, "proposed": 0, "errors": 0}

        worker.sync_due_connections = fake_pass
        worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()
        return passes

    passes = asyncio.run(scenario())
    assert passes  # at least one pass ran, and stop() returned


class TestBatchedLabelWrites:
    class BatchingFake(FakeProvider):
        """FakeProvider plus the optional batch write, both surfaces recorded."""

        def __init__(self):
            super().__init__()
            self.batch_calls: list[tuple[list[str], str]] = []
            self.single_calls: list[tuple[str, str]] = []

        def add_labels_batch(self, provider_message_ids, label):
            self.batch_calls.append((list(provider_message_ids), label))
            return WriteResult(ok=True)

        def add_label(self, provider_message_id, label):
            self.single_calls.append((provider_message_id, label))
            return super().add_label(provider_message_id, label)

    def test_label_groups_use_one_provider_call(self):
        client = TestClient(app)
        org = _signup_org(client)
        conn = _dev_connection(org)
        provider = self.BatchingFake()
        result = InboxSyncService().sync(
            org_id=conn["org_id"],
            user_id=org["user"]["id"],
            connection_id=conn["id"],
            provider=provider,
        )
        # The fixture inbox yields two 'urgent' classifies — one batch call —
        # while single-message labels keep the per-message write.
        assert (["m-legal-1", "m-urgent-1"], "urgent") in provider.batch_calls
        assert all(label != "urgent" for _, label in provider.single_calls)
        assert result["auto_executed"] == 5
        # Every auto action row landed as executed.
        listing = ProposedActionRepository().list_for_org(conn["org_id"])
        actions = [a for a in listing["actions"] if not a["requires_approval"]]
        assert actions and all(a["status"] == "executed" for a in actions)


class TestFailedSendRetry:
    """A reply a human approved that the provider then refused used to sit at
    status="failed" with nothing on any code path picking it up. That is the
    product silently not doing the one thing it was told to do."""

    class FlakyProvider(FakeProvider):
        """Refuses the first `fail_times` sends, then succeeds."""

        def __init__(self, fail_times: int = 1):
            super().__init__()
            self.remaining_failures = fail_times
            self.send_attempts = 0

        def send_reply(self, provider_message_id, body):
            self.send_attempts += 1
            if self.remaining_failures > 0:
                self.remaining_failures -= 1
                return WriteResult(ok=False, detail="mailbox quota exceeded")
            return super().send_reply(provider_message_id, body)

    def _approved_but_failed(self, client, provider):
        """Sync, approve one reply against a provider that refuses it."""
        org = _signup_org(client)
        conn = _dev_connection(org)
        service = InboxSyncService()
        service.sync(
            org_id=conn["org_id"],
            user_id=org["user"]["id"],
            connection_id=conn["id"],
            provider=FakeProvider(),
        )
        actions = ProposedActionRepository()
        reply = next(
            a
            for a in actions.list_for_org(conn["org_id"], status="proposed")["actions"]
            if a["action_type"] == "reply"
        )
        service.approve(
            org_id=conn["org_id"],
            user_id=org["user"]["id"],
            action_id=reply["id"],
            provider=provider,
        )
        stored = actions.get(conn["org_id"], reply["id"])
        assert stored["status"] == "failed"
        return conn["org_id"], reply["id"]

    def test_a_failed_send_records_why(self):
        client = TestClient(app)
        org_id, action_id = self._approved_but_failed(client, self.FlakyProvider(fail_times=99))
        stored = ProposedActionRepository().get(org_id, action_id)
        assert "quota" in (stored["last_error"] or "")

    def test_a_transient_failure_is_retried_and_recovers(self, monkeypatch):
        client = TestClient(app)
        provider = self.FlakyProvider(fail_times=1)
        org_id, action_id = self._approved_but_failed(client, provider)

        # The retry builds its own provider from the connection; hand it ours.
        monkeypatch.setattr(
            "app.saas.provider_factory.build_provider", lambda conn: provider, raising=True
        )
        result = InboxSyncService().retry_failed_sends(org_id=org_id)
        assert result == {"attempted": 1, "recovered": 1, "still_failing": 0}

        stored = ProposedActionRepository().get(org_id, action_id)
        assert stored["status"] == "executed"
        assert stored["execution_ref"]
        assert stored["retry_count"] == 1
        assert stored["last_error"] is None

    def test_retries_are_bounded(self, monkeypatch):
        """A permanently-bad recipient must not be retried forever."""
        from app.saas.sync_service import MAX_SEND_RETRIES

        client = TestClient(app)
        provider = self.FlakyProvider(fail_times=999)
        org_id, action_id = self._approved_but_failed(client, provider)
        monkeypatch.setattr(
            "app.saas.provider_factory.build_provider", lambda conn: provider, raising=True
        )

        service = InboxSyncService()
        for _ in range(MAX_SEND_RETRIES + 3):
            service.retry_failed_sends(org_id=org_id)

        stored = ProposedActionRepository().get(org_id, action_id)
        assert stored["status"] == "failed"
        assert stored["retry_count"] == MAX_SEND_RETRIES

    def test_auto_applied_labels_are_never_retried(self, monkeypatch):
        """Re-firing writes nobody approved is exactly what the approval gate
        exists to prevent."""
        client = TestClient(app)
        org = _signup_org(client)
        conn = _dev_connection(org)

        class LabelRefuser(FakeProvider):
            def add_label(self, provider_message_id, label):
                return WriteResult(ok=False, detail="label service down")

        InboxSyncService().sync(
            org_id=conn["org_id"],
            user_id=org["user"]["id"],
            connection_id=conn["id"],
            provider=LabelRefuser(),
        )
        actions = ProposedActionRepository()
        failed_labels = [
            a
            for a in actions.list_for_org(conn["org_id"], limit=200)["actions"]
            if a["status"] == "failed" and not a["requires_approval"]
        ]
        assert failed_labels, "the refusing provider must have failed some label writes"
        assert actions.list_failed_sends(conn["org_id"], max_retries=3) == []

    def test_the_sweep_retries_and_reports(self, monkeypatch):
        client = TestClient(app)
        provider = self.FlakyProvider(fail_times=1)
        org_id, _action_id = self._approved_but_failed(client, provider)
        monkeypatch.setattr(
            "app.saas.provider_factory.build_provider", lambda conn: provider, raising=True
        )

        summary = BackgroundSyncWorker().sync_due_connections()
        assert summary["retried"] >= 1
        assert summary["recovered"] >= 1

    def test_the_approvals_page_shows_what_did_not_send(self):
        """It used to be on no page at all, so a reviewer believed they had
        sent something they had not."""
        import re as _re

        web = TestClient(app, follow_redirects=False)
        page = web.get("/signup").text
        csrf = _re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        email = f"fs_{uuid.uuid4().hex[:10]}@failed.example"
        assert (
            web.post(
                "/signup",
                data={
                    "csrf_token": csrf,
                    "org_name": "Failed Sends",
                    "full_name": "F",
                    "email": email,
                    "password": "a-strong-password",
                },
            ).status_code
            == 303
        )
        page = web.get("/app/connect").text
        csrf = _re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        assert web.post("/app/connect/demo", data={"csrf_token": csrf}).status_code == 303

        from app.saas.repository import UserRepository

        org_id = UserRepository().get_by_email_global(email)["org_id"]
        actions = ProposedActionRepository()
        target = next(
            a
            for a in actions.list_for_org(org_id, status="proposed")["actions"]
            if a["action_type"] == "reply"
        )
        actions.set_status(
            org_id,
            target["id"],
            "failed",
            decided_by="someone",
            decided_at=datetime.now(timezone.utc).isoformat(),
            last_error="recipient rejected the message",
        )

        html = web.get("/app/approvals").text
        assert "Did not send" in html
        assert "recipient rejected the message" in html
