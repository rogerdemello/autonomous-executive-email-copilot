"""Background inbox sync: the copilot works connected mailboxes on a cadence.

An asyncio task started from the app lifespan (opt-in via ``SYNC_WORKER_ENABLED``)
that periodically sweeps every connected mailbox across all orgs and runs the
same :class:`~app.saas.sync_service.InboxSyncService` the "Sync now" button
uses. Design constraints, in order:

- **The cadence is persistent.** Due-ness is computed from the connection's
  stored ``last_synced_at``, not in-process timers, so a restart neither
  re-syncs everything at once nor forgets who is overdue.
- **Connections don't share fate.** One broken mailbox (revoked token, lapsed
  plan, provider 500) is logged and backed off; every other connection still
  syncs on schedule.
- **The sweep is jittered.** Each connection gets a stable per-id offset on top
  of the base interval so a fleet of mailboxes doesn't hit the provider APIs in
  lockstep after a deploy.
- **The event loop stays free.** All DB and provider I/O is synchronous, so a
  pass runs in a worker thread via ``asyncio.to_thread``; the loop only ever
  awaits.
"""

from __future__ import annotations

import asyncio
import logging
import zlib
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class BackgroundSyncWorker:
    """Sweeps connected mailboxes and syncs the ones whose cadence is due."""

    def __init__(
        self,
        *,
        interval_seconds: int | None = None,
        jitter_fraction: float | None = None,
        poll_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.interval_seconds = (
            settings.sync_worker_interval_seconds if interval_seconds is None else interval_seconds
        )
        self.jitter_fraction = (
            settings.sync_worker_jitter_fraction if jitter_fraction is None else jitter_fraction
        )
        self.poll_seconds = (
            settings.sync_worker_poll_seconds if poll_seconds is None else poll_seconds
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        # Connections that just failed wait a full interval before the next
        # attempt — without this, a lapsed plan or flaky provider would be
        # retried every poll, since a failed sync never advances last_synced_at.
        self._retry_after: dict[str, datetime] = {}
        # Proof of life. Every pass used to be logged and dropped, so a worker
        # that had died was invisible: /health/ready still returned 200 and the
        # only symptom was a queue that stopped filling — which is exactly what
        # a quiet mailbox looks like. Kept in memory on purpose; this answers
        # "is *this* process still sweeping", which does not survive a restart
        # and should not pretend to.
        self.started_at: datetime | None = None
        self.last_pass_at: datetime | None = None
        self.last_pass: dict | None = None
        self.passes = 0
        self.recent: list[dict] = []

    # -- cadence --------------------------------------------------------------
    def _jitter(self, connection_id: str) -> float:
        """A stable per-connection fraction in [0, jitter_fraction)."""
        return (zlib.crc32(connection_id.encode()) % 1000) / 1000.0 * self.jitter_fraction

    def is_due(self, connection: dict, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        retry_at = self._retry_after.get(connection["id"])
        if retry_at and now < retry_at:
            return False
        last = _parse_iso(connection.get("last_synced_at"))
        if last is None:
            return True  # never synced — a fresh connection syncs immediately
        age = (now - last).total_seconds()
        return age >= self.interval_seconds * (1.0 + self._jitter(connection["id"]))

    # -- one pass (synchronous; runs in a worker thread) -----------------------
    def sync_due_connections(self, now: datetime | None = None) -> dict:
        from .provider_factory import BrokenConnectionError, build_provider
        from .repository import MailboxRepository
        from .sync_service import InboxSyncService, ProcessingError

        now = now or datetime.now(timezone.utc)
        service = InboxSyncService()
        summary = {
            "checked": 0,
            "synced": 0,
            "messages": 0,
            "proposed": 0,
            "errors": 0,
            "retried": 0,
            "recovered": 0,
        }
        for conn in MailboxRepository().list_all_connected():
            summary["checked"] += 1
            if not self.is_due(conn, now=now):
                continue
            try:
                result = service.sync(
                    org_id=conn["org_id"],
                    user_id=conn.get("connected_by"),
                    connection_id=conn["id"],
                    provider=build_provider(conn),
                )
            except BrokenConnectionError as exc:
                # build_provider already flagged the row status "error", which
                # removes it from the next sweep until a human reconnects.
                summary["errors"] += 1
                logger.warning("Background sync: connection %s is broken: %s", conn["id"], exc)
            except ProcessingError as exc:
                # e.g. a lapsed plan (402) — expected, not an incident.
                summary["errors"] += 1
                self._retry_after[conn["id"]] = now + timedelta(seconds=self.interval_seconds)
                logger.info("Background sync skipped connection %s: %s", conn["id"], exc.message)
            except Exception:
                summary["errors"] += 1
                self._retry_after[conn["id"]] = now + timedelta(seconds=self.interval_seconds)
                logger.exception("Background sync failed for connection %s", conn["id"])
            else:
                summary["synced"] += 1
                summary["messages"] += result.get("messages", 0)
                summary["proposed"] += result.get("proposed", 0)
                self._retry_after.pop(conn["id"], None)

        # A reply a human approved that the provider then refused used to sit
        # at status="failed" forever, with nothing on any code path picking it
        # up. That is the product silently not doing the one thing it was
        # explicitly told to do, so it belongs in the sweep.
        summary.update(self._retry_failed_sends(service))
        self._record_pass(summary, now)
        return summary

    # -- heartbeat ------------------------------------------------------------
    # Ten passes is a few hours of history at the production cadence — enough to
    # see a pattern, small enough to never need trimming logic anyone thinks about.
    _RECENT_LIMIT = 10

    def _record_pass(self, summary: dict, now: datetime) -> None:
        self.passes += 1
        self.last_pass_at = now
        self.last_pass = dict(summary)
        self.recent.append({"at": now.isoformat(), **summary})
        del self.recent[: -self._RECENT_LIMIT]

    def stale_after_seconds(self) -> float:
        """How long without a pass before the worker is presumed dead.

        Three polls: one missed poll is a slow sync holding the thread, three in
        a row is not.
        """
        return self.poll_seconds * 3

    def heartbeat(self, now: datetime | None = None) -> dict:
        """What this worker has been doing — read by the operator view."""
        now = now or datetime.now(timezone.utc)
        age = None if self.last_pass_at is None else (now - self.last_pass_at).total_seconds()
        # Before the first pass completes, measure staleness from startup —
        # otherwise a worker that died during its very first sweep looks fine
        # forever, which is the one case where nobody is watching yet.
        reference = self.last_pass_at or self.started_at
        stale = (
            reference is not None and (now - reference).total_seconds() > self.stale_after_seconds()
        )
        return {
            "running": self._task is not None and not self._task.done(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_pass_at": self.last_pass_at.isoformat() if self.last_pass_at else None,
            "seconds_since_last_pass": age,
            "passes": self.passes,
            "stale": stale,
            "interval_seconds": self.interval_seconds,
            "poll_seconds": self.poll_seconds,
            "last_pass": self.last_pass,
            "recent": list(self.recent),
        }

    def _retry_failed_sends(self, service) -> dict:
        from .repository import ProposedActionRepository
        from .sync_service import MAX_SEND_RETRIES

        retried = recovered = 0
        for org_id in ProposedActionRepository().orgs_with_failed_sends(MAX_SEND_RETRIES):
            try:
                result = service.retry_failed_sends(org_id=org_id, max_retries=MAX_SEND_RETRIES)
            except Exception:
                logger.exception("Retrying failed sends crashed for org %s", org_id)
                continue
            retried += result["attempted"]
            recovered += result["recovered"]
        if retried:
            logger.info("Retried %s failed send(s); %s recovered", retried, recovered)
        return {"retried": retried, "recovered": recovered}

    # -- lifecycle --------------------------------------------------------------
    async def run_forever(self) -> None:
        logger.info(
            "Background sync worker started (interval=%ss, jitter=%s, poll=%ss)",
            self.interval_seconds,
            self.jitter_fraction,
            self.poll_seconds,
        )
        while not self._stop.is_set():
            try:
                summary = await asyncio.to_thread(self.sync_due_connections)
                if summary["synced"] or summary["errors"]:
                    logger.info(
                        "Background sync pass: %s synced, %s messages, %s proposed, %s errors",
                        summary["synced"],
                        summary["messages"],
                        summary["proposed"],
                        summary["errors"],
                    )
            except Exception:
                logger.exception("Background sync pass crashed; retrying next poll")
                # Still a heartbeat: the loop *is* turning. Staleness means
                # "nothing is sweeping"; a pass that runs and throws is a
                # different fault, and conflating them would hide whichever
                # one happened second.
                self._record_pass(
                    {"crashed": True, "checked": 0, "synced": 0, "errors": 1},
                    datetime.now(timezone.utc),
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:  # noqa: UP041 - on 3.10 this is NOT builtins TimeoutError
                pass
        logger.info("Background sync worker stopped")

    def start(self) -> asyncio.Task:
        self._stop.clear()
        self.started_at = datetime.now(timezone.utc)
        self._task = asyncio.get_running_loop().create_task(
            self.run_forever(), name="background-inbox-sync"
        )
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
