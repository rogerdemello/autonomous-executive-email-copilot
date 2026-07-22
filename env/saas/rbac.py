"""Role-based access control for organization members.

Three roles, strictly ordered by privilege. Every permission check reduces to
"does this member's role rank at or above the required role", which keeps the
model small and auditable.
"""

from __future__ import annotations

from .models_db import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER

# Higher number = more privilege.
_RANK = {ROLE_MEMBER: 1, ROLE_ADMIN: 2, ROLE_OWNER: 3}


def rank(role: str) -> int:
    return _RANK.get(role, 0)


def role_at_least(role: str, required: str) -> bool:
    """True if ``role`` is at least as privileged as ``required``."""
    return rank(role) >= rank(required)


def can_manage_members(role: str) -> bool:
    """Invite, change roles of, and remove members (admin or owner)."""
    return role_at_least(role, ROLE_ADMIN)


def can_manage_billing(role: str) -> bool:
    """Activate licenses and view billing (owner only — it's a financial action)."""
    return role_at_least(role, ROLE_OWNER)


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """A member may only grant a role they themselves rank above-or-equal to,
    and only managers may assign roles at all. This prevents privilege
    escalation (an admin cannot mint an owner)."""
    if not can_manage_members(actor_role):
        return False
    return rank(actor_role) >= rank(target_role)


__all__ = [
    "ROLE_OWNER",
    "ROLE_ADMIN",
    "ROLE_MEMBER",
    "rank",
    "role_at_least",
    "can_manage_members",
    "can_manage_billing",
    "can_assign_role",
]
