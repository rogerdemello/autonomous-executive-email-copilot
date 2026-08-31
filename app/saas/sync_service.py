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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.copilot import enrich, pipeline
from app.copilot.providers.base import FetchedMessage, MailProvider
from app.core.config import get_settings

from .repository import (
    AuditRepository,
    CommitmentRepository,
    MailboxRepository,
    OrganizationRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

try:
    from telemetry.otel import in_span
except ImportError:  # pragma: no cover - telemetry is optional

    def in_span(name, attributes=None, kind=None):
        from contextlib import nullcontext

        return nullcontext()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# How many times a failed send is re-attempted before it needs a human. Three
# covers the transient cases (a token refresh mid-write, a provider 503) without
# hammering a recipient address that is simply wrong.
MAX_SEND_RETRIES = 3


class ProcessingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedDraft:
    """The prose held for approval, and where it came from."""

    body: str | None
    source: str  # llm | authored | generic
    confidence: float | None = None
    rationale: list[str] = field(default_factory=list)


def _draft_text(provider: MailProvider, proposal) -> str | None:
    """The reply body to hold for approval, provider-authored where available.

    Kept as the narrow, dependency-free path: the policy decides *whether* to
    reply and emits one generic sentence, and a provider may offer better wording
    for a specific message (the demo mailbox ships authored drafts). See
    :func:`resolve_draft` for the full resolution including model-written prose.

    Still gated on ``reply``. An authored draft answers the *sender*; an escalation
    needs a handover note to a colleague, and showing the one in place of the
    other would misdescribe what approving the action does. Escalation prose comes
    from the model, which is asked for a handover note explicitly.
    """
    author = getattr(provider, "draft_for", None)
    if proposal.action_type == "reply" and callable(author):
        drafted = author(proposal.email_id)
        if drafted:
            return str(drafted)
    return proposal.content


def resolve_draft(
    provider: MailProvider,
    proposal,
    *,
    message: FetchedMessage | None = None,
    signals=None,
    context=None,
    live_llm: bool = False,
    examples: list[dict] | None = None,
) -> ResolvedDraft:
    """Resolve the prose for one held action, best source first.

    Order is cache → model → provider-authored → the policy's generic sentence.
    The cache comes first so a seeded demo never touches the network; the model
    is only consulted when ``live_llm`` is set, because a sync is request-bound
    and a stalled provider would be felt by the user.

    ``examples`` are this org's recently accepted drafts (see
    :mod:`app.saas.learning`); they ride along in the prompt and in the cache
    key, so a workspace whose voice has changed re-drafts rather than replaying
    prose written before the feedback existed.

    Every step degrades rather than raises. Losing the model costs prose, not the
    decision — which was made deterministically before any of this ran.
    """
    from app.llm.draft_cache import draft_key, examples_digest, get_draft_cache

    action_type = proposal.action_type
    fallback = _draft_text(provider, proposal)

    if message is None or action_type not in ("reply", "escalate"):
        return ResolvedDraft(body=fallback, source="authored" if fallback else "generic")

    key = draft_key(
        provider_message_id=message.provider_message_id,
        subject=message.subject or "",
        body=message.body or "",
        action_type=action_type,
        extra=examples_digest(examples),
    )

    cache = get_draft_cache()
    cached = cache.get(key)
    if cached:
        return ResolvedDraft(
            body=str(cached["body"]),
            source="llm",
            confidence=cached.get("confidence"),
            rationale=list(cached.get("rationale") or []),
        )

    if live_llm:
        from app.llm.drafter import get_drafter

        result = get_drafter().draft(
            message=message,
            action_type=action_type,
            signals=signals,
            escalate_to=proposal.escalate_to,
            context=context,
            examples=examples,
        )
        if result is not None:
            cache.put(
                key,
                body=result.body,
                rationale=result.rationale,
                confidence=result.confidence,
                model=result.model,
                subject=message.subject or "",
            )
            return ResolvedDraft(
                body=result.body,
                source="llm",
                confidence=result.confidence,
                rationale=result.rationale,
            )

    return ResolvedDraft(body=fallback, source="authored" if fallback else "generic")


class InboxSyncService:
    def __init__(self) -> None:
        self.mailboxes = MailboxRepository()
        self.messages = ProcessedMessageRepository()
        self.actions = ProposedActionRepository()
        self.audit = AuditRepository()
        self.orgs = OrganizationRepository()
        self.users = UserRepository()
        self.commitments = CommitmentRepository()

    def _require_active_plan(self, org_id: str) -> None:
        """Sync and approve are the value loop; a lapsed plan stops them here.

        Deliberately NOT enforced on sign-in, settings, or rejection - an admin
        needs those exactly when the plan has expired. Imported lazily to keep
        this module's import surface small.
        """
        from .billing import BillingError, BillingService

        try:
            BillingService().require_active(org_id)
        except BillingError as exc:
            raise ProcessingError(exc.message, exc.status_code) from exc

    def _draft_context(self, org_id: str, user_id: str):
        """Who the drafter writes as. Best-effort — defaults are always usable."""
        from app.llm.drafter import DraftContext

        try:
            org = self.orgs.get(org_id) or {}
            user = self.users.get(org_id, user_id) or {}
        except Exception:  # noqa: BLE001 - naming is cosmetic, never fail a sync
            return DraftContext()
        kwargs = {}
        if user.get("full_name"):
            kwargs["executive_name"] = user["full_name"]
        if org.get("name"):
            kwargs["organisation"] = org["name"]
        return DraftContext(**kwargs)

    # -- sync ---------------------------------------------------------------
    def sync(
        self,
        *,
        org_id: str,
        user_id: str,
        connection_id: str,
        provider: MailProvider,
        live_llm: bool | None = None,
    ) -> dict:
        # One span per sync: the fetch, every draft and every provider write
        # happen inside it, so a slow sync is attributable in a trace.
        with in_span("inbox.sync", {"org_id": org_id, "connection_id": connection_id}):
            return self._sync(
                org_id=org_id,
                user_id=user_id,
                connection_id=connection_id,
                provider=provider,
                live_llm=live_llm,
            )

    def _sync(
        self,
        *,
        org_id: str,
        user_id: str,
        connection_id: str,
        provider: MailProvider,
        live_llm: bool | None = None,
    ) -> dict:
        self._require_active_plan(org_id)
        connection = self.mailboxes.get(org_id, connection_id)
        if not connection:
            raise ProcessingError("Mailbox connection not found", 404)
        account_email = connection.get("account_email", "unknown")

        settings = get_settings()
        if live_llm is None:
            live_llm = settings.llm_drafting_enabled

        # Explicit: the provider interface's own default is 25, which quietly
        # truncates any mailbox larger than that.
        fetched = provider.fetch_messages(limit=settings.inbox_sync_limit)
        observation = enrich.to_observation(fetched, account_email=account_email)
        proposals = pipeline.to_proposals(pipeline.run_policy(observation))

        # Proposals carry only an email_id; drafting needs the message itself and
        # the signals inferred from it.
        message_by_id = {m.provider_message_id: m for m in fetched}
        signals_by_id = {e.id: e for e in observation.emails}
        draft_context = self._draft_context(org_id, user_id) if live_llm else None

        # What this org's reviewers have taught the copilot (see app.saas.learning):
        # pairs they keep rejecting get downgraded below, and their accepted drafts
        # ride along in the drafting prompt. Examples are only fetched when live
        # drafting is on — with it off, cached prose replays under its original key.
        from .learning import FeedbackService

        feedback = FeedbackService()
        suppressed = feedback.suppressed_pairs(org_id)
        examples_by_type: dict[str, list[dict]] = {}

        def _examples(action_type: str) -> list[dict] | None:
            if not live_llm:
                return None
            if action_type not in examples_by_type:
                examples_by_type[action_type] = feedback.few_shot_examples(org_id, action_type)
            return examples_by_type[action_type] or None

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
                sender_name=msg.sender_name or None,
                received_at=msg.received_at or None,
                subject=msg.subject,
                body_preview=(msg.body or "")[:500],
                # The preview drives the list; the full body drives the reader.
                body=msg.body or None,
                sender_role=obs_email.sender_role,
                priority_hint=obs_email.priority_hint,
                risk_tag=obs_email.risk_tag,
                deadline_minutes=obs_email.deadline_minutes,
                business_value=obs_email.business_value,
            )
            row_by_provider_id[msg.provider_message_id] = row

        # What this mailbox is now waiting on, and what it now owes. Extracted
        # from the message text the same deterministic way the routing signals
        # are, so it costs nothing and behaves identically with the model off.
        #
        # Spam is excluded using the classifier's own verdict. "Subscribe now
        # and we will register your team at a permanent discount" parses as a
        # perfectly good promise, and two of them in a seven-row follow-up list
        # is enough to make nobody open it again.
        spam_ids = {
            p.email_id for p in proposals if p.action_type == "classify" and p.label == "spam"
        }
        commitments_found = self._record_commitments(
            org_id, fetched, row_by_provider_id, skip_ids=spam_ids
        )

        proposed_count = 0
        auto_count = 0
        skipped_count = 0
        downgraded_count = 0
        # Low-risk actions become labels; grouping them by label lets a provider
        # with a batch write (Gmail's batchModify) apply each label in one call
        # instead of one call per message.
        auto_by_label: dict[str, list[tuple]] = {}
        for prop in proposals:
            message_row = row_by_provider_id.get(prop.email_id)
            if message_row is None:
                continue
            # Idempotency: if this message already has a live (non-rejected,
            # non-failed) action of this type, skip it. This makes re-syncing an
            # inbox safe — no duplicate proposals and no repeated provider writes.
            active_types = self.actions.active_types_for_message(org_id, message_row["id"])
            if prop.action_type in active_types:
                skipped_count += 1
                continue
            if prop.requires_approval:
                signals = signals_by_id.get(prop.email_id)
                pair = (prop.action_type, (signals.sender_role if signals else None) or "unknown")
                vetoed = suppressed.get(pair)
                if vetoed is not None:
                    # The org's reviewers have repeatedly rejected this shape of
                    # proposal — file it instead of asking again, and say why.
                    if "defer" in active_types:
                        skipped_count += 1
                        continue
                    result = provider.add_label(prop.email_id, "deferred")
                    self.actions.create(
                        org_id=org_id,
                        message_id=message_row["id"],
                        action_type="defer",
                        content=None,
                        escalate_to=None,
                        label="deferred",
                        status="executed" if result.ok else "failed",
                        requires_approval=False,
                        outcome="auto" if result.ok else None,
                        execution_ref=result.provider_ref,
                        executed_at=_now_iso() if result.ok else None,
                        rationale=[
                            f"Downgraded from {prop.action_type}: you rejected "
                            f"{vetoed.rejected} of the last {vetoed.total} "
                            f"{prop.action_type} proposals for {pair[1]} senders"
                        ],
                    )
                    downgraded_count += 1
                    continue
                # External action (reply/escalate) — hold for a human.
                message = message_by_id.get(prop.email_id)
                drafted = resolve_draft(
                    provider,
                    prop,
                    message=message,
                    signals=signals,
                    context=draft_context,
                    live_llm=live_llm,
                    examples=_examples(prop.action_type),
                )
                # Draft-then-verify: check the prose against its source before
                # it queues. A flagged draft still queues — the human is the
                # gate — but the reviewer sees what to look at first.
                verification_status = None
                verification_notes: list[str] = []
                verification_claims: list[dict] = []
                if drafted.body and message is not None:
                    from app.llm.verifier import verify_draft

                    verdict = verify_draft(
                        drafted.body,
                        message=message,
                        action_type=prop.action_type,
                        live_llm=live_llm,
                    )
                    verification_status = verdict.status
                    verification_notes = verdict.notes
                    verification_claims = [f.to_dict() for f in verdict.findings]
                self.actions.create(
                    org_id=org_id,
                    message_id=message_row["id"],
                    action_type=prop.action_type,
                    content=drafted.body,
                    escalate_to=prop.escalate_to,
                    label=prop.label,
                    status="proposed",
                    requires_approval=True,
                    draft_source=drafted.source,
                    draft_confidence=drafted.confidence,
                    rationale=drafted.rationale,
                    verification_status=verification_status,
                    verification_notes=verification_notes,
                    verification_claims=verification_claims,
                )
                proposed_count += 1
            else:
                # Internal/low-risk (classify/defer) — auto-apply as a label.
                label = prop.label or (
                    "deferred" if prop.action_type == "defer" else prop.action_type
                )
                auto_by_label.setdefault(label, []).append((prop, message_row))

        batch_write = getattr(provider, "add_labels_batch", None)
        for label, items in auto_by_label.items():
            if callable(batch_write) and len(items) > 1:
                # One provider call for the whole label group.
                results = [batch_write([p.email_id for p, _ in items], label)] * len(items)
            else:
                results = [provider.add_label(p.email_id, label) for p, _ in items]
            for (prop, message_row), result in zip(items, results, strict=True):
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

        if live_llm:
            # Persist anything the model just wrote, so the next run — and the
            # demo — replays it from disk instead of paying for it again.
            try:
                from app.llm.draft_cache import get_draft_cache

                get_draft_cache().save()
            except OSError as exc:
                logger.warning("Could not write the draft cache: %s", exc)

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
                "downgraded": downgraded_count,
                "commitments": commitments_found,
            },
        )
        return {
            "connection_id": connection_id,
            "messages": len(fetched),
            "proposed": proposed_count,
            "auto_executed": auto_count,
            "skipped": skipped_count,
            "downgraded": downgraded_count,
            "commitments": commitments_found,
        }

    def _record_commitments(
        self,
        org_id: str,
        fetched: list[FetchedMessage],
        row_by_provider_id: dict[str, dict],
        *,
        skip_ids: set[str] | None = None,
    ) -> int:
        """Extract and store the promises in a batch of incoming messages.

        Best-effort by construction: a commitment the extractor misses is a
        missing row, but an exception here would abort a sync that had already
        done its real work. Returns how many were newly recorded.

        Incoming mail is read with ``include_requests``: "please send the
        figures by Thursday" is not a promise anyone made, but it is
        unambiguously something you now owe, and leaving it off the list is
        exactly the failure this surface exists to prevent.
        """
        from app.copilot import commitments as extractor

        skip_ids = skip_ids or set()
        recorded = 0
        for msg in fetched:
            if msg.provider_message_id in skip_ids:
                continue
            row = row_by_provider_id.get(msg.provider_message_id)
            if row is None:
                continue
            try:
                found = extractor.extract(
                    msg.body or "", direction=extractor.THEIRS, include_requests=True
                )
            except Exception:  # noqa: BLE001 - never fail a sync over a follow-up
                logger.exception("Commitment extraction failed for %s", msg.provider_message_id)
                continue
            for item in found:
                created = self.commitments.upsert(
                    org_id=org_id,
                    message_id=row["id"],
                    thread_id=row.get("thread_id"),
                    direction=item.direction,
                    text=item.text,
                    due_at=item.due_at,
                    due_phrase=item.due_phrase,
                    counterparty=msg.sender,
                    subject=msg.subject,
                )
                if created:
                    recorded += 1
        return recorded

    # -- approve / reject ---------------------------------------------------
    def approve(
        self,
        *,
        org_id: str,
        user_id: str,
        action_id: str,
        provider: MailProvider,
        edited_content: str | None = None,
    ) -> dict:
        with in_span("inbox.approve", {"org_id": org_id, "action_id": action_id}):
            return self._approve(
                org_id=org_id,
                user_id=user_id,
                action_id=action_id,
                provider=provider,
                edited_content=edited_content,
            )

    def _approve(
        self,
        *,
        org_id: str,
        user_id: str,
        action_id: str,
        provider: MailProvider,
        edited_content: str | None = None,
    ) -> dict:
        # Approving dispatches an outbound write - part of the value loop the
        # plan pays for. Rejection stays free: it only records a decision.
        self._require_active_plan(org_id)
        action = self.actions.get(org_id, action_id)
        if not action:
            raise ProcessingError("Action not found", 404)
        if action["status"] != "proposed":
            raise ProcessingError(f"Action is already {action['status']}", 409)

        message = self.messages.get(org_id, action["message_id"])
        if not message:
            raise ProcessingError("Message for this action no longer exists", 404)
        provider_message_id = message["provider_message_id"]

        # A reviewer amending the draft before approving is the strongest
        # feedback the product collects: keep both texts, send the human's.
        outcome = "approved"
        original = action.get("content")
        edited = (edited_content or "").strip()
        if edited and edited != (original or "").strip():
            action["content"] = edited
            outcome = "edited"

        result = self._execute(provider, action, provider_message_id, message)
        now = _now_iso()
        if result.ok:
            amended = (
                {"content": action["content"], "original_content": original}
                if outcome == "edited"
                else {}
            )
            updated = self.actions.set_status(
                org_id,
                action_id,
                "executed",
                outcome=outcome,
                decided_by=user_id,
                decided_at=now,
                executed_at=now,
                execution_ref=result.provider_ref,
                **amended,
            )
        else:
            # Failed, but not forgotten: the background worker retries this
            # (see `retry_failed_sends`). Before that existed, "failed" was
            # terminal and nothing looked at it again — a reviewer approved a
            # reply, the provider hiccuped, and it silently never went.
            updated = self.actions.set_status(
                org_id,
                action_id,
                "failed",
                decided_by=user_id,
                decided_at=now,
                last_error=result.detail or "the provider rejected the write",
            )
        if result.ok:
            # A promise in a reply you just sent is a promise you now owe. This
            # is the half of follow-up tracking that only the sending party
            # can see, and the reason a bolt-on tracker cannot do it properly.
            self._record_own_commitments(org_id, action, message)
        self.audit.record(
            action="inbox.action.approve",
            org_id=org_id,
            actor_user_id=user_id,
            target=action_id,
            detail={
                "action_type": action["action_type"],
                "ok": result.ok,
                "edited": outcome == "edited",
            },
        )
        return updated or {}

    def _record_own_commitments(self, org_id: str, action: dict, message: dict) -> int:
        """Extract promises from a reply the workspace just sent."""
        if action.get("action_type") != "reply" or not action.get("content"):
            return 0
        from app.copilot import commitments as extractor

        recorded = 0
        try:
            found = extractor.extract(action["content"], direction=extractor.OURS)
        except Exception:  # noqa: BLE001 - the reply was sent; this is bookkeeping
            logger.exception("Commitment extraction failed for action %s", action.get("id"))
            return 0
        for item in found:
            created = self.commitments.upsert(
                org_id=org_id,
                message_id=message.get("id"),
                thread_id=message.get("thread_id"),
                direction=extractor.OURS,
                text=item.text,
                due_at=item.due_at,
                due_phrase=item.due_phrase,
                counterparty=message.get("sender"),
                subject=message.get("subject"),
            )
            if created:
                recorded += 1
        return recorded

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

    # -- retry --------------------------------------------------------------
    def retry_failed_sends(self, *, org_id: str, max_retries: int = MAX_SEND_RETRIES) -> dict:
        """Re-dispatch approved actions whose provider write failed.

        A human already made the decision here — this only re-attempts the
        mechanical step that dropped. Bounded by ``retry_count`` so a
        permanently-broken recipient is not retried forever, and the last error
        is kept so a reviewer can see why it is stuck rather than watching an
        action sit in "failed" with no explanation.

        Deliberately does *not* retry auto-applied labels: a label that failed
        to apply is cosmetic, and re-firing writes nobody approved is exactly
        the behaviour the approval gate exists to prevent.
        """
        from .provider_factory import BrokenConnectionError, build_provider

        summary = {"attempted": 0, "recovered": 0, "still_failing": 0}
        for action in self.actions.list_failed_sends(org_id, max_retries=max_retries):
            message = self.messages.get(org_id, action["message_id"])
            if not message:
                continue
            connection = self.mailboxes.get(org_id, message["connection_id"])
            if not connection:
                continue
            try:
                provider = build_provider(connection)
            except BrokenConnectionError as exc:
                # The mailbox itself needs a human to reconnect; retrying the
                # send cannot help and would just burn the retry budget.
                logger.info(
                    "Not retrying action %s: its mailbox needs reconnecting (%s)",
                    action["id"],
                    exc,
                )
                continue

            summary["attempted"] += 1
            attempts = int(action.get("retry_count") or 0) + 1
            result = self._execute(provider, action, message["provider_message_id"], message)
            now = _now_iso()
            if result.ok:
                self.actions.set_status(
                    org_id,
                    action["id"],
                    "executed",
                    outcome=action.get("outcome") or "approved",
                    executed_at=now,
                    execution_ref=result.provider_ref,
                    retry_count=attempts,
                    last_error=None,
                )
                summary["recovered"] += 1
            else:
                self.actions.set_status(
                    org_id,
                    action["id"],
                    "failed",
                    retry_count=attempts,
                    last_error=result.detail or "the provider rejected the write",
                )
                summary["still_failing"] += 1
            self.audit.record(
                action="inbox.action.retry",
                org_id=org_id,
                target=action["id"],
                detail={
                    "action_type": action["action_type"],
                    "attempt": attempts,
                    "ok": result.ok,
                },
            )
        return summary

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
