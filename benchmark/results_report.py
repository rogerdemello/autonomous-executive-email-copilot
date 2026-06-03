from __future__ import annotations

import csv
import json
import os
from statistics import mean, stdev

from .runner import BenchmarkResult

# Columns shared by the aggregated rows, in stable display order.
AGGREGATE_FIELDS = [
    "task_id",
    "persona",
    "agent_name",
    "n",
    "mean_score",
    "ci95_low",
    "ci95_high",
    "mean_tokens",
    "mean_cost_usd",
    "mean_time_ms",
]


def _confidence_interval(scores: list[float]) -> tuple[float, float]:
    """Return the 95% CI (mean +/- 1.96*std/sqrt(n)) for ``scores``.

    With a single sample the standard deviation is undefined, so the interval
    collapses onto the mean (half-width 0.0).
    """
    avg = mean(scores)
    n = len(scores)
    if n < 2:
        return (avg, avg)
    half_width = 1.96 * stdev(scores) / (n**0.5)
    return (avg - half_width, avg + half_width)


def aggregate_results(results: list[BenchmarkResult]) -> list[dict]:
    """Aggregate raw results by ``(task_id, persona, agent_name)`` across seeds.

    Each group yields the mean score, its 95% confidence interval, and the mean
    tokens, cost, and wall-clock time. Rows are sorted by the grouping key for a
    stable, reproducible artifact.
    """
    groups: dict[tuple[str, str, str], list[BenchmarkResult]] = {}
    for result in results:
        key = (result.task_id, result.persona, result.agent_name)
        groups.setdefault(key, []).append(result)

    rows: list[dict] = []
    for (task_id, persona, agent_name), group in sorted(groups.items()):
        scores = [r.metrics.score for r in group]
        avg_score = mean(scores)
        ci_low, ci_high = _confidence_interval(scores)

        rows.append(
            {
                "task_id": task_id,
                "persona": persona,
                "agent_name": agent_name,
                "n": len(group),
                "mean_score": round(avg_score, 6),
                "ci95_low": round(ci_low, 6),
                "ci95_high": round(ci_high, 6),
                "mean_tokens": round(mean(r.metrics.tokens for r in group), 2),
                "mean_cost_usd": round(mean(r.metrics.cost_usd for r in group), 6),
                "mean_time_ms": round(mean(r.metrics.time_ms for r in group), 2),
            }
        )

    return rows


def _write_json(rows: list[dict], results: list[BenchmarkResult], path: str) -> None:
    payload = {
        "aggregates": rows,
        "results": [r.to_dict() for r in results],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AGGREGATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _render_html(rows: list[dict]) -> str:
    header_cells = "".join(f"<th>{field}</th>" for field in AGGREGATE_FIELDS)

    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{row[field]}</td>" for field in AGGREGATE_FIELDS)
        body_rows.append(f"        <tr>{cells}</tr>")
    body = "\n".join(body_rows)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Benchmark Results</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 2rem; }}
        h1 {{ margin-bottom: 0.5rem; }}
        .summary {{ margin-bottom: 1.5rem; color: #555; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f5f5f5; }}
        tr:nth-child(even) {{ background: #fafafa; }}
        td {{ font-variant-numeric: tabular-nums; }}
    </style>
</head>
<body>
    <h1>Benchmark Results</h1>
    <div class="summary">
        <p>Aggregated by (task, persona, agent) across seeds. Score interval is the 95% CI (mean &plusmn; 1.96&middot;std/&radic;n).</p>
        <p>Groups: {len(rows)}</p>
    </div>
    <table>
        <tr>{header_cells}</tr>
{body}
    </table>
</body>
</html>"""


def write_results_report(results: list[BenchmarkResult], output_dir: str) -> dict[str, str]:
    """Write ``results.json``, ``results.csv``, and ``results.html`` to ``output_dir``.

    Returns a mapping of artifact name to its absolute path.
    """
    os.makedirs(output_dir, exist_ok=True)
    rows = aggregate_results(results)

    json_path = os.path.join(output_dir, "results.json")
    csv_path = os.path.join(output_dir, "results.csv")
    html_path = os.path.join(output_dir, "results.html")

    _write_json(rows, results, json_path)
    _write_csv(rows, csv_path)
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(_render_html(rows))

    return {
        "json": os.path.abspath(json_path),
        "csv": os.path.abspath(csv_path),
        "html": os.path.abspath(html_path),
    }
