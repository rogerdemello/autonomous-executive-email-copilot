"""Org data lifecycle: export and hard-delete all of a tenant's data (GDPR).

Enterprise buyers require a way to get their data out and to have it erased.
Both operations walk the same list, :data:`TENANT_TABLES`, so a table cannot be
exported but forgotten on delete or the other way round. A new product table
that carries an ``org_id`` **must** be added there — and
:func:`uncovered_tenant_tables` exists so a test can fail the build when it is
not. That rule used to live in this docstring alone; two tables
(``saas_llm_usage`` and ``saas_escalation_contacts``) were then added without
it, and "erase my workspace" quietly left behind the model-spend ledger and the
email addresses of the customer's colleagues. On Postgres the second one was
worse: it has a foreign key to the organization, so the delete failed outright.

Export never includes secrets (password hashes, OAuth tokens are excluded by the
models' ``to_dict``). Delete is a hard purge in FK-safe order, wrapped in a single
transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.db import Base, get_session

from .models_db import (
    AuditLogEntry,
    Commitment,
    EscalationContact,
    License,
    LlmUsage,
    MailboxConnection,
    Organization,
    ProcessedMessage,
    ProposedAction,
    SalesLead,
    User,
)

# (key in the export bundle / delete report, model). Order is the FK-safe
# delete order — children first: actions -> messages -> everything that points
# at the organization -> users — and the organization row itself goes last.
TENANT_TABLES = (
    ("commitments", Commitment),
    ("proposed_actions", ProposedAction),
    ("processed_messages", ProcessedMessage),
    ("escalation_contacts", EscalationContact),
    ("llm_usage", LlmUsage),
    ("licenses", License),
    ("mailbox_connections", MailboxConnection),
    ("audit_log", AuditLogEntry),
    ("sales_leads", SalesLead),
    ("users", User),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uncovered_tenant_tables() -> list[str]:
    """Tables that hold an ``org_id`` but are in neither export nor delete.

    Read from the live SQLAlchemy metadata rather than from a second hand-kept
    list, so adding a model is enough to make it appear here. An empty result is
    the invariant; a test asserts it.
    """
    # Make sure every product model has registered on the shared metadata.
    from app.core.db import _register_saas_models

    _register_saas_models()
    covered = {model.__tablename__ for _key, model in TENANT_TABLES}
    covered.add(Organization.__tablename__)
    return sorted(
        name
        for name, table in Base.metadata.tables.items()
        if "org_id" in table.columns and name not in covered
    )


class DataLifecycleService:
    def remaining_rows(self, org_id: str) -> dict[str, int]:
        """Rows still held for ``org_id``, per table; empty means fully erased.

        Counts every table that carries an ``org_id`` plus the organization row
        itself, read from the live metadata rather than from
        :data:`TENANT_TABLES` — so it answers "is anything left?" independently
        of the list that decides what gets deleted, which is the one that can be
        wrong. Used to verify an erasure, and by the tests that pin it.
        """
        from sqlalchemy import func, select

        from app.core.db import _register_saas_models

        _register_saas_models()
        left: dict[str, int] = {}
        with get_session() as session:
            for name, table in Base.metadata.tables.items():
                if "org_id" in table.columns:
                    column = table.c.org_id
                elif name == Organization.__tablename__:
                    column = table.c.id
                else:
                    continue
                count = session.execute(
                    select(func.count()).select_from(table).where(column == org_id)
                ).scalar()
                if count:
                    left[name] = int(count)
        return left

    def export_org(self, org_id: str) -> dict | None:
        """Return a full, secret-free JSON bundle of an org's data, or None."""
        with get_session() as session:
            org = session.get(Organization, org_id)
            if not org:
                return None

            bundle: dict = {
                "exported_at": _now_iso(),
                "organization": org.to_dict(),
            }
            for key, model in TENANT_TABLES:
                bundle[key] = [
                    row.to_dict()
                    for row in session.query(model).filter(model.org_id == org_id).all()
                ]
            return bundle

    def delete_org(self, org_id: str) -> dict | None:
        """Hard-delete an org and every row scoped to it. Returns per-table counts."""
        with get_session() as session:
            org = session.get(Organization, org_id)
            if not org:
                return None

            counts: dict[str, int] = {}
            for key, model in TENANT_TABLES:
                counts[key] = (
                    session.query(model)
                    .filter(model.org_id == org_id)
                    .delete(synchronize_session=False)
                )
            session.delete(org)
            counts["organization"] = 1
            return counts
