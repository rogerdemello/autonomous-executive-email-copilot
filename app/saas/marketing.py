"""Machine-readable marketing endpoints.

The human-facing landing and pricing pages are templates in :mod:`app.web`.
What stays here is the data behind them: ``/api/pricing`` serves the plan
registry directly from :data:`licensing.PLANS`, so published pricing can never
drift from what the entitlement system actually grants — and ``security.txt``,
which is generated rather than checked in, for the same reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import get_settings

from . import licensing

marketing_router = APIRouter(tags=["marketing"])

# Where the published vulnerability-disclosure policy lives (RFC 9116 "Policy").
SECURITY_POLICY_URL = (
    "https://github.com/rogerdemello/autonomous-executive-email-copilot/blob/main/SECURITY.md"
)


@marketing_router.get("/api/pricing", include_in_schema=True)
def pricing_json() -> JSONResponse:
    """Machine-readable pricing, sourced from the entitlement plan registry."""
    plans = [
        {
            "key": p.key,
            "name": p.name,
            "seats": p.seats,
            "features": list(p.features),
            "price_display": p.price_display,
        }
        for p in licensing.PLANS.values()
    ]
    return JSONResponse({"plans": plans})


@marketing_router.get(
    "/.well-known/security.txt", response_class=PlainTextResponse, include_in_schema=False
)
def security_txt() -> PlainTextResponse:
    """Serve an RFC 9116 security.txt at the well-known location.

    Generated rather than served from disk: a checked-in copy drifts silently
    from the deployment's real contact address and public URL, and RFC 9116
    requires ``Expires`` to stay in the future — a hardcoded date is a
    maintenance landmine that quietly invalidates the whole file.
    """
    settings = get_settings()
    contact = settings.sales_contact_email.replace("sales@", "security@")
    base = settings.resolved_app_public_url
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=365)
    body = (
        f"Contact: mailto:{contact}\n"
        "Preferred-Languages: en\n"
        f"Policy: {SECURITY_POLICY_URL}\n"
        f"Canonical: {base}/.well-known/security.txt\n"
        f"Expires: {expires.isoformat().replace('+00:00', 'Z')}\n"
    )
    return PlainTextResponse(body)
