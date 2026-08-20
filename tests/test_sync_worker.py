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
    return {"checked", "synced", "messages", "proposed", "errors"}


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
