"""The operator's one page: is this product working right now?

Everything here was already queryable — over seven JSON endpoints, with a
bearer token, from a shell. That is a fine way to run a workspace and a useless
way to answer "is it working", which is a question asked from a phone, on a
Sunday, by the one person who would notice.

Read-only by construction: this module issues no writes and mounts no forms
other than the one that authenticates it. Every number comes from a repository
method that the JSON surface already exposes.

**Auth.** The bearer check in :func:`operator_routes.require_operator` is
unchanged and remains the only way to get in. A browser cannot put a token in an
``Authorization`` header by typing a URL, so posting the token to
``/operator/session`` exchanges it for a short-lived signed cookie — the same
HS256 mechanism as a user session, a separate cookie name, and a two-hour life.
The token is never placed in a URL: query strings land in access logs, proxy
logs and ``Referer`` headers, which is how an admin credential becomes a
permanent one.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.core.paths import TEMPLATES_DIR
from app.saas import tokens

from .billing import BillingService
from .repository import (
    LlmUsageRepository,
    MailboxRepository,
    OrganizationRepository,
    ProposedActionRepository,
    SalesLeadRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

operator_view_router = APIRouter(prefix="/operator", tags=["operator"], include_in_schema=False)

OPERATOR_COOKIE = "ec_operator"
# Long enough to work through an incident, short enough that a forgotten open
# tab on a shared machine is not a standing admin session.
_COOKIE_TTL_SECONDS = 2 * 60 * 60

# An entitlement inside this window is the operator's cue to talk to a customer
# before it lapses rather than after.
_EXPIRING_SOON_DAYS = 7

_orgs = OrganizationRepository()
_users = UserRepository()
_mailboxes = MailboxRepository()
_actions = ProposedActionRepository()
_leads = SalesLeadRepository()
_usage = LlmUsageRepository()
_billing = BillingService()


def _templates():
    from fastapi.templating import Jinja2Templates

    return Jinja2Templates(directory=str(TEMPLATES_DIR))


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def _configured_token() -> str:
    """The operator token, or 404 the whole surface if there isn't one.

    404 rather than 401, matching the JSON routes: a deployment that has not
    opted in should not reveal that an admin surface exists at all.
    """
    configured = (get_settings().operator_token or "").strip()
    if not configured:
        raise HTTPException(status_code=404, detail="Not Found")
    return configured


def _has_operator_cookie(request: Request) -> bool:
    raw = request.cookies.get(OPERATOR_COOKIE)
    if not raw:
        return False
    try:
        claims = tokens.decode(raw, get_settings().resolved_auth_secret)
    except tokens.TokenError:
        return False
    if claims.get("typ") != "operator":
        return False
    # Bind the cookie to the token that minted it, so rotating OPERATOR_TOKEN
    # invalidates every outstanding cookie — which is the entire point of
    # rotating it.
    return hmac.compare_digest(str(claims.get("tok", "")), _token_fingerprint(_configured_token()))


def _token_fingerprint(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _set_operator_cookie(response: Response) -> None:
    settings = get_settings()
    token = tokens.encode(
        {"typ": "operator", "tok": _token_fingerprint(_configured_token())},
        settings.resolved_auth_secret,
        ttl_seconds=_COOKIE_TTL_SECONDS,
    )
    response.set_cookie(
        OPERATOR_COOKIE,
        token,
        max_age=_COOKIE_TTL_SECONDS,
        httponly=True,
        # Strict, not Lax: nothing should ever navigate into this page from
        # another site, so there is no usability cost to refusing it.
        samesite="strict",
        secure=settings.environment.strip().lower() != "development",
        path="/operator",
    )


@operator_view_router.get("/session", response_class=HTMLResponse)
def operator_login_form(request: Request, error: str | None = None) -> HTMLResponse:
    _configured_token()
    return _templates().TemplateResponse(
        request,
        "operator_login.html",
        {"error": "That token was not accepted." if error else None},
    )


@operator_view_router.post("/session")
def operator_login(request: Request, operator_token: str = Form("")) -> Response:
    configured = _configured_token()
    if not operator_token or not hmac.compare_digest(operator_token.strip(), configured):
        logger.warning("Rejected an operator sign-in attempt")
        return RedirectResponse("/operator/session?error=1", status_code=303)
    response = RedirectResponse("/operator", status_code=303)
    _set_operator_cookie(response)
    return response


@operator_view_router.post("/session/end")
def operator_logout() -> Response:
    response = RedirectResponse("/operator/session", status_code=303)
    response.delete_cookie(OPERATOR_COOKIE, path="/operator")
    return response


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
@operator_view_router.get("", response_class=HTMLResponse)
@operator_view_router.get("/", response_class=HTMLResponse)
def operator_health(request: Request) -> Response:
    _configured_token()
    if not _has_operator_cookie(request):
        return RedirectResponse("/operator/session", status_code=303)
    return _templates().TemplateResponse(request, "operator.html", build_health(request))


def build_health(request: Request, now: datetime | None = None) -> dict:
    """Everything the page renders. Split out so it is testable without HTML."""
    now = now or datetime.now(timezone.utc)
    spend_by_org = _safe(lambda: _usage.by_org(now=now), {})
    workspaces = _workspaces(now, spend_by_org)
    return {
        "now": now.isoformat(timespec="seconds"),
        "workspaces": workspaces,
        "totals": {
            "workspaces": len(workspaces),
            "members": sum(w["members"] for w in workspaces),
            "expiring_soon": sum(1 for w in workspaces if w["expiring_soon"]),
            "lapsed": sum(1 for w in workspaces if not w["active"]),
            "spend_usd": round(sum(spend_by_org.values()), 2),
        },
        "connections": _safe(_mailboxes.count_by_status, {}),
        "worker": _worker(request),
        "failed_sends": _failed_sends(),
        "leads": _safe(lambda: [lead for lead in _leads.list(limit=25)], []),
        "budget_usd": float(get_settings().llm_monthly_budget_usd),
        "drafting_enabled": get_settings().llm_drafting_enabled,
    }


def _safe(call, fallback):
    """One broken query must not take the whole health page down with it.

    A status page that 500s when a single number is unavailable is the page
    failing at precisely the moment it exists for.
    """
    try:
        return call()
    except Exception:  # noqa: BLE001 - see above
        logger.warning("Operator health: a query failed", exc_info=True)
        return fallback


def _workspaces(now: datetime, spend_by_org: dict[str, float]) -> list[dict]:
    rows = []
    for org in _safe(lambda: _orgs.list_all(limit=500), []):
        members = _safe(lambda org_id=org["id"]: _users.list_for_org(org_id), [])
        owner = next((m for m in members if m.get("role") == "owner"), None)
        entitlement = _safe(
            lambda org_id=org["id"]: _billing.current_entitlement(org_id),
            {},
        )
        days = _days_until(entitlement.get("expires_at"), now)
        rows.append(
            {
                "id": org["id"],
                "name": org.get("name") or org["id"],
                "created_at": (org.get("created_at") or "")[:10],
                "owner_email": owner.get("email") if owner else None,
                "members": len(members),
                "plan": entitlement.get("plan"),
                "active": bool(entitlement.get("is_valid")),
                "days_remaining": days,
                "expiring_soon": days is not None and 0 <= days <= _EXPIRING_SOON_DAYS,
                "spend_usd": round(spend_by_org.get(org["id"], 0.0), 2),
            }
        )
    # Whoever needs attention first: lapsed, then expiring, then by spend.
    rows.sort(key=lambda r: (r["active"], not r["expiring_soon"], -r["spend_usd"]))
    return rows


def _days_until(expires_at: str | None, now: datetime) -> int | None:
    if not expires_at:
        return None
    try:
        moment = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - now + timedelta(seconds=1)).days


def _worker(request: Request) -> dict:
    worker = getattr(request.app.state, "sync_worker", None)
    if worker is None:
        return {"enabled": False}
    return {"enabled": True, **_safe(worker.heartbeat, {"unknown": True})}


def _failed_sends() -> dict:
    """Approved replies the provider refused, which need a human eventually."""
    from .sync_service import MAX_SEND_RETRIES

    orgs = _safe(lambda: _actions.orgs_with_failed_sends(MAX_SEND_RETRIES), [])
    return {"orgs": len(orgs), "max_retries": MAX_SEND_RETRIES}
