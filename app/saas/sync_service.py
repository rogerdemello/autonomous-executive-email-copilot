"""Inbox sync service: run the copilot over a connected mailbox, tenant-scoped.

Orchestrates the gold-free product pipeline (``app.copilot``) with tenant
persistence and the mailbox provider: fetch → enrich → decide → persist proposed
actions → (auto-execute low-risk, hold external actions for approval). Approvals
dispatch to the provider's write surface and capture the human outcome — the
non-gold quality signal that replaces the sim's grader.

The provider is passed in, so this is fully testable with the in-memory
``FakeProvider`` and slots behind a background worker later unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.copilot import enrich, pipeline
from app.copilot.providers.base import MailProvider

from .repository import (
    AuditRepository,
    MailboxRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcessingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InboxSyncService:
    def __init__(self) -> None:
        self.mailboxes = MailboxRepository()
        self.messages = ProcessedMessageRepository()
        self.actions = ProposedActionRepository()
        self.audit = AuditRepository()

    # -- sync ---------------------------------------------------------------
    def sync(
        self, *, org_id: str, user_id: str, connection_id: str, provider: MailProvider
    ) -> dict:
        connection = self.mailboxes.get(org_id, connection_id)
        if not connection:
            raise ProcessingError("Mailbox connection not found", 404)
        account_email = connection.get("account_email", "unknown")

        fetched = provider.fetch_messages()
        observation = enrich.to_observation(fetched, account_email=account_email)
        proposals = pipeline.to_proposals(pipeline.run_policy(observation))

        # Persist each fetched message; keep a provider_id -> row_id map to link
        # proposals to their message.
        row_by_provider_id: dict[str, dict] = {}
        for msg, obs_email in zip(fetched, observation.emails, strict=True):
            row = self.messages.upsert(
                org_id=org_id,
                connection_id=connection_id,
                provider_message_id=msg.provider_message_id,
                thread_id=msg.thread_id or None,
                sender=msg.sender,
                subject=msg.subject,
                body_preview=(msg.body or "")[:500],
                sender_role=obs_email.sender_role,
                priority_hint=obs_email.priority_hint,
                risk_tag=obs_email.risk_tag,
                deadline_minutes=obs_email.deadline_minutes,
                business_value=obs_email.business_value,
            )
            row_by_provider_id[msg.provider_message_id] = row

        proposed_count = 0
        auto_count = 0
        skipped_count = 0
        for prop in proposals:
            message_row = row_by_provider_id.get(prop.email_id)
            if message_row is None:
                continue
            # Idempotency: if this message already has a live (non-rejected,
            # non-failed) action of this type, skip it. This makes re-syncing an
            # inbox safe — no duplicate proposals and no repeated provider writes.
            if prop.action_type in self.actions.active_types_for_message(org_id, message_row["id"]):
                skipped_count += 1
                continue
            if prop.requires_approval:
                # External action (reply/escalate) — hold for a human.
                self.actions.create(
                    org_id=org_id,
                    message_id=message_row["id"],
                    action_type=prop.action_type,
                    content=prop.content,
                    escalate_to=prop.escalate_to,
                    label=prop.label,
                    status="proposed",
                    requires_approval=True,
                )
                proposed_count += 1
            else:
                # Internal/low-risk (classify/defer) — auto-apply as a label.
                label = prop.label or (
                    "deferred" if prop.action_type == "defer" else prop.action_type
                )
                result = provider.add_label(prop.email_id, label)
                self.actions.create(
                    org_id=org_id,
                    message_id=message_row["id"],
                    action_type=prop.action_type,
                    content=prop.content,
                    escalate_to=prop.escalate_to,
                    label=label,
                    status="executed" if result.ok else "failed",
                    requires_approval=False,
                    outcome="auto" if result.ok else None,
                    execution_ref=result.provider_ref,
                    executed_at=_now_iso() if result.ok else None,
                )
                auto_count += 1

        self.mailboxes.set_synced_at(org_id, connection_id, _now_iso())
        self.audit.record(
            action="inbox.sync",
            org_id=org_id,
            actor_user_id=user_id,
            target=connection_id,
            detail={
                "messages": len(fetched),
                "proposed": proposed_count,
                "auto_executed": auto_count,
                "skipped": skipped_count,
            },
        )
        return {
            "connection_id": connection_id,
            "messages": len(fetched),
            "proposed": proposed_count,
            "auto_executed": auto_count,
            "skipped": skipped_count,
        }

    # -- approve / reject ---------------------------------------------------
    def approve(self, *, org_id: str, user_id: str, action_id: str, provider: MailProvider) -> dict:
        action = self.actions.get(org_id, action_id)
        if not action:
            raise ProcessingError("Action not found", 404)
        if action["status"] != "proposed":
            raise ProcessingError(f"Action is already {action['status']}", 409)

        message = self.messages.get(org_id, action["message_id"])
        if not message:
            raise ProcessingError("Message for this action no longer exists", 404)
        provider_message_id = message["provider_message_id"]

        result = self._execute(provider, action, provider_message_id, message)
        now = _now_iso()
        if result.ok:
            updated = self.actions.set_status(
                org_id,
                action_id,
                "executed",
                outcome="approved",
                decided_by=user_id,
                decided_at=now,
                executed_at=now,
                execution_ref=result.provider_ref,
            )
        else:
            updated = self.actions.set_status(
                org_id, action_id, "failed", decided_by=user_id, decided_at=now
            )
        self.audit.record(
            action="inbox.action.approve",
            org_id=org_id,
            actor_user_id=user_id,
            target=action_id,
            detail={"action_type": action["action_type"], "ok": result.ok},
        )
        return updated or {}

    def reject(
        self, *, org_id: str, user_id: str, action_id: str, comment: str | None = None
    ) -> dict:
        action = self.actions.get(org_id, action_id)
        if not action:
            raise ProcessingError("Action not found", 404)
        if action["status"] != "proposed":
            raise ProcessingError(f"Action is already {action['status']}", 409)
        updated = self.actions.set_status(
            org_id,
            action_id,
            "rejected",
            outcome="rejected",
            decided_by=user_id,
            decided_at=_now_iso(),
        )
        self.audit.record(
            action="inbox.action.reject",
            org_id=org_id,
            actor_user_id=user_id,
            target=action_id,
            detail={"action_type": action["action_type"], "comment": comment},
        )
        return updated or {}

    def _execute(
        self, provider: MailProvider, action: dict, provider_message_id: str, message: dict
    ):
        """Dispatch an approved action to the provider's write surface."""
        action_type = action["action_type"]
        if action_type == "reply":
            body = action.get("content") or "Acknowledged — we will follow up shortly."
            return provider.send_reply(provider_message_id, body)
        if action_type == "escalate":
            target = action.get("escalate_to") or "the appropriate team"
            subject = message.get("subject") or ""
            body = (
                f"Escalating to {target}.\n\n"
                f"Original subject: {subject}\n"
                f"{action.get('content') or ''}"
            ).strip()
            return provider.create_draft(provider_message_id, body)
        # Any other approved action falls back to a label.
        return provider.add_label(provider_message_id, action.get("label") or action_type)
