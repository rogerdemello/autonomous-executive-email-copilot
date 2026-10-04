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
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.copilot.providers.demo import DEMO_PROVIDER_KEY, demo_message_count
from app.core.config import get_settings
from app.core.models import ESCALATION_ROLES
from app.core.paths import DATA_ROOT, TEMPLATES_DIR
from app.core.security import (
    lead_submission_allowed,
    login_attempt_allowed,
    sandbox_creation_allowed,
)
from app.saas import licensing, oauth, rbac
from app.saas import sandbox as sandboxes
from app.saas.auth import AuthError, AuthService
from app.saas.billing import BillingError, BillingService
from app.saas.deps import SESSION_COOKIE, reject_shared_demo_account
from app.saas.email import send_email
from app.saas.mailbox import MailboxError, MailboxService
from app.saas.marketing import REPO_URL, SECURITY_ADVISORY_URL, public_base_url
from app.saas.models_db import ROLE_ADMIN, ROLE_OWNER, ROLES
from app.saas.org_service import OrgError, OrgService
from app.saas.provider_factory import BrokenConnectionError, build_provider
from app.saas.rbac import role_at_least
from app.saas.repository import (
    AuditRepository,
    CommitmentRepository,
    EscalationContactRepository,
    MailboxRepository,
    OrganizationRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
    UserRepository,
)
from app.saas.sync_service import InboxSyncService, ProcessingError

from .assets import register as register_assets
from .session import (
    clear_session_cookie,
    issue_csrf_token,
    set_session_cookie,
    verify_csrf,
)

logger = logging.getLogger(__name__)

web_router = APIRouter(include_in_schema=False)
templates = register_assets(Jinja2Templates(directory=str(TEMPLATES_DIR)))

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
_commitments = CommitmentRepository()

# The demo workspace's identity lives with the seeder; re-exported here because
# tests and scripts historically import these names from this module.
from app.saas.demo_seed import (  # noqa: E402
    DEMO_OWNER_EMAIL,
    DEMO_OWNER_PASSWORD,
)

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
# Only the two feature flags that are actually *enforced* have labels, because
# a label is what makes one renderable. The other four (approvals, analytics,
# priority support, custom models) used to render as chips in Settings while
# gating nothing whatsoever — a decorative claim about what the customer had
# bought. If a flag starts gating something, give it a label then.
_FEATURE_LABELS = {
    licensing.FEATURE_AUDIT_LOG: "Audit log",  # enforced: routes.activity
    licensing.FEATURE_SSO: "SSO (SAML/OIDC)",  # enforced: saas.routes SSO login
}


# --------------------------------------------------------------------------- #
# Template helpers
# --------------------------------------------------------------------------- #
def _short_time(value: Any) -> str:
    """Render an ISO timestamp as something a human reads at a glance."""
    if not value:
        return "-"
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


def _client_ip(request: Request) -> str | None:
    """The caller's address, for the audit log.

    Every audit call on this router omitted this while the JSON API passed it,
    so ``saas_audit_log.ip`` was NULL for the surface people actually use —
    the one column an incident review reaches for first. Behind a platform
    proxy uvicorn is started with ``--proxy-headers``, so ``request.client``
    is already the real client rather than the proxy.
    """
    return request.client.host if request.client else None


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
    settings = get_settings()
    base: dict[str, Any] = {
        "csrf_token": issue_csrf_token(request),
        # None unless a real address is configured: pages fall back to the
        # contact form rather than publishing `sales@example.com`.
        "sales_email": settings.public_sales_email,
        "security_email": settings.public_security_email,
        "security_advisory_url": SECURITY_ADVISORY_URL,
        "repo_url": REPO_URL,
        # For <link rel="canonical"> and the Open Graph tags: absolute, and the
        # origin actually serving this request, so a pasted link unfurls with a
        # working image and a canonical nobody has to correct.
        "public_base_url": public_base_url(request),
        "page_url": f"{public_base_url(request)}{request.url.path}",
        # Whether "Try the live demo" is offered at all. When it is not, the
        # landing page links to sign-up instead of to a page that 404s.
        "demo_enabled": settings.demo_login_active,
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
    """Shared context for every signed-in page: org, role, pending badge, and
    the two numbers that say what the copilot is for."""
    org = _orgs.get(user["org_id"]) or {"name": "Your workspace", "slug": ""}
    pending = _actions.list_for_org(user["org_id"], status="proposed", limit=100)
    connections = _mailboxes.list_for_org(user["org_id"])
    sandbox_hours = sandboxes.hours_left(org)
    return {
        "organization": org,
        "active": active,
        # Set only inside a visitor's demo sandbox: drives the banner that says
        # what this is, that nothing leaves it, and when it is deleted.
        "sandbox": {"hours_left": sandbox_hours} if sandbox_hours is not None else None,
        "pending_count": pending.get("total", 0),
        "can_manage": role_at_least(user["role"], ROLE_ADMIN),
        "connections": connections,
        # A mailbox that can no longer authenticate was visible only as a chip
        # on /app/connect — a page nobody revisits after setup. Everywhere
        # else, the symptom was an inbox that quietly stopped filling, which
        # reads as a quiet week rather than as a broken product. It is now a
        # banner on every signed-in page until somebody reconnects it.
        "mailbox_broken": any(c.get("status") == "error" for c in connections),
        # "142 drafts verified · 9 claims caught" is the claim no competitor
        # can make, and it lived in the database being rendered as one chip on
        # one page. Two grouped queries, on every signed-in page.
        "verification": _actions.verification_summary(user["org_id"]),
        # The overdue count drives the "Waiting on" badge in the sidebar: a
        # follow-up tracker you have to remember to open is not a tracker.
        "waiting": _commitments.summarize_for_org(
            user["org_id"], datetime.now(timezone.utc).date().isoformat()
        ),
    }


# --------------------------------------------------------------------------- #
# Public pages
# --------------------------------------------------------------------------- #
_LANDING_METRICS_PATH = DATA_ROOT / "landing_metrics.json"
_landing_metrics_cache: dict[str, Any] | None = None


def landing_metrics() -> dict[str, Any]:
    """The benchmark artifact the proof section renders.

    Loaded once and cached: it is a small file that only changes when
    ``scripts/build_landing_metrics.py`` runs.

    A missing artifact is a **hard failure**, not a fallback to hardcoded
    numbers. The section it feeds is headed "Measured, not guessed"; silently
    degrading to invented values is the precise failure mode that heading
    exists to rule out, and it would be invisible in production.
    """
    global _landing_metrics_cache
    if _landing_metrics_cache is None:
        try:
            with open(_LANDING_METRICS_PATH, encoding="utf-8") as handle:
                _landing_metrics_cache = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Landing benchmark artifact missing or unreadable at "
                f"{_LANDING_METRICS_PATH}. Generate it with: "
                f"python scripts/build_landing_metrics.py"
            ) from exc
    return _landing_metrics_cache


@web_router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    # HEAD as well as GET: load balancers, uptime probes, and link unfurlers all
    # HEAD the root, and a 405 there reads as an outage.
    return _render(request, "landing.html", {"bench": landing_metrics()})


@web_router.get("/welcome", include_in_schema=False)
def welcome_redirect() -> RedirectResponse:
    """The landing page used to live here; keep shared links working."""
    return RedirectResponse(url="/", status_code=301)


@web_router.get("/pricing", include_in_schema=False)
def pricing_redirect() -> RedirectResponse:
    """There is no public pricing page. Old links land on the homepage.

    Kept as a redirect rather than deleted: the page existed once, links to it
    are in the wild, and a 301 costs one route while a 404 costs a visitor.
    """
    return RedirectResponse(url="/", status_code=301)


@web_router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request) -> HTMLResponse:
    """The privacy policy.

    On the critical path for Gmail: Google will not begin OAuth verification
    for the restricted ``gmail.*`` scopes without a published privacy policy on
    the app's own domain, and the policy must carry the Limited Use
    disclosure. The scope table it renders is kept in sync with
    :mod:`app.saas.oauth` by hand — a mismatch there is a rejection reason.
    """
    return _render(request, "privacy.html")


@web_router.get("/terms", response_class=HTMLResponse)
def terms(request: Request) -> HTMLResponse:
    return _render(request, "terms.html")


@web_router.get("/contact-sales", response_class=HTMLResponse)
def contact_sales_form(request: Request) -> HTMLResponse:
    return _render(request, "contact_sales.html", {"form": {}})


@web_router.post("/contact-sales")
def contact_sales_submit(
    request: Request,
    email: str = Form(""),
    name: str = Form(""),
    company: str = Form(""),
    seats: str = Form(""),
    message: str = Form(""),
    website: str = Form(""),  # honeypot — humans never see or fill it
    csrf_token: str = Form(""),
) -> HTMLResponse:
    verify_csrf(request, csrf_token)
    form = {"email": email, "name": name, "company": company, "seats": seats, "message": message}

    if website.strip():
        # A filled honeypot is a bot. Pretend success so it learns nothing;
        # persist nothing.
        return _render(request, "contact_sales.html", {"form": form, "submitted": True})

    client_ip = request.client.host if request.client else "unknown"
    if not lead_submission_allowed(client_ip):
        return _render(
            request,
            "contact_sales.html",
            {"form": form, "error": "Too many submissions. Wait a minute and try again."},
            status_code=429,
        )

    email = email.strip()
    if "@" not in email or len(email) > 320:
        return _render(
            request,
            "contact_sales.html",
            {"form": form, "error": "Enter a valid work email so we can reply."},
            status_code=400,
        )

    seats_value: int | None = None
    if seats.strip():
        try:
            seats_value = max(1, min(int(seats), 100000))
        except ValueError:
            seats_value = None

    BillingService().capture_lead(
        email=email,
        kind="contact_sales",
        name=name.strip() or None,
        company=company.strip() or None,
        seats=seats_value,
        message=message.strip()[:4000] or None,
    )
    return _render(request, "contact_sales.html", {"form": form, "submitted": True})


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def _demo_credentials() -> dict | None:
    """Credentials to advertise on the login page.

    Gated on ``demo_login_active``: off in production unless the operator sets
    ``DEMO_LOGIN_ENABLED=true`` explicitly — the posture for a public demo
    deployment that sells with the prefilled login. Advertising the credential
    is safe there because it is already public (repo, README, docs/DEMO.md),
    the workspace holds only fixture data and no OAuth tokens, and the shared
    account is blocked from every destructive/administrative action (see
    ``reject_shared_demo_account``). The remaining risk is vandalism of the
    demo itself, which ``POST /operator/demo/reseed`` undoes in seconds.

    The account-exists check comes second so a deployment without the demo
    shows no hint for an account nobody can use.
    """
    if not get_settings().demo_login_active:
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
    visitor = _current_user(request)
    # Someone inside a demo sandbox who navigates to Sign in wants a *different*
    # account, not to be bounced back into the one they are leaving.
    if visitor and not sandboxes.is_sandbox_user(visitor):
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
    client_ip = request.client.host if request.client else "unknown"
    if not login_attempt_allowed(client_ip, email, get_settings().login_rate_limit_per_minute):
        return _render(
            request,
            "login.html",
            {
                "error": "Too many sign-in attempts. Wait a minute and try again.",
                "email": email,
                "next_url": _safe_next(next),
                "sso_enabled": get_settings().sso_enabled,
                "demo_credentials": _demo_credentials(),
            },
            status_code=429,
        )
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
            ip=_client_ip(request),
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
        ip=_client_ip(request),
    )
    response = RedirectResponse(url=_safe_next(next), status_code=303)
    set_session_cookie(response, token)
    return response


@web_router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request) -> HTMLResponse:
    visitor = _current_user(request)
    # "Start your own workspace" is the sandbox banner's call to action; it must
    # not bounce a sandbox visitor straight back into the sandbox.
    if visitor and not sandboxes.is_sandbox_user(visitor):
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
        ip=_client_ip(request),
    )
    # Straight to connect: a workspace with no mailbox has nothing to show.
    response = RedirectResponse(url="/app/connect", status_code=303)
    set_session_cookie(response, token)
    return response


@web_router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    visitor = _current_user(request)
    if visitor and sandboxes.is_sandbox_user(visitor):
        # Leaving a demo sandbox is the cleanest moment to delete it: the
        # password is unknown, so once the cookie is gone nothing can reach it.
        from app.saas.data_lifecycle import DataLifecycleService

        DataLifecycleService().delete_org(visitor["org_id"])
    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response


# --------------------------------------------------------------------------- #
# The public demo: one click, no credentials
# --------------------------------------------------------------------------- #
def _demo_page(request: Request, *, error: str | None = None, status_code: int = 200):
    return _render(
        request,
        "demo.html",
        {"demo_message_count": demo_message_count(), "error": error},
        status_code=status_code,
    )


@web_router.get("/demo", response_class=HTMLResponse)
def demo_page(request: Request) -> HTMLResponse:
    """What the demo is, with one button.

    A page rather than the action itself: link unfurlers, prefetchers and crawlers
    all issue GETs, and a GET that built a workspace would build one per bot. The
    landing page's "Try the live demo" is a form that POSTs straight to
    :func:`open_demo`, so from there it is still one click.
    """
    if not get_settings().demo_login_active:
        raise HTTPException(status_code=404, detail="Not found")
    return _demo_page(request)


@web_router.post("/demo")
def open_demo(request: Request, csrf_token: str = Form("")) -> Response:
    """Build the visitor a private demo workspace and sign them straight in.

    See :mod:`app.saas.sandbox` for why it is built per visitor, and for what
    bounds it. Nothing here is credential-based: the visitor is handed a session
    for an account whose password nobody knows.
    """
    verify_csrf(request, csrf_token)
    if not get_settings().demo_login_active:
        raise HTTPException(status_code=404, detail="Not found")

    current = _current_user(request)
    if current is not None:
        # Already signed in — to a sandbox of this demo, or as a real customer.
        # The answer is "take them there", never "replace their session": a
        # customer who clicked the landing page's demo button must not be signed
        # out of their own workspace, and a double click must not build two.
        in_sandbox = sandboxes.is_sandbox_user(current)
        return RedirectResponse(
            url="/app/inbox?tour=1" if in_sandbox else "/app/inbox", status_code=303
        )

    client_ip = request.client.host if request.client else "unknown"
    if not sandbox_creation_allowed(client_ip):
        return _demo_page(
            request,
            error="You have opened the demo several times in a row. Give it a few minutes and try again.",
            status_code=429,
        )
    try:
        created = sandboxes.create_sandbox(ip=_client_ip(request))
    except sandboxes.SandboxUnavailable as exc:
        return _demo_page(request, error=str(exc), status_code=503)
    except Exception:  # noqa: BLE001 - say so on the page rather than a JSON 500
        logger.exception("Opening a demo sandbox failed")
        return _demo_page(
            request, error="We couldn't open the demo just now. Please try again.", status_code=500
        )

    token, _ttl = _auth.issue_token(created["user"])
    response = RedirectResponse(url="/app/inbox?tour=1", status_code=303)
    set_session_cookie(response, token)
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
            ip=_client_ip(request),
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
        ip=_client_ip(request),
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
        ip=_client_ip(request),
    )
    _sync_connection(user, connection)
    return RedirectResponse(url="/app/inbox", status_code=303)


@web_router.post("/app/connect/{provider_key}")
def connect_provider(request: Request, provider_key: str, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    # The shared demo login must not attach a real mailbox to the shared org.
    reject_shared_demo_account(user)
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
    reject_shared_demo_account(user)
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


# One screen of messages. Small enough that the list stays scannable, large
# enough that a normal morning fits on one page.
INBOX_PAGE_SIZE = 50

# The classifier's own vocabulary, offered as filters. Anything else in the
# query string is ignored rather than passed to the database.
_INBOX_LABELS = ("urgent", "normal", "spam")
_INBOX_PRIORITIES = ("high", "medium", "low")


@web_router.get("/app/inbox", response_class=HTMLResponse)
def inbox(
    request: Request,
    message: str | None = None,
    notice: str | None = None,
    q: str | None = None,
    label: str | None = None,
    priority: str | None = None,
    page: int = 1,
    tour: str | None = None,
) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "inbox")
    context["notice"] = _REVIEW_NOTICES.get(notice or "")
    # The guided path is part of the demo sandbox, never of a real workspace —
    # and `?tour=1` on a real account is simply ignored.
    context["tour"] = bool(tour) and context["sandbox"] is not None
    if context["tour"]:
        # The tour points at the drafts the verifier flagged — how many, and the
        # first of them — rather than asserting a number that the fixtures could
        # change (it has: the README said "one", the demo holds two).
        waiting_for_review = _actions.list_pending_with_messages(user["org_id"], limit=100)
        flagged = [
            item["action"]["id"]
            for item in waiting_for_review["items"]
            if item["action"].get("verification_status") == "flagged"
        ]
        context["tour_flagged_id"] = flagged[0] if flagged else None
        context["tour_flagged_count"] = len(flagged)

    query = (q or "").strip()[:200]
    label = label if label in _INBOX_LABELS else None
    priority = priority if priority in _INBOX_PRIORITIES else None
    page = max(1, page)

    listing = _messages.list_for_org(
        user["org_id"],
        limit=INBOX_PAGE_SIZE,
        offset=(page - 1) * INBOX_PAGE_SIZE,
        q=query or None,
        label=label,
        priority=priority,
    )
    messages = listing.get("messages", [])
    total = listing.get("total", len(messages))

    context["messages"] = messages
    # The summary tiles describe the *workspace*, so they must not move when a
    # filter is applied: "8 triaged" while a spam filter is on is a false
    # statement about what the copilot did, and it is the first number on the
    # page. `message_total` is the filtered count and drives the list header.
    context["message_total"] = total
    context["mailbox_total"] = _messages.list_for_org(user["org_id"], limit=1)["total"]
    # A mailbox connected but never synced is a third state, and it used to
    # render as "nothing matches that filter" — telling a customer their inbox
    # is empty when the copilot simply has not finished reading it. The first
    # pull of a real mailbox takes minutes and now happens in the background,
    # so this is the normal view for the first few minutes of every account.
    context["first_sync_pending"] = not messages and any(
        not connection.get("last_synced_at") for connection in context["connections"]
    )
    context["summary"] = _actions.summarize_for_org(user["org_id"])
    context["filters"] = {"q": query, "label": label, "priority": priority}
    context["filter_labels"] = _INBOX_LABELS
    context["filter_priorities"] = _INBOX_PRIORITIES
    # Paging state the template renders directly, so it never has to recompute
    # "which page am I on" from a total and a limit.
    context["page"] = {
        "number": page,
        "size": INBOX_PAGE_SIZE,
        "total": total,
        "first": (page - 1) * INBOX_PAGE_SIZE + 1 if messages else 0,
        "last": (page - 1) * INBOX_PAGE_SIZE + len(messages),
        "has_prev": page > 1,
        "has_next": page * INBOX_PAGE_SIZE < total,
        "query": _query_string(q=query, label=label, priority=priority),
    }

    # Labels for exactly this page. Previously this listed the org's first 500
    # actions and filtered in Python, so spam chips vanished from older
    # messages on any tenant past that threshold.
    context["labels"] = _actions.labels_for_messages(user["org_id"], [m["id"] for m in messages])
    context["thread_sizes"] = _messages.thread_sizes(
        user["org_id"], [m.get("thread_id") for m in messages]
    )

    selected = None
    if message:
        # Look the requested message up directly rather than searching the
        # current page: a link from Approvals, or a bookmark, points at a
        # message that a filter or a later page may well have excluded.
        selected = _messages.get(user["org_id"], message)
    # No message means the list, not the first message pre-opened. The inbox is
    # two screens — a full-width list, and a reader you land on by choosing a
    # row — because at the laptop widths people actually have (1920x1080 at
    # 150% scale is a 1280px viewport) a list beside a reader beside the
    # copilot left the message itself about 256px wide.
    context["selected"] = selected

    # Previous/next within the page you are reading from, so the reader keeps
    # the j/k navigation the list has. Deliberately empty when the message is
    # not on this page (opened from Approvals, a bookmark, or an excluding
    # filter): stepping to a neighbour of a list you are not looking at is a
    # worse answer than offering no step at all.
    neighbours: dict[str, str | None] = {"prev": None, "next": None}
    if selected:
        page_ids = [m["id"] for m in messages]
        if selected["id"] in page_ids:
            position = page_ids.index(selected["id"])
            if position > 0:
                neighbours["prev"] = page_ids[position - 1]
            if position + 1 < len(page_ids):
                neighbours["next"] = page_ids[position + 1]
    context["neighbours"] = neighbours

    if selected:
        actions = _actions.list_for_message(user["org_id"], selected["id"])
        # The classifier's verdict (spam / normal / urgent) is shown as a label,
        # not an action block, so classify actions are filtered out below but
        # their labels are surfaced per message.
        selected_actions = [a for a in actions if a["action_type"] != "classify"]
        context["selected_actions"] = selected_actions
        context["selected_label"] = next(
            (a["label"] for a in actions if a["action_type"] == "classify" and a.get("label")),
            None,
        )
        context["thread"] = (
            _messages.list_thread(user["org_id"], selected.get("thread_id") or "")
            if selected.get("thread_id")
            else []
        )
        # A stored rationale is the model's own reasoning about *this* decision;
        # prefer it over reasoning reconstructed from the signals after the fact.
        stored = next((a["rationale"] for a in selected_actions if a.get("rationale")), None)
        context["selected_rationale"] = stored or _rationale_for(user["org_id"], selected)
    else:
        context["selected_actions"] = []
        context["selected_label"] = None
        context["thread"] = []
        context["selected_rationale"] = []

    return _render(request, "inbox.html", context)


def _query_string(**params: Any) -> str:
    """Encode the non-empty params as a query fragment (no leading '?')."""
    return urlencode({k: v for k, v in params.items() if v})


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
        f"target response within {message.get('deadline_minutes') or '-'} minutes.",
    ]
    risk = message.get("risk_tag")
    if risk and risk != "none":
        points.append(f"Risk vocabulary matched '{risk}', which drives where this is routed.")
    return points


# Post-redirect notices for review decisions, keyed rather than free-text so
# the query string can never render attacker-chosen copy (same rule as the
# settings notices below).
_REVIEW_NOTICES = {
    "sent": "Approved. The reply was sent, and the decision is on the audit log.",
    "approved": "Approved. The action was applied, and the decision is on the audit log.",
    "rejected": "Rejected. Nothing was sent. The copilot learns from the call you made.",
}


@web_router.post("/app/actions/{action_id}/approve")
def approve_action(
    request: Request,
    action_id: str,
    csrf_token: str = Form(""),
    message: str = Form(""),
    content: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)

    action = _actions.get(user["org_id"], action_id)
    provider = _provider_for_action(user["org_id"], action_id)
    try:
        _sync.approve(
            org_id=user["org_id"],
            user_id=user["id"],
            action_id=action_id,
            provider=provider,
            edited_content=content or None,
        )
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    notice = "sent" if action and action["action_type"] == "reply" else "approved"
    return RedirectResponse(url=_back_to(request, message, notice), status_code=303)


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
    return RedirectResponse(url=_back_to(request, message, "rejected"), status_code=303)


def _back_to(request: Request, message_id: str, notice: str | None = None) -> str:
    """Return the reviewer to where they were, not to a generic page."""
    suffix = f"notice={notice}" if notice else ""
    referer = request.headers.get("referer", "")
    if "/app/approvals" in referer:
        return f"/app/approvals?{suffix}" if suffix else "/app/approvals"
    if message_id:
        return (
            f"/app/inbox?message={message_id}&{suffix}"
            if suffix
            else f"/app/inbox?message={message_id}"
        )
    return f"/app/inbox?{suffix}" if suffix else "/app/inbox"


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
def approvals(request: Request, notice: str | None = None) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "approvals")
    context["notice"] = _REVIEW_NOTICES.get(notice or "")

    # One join, not one action query plus a message lookup per row. This is by
    # design the busiest page in the product, so an N+1 here is the worst place
    # for one.
    pending = _actions.list_pending_with_messages(user["org_id"], limit=100)
    # An escalation whose role has no mailbox configured cannot be executed.
    # Said here, beside the button, rather than discovered as a failed send.
    escalation_targets = _escalation_map(user["org_id"])
    context["actions"] = [
        {
            "action": item["action"],
            "message": item["message"],
            # Approving is a decision; showing the reasoning next to the button
            # is what makes it an informed one.
            "rationale": item["action"].get("rationale")
            or _rationale_for(user["org_id"], item["message"]),
            "escalation_to": escalation_targets.get(item["action"].get("escalate_to") or ""),
            "escalation_unset": (
                item["action"]["action_type"] == "escalate"
                and not escalation_targets.get(item["action"].get("escalate_to") or "")
            ),
        }
        for item in pending["items"]
    ]
    context["pending_total"] = pending["total"]

    # Approved actions the provider then refused. These were on no page at
    # all, so a reviewer believed they had sent something they had not.
    from app.saas.sync_service import MAX_SEND_RETRIES

    failed = _actions.list_failed_sends(user["org_id"], max_retries=MAX_SEND_RETRIES + 1, limit=20)
    context["failed_sends"] = [
        {
            "action": action,
            "message": _messages.get(user["org_id"], action["message_id"]) or {},
            "retryable": int(action.get("retry_count") or 0) < MAX_SEND_RETRIES,
        }
        for action in failed
    ]

    # What the queue's past decisions have taught the copilot — shown beside
    # the queue those decisions came from, so the learning is auditable.
    from app.saas.learning import FeedbackService

    context["learning"] = FeedbackService().insights(user["org_id"])
    return _render(request, "approvals.html", context)


# --------------------------------------------------------------------------- #
# Waiting on: commitments in both directions
# --------------------------------------------------------------------------- #
WAITING_PAGE_SIZE = 100
_COMMITMENT_DIRECTIONS = ("ours", "theirs")


@web_router.get("/app/waiting", response_class=HTMLResponse)
def waiting(
    request: Request,
    direction: str | None = None,
    show: str | None = None,
    notice: str | None = None,
) -> HTMLResponse:
    """What this workspace owes, and what it is waiting on.

    The capability every competitor is missing and every review of them names.
    An executive's real failure mode is not a mis-triaged message; it is a
    promise made three weeks ago that nobody wrote down.
    """
    user = _require_user(request)
    context = _app_context(request, user, "waiting")
    context["notice"] = _WAITING_NOTICES.get(notice or "")

    direction = direction if direction in _COMMITMENT_DIRECTIONS else None
    status = "done" if show == "done" else "open"

    listing = _commitments.list_for_org(
        user["org_id"], status=status, direction=direction, limit=WAITING_PAGE_SIZE
    )
    today = datetime.now(timezone.utc).date().isoformat()
    context["commitments"] = [
        {**row, "overdue": bool(row["due_at"]) and row["due_at"] < today}
        for row in listing["commitments"]
    ]
    context["filters"] = {"direction": direction, "show": status}
    context["directions"] = _COMMITMENT_DIRECTIONS
    return _render(request, "waiting.html", context)


_WAITING_NOTICES = {
    "done": "Marked done. It stays on the record under Done.",
    "dropped": "Dismissed. The copilot will not raise it again.",
    "reopened": "Reopened.",
}


@web_router.post("/app/commitments/{commitment_id}/{decision}")
def resolve_commitment(
    request: Request, commitment_id: str, decision: str, csrf_token: str = Form("")
) -> Response:
    """Mark a commitment done, dismiss it, or put it back.

    "Dismissed" is a human saying this was never a real commitment. Keeping
    that distinct from "done" is what makes the list worth reading: a tracker
    you cannot correct becomes a tracker you stop opening.
    """
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    status = {"done": "done", "dismiss": "dropped", "reopen": "open"}.get(decision)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown decision")
    if _commitments.get(user["org_id"], commitment_id) is None:
        raise HTTPException(status_code=404, detail="That follow-up no longer exists")

    _commitments.set_status(user["org_id"], commitment_id, status, actor_user_id=user["id"])
    _audit.record(
        action="commitment.resolve",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        target=commitment_id,
        detail={"status": status},
        ip=_client_ip(request),
    )
    notice = {"done": "done", "dropped": "dropped", "open": "reopened"}[status]
    return RedirectResponse(url=f"/app/waiting?notice={notice}", status_code=303)


ACTIVITY_PAGE_SIZE = 50


@web_router.get("/app/activity", response_class=HTMLResponse)
def activity(
    request: Request,
    action: str | None = None,
    actor: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    user = _require_user(request)
    context = _app_context(request, user, "activity")
    can_view = role_at_least(user["role"], ROLE_ADMIN)
    if can_view:
        # Role decides who may look; the entitlement decides whether the
        # feature exists at all. Renders as an error page with the way forward.
        try:
            _billing.require_feature(user["org_id"], licensing.FEATURE_AUDIT_LOG)
        except BillingError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    context["can_view"] = can_view

    members = _users.list_for_org(user["org_id"])
    context["actor_names"] = {
        member["id"]: member.get("full_name") or member["email"] for member in members
    }
    context["members"] = members

    if not can_view:
        context["entries"] = []
        context["available_actions"] = []
        context["filters"] = {"action": None, "actor": None}
        context["page"] = {"number": 1, "total": 0, "has_prev": False, "has_next": False}
        return _render(request, "activity.html", context)

    available = _audit.actions_for_org(user["org_id"])
    # Only ever pass through a value the log actually contains: the filter is
    # a dropdown, and an arbitrary query-string value has no business reaching
    # a query or being echoed back into the page.
    action = action if action in available else None
    actor = actor if actor in context["actor_names"] else None
    page = max(1, page)

    result = _audit.page_for_org(
        user["org_id"],
        limit=ACTIVITY_PAGE_SIZE,
        offset=(page - 1) * ACTIVITY_PAGE_SIZE,
        action=action,
        actor_user_id=actor,
    )
    context["entries"] = result["entries"]
    context["available_actions"] = available
    context["filters"] = {"action": action, "actor": actor}
    context["page"] = {
        "number": page,
        "size": ACTIVITY_PAGE_SIZE,
        "total": result["total"],
        "first": (page - 1) * ACTIVITY_PAGE_SIZE + 1 if result["entries"] else 0,
        "last": (page - 1) * ACTIVITY_PAGE_SIZE + len(result["entries"]),
        "has_prev": page > 1,
        "has_next": page * ACTIVITY_PAGE_SIZE < result["total"],
        "query": _query_string(action=action, actor=actor),
    }
    return _render(request, "activity.html", context)


# Post-redirect notices for the settings page, keyed rather than free-text so
# the query string can never render attacker-chosen copy.
_SETTINGS_NOTICES = {
    "password_changed": "Password updated.",
    "license_activated": "License activated. Your plan is live.",
    "role_updated": "Member role updated.",
    "member_removed": "Member removed from the workspace.",
    "escalation_set": "Escalation contact saved.",
    "escalation_cleared": (
        "Escalation contact removed. Escalations to that role cannot be sent until one is set."
    ),
}


def _days_remaining(expires_at: Any) -> int | None:
    """Whole days left on an entitlement, floored at 0. None if undated."""
    if not expires_at:
        return None
    try:
        moment = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, (moment - datetime.now(timezone.utc)).days)


def _settings_context(request: Request, user: dict) -> dict[str, Any]:
    context = _app_context(request, user, "settings")
    raw = _billing.current_entitlement(user["org_id"])
    members = _users.list_for_org(user["org_id"])
    plan = raw.get("plan")
    active = bool(raw.get("is_valid"))
    # What Settings renders is *access*, not a price tier: a trial with a clock
    # on it, or full access. The plan key stays server-side — naming a tier the
    # customer cannot look up invites the question "what are the others?", and
    # there is no page that answers it.
    context["access"] = {
        "active": active,
        "is_trial": plan == "trial",
        "days_remaining": _days_remaining(raw.get("expires_at")),
        "expires_at": raw.get("expires_at"),
        # The upsell shows on a trial, and on anything that has lapsed. A
        # customer with live paid access is not asked to buy again.
        "show_keep_access": (plan == "trial") or not active,
    }
    context["entitlement"] = {
        "plan": plan,
        "seats": raw.get("seats"),
        "features": raw.get("features", []),
        "expires_at": raw.get("expires_at"),
        "active": active,
    }
    context["members"] = members
    context["member_count"] = len(members)
    context["assignable_roles"] = [
        role for role in ROLES if rbac.can_assign_role(user["role"], role)
    ]
    context["is_owner"] = user["role"] == ROLE_OWNER
    context["model_usage"] = _model_usage(user["org_id"])
    context["escalation_contacts"] = _escalation_contacts(user["org_id"])
    return context


def _escalation_map(org_id: str) -> dict[str, str]:
    """``{role: mailbox}`` for this workspace; empty if it cannot be read."""
    try:
        return EscalationContactRepository().map_for_org(org_id)
    except Exception:  # noqa: BLE001 - a page must render regardless
        return {}


def _escalation_contacts(org_id: str) -> list[dict[str, Any]]:
    """Every escalation role and the mailbox it resolves to, in a fixed order.

    Rendered for all members, editable only by admins: whether escalations can
    be sent at all is something the person approving them needs to know, and the
    unset state is the interesting one.
    """
    configured = _escalation_map(org_id)
    return [
        {
            "role": role,
            "label": role.replace("_", " "),
            "email": configured.get(role, ""),
        }
        for role in ESCALATION_ROLES
    ]


def _model_usage(org_id: str) -> dict[str, Any] | None:
    """This month's model spend against its ceiling, or ``None`` to hide it.

    Hidden when drafting is off and nothing has ever been spent: a workspace
    running on cached and deterministic prose has no spend to explain, and a
    row of zeroes reads as a broken feature rather than an absent one.
    """
    from app.saas.repository import LlmUsageRepository

    settings = get_settings()
    try:
        summary = LlmUsageRepository().summary(org_id)
    except Exception:  # noqa: BLE001 - a settings page must render regardless
        return None
    if not summary["calls"] and not settings.llm_drafting_enabled:
        return None
    limit = float(settings.llm_monthly_budget_usd)
    return {
        "cost_usd": summary["cost_usd"],
        "calls": summary["calls"],
        "tokens": summary["prompt_tokens"] + summary["completion_tokens"],
        "limit_usd": limit,
        "capped": limit > 0,
        "exhausted": limit > 0 and summary["cost_usd"] >= limit,
        "percent": min(100, round(summary["cost_usd"] / limit * 100)) if limit > 0 else 0,
    }


@web_router.get("/app/settings", response_class=HTMLResponse)
def settings_page(request: Request, notice: str | None = None) -> HTMLResponse:
    user = _require_user(request)
    context = _settings_context(request, user)
    context["notice"] = _SETTINGS_NOTICES.get(notice or "")
    return _render(request, "settings.html", context)


def _render_settings(
    request: Request,
    user: dict,
    *,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = _settings_context(request, user)
    context["error"] = error
    context["notice"] = notice
    return _render(request, "settings.html", context, status_code=status_code)


# --------------------------------------------------------------------------- #
# Settings: members
# --------------------------------------------------------------------------- #
@web_router.post("/app/members/invite")
def invite_member_web(
    request: Request,
    email: str = Form(""),
    full_name: str = Form(""),
    role: str = Form("member"),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    reject_shared_demo_account(user)

    import secrets

    temp_password = secrets.token_urlsafe(9)
    try:
        member = OrgService().invite_member(
            actor=user,
            email=email.strip().lower(),
            full_name=full_name.strip(),
            role=role,
            temp_password=temp_password,
        )
    except OrgError as exc:
        return _render_settings(request, user, error=exc.message, status_code=exc.status_code)
    # Rendered rather than redirected: the temporary password is shown exactly
    # once, to the admin who created it (it is also in the invite email).
    return _render_settings(
        request,
        user,
        notice=(
            f"Invited {member['email']} as {member['role']}. "
            f"Temporary password (shown once, also emailed): {temp_password}"
        ),
    )


@web_router.post("/app/members/{member_id}/role")
def change_member_role_web(
    request: Request,
    member_id: str,
    role: str = Form(""),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    reject_shared_demo_account(user)
    try:
        OrgService().change_member_role(actor=user, member_id=member_id, role=role)
    except OrgError as exc:
        return _render_settings(request, user, error=exc.message, status_code=exc.status_code)
    return RedirectResponse(url="/app/settings?notice=role_updated", status_code=303)


@web_router.post("/app/members/{member_id}/remove")
def remove_member_web(request: Request, member_id: str, csrf_token: str = Form("")) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    reject_shared_demo_account(user)
    try:
        OrgService().remove_member(actor=user, member_id=member_id)
    except OrgError as exc:
        return _render_settings(request, user, error=exc.message, status_code=exc.status_code)
    return RedirectResponse(url="/app/settings?notice=member_removed", status_code=303)


# --------------------------------------------------------------------------- #
# Settings: account, license, data
# --------------------------------------------------------------------------- #
@web_router.post("/app/settings/password")
def change_password_web(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    reject_shared_demo_account(user)
    if len(new_password) < 8:
        return _render_settings(
            request, user, error="Choose a new password of at least 8 characters.", status_code=400
        )
    try:
        _auth.change_password(user, current_password, new_password)
    except AuthError as exc:
        return _render_settings(request, user, error=exc.message, status_code=exc.status_code)
    _audit.record(
        action="auth.change_password",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        detail={"surface": "web"},
        ip=_client_ip(request),
    )
    return RedirectResponse(url="/app/settings?notice=password_changed", status_code=303)


@web_router.post("/app/settings/license")
def activate_license_web(
    request: Request, license_key: str = Form(""), csrf_token: str = Form("")
) -> Response:
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    if user["role"] != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can activate a license")
    reject_shared_demo_account(user)
    try:
        _billing.activate_license(
            org_id=user["org_id"], license_key=license_key.strip(), actor_user_id=user["id"]
        )
    except BillingError as exc:
        return _render_settings(request, user, error=exc.message, status_code=exc.status_code)
    return RedirectResponse(url="/app/settings?notice=license_activated", status_code=303)


@web_router.post("/app/settings/escalation")
def set_escalation_contact_web(
    request: Request,
    role: str = Form(""),
    email: str = Form(""),
    csrf_token: str = Form(""),
) -> Response:
    """Point one escalation role at a mailbox, or clear it.

    An empty address clears the contact rather than storing a blank one: half a
    contact would satisfy the "is it configured?" check and then produce a draft
    addressed to nobody.
    """
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    _require_manage(user)
    reject_shared_demo_account(user)

    chosen = (role or "").strip()
    if chosen not in ESCALATION_ROLES:
        return _render_settings(
            request, user, error="That is not an escalation role.", status_code=400
        )

    address = (email or "").strip()
    contacts = EscalationContactRepository()
    if not address:
        contacts.clear(user["org_id"], chosen)
        notice = "escalation_cleared"
    else:
        from email_validator import EmailNotValidError, validate_email

        try:
            # Deliverability is not checked: it costs a DNS lookup inside a form
            # POST, and a syntactically valid address that does not exist fails
            # visibly at send time anyway, on a retryable action.
            address = validate_email(address, check_deliverability=False).normalized
        except EmailNotValidError:
            return _render_settings(
                request,
                user,
                error="That does not look like an email address.",
                status_code=400,
            )
        contacts.set_email(user["org_id"], chosen, address)
        notice = "escalation_set"

    _audit.record(
        action="org.escalation_contact",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        target=chosen,
        detail={"email": address or None},
        ip=_client_ip(request),
    )
    return RedirectResponse(url=f"/app/settings?notice={notice}", status_code=303)


@web_router.get("/app/settings/export")
def export_org_web(request: Request) -> Response:
    """The tenant's full data bundle as a JSON download. Owner only (GDPR)."""
    user = _require_user(request)
    if user["role"] != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can export the workspace")
    reject_shared_demo_account(user)
    from app.saas.data_lifecycle import DataLifecycleService

    bundle = DataLifecycleService().export_org(user["org_id"])
    if bundle is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    _audit.record(
        action="org.export",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        ip=_client_ip(request),
    )
    org = _orgs.get(user["org_id"]) or {}
    filename = f"{org.get('slug', 'workspace')}-export.json"
    return Response(
        content=json.dumps(bundle, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@web_router.post("/app/settings/delete-org")
def delete_org_web(
    request: Request, confirm: str = Form(""), csrf_token: str = Form("")
) -> Response:
    """Permanent erasure, gated on retyping the org slug — deliberate friction."""
    verify_csrf(request, csrf_token)
    user = _require_user(request)
    if user["role"] != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can delete the workspace")
    reject_shared_demo_account(user)
    org = _orgs.get(user["org_id"])
    if not org or confirm.strip() != org["slug"]:
        return _render_settings(
            request,
            user,
            error="Type the workspace slug exactly to confirm deletion.",
            status_code=400,
        )
    from app.saas.data_lifecycle import DataLifecycleService

    DataLifecycleService().delete_org(user["org_id"])
    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response


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
    "/contact-sales",
    "/welcome",
    "/privacy",
    "/terms",
    "/demo",
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

    One addition for the front door: a URL that matches *nothing* at all (a
    mistyped path, a capital letter in ``/Demo``) is a 404 from a browser that
    asked for HTML, and gets the same page instead of a black screen of JSON. It
    keys on the ``Accept`` header, not the path, because the point is who is
    asking: ``curl`` and every HTTP library send ``*/*`` and still get JSON.
    """
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    if not isinstance(exc, StarletteHTTPException):
        return await http_exception_handler(request, exc)  # type: ignore[arg-type]
    unknown_page = exc.status_code == 404 and "text/html" in request.headers.get("accept", "")
    if not (is_web_path(request.url.path) or unknown_page):
        return await http_exception_handler(request, exc)

    detail = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
    return _render(
        request,
        "error.html",
        {"status_code": exc.status_code, "message": detail},
        status_code=exc.status_code,
    )
