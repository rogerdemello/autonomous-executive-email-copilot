"""The operator's sales/ops API: provision workspaces, mint and revoke licenses,
read leads, reseed the demo.

This is how a sales-led deployment is run day to day when the operator has no
shell on the box (Render): everything ``scripts/issue_license.py`` can do
locally, but executed by the server that already holds the signing secret and
the database. Deliberately NOT the customer-facing auth model — a single
``OPERATOR_TOKEN`` bearer credential, compared in constant time, guards every
method including reads. When the token is unconfigured the whole surface 404s,
so a deployment that doesn't opt in simply does not have these routes.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import get_settings
from app.core.security import extract_bearer_token

from . import licensing
from .auth import AuthError
from .billing import BillingService
from .repository import (
    AuditRepository,
    LicenseRepository,
    OrganizationRepository,
    SalesLeadRepository,
    UserRepository,
)
from .schemas import (
    LeadStatusRequest,
    MintLicenseRequest,
    ProvisionOrgRequest,
    RevokeLicenseRequest,
)

logger = logging.getLogger(__name__)

operator_router = APIRouter(prefix="/operator", tags=["operator"], include_in_schema=False)

_orgs = OrganizationRepository()
_users = UserRepository()
_licenses = LicenseRepository()
_leads = SalesLeadRepository()
_audit = AuditRepository()
_billing = BillingService()


def require_operator(request: Request) -> None:
    """Gate every operator route on the configured OPERATOR_TOKEN.

    404 (not 401) when no token is configured: an unconfigured deployment
    should not even reveal that an admin surface exists.
    """
    configured = (get_settings().operator_token or "").strip()
    if not configured:
        raise HTTPException(status_code=404, detail="Not Found")
    presented = extract_bearer_token(
        request.headers.get("Authorization"), request.headers.get("X-Operator-Token")
    )
    if not presented or not hmac.compare_digest(presented, configured):
        raise HTTPException(status_code=401, detail="Invalid operator token")


@operator_router.get("/orgs", dependencies=[Depends(require_operator)])
def list_orgs(limit: int = 200) -> dict:
    """Every workspace with its owner and current entitlement — the customer
    list, and where the operator finds the org_id that licensing needs."""
    orgs = _orgs.list_all(limit=max(1, min(limit, 1000)))
    out = []
    for org in orgs:
        members = _users.list_for_org(org["id"])
        owner = next((m for m in members if m.get("role") == "owner"), None)
        entitlement = _billing.current_entitlement(org["id"])
        out.append(
            {
                "organization": org,
                "owner_email": owner.get("email") if owner else None,
                "member_count": len(members),
                "entitlement": {
                    "plan": entitlement.get("plan"),
                    "seats": entitlement.get("seats"),
                    "expires_at": entitlement.get("expires_at"),
                    "is_valid": bool(entitlement.get("is_valid")),
                },
            }
        )
    return {"organizations": out, "total": len(out)}


@operator_router.post("/orgs", dependencies=[Depends(require_operator)])
def provision_org_endpoint(body: ProvisionOrgRequest, request: Request) -> dict:
    """Provision a customer workspace (sales-led onboarding).

    Works regardless of SIGNUP_ENABLED — that flag gates self-serve only. When
    no password is supplied, the generated temporary password appears in this
    response exactly once; hand it to the customer over a trusted channel.
    """
    from .provisioning import provision_org

    try:
        result = provision_org(
            org_name=body.org_name,
            owner_email=body.owner_email,
            owner_name=body.owner_name,
            password=body.password,
            plan=body.plan,
            seats=body.seats,
            valid_days=body.valid_days,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _audit.record(
        action="operator.provision_org",
        org_id=result["organization"]["id"],
        detail={"owner_email": result["owner"]["email"], "plan": body.plan},
        ip=request.client.host if request.client else None,
    )
    return result


@operator_router.post("/licenses", dependencies=[Depends(require_operator)])
def mint_license_endpoint(body: MintLicenseRequest, request: Request) -> dict:
    """Mint a signed license key for an org and persist it (so it can be
    revoked later). The customer owner activates it in Settings."""
    if not _orgs.get(body.org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    try:
        key, terms = licensing.mint_license(
            body.org_id,
            body.plan,
            get_settings().resolved_auth_secret,
            seats=body.seats,
            valid_days=body.valid_days,
        )
    except licensing.LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _licenses.upsert(
        org_id=body.org_id,
        key_id=terms.key_id,
        plan=terms.plan,
        seats=terms.seats,
        features=list(terms.features),
        expires_at_iso=terms.expires_at_iso,
    )
    _audit.record(
        action="operator.mint_license",
        org_id=body.org_id,
        target=terms.key_id,
        detail={"plan": terms.plan, "seats": terms.seats, "expires_at": terms.expires_at_iso},
        ip=request.client.host if request.client else None,
    )
    return {"license_key": key, "terms": terms.__dict__}


@operator_router.post("/licenses/revoke", dependencies=[Depends(require_operator)])
def revoke_license_endpoint(body: RevokeLicenseRequest, request: Request) -> dict:
    """The churn/non-payment kill switch.

    With ``key_id``: revoke that key — the entitlement falls back to the next
    most recent active license (a downgrade, e.g. back to the trial). Without
    ``key_id``: revoke EVERY active license, killing the entitlement outright —
    sync and approvals stop with a 402. A revoked key can never be reactivated.
    """
    if body.key_id is not None:
        if not _licenses.revoke(body.org_id, body.key_id):
            raise HTTPException(status_code=404, detail="License not found for this organization")
        revoked_count = 1
    else:
        if not _orgs.get(body.org_id):
            raise HTTPException(status_code=404, detail="Organization not found")
        revoked_count = _licenses.revoke_all_for_org(body.org_id)
    _audit.record(
        action="operator.revoke_license",
        org_id=body.org_id,
        target=body.key_id or "*",
        detail={"revoked": revoked_count},
        ip=request.client.host if request.client else None,
    )
    return {
        "status": "revoked",
        "org_id": body.org_id,
        "key_id": body.key_id,
        "revoked": revoked_count,
    }


@operator_router.get("/leads", dependencies=[Depends(require_operator)])
def list_leads(limit: int = 100) -> dict:
    """Captured 'contact sales' leads, newest first."""
    leads = _leads.list(limit=max(1, min(limit, 1000)))
    return {"leads": leads, "total": len(leads)}


@operator_router.patch("/leads/{lead_id}", dependencies=[Depends(require_operator)])
def set_lead_status(lead_id: int, body: LeadStatusRequest, request: Request) -> dict:
    lead = _leads.set_status(lead_id, body.status)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    _audit.record(
        action="operator.lead_status",
        target=str(lead_id),
        detail={"status": body.status},
        ip=request.client.host if request.client else None,
    )
    return lead


@operator_router.post("/demo/reseed", dependencies=[Depends(require_operator)])
def reseed_demo(request: Request) -> dict:
    """Reset the demo workspace to its pristine pre-demo state — the undo
    button for anything a visitor did with the shared login."""
    from .demo_seed import seed_demo

    summary = seed_demo(fresh=True)
    summary.pop("pending_actions", None)
    _audit.record(
        action="operator.demo_reseed",
        detail={"messages": summary.get("messages"), "pending": summary.get("pending")},
        ip=request.client.host if request.client else None,
    )
    return summary
