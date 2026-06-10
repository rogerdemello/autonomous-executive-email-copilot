"""Per-step credit aggregation.

Aggregates the frozen ``GraderResponse.step_breakdown`` deltas across many graded
trajectories, grouped by action type, to show where score is gained/lost. Pure
and report-only; reads the same step breakdown the golden snapshot freezes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from env.models import GraderResponse


def aggregate_step_credit(responses: Sequence[GraderResponse]) -> dict[str, dict[str, float]]:
    """Group per-step score deltas by action type → n / mean_delta / total_delta."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for resp in responses:
        for step in resp.step_breakdown:
            key = step.action or "unknown"
            sums[key] = sums.get(key, 0.0) + step.score_delta
            counts[key] = counts.get(key, 0) + 1

    return {
        key: {
            "n": counts[key],
            "mean_delta": round(sums[key] / counts[key], 6),
            "total_delta": round(sums[key], 6),
        }
        for key in sorted(sums)
    }
