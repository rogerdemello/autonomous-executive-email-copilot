"""Staged, READ-ONLY real-inbox connectors (off by default).

This package maps real mail into the un-privileged ``Observation``/``ObservationEmail``
schema so an agent can be pointed at a real inbox. It is deliberately isolated:

* It NEVER imports ``env.grader`` or ``env.environment``, and they never import it —
  so it cannot touch grading or determinism. (Guarded by tests/test_connector_isolation.py.)
* It emits ONLY ``Observation``/``ObservationEmail`` — never ``EmailRecord`` or the gold
  ``expected_*`` fields — so real mail is ungradeable by construction (INV-5).
* It is read-only: connectors expose no send/delete/mutate methods.
* It is gated behind ``EMAIL_CONNECTOR_ENABLED`` (default off); the benchmark refuses
  to run with it enabled, keeping the benchmark sim-only by construction.
"""

from .base import RawEmail, ReadOnlyEmailConnector
from .config import email_connector_enabled, email_connector_provider
from .mapping import to_observation, to_observation_email

__all__ = [
    "RawEmail",
    "ReadOnlyEmailConnector",
    "email_connector_enabled",
    "email_connector_provider",
    "to_observation",
    "to_observation_email",
]
