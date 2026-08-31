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

from sqlalchemy import func

from app.core.db import get_session

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

# A stored message body is capped rather than unbounded: a mailbox will sooner
# or later contain one message with a megabyte of quoted history, and there is
# no reading experience that needs more than this.
BODY_MAX_CHARS = 100_000


def clamp_page(limit: int | None, offset: int | None) -> tuple[int, int]:
    """Clamp a requested ``(limit, offset)`` into safe bounds."""
    safe_limit = DEFAULT_PAGE_SIZE if not limit or limit < 1 else min(int(limit), MAX_PAGE_SIZE)
    safe_offset = 0 if not offset or offset < 0 else int(offset)
    return safe_limit, safe_offset


def _clamp_body(body: str | None) -> str | None:
    if body is None:
        return None
    if len(body) <= BODY_MAX_CHARS:
        return body
    return body[:BODY_MAX_CHARS] + "\n\n[… truncated]"


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a search for "50%" is not a match-everything.

    Paired with ``escape="\\"`` at the call site.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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

    def list_all(self, limit: int = 200) -> list[dict[str, Any]]:
        """Every organization, newest first. Operator surface only."""
        with get_session() as session:
            rows = (
                session.query(Organization)
                .order_by(Organization.created_at.desc())
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]


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

    def revoke_all_for_org(self, org_id: str) -> int:
        """Revoke every active license for the org — the full cut-off.

        Revoking a single key merely falls back to the next most recent active
        license (e.g. the original trial), which is a downgrade, not a stop.
        """
        with get_session() as session:
            rows = (
                session.query(License)
                .filter(License.org_id == org_id, License.status == "active")
                .all()
            )
            for lic in rows:
                lic.status = "revoked"
            return len(rows)

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

    def page_for_org(
        self,
        org_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        action: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """A filtered, counted page of the audit log.

        The Activity page rendered one unfiltered table of the most recent 100
        entries with no way to page back, which makes the log unusable as
        evidence the moment a workspace has been running for a week — and
        "answer procurement's questions" is the whole reason it exists.
        """
        safe_limit, safe_offset = clamp_page(limit, offset)
        with get_session() as session:
            query = session.query(AuditLogEntry).filter(AuditLogEntry.org_id == org_id)
            if action:
                query = query.filter(AuditLogEntry.action == action)
            if actor_user_id:
                query = query.filter(AuditLogEntry.actor_user_id == actor_user_id)
            total = query.count()
            rows = (
                query.order_by(AuditLogEntry.created_at.desc(), AuditLogEntry.id.desc())
                .offset(safe_offset)
                .limit(safe_limit)
                .all()
            )
            return {
                "entries": [r.to_dict() for r in rows],
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
            }

    def actions_for_org(self, org_id: str) -> list[str]:
        """The distinct action names present, for the filter dropdown."""
        with get_session() as session:
            rows = (
                session.query(AuditLogEntry.action)
                .filter(AuditLogEntry.org_id == org_id)
                .distinct()
                .order_by(AuditLogEntry.action.asc())
                .all()
            )
            return [str(r[0]) for r in rows]


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

    def list_all_connected(self) -> list[dict[str, Any]]:
        """Every connected mailbox across all orgs — the background worker's
        work list. Deliberately cross-tenant (the worker is a system actor);
        broken connections are excluded because they need a human to
        reconnect, not retries."""
        with get_session() as session:
            rows = (
                session.query(MailboxConnection)
                .filter(MailboxConnection.status == "connected")
                .order_by(MailboxConnection.created_at.asc())
                .all()
            )
            return [r.to_dict() for r in rows]

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

    def delete(self, org_id: str, connection_id: str) -> dict[str, int] | None:
        """Delete the connection AND everything derived from it, in one
        transaction. Returns the removed row counts, or None if not found.

        The UI promises "processed messages and pending actions for this
        mailbox are removed", and the dedup key includes ``connection_id`` —
        leaving the rows behind both breaks that promise and duplicates the
        whole inbox on reconnect (a fresh connection id means every message
        looks new). Actions go first: they reference messages by FK.
        """
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
            message_ids = (
                session.query(ProcessedMessage.id)
                .filter(
                    ProcessedMessage.org_id == org_id,
                    ProcessedMessage.connection_id == connection_id,
                )
                .subquery()
            )
            actions_removed = (
                session.query(ProposedAction)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.message_id.in_(message_ids.select()),
                )
                .delete(synchronize_session=False)
            )
            messages_removed = (
                session.query(ProcessedMessage)
                .filter(
                    ProcessedMessage.org_id == org_id,
                    ProcessedMessage.connection_id == connection_id,
                )
                .delete(synchronize_session=False)
            )
            session.delete(row)
            return {"messages": messages_removed, "actions": actions_removed}

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
        sender_name: str | None = None,
        received_at: str | None = None,
        body: str | None = None,
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
                existing.sender_name = sender_name
                existing.subject = subject
                existing.body_preview = body_preview
                existing.body = _clamp_body(body)
                existing.sender_role = sender_role
                existing.priority_hint = priority_hint
                existing.risk_tag = risk_tag
                existing.deadline_minutes = deadline_minutes
                existing.business_value = business_value
                existing.received_at = received_at
                existing.synced_at = _now_iso()
                session.flush()
                return existing.to_dict()
            msg = ProcessedMessage(
                org_id=org_id,
                connection_id=connection_id,
                provider_message_id=provider_message_id,
                thread_id=thread_id,
                sender=sender,
                sender_name=sender_name,
                subject=subject,
                body_preview=body_preview,
                body=_clamp_body(body),
                sender_role=sender_role,
                priority_hint=priority_hint,
                risk_tag=risk_tag,
                deadline_minutes=deadline_minutes,
                business_value=business_value,
                received_at=received_at,
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
        q: str | None = None,
        label: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        """A page of messages, newest first, with optional search and filters.

        ``q`` matches sender, sender name, and subject. ``label`` is the
        classifier's verdict (spam / normal / urgent), which lives on the
        message's ``classify`` action rather than on the message, so it is a
        subquery rather than a column test. ``priority`` is the inferred
        priority hint and *is* a column.
        """
        safe_limit, safe_offset = clamp_page(limit, offset)
        with get_session() as session:
            query = session.query(ProcessedMessage).filter(ProcessedMessage.org_id == org_id)
            if connection_id:
                query = query.filter(ProcessedMessage.connection_id == connection_id)
            if q and q.strip():
                needle = f"%{_escape_like(q.strip())}%"
                query = query.filter(
                    ProcessedMessage.subject.ilike(needle, escape="\\")
                    | ProcessedMessage.sender.ilike(needle, escape="\\")
                    | ProcessedMessage.sender_name.ilike(needle, escape="\\")
                )
            if label:
                labelled = (
                    session.query(ProposedAction.message_id)
                    .filter(
                        ProposedAction.org_id == org_id,
                        ProposedAction.action_type == "classify",
                        ProposedAction.label == label,
                    )
                    .subquery()
                )
                query = query.filter(ProcessedMessage.id.in_(labelled.select()))
            if priority:
                query = query.filter(ProcessedMessage.priority_hint == priority)
            total = query.count()
            rows = (
                query.order_by(
                    ProcessedMessage.received_at.desc().nullslast(),
                    ProcessedMessage.synced_at.desc(),
                    ProcessedMessage.id.asc(),
                )
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

    def list_thread(self, org_id: str, thread_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Every message in one thread, oldest first.

        ``thread_id`` has been stored since the table existed and was never
        read, while the landing page sold "summarizes long threads".
        """
        if not thread_id:
            return []
        with get_session() as session:
            rows = (
                session.query(ProcessedMessage)
                .filter(
                    ProcessedMessage.org_id == org_id,
                    ProcessedMessage.thread_id == thread_id,
                )
                .order_by(
                    ProcessedMessage.received_at.asc().nullsfirst(),
                    ProcessedMessage.synced_at.asc(),
                )
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]

    def thread_sizes(self, org_id: str, thread_ids: list[str]) -> dict[str, int]:
        """``{thread_id: message count}`` for the threads on the current page.

        One grouped query for the whole page rather than one per row.
        """
        wanted = [t for t in dict.fromkeys(thread_ids) if t]
        if not wanted:
            return {}
        with get_session() as session:
            rows = (
                session.query(ProcessedMessage.thread_id, func.count(ProcessedMessage.id))
                .filter(
                    ProcessedMessage.org_id == org_id,
                    ProcessedMessage.thread_id.in_(wanted),
                )
                .group_by(ProcessedMessage.thread_id)
                .all()
            )
            return {str(thread_id): int(count) for thread_id, count in rows}

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
        draft_source: str | None = None,
        draft_confidence: float | None = None,
        rationale: list[str] | None = None,
        verification_status: str | None = None,
        verification_notes: list[str] | None = None,
        verification_claims: list[dict] | None = None,
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
                draft_source=draft_source,
                draft_confidence=draft_confidence,
                rationale="\n".join(rationale) if rationale else None,
                verification_status=verification_status,
                verification_notes="\n".join(verification_notes) if verification_notes else None,
                verification_claims=json.dumps(verification_claims)
                if verification_claims
                else None,
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

    def labels_for_messages(self, org_id: str, message_ids: list[str]) -> dict[str, str]:
        """``{message_id: classifier label}`` for exactly the messages asked for.

        The inbox used to build this by listing the org's first 500 actions and
        filtering in Python, so on any tenant with more than 500 actions the
        spam chips silently vanished from the older half of the page — the
        product's most visible claim, disappearing with scale and with nothing
        reporting it.
        """
        wanted = [m for m in dict.fromkeys(message_ids) if m]
        if not wanted:
            return {}
        with get_session() as session:
            rows = (
                session.query(ProposedAction.message_id, ProposedAction.label)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.action_type == "classify",
                    ProposedAction.message_id.in_(wanted),
                    ProposedAction.label.isnot(None),
                )
                .all()
            )
            return {str(message_id): str(label) for message_id, label in rows}

    def list_for_message(self, org_id: str, message_id: str) -> list[dict[str, Any]]:
        """Every action on one message, newest first. One query, not a scan."""
        with get_session() as session:
            rows = (
                session.query(ProposedAction)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.message_id == message_id,
                )
                .order_by(ProposedAction.created_at.desc())
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_pending_with_messages(
        self, org_id: str, limit: int | None = None, offset: int | None = None
    ) -> dict[str, Any]:
        """Pending actions joined to their messages, in one query.

        The approvals page used to fetch the action list and then issue a
        separate ``messages.get()`` per row — a textbook N+1 on the page that
        is, by design, the busiest one in the product.
        """
        safe_limit, safe_offset = clamp_page(limit, offset)
        with get_session() as session:
            query = (
                session.query(ProposedAction, ProcessedMessage)
                .join(ProcessedMessage, ProposedAction.message_id == ProcessedMessage.id)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.status == "proposed",
                )
            )
            total = query.count()
            rows = (
                query.order_by(ProposedAction.created_at.desc())
                .offset(safe_offset)
                .limit(safe_limit)
                .all()
            )
            return {
                "items": [
                    {"action": action.to_dict(), "message": message.to_dict()}
                    for action, message in rows
                ],
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
            }

    def summarize_for_org(self, org_id: str) -> dict[str, int]:
        """Counts of what the copilot did, aggregated in the database.

        Every other count in this layer is ``len()`` over one page of results,
        which silently under-reports the moment an org has more actions than the
        page size. This groups in SQL instead, so the inbox summary stays true
        for a real mailbox rather than only for the demo's fourteen messages.
        """
        with get_session() as session:
            rows = (
                session.query(ProposedAction.status, func.count(ProposedAction.id))
                .filter(ProposedAction.org_id == org_id)
                .group_by(ProposedAction.status)
                .all()
            )
            by_status = {str(status): int(count) for status, count in rows}
            llm_drafts = (
                session.query(func.count(ProposedAction.id))
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.draft_source == "llm",
                )
                .scalar()
                or 0
            )
        return {
            "total": sum(by_status.values()),
            "awaiting": by_status.get("proposed", 0),
            "auto_applied": by_status.get("executed", 0),
            "decided": by_status.get("approved", 0) + by_status.get("rejected", 0),
            "llm_drafts": int(llm_drafts),
        }

    def verification_summary(self, org_id: str) -> dict[str, int]:
        """How much checking has happened, and what it caught.

        This is the number that sells the product — no competitor can tell a
        customer whether the draft it wrote is true — and it was sitting in the
        database being rendered as a single chip on one page.
        """
        with get_session() as session:
            rows = (
                session.query(ProposedAction.verification_status, func.count(ProposedAction.id))
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.verification_status.isnot(None),
                )
                .group_by(ProposedAction.verification_status)
                .all()
            )
            by_status = {str(status): int(count) for status, count in rows}
            # Claims are counted from the note lines rather than the JSON blob:
            # notes exist on every flagged action, including those verified
            # before the evidence column did.
            flagged_notes = (
                session.query(ProposedAction.verification_notes)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.verification_status == "flagged",
                )
                .all()
            )
        claims = sum(
            len([line for line in (n[0] or "").split("\n") if line]) for n in flagged_notes
        )
        verified = by_status.get("verified", 0)
        flagged = by_status.get("flagged", 0)
        return {
            "checked": verified + flagged,
            "verified": verified,
            "flagged": flagged,
            "claims_caught": claims,
        }

    def list_failed_sends(self, org_id: str, max_retries: int, limit: int = 50) -> list[dict]:
        """Approved actions whose provider write failed and may be retried.

        Only actions a human *decided* on: an auto-applied label that failed is
        cosmetic, but an approved reply that never left is the product silently
        not doing the one thing it was told to do.
        """
        with get_session() as session:
            rows = (
                session.query(ProposedAction)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.status == "failed",
                    ProposedAction.requires_approval.is_(True),
                    ProposedAction.decided_by.isnot(None),
                    func.coalesce(ProposedAction.retry_count, 0) < max_retries,
                )
                .order_by(ProposedAction.decided_at.asc())
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]

    def orgs_with_failed_sends(self, max_retries: int) -> list[str]:
        """Org ids with at least one retryable failed send. The worker's list."""
        with get_session() as session:
            rows = (
                session.query(ProposedAction.org_id)
                .filter(
                    ProposedAction.status == "failed",
                    ProposedAction.requires_approval.is_(True),
                    ProposedAction.decided_by.isnot(None),
                    func.coalesce(ProposedAction.retry_count, 0) < max_retries,
                )
                .distinct()
                .all()
            )
            return [str(r[0]) for r in rows]

    def get(self, org_id: str, action_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            row = (
                session.query(ProposedAction)
                .filter(ProposedAction.id == action_id, ProposedAction.org_id == org_id)
                .first()
            )
            return row.to_dict() if row else None

    def list_decided(self, org_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """Human-decided actions, newest first, joined with the message's
        inferred signals. This is the learning corpus: every row is a labeled
        example (the copilot proposed X on a message shaped Y; a person said
        yes / fixed the wording / said no)."""
        with get_session() as session:
            rows = (
                session.query(ProposedAction, ProcessedMessage)
                .join(ProcessedMessage, ProposedAction.message_id == ProcessedMessage.id)
                .filter(
                    ProposedAction.org_id == org_id,
                    ProposedAction.outcome.in_(["approved", "edited", "rejected"]),
                )
                .order_by(ProposedAction.decided_at.desc())
                .limit(limit)
                .all()
            )
            out = []
            for action, message in rows:
                item = action.to_dict()
                item["sender_role"] = message.sender_role
                item["subject"] = message.subject
                item["sender"] = message.sender
                out.append(item)
            return out

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

    def set_status(self, lead_id: int, status: str) -> dict[str, Any] | None:
        """Move a lead through the funnel (new -> contacted -> closed)."""
        with get_session() as session:
            lead = session.get(SalesLead, lead_id)
            if not lead:
                return None
            lead.status = status
            session.flush()
            return lead.to_dict()
