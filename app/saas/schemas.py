"""Pydantic request/response models for the SaaS API surface."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from .models_db import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER

RoleLiteral = str  # validated against ROLES in the route layer for clearer errors


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(default="", max_length=255)
    org_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: dict
    organization: dict


class UserOut(BaseModel):
    id: str
    org_id: str
    email: str
    full_name: str
    role: str
    status: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    new_password: str = Field(min_length=8, max_length=256)


class InviteMemberRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: str = Field(default=ROLE_MEMBER)
    # Temporary password the invited user signs in with (foundation: no email
    # delivery yet). The invitee should change it on first login.
    temp_password: str = Field(min_length=8, max_length=256)


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(examples=[ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER])


class ActivateLicenseRequest(BaseModel):
    license_key: str = Field(min_length=10)


class DeleteOrgRequest(BaseModel):
    # Must equal the org's slug — a deliberate friction so deletion can't happen
    # by accident.
    confirm: str = Field(min_length=1)


class ContactSalesRequest(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=255)
    company: str = Field(default="", max_length=255)
    seats: int | None = Field(default=None, ge=1, le=100000)
    message: str = Field(default="", max_length=4000)
    kind: str = Field(default="contact_sales", max_length=32)


class EntitlementOut(BaseModel):
    plan: str
    seats: int
    seats_used: int
    features: list[str]
    status: str
    expires_at: str | None = None
    is_valid: bool


__all__ = [
    "SignupRequest",
    "LoginRequest",
    "TokenResponse",
    "UserOut",
    "ChangePasswordRequest",
    "InviteMemberRequest",
    "UpdateMemberRoleRequest",
    "ActivateLicenseRequest",
    "ContactSalesRequest",
    "EntitlementOut",
    "ROLE_OWNER",
    "ROLE_ADMIN",
    "ROLE_MEMBER",
]
