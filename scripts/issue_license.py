#!/usr/bin/env python
"""Issue a signed license key for a customer organization (sales ops tool).

Billing is sales-led: after a contract is signed, an operator runs this to mint
the customer's license key and (optionally) persist the license row so it can be
revoked later. The key is handed to the customer, who activates it via
``POST /billing/activate-license`` or the in-app billing settings page.

The key is signed with ``AUTH_SECRET_KEY`` — it MUST match the running server's
secret, or activation will fail signature verification.

NOTE: against a deployed instance, use the operator API instead — the server
already holds the production signing secret and database, so nothing secret
has to leave the box:

    curl -X POST https://<app>/operator/licenses \
      -H "Authorization: Bearer $OPERATOR_TOKEN" \
      -d '{"org_id": "<org_id>", "plan": "business", "valid_days": 365}'

This script remains the local/dev path (and works wherever your shell's
DATABASE_URL and AUTH_SECRET_KEY point at the target instance's).

Examples
--------
    # Mint a 1-year Business license for org <id> and print the key
    python scripts/issue_license.py --org <org_id> --plan business --valid-days 365

    # Override seats and persist the license row for revocation tracking
    python scripts/issue_license.py --org <org_id> --plan enterprise --seats 250 --persist
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running a script puts its own directory on sys.path, not the repo root, so the
# first-party packages are not importable. Add the repo root explicitly to keep
# this entrypoint runnable directly (`python scripts/issue_license.py`).
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.core.config import get_settings  # noqa: E402
from app.saas import licensing  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a signed license key.")
    parser.add_argument("--org", required=True, help="Organization id the key is bound to")
    parser.add_argument(
        "--plan",
        required=True,
        choices=sorted(licensing.PLANS),
        help="Plan tier",
    )
    parser.add_argument("--seats", type=int, default=None, help="Override the plan's seat count")
    parser.add_argument(
        "--valid-days",
        type=int,
        default=None,
        help="License term in days (defaults: trial=14, others=long-term)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Also write the license row to the DB (enables revocation/seat checks)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.auth_secret_is_dev:
        print(
            "WARNING: AUTH_SECRET_KEY is not set — minting with the insecure dev "
            "secret. The key will only validate against a server using the same "
            "dev secret. Set AUTH_SECRET_KEY for real keys.",
            file=sys.stderr,
        )

    key, terms = licensing.mint_license(
        args.org,
        args.plan,
        settings.resolved_auth_secret,
        seats=args.seats,
        valid_days=args.valid_days,
    )

    if args.persist:
        from app.core.db import migrate_db
        from app.saas.repository import LicenseRepository

        migrate_db()
        LicenseRepository().upsert(
            org_id=terms.org_id,
            key_id=terms.key_id,
            plan=terms.plan,
            seats=terms.seats,
            features=list(terms.features),
            expires_at_iso=terms.expires_at_iso,
        )

    print("License issued")
    print(f"  org_id   : {terms.org_id}")
    print(f"  plan     : {terms.plan}")
    print(f"  seats    : {terms.seats}")
    print(f"  features : {', '.join(terms.features) or '—'}")
    print(f"  expires  : {terms.expires_at_iso}")
    print(f"  key_id   : {terms.key_id}")
    print(f"  persisted: {bool(args.persist)}")
    print()
    print("License key (give this to the customer):")
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
