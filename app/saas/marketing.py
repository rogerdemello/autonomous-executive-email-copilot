"""Machine-readable marketing endpoints.

The human-facing landing page is a template in :mod:`app.web`. Pricing is
deliberately not published anywhere — the product is sales-led, and plans are
granted through the licensing/entitlement system, not a public price list.
What stays here is ``security.txt``, which is generated rather than checked in
so it can never drift from the deployment's real contact address.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings

marketing_router = APIRouter(tags=["marketing"])

# Where the published vulnerability-disclosure policy lives (RFC 9116 "Policy").
SECURITY_POLICY_URL = (
    "https://github.com/rogerdemello/autonomous-executive-email-copilot/blob/main/SECURITY.md"
)


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
