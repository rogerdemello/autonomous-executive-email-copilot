"""Deterministic significance testing between agents.

Agents are compared with a PAIRED test over the shared ``(task, persona, seed)``
grid, so each pair of observations comes from an identical scenario. The test is
a paired t-test using ``scipy.stats.t`` for the p-value — fully deterministic
(no RNG), satisfying INV-2 at the statistics layer. Cohen's d (paired) reports
effect size.
"""

from __future__ import annotations

import math
from statistics import mean, stdev
from typing import TYPE_CHECKING

from scipy.stats import t as _t_dist

if TYPE_CHECKING:
    from .runner import BenchmarkResult

_ALPHA = 0.05


def _cell_key(result: BenchmarkResult) -> tuple[str, str, int]:
    return (result.task_id, result.persona, result.seed)


def paired_deltas(
    results_a: list[BenchmarkResult], results_b: list[BenchmarkResult]
) -> list[float]:
    """Per-cell score deltas (a - b) over the grid shared by both agents.

    Cells are matched on ``(task, persona, seed)`` and iterated in sorted order so
    the delta sequence is reproducible.
    """
    a = {_cell_key(r): r.metrics.score for r in results_a}
    b = {_cell_key(r): r.metrics.score for r in results_b}
    return [a[k] - b[k] for k in sorted(a.keys() & b.keys())]


def cohens_d_paired(deltas: list[float]) -> float:
    """Paired Cohen's d = mean(delta) / sd(delta); 0.0 when undefined."""
    if len(deltas) < 2:
        return 0.0
    sd = stdev(deltas)
    if sd == 0.0:
        return 0.0
    return mean(deltas) / sd


def paired_t_test(deltas: list[float]) -> dict[str, float]:
    """Two-sided paired t-test. Returns ``t_stat`` and ``p_value`` (deterministic)."""
    n = len(deltas)
    if n < 2:
        return {"t_stat": 0.0, "p_value": 1.0}

    md = mean(deltas)
    sd = stdev(deltas)
    if sd == 0.0:
        # No variance: identical deltas. Either a perfect, consistent separation
        # (mean != 0 -> significant) or no difference at all (mean == 0).
        if md == 0.0:
            return {"t_stat": 0.0, "p_value": 1.0}
        return {"t_stat": math.inf, "p_value": 0.0}

    se = sd / (n**0.5)
    t_stat = md / se
    p_value = float(2.0 * _t_dist.sf(abs(t_stat), n - 1))
    return {"t_stat": t_stat, "p_value": p_value}


def _round_finite(value: float, digits: int = 6) -> float:
    return round(value, digits) if math.isfinite(value) else value


def compare_agents(
    results: list[BenchmarkResult],
    agent_a: str,
    agent_b: str,
    alpha: float = _ALPHA,
) -> dict[str, object]:
    """Compare two agents' scores with a paired t-test + Cohen's d.

    ``mean_delta > 0`` means ``agent_a`` scores higher than ``agent_b``.
    """
    results_a = [r for r in results if r.agent_name == agent_a]
    results_b = [r for r in results if r.agent_name == agent_b]
    deltas = paired_deltas(results_a, results_b)

    tt = paired_t_test(deltas)
    return {
        "agent_a": agent_a,
        "agent_b": agent_b,
        "n": len(deltas),
        "mean_delta": round(mean(deltas), 6) if deltas else 0.0,
        "t_stat": _round_finite(tt["t_stat"]),
        "p_value": round(tt["p_value"], 6),
        "cohens_d": round(cohens_d_paired(deltas), 6),
        "significant": bool(deltas) and tt["p_value"] < alpha,
    }


def compare_all_pairs(
    results: list[BenchmarkResult], alpha: float = _ALPHA
) -> list[dict[str, object]]:
    """Pairwise comparison of every agent present in ``results`` (sorted, unique pairs)."""
    agents = sorted({r.agent_name for r in results})
    out: list[dict[str, object]] = []
    for i, a in enumerate(agents):
        for b in agents[i + 1 :]:
            out.append(compare_agents(results, a, b, alpha=alpha))
    return out
