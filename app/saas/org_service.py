"""Organization membership operations, shared by the JSON API and the web UI.

Inviting, re-roling, and removing members carries real rules — seat limits,
role-ceiling checks, the last-owner guard — and both surfaces must enforce all
of them identically. The rules live here once; the routers only translate
:class:`OrgError` into their surface's error shape (JSON detail vs error page).
"""

from __future__ import annotations

from app.core.config import get_settings

from . import passwords, rbac
from .billing import BillingService
from .email import send_email
from .models_db import ROLE_OWNER, ROLES
from .repository import AuditRepository, OrganizationRepository, UserRepository


class OrgError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class OrgService:
    def __init__(self) -> None:
        self.users = UserRepository()
        self.orgs = OrganizationRepository()
        self.audit = AuditRepository()
        self.billing = BillingService()

    def invite_member(
        self,
        *,
        actor: dict,
        email: str,
        full_name: str,
        role: str,
        temp_password: str,
        ip: str | None = None,
    ) -> dict:
        """Create a member, email their credentials, audit. Returns the member."""
        if role not in ROLES:
            raise OrgError(f"role must be one of {ROLES}", 422)
        if not rbac.can_assign_role(actor["role"], role):
            raise OrgError("Cannot assign a role above your own", 403)

        org_id = actor["org_id"]
        if not self.billing.has_seat_available(org_id):
            raise OrgError(
                "No seats available on your current plan. Contact sales to add seats.", 402
            )
        if self.users.email_exists(email):
            raise OrgError("A user with this email already exists", 409)

        member = self.users.create(
            org_id=org_id,
            email=email,
            password_hash=passwords.hash_password(temp_password),
            full_name=full_name,
            role=role,
        )
        org = self.orgs.get(org_id)
        org_name = org["name"] if org else "your team"
        login_url = f"{get_settings().resolved_app_public_url}/login"
        send_email(
            member["email"],
            f"You've been invited to {org_name} on Executive Email Copilot",
            f"{actor.get('email', 'A teammate')} invited you to {org_name}.\n\n"
            f"Sign in at {login_url} with:\n"
            f"  Email: {member['email']}\n"
            f"  Temporary password: {temp_password}\n\n"
            "Please change your password after your first sign-in.",
        )
        self.audit.record(
            action="member.invite",
            org_id=org_id,
            actor_user_id=actor["id"],
            target=member["id"],
            detail={"email": member["email"], "role": role},
            ip=ip,
        )
        return member

    def change_member_role(
        self, *, actor: dict, member_id: str, role: str, ip: str | None = None
    ) -> dict:
        if role not in ROLES:
            raise OrgError(f"role must be one of {ROLES}", 422)
        if not rbac.can_assign_role(actor["role"], role):
            raise OrgError("Cannot assign a role above your own", 403)

        org_id = actor["org_id"]
        target = self.users.get(org_id, member_id)
        if not target:
            raise OrgError("Member not found", 404)
        # Guard against demoting the last owner (would orphan the org's billing).
        if target["role"] == ROLE_OWNER and role != ROLE_OWNER:
            owners = [m for m in self.users.list_for_org(org_id) if m["role"] == ROLE_OWNER]
            if len(owners) <= 1:
                raise OrgError("An organization must keep at least one owner", 409)

        updated = self.users.update_role(org_id, member_id, role)
        self.audit.record(
            action="member.role_change",
            org_id=org_id,
            actor_user_id=actor["id"],
            target=member_id,
            detail={"new_role": role},
            ip=ip,
        )
        return updated

    def remove_member(self, *, actor: dict, member_id: str, ip: str | None = None) -> None:
        org_id = actor["org_id"]
        if member_id == actor["id"]:
            raise OrgError("You cannot remove yourself", 409)
        target = self.users.get(org_id, member_id)
        if not target:
            raise OrgError("Member not found", 404)
        if target["role"] == ROLE_OWNER:
            owners = [m for m in self.users.list_for_org(org_id) if m["role"] == ROLE_OWNER]
            if len(owners) <= 1:
                raise OrgError("An organization must keep at least one owner", 409)
        self.users.delete(org_id, member_id)
        self.audit.record(
            action="member.remove",
            org_id=org_id,
            actor_user_id=actor["id"],
            target=member_id,
            ip=ip,
        )


__all__ = ["OrgService", "OrgError"]
