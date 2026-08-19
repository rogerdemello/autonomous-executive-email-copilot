"""The server-rendered UI: landing, auth, mailbox connect, inbox, approvals.

These handlers own no business logic. Every one of them calls the same services
the JSON API calls — :class:`~app.saas.auth.AuthService`,
:class:`~app.saas.mailbox.MailboxService`,
:class:`~app.saas.sync_service.InboxSyncService` — so a rule enforced for API
clients (role checks, seat limits, approval gating) is enforced here by
construction rather than by remembering to duplicate it.

Every mutating route is a plain HTML form POST guarded by a CSRF token, then a
redirect. Nothing on these pages requires JavaScript.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.copilot.providers.demo import DEMO_PROVIDER_KEY, demo_account_email, demo_message_count
from app.core.config import get_settings
from app.core.paths import TEMPLATES_DIR
from app.saas import licensing, oauth
from app.saas.auth import AuthError, AuthService
from app.saas.billing import BillingService
from app.saas.deps import SESSION_COOKIE
from app.saas.email import send_email
from app.saas.mailbox import MailboxError, MailboxService
from app.saas.models_db import ROLE_ADMIN
from app.saas.provider_factory import BrokenConnectionError, build_provider
from app.saas.rbac import role_at_least
from app.saas.repository import (
    AuditRepository,
    MailboxRepository,
    OrganizationRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
    UserRepository,
)
from app.saas.sync_service import InboxSyncService, ProcessingError

from .session import (
    clear_session_cookie,
    issue_csrf_token,
    set_session_cookie,
    verify_csrf,
)

logger = logging.getLogger(__name__)

web_router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_auth = AuthService()
_billing = BillingService()
_mailbox_service = MailboxService()
_sync = InboxSyncService()
_orgs = OrganizationRepository()
_users = UserRepository()
_mailboxes = MailboxRepository()
_messages = ProcessedMessageRepository()
_actions = ProposedActionRepository()
_audit = AuditRepository()

# How the demo workspace is seeded, and what the login page offers to a visitor.
DEMO_ORG_NAME = "Northwind Industries"
DEMO_OWNER_EMAIL = demo_account_email()
DEMO_OWNER_PASSWORD = "demo1234"
DEMO_OWNER_NAME = "Alex Chen"

_ACTION_LABELS = {
    "reply": "Send a reply",
    "escalate": "Escalate to a specialist",
    "classify": "Label the message",
    "defer": "Defer for later",
}
_STATUS_LABELS = {
    "proposed": "awaiting approval",
    "approved": "approved",
    "executed": "done",
    "rejected": "rejected",
    "failed": "failed",
}
_ROLE_LABELS = {
    "internal": "Colleague",
    "client": "Client",
    "vendor": "Vendor / automated",
    "unknown": "External",
}
_PROVIDER_LABELS = {
    DEMO_PROVIDER_KEY: "Demo mailbox",
    "google": "Gmail",
    "microsoft": "Microsoft 365",
}
_FEATURE_LABELS = {
    licensing.FEATURE_APPROVALS: "Human-in-the-loop approvals",
    licensing.FEATURE_ANALYTICS: "Analytics & reporting",
    licensing.FEATURE_AUDIT_LOG: "Audit log",
    licensing.FEATURE_SSO: "SSO (SAML/OIDC)",
    licensing.FEATURE_PRIORITY_SUPPORT: "Priority support & SLA",
    licensing.FEATURE_CUSTOM_MODELS: "Bring-your-own / custom models",
}


# --------------------------------------------------------------------------- #
# Template helpers
# --------------------------------------------------------------------------- #
def _short_time(value: Any) -> str:
    """Render an ISO timestamp as something a human reads at a glance."""
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if moment.year == now.year:
        return moment.strftime("%d %b, %H:%M")
    return moment.strftime("%d %b %Y")


def _audit_detail(value: Any) -> str:
    """Flatten an audit detail blob into one readable line."""
    if not value:
        return ""
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return value
    if isinstance(data, dict):
        return " · ".join(f"{k}={v}" for k, v in data.items())
    return str(data)


templates.env.filters["short_time"] = _short_time
templates.env.filters["audit_detail"] = _audit_detail


def _current_user(request: Request) -> dict | None:
    """Resolve the signed-in user from the session cookie, or None.

    Never raises: an expired or tampered cookie makes a visitor anonymous
    rather than producing an error page they cannot act on.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return _auth.resolve(token)
    except AuthError:
        return None


def _render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    base: dict[str, Any] = {
        "csrf_token": issue_csrf_token(request),
        "sales_email": get_settings().sales_contact_email,
        "current_user": _current_user(request),
        "feature_labels": _FEATURE_LABELS,
        "provider_labels": _PROVIDER_LABELS,
        "action_labels": _ACTION_LABELS,
        "status_labels": _STATUS_LABELS,
        "role_labels": _ROLE_LABELS,
    }
    base.update(context or {})
    return templates.TemplateResponse(request, template, base, status_code=status_code)


def _require_user(request: Request) -> dict:
    """The signed-in user, or a redirect back to login carrying the target."""
    user = _current_user(request)
    if user is None:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        raise _LoginRedirect(target)
    return user


class _LoginRedirect(Exception):
    """Raised when an anonymous visitor reaches a page behind the session."""

    def __init__(self, next_url: str) -> None:
        super().__init__(next_url)
        self.next_url = next_url


def _safe_next(raw: str | None) -> str:
    """Only ever redirect within this site.

    An attacker-supplied ``?next=https://evil.example`` would otherwise turn our
    login page into a credible open redirect for phishing.

    Parsed rather than prefix-matched, because the interesting bypasses are all
    about what a *browser* considers the authority, not what ``startswith``
    does: ``/\\evil.example`` is normalized to ``//evil.example`` by every major
    browser, and control characters are how header injection is attempted.
    Starlette happens to percent-encode both when it builds the ``Location``
    header, so neither is exploitable today — this does not depend on that.
    """
    default = "/app/inbox"
    if not raw:
        return default
    # Control characters are never legitimate here and are the raw material for
    # header injection.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return default
    # Browsers treat a backslash as a path separator when parsing the authority.
    if "\\" in raw:
        return default
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return default
    if not raw.startswith("/") or raw.startswith("//"):
        return default
    return raw


def _app_context(request: Request, user: dict, active: str) -> dict[str, Any]:
    """Shared context for every signed-in page: org, role, pending badge."""
    org = _orgs.get(user["org_id"]) or {"name": "Your workspace", "slug": ""}
    pending = _actions.list_for_org(user["org_id"], status="proposed", limit=100)
    return {
        "organization": org,
        "active": active,
        "pending_count": pending.get("total", 0),
        "can_manage": role_at_least(user["role"], ROLE_ADMIN),
        "connections": _mailboxes.list_for_org(user["org_id"]),
    }


# --------------------------------------------------------------------------- #
# Public pages
# --------------------------------------------------------------------------- #
@web_router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    # HEAD as well as GET: load balancers, uptime probes, and link unfurlers all
    # HEAD the root, and a 405 there reads as an outage.
    return _render(request, "landing.html")


@web_router.get("/welcome", include_in_schema=False)
def welcome_redirect() -> RedirectResponse:
    """The landing page used to live here; keep shared links working."""
    return RedirectResponse(url="/", status_code=301)


@web_router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request) -> HTMLResponse:
    order = ["trial", "team", "business", "enterprise"]
    plans = [licensing.PLANS[key] for key in order if key in licensing.PLANS]
    return _render(request, "pricing.html", {"plans": plans})


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def _demo_credentials() -> dict | None:
    """Credentials to advertise on the login page — never in production.

    Two conditions, and the order matters. ``ENVIRONMENT=production`` blocks this
    outright: if someone seeds the demo workspace on a public deployment, this
    would otherwise print a working password on an unauthenticated page. Only
    then do we check the account exists, so a non-production instance without the
    demo shows no hint for an account nobody can use.
    """
    if get_settings().is_production:
        return None
    if not _users.get_by_email_global(DEMO_OWNER_EMAIL):
        return None
    return {"email": DEMO_OWNER_EMAIL, "password": DEMO_OWNER_PASSWORD}


# Known post-redirect notices, keyed rather than free-text so the query string
# can never be used to render attacker-chosen copy on the login page.
_LOGIN_NOTICES = {
    "reset": "Password updated. Sign in with your new password.",
}


@web_router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, next: str | None = None, notice: str | None = None
) -> HTMLResponse:
    if _current_user(request):
        return RedirectResponse(url=_safe_next(next), status_code=303)  # type: ignore[return-value]
    return _render(
        request,
        "login.html",
        {
            "next_url": _safe_next(next),
            "sso_enabled": get_settings().sso_enabled,
            "demo_credentials": _demo_credentials(),
            "notice": _LOGIN_NOTICES.get(notice or ""),
        },
    )


@web_router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/app/inbox"),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    try:
        user = _auth.authenticate(email=email, password=password)
    except AuthError as exc:
        # The web surface must audit failures like the API does — the Activity
        # page claims every sign-in attempt lands there.
        account = _users.get_by_email_global(email)
        _audit.record(
            action="auth.login_failed",
            org_id=account["org_id"] if account else None,
            detail={"email": email, "surface": "web"},
        )
        return _render(
            request,
            "login.html",
            {
                "error": exc.message,
                "email": email,
                "next_url": _safe_next(next),
                "sso_enabled": get_settings().sso_enabled,
                "demo_credentials": _demo_credentials(),
            },
            status_code=exc.status_code,
        )

    token, _ttl = _auth.issue_token(user)
    _audit.record(
        action="auth.login",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        detail={"email": user["email"], "surface": "web"},
    )
    response = RedirectResponse(url=_safe_next(next), status_code=303)
    set_session_cookie(response, token)
    return response


@web_router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request) -> HTMLResponse:
    if _current_user(request):
        return RedirectResponse(url="/app/inbox", status_code=303)  # type: ignore[return-value]
    return _render(
        request,
        "signup.html",
        {"form": {}, "signup_enabled": get_settings().signup_enabled},
    )


@web_router.post("/signup")
def signup_submit(
    request: Request,
    org_name: str = Form(""),
    full_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    form = {"org_name": org_name, "full_name": full_name, "email": email}

    if len(password) < 8:
        return _render(
            request,
            "signup.html",
            {
                "error": "Choose a password of at least 8 characters.",
                "form": form,
                "signup_enabled": get_settings().signup_enabled,
            },
            status_code=400,
        )

    try:
        user, org, _terms = _auth.signup(
            email=email, password=password, full_name=full_name, org_name=org_name
        )
    except AuthError as exc:
        return _render(
            request,
            "signup.html",
            {
                "error": exc.message,
                "form": form,
                "signup_enabled": get_settings().signup_enabled,
            },
            status_code=exc.status_code,
        )

    token, _ttl = _auth.issue_token(user)
    _audit.record(
        action="auth.signup",
        org_id=org["id"],
        actor_user_id=user["id"],
        detail={"email": user["email"], "surface": "web"},
    )
    # Straight to connect: a workspace with no mailbox has nothing to show.
    response = RedirectResponse(url="/app/connect", status_code=303)
    set_session_cookie(response, token)
    return response


@web_router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
@web_router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_form(request: Request) -> HTMLResponse:
    return _render(request, "forgot_password.html")


@web_router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    csrf_token: str = Form(""),
) -> HTMLResponse:
    verify_csrf(request, csrf_token)
    token = _auth.request_password_reset(email)
    if token:
        settings = get_settings()
        link = f"{settings.resolved_app_public_url}/reset-password?token={quote(token)}"
        send_email(
            email,
            "Reset your Executive Email Copilot password",
            "We received a request to reset your password. Use the link below "
            f"(valid for {settings.password_reset_ttl_minutes} minutes):\n\n{link}\n\n"
            "If you didn't request this, you can safely ignore this email.",
        )
        account = _users.get_by_email_global(email)
        _audit.record(
            action="auth.password_reset_requested",
            org_id=account["org_id"] if account else None,
            detail={"email": email, "surface": "web"},
        )
    # The confirmation is identical whether or not the account exists, so this
    # form cannot be used to probe which emails are registered.
    return _render(request, "forgot_password.html", {"sent": True, "email": email})


@web_router.get("/reset-password", response_class=HTMLResponse)
def reset_password_form(request: Request, token: str | None = None) -> Response:
    if not token:
        return RedirectResponse(url="/forgot-password", status_code=303)
    return _render(request, "reset_password.html", {"token": token})


@web_router.post("/reset-password")
def reset_password_submit(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    if len(password) < 8:
        return _render(
            request,
            "reset_password.html",
            {"token": token, "error": "Choose a password of at least 8 characters."},
            status_code=400,
        )
    try:
        user = _auth.reset_password(token, password)
    except AuthError as exc:
        return _render(
            request,
            "reset_password.html",
            {"token": token, "error": exc.message},
            status_code=exc.status_code,
        )
    _audit.record(
        action="auth.password_reset",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        detail={"surface": "web"},
    )
    return RedirectResponse(url="/login?notice=reset", status_code=303)


# --------------------------------------------------------------------------- #
# Mailbox connection
# --------------------------------------------------------------------------- #
@web_router.get("/app/connect", response_class=HTMLResponse)
def connect_page(request: Request, notice: str | None = None) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "connect")
    context.update(
        {
            "providers": [
                {"key": p["key"], "name": p["name"], "configured": p["available"]}
                for p in oauth.available_providers()
            ],
            "demo_message_count": demo_message_count(),
            "notice": notice,
        }
    )
    return _render(request, "connect.html", context)


@web_router.post("/app/connect/demo")
def connect_demo(request: Request, csrf_token: str = Form("")) -> Response:
    """Attach the demo mailbox and immediately triage it.

    Deliberately the same code path a real mailbox takes — a connection row, a
    provider, then :meth:`InboxSyncService.sync`. The only difference is which
    provider the factory builds.
    """
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)

    connection = _mailboxes.upsert_connection(
        org_id=user["org_id"],
        provider=DEMO_PROVIDER_KEY,
        account_email=DEMO_OWNER_EMAIL,
        connected_by=user["id"],
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    _audit.record(
        action="mailbox.connect",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        target=connection["id"],
        detail={"provider": DEMO_PROVIDER_KEY, "account_email": DEMO_OWNER_EMAIL},
    )
    _sync_connection(user, connection)
    return RedirectResponse(url="/app/inbox", status_code=303)


@web_router.post("/app/connect/{provider_key}")
def connect_provider(request: Request, provider_key: str, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    try:
        authorize_url = _mailbox_service.start_connect(
            org_id=user["org_id"],
            user_id=user["id"],
            provider_key=provider_key,
            request_base_url=str(request.base_url).rstrip("/"),
        )
    except MailboxError as exc:
        context = _app_context(request, user, "connect")
        context.update(
            {
                "providers": [
                    {"key": p["key"], "name": p["name"], "configured": p["available"]}
                    for p in oauth.available_providers()
                ],
                "demo_message_count": demo_message_count(),
                "error": exc.message,
            }
        )
        return _render(request, "connect.html", context, status_code=exc.status_code)
    return RedirectResponse(url=authorize_url, status_code=303)


@web_router.post("/app/mailboxes/{connection_id}/disconnect")
def disconnect_mailbox(
    request: Request, connection_id: str, csrf_token: str = Form("")
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    _mailbox_service.disconnect(
        org_id=user["org_id"], user_id=user["id"], connection_id=connection_id
    )
    return RedirectResponse(url="/app/connect", status_code=303)


@web_router.post("/app/mailboxes/{connection_id}/sync")
def sync_one(request: Request, connection_id: str, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    connection = _mailboxes.get(user["org_id"], connection_id)
    if connection:
        _sync_connection(user, connection)
    return RedirectResponse(url="/app/inbox", status_code=303)


@web_router.post("/app/sync")
def sync_all(request: Request, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    for connection in _mailboxes.list_for_org(user["org_id"]):
        _sync_connection(user, connection)
    return RedirectResponse(url="/app/inbox", status_code=303)


def _sync_connection(user: dict, connection: dict) -> None:
    """Run one sync, surfacing failures as a page error rather than a 500."""
    try:
        _sync.sync(
            org_id=user["org_id"],
            user_id=user["id"],
            connection_id=connection["id"],
            provider=build_provider(connection),
        )
    except BrokenConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _require_manage(user: dict) -> None:
    if not role_at_least(user["role"], ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="This action requires the admin role or higher")


# --------------------------------------------------------------------------- #
# The product
# --------------------------------------------------------------------------- #
@web_router.get("/app", include_in_schema=False)
def app_root() -> RedirectResponse:
    return RedirectResponse(url="/app/inbox", status_code=307)


@web_router.get("/app/inbox", response_class=HTMLResponse)
def inbox(request: Request, message: str | None = None) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "inbox")

    listing = _messages.list_for_org(user["org_id"], limit=100)
    messages = listing.get("messages", [])
    context["messages"] = messages
    context["message_total"] = listing.get("total", len(messages))
    context["summary"] = _actions.summarize_for_org(user["org_id"])

    selected = None
    if messages:
        selected = next((m for m in messages if m["id"] == message), messages[0])
    context["selected"] = selected

    if selected:
        actions = _actions.list_for_org(user["org_id"], limit=500).get("actions", [])
        selected_actions = [
            a
            for a in actions
            if a["message_id"] == selected["id"] and a["action_type"] != "classify"
        ]
        context["selected_actions"] = selected_actions
        # A stored rationale is the model's own reasoning about *this* decision;
        # prefer it over reasoning reconstructed from the signals after the fact.
        stored = next((a["rationale"] for a in selected_actions if a.get("rationale")), None)
        context["selected_rationale"] = stored or _rationale_for(user["org_id"], selected)
    else:
        context["selected_actions"] = []
        context["selected_rationale"] = []

    return _render(request, "inbox.html", context)


def _rationale_for(org_id: str, message: dict) -> list[str]:
    """The copilot's reasoning for a message.

    A provider may supply written reasoning (the demo mailbox does). When it
    does not — a real Gmail account — describe the inferred signals that
    actually drove the decision, so the panel is never empty and never invents
    a justification it does not have.
    """
    connection = _mailboxes.get(org_id, message["connection_id"])
    if connection:
        provider = build_provider(connection)
        narrator = getattr(provider, "rationale_for", None)
        if callable(narrator):
            authored = narrator(message["provider_message_id"])
            if authored:
                return list(authored)

    points = [
        f"Sender looks {_ROLE_LABELS.get(message.get('sender_role'), 'external').lower()}; "
        f"business value scored {message.get('business_value') or 0:.2f}.",
        f"Priority inferred as {message.get('priority_hint') or 'unknown'}, "
        f"target response within {message.get('deadline_minutes') or '—'} minutes.",
    ]
    risk = message.get("risk_tag")
    if risk and risk != "none":
        points.append(f"Risk vocabulary matched '{risk}', which drives where this is routed.")
    return points


@web_router.post("/app/actions/{action_id}/approve")
def approve_action(
    request: Request,
    action_id: str,
    csrf_token: str = Form(""),
    message: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)

    provider = _provider_for_action(user["org_id"], action_id)
    try:
        _sync.approve(
            org_id=user["org_id"], user_id=user["id"], action_id=action_id, provider=provider
        )
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return RedirectResponse(url=_back_to(request, message), status_code=303)


@web_router.post("/app/actions/{action_id}/reject")
def reject_action(
    request: Request,
    action_id: str,
    csrf_token: str = Form(""),
    message: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    try:
        _sync.reject(org_id=user["org_id"], user_id=user["id"], action_id=action_id, comment=None)
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return RedirectResponse(url=_back_to(request, message), status_code=303)


def _back_to(request: Request, message_id: str) -> str:
    """Return the reviewer to where they were, not to a generic page."""
    referer = request.headers.get("referer", "")
    if "/app/approvals" in referer:
        return "/app/approvals"
    return f"/app/inbox?message={message_id}" if message_id else "/app/inbox"


def _provider_for_action(org_id: str, action_id: str):
    action = _actions.get(org_id, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    message = _messages.get(org_id, action["message_id"])
    if not message:
        raise HTTPException(status_code=404, detail="Message for this action no longer exists")
    connection = _mailboxes.get(org_id, message["connection_id"])
    if not connection:
        raise HTTPException(status_code=404, detail="Mailbox connection no longer exists")
    try:
        return build_provider(connection)
    except BrokenConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@web_router.get("/app/approvals", response_class=HTMLResponse)
def approvals(request: Request) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "approvals")

    pending = _actions.list_for_org(user["org_id"], status="proposed", limit=100).get("actions", [])
    items = []
    for action in pending:
        message = _messages.get(user["org_id"], action["message_id"])
        if message:
            items.append(
                {
                    "action": action,
                    "message": message,
                    # Approving is a decision; showing the reasoning next to the
                    # button is what makes it an informed one.
                    "rationale": action.get("rationale") or _rationale_for(user["org_id"], message),
                }
            )
    context["actions"] = items
    return _render(request, "approvals.html", context)


@web_router.get("/app/activity", response_class=HTMLResponse)
def activity(request: Request) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "activity")
    can_view = role_at_least(user["role"], ROLE_ADMIN)
    context["can_view"] = can_view
    context["entries"] = _audit.list_for_org(user["org_id"], limit=100) if can_view else []
    context["actor_names"] = {
        member["id"]: member.get("full_name") or member["email"]
        for member in _users.list_for_org(user["org_id"])
    }
    return _render(request, "activity.html", context)


@web_router.get("/app/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "settings")

    raw = _billing.current_entitlement(user["org_id"])
    members = _users.list_for_org(user["org_id"])
    context["entitlement"] = {
        "plan": raw.get("plan"),
        "seats": raw.get("seats"),
        "features": raw.get("features", []),
        "expires_at": raw.get("expires_at"),
        "active": bool(raw.get("is_valid")),
    }
    context["members"] = members
    context["member_count"] = len(members)
    return _render(request, "settings.html", context)


# --------------------------------------------------------------------------- #
# Anonymous visitors get the login page, not an error
# --------------------------------------------------------------------------- #
async def login_redirect_handler(request: Request, exc: Exception) -> Response:
    next_url = getattr(exc, "next_url", "/app/inbox")
    return RedirectResponse(url=f"/login?next={quote(next_url, safe='/?=&')}", status_code=303)


# --------------------------------------------------------------------------- #
# Browser pages get an error *page*, not raw JSON
# --------------------------------------------------------------------------- #
# Paths served by this router. Everything else (the JSON API, the benchmark
# surface) keeps FastAPI's default JSON error contract.
_WEB_PATH_PREFIXES = (
    "/app",
    "/login",
    "/logout",
    "/signup",
    "/forgot-password",
    "/reset-password",
    "/pricing",
    "/welcome",
)


def is_web_path(path: str) -> bool:
    return path == "/" or any(
        path == prefix or path.startswith(prefix + "/") for prefix in _WEB_PATH_PREFIXES
    )


async def web_http_error_handler(request: Request, exc: Exception) -> Response:
    """Render HTTPExceptions on browser pages as a page the user can act on.

    A double-clicked Approve, a disconnect race, or a stale form otherwise
    dumps ``{"detail": ...}`` in the browser with no way back. API routes are
    deliberately untouched — machine callers want the JSON.
    """
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    if not isinstance(exc, StarletteHTTPException) or not is_web_path(request.url.path):
        return await http_exception_handler(request, exc)  # type: ignore[arg-type]

    detail = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
    return _render(
        request,
        "error.html",
        {"status_code": exc.status_code, "message": detail},
        status_code=exc.status_code,
    )
