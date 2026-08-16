"""Commercial SaaS layer: accounts, organizations (tenants), auth, and licensing.

This package turns the single-tenant product demo into a multi-tenant,
sales-led SaaS. It is *additive*: the benchmark, scoring contract, and the
existing unversioned API surface are untouched, and every table here defaults
its tenant column so pre-existing rows and deterministic benchmark runs remain
byte-identical.

Modules:
- ``passwords``  — stdlib PBKDF2 password hashing.
- ``tokens``     — stdlib signed (HS256) session tokens, no external JWT dep.
- ``models_db``  — SQLAlchemy tables (Organization, User, License, AuditLog).
- ``licensing``  — sales-led license-key mint/verify + entitlement checks.
- ``repository`` — tenant-aware data access for orgs/users/licenses.
- ``rbac``       — roles and permission checks.
- ``auth``       — signup/login/current-user service.
- ``deps``       — FastAPI dependencies (current user, role guards).
- ``schemas``    — request/response pydantic models.
- ``routes``     — /auth, /org, /billing APIRouter.
"""

from __future__ import annotations

__all__ = [
    "passwords",
    "tokens",
    "licensing",
]
