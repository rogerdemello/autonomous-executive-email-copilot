"""Mailbox connection API: list providers, connect (OAuth), disconnect.

Connecting or disconnecting a mailbox is a privileged action (admin+). The OAuth
callback is public — the provider redirects a browser to it with no auth header,
so identity rides in the signed ``state`` instead.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import oauth
from .deps import get_current_user, require_role
from .mailbox import MailboxError, MailboxService
from .models_db import ROLE_ADMIN

logger = logging.getLogger(__name__)

mailbox_router = APIRouter(prefix="/mailbox", tags=["mailbox"])
_service = MailboxService()


@mailbox_router.get("/providers")
def list_providers(user: dict = Depends(get_current_user)) -> dict:
    """Available mailbox providers and whether each is configured on this server."""
    return {"providers": oauth.available_providers()}


@mailbox_router.get("/connections")
def list_connections(user: dict = Depends(get_current_user)) -> dict:
    return {"connections": _service.list_connections(user["org_id"])}


@mailbox_router.post("/connect/{provider}")
def connect(
    provider: str,
    actor: dict = Depends(require_role(ROLE_ADMIN)),
) -> dict:
    """Begin an OAuth connect; returns the provider consent URL for the client
    to redirect to (``window.location = authorize_url``)."""
    try:
        url = _service.start_connect(
            org_id=actor["org_id"], user_id=actor["id"], provider_key=provider
        )
    except MailboxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"authorize_url": url}


@mailbox_router.get("/oauth/callback", include_in_schema=False)
def oauth_callback(request: Request) -> HTMLResponse:
    """Public OAuth redirect target. Identity is carried in the signed state."""
    params = request.query_params
    error = params.get("error")
    if error:
        return _result_page(False, f"Authorization was denied ({error}).")
    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return _result_page(False, "Missing authorization code or state.")
    try:
        conn = _service.complete_callback(
            state=state, code=code, request_base_url=str(request.base_url).rstrip("/")
        )
    except MailboxError as exc:
        logger.warning("Mailbox OAuth callback failed: %s", exc.message)
        return _result_page(False, exc.message)
    return _result_page(True, f"Connected {conn['account_email']} ({conn['provider']}).")


@mailbox_router.delete("/connections/{connection_id}")
def disconnect(
    connection_id: str,
    actor: dict = Depends(require_role(ROLE_ADMIN)),
) -> dict:
    ok = _service.disconnect(
        org_id=actor["org_id"], user_id=actor["id"], connection_id=connection_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "ok", "disconnected": connection_id}


def _result_page(ok: bool, message: str) -> HTMLResponse:
    """A tiny self-contained page the OAuth popup/redirect lands on."""
    icon = "✓" if ok else "✕"
    color = "#0f9d69" if ok else "#d64545"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mailbox connection</title>
<style>body{{font-family:-apple-system,Segoe UI,Arial,sans-serif;background:#0b1020;color:#eef2ff;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#161d3a;border:1px solid #26305a;border-radius:16px;padding:34px 40px;max-width:420px;text-align:center}}
.icon{{font-size:44px;color:{color}}} a{{color:#6d8bff}}</style></head>
<body><div class="card"><div class="icon">{icon}</div>
<h2>{"Mailbox connected" if ok else "Connection failed"}</h2>
<p style="color:#9aa7c7">{message}</p>
<p><a href="/dashboard/">Return to the dashboard</a></p></div></body></html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)
