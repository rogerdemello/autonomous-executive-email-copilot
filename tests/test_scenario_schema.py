from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from env.scenario_schema import (
    SCENARIOS_DIR,
    ScenarioFile,
    validate_all_scenarios,
    validate_scenario_file,
)

SHIPPED_SCENARIOS = sorted(SCENARIOS_DIR.glob("*.yaml"))


def test_scenarios_dir_is_populated() -> None:
    assert SHIPPED_SCENARIOS, "expected at least one scenario file in data/scenarios"


@pytest.mark.parametrize("scenario_path", SHIPPED_SCENARIOS, ids=lambda p: p.stem)
def test_shipped_scenario_validates(scenario_path: Path) -> None:
    model = validate_scenario_file(scenario_path)
    assert isinstance(model, ScenarioFile)
    assert model.emails  # min_length guarantees non-empty


def test_validate_all_scenarios_covers_every_file() -> None:
    results = validate_all_scenarios()
    assert {p.stem for p in SHIPPED_SCENARIOS} == set(results)
    assert all(isinstance(v, ScenarioFile) for v in results.values())


def test_malformed_scenario_raises() -> None:
    malformed = {
        "time_budget": "not-an-int",  # wrong type
        "risk_level": "extreme",  # not in RiskType literal
        "emails": [],  # violates min_length=1
    }
    with pytest.raises(ValidationError):
        ScenarioFile.model_validate(malformed)


def test_email_with_unknown_field_rejected() -> None:
    bad = {
        "time_budget": 90,
        "risk_level": "low",
        "emails": [
            {
                "id": "x1",
                "sender": "a@b.com",
                "sender_role": "client",
                "subject": "Hi",
                "body": "Body",
                "priority_hint": "high",
                "deadline_minutes": 60,
                "business_value": 0.5,
                "unexpected_field": True,  # extra="forbid" should reject
            }
        ],
    }
    with pytest.raises(ValidationError):
        ScenarioFile.model_validate(bad)


def test_interruption_requires_trigger() -> None:
    bad = {
        "time_budget": 90,
        "risk_level": "low",
        "emails": [
            {
                "id": "x1",
                "sender": "a@b.com",
                "sender_role": "client",
                "subject": "Hi",
                "body": "Body",
                "priority_hint": "high",
                "deadline_minutes": 60,
                "business_value": 0.5,
            }
        ],
        "interruptions": [
            {
                "email": {
                    "id": "i1",
                    "sender": "ops@b.com",
                    "sender_role": "internal",
                    "subject": "Q",
                    "body": "Body",
                    "priority_hint": "low",
                    "deadline_minutes": 30,
                    "business_value": 0.2,
                }
            }
        ],
    }
    with pytest.raises(ValidationError):
        ScenarioFile.model_validate(bad)
