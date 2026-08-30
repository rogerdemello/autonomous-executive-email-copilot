"""Operator-side workspace provisioning.

The one code path that creates an organization with an owner and a starting
license, shared by three callers with different gates:

- self-serve signup (``AuthService.signup``) — gated on ``SIGNUP_ENABLED``;
- the operator API (``POST /operator/orgs``) — gated on the operator token,
  works regardless of the signup flag (that is the point of sales-led);
- the demo seeder (``app.saas.demo_seed``) — must succeed on a production
  deployment where signup is off.

Extracted from ``AuthService.signup`` rather than duplicated so a future rule
(e.g. a welcome audit event, a default team) lands in every path at once.
"""

from __future__ import annotations

import secrets

from app.core.config import get_settings

from . import licensing, passwords
from .auth import AuthError, slugify
from .models_db import ROLE_OWNER
from .repository import LicenseRepository, OrganizationRepository, UserRepository


def unique_slug(orgs: OrganizationRepository, name: str) -> str:
    base = slugify(name)
    candidate = base
    suffix = 2
    while orgs.slug_exists(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def provision_org(
    *,
    org_name: str,
    owner_email: str,
    owner_name: str = "",
    password: str | None = None,
    plan: str = "trial",
    seats: int | None = None,
    valid_days: int | None = None,
) -> dict:
    """Create an org + owner + persisted starting license.

    Returns ``{"organization", "owner", "temp_password", "entitlement"}``.
    ``temp_password`` is set only when no password was supplied (a generated
    credential the operator hands to the customer — it appears exactly once,
    in this return value). Raises :class:`AuthError` on a taken email or an
    unknown plan.
    """
    orgs = OrganizationRepository()
    users = UserRepository()
    licenses = LicenseRepository()

    email = owner_email.lower().strip()
    if not email or "@" not in email:
        raise AuthError("A valid owner email is required.", 400)
    if users.email_exists(email):
        raise AuthError("An account with this email already exists.", 409)

    try:
        licensing.resolve_plan(plan)
    except licensing.LicenseError as exc:
        raise AuthError(str(exc), 400) from exc

    temp_password: str | None = None
    if not password:
        temp_password = secrets.token_urlsafe(12)
        password = temp_password

    org = orgs.create(name=org_name.strip() or "Workspace", slug=unique_slug(orgs, org_name))
    user = users.create(
        org_id=org["id"],
        email=email,
        password_hash=passwords.hash_password(password),
        full_name=owner_name.strip(),
        role=ROLE_OWNER,
    )
    _key, terms = licensing.mint_license(
        org["id"],
        plan,
        get_settings().resolved_auth_secret,
        seats=seats,
        valid_days=valid_days,
    )
    licenses.upsert(
        org_id=org["id"],
        key_id=terms.key_id,
        plan=terms.plan,
        seats=terms.seats,
        features=list(terms.features),
        expires_at_iso=terms.expires_at_iso,
    )
    return {
        "organization": org,
        "owner": user,
        "temp_password": temp_password,
        "entitlement": terms.__dict__,
    }
