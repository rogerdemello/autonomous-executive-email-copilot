"""Gated, determinism-safe scenario variants.

Two guarantees are locked in here:

* With ``SCENARIO_VARIANTS`` unset/off (the default), scenario selection is
  byte-identical to historical behaviour: the canonical ``{task_id}.yaml`` is
  always loaded and golden baseline scores are unchanged.
* With the flag on, selection is a deterministic function of the seed, and
  every globbed candidate file validates against ``env.scenario_schema``.
"""

from __future__ import annotations

import random

import pytest

from baseline.run_baseline import run
from env.data_loader import SCENARIOS_DIR, resolve_scenario_path
from env.scenario_schema import validate_scenario_file
from env.tasks import build_scenario

TASKS = ["easy_classification", "medium_prioritization", "hard_full_management"]

# Golden baseline scores (must match tests/test_grading_rigor.py exactly).
GOLDEN = {
    "easy_classification": 0.999999,
    "medium_prioritization": 0.999999,
    "hard_full_management": 0.600455,
}


# --- Flag OFF: canonical only, scores unchanged ---------------------------


@pytest.mark.parametrize("task_id", TASKS)
def test_flag_off_resolves_canonical(task_id):
    path = resolve_scenario_path(task_id, seed=123, dir_path=SCENARIOS_DIR)
    assert path == SCENARIOS_DIR / f"{task_id}.yaml"


@pytest.mark.parametrize("task_id,expected", GOLDEN.items())
def test_flag_off_scores_match_today(task_id, expected):
    result = run(task_id=task_id, seed=42, max_steps=100, persona="balanced", mode="baseline")
    assert abs(result["score"] - expected) < 1e-4, result["score"]


def test_flag_off_picks_canonical_even_when_variant_exists():
    # A variant file exists on disk for this task, but with the flag off it must
    # never be selected.
    canonical = SCENARIOS_DIR / "easy_classification.yaml"
    for seed in range(8):
        assert resolve_scenario_path("easy_classification", seed, SCENARIOS_DIR) == canonical


# --- Flag ON: deterministic selection + every candidate is valid ----------


def test_flag_on_selection_is_deterministic_per_seed(monkeypatch):
    monkeypatch.setenv("SCENARIO_VARIANTS", "true")

    for seed in (0, 1, 7, 42, 100):
        first = resolve_scenario_path("easy_classification", seed, SCENARIOS_DIR)
        second = resolve_scenario_path("easy_classification", seed, SCENARIOS_DIR)
        assert first == second

        # Mirror the loader's selection algorithm to confirm it is the documented
        # seed -> choice mapping over the deterministically sorted candidates.
        candidates = sorted(
            p for p in SCENARIOS_DIR.glob("easy_classification*.yaml") if p.is_file()
        )
        assert len(candidates) >= 2
        assert first == random.Random(seed).choice(candidates)


def test_flag_on_selection_spans_more_than_canonical(monkeypatch):
    monkeypatch.setenv("SCENARIO_VARIANTS", "true")
    chosen = {
        resolve_scenario_path("easy_classification", seed, SCENARIOS_DIR).name for seed in range(50)
    }
    # With at least two candidates, a 50-seed sweep should surface more than one.
    assert len(chosen) >= 2


def test_flag_on_build_scenario_is_seed_deterministic(monkeypatch):
    monkeypatch.setenv("SCENARIO_VARIANTS", "true")
    a = build_scenario("easy_classification", seed=7, persona="balanced")
    b = build_scenario("easy_classification", seed=7, persona="balanced")
    assert [e.id for e in a.emails] == [e.id for e in b.emails]
    assert a.time_budget == b.time_budget


def test_every_globbed_candidate_is_schema_valid():
    for task_id in TASKS:
        candidates = sorted(p for p in SCENARIOS_DIR.glob(f"{task_id}*.yaml") if p.is_file())
        assert candidates, f"no scenario files for {task_id}"
        for path in candidates:
            validate_scenario_file(path)
