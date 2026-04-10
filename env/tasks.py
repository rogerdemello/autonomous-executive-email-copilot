from __future__ import annotations

import copy
import random
from pathlib import Path

from .models import EmailRecord, InterruptionEvent, PersonaType, Scenario, TaskDefinition
from .data_loader import load_yaml
from .utils import compute_gold_priority_order


DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
TASKS_FILE = DATA_ROOT / "tasks.yaml"
SCENARIOS_DIR = DATA_ROOT / "scenarios"


def _load_tasks_manifest() -> dict:
    return load_yaml(TASKS_FILE)


def _load_scenario_manifest(task_id: str) -> dict:
    path = SCENARIOS_DIR / f"{task_id}.yaml"
    if not path.exists():
        raise ValueError(f"Missing scenario file for task '{task_id}': {path}")
    return load_yaml(path)


def list_tasks() -> list[TaskDefinition]:
    manifest = _load_tasks_manifest()
    task_items = manifest.get("tasks", [])
    return [TaskDefinition.model_validate(item) for item in task_items]


def _resolve_trigger_minute(event_data: dict, rng: random.Random) -> int:
    if "trigger_minute" in event_data:
        return int(event_data["trigger_minute"])

    minute_range = event_data.get("trigger_minute_range")
    if minute_range is None or len(minute_range) != 2:
        raise ValueError("Interruption event requires trigger_minute or trigger_minute_range")

    start, end = int(minute_range[0]), int(minute_range[1])
    low, high = sorted([start, end])
    return rng.randint(low, high)


def _build_interruptions(interruption_items: list[dict], rng: random.Random) -> list[InterruptionEvent]:
    events: list[InterruptionEvent] = []
    for raw_event in interruption_items:
        trigger_minute = _resolve_trigger_minute(raw_event, rng)
        email_raw = raw_event.get("email")
        if not isinstance(email_raw, dict):
            raise ValueError("Interruption event missing email payload")
        events.append(
            InterruptionEvent(
                trigger_minute=trigger_minute,
                email=EmailRecord.model_validate(email_raw),
            )
        )
    return events


def build_scenario(task_id: str, seed: int, persona: PersonaType = "balanced") -> Scenario:
    rng = random.Random(seed)

    scenario_manifest = copy.deepcopy(_load_scenario_manifest(task_id))

    raw_emails = scenario_manifest.get("emails", [])
    emails = [EmailRecord.model_validate(item) for item in raw_emails]
    rng.shuffle(emails)

    interruption_items = scenario_manifest.get("interruptions", [])
    interruptions = _build_interruptions(interruption_items, rng)

    return Scenario(
        task_id=task_id,
        seed=seed,
        persona=persona,
        time_budget=int(scenario_manifest.get("time_budget", 120)),
        risk_level=scenario_manifest.get("risk_level", "medium"),
        emails=emails,
        gold_priority_order=compute_gold_priority_order(emails),
        interruptions=interruptions,
    )
