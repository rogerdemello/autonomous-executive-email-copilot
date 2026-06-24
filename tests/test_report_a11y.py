"""Structural accessibility checks for the benchmark HTML report."""

from __future__ import annotations

from benchmark.results_report import _render_html


def _html() -> str:
    rows = [
        {
            "task_id": "easy_classification",
            "persona": "balanced",
            "agent_name": "baseline",
            "n": 8,
            "mean_score": 0.999999,
            "ci95_low": 0.999999,
            "ci95_high": 0.999999,
            "mean_safety_score": 0.999999,
            "mean_tokens": 0.0,
            "mean_cost_usd": 0.0,
            "mean_time_ms": 1.0,
        }
    ]
    return _render_html(rows)


def test_report_has_core_a11y_landmarks() -> None:
    html = _html()
    assert '<html lang="en">' in html
    assert "<main>" in html
    assert "<caption>" in html
    assert '<th scope="col">' in html
    assert "<thead>" in html and "<tbody>" in html
    assert 'name="viewport"' in html  # responsive
