"""Coarse performance-regression guard.

Grades the heaviest task's canonical rollout a number of times and asserts the
average stays under a deliberately wide budget. The margin is large on purpose:
this catches order-of-magnitude regressions (e.g. accidental O(n^2) or per-call
disk I/O in the grade path) without flaking on shared CI runners (R5).
"""

from __future__ import annotations

from time import perf_counter

from env.grader import evaluate_trajectory

from .invariants import canonical_trace

_ITERATIONS = 20
_MAX_AVG_SECONDS = 1.0  # wide margin; a single grade is well under ~10ms locally


def test_grade_path_perf_budget() -> None:
    task, seed, persona = "hard_full_management", 42, "balanced"
    trace = canonical_trace(task, seed, persona)

    start = perf_counter()
    for _ in range(_ITERATIONS):
        evaluate_trajectory(task_id=task, seed=seed, actions=trace, persona=persona)
    avg = (perf_counter() - start) / _ITERATIONS

    assert avg < _MAX_AVG_SECONDS, (
        f"grade path too slow: {avg:.4f}s avg (budget {_MAX_AVG_SECONDS}s)"
    )
