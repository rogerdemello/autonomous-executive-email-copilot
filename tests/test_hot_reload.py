from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

from env import tasks, utils
from env.data_loader import clear_yaml_cache
from env.tasks import build_scenario, list_tasks


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    os.utime(path, None)
    time.sleep(0.01)


def test_settings_hot_reload(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.yaml"

    first_settings = {
        "action_cost_minutes": {
            "classify": 2,
            "reply": 8,
            "defer": 1,
            "escalate": 3,
            "prioritize": 4,
        },
        "persona_profiles": {
            "strict_ceo": {
                "deadline_penalty_multiplier": 1.35,
                "terminal_penalty_multiplier": 1.35,
                "urgent_defer_penalty_multiplier": 1.2,
                "redundant_penalty_multiplier": 1.1,
            },
            "balanced": {
                "deadline_penalty_multiplier": 1.0,
                "terminal_penalty_multiplier": 1.0,
                "urgent_defer_penalty_multiplier": 1.0,
                "redundant_penalty_multiplier": 1.0,
            },
            "chill_manager": {
                "deadline_penalty_multiplier": 0.65,
                "terminal_penalty_multiplier": 0.7,
                "urgent_defer_penalty_multiplier": 0.85,
                "redundant_penalty_multiplier": 0.9,
            },
        },
        "classifier_terms": {
            "spam": ["offer"],
            "urgent": ["asap"],
        },
    }
    _write_yaml(settings_path, first_settings)

    monkeypatch.setattr(utils, "SETTINGS_FILE", settings_path)
    clear_yaml_cache()

    assert utils.get_action_cost_minutes()["classify"] == 2
    assert utils.classify_heuristic("Special offer", "", "low", "none") == "spam"

    updated_settings = {
        **first_settings,
        "action_cost_minutes": {
            **first_settings["action_cost_minutes"],
            "classify": 9,
        },
        "classifier_terms": {
            "spam": ["promo"],
            "urgent": ["immediately"],
        },
    }
    _write_yaml(settings_path, updated_settings)

    assert utils.get_action_cost_minutes()["classify"] == 9
    assert utils.classify_heuristic("promo code", "", "low", "none") == "spam"


def test_tasks_and_scenario_hot_reload(monkeypatch, tmp_path) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    scenarios_dir = tmp_path / "scenarios"
    scenario_file = scenarios_dir / "custom.yaml"

    _write_yaml(
        tasks_file,
        {
            "tasks": [
                {
                    "id": "custom",
                    "name": "Custom Task",
                    "difficulty": "easy",
                    "description": "Custom task description.",
                }
            ]
        },
    )

    first_scenario = {
        "time_budget": 50,
        "risk_level": "low",
        "emails": [
            {
                "id": "c1",
                "sender": "client@example.com",
                "sender_role": "client",
                "subject": "Need response",
                "body": "Please respond today.",
                "priority_hint": "medium",
                "deadline_minutes": 120,
                "business_value": 0.8,
                "risk_tag": "ops",
                "expected_label": "normal",
                "expected_action": "reply",
                "expected_reply_keywords": ["respond"],
            }
        ],
        "interruptions": [
            {
                "trigger_minute": 10,
                "email": {
                    "id": "c2",
                    "sender": "ops@example.com",
                    "sender_role": "internal",
                    "subject": "Ops question",
                    "body": "Need quick check.",
                    "priority_hint": "low",
                    "deadline_minutes": 300,
                    "business_value": 0.3,
                    "risk_tag": "ops",
                    "expected_label": "normal",
                    "expected_action": "defer",
                },
            }
        ],
    }
    _write_yaml(scenario_file, first_scenario)

    monkeypatch.setattr(tasks, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(tasks, "SCENARIOS_DIR", scenarios_dir)
    clear_yaml_cache()

    task_defs = list_tasks()
    assert len(task_defs) == 1
    assert task_defs[0].id == "custom"

    scenario = build_scenario(task_id="custom", seed=42, persona="balanced")
    assert scenario.time_budget == 50
    assert len(scenario.emails) == 1

    updated_scenario = {**first_scenario, "time_budget": 75}
    _write_yaml(scenario_file, updated_scenario)

    scenario_reloaded = build_scenario(task_id="custom", seed=42, persona="balanced")
    assert scenario_reloaded.time_budget == 75
