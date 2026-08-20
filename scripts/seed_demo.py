#!/usr/bin/env python3
"""Seed a ready-to-present demo workspace.

Creates the Northwind Industries organization with an owner account, attaches
the demo mailbox, and runs the first sync — so a fresh clone goes from `git
clone` to a populated inbox in one command.

Idempotent: run it as often as you like. If the workspace already exists it is
reset to a clean state (processed messages and pending actions cleared, then
re-synced) rather than duplicated, which is what you want between rehearsals.

    python scripts/seed_demo.py            # seed or reset
    python scripts/seed_demo.py --fresh    # also delete the org first

Nothing here needs network access, an API key, or OAuth credentials.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.copilot.providers.demo import DEMO_PROVIDER_KEY, demo_message_count  # noqa: E402
from app.core.db import migrate_db  # noqa: E402
from app.saas.auth import AuthError, AuthService  # noqa: E402
from app.saas.data_lifecycle import DataLifecycleService  # noqa: E402
from app.saas.provider_factory import build_provider  # noqa: E402
from app.saas.repository import (  # noqa: E402
    MailboxRepository,
    OrganizationRepository,
    ProposedActionRepository,
    UserRepository,
)
from app.saas.sync_service import InboxSyncService  # noqa: E402
from app.web.routes import (  # noqa: E402
    DEMO_ORG_NAME,
    DEMO_OWNER_EMAIL,
    DEMO_OWNER_NAME,
    DEMO_OWNER_PASSWORD,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="delete the demo organization first, then recreate it from scratch",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help=(
            "call the configured LLM to write the reply and escalation prose, and "
            "commit it to data/demo/drafts.json. Run this once, with a key and a "
            "network; every later run replays those drafts from disk."
        ),
    )
    args = parser.parse_args()

    if args.with_llm:
        from app.core.config import get_settings

        if not get_settings().provider_available:
            print(
                "--with-llm needs a provider credential (OPENAI_API_KEY, "
                "ANTHROPIC_API_KEY, GOOGLE_API_KEY or OLLAMA_BASE_URL).",
                file=sys.stderr,
            )
            return 1

    migrate_db()

    users = UserRepository()
    orgs = OrganizationRepository()
    mailboxes = MailboxRepository()
    actions = ProposedActionRepository()
    auth = AuthService()

    existing = users.get_by_email_global(DEMO_OWNER_EMAIL)

    if existing and args.fresh:
        print(f"Removing the existing {DEMO_ORG_NAME} workspace…")
        DataLifecycleService().delete_org(existing["org_id"])
        existing = None

    if existing:
        owner = existing
        org = orgs.get(owner["org_id"])
        print(f"Reusing the existing workspace: {org['name']}")
        # Clear prior triage so a rehearsal starts exactly where a first run
        # does — otherwise last session's approvals are still decided.
        _purge_messages(owner["org_id"])
        # Re-mint the trial: an expired plan blocks sync and approvals (by
        # design), and a demo workspace older than the trial window would
        # otherwise 402 on stage. Most recently issued license wins.
        from app.core.config import get_settings
        from app.saas import licensing
        from app.saas.billing import BillingService

        key, _terms = licensing.mint_license(
            org["id"], "trial", get_settings().resolved_auth_secret
        )
        BillingService().activate_license(
            org_id=org["id"], license_key=key, actor_user_id=owner["id"]
        )
    else:
        try:
            owner, org, _terms = auth.signup(
                email=DEMO_OWNER_EMAIL,
                password=DEMO_OWNER_PASSWORD,
                full_name=DEMO_OWNER_NAME,
                org_name=DEMO_ORG_NAME,
            )
        except AuthError as exc:
            print(f"Could not create the demo workspace: {exc.message}", file=sys.stderr)
            if "disabled" in exc.message.lower():
                print("Set SIGNUP_ENABLED=true and try again.", file=sys.stderr)
            return 1
        print(f"Created {org['name']} with owner {owner['email']}")

    connection = mailboxes.upsert_connection(
        org_id=owner["org_id"],
        provider=DEMO_PROVIDER_KEY,
        account_email=DEMO_OWNER_EMAIL,
        connected_by=owner["id"],
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    print(f"Connected the demo mailbox ({demo_message_count()} messages)")

    if args.with_llm:
        print("Drafting with the model — this makes network calls and costs money…")

    result = InboxSyncService().sync(
        org_id=owner["org_id"],
        user_id=owner["id"],
        connection_id=connection["id"],
        provider=build_provider(connection),
        live_llm=args.with_llm,
    )
    pending = actions.list_for_org(owner["org_id"], status="proposed", limit=200)

    print(
        f"Triaged {result.get('messages', 0)} messages: "
        f"{result.get('auto_executed', 0)} applied automatically, "
        f"{pending.get('total', 0)} held for approval"
    )

    _report_draft_sources(pending.get("actions") or [])
    print()
    print("  Demo workspace ready.")
    print("    uvicorn app.main:app --reload --port 8000")
    print("    http://localhost:8000/login")
    print(f"    {DEMO_OWNER_EMAIL} / {DEMO_OWNER_PASSWORD}")
    return 0


def _report_draft_sources(actions: list) -> None:
    """Say where the held prose came from — the thing worth knowing before a demo."""
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.get("draft_source") or "generic"] = (
            counts.get(action.get("draft_source") or "generic", 0) + 1
        )
    if not counts:
        return
    labels = {
        "llm": "model-written",
        "authored": "authored fixture prose",
        "generic": "the policy's generic sentence",
    }
    summary = ", ".join(
        f"{count} {labels.get(source, source)}" for source, count in sorted(counts.items())
    )
    print(f"  Drafts: {summary}")
    if not counts.get("llm"):
        print("  (no cached model drafts — run once with --with-llm to generate them)")


def _purge_messages(org_id: str) -> None:
    """Drop processed messages so the next sync re-creates them cleanly."""
    from app.core.db import get_session
    from app.saas.models_db import ProcessedMessage, ProposedAction

    with get_session() as session:
        session.query(ProposedAction).filter(ProposedAction.org_id == org_id).delete()
        session.query(ProcessedMessage).filter(ProcessedMessage.org_id == org_id).delete()


if __name__ == "__main__":
    raise SystemExit(main())
