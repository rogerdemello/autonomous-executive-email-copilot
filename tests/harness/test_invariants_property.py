"""Property-based + fuzz tests for the core invariants.

Hypothesis generates arbitrary (but type-valid) action sequences across every
task and persona and asserts INV-1..4 hold. ``derandomize=True`` keeps the gate
reproducible (a flaky gate is itself a regression, R5).
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from env.environment import ExecutiveEmailEnv
from env.models import Action

from .invariants import (
    PERSONAS,
    TASKS,
    assert_deterministic,
    assert_grader_response_valid,
    assert_persona_invariant_headline,
    assert_reward_bounded,
)

_ACTION_TYPES = ["classify", "reply", "defer", "escalate", "prioritize"]
_LABELS = ["spam", "normal", "urgent"]
_CONTENTS = ["", "ok", "Acknowledged. Timeline and mitigation to follow."]
_ESCALATE = ["legal_team", "chief_of_staff", "none"]
_ID_CHOICE = ["real", "none", "fake"]


@st.composite
def _action_specs(draw: st.DrawFn) -> list[dict]:
    n = draw(st.integers(min_value=0, max_value=14))
    return [
        {
            "type": draw(st.sampled_from(_ACTION_TYPES)),
            "id_choice": draw(st.sampled_from(_ID_CHOICE)),
            "id_index": draw(st.integers(min_value=0, max_value=24)),
            "label": draw(st.sampled_from(_LABELS)),
            "content": draw(st.sampled_from(_CONTENTS)),
            "use_prio": draw(st.booleans()),
            "escalate_to": draw(st.sampled_from(_ESCALATE)),
        }
        for _ in range(n)
    ]


def _build_actions(specs: list[dict], email_ids: list[str]) -> list[Action]:
    actions: list[Action] = []
    for s in specs:
        if s["id_choice"] == "real" and email_ids:
            email_id: str | None = email_ids[s["id_index"] % len(email_ids)]
        elif s["id_choice"] == "fake":
            email_id = f"fake_{s['id_index']}"
        else:
            email_id = None
        actions.append(
            Action(
                action_type=s["type"],
                email_id=email_id,
                label=s["label"],
                content=s["content"] or None,
                priority_order=list(email_ids) if s["use_prio"] else [],
                escalate_to=None if s["escalate_to"] == "none" else s["escalate_to"],
            )
        )
    return actions


@settings(
    max_examples=60, derandomize=True, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
@given(task=st.sampled_from(TASKS), persona=st.sampled_from(PERSONAS), specs=_action_specs())
def test_grader_invariants_hold(task: str, persona: str, specs: list[dict]) -> None:
    env = ExecutiveEmailEnv(task_id=task, seed=42, persona=persona)
    observation = env.reset(task_id=task, seed=42, persona=persona)
    email_ids = [e.id for e in observation.emails]
    actions = _build_actions(specs, email_ids)

    # INV-4: every per-step reward stays in [-1, 1] (fresh env so we observe steps).
    step_env = ExecutiveEmailEnv(task_id=task, seed=42, persona=persona)
    for action in actions:
        result = step_env.step(action)
        assert_reward_bounded(result.reward)

    # INV-1 + INV-2: grader output is bounded and reproducible.
    resp = assert_deterministic(task, 42, persona, actions)
    assert_grader_response_valid(resp)


@settings(
    max_examples=40, derandomize=True, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
@given(task=st.sampled_from(TASKS), specs=_action_specs())
def test_headline_score_is_persona_invariant(task: str, specs: list[dict]) -> None:
    env = ExecutiveEmailEnv(task_id=task, seed=42, persona="balanced")
    observation = env.reset(task_id=task, seed=42, persona="balanced")
    email_ids = [e.id for e in observation.emails]
    actions = _build_actions(specs, email_ids)

    # INV-3: same actions -> identical score/breakdown across all personas.
    assert_persona_invariant_headline(task, 42, actions)
