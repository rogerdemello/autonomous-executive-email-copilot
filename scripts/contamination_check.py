#!/usr/bin/env python3
"""Eval-set contamination check.

Guards a basic but important integrity property: the gold ANSWER fields
(``expected_label``, ``expected_action``, ``expected_reply_keywords``,
``recommended_escalation``, ``critical``) must never be exposed to an agent. The
agent only ever sees an ``Observation`` (which structurally omits them, INV-5),
and the prompt-construction surface must not reference them either.

Run standalone:  ``python scripts/contamination_check.py``  (exit 1 on leakage)
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Gold answer field names that must never appear in anything an agent can read.
GOLD_FIELD_NAMES = (
    "expected_label",
    "expected_action",
    "expected_reply_keywords",
    "recommended_escalation",
)

_TASKS = ["easy_classification", "medium_prioritization", "hard_full_management"]


def find_gold_field_leakage(text: str) -> list[str]:
    """Return any gold field names present in ``text`` (empty list == clean)."""
    return [name for name in GOLD_FIELD_NAMES if name in text]


def check() -> list[str]:
    """Scan the agent-facing prompt surface for gold-answer leakage.

    Returns a list of human-readable findings; empty means clean.
    """
    from env.environment import ExecutiveEmailEnv
    from env.llm_agent import SYSTEM_PROMPT, _build_user_prompt

    findings: list[str] = []

    leaks = find_gold_field_leakage(SYSTEM_PROMPT)
    if leaks:
        findings.append(f"SYSTEM_PROMPT exposes gold fields: {leaks}")

    for task in _TASKS:
        env = ExecutiveEmailEnv(task_id=task, seed=42, persona="balanced")
        observation = env.reset(task_id=task, seed=42, persona="balanced")
        prompt = _build_user_prompt(observation)
        leaks = find_gold_field_leakage(prompt)
        if leaks:
            findings.append(f"{task} user prompt exposes gold fields: {leaks}")

    return findings


def main() -> int:
    findings = check()
    if findings:
        print("CONTAMINATION DETECTED:")
        for f in findings:
            print(f"  - {f}")
        return 1
    print("No eval-set contamination detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
