"""Gating for the read-only email connectors.

Deliberately reads ``os.environ`` directly (not ``env.config``) so the connector
package stays dependency-light and trivially isolated. Off by default.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def email_connector_enabled() -> bool:
    """True only if EMAIL_CONNECTOR_ENABLED is explicitly set to a truthy value."""
    return os.environ.get("EMAIL_CONNECTOR_ENABLED", "").strip().lower() in _TRUTHY


def email_connector_provider() -> str:
    """The configured provider id (e.g. 'imap'); empty string if unset."""
    return os.environ.get("EMAIL_CONNECTOR_PROVIDER", "").strip().lower()
