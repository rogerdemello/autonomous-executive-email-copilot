"""Generate (and verify) the benchmark artifact the landing page renders.

The landing page's proof section sits under the heading "Measured, not
guessed." It used to be hardcoded ``<td>`` values and literal ``--v:`` bar
widths — the numbers happened to be right, but nothing connected them to the
benchmark, so nothing would have noticed if they drifted, and the heading was
a claim about a file that did not exist.

This writes ``data/landing_metrics.json``: the small, purpose-shaped artifact
the ``/`` route loads and the template renders rows and bar widths from.

Two kinds of column live in it, and the difference is recorded per agent
rather than glossed over:

- **Deterministic agents** (``baseline``, ``multiagent``) need no API key and
  no network. They are regenerated here and re-verified by ``--check`` in CI,
  so a policy change that moves a score turns the build red instead of
  quietly making the landing page wrong.
- **The ``llm`` agent** needs a real provider key and real money. It is only
  run when explicitly selected *and* a key is configured. Otherwise its cells
  are carried over verbatim from the existing artifact, which records when
  they were measured and against which model. ``--check`` never re-runs it;
  a recorded measurement is not a reproducible one and the artifact says so.

Usage::

    python scripts/build_landing_metrics.py                 # regenerate offline columns
    python scripts/build_landing_metrics.py --check         # CI: verify, write nothing
    python scripts/build_landing_metrics.py --agents baseline multiagent llm
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from statistics import mean

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.core.paths import DATA_ROOT  # noqa: E402
from research.benchmark.runner import BenchmarkRunner  # noqa: E402

ARTIFACT_PATH = DATA_ROOT / "landing_metrics.json"

SCHEMA_VERSION = 1

# The grid the published numbers describe. Deliberately pinned here rather
# than taken from the runner's defaults: the recorded `llm` column was
# measured on exactly this grid, and silently widening the seed set would make
# three of the four columns describe a different experiment from the fourth.
TASKS = [
    ("easy_classification", "Classification"),
    ("medium_prioritization", "Prioritization"),
    ("hard_full_management", "Full management"),
]
PERSONAS = ["strict_ceo", "balanced", "chill_manager"]
SEEDS = [42, 43, 44]

# Display labels for the columns, in table order.
AGENT_LABELS = {
    "baseline": "Heuristic",
    "multiagent": "Multi-agent",
    "reflective": "Reflective",
    "llm": "LLM (gpt-4o)",
}

# Agents that run offline and deterministically, and are therefore verifiable.
DETERMINISTIC = ("baseline", "multiagent", "reflective")

# Scores are floats from a deterministic simulation, so they compare exactly in
# practice; the tolerance only absorbs platform float formatting.
TOLERANCE = 5e-4


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def measure(agents: list[str]) -> dict[str, dict[str, dict]]:
    """Run ``agents`` over the pinned grid. Returns ``{agent: {task: cell}}``."""
    runner = BenchmarkRunner(
        tasks=[task_id for task_id, _ in TASKS], personas=PERSONAS, seeds=SEEDS
    )
    measured: dict[str, dict[str, dict]] = {}
    for agent in agents:
        results = runner.run_agent(agent)
        by_task: dict[str, dict] = {}
        for task_id, _label in TASKS:
            group = [r for r in results if r.task_id == task_id]
            by_task[task_id] = {
                "score": round(mean(r.metrics.score for r in group), 4),
                "n": len(group),
                "cost_usd": round(mean(r.metrics.cost_usd for r in group), 6),
            }
        measured[agent] = by_task
    return measured


def measure_demo_workspace() -> dict:
    """Run the real policy over the shipped demo mailbox and count what it did.

    The landing page's "A working day" timeline used to assert "38 messages
    classified ... three marked critical" — numbers that described no run of
    anything. These come from ``data/demo/inbox.json`` put through the same
    ``enrich -> policy -> proposals`` path a connected Gmail account takes, so
    editing a fixture subject line changes both the demo and the page.
    """
    from collections import Counter

    from app.copilot import enrich, pipeline
    from app.copilot.providers.demo import DemoProvider, demo_account_email

    provider = DemoProvider()
    messages = provider.fetch_messages(limit=10_000)
    observation = enrich.to_observation(messages, account_email=demo_account_email())
    proposals = pipeline.to_proposals(pipeline.run_policy(observation))

    labels = Counter(p.label for p in proposals if p.action_type == "classify")
    kinds = Counter(p.action_type for p in proposals)
    return {
        "messages": len(messages),
        "classified": kinds.get("classify", 0),
        "urgent": labels.get("urgent", 0),
        "spam_filed": labels.get("spam", 0),
        "deferred": kinds.get("defer", 0),
        "replies_drafted": kinds.get("reply", 0),
        "escalated": kinds.get("escalate", 0),
        "held_for_approval": sum(1 for p in proposals if p.requires_approval),
        "auto_applied": sum(1 for p in proposals if not p.requires_approval),
    }


def load_artifact(path=ARTIFACT_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _existing_columns() -> dict[str, dict]:
    """Columns already in the artifact, keyed by agent. Empty if there is none."""
    try:
        return {col["agent"]: col for col in load_artifact()["columns"]}
    except (OSError, KeyError, ValueError):
        return {}


def build(agents: list[str]) -> dict:
    """Build the full artifact, carrying over any column not being re-measured."""
    existing = _existing_columns()
    measured = measure(agents)

    columns = []
    for agent in AGENT_LABELS:
        if agent in measured:
            columns.append(
                {
                    "agent": agent,
                    "label": AGENT_LABELS[agent],
                    "deterministic": agent in DETERMINISTIC,
                    "measured_at": _now(),
                    "cells": measured[agent],
                }
            )
        elif agent in existing:
            # Not re-measured this run (no key, or not selected). Keep it, with
            # its own recorded provenance untouched.
            columns.append(existing[agent])

    llm = next((c for c in columns if c["agent"] == "llm"), None)
    hard_cost = (llm or {}).get("cells", {}).get("hard_full_management", {}).get("cost_usd")

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": _now(),
        "grid": {
            "tasks": [task_id for task_id, _ in TASKS],
            "personas": list(PERSONAS),
            "seeds": list(SEEDS),
            "episodes_per_column": len(TASKS) * len(PERSONAS) * len(SEEDS),
        },
        "rows": [{"task_id": task_id, "label": label} for task_id, label in TASKS],
        "columns": columns,
        "cost_usd_per_episode": hard_cost,
        "demo": measure_demo_workspace(),
        "reproduce": "python scripts/build_landing_metrics.py",
    }


def check() -> int:
    """Re-measure the deterministic columns and compare with the artifact.

    This is what makes "Measured, not guessed" enforceable: a policy change
    that moves a benchmark score fails the build instead of quietly making the
    landing page a lie.
    """
    try:
        artifact = load_artifact()
    except OSError:
        print(f"MISSING: {ARTIFACT_PATH}. Run: python scripts/build_landing_metrics.py")
        return 1

    committed = {c["agent"]: c for c in artifact["columns"]}
    verifiable = [a for a in DETERMINISTIC if a in committed]
    if not verifiable:
        print("No deterministic columns in the artifact to verify.")
        return 1

    measured = measure(verifiable)
    failures = []
    for agent in verifiable:
        for task_id, _label in TASKS:
            want = committed[agent]["cells"][task_id]["score"]
            got = measured[agent][task_id]["score"]
            if abs(want - got) > TOLERANCE:
                failures.append(f"  {agent}/{task_id}: artifact {want} != measured {got}")

    # The demo-workspace counts feed the "A working day" section and come from
    # a fixture put through the real policy, so they are verifiable too.
    demo_now = measure_demo_workspace()
    for key, got in demo_now.items():
        want = artifact.get("demo", {}).get(key)
        if want != got:
            failures.append(f"  demo/{key}: artifact {want} != measured {got}")

    if failures:
        print("Landing metrics are stale — the benchmark no longer agrees with them:")
        print("\n".join(failures))
        print("\nRe-run: python scripts/build_landing_metrics.py")
        return 1

    print(f"Landing metrics verified: {len(verifiable)} deterministic column(s) match.")
    for agent, column in committed.items():
        if not column.get("deterministic"):
            print(f"  (not re-run: {agent}, recorded {column.get('measured_at', 'unknown')})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=tuple(AGENT_LABELS),
        default=["baseline", "multiagent"],
        help="Agents to measure (default: %(default)s). 'llm' needs a provider key.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed artifact against a fresh run; write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return check()

    if "llm" in args.agents:
        from app.core.config import get_settings

        if not get_settings().provider_available:
            print("Refusing to run the 'llm' agent: no provider credential is configured.")
            return 1

    artifact = build(args.agents)
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ARTIFACT_PATH, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2)
        handle.write("\n")
    print(f"Wrote {ARTIFACT_PATH}")
    for column in artifact["columns"]:
        cells = " ".join(f"{c['score']:.2f}" for c in column["cells"].values())
        note = "" if column["deterministic"] else "  (recorded, not re-run)"
        print(f"  {column['label']:<14} {cells}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
