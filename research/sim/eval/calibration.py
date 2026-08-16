"""Calibration metrics: are an agent's confidences trustworthy?

Pure, report-only functions over ``(confidence, was_correct)`` pairs. Both
metrics are bounded in [0, 1] for probabilities in [0, 1] (lower is better).
"""

from __future__ import annotations

from collections.abc import Sequence

Pair = tuple[float, bool]


def brier_score(pairs: Sequence[Pair]) -> float:
    """Mean squared error between confidence and outcome (0 = perfect)."""
    if not pairs:
        return 0.0
    return sum((p - (1.0 if outcome else 0.0)) ** 2 for p, outcome in pairs) / len(pairs)


def expected_calibration_error(pairs: Sequence[Pair], n_bins: int = 10) -> float:
    """Expected Calibration Error: |accuracy - confidence| averaged over bins."""
    if not pairs:
        return 0.0
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")

    bins: list[list[Pair]] = [[] for _ in range(n_bins)]
    for prob, outcome in pairs:
        idx = min(n_bins - 1, max(0, int(prob * n_bins)))
        bins[idx].append((prob, outcome))

    total = len(pairs)
    ece = 0.0
    for group in bins:
        if not group:
            continue
        confidence = sum(p for p, _ in group) / len(group)
        accuracy = sum(1 for _, outcome in group if outcome) / len(group)
        ece += (len(group) / total) * abs(accuracy - confidence)
    return ece
