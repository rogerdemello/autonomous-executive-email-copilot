"""Tenant-aware data access for the SaaS layer.

Every read/write is scoped by ``org_id`` unless it is an explicitly global
lookup (e.g. resolving a login email to a user before we know their org).
Routing all org data through these helpers is what makes cross-tenant leakage a
type error rather than a forgotten ``WHERE`` clause: product repositories added
later should subclass the same pattern.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

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


# Pagination bounds shared by list endpoints so a large tenant can't force an
# unbounded query. Callers clamp the requested page size into [1, MAX_PAGE_SIZE].
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def clamp_page(limit: int | None, offset: int | None) -> tuple[int, int]:
    """Clamp a requested ``(limit, offset)`` into safe bounds."""
    safe_limit = DEFAULT_PAGE_SIZE if not limit or limit < 1 else min(int(limit), MAX_PAGE_SIZE)
    safe_offset = 0 if not offset or offset < 0 else int(offset)
    return safe_limit, safe_offset


class OrganizationRepository:
    def create(self, name: str, slug: str) -> dict[str, Any]:
        with get_session() as session:
            org = Organization(name=name, slug=slug)
            session.add(org)
            session.flush()
            return org.to_dict()

    def get(self, org_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            org = session.get(Organization, org_id)
            return org.to_dict() if org else None

    def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        with get_session() as session:
            org = session.query(Organization).filter(Organization.slug == slug).first()
            return org.to_dict() if org else None

    def slug_exists(self, slug: str) -> bool:
        with get_session() as session:
            return (
                session.query(Organization.id).filter(Organization.slug == slug).first() is not None
            )


class UserRepository:
    def create(
        self,
        *,
        org_id: str,
        email: str,
        password_hash: str,
        full_name: str = "",
        role: str = "member",
        status: str = "active",
    ) -> dict[str, Any]:
        with get_session() as session:
            user = User(
                org_id=org_id,
                email=email.lower().strip(),
                password_hash=password_hash,
                full_name=full_name,
                role=role,
                status=status,
            )
            session.add(user)
            session.flush()
            return user.to_dict()

    def get_by_email_global(self, email: str) -> dict[str, Any] | None:
        """Global lookup by email (login path — org unknown until resolved).

        Returns the full row INCLUDING ``password_hash`` for credential checks;
        callers must not leak it. This is the one deliberately un-scoped read.
        """
        with get_session() as session:
            user = session.query(User).filter(User.email == email.lower().strip()).first()
            if not user:
                return None
            data = user.to_dict()
            data["password_hash"] = user.password_hash
            return data

    def email_exists(self, email: str) -> bool:
        with get_session() as session:
            return (
                session.query(User.id).filter(User.email == email.lower().strip()).first()
                is not None
            )

    def get(self, org_id: str, user_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id, User.org_id == org_id).first()
            return user.to_dict() if user else None

    def list_for_org(self, org_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            users = (
                session.query(User).filter(User.org_id == org_id).order_by(User.created_at).all()
            )
            return [u.to_dict() for u in users]

    def count_active_for_org(self, org_id: str) -> int:
        with get_session() as session:
            return (
                session.query(User).filter(User.org_id == org_id, User.status == "active").count()
            )

    def update_role(self, org_id: str, user_id: str, role: str) -> dict[str, Any] | None:
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id, User.org_id == org_id).first()
            if not user:
                return None
            user.role = role
            user.updated_at = _now_iso()
            session.flush()
            return user.to_dict()

    def set_password(self, org_id: str, user_id: str, password_hash: str) -> bool:
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id, User.org_id == org_id).first()
            if not user:
                return False
            user.password_hash = password_hash
            user.updated_at = _now_iso()
            return True

    def touch_login(self, user_id: str) -> None:
        with get_session() as session:
            user = session.get(User, user_id)
            if user:
                user.last_login_at = _now_iso()

    def delete(self, org_id: str, user_id: str) -> bool:
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id, User.org_id == org_id).first()
            if not user:
                return False
            session.delete(user)
            return True


class LicenseRepository:
    def upsert(
        self,
        *,
        org_id: str,
        key_id: str,
        plan: str,
        seats: int,
        features: list[str],
        expires_at_iso: str,
    ) -> dict[str, Any]:
        with get_session() as session:
            existing = session.query(License).filter(License.key_id == key_id).first()
            if existing:
                existing.plan = plan
                existing.seats = seats
                existing.features_json = json.dumps(features)
                existing.expires_at = expires_at_iso
                existing.status = "active"
                session.flush()
                return existing.to_dict()
            lic = License(
                org_id=org_id,
                key_id=key_id,
                plan=plan,
                seats=seats,
                features_json=json.dumps(features),
                expires_at=expires_at_iso,
            )
            session.add(lic)
            session.flush()
            return lic.to_dict()

    def get_active_for_org(self, org_id: str) -> dict[str, Any] | None:
        """The org's current active license (most recently issued wins)."""
        with get_session() as session:
            lic = (
                session.query(License)
                .filter(License.org_id == org_id, License.status == "active")
                .order_by(License.issued_at.desc())
                .first()
            )
            return lic.to_dict() if lic else None

    def get_by_key_id(self, key_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            lic = session.query(License).filter(License.key_id == key_id).first()
            return lic.to_dict() if lic else None

    def revoke(self, org_id: str, key_id: str) -> bool:
        with get_session() as session:
            lic = (
                session.query(License)
                .filter(License.key_id == key_id, License.org_id == org_id)
                .first()
            )
            if not lic:
                return False
            lic.status = "revoked"
            return True


class AuditRepository:
    def record(
        self,
        *,
        action: str,
        org_id: str | None = None,
        actor_user_id: str | None = None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None:
        with get_session() as session:
            session.add(
                AuditLogEntry(
                    org_id=org_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    target=target,
                    detail_json=json.dumps(detail) if detail else None,
                    ip=ip,
                )
            )

    def list_for_org(self, org_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(AuditLogEntry)
                .filter(AuditLogEntry.org_id == org_id)
                .order_by(AuditLogEntry.created_at.desc())
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]


class MailboxRepository:
    """Tenant-scoped access to connected mailboxes. Token fields are stored
    already-encrypted by the caller (never plaintext)."""

    def upsert_connection(
        self,
        *,
        org_id: str,
        provider: str,
        account_email: str,
        connected_by: str | None,
        access_token_enc: str | None,
        refresh_token_enc: str | None,
        token_expires_at: str | None,
        scopes: str | None,
    ) -> dict[str, Any]:
        with get_session() as session:
            existing = (
                session.query(MailboxConnection)
                .filter(
                    MailboxConnection.org_id == org_id,
                    MailboxConnection.provider == provider,
                    MailboxConnection.account_email == account_email,
                )
                .first()
            )
            if existing:
                existing.connected_by = connected_by
                existing.access_token_enc = access_token_enc
                if refresh_token_enc:
                    existing.refresh_token_enc = refresh_token_enc
                existing.token_expires_at = token_expires_at
                existing.scopes = scopes
                existing.status = "connected"
                existing.updated_at = _now_iso()
                session.flush()
                return existing.to_dict()
            conn = MailboxConnection(
                org_id=org_id,
                provider=provider,
                account_email=account_email,
                connected_by=connected_by,
                access_token_enc=access_token_enc,
                refresh_token_enc=refresh_token_enc,
                token_expires_at=token_expires_at,
                scopes=scopes,
            )
            session.add(conn)
            session.flush()
            return conn.to_dict()

    def list_for_org(self, org_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(MailboxConnection)
                .filter(MailboxConnection.org_id == org_id)
                .order_by(MailboxConnection.created_at.desc())
                .all()
            )
            return [r.to_dict() for r in rows]

    def get(self, org_id: str, connection_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            row = (
                session.query(MailboxConnection)
                .filter(
                    MailboxConnection.id == connection_id,
                    MailboxConnection.org_id == org_id,
                )
                .first()
            )
            return row.to_dict() if row else None

    def set_status(self, org_id: str, connection_id: str, status: str) -> bool:
        with get_session() as session:
            row = (
                session.query(MailboxConnection)
                .filter(
                    MailboxConnection.id == connection_id,
                    MailboxConnection.org_id == org_id,
                )
                .first()
            )
            if not row:
                return False
            row.status = status
            row.updated_at = _now_iso()
            return True

    def delete(self, org_id: str, connection_id: str) -> bool:
        with get_session() as session:
            row = (
                session.query(MailboxConnection)
                .filter(
                    MailboxConnection.id == connection_id,
                    MailboxConnection.org_id == org_id,
                )
                .first()
            )
            if not row:
                return False
            session.delete(row)
            return True

    def get_with_tokens(self, org_id: str, connection_id: str) -> dict[str, Any] | None:
        """Server-internal read INCLUDING the encrypted token fields, for building
        an authenticated provider. Never serialize this to an API response."""
        with get_session() as session:
            row = (
                session.query(MailboxConnection)
                .filter(
                    MailboxConnection.id == connection_id,
                    MailboxConnection.org_id == org_id,
                )
                .first()
            )
            if not row:
                return None
            data = row.to_dict()
            data["access_token_enc"] = row.access_token_enc
            data["refresh_token_enc"] = row.refresh_token_enc
            return data

    def set_synced_at(self, org_id: str, connection_id: str, iso: str) -> bool:
        with get_session() as session:
            row = (
                session.query(MailboxConnection)
                .filter(
                    MailboxConnection.id == connection_id,
                    MailboxConnection.org_id == org_id,
                )
                .first()
            )
            if not row:
                return False
            row.last_synced_at = iso
            row.updated_at = _now_iso()
            return True


class ProcessedMessageRepository:
    """Tenant-scoped access to fetched-and-enriched real messages."""

    def upsert(
        self,
        *,
        org_id: str,
        connection_id: str,
        provider_message_id: str,
        thread_id: str | None,
        sender: str | None,
        subject: str | None,
        body_preview: str | None,
        sender_role: str | None,
        priority_hint: str | None,
        risk_tag: str | None,
        deadline_minutes: int | None,
        business_value: float | None,
    ) -> dict[str, Any]:
        with get_session() as session:
            existing = (
                session.query(ProcessedMessage)
                .filter(
                    ProcessedMessage.org_id == org_id,
                    ProcessedMessage.connection_id == connection_id,
                    ProcessedMessage.provider_message_id == provider_message_id,
                )
                .first()
            )
            if existing:
                existing.thread_id = thread_id
                existing.sender = sender
                existing.subject = subject
                existing.body_preview = body_preview
                existing.sender_role = sender_role
                existing.priority_hint = priority_hint
                existing.risk_tag = risk_tag
                existing.deadline_minutes = deadline_minutes
                existing.business_value = business_value
                existing.synced_at = _now_iso()
                session.flush()
                return existing.to_dict()
            msg = ProcessedMessage(
                org_id=org_id,
                connection_id=connection_id,
                provider_message_id=provider_message_id,
                thread_id=thread_id,
                sender=sender,
                subject=subject,
                body_preview=body_preview,
                sender_role=sender_role,
                priority_hint=priority_hint,
                risk_tag=risk_tag,
                deadline_minutes=deadline_minutes,
                business_value=business_value,
            )
            session.add(msg)
            session.flush()
            return msg.to_dict()

    def list_for_org(
        self,
        org_id: str,
        connection_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        safe_limit, safe_offset = clamp_page(limit, offset)
        with get_session() as session:
            query = session.query(ProcessedMessage).filter(ProcessedMessage.org_id == org_id)
            if connection_id:
                query = query.filter(ProcessedMessage.connection_id == connection_id)
            total = query.count()
            rows = (
                query.order_by(ProcessedMessage.synced_at.desc())
                .offset(safe_offset)
                .limit(safe_limit)
                .all()
            )
            return {
                "messages": [r.to_dict() for r in rows],
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
            }

    def get(self, org_id: str, message_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            row = (
                session.query(ProcessedMessage)
                .filter(ProcessedMessage.id == message_id, ProcessedMessage.org_id == org_id)
                .first()
            )
            return row.to_dict() if row else None


class ProposedActionRepository:
    """Tenant-scoped access to proposed/decided/executed actions on real mail."""

    def create(
        self,
        *,
        org_id: str,
        message_id: str,
        action_type: str,
        content: str | None,
        escalate_to: str | None,
        label: str | None,
        status: str,
        requires_approval: bool,
        outcome: str | None = None,
        execution_ref: str | None = None,
        executed_at: str | None = None,
    ) -> dict[str, Any]:
        with get_session() as session:
            action = ProposedAction(
                org_id=org_id,
                message_id=message_id,
                action_type=action_type,
                content=content,
                escalate_to=escalate_to,
                label=label,
                status=status,
                requires_approval=requires_approval,
                outcome=outcome,
                execution_ref=execution_ref,
                executed_at=executed_at,
            )
            session.add(action)
            session.flush()
            return action.to_dict()

    def list_for_org(
        self,
        org_id: str,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        safe_limit, safe_offset = clamp_page(limit, offset)
        with get_session() as session:
            query = session.query(ProposedAction).filter(ProposedAction.org_id == org_id)
            if status:
                query = query.filter(ProposedAction.status == status)
            total = query.count()
            rows = (
                query.order_by(ProposedAction.created_at.desc())
                .offset(safe_offset)
                .limit(safe_limit)
                .all()
            )
            return {
                "actions": [r.to_dict() for r in rows],
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
            }

    def get(self, org_id: str, action_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            row = (
                session.query(ProposedAction)
                .filter(ProposedAction.id == action_id, ProposedAction.org_id == org_id)
                .first()
            )
            return row.to_dict() if row else None

    def active_types_for_message(self, org_id: str, message_id: str) -> set[str]:
        """The ``action_type``s on a message that already have a *live* action.

        "Live" = not ``rejected`` and not ``failed`` (those may be re-proposed on a
        later sync). Used to keep re-syncing an inbox idempotent: a proposal whose
        type is already live is skipped, so we never duplicate a pending action or
        re-fire a provider write for one already executed.
        """
        with get_session() as session:
            rows = (
                session.query(ProposedAction.action_type)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.message_id == message_id,
                    ~ProposedAction.status.in_(["rejected", "failed"]),
                )
                .all()
            )
            return {r[0] for r in rows}

    def set_status(
        self, org_id: str, action_id: str, status: str, **fields: Any
    ) -> dict[str, Any] | None:
        with get_session() as session:
            row = (
                session.query(ProposedAction)
                .filter(ProposedAction.id == action_id, ProposedAction.org_id == org_id)
                .first()
            )
            if not row:
                return None
            row.status = status
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            return row.to_dict()


class SalesLeadRepository:
    def create(
        self,
        *,
        email: str,
        kind: str = "contact_sales",
        name: str | None = None,
        company: str | None = None,
        seats: int | None = None,
        message: str | None = None,
        org_id: str | None = None,
    ) -> dict[str, Any]:
        with get_session() as session:
            lead = SalesLead(
                email=email.lower().strip(),
                kind=kind,
                name=name,
                company=company,
                seats=seats,
                message=message,
                org_id=org_id,
            )
            session.add(lead)
            session.flush()
            return lead.to_dict()

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = session.query(SalesLead).order_by(SalesLead.created_at.desc()).limit(limit).all()
            return [r.to_dict() for r in rows]
