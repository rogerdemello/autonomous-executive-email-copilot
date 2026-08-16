"""Persisted leaderboard history with run-over-run deltas.

Each benchmark run can append one JSON line per call to a history file
(``benchmark/leaderboard_history.jsonl`` by default). Appends never mutate prior
lines, and each entry records the delta of its per-agent summary versus the
previous entry — so regressions/improvements are visible over time.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import BenchmarkResult

_DELTA_KEYS = ("mean_score", "mean_safety_score", "mean_cost_usd")


def summarize_results(results: list[BenchmarkResult]) -> dict[str, dict[str, float]]:
    """Per-agent overall summary (mean score, safety, cost) across all cells."""
    groups: dict[str, list[BenchmarkResult]] = {}
    for r in results:
        groups.setdefault(r.agent_name, []).append(r)

    summary: dict[str, dict[str, float]] = {}
    for agent, group in sorted(groups.items()):
        summary[agent] = {
            "n": len(group),
            "mean_score": round(mean(r.metrics.score for r in group), 6),
            "mean_safety_score": round(mean(r.metrics.safety_score for r in group), 6),
            "mean_cost_usd": round(mean(r.metrics.cost_usd for r in group), 6),
        }
    return summary


def load_history(path: str | Path) -> list[dict]:
    """Read all history entries (empty list if the file does not exist)."""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def compute_deltas(
    summary: dict[str, dict[str, float]],
    previous: dict[str, dict[str, float]] | None,
) -> dict[str, dict[str, float] | None]:
    """Delta of each agent's summary vs the previous run (None if no prior data)."""
    deltas: dict[str, dict[str, float] | None] = {}
    for agent, stats in summary.items():
        prev = previous.get(agent) if previous else None
        if prev is None:
            deltas[agent] = None
        else:
            deltas[agent] = {k: round(stats[k] - prev[k], 6) for k in _DELTA_KEYS}
    return deltas


def append_run(
    path: str | Path,
    results: list[BenchmarkResult],
    *,
    label: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """Append one history entry for ``results`` and return it.

    ``timestamp`` is accepted explicitly (not generated) so callers control it and
    tests stay deterministic.
    """
    history = load_history(path)
    previous = history[-1]["summary"] if history else None

    summary = summarize_results(results)
    entry = {
        "label": label,
        "timestamp": timestamp,
        "summary": summary,
        "deltas": compute_deltas(summary, previous),
    }

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry
