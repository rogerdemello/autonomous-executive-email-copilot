"""Org data lifecycle: export and hard-delete all of a tenant's data (GDPR).

Enterprise buyers require a way to get their data out and to have it erased.
Both operations enumerate every tenant-scoped table in one place so a new
product table can't be silently missed — add it here when you add it.

Export never includes secrets (password hashes, OAuth tokens are excluded by the
models' ``to_dict``). Delete is a hard purge in FK-safe order, wrapped in a single
transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db import get_session
from .models_db import (
    AuditLogEntry,
    License,
    MailboxConnection,
    Organization,
    ProcessedMessage,
    ProposedAction,
    SalesLead,
    User,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataLifecycleService:
    def export_org(self, org_id: str) -> dict | None:
        """Return a full, secret-free JSON bundle of an org's data, or None."""
        with get_session() as session:
            org = session.get(Organization, org_id)
            if not org:
                return None

            def rows(model, column):
                return [r.to_dict() for r in session.query(model).filter(column == org_id).all()]

            return {
                "exported_at": _now_iso(),
                "organization": org.to_dict(),
                "users": rows(User, User.org_id),
                "licenses": rows(License, License.org_id),
                "mailbox_connections": rows(MailboxConnection, MailboxConnection.org_id),
                "processed_messages": rows(ProcessedMessage, ProcessedMessage.org_id),
                "proposed_actions": rows(ProposedAction, ProposedAction.org_id),
                "audit_log": rows(AuditLogEntry, AuditLogEntry.org_id),
                "sales_leads": rows(SalesLead, SalesLead.org_id),
            }

    def delete_org(self, org_id: str) -> dict | None:
        """Hard-delete an org and every row scoped to it. Returns per-table counts."""
        with get_session() as session:
            org = session.get(Organization, org_id)
            if not org:
                return None

            counts: dict[str, int] = {}
            # Children first (FK-safe): actions -> messages -> licenses/mailboxes ->
            # audit/leads -> users -> the org itself.
            for label, model, column in (
                ("proposed_actions", ProposedAction, ProposedAction.org_id),
                ("processed_messages", ProcessedMessage, ProcessedMessage.org_id),
                ("licenses", License, License.org_id),
                ("mailbox_connections", MailboxConnection, MailboxConnection.org_id),
                ("audit_log", AuditLogEntry, AuditLogEntry.org_id),
                ("sales_leads", SalesLead, SalesLead.org_id),
                ("users", User, User.org_id),
            ):
                counts[label] = (
                    session.query(model).filter(column == org_id).delete(synchronize_session=False)
                )
            session.delete(org)
            counts["organization"] = 1
            return counts
