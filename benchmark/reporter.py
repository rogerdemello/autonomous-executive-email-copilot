from __future__ import annotations

import json
from statistics import mean

from .runner import BenchmarkResult, BenchmarkRunner


class Reporter:
    def __init__(self, runner: BenchmarkRunner):
        self.runner = runner

    def generate_json(self, results: list[BenchmarkResult]) -> str:
        data = self._build_comparison_data(results)
        return json.dumps(data, indent=2)

    def generate_html(self, results: list[BenchmarkResult]) -> str:
        data = self._build_comparison_data(results)

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Agent Benchmark Comparison</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 2rem; }}
        h1 {{ margin-bottom: 0.5rem; }}
        .summary {{ margin-bottom: 2rem; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f5f5f5; }}
        .score {{ font-weight: bold; }}
        .best {{ color: #22c55e; }}
        .baseline {{ background: #e0f2fe; }}
    </style>
</head>
<body>
    <h1>Agent Benchmark Comparison</h1>
    <div class="summary">
        <p>Tasks: {len(self.runner.tasks)} | Personas: {len(self.runner.personas)} | Seeds: {len(self.runner.seeds)}</p>
        <p>Total runs: {len(results)}</p>
    </div>
    <h2>Score Comparison by Agent</h2>
    <table>
        <tr>
            <th>Agent</th>
            <th>Avg Score</th>
            <th>Avg Time (ms)</th>
            <th>Avg Tokens</th>
            <th>Avg Cost ($)</th>
        </tr>
"""
        for row in data["summary"]:
            html += f"""        <tr>
            <td>{row["agent"]}</td>
            <td class="score">{row["avg_score"]:.3f}</td>
            <td>{row["avg_time_ms"]}</td>
            <td>{row["avg_tokens"]}</td>
            <td>${row["avg_cost_usd"]:.4f}</td>
        </tr>
"""

        html += """    </table>
    <h2>Detailed Results</h2>
    <table>
        <tr>
            <th>Task</th>
            <th>Persona</th>
            <th>Seed</th>
            <th>Agent</th>
            <th>Score</th>
            <th>Time (ms)</th>
            <th>Tokens</th>
            <th>Cost ($)</th>
        </tr>
"""

        for result in data["results"]:
            html += f"""        <tr>
            <td>{result["task_id"]}</td>
            <td>{result["persona"]}</td>
            <td>{result["seed"]}</td>
            <td>{result["agent_name"]}</td>
            <td class="score">{result["score"]:.3f}</td>
            <td>{result["time_ms"]}</td>
            <td>{result["tokens"]}</td>
            <td>${result["cost_usd"]:.4f}</td>
        </tr>
"""

        html += """    </table>
</body>
</html>"""
        return html

    def _build_comparison_data(self, results: list[BenchmarkResult]) -> dict:
        agent_results: dict[str, list] = {}
        for result in results:
            if result.agent_name not in agent_results:
                agent_results[result.agent_name] = []
            agent_results[result.agent_name].append(result)

        summary = []
        for agent_name, agent_results_list in agent_results.items():
            avg_score = mean(r.metrics.score for r in agent_results_list)
            avg_time = mean(r.metrics.time_ms for r in agent_results_list)
            avg_tokens = mean(r.metrics.tokens for r in agent_results_list)
            avg_cost = mean(r.metrics.cost_usd for r in agent_results_list)

            summary.append({
                "agent": agent_name,
                "avg_score": round(avg_score, 4),
                "avg_time_ms": int(avg_time),
                "avg_tokens": int(avg_tokens),
                "avg_cost_usd": round(avg_cost, 4),
            })

        summary.sort(key=lambda x: x["avg_score"], reverse=True)

        return {
            "summary": summary,
            "results": [r.to_dict() for r in results],
        }