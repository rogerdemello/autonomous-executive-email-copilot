"""Per-visitor demo workspaces: "Try the live demo", with nothing to type.

The first public demo was a seeded account with a published password, and a login
page that pre-filled it. That fails in three ways, and a reviewer hit the first:

1. **It depends on the seed having run.** The account exists only if
   ``DEMO_SEED_ON_STARTUP`` was set and the database survived since. On a host
   with an ephemeral database every cold start wiped it, so the login page
   offered nothing — a blank form on the one page a first-time visitor is sent to.
2. **State is shared.** Whoever approved the queue first left the next visitor
   an empty one — the demo's whole point, the held drafts, was consumed.
3. **A published credential is a credential.** It had to be fenced off from every
   destructive action, and it still lets any visitor see what any other did.

A sandbox is a *real* workspace — organization, owner, trial license, the demo
mailbox attached and triaged by the same code a real mailbox goes through — built
for one visitor when they click the button, owned by an account whose password
nobody ever learns (the visitor is handed a session, not a credential), and
deleted when that session would have expired anyway. Because it is built on
demand it works on an empty database, and because it is private there is nothing
to reset.

Everything that makes this safe to expose to an unauthenticated request lives
here or is enforced by something named here:

- it is **bounded**: a per-IP rate limit (``app.core.security``) and
  :data:`MAX_LIVE_SANDBOXES`, which *refuses* when full rather than evicting a
  visitor who is mid-demo;
- it **cleans up**: :func:`purge_expired` runs on every creation and every worker
  pass, and a failed build deletes its own half-made organization;
- it **cannot spend**: a sandbox never drafts live (``InboxSyncService`` checks
  the same marker), so a stranger cannot run up the deployment's model bill;
- it **cannot reach out**: no real mailbox connects, no member is invited, no
  email is sent (``.invalid`` is undeliverable by RFC 6761), and every
  administrative action is refused (``deps.reject_shared_demo_account``);
- it is **never swept by the background worker**, which would otherwise re-triage
  every visitor's fixture mailbox every few minutes to learn nothing.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# RFC 6761 guarantees ``.invalid`` never resolves, so nothing addressed to a
# sandbox owner can be delivered, whatever else goes wrong.
SANDBOX_EMAIL_DOMAIN = "sandbox.invalid"

# Live sandboxes at one time. A visitor who finds the demo "full" is a bad day;
# a visitor evicted mid-click by someone else's burst is a worse one — so at the
# cap new requests are refused (:class:`SandboxUnavailable`) and nobody's
# workspace is ever deleted to make room. At one a day this is unreachable;
# it exists for the day it is not.
MAX_LIVE_SANDBOXES = 250


class SandboxUnavailable(Exception):
    """The demo is at capacity. Retry shortly — a slot frees as sessions expire."""


def sandbox_lifetime() -> timedelta:
    """How long a sandbox lives: exactly as long as the session that opened it.

    Past that moment nobody can sign in to it — the password is unknown — so
    keeping it longer would be storing a workspace no one can reach.
    """
    return timedelta(minutes=get_settings().access_token_ttl_minutes)


def _iso(moment: datetime) -> str:
    # Whole seconds, so stored deadlines compare lexically the way they sort.
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def is_sandbox_org(org: dict | None) -> bool:
    return bool(org and org.get("sandbox_expires_at"))


def is_sandbox_user(user: dict) -> bool:
    """Is this user the owner of a demo sandbox (as opposed to a real workspace)?"""
    from .repository import OrganizationRepository

    return is_sandbox_org(OrganizationRepository().get(user["org_id"]))


def hours_left(org: dict, now: datetime | None = None) -> int | None:
    """Whole hours until a sandbox is deleted, at least 1; ``None`` if it is not one."""
    expires = org.get("sandbox_expires_at")
    if not expires:
        return None
    try:
        deadline = datetime.fromisoformat(str(expires))
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    remaining = (deadline - (now or datetime.now(timezone.utc))).total_seconds()
    return max(1, int(-(-remaining // 3600)))


def purge_expired(now: datetime | None = None) -> int:
    """Delete every sandbox whose deadline has passed. Returns how many.

    Goes through :class:`DataLifecycleService` — the same hard delete a customer
    gets — so a sandbox leaves nothing behind in any tenant table. Real
    workspaces have no deadline and are never selected.
    """
    from .data_lifecycle import DataLifecycleService
    from .repository import OrganizationRepository

    moment = now or datetime.now(timezone.utc)
    lifecycle = DataLifecycleService()
    purged = 0
    for org_id in OrganizationRepository().expired_sandbox_ids(_iso(moment)):
        try:
            if lifecycle.delete_org(org_id):
                purged += 1
        except Exception:  # noqa: BLE001 - one stuck row must not block the rest
            logger.exception("Could not purge expired sandbox %s", org_id)
    if purged:
        logger.info("Purged %s expired demo sandbox(es)", purged)
    return purged


def create_sandbox(*, ip: str | None = None, now: datetime | None = None) -> dict:
    """Build a private, fully triaged demo workspace; return its owner and org.

    Raises :class:`SandboxUnavailable` at capacity. Any other failure deletes
    whatever was half-built before it propagates, so a visitor who hits an error
    leaves nothing behind for the purge to find later.
    """
    from .data_lifecycle import DataLifecycleService
    from .demo_seed import (
        DEMO_ORG_NAME,
        DEMO_OWNER_NAME,
        populate_demo_workspace,
    )
    from .provisioning import provision_org
    from .repository import AuditRepository, OrganizationRepository

    moment = now or datetime.now(timezone.utc)
    purge_expired(moment)
    if OrganizationRepository().count_sandboxes() >= MAX_LIVE_SANDBOXES:
        raise SandboxUnavailable("The demo is busy right now. Try again in a few minutes.")

    handle = secrets.token_hex(6)
    expires_at = _iso(moment + sandbox_lifetime())
    result = provision_org(
        org_name=DEMO_ORG_NAME,
        # The owner's address is never shown as a credential and cannot receive
        # mail. The password is generated and discarded: there is no way in but
        # the session this function's caller mints.
        owner_email=f"demo-{handle}@{SANDBOX_EMAIL_DOMAIN}",
        owner_name=DEMO_OWNER_NAME,
        slug=f"sandbox-{handle}",
        sandbox_expires_at=expires_at,
    )
    owner, org = result["owner"], result["organization"]

    try:
        # live_llm=False, explicitly: a stranger's click must not be able to
        # spend the deployment's model budget. Cached drafts replay regardless.
        populate_demo_workspace(owner, live_llm=False)
        AuditRepository().record(
            action="demo.sandbox_created",
            org_id=org["id"],
            actor_user_id=owner["id"],
            detail={"expires_at": expires_at},
            ip=ip,
        )
    except Exception:
        logger.exception("Building a demo sandbox failed; removing what was made")
        try:
            DataLifecycleService().delete_org(org["id"])
        except Exception:  # noqa: BLE001 - the purge will retry it at its deadline
            logger.exception("Could not remove the half-built sandbox %s", org["id"])
        raise

    return {"user": owner, "organization": org, "expires_at": expires_at}
