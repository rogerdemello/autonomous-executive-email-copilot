"""Access grants: mint, verify, and reason about entitlements.

The motion is self-serve — sign up, get a 14-day trial, then talk to us to
keep your access. There is no card capture and **no price anywhere in this
codebase**: what a customer pays is a conversation, not a table. An operator
issues a signed **access key** to a customer via ``scripts/issue_license.py``.
The key is a self-contained signed token (same HS256 machinery as session
tokens) encoding the plan, seat count, feature flags, and expiry, so it can be
verified offline. A copy of its terms is also persisted (``License`` row) so a
key can be **revoked** and its seat limit enforced server-side.

Plan definitions live here so the entitlement checks have one source of truth.
They are an *internal* vocabulary for what a key grants — nothing renders them
to a visitor, and nothing should: naming a tier the visitor cannot look up is
worse than naming none.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import tokens

# Feature flags an entitlement can grant. Kept small and explicit.
FEATURE_APPROVALS = "approvals"
FEATURE_ANALYTICS = "analytics"
FEATURE_SSO = "sso"
FEATURE_AUDIT_LOG = "audit_log"
FEATURE_PRIORITY_SUPPORT = "priority_support"
FEATURE_CUSTOM_MODELS = "custom_models"


@dataclass(frozen=True)
class Plan:
    """What an access grant carries. ``seats`` is the default seat grant; a
    minted key may override it.

    Deliberately has no price or marketing blurb. Both used to live here and
    were referenced by nothing that renders — the fields survived the deletion
    of the pricing page and were the last place in the codebase that implied a
    published price list.
    """

    key: str
    name: str
    seats: int
    features: tuple[str, ...]


# Ordered from entry to enterprise. Seat counts on the larger grants are
# negotiated per customer and overridden at mint time.
PLANS: dict[str, Plan] = {
    # The trial is full-featured-minus-enterprise on purpose: an evaluation
    # that can't test SSO or read the audit trail isn't an evaluation. The
    # gate on a trial is the 14-day clock, which IS enforced (an expired
    # entitlement blocks syncing and approvals).
    "trial": Plan(
        key="trial",
        name="Trial",
        seats=3,
        features=(FEATURE_APPROVALS, FEATURE_ANALYTICS, FEATURE_AUDIT_LOG, FEATURE_SSO),
    ),
    "team": Plan(
        key="team",
        name="Team",
        seats=10,
        features=(FEATURE_APPROVALS, FEATURE_ANALYTICS),
    ),
    "business": Plan(
        key="business",
        name="Business",
        seats=50,
        features=(
            FEATURE_APPROVALS,
            FEATURE_ANALYTICS,
            FEATURE_AUDIT_LOG,
            FEATURE_SSO,
        ),
    ),
    "enterprise": Plan(
        key="enterprise",
        name="Enterprise",
        seats=1000,
        features=(
            FEATURE_APPROVALS,
            FEATURE_ANALYTICS,
            FEATURE_AUDIT_LOG,
            FEATURE_SSO,
            FEATURE_PRIORITY_SUPPORT,
            FEATURE_CUSTOM_MODELS,
        ),
    ),
}

DEFAULT_TRIAL_DAYS = 14
# A license with no negotiated end date is minted with a long, finite term so
# the underlying token always carries an ``exp`` (perpetual-until-renewal).
PERPETUAL_DAYS = 3650


class LicenseError(Exception):
    """Raised when a license key is malformed, mis-signed, or expired."""


@dataclass(frozen=True)
class Entitlement:
    """The verified terms a license grants. Immutable snapshot of a key."""

    key_id: str
    org_id: str
    plan: str
    seats: int
    features: tuple[str, ...] = field(default_factory=tuple)
    issued_at: int = 0
    expires_at: int = 0

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def seats_ok(self, active_users: int) -> bool:
        """True if ``active_users`` fits within the licensed seat count."""
        return active_users <= self.seats

    @property
    def expires_at_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, tz=timezone.utc).isoformat()

    @property
    def issued_at_iso(self) -> str:
        return datetime.fromtimestamp(self.issued_at, tz=timezone.utc).isoformat()


def resolve_plan(plan_key: str) -> Plan:
    plan = PLANS.get(plan_key)
    if plan is None:
        raise LicenseError(f"unknown plan: {plan_key!r}")
    return plan


def mint_license(
    org_id: str,
    plan: str,
    secret: str,
    *,
    seats: int | None = None,
    features: tuple[str, ...] | None = None,
    valid_days: int | None = None,
    now: float | None = None,
) -> tuple[str, Entitlement]:
    """Create a signed license key for ``org_id`` and return ``(key, terms)``.

    ``seats``/``features`` default to the plan's grant when omitted. ``valid_days``
    defaults to the trial length for the trial plan, otherwise a long finite term.
    """
    plan_def = resolve_plan(plan)
    effective_seats = plan_def.seats if seats is None else seats
    effective_features = plan_def.features if features is None else features
    if valid_days is None:
        valid_days = DEFAULT_TRIAL_DAYS if plan == "trial" else PERPETUAL_DAYS
    if effective_seats < 1:
        raise LicenseError("seats must be at least 1")

    key_id = uuid.uuid4().hex
    ttl_seconds = int(timedelta(days=valid_days).total_seconds())
    claims = {
        "typ": "license",
        "jti": key_id,
        "org": org_id,
        "plan": plan,
        "seats": effective_seats,
        "feat": list(effective_features),
    }
    key = tokens.encode(claims, secret, ttl_seconds=ttl_seconds, now=now)
    terms = verify_license_key(key, secret, now=now)
    return key, terms


def verify_license_key(key: str, secret: str, *, now: float | None = None) -> Entitlement:
    """Verify a license key's signature and expiry; return its terms.

    Raises :class:`LicenseError` on tampering, wrong secret, expiry, or if the
    token is not a license. Does NOT consult the database — revocation and seat
    enforcement are layered on by the caller via the persisted ``License`` row.
    """
    try:
        claims = tokens.decode(key, secret, now=now)
    except tokens.TokenError as exc:
        raise LicenseError(str(exc)) from exc
    if claims.get("typ") != "license":
        raise LicenseError("token is not a license key")
    try:
        return Entitlement(
            key_id=str(claims["jti"]),
            org_id=str(claims["org"]),
            plan=str(claims["plan"]),
            seats=int(claims["seats"]),
            features=tuple(claims.get("feat", [])),
            issued_at=int(claims.get("iat", 0)),
            expires_at=int(claims.get("exp", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseError(f"malformed license claims: {exc}") from exc
