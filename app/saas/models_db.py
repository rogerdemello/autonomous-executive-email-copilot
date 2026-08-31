"""SQLAlchemy tables for the commercial SaaS layer.

These tables register on the same declarative ``Base`` as the rest of the app,
so ``Base.metadata.create_all`` (called by ``app.core.db.init_db``/``migrate_db``)
provisions them automatically. They are natively tenant-scoped: every
customer-owned row carries ``org_id``. No existing table is altered, so the
benchmark's deterministic tables and golden snapshots are untouched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.db import Base


def _new_id() -> str:
    """Opaque, URL-safe primary key."""
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Roles, highest privilege first. Membership role gates what a user may do
# within their organization (see app.saas.rbac).
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)


class Organization(Base):
    """A customer tenant. All product data is scoped to an organization."""

    __tablename__ = "saas_organizations"

    id = Column(String(32), primary_key=True, default=_new_id)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    status = Column(String(32), nullable=False, default="active")  # active | suspended
    created_at = Column(String(50), nullable=False, default=_now_iso)
    updated_at = Column(String(50), nullable=False, default=_now_iso, onupdate=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class User(Base):
    """A person who signs in. Belongs to exactly one organization."""

    __tablename__ = "saas_users"

    id = Column(String(32), primary_key=True, default=_new_id)
    org_id = Column(String(32), ForeignKey("saas_organizations.id"), nullable=False, index=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False, default="")
    role = Column(String(32), nullable=False, default=ROLE_MEMBER)
    status = Column(String(32), nullable=False, default="active")  # active | invited | disabled
    created_at = Column(String(50), nullable=False, default=_now_iso)
    updated_at = Column(String(50), nullable=False, default=_now_iso, onupdate=_now_iso)
    last_login_at = Column(String(50), nullable=True)

    def to_dict(self) -> dict:
        """Serialize WITHOUT the password hash — never expose credentials."""
        return {
            "id": self.id,
            "org_id": self.org_id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
        }


class License(Base):
    """A sales-issued entitlement grant for an organization.

    The full license *key* is a signed token given to the customer; we persist
    only its id (``key_id``) plus the decoded terms, so the key can be revoked
    (status -> revoked) and its seat/plan/feature terms enforced server-side.
    """

    __tablename__ = "saas_licenses"

    id = Column(String(32), primary_key=True, default=_new_id)
    org_id = Column(String(32), ForeignKey("saas_organizations.id"), nullable=False, index=True)
    key_id = Column(String(64), unique=True, nullable=False, index=True)
    plan = Column(String(32), nullable=False, default="trial")
    seats = Column(Integer, nullable=False, default=1)
    features_json = Column(Text, nullable=True)  # JSON list of feature flags
    status = Column(String(32), nullable=False, default="active")  # active | revoked | expired
    issued_at = Column(String(50), nullable=False, default=_now_iso)
    expires_at = Column(String(50), nullable=True)
    created_at = Column(String(50), nullable=False, default=_now_iso)

    def to_dict(self) -> dict:
        import json

        try:
            features = json.loads(self.features_json) if self.features_json else []  # type: ignore[arg-type]
        except (TypeError, ValueError):
            features = []
        return {
            "id": self.id,
            "org_id": self.org_id,
            "key_id": self.key_id,
            "plan": self.plan,
            "seats": self.seats,
            "features": features,
            "status": self.status,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


class AuditLogEntry(Base):
    """Append-only record of security-relevant actions (enterprise requirement)."""

    __tablename__ = "saas_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(String(32), nullable=True, index=True)
    actor_user_id = Column(String(32), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    target = Column(String(255), nullable=True)
    detail_json = Column(Text, nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(String(50), nullable=False, default=_now_iso, index=True)

    def to_dict(self) -> dict:
        import json

        try:
            detail = json.loads(self.detail_json) if self.detail_json else {}  # type: ignore[arg-type]
        except (TypeError, ValueError):
            detail = {}
        return {
            "id": self.id,
            "org_id": self.org_id,
            "actor_user_id": self.actor_user_id,
            "action": self.action,
            "target": self.target,
            "detail": detail,
            "ip": self.ip,
            "created_at": self.created_at,
        }


class MailboxConnection(Base):
    """A customer mailbox (Gmail / M365) connected via OAuth, scoped to an org.

    OAuth tokens are stored **encrypted** (see ``app.saas.crypto``). A row is the
    product's link to a real inbox the copilot will manage.
    """

    __tablename__ = "saas_mailbox_connections"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", "account_email", name="uq_mailbox_account"),
    )

    id = Column(String(32), primary_key=True, default=_new_id)
    org_id = Column(String(32), ForeignKey("saas_organizations.id"), nullable=False, index=True)
    connected_by = Column(String(32), nullable=True)  # user id
    provider = Column(String(32), nullable=False)  # google | microsoft
    account_email = Column(String(320), nullable=False)
    access_token_enc = Column(Text, nullable=True)
    refresh_token_enc = Column(Text, nullable=True)
    token_expires_at = Column(String(50), nullable=True)
    scopes = Column(Text, nullable=True)
    status = Column(
        String(32), nullable=False, default="connected"
    )  # connected | disconnected | error
    last_synced_at = Column(String(50), nullable=True)
    created_at = Column(String(50), nullable=False, default=_now_iso)
    updated_at = Column(String(50), nullable=False, default=_now_iso, onupdate=_now_iso)

    def to_dict(self) -> dict:
        """Serialize WITHOUT token material — never expose credentials."""
        return {
            "id": self.id,
            "org_id": self.org_id,
            "connected_by": self.connected_by,
            "provider": self.provider,
            "account_email": self.account_email,
            "status": self.status,
            "scopes": (self.scopes or "").split() if self.scopes else [],
            "token_expires_at": self.token_expires_at,
            "last_synced_at": self.last_synced_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProcessedMessage(Base):
    """A real email fetched from a connected mailbox and enriched with inferred
    signals. Tenant-scoped; distinct from the benchmark ``Episode`` table (which
    is sim-only and not org-scoped)."""

    __tablename__ = "saas_processed_messages"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "connection_id", "provider_message_id", name="uq_processed_message"
        ),
    )

    id = Column(String(32), primary_key=True, default=_new_id)
    org_id = Column(String(32), ForeignKey("saas_organizations.id"), nullable=False, index=True)
    connection_id = Column(
        String(32), ForeignKey("saas_mailbox_connections.id"), nullable=False, index=True
    )
    provider_message_id = Column(String(255), nullable=False)
    thread_id = Column(String(255), nullable=True)
    sender = Column(String(320), nullable=True)
    sender_name = Column(String(255), nullable=True)
    subject = Column(Text, nullable=True)
    # The first 500 characters, for the message list. Kept separate from `body`
    # so a list query never drags full message bodies across the wire.
    body_preview = Column(Text, nullable=True)
    # The whole message. Without this the reader pane could only ever show the
    # preview — you could not read an email in this inbox, which is a strange
    # limitation for an email product. Capped at BODY_MAX_CHARS on write so one
    # pathological message cannot bloat a tenant's table.
    body = Column(Text, nullable=True)
    sender_role = Column(String(32), nullable=True)
    priority_hint = Column(String(16), nullable=True)
    risk_tag = Column(String(32), nullable=True)
    deadline_minutes = Column(Integer, nullable=True)
    business_value = Column(Float, nullable=True)
    # When the provider says the message arrived, as opposed to when we pulled
    # it. The inbox orders and timestamps on this; synced_at is an ops detail.
    received_at = Column(String(50), nullable=True)
    synced_at = Column(String(50), nullable=False, default=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "connection_id": self.connection_id,
            "provider_message_id": self.provider_message_id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "sender_name": self.sender_name,
            "subject": self.subject,
            "body_preview": self.body_preview,
            "body": self.body,
            "sender_role": self.sender_role,
            "priority_hint": self.priority_hint,
            "risk_tag": self.risk_tag,
            "deadline_minutes": self.deadline_minutes,
            "business_value": self.business_value,
            "received_at": self.received_at,
            "synced_at": self.synced_at,
        }


class ProposedAction(Base):
    """An action the copilot proposes on a real message. External actions
    (reply/escalate) default to held-for-approval; ``outcome`` records the human
    decision — the non-gold quality signal that replaces the sim's grader."""

    __tablename__ = "saas_proposed_actions"

    id = Column(String(32), primary_key=True, default=_new_id)
    org_id = Column(String(32), ForeignKey("saas_organizations.id"), nullable=False, index=True)
    message_id = Column(
        String(32), ForeignKey("saas_processed_messages.id"), nullable=False, index=True
    )
    action_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=True)
    escalate_to = Column(String(255), nullable=True)
    label = Column(String(64), nullable=True)
    # proposed | approved | rejected | executed | failed
    status = Column(String(32), nullable=False, default="proposed", index=True)
    requires_approval = Column(Boolean, nullable=False, default=False)
    decided_by = Column(String(32), nullable=True)
    decided_at = Column(String(50), nullable=True)
    executed_at = Column(String(50), nullable=True)
    execution_ref = Column(String(255), nullable=True)
    outcome = Column(String(32), nullable=True)  # approved | edited | rejected | auto
    # When a reviewer amends the draft before approving, the proposed text is
    # kept here — the (original, edited) pair is the strongest learning signal
    # the product collects: a human corrected the copilot's wording.
    original_content = Column(Text, nullable=True)
    created_at = Column(String(50), nullable=False, default=_now_iso)
    # Where the *prose* came from — llm | authored | generic. The decision itself
    # is always the deterministic policy's, so this describes the words only.
    draft_source = Column(String(16), nullable=True)
    draft_confidence = Column(Float, nullable=True)
    # The reviewer-facing "why", newline-separated. Persisted rather than
    # recomputed so the approvals queue can show it too, and so it still reads
    # correctly months later even if the heuristics have since changed.
    rationale = Column(Text, nullable=True)
    # Draft-then-verify (app/llm/verifier.py): "verified" | "flagged" | NULL
    # (not checked — e.g. no draft body). Notes are newline-separated and say
    # exactly what was flagged; a flagged draft still queues, the human is the
    # gate.
    verification_status = Column(String(16), nullable=True)
    verification_notes = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "message_id": self.message_id,
            "action_type": self.action_type,
            "content": self.content,
            "escalate_to": self.escalate_to,
            "label": self.label,
            "status": self.status,
            "requires_approval": self.requires_approval,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "executed_at": self.executed_at,
            "execution_ref": self.execution_ref,
            "outcome": self.outcome,
            "original_content": self.original_content,
            "created_at": self.created_at,
            "draft_source": self.draft_source,
            "draft_confidence": self.draft_confidence,
            "rationale": [line for line in (self.rationale or "").split("\n") if line],
            "verification_status": self.verification_status,
            "verification_notes": [
                line for line in (self.verification_notes or "").split("\n") if line
            ],
        }


class SalesLead(Base):
    """A captured 'Contact sales' / license-request lead (sales-led funnel)."""

    __tablename__ = "saas_sales_leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(String(32), nullable=True, index=True)
    email = Column(String(320), nullable=False, index=True)
    name = Column(String(255), nullable=True)
    company = Column(String(255), nullable=True)
    seats = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    kind = Column(String(32), nullable=False, default="contact_sales")
    status = Column(String(32), nullable=False, default="new")  # new | contacted | closed
    created_at = Column(String(50), nullable=False, default=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "email": self.email,
            "name": self.name,
            "company": self.company,
            "seats": self.seats,
            "message": self.message,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
        }
