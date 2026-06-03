from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import LabelType, RiskTag, RiskType, SenderRole

# Default scenario directory (data/scenarios) relative to the repo root.
SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"


class ScenarioEmail(BaseModel):
    """An email entry as written in a scenario YAML file.

    Mirrors the subset of ``EmailRecord`` fields the loader actually reads from
    disk (runtime-only fields such as ``predicted_label`` are never authored in
    scenario files, so they are intentionally absent here).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    sender: str
    sender_role: SenderRole
    subject: str
    body: str
    priority_hint: Literal["low", "medium", "high"]
    deadline_minutes: int
    business_value: float = Field(ge=0.0, le=1.0)
    risk_tag: RiskTag = "none"

    expected_label: LabelType = "normal"
    expected_action: Literal["reply", "defer", "escalate", "ignore"] = "reply"
    expected_reply_keywords: list[str] = Field(default_factory=list)
    recommended_escalation: str | None = None
    critical: bool = False


class ScenarioInterruption(BaseModel):
    """A mid-episode interruption authored in a scenario file.

    ``env.tasks._resolve_trigger_minute`` accepts either an explicit
    ``trigger_minute`` or a two-element ``trigger_minute_range``; at least one
    must be present.
    """

    model_config = ConfigDict(extra="forbid")

    trigger_minute: int | None = None
    trigger_minute_range: list[int] | None = None
    email: ScenarioEmail

    @model_validator(mode="after")
    def _check_trigger(self) -> ScenarioInterruption:
        if self.trigger_minute is None and self.trigger_minute_range is None:
            raise ValueError("interruption requires 'trigger_minute' or 'trigger_minute_range'")
        if self.trigger_minute_range is not None and len(self.trigger_minute_range) != 2:
            raise ValueError("'trigger_minute_range' must contain exactly two values")
        return self


class ScenarioFile(BaseModel):
    """Top-level shape of a ``data/scenarios/*.yaml`` file.

    Matches the keys read by ``env.tasks.build_scenario`` via the loader:
    ``time_budget``, ``risk_level``, ``emails`` and ``interruptions``.
    """

    model_config = ConfigDict(extra="forbid")

    time_budget: int = Field(gt=0)
    risk_level: RiskType
    emails: list[ScenarioEmail] = Field(min_length=1)
    interruptions: list[ScenarioInterruption] = Field(default_factory=list)


def validate_scenario_file(path: str | Path) -> ScenarioFile:
    """Parse and validate a single scenario YAML file.

    Raises ``pydantic.ValidationError`` if the file does not match the schema.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return ScenarioFile.model_validate(data)


def validate_all_scenarios(directory: str | Path = SCENARIOS_DIR) -> dict[str, ScenarioFile]:
    """Validate every ``*.yaml`` scenario in ``directory``.

    Returns a mapping of file stem (task id) to its parsed model.
    """
    directory = Path(directory)
    results: dict[str, ScenarioFile] = {}
    for yaml_path in sorted(directory.glob("*.yaml")):
        results[yaml_path.stem] = validate_scenario_file(yaml_path)
    return results
