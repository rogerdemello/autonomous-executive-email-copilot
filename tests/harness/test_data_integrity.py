"""Data + schema integrity guard (INV-5).

* Every shipped scenario validates against ``env.scenario_schema``.
* ``build_scenario`` succeeds for every task and yields gradeable records.
* The closed-schema guarantee: gold fields (``expected_*``, ``critical``,
  ``recommended_escalation``) live only on ``EmailRecord`` and can NEVER appear on
  an ``ObservationEmail`` — so real, un-privileged mail is ungradeable by
  construction. This is what later isolates the read-only email adapter (Phase 5).
"""

from __future__ import annotations

import pytest

from env.environment import ExecutiveEmailEnv
from env.models import EmailRecord, ObservationEmail
from env.scenario_schema import validate_all_scenarios
from env.tasks import build_scenario

from .invariants import PERSONAS, TASKS

_GOLD_FIELDS = {
    "expected_label",
    "expected_action",
    "expected_reply_keywords",
    "recommended_escalation",
    "critical",
}


def test_all_shipped_scenarios_validate() -> None:
    results = validate_all_scenarios()
    assert results, "no scenarios found"
    for stem, model in results.items():
        assert model.emails, f"{stem} has no emails"


@pytest.mark.parametrize("task", TASKS)
def test_build_scenario_succeeds(task: str) -> None:
    scenario = build_scenario(task, seed=42, persona="balanced")
    assert scenario.emails
    assert all(isinstance(e, EmailRecord) for e in scenario.emails)


def test_observation_email_carries_no_gold_fields() -> None:
    # Structural guarantee: gold fields exist on EmailRecord but not ObservationEmail.
    assert _GOLD_FIELDS.issubset(set(EmailRecord.model_fields))
    assert _GOLD_FIELDS.isdisjoint(set(ObservationEmail.model_fields))


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("persona", PERSONAS)
def test_observations_never_leak_gold_fields(task: str, persona: str) -> None:
    env = ExecutiveEmailEnv(task_id=task, seed=42, persona=persona)
    observation = env.reset(task_id=task, seed=42, persona=persona)
    for email in observation.emails:
        dumped = email.model_dump()
        assert _GOLD_FIELDS.isdisjoint(dumped.keys()), f"gold field leaked into observation: {task}"
