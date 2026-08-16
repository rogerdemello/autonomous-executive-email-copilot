"""Authentication & onboarding service.

Orchestrates the repositories, password hashing, session tokens, and licensing
into the high-level operations the API exposes: sign up (provision org + owner +
trial license), authenticate, issue/verify tokens, and resolve the current user.
Business rules live here so route handlers stay thin.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from app.core.config import get_settings

from . import licensing, passwords, tokens
from .models_db import ROLE_OWNER
from .repository import (
    LicenseRepository,
    OrganizationRepository,
    UserRepository,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class AuthError(Exception):
    """Auth/onboarding failure with a safe, user-facing message and HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def slugify(name: str) -> str:
    """Turn an org name into a URL-safe slug base (no uniqueness guarantee)."""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "org"


class AuthService:
    def __init__(self) -> None:
        self.orgs = OrganizationRepository()
        self.users = UserRepository()
        self.licenses = LicenseRepository()

    # -- onboarding ---------------------------------------------------------
    def signup(
        self, *, email: str, password: str, full_name: str, org_name: str
    ) -> tuple[dict, dict, dict]:
        """Provision a new org with an owner user and a trial license.

        Returns ``(user, organization, entitlement_terms)``. Raises
        :class:`AuthError` if signup is disabled or the email is taken.
        """
        settings = get_settings()
        if not settings.signup_enabled:
            raise AuthError("Self-serve signup is disabled; contact sales.", 403)

        email = email.lower().strip()
        if self.users.email_exists(email):
            raise AuthError("An account with this email already exists.", 409)

        org = self.orgs.create(name=org_name.strip(), slug=self._unique_slug(org_name))
        user = self.users.create(
            org_id=org["id"],
            email=email,
            password_hash=passwords.hash_password(password),
            full_name=full_name.strip(),
            role=ROLE_OWNER,
        )
        # Every new org starts on a time-boxed trial entitlement.
        _key, terms = licensing.mint_license(org["id"], "trial", settings.resolved_auth_secret)
        self.licenses.upsert(
            org_id=org["id"],
            key_id=terms.key_id,
            plan=terms.plan,
            seats=terms.seats,
            features=list(terms.features),
            expires_at_iso=terms.expires_at_iso,
        )
        return user, org, terms.__dict__

    def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        suffix = 2
        while self.orgs.slug_exists(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def login_or_provision_sso(self, *, email: str, full_name: str) -> dict:
        """Resolve an SSO-verified identity to a user, provisioning on first sight.

        Existing user -> logged in. New user with self-serve signup enabled ->
        a fresh org (named from the email domain) + owner + trial. New user with
        signup disabled -> rejected (an operator must invite them first).
        """
        settings = get_settings()
        email = email.lower().strip()
        record = self.users.get_by_email_global(email)
        if record:
            if record.get("status") == "disabled":
                raise AuthError("This account has been disabled.", 403)
            self.users.touch_login(record["id"])
            record.pop("password_hash", None)
            return record

        if not settings.signup_enabled:
            raise AuthError("No account for this identity; ask an admin to invite you.", 403)

        domain = email.split("@", 1)[-1].split(".")[0] if "@" in email else "workspace"
        org = self.orgs.create(name=domain.capitalize(), slug=self._unique_slug(domain))
        # SSO users authenticate via the IdP; store an unusable random password.
        user = self.users.create(
            org_id=org["id"],
            email=email,
            password_hash=passwords.hash_password(uuid.uuid4().hex + uuid.uuid4().hex),
            full_name=(full_name or "").strip(),
            role=ROLE_OWNER,
        )
        _key, terms = licensing.mint_license(org["id"], "trial", settings.resolved_auth_secret)
        self.licenses.upsert(
            org_id=org["id"],
            key_id=terms.key_id,
            plan=terms.plan,
            seats=terms.seats,
            features=list(terms.features),
            expires_at_iso=terms.expires_at_iso,
        )
        return user

    # -- authentication -----------------------------------------------------
    def authenticate(self, *, email: str, password: str) -> dict:
        """Verify credentials and return the (sanitized) user, or raise.

        Uses a uniform error for both "no such user" and "wrong password" so the
        endpoint doesn't reveal which emails are registered.
        """
        record = self.users.get_by_email_global(email)
        invalid = AuthError("Invalid email or password.", 401)
        if not record:
            # Still run a hash to keep timing roughly uniform against enumeration.
            passwords.verify_password(password, passwords.hash_password("decoy"))
            raise invalid
        if record.get("status") == "disabled":
            raise AuthError("This account has been disabled.", 403)
        if not passwords.verify_password(password, record["password_hash"]):
            raise invalid
        self.users.touch_login(record["id"])
        record.pop("password_hash", None)
        return record

    # -- tokens -------------------------------------------------------------
    def issue_token(self, user: dict) -> tuple[str, int]:
        """Mint a session token for ``user``; return ``(token, expires_in_secs)``."""
        settings = get_settings()
        ttl = int(settings.access_token_ttl_minutes) * 60
        token = tokens.encode(
            {"sub": user["id"], "org": user["org_id"], "role": user["role"]},
            settings.resolved_auth_secret,
            ttl_seconds=ttl,
        )
        return token, ttl

    def resolve(self, token: str) -> dict:
        """Verify a session token and return the live user row, or raise.

        Re-reads the user from the DB (rather than trusting token claims) so role
        changes and disablement take effect without waiting for token expiry.
        """
        settings = get_settings()
        try:
            claims = tokens.decode(token, settings.resolved_auth_secret)
        except tokens.TokenError as exc:
            raise AuthError("Invalid or expired session.", 401) from exc
        if claims.get("typ") == "license":
            raise AuthError("A license key is not a session token.", 401)
        org_id = claims.get("org")
        user_id = claims.get("sub")
        if not org_id or not user_id:
            raise AuthError("Malformed session token.", 401)
        user = self.users.get(org_id, user_id)
        if not user:
            raise AuthError("Account no longer exists.", 401)
        if user.get("status") == "disabled":
            raise AuthError("This account has been disabled.", 403)
        return user

    def change_password(self, user: dict, current: str, new: str) -> None:
        record = self.users.get_by_email_global(user["email"])
        if not record or not passwords.verify_password(current, record["password_hash"]):
            raise AuthError("Current password is incorrect.", 400)
        self.users.set_password(user["org_id"], user["id"], passwords.hash_password(new))

    # -- password reset -----------------------------------------------------
    def request_password_reset(self, email: str) -> str | None:
        """Return a signed, short-lived reset token if the email exists, else None.

        The caller must return a uniform 200 either way so the endpoint doesn't
        reveal which emails are registered.
        """
        record = self.users.get_by_email_global(email)
        if not record:
            return None
        settings = get_settings()
        ttl = int(settings.password_reset_ttl_minutes) * 60
        return tokens.encode(
            {"typ": "password_reset", "sub": record["id"], "org": record["org_id"]},
            settings.resolved_auth_secret,
            ttl_seconds=ttl,
        )

    def reset_password(self, token: str, new_password: str) -> dict:
        """Verify a reset token and set the new password. Returns the user."""
        settings = get_settings()
        try:
            claims = tokens.decode(token, settings.resolved_auth_secret)
        except tokens.TokenError as exc:
            raise AuthError("This reset link is invalid or has expired.", 400) from exc
        if claims.get("typ") != "password_reset":
            raise AuthError("This is not a password-reset link.", 400)
        org_id = claims.get("org")
        user_id = claims.get("sub")
        user = self.users.get(org_id, user_id) if org_id and user_id else None
        if not user:
            raise AuthError("Account no longer exists.", 400)
        self.users.set_password(org_id, user_id, passwords.hash_password(new_password))
        return user
