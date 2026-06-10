"""Cost-aware scoring: quality per unit of spend.

Report-only. Never feeds the grader. ``score_per_dollar`` / ``score_per_1k_tokens``
are ``None`` when the denominator is zero (e.g. deterministic offline agents),
which keeps the JSON artifact clean and JSON-serialisable.
"""

from __future__ import annotations

from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import BenchmarkResult


def score_per_dollar(score: float, cost_usd: float) -> float | None:
    return round(score / cost_usd, 6) if cost_usd > 0 else None


def score_per_1k_tokens(score: float, tokens: int) -> float | None:
    return round(score / (tokens / 1000.0), 6) if tokens > 0 else None


def cost_efficiency(results: list[BenchmarkResult]) -> list[dict[str, object]]:
    """Per-agent cost efficiency across all cells (sorted by agent name)."""
    groups: dict[str, list[BenchmarkResult]] = {}
    for r in results:
        groups.setdefault(r.agent_name, []).append(r)

    rows: list[dict[str, object]] = []
    for agent, group in sorted(groups.items()):
        mean_score = mean(r.metrics.score for r in group)
        mean_cost = mean(r.metrics.cost_usd for r in group)
        mean_tokens = mean(r.metrics.tokens for r in group)
        rows.append(
            {
                "agent_name": agent,
                "mean_score": round(mean_score, 6),
                "mean_cost_usd": round(mean_cost, 6),
                "mean_tokens": round(mean_tokens, 2),
                "score_per_dollar": score_per_dollar(mean_score, mean_cost),
                "score_per_1k_tokens": score_per_1k_tokens(mean_score, int(mean_tokens)),
            }
        )
    return rows
