"""SaaS API: authentication, organization/members, and sales-led billing.

Mounted on the main app under three routers. Public endpoints (signup, login,
contact-sales) are exempt from the operator ``API_AUTH_TOKEN`` gateway (see
``app.main``) so customers can always reach them.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.security import lead_submission_allowed, login_attempt_allowed

from . import licensing
from .auth import AuthError, AuthService
from .billing import BillingError, BillingService
from .data_lifecycle import DataLifecycleService
from .deps import get_current_user, reject_shared_demo_account, require_role
from .email import send_email
from .models_db import ROLE_ADMIN, ROLE_OWNER
from .org_service import OrgError, OrgService
from .repository import AuditRepository, OrganizationRepository, UserRepository
from .schemas import (
    ActivateLicenseRequest,
    ChangePasswordRequest,
    ContactSalesRequest,
    DeleteOrgRequest,
    ForgotPasswordRequest,
    InviteMemberRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    UpdateMemberRoleRequest,
)

logger = logging.getLogger(__name__)

_auth = AuthService()
_billing = BillingService()
_orgs = OrganizationRepository()
_users = UserRepository()
_audit = AuditRepository()
_lifecycle = DataLifecycleService()
_org_service = OrgService()

# Path prefixes that must bypass the operator API_AUTH_TOKEN gateway because they
# are how customers authenticate in the first place. Consumed by app.main.
PUBLIC_SAAS_PREFIXES = (
    "/auth/signup",
    "/auth/login",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/sso",
    "/billing/contact-sales",
)

# The SaaS/product API self-authenticates with per-user session tokens (see
# app.saas.deps), so the operator-level API_AUTH_TOKEN gate must NOT also apply
# to it — otherwise a customer's session token wouldn't match the operator token
# and every authenticated SaaS call would 401. These prefixes enforce their own
# auth via route dependencies; unauthenticated calls still 401 there.
SAAS_SELF_AUTH_PREFIXES = (
    "/auth",
    "/org",
    "/billing",
    "/mailbox",
    "/inbox",
    # The server-rendered UI authenticates per-user with the session cookie
    # and guards its own forms with CSRF, so it must not also be gated behind
    # the operator-level API_AUTH_TOKEN — that would 401 every form post on a
    # locked-down deployment.
    "/app",
    "/login",
    "/signup",
    "/logout",
    "/static",
    # Public lead-capture form (CSRF + honeypot + its own throttle).
    "/contact-sales",
    # The operator API enforces its own bearer token on EVERY method (including
    # GET) — stricter than the gateway, which must therefore not double-gate it.
    "/operator",
)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
org_router = APIRouter(prefix="/org", tags=["organization"])
billing_router = APIRouter(prefix="/billing", tags=["billing"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@auth_router.post("/signup")
def signup(body: SignupRequest, request: Request) -> dict:
    try:
        user, org, _terms = _auth.signup(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            org_name=body.org_name,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    token, expires_in = _auth.issue_token(user)
    _audit.record(
        action="auth.signup",
        org_id=org["id"],
        actor_user_id=user["id"],
        detail={"email": user["email"]},
        ip=_client_ip(request),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": user,
        "organization": org,
    }


@auth_router.post("/login")
def login(body: LoginRequest, request: Request) -> dict:
    if not login_attempt_allowed(
        _client_ip(request), body.email, get_settings().login_rate_limit_per_minute
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Wait a minute and try again.",
            headers={"Retry-After": "60"},
        )
    try:
        user = _auth.authenticate(email=body.email, password=body.password)
    except AuthError as exc:
        # Attach the org when the account exists, or the failure can never
        # appear on that tenant's Activity page — which promises to show
        # failed sign-ins.
        account = _users.get_by_email_global(body.email)
        _audit.record(
            action="auth.login_failed",
            org_id=account["org_id"] if account else None,
            detail={"email": body.email, "surface": "api"},
            ip=_client_ip(request),
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    org = _orgs.get(user["org_id"])
    token, expires_in = _auth.issue_token(user)
    _audit.record(
        action="auth.login",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        ip=_client_ip(request),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": user,
        "organization": org,
    }


@auth_router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    org = _orgs.get(user["org_id"])
    return {"user": user, "organization": org}


@auth_router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    reject_shared_demo_account(user)
    try:
        _auth.change_password(user, body.current_password, body.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _audit.record(
        action="auth.change_password",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        ip=_client_ip(request),
    )
    return {"status": "ok"}


@auth_router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, request: Request) -> dict:
    """Start a password reset. Always returns 200 (never reveals if the email
    exists); when it does, a reset link is emailed."""
    token = _auth.request_password_reset(body.email)
    if token:
        link = f"{get_settings().resolved_app_public_url}/reset-password?token={token}"
        send_email(
            body.email,
            "Reset your Executive Email Copilot password",
            "We received a request to reset your password. Use the link below "
            f"(valid for {get_settings().password_reset_ttl_minutes} minutes):\n\n{link}\n\n"
            "If you didn't request this, you can safely ignore this email.",
        )
        account = _users.get_by_email_global(body.email)
        _audit.record(
            action="auth.password_reset_requested",
            org_id=account["org_id"] if account else None,
            detail={"email": body.email},
            ip=_client_ip(request),
        )
    return {"status": "ok"}


@auth_router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, request: Request) -> dict:
    try:
        user = _auth.reset_password(body.token, body.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _audit.record(
        action="auth.password_reset",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        ip=_client_ip(request),
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# SSO (OIDC single sign-on)
# --------------------------------------------------------------------------- #
@auth_router.get("/sso/status")
def sso_status() -> dict:
    """Whether server-level SSO is configured (drives the login UI button)."""
    return {"enabled": get_settings().sso_enabled}


@auth_router.get("/sso/login", include_in_schema=False)
def sso_login(request: Request) -> RedirectResponse:
    """Begin the OIDC flow: redirect the browser to the identity provider."""
    settings = get_settings()
    if not settings.sso_enabled:
        raise HTTPException(status_code=404, detail="SSO is not configured on this server")
    import secrets

    from . import oidc

    try:
        config = oidc.fetch_discovery(settings.oidc_issuer or "")
        nonce = secrets.token_urlsafe(16)
        state = oidc.sign_state(nonce=nonce)
        url = oidc.build_authorize_url(
            config=config,
            client_id=settings.oidc_client_id or "",
            redirect=oidc.redirect_uri(str(request.base_url).rstrip("/")),
            state=state,
            nonce=nonce,
        )
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=307)


@auth_router.get("/sso/callback", include_in_schema=False)
def sso_callback(request: Request) -> RedirectResponse:
    """OIDC redirect target: verify the id_token, provision/log in, set the
    session cookie, and land the browser in the app."""
    settings = get_settings()
    if not settings.sso_enabled:
        raise HTTPException(status_code=404, detail="SSO is not configured on this server")
    from . import oidc

    params = request.query_params
    code, state = params.get("code"), params.get("state")
    if params.get("error") or not code or not state:
        raise HTTPException(status_code=400, detail="SSO was cancelled or returned no code")
    try:
        nonce = oidc.verify_state(state).get("nonce")
        config = oidc.fetch_discovery(settings.oidc_issuer or "")
        token_response = oidc.exchange_code(
            config=config,
            code=code,
            client_id=settings.oidc_client_id or "",
            client_secret=settings.oidc_client_secret or "",
            redirect=oidc.redirect_uri(str(request.base_url).rstrip("/")),
        )
        id_token = token_response.get("id_token")
        if not id_token:
            raise oidc.OIDCError("token response contained no id_token")
        identity = oidc.verify_id_token(
            id_token,
            jwks=oidc.fetch_jwks(config),
            issuer=settings.oidc_issuer or "",
            audience=settings.oidc_client_id or "",
            nonce=nonce,
        )
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=401, detail=f"SSO sign-in failed: {exc}") from exc

    try:
        user = _auth.login_or_provision_sso(email=identity.email, full_name=identity.name)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # Plan gate, not an auth gate: the IdP verified who they are; the license
    # decides whether SSO sign-in is part of what this org bought. (Trials
    # include it — a fresh SSO-provisioned org must be able to sign in.)
    try:
        _billing.require_feature(user["org_id"], licensing.FEATURE_SSO)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    token, _ttl = _auth.issue_token(user)
    _audit.record(
        action="auth.sso_login",
        org_id=user["org_id"],
        actor_user_id=user["id"],
        detail={"email": user["email"]},
        ip=_client_ip(request),
    )
    # Land in the product signed in: the browser needs the session *cookie*,
    # not a token in the URL (which would leak into history and referrers).
    from app.web.session import set_session_cookie

    response = RedirectResponse(url="/app/inbox", status_code=303)
    set_session_cookie(response, token)
    return response


# --------------------------------------------------------------------------- #
# Organization & members
# --------------------------------------------------------------------------- #
@org_router.get("")
def get_org(user: dict = Depends(get_current_user)) -> dict:
    org = _orgs.get(user["org_id"])
    entitlement = _billing.current_entitlement(user["org_id"])
    members = _users.list_for_org(user["org_id"])
    return {"organization": org, "entitlement": entitlement, "member_count": len(members)}


@org_router.get("/members")
def list_members(user: dict = Depends(get_current_user)) -> dict:
    return {"members": _users.list_for_org(user["org_id"])}


@org_router.post("/members")
def invite_member(
    body: InviteMemberRequest,
    request: Request,
    actor: dict = Depends(require_role(ROLE_ADMIN)),
) -> dict:
    reject_shared_demo_account(actor)
    try:
        member = _org_service.invite_member(
            actor=actor,
            email=body.email,
            full_name=body.full_name,
            role=body.role,
            temp_password=body.temp_password,
            ip=_client_ip(request),
        )
    except OrgError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"member": member}


@org_router.patch("/members/{member_id}/role")
def update_member_role(
    member_id: str,
    body: UpdateMemberRoleRequest,
    request: Request,
    actor: dict = Depends(require_role(ROLE_ADMIN)),
) -> dict:
    reject_shared_demo_account(actor)
    try:
        updated = _org_service.change_member_role(
            actor=actor, member_id=member_id, role=body.role, ip=_client_ip(request)
        )
    except OrgError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"member": updated}


@org_router.delete("/members/{member_id}")
def remove_member(
    member_id: str,
    request: Request,
    actor: dict = Depends(require_role(ROLE_ADMIN)),
) -> dict:
    reject_shared_demo_account(actor)
    try:
        _org_service.remove_member(actor=actor, member_id=member_id, ip=_client_ip(request))
    except OrgError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"status": "ok", "removed": member_id}


@org_router.get("/audit-log")
def audit_log(actor: dict = Depends(require_role(ROLE_ADMIN))) -> dict:
    try:
        _billing.require_feature(actor["org_id"], licensing.FEATURE_AUDIT_LOG)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"entries": _audit.list_for_org(actor["org_id"])}


@org_router.get("/export")
def export_org(owner: dict = Depends(require_role(ROLE_OWNER))) -> dict:
    """Download all of the organization's data (secret-free). Owner only (GDPR)."""
    reject_shared_demo_account(owner)
    bundle = _lifecycle.export_org(owner["org_id"])
    if bundle is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    _audit.record(action="org.export", org_id=owner["org_id"], actor_user_id=owner["id"])
    return bundle


@org_router.delete("")
def delete_org(
    body: DeleteOrgRequest,
    request: Request,
    owner: dict = Depends(require_role(ROLE_OWNER)),
) -> dict:
    """Permanently delete the organization and all its data (GDPR erasure).

    Requires ``confirm`` to equal the org slug — deliberate friction against an
    accidental, irreversible purge."""
    reject_shared_demo_account(owner)
    org = _orgs.get(owner["org_id"])
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.confirm.strip() != org["slug"]:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation does not match. Type the org slug '{org['slug']}' to confirm.",
        )
    # Log before the purge — the org's own audit rows are about to be deleted.
    logger.warning(
        "org.delete org_id=%s slug=%s by=%s ip=%s",
        owner["org_id"],
        org["slug"],
        owner["id"],
        _client_ip(request),
    )
    counts = _lifecycle.delete_org(owner["org_id"])
    return {"status": "deleted", "deleted": counts}


# --------------------------------------------------------------------------- #
# Billing (sales-led)
# --------------------------------------------------------------------------- #
@billing_router.get("/entitlement")
def entitlement(user: dict = Depends(get_current_user)) -> dict:
    return _billing.current_entitlement(user["org_id"])


@billing_router.post("/activate-license")
def activate_license(
    body: ActivateLicenseRequest,
    request: Request,
    owner: dict = Depends(require_role(ROLE_OWNER)),
) -> dict:
    reject_shared_demo_account(owner)
    try:
        ent = _billing.activate_license(
            org_id=owner["org_id"],
            license_key=body.license_key.strip(),
            actor_user_id=owner["id"],
        )
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"status": "ok", "entitlement": ent}


@billing_router.post("/contact-sales")
def contact_sales(body: ContactSalesRequest, request: Request) -> dict:
    if not lead_submission_allowed(_client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many submissions. Wait a minute and try again.",
            headers={"Retry-After": "60"},
        )
    lead = _billing.capture_lead(
        email=body.email,
        kind=body.kind or "contact_sales",
        name=body.name or None,
        company=body.company or None,
        seats=body.seats,
        message=body.message or None,
    )
    return {"status": "received", "lead_id": lead["id"]}
