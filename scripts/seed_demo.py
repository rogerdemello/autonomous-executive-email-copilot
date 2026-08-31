#!/usr/bin/env python3
"""Seed a ready-to-present demo workspace.

Creates the Northwind Industries organization with an owner account, attaches
the demo mailbox, and runs the first sync — so a fresh clone goes from `git
clone` to a populated inbox in one command.

Idempotent: run it as often as you like. If the workspace already exists it is
reset to a clean state (processed messages and pending actions cleared, then
re-synced) rather than duplicated, which is what you want between rehearsals.

    python scripts/seed_demo.py            # seed or reset
    python scripts/seed_demo.py --fresh    # also delete the org first

Nothing here needs network access, an API key, or OAuth credentials.

Thin wrapper over :func:`app.saas.demo_seed.seed_demo` — the same code path a
deployed instance runs at startup (``DEMO_SEED_ON_STARTUP=true``) and behind
``POST /operator/demo/reseed``, so a local rehearsal and the public demo can
never drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.saas.demo_seed import (  # noqa: E402
    DEMO_OWNER_EMAIL,
    DEMO_OWNER_PASSWORD,
    seed_demo,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="delete the demo organization first, then recreate it from scratch",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help=(
            "call the configured LLM to write the reply and escalation prose, and "
            "commit it to data/demo/drafts.json. Run this once, with a key and a "
            "network; every later run replays those drafts from disk."
        ),
    )
    args = parser.parse_args()

    if args.with_llm:
        from app.core.config import get_settings

        if not get_settings().provider_available:
            print(
                "--with-llm needs a provider credential (OPENAI_API_KEY, "
                "ANTHROPIC_API_KEY, GOOGLE_API_KEY or OLLAMA_BASE_URL).",
                file=sys.stderr,
            )
            return 1
        print("Drafting with the model — this makes network calls and costs money…")

    summary = seed_demo(fresh=args.fresh, live_llm=args.with_llm)

    print(
        f"Triaged {summary['messages']} messages: "
        f"{summary['auto_executed']} applied automatically, "
        f"{summary['pending']} held for approval"
    )
    _report_draft_sources(summary["pending_actions"])
    print()
    print("  Demo workspace ready.")
    print("    uvicorn app.main:app --reload --port 8000")
    print("    http://localhost:8000/login")
    print(f"    {DEMO_OWNER_EMAIL} / {DEMO_OWNER_PASSWORD}")
    return 0


def _report_draft_sources(actions: list) -> None:
    """Say where the held prose came from — the thing worth knowing before a demo."""
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.get("draft_source") or "generic"] = (
            counts.get(action.get("draft_source") or "generic", 0) + 1
        )
    if not counts:
        return
    labels = {
        "llm": "model-written",
        "authored": "authored fixture prose",
        "generic": "the policy's generic sentence",
    }
    summary = ", ".join(
        f"{count} {labels.get(source, source)}" for source, count in sorted(counts.items())
    )
    print(f"  Drafts: {summary}")
    if not counts.get("llm"):
        print("  (no cached model drafts — run once with --with-llm to generate them)")


if __name__ == "__main__":
    raise SystemExit(main())
