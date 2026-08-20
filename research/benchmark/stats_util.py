"""Shared statistics helpers.

Single source of truth for the 95% t-critical table and CI margin, imported by
both ``baseline/leaderboard.py`` and the benchmark reporting/significance layers
so the table is never duplicated.
"""

from __future__ import annotations

from statistics import mean, stdev

# t-distribution critical values for a two-sided 95% interval, by degrees of freedom.
T_CRITICAL_95: dict[int, float] = {
    2: 12.706,
    3: 4.303,
    4: 3.182,
    5: 2.776,
    6: 2.571,
    7: 2.447,
    8: 2.365,
    9: 2.306,
    10: 2.262,
    11: 2.228,
    12: 2.201,
    13: 2.179,
    14: 2.160,
    15: 2.145,
}


def t_critical_95(df: int) -> float:
    """t-critical value for a 95% CI with ``df`` degrees of freedom."""
    if df < 2:
        return 12.706  # fallback for very small samples
    if df in T_CRITICAL_95:
        return T_CRITICAL_95[df]
    return 1.96  # z-approximation for large df


def ci95_margin(scores: list[float]) -> float:
    """Half-width of the 95% CI for ``scores`` (0.0 for a single sample)."""
    if len(scores) < 2:
        return 0.0
    se = stdev(scores) / (len(scores) ** 0.5)
    return t_critical_95(len(scores) - 1) * se


def mean_ci95(scores: list[float]) -> tuple[float, float]:
    """Return ``(low, high)`` 95% CI bounds around the mean of ``scores``."""
    if not scores:
        return (0.0, 0.0)
    avg = mean(scores)
    margin = ci95_margin(scores)
    return (avg - margin, avg + margin)
