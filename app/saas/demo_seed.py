"""Seed (or reset) the ready-to-present demo workspace, importable in-process.

Lives in the app package rather than only in ``scripts/`` because the deployed
instance has no shell (Render free tier): ``app.main``'s lifespan calls
:func:`seed_demo` at startup when ``DEMO_SEED_ON_STARTUP`` is set, and the
operator API exposes a reseed endpoint for resetting between sales calls.

Provisions through :func:`app.saas.provisioning.provision_org`, NOT
``AuthService.signup`` — production runs with ``SIGNUP_ENABLED=false`` and the
demo must still exist there.
"""

from __future__ import annotations

import logging

from app.copilot.providers.demo import DEMO_PROVIDER_KEY, demo_account_email

logger = logging.getLogger(__name__)

# How the demo workspace is seeded, and what the login page offers a visitor.
DEMO_ORG_NAME = "Northwind Industries"
DEMO_OWNER_EMAIL = demo_account_email()
DEMO_OWNER_PASSWORD = "demo1234"  # nosec B105 - deliberately public demo credential
DEMO_OWNER_NAME = "Alex Chen"

# Who Northwind hands an escalation to. Seeded because a workspace without them
# is a half-configured one: every escalation in the queue would carry "no
# mailbox set", which is a true warning about an unfinished setup and a false
# impression of the product. Same domain as the demo mailbox, so the addresses
# read as colleagues rather than as anything a visitor might write to.
DEMO_ESCALATION_CONTACTS = {
    "legal_team": "legal@northwind.example",
    "chief_of_staff": "chief.of.staff@northwind.example",
}


def populate_demo_workspace(owner: dict, *, live_llm: bool = False) -> dict:
    """Give a workspace the demo mailbox, its escalation contacts, and triage it.

    The one place the demo mailbox is attached and read. The seeded demo account
    and every per-visitor sandbox (:mod:`app.saas.sandbox`) go through here, so
    "what the demo shows" cannot drift between them. It is the same path a real
    mailbox takes — a connection row, a provider from the factory, then
    :meth:`InboxSyncService.sync` — and the mailbox's own identity stays
    ``DEMO_OWNER_EMAIL`` whoever owns the workspace, because the routing
    decides internal-versus-external senders from that domain.

    ``live_llm`` is explicit and defaults off: the demo must show cached model
    prose with no key and no network, and a sandbox must never be able to spend
    the deployment's money. Returns the sync result.
    """
    from app.saas.provider_factory import build_provider
    from app.saas.repository import EscalationContactRepository, MailboxRepository
    from app.saas.sync_service import InboxSyncService

    contacts = EscalationContactRepository()
    for role, address in DEMO_ESCALATION_CONTACTS.items():
        contacts.set_email(owner["org_id"], role, address)

    connection = MailboxRepository().upsert_connection(
        org_id=owner["org_id"],
        provider=DEMO_PROVIDER_KEY,
        account_email=DEMO_OWNER_EMAIL,
        connected_by=owner["id"],
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    return InboxSyncService().sync(
        org_id=owner["org_id"],
        user_id=owner["id"],
        connection_id=connection["id"],
        provider=build_provider(connection),
        live_llm=live_llm,
    )


def seed_demo(*, fresh: bool = False, live_llm: bool = False) -> dict:
    """Create or reset the demo workspace; idempotent and cheap when current.

    Returns a small summary dict (created, messages, auto_executed, pending).
    ``fresh`` deletes the org first; otherwise an existing workspace is reset
    to a clean pre-demo state (messages/actions cleared, trial re-minted,
    mailbox re-synced).
    """
    from app.core.config import get_settings
    from app.core.db import get_session, migrate_db
    from app.saas import licensing
    from app.saas.billing import BillingService
    from app.saas.data_lifecycle import DataLifecycleService
    from app.saas.models_db import ProcessedMessage, ProposedAction
    from app.saas.provisioning import provision_org
    from app.saas.repository import (
        OrganizationRepository,
        ProposedActionRepository,
        UserRepository,
    )

    migrate_db()

    users = UserRepository()
    orgs = OrganizationRepository()
    actions = ProposedActionRepository()

    existing = users.get_by_email_global(DEMO_OWNER_EMAIL)
    if existing and fresh:
        logger.info("Removing the existing %s workspace", DEMO_ORG_NAME)
        DataLifecycleService().delete_org(existing["org_id"])
        existing = None

    created = existing is None
    if existing:
        owner = existing
        org = orgs.get(owner["org_id"])
        # Clear prior triage so the next walkthrough starts where a first run
        # does, and re-mint the trial: an expired plan blocks sync/approvals.
        with get_session() as session:
            session.query(ProposedAction).filter(ProposedAction.org_id == owner["org_id"]).delete()
            session.query(ProcessedMessage).filter(
                ProcessedMessage.org_id == owner["org_id"]
            ).delete()
        key, _terms = licensing.mint_license(
            org["id"], "trial", get_settings().resolved_auth_secret
        )
        BillingService().activate_license(
            org_id=org["id"], license_key=key, actor_user_id=owner["id"]
        )
    else:
        result = provision_org(
            org_name=DEMO_ORG_NAME,
            owner_email=DEMO_OWNER_EMAIL,
            owner_name=DEMO_OWNER_NAME,
            password=DEMO_OWNER_PASSWORD,
        )
        owner, org = result["owner"], result["organization"]
        logger.info("Created %s with owner %s", org["name"], owner["email"])

    sync_result = populate_demo_workspace(owner, live_llm=live_llm)
    pending = actions.list_for_org(owner["org_id"], status="proposed", limit=200)
    summary = {
        "created": created,
        "messages": sync_result.get("messages", 0),
        "auto_executed": sync_result.get("auto_executed", 0),
        "pending": pending.get("total", 0),
        "pending_actions": pending.get("actions") or [],
    }
    logger.info(
        "Demo workspace ready: %s messages triaged, %s held for approval",
        summary["messages"],
        summary["pending"],
    )
    return summary
