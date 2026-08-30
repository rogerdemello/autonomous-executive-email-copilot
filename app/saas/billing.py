"""Sales-led billing service: license activation, entitlement, and lead capture.

There is no self-serve card capture. A customer receives a signed license key
from sales and *activates* it against their org; entitlement is then the
intersection of (the key's signed terms) and (the persisted license row's
status). "Contact sales" / license-request leads are captured here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import get_settings

from . import licensing
from .repository import (
    AuditRepository,
    LicenseRepository,
    SalesLeadRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class BillingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BillingService:
    def __init__(self) -> None:
        self.licenses = LicenseRepository()
        self.users = UserRepository()
        self.audit = AuditRepository()
        self.leads = SalesLeadRepository()

    def activate_license(self, *, org_id: str, license_key: str, actor_user_id: str) -> dict:
        """Verify and bind a license key to ``org_id``. Returns the entitlement."""
        settings = get_settings()
        try:
            terms = licensing.verify_license_key(license_key, settings.resolved_auth_secret)
        except licensing.LicenseError as exc:
            raise BillingError(f"Invalid license key: {exc}", 400) from exc

        if terms.org_id != org_id:
            # Keys are bound to a single org at mint time.
            raise BillingError("This license key was issued for a different organization.", 403)

        existing = self.licenses.get_by_key_id(terms.key_id)
        if existing and existing.get("status") == "revoked":
            raise BillingError("This license key has been revoked.", 403)

        self.licenses.upsert(
            org_id=org_id,
            key_id=terms.key_id,
            plan=terms.plan,
            seats=terms.seats,
            features=list(terms.features),
            expires_at_iso=terms.expires_at_iso,
        )
        self.audit.record(
            action="license.activate",
            org_id=org_id,
            actor_user_id=actor_user_id,
            target=terms.key_id,
            detail={"plan": terms.plan, "seats": terms.seats},
        )
        return self.current_entitlement(org_id)

    def current_entitlement(self, org_id: str) -> dict:
        """Compute the org's live entitlement (plan, seats, features, validity)."""
        row = self.licenses.get_active_for_org(org_id)
        seats_used = self.users.count_active_for_org(org_id)
        if not row:
            return {
                "plan": "none",
                "seats": 0,
                "seats_used": seats_used,
                "features": [],
                "status": "none",
                "expires_at": None,
                "is_valid": False,
            }
        expires = _parse_iso(row.get("expires_at"))
        now = datetime.now(timezone.utc)
        expired = expires is not None and now >= expires
        status = row.get("status", "active")
        is_valid = status == "active" and not expired
        return {
            "plan": row["plan"],
            "seats": row["seats"],
            "seats_used": seats_used,
            "features": row.get("features", []),
            "status": "expired" if expired and status == "active" else status,
            "expires_at": row.get("expires_at"),
            "is_valid": is_valid,
        }

    def has_seat_available(self, org_id: str) -> bool:
        ent = self.current_entitlement(org_id)
        if not ent["is_valid"]:
            return False
        return ent["seats_used"] < ent["seats"]

    def require_active(self, org_id: str) -> dict:
        """The org's entitlement, or a 402 :class:`BillingError` if lapsed.

        Gates the value loop (sync, approve) — never sign-in or settings, which
        an admin needs precisely when the plan has expired.
        """
        ent = self.current_entitlement(org_id)
        if not ent["is_valid"]:
            raise BillingError(
                "Your plan has expired. Activate a license (Settings) or contact "
                "sales to continue.",
                402,
            )
        return ent

    def require_feature(self, org_id: str, feature: str) -> None:
        """Raise a 403 :class:`BillingError` unless the plan grants ``feature``."""
        ent = self.current_entitlement(org_id)
        if feature not in ent.get("features", []):
            raise BillingError(
                f"This feature ({feature}) is not included in your current plan. "
                "Contact sales to upgrade.",
                403,
            )

    def capture_lead(
        self,
        *,
        email: str,
        kind: str = "contact_sales",
        name: str | None = None,
        company: str | None = None,
        seats: int | None = None,
        message: str | None = None,
        org_id: str | None = None,
    ) -> dict:
        lead = self.leads.create(
            email=email,
            kind=kind,
            name=name,
            company=company,
            seats=seats,
            message=message,
            org_id=org_id,
        )
        self._notify_sales(lead)
        return lead

    def _notify_sales(self, lead: dict) -> None:
        """Best-effort out-of-band notification. Never raises into the request.

        The webhook POST runs on a daemon thread: it happens inside the
        prospect's form submission, and a slow Slack endpoint must cost them
        nothing — the lead is already persisted either way.
        """
        settings = get_settings()
        logger.info(
            "sales_lead captured kind=%s email=%s company=%s seats=%s",
            lead.get("kind"),
            lead.get("email"),
            lead.get("company"),
            lead.get("seats"),
        )
        webhook = (settings.sales_webhook_url or "").strip()
        if not webhook:
            return

        def _post() -> None:
            try:
                import httpx

                text = (
                    f":moneybag: *New {lead.get('kind')} lead*\n"
                    f"• Email: {lead.get('email')}\n"
                    f"• Name: {lead.get('name') or '—'}\n"
                    f"• Company: {lead.get('company') or '—'}\n"
                    f"• Seats: {lead.get('seats') or '—'}\n"
                    f"• Message: {lead.get('message') or '—'}"
                )
                httpx.post(webhook, json={"text": text}, timeout=5.0)
            except Exception:  # pragma: no cover - notification is best-effort
                logger.warning("Failed to post sales lead to webhook", exc_info=True)

        import threading

        threading.Thread(target=_post, name="sales-lead-webhook", daemon=True).start()


__all__ = ["BillingService", "BillingError"]
