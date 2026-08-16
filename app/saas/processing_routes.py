"""Inbox processing API: sync a connected mailbox, review + act on proposals.

Syncing and approving/rejecting are privileged (admin+). Reads (messages,
actions) are available to any org member. Self-authed via session tokens like the
other SaaS routers (``/inbox`` is in ``SAAS_SELF_AUTH_PREFIXES``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.copilot.providers.base import MailProvider

from . import provider_factory
from .deps import get_current_user, require_role
from .models_db import ROLE_ADMIN
from .repository import (
    MailboxRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
)
from .sync_service import InboxSyncService, ProcessingError

logger = logging.getLogger(__name__)

inbox_router = APIRouter(prefix="/inbox", tags=["inbox"])

_service = InboxSyncService()
_mailboxes = MailboxRepository()
_messages = ProcessedMessageRepository()
_actions = ProposedActionRepository()


def build_provider(connection: dict) -> MailProvider:
    """Build an authenticated provider for a connection.

    Delegates to ``provider_factory`` which decrypts the stored OAuth token and
    constructs the real Gmail/Graph provider (or a FakeProvider when the provider
    isn't configured / has no token). Kept as a module-level indirection so tests
    can monkeypatch it to inject a shared fake.
    """
    return provider_factory.build_provider(connection)


class SyncRequest(BaseModel):
    connection_id: str | None = None


class RejectRequest(BaseModel):
    comment: str | None = None


@inbox_router.post("/sync")
def sync(body: SyncRequest, actor: dict = Depends(require_role(ROLE_ADMIN))) -> dict:
    org_id = actor["org_id"]
    if body.connection_id:
        connections = [c for c in _mailboxes.list_for_org(org_id) if c["id"] == body.connection_id]
        if not connections:
            raise HTTPException(status_code=404, detail="Mailbox connection not found")
    else:
        connections = _mailboxes.list_for_org(org_id)
    if not connections:
        raise HTTPException(
            status_code=400, detail="No mailbox connected. Connect a mailbox first."
        )

    results = []
    for conn in connections:
        provider = build_provider(conn)
        try:
            results.append(
                _service.sync(
                    org_id=org_id,
                    user_id=actor["id"],
                    connection_id=conn["id"],
                    provider=provider,
                )
            )
        except ProcessingError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"results": results}


@inbox_router.get("/messages")
def list_messages(
    connection_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Processed messages (paginated). Returns ``{messages, total, limit, offset}``."""
    return _messages.list_for_org(
        user["org_id"], connection_id=connection_id, limit=limit, offset=offset
    )


@inbox_router.get("/actions")
def list_actions(
    status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Proposed/decided actions (paginated). Returns ``{actions, total, limit, offset}``."""
    return _actions.list_for_org(user["org_id"], status=status, limit=limit, offset=offset)


@inbox_router.post("/actions/{action_id}/approve")
def approve_action(action_id: str, actor: dict = Depends(require_role(ROLE_ADMIN))) -> dict:
    org_id = actor["org_id"]
    provider = _provider_for_action(org_id, action_id)
    try:
        updated = _service.approve(
            org_id=org_id, user_id=actor["id"], action_id=action_id, provider=provider
        )
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"action": updated}


@inbox_router.post("/actions/{action_id}/reject")
def reject_action(
    action_id: str, body: RejectRequest, actor: dict = Depends(require_role(ROLE_ADMIN))
) -> dict:
    try:
        updated = _service.reject(
            org_id=actor["org_id"], user_id=actor["id"], action_id=action_id, comment=body.comment
        )
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"action": updated}


def _provider_for_action(org_id: str, action_id: str) -> MailProvider:
    """Resolve the mailbox connection behind an action and build its provider."""
    action = _actions.get(org_id, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    message = _messages.get(org_id, action["message_id"])
    if not message:
        raise HTTPException(status_code=404, detail="Message for this action no longer exists")
    connection = _mailboxes.get(org_id, message["connection_id"])
    if not connection:
        raise HTTPException(status_code=404, detail="Mailbox connection no longer exists")
    return build_provider(connection)
