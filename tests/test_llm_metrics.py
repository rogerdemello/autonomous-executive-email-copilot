import json
from pathlib import Path

from telemetry.metrics import PrometheusMetrics, get_metrics_output, record_llm_usage

DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "telemetry" / "grafana_dashboard.json"


class TestCounterAmount:
    def test_counter_adds_arbitrary_amount(self):
        m = PrometheusMetrics()
        m.counter("amount_counter", {"k": "v"}, amount=2.5)
        m.counter("amount_counter", {"k": "v"}, amount=1.5)
        output = m.get_metrics()
        assert 'amount_counter{k="v"} 4.0' in output

    def test_counter_default_amount_is_one(self):
        m = PrometheusMetrics()
        m.counter("plain_counter")
        m.counter("plain_counter")
        assert "plain_counter 2" in m.get_metrics()


class TestRecordLLMUsage:
    def test_updates_cost_latency_and_tokens(self):
        record_llm_usage(
            latency_ms=123.0,
            cost_usd=0.05,
            prompt_tokens=100,
            completion_tokens=40,
            model="gpt-test",
        )
        output = get_metrics_output()
        assert "llm_latency_ms_count" in output
        assert "llm_latency_ms_sum" in output
        assert "llm_cost_usd_total" in output
        assert "llm_tokens_total" in output
        assert "llm_calls_total" in output
        assert 'kind="prompt"' in output
        assert 'kind="completion"' in output
        assert 'model="gpt-test"' in output

    def test_cost_accumulates(self):
        # The module-level registry is global; assert the unlabeled cost series
        # grows by exactly the recorded amount.
        before = _llm_cost_value(get_metrics_output())
        record_llm_usage(latency_ms=10.0, cost_usd=0.25)
        record_llm_usage(latency_ms=10.0, cost_usd=0.75)
        after = _llm_cost_value(get_metrics_output())
        assert after - before == 1.0

    def test_zero_values_do_not_create_token_or_cost_series(self):
        m = PrometheusMetrics()
        m.histogram("llm_latency_ms", 5.0)
        # latency-only call should not create cost/token counters
        output = m.get_metrics()
        assert "llm_cost_usd_total" not in output
        assert "llm_tokens_total" not in output


class TestDashboardPanels:
    def test_dashboard_parses(self):
        data = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
        assert "dashboard" in data
        assert isinstance(data["dashboard"]["panels"], list)

    def test_dashboard_contains_new_panels(self):
        data = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
        titles = {p["title"] for p in data["dashboard"]["panels"]}
        assert "LLM Cost (USD)" in titles
        assert "LLM Tokens" in titles
        assert "LLM Latency (ms)" in titles

    def test_new_panel_ids_are_unique(self):
        data = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
        ids = [p["id"] for p in data["dashboard"]["panels"]]
        assert len(ids) == len(set(ids))


def _llm_cost_value(output: str) -> float:
    # Sum the unlabeled (no model) cost series only, so this is independent of
    # other tests that record cost under a model label.
    for line in output.splitlines():
        if line.startswith("llm_cost_usd_total ") and "{" not in line:
            return float(line.rsplit(" ", 1)[-1])
    return 0.0
