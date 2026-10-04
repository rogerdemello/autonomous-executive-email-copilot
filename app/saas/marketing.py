"""Machine-readable marketing endpoints.

The human-facing pages are templates in :mod:`app.web`. Acquisition is
self-serve — sign up, connect a mailbox, 14-day trial — and there is
deliberately no published price anywhere: continued access is arranged through
a conversation and granted as a signed key by the licensing/entitlement
system. What lives here is the machine-readable site files — ``security.txt``,
``robots.txt`` and ``sitemap.xml`` — all *generated* rather than checked in, so
none of them can drift from the deployment's real address or contact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from app.core.config import get_settings

marketing_router = APIRouter(tags=["marketing"])

# The public source. Linked from the site footer: the code is the evidence, and
# the licence is MIT.
REPO_URL = "https://github.com/rogerdemello/autonomous-executive-email-copilot"

# Where the published vulnerability-disclosure policy lives (RFC 9116 "Policy").
SECURITY_POLICY_URL = f"{REPO_URL}/blob/main/SECURITY.md"

# The private channel SECURITY.md promises. Used as the Contact when no
# SECURITY_CONTACT_EMAIL is configured: a working private channel beats a
# `security@` address derived from the sales one, which is only a guess about
# a mailbox that may not exist. (Needs "private vulnerability reporting"
# enabled on the repository, or the form 404s for anyone who is not a
# collaborator.)
SECURITY_ADVISORY_URL = f"{REPO_URL}/security/advisories/new"

# The public pages, in the order a first-time visitor meets them. Everything
# behind a sign-in is deliberately absent, here and from robots.txt.
SITEMAP_PATHS = ("/", "/demo", "/signup", "/contact-sales", "/privacy", "/terms")


def public_base_url(request: Request) -> str:
    """The origin this deployment is reached at, with no trailing slash.

    ``APP_PUBLIC_URL`` when the operator set it. Otherwise the request's own
    origin — *not* ``Settings.resolved_app_public_url``, whose last resort is
    ``http://localhost:8000``: an unconfigured deployment used to publish that as
    its canonical address, which is a link nobody else can open.
    """
    configured = (get_settings().app_public_url or "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


@marketing_router.get(
    "/.well-known/security.txt", response_class=PlainTextResponse, include_in_schema=False
)
def security_txt(request: Request) -> PlainTextResponse:
    """Serve an RFC 9116 security.txt at the well-known location.

    Generated rather than served from disk: a checked-in copy drifts silently
    from the deployment's real contact address and public URL, and RFC 9116
    requires ``Expires`` to stay in the future — a hardcoded date is a
    maintenance landmine that quietly invalidates the whole file.
    """
    settings = get_settings()
    security_email = settings.public_security_email
    contact = f"mailto:{security_email}" if security_email else SECURITY_ADVISORY_URL
    base = public_base_url(request)
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=365)
    body = (
        f"Contact: {contact}\n"
        "Preferred-Languages: en\n"
        f"Policy: {SECURITY_POLICY_URL}\n"
        f"Canonical: {base}/.well-known/security.txt\n"
        f"Expires: {expires.isoformat().replace('+00:00', 'Z')}\n"
    )
    return PlainTextResponse(body)


@marketing_router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots_txt(request: Request) -> PlainTextResponse:
    """Let crawlers read the public site and nothing behind a sign-in.

    ``/app`` and the JSON API are private by construction (every route 401s or
    redirects), so this is courtesy, not security: it keeps a sign-in redirect
    from being indexed as the page's content and keeps the API out of results.
    """
    base = public_base_url(request)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /app/\n"
        "Disallow: /auth/\n"
        "Disallow: /org/\n"
        "Disallow: /mailbox/\n"
        "Disallow: /inbox/\n"
        "Disallow: /operator\n"
        "Disallow: /docs\n"
        "Disallow: /openapi.json\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@marketing_router.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request) -> Response:
    """The public pages, with absolute URLs for the origin actually serving them."""
    base = public_base_url(request)
    demo_on = get_settings().demo_login_active
    urls = [path for path in SITEMAP_PATHS if demo_on or path != "/demo"]
    entries = "".join(f"  <url><loc>{base}{path}</loc></url>\n" for path in urls)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")
