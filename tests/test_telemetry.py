from telemetry.alerts import (
    AlertManager,
    AlertRule,
    alert_manager,
    cost_spike_rule,
    high_error_rate_rule,
    high_failure_rate_rule,
)
from telemetry.metrics import (
    PrometheusMetrics,
    get_metrics_output,
    record_api_error,
    record_episode_end,
    record_episode_start,
    record_llm_usage,
    record_request,
)


class TestPrometheusMetrics:
    def test_counter_increments(self):
        m = PrometheusMetrics()
        m.counter("test_counter", {"label": "value"})
        m.counter("test_counter", {"label": "value"})
        output = m.get_metrics()
        assert 'test_counter{label="value"} 2' in output

    def test_gauge_sets_value(self):
        m = PrometheusMetrics()
        m.gauge("test_gauge", 42.0, {"env": "test"})
        output = m.get_metrics()
        assert 'test_gauge{env="test"} 42.0' in output

    def test_histogram_records_values(self):
        m = PrometheusMetrics()
        m.histogram("request_duration_ms", 100.0)
        m.histogram("request_duration_ms", 200.0)
        output = m.get_metrics()
        assert "request_duration_ms_count" in output
        assert "request_duration_ms_sum" in output

    def test_record_request_function(self):
        record_request(50.0, {"endpoint": "/test"})
        output = get_metrics_output()
        assert "requests_total" in output
        assert "request_duration_ms" in output

    def test_record_episode_functions(self):
        record_episode_start()
        record_episode_end(success=True)
        output = get_metrics_output()
        assert "active_episodes" in output
        assert "episodes_completed_total" in output

    def test_record_api_error(self):
        record_api_error("timeout")
        output = get_metrics_output()
        assert "api_errors_total" in output

    def test_record_llm_usage_accumulates_cost_and_tokens(self):
        """Cost and tokens are values, not labels.

        The predecessors of this test exercised record_tokens_used/
        record_cost_usd, which encoded the measurement as a label and so left
        every counter pinned at 1 while minting a series per distinct value.
        """
        record_llm_usage(latency_ms=12.0, cost_usd=0.25, prompt_tokens=10, completion_tokens=5)
        output = get_metrics_output()
        assert "llm_cost_usd_total" in output
        assert "llm_tokens_total" in output
        assert "llm_latency_ms" in output


class TestAlertManager:
    def test_alert_rule_condition(self):
        def always_true(metrics):
            return True

        rule = AlertRule(
            name="test_rule",
            condition=always_true,
            threshold=0.5,
        )
        manager = AlertManager()
        manager.add_rule(rule)
        manager.set_metrics({"test": 1})
        triggered = manager.check_rules()
        assert len(triggered) == 1
        assert triggered[0].rule_name == "test_rule"

    def test_alert_no_trigger(self):
        def always_false(metrics):
            return False

        rule = AlertRule(
            name="never_trigger",
            condition=always_false,
            threshold=0.5,
        )
        manager = AlertManager()
        manager.add_rule(rule)
        manager.set_metrics({"test": 1})
        triggered = manager.check_rules()
        assert len(triggered) == 0

    def test_high_failure_rate_rule(self):
        rule = high_failure_rate_rule(0.3)
        metrics_data = {"episodes_completed_total": 7, "episodes_failed_total": 4}
        assert rule.condition(metrics_data) is True

    def test_high_failure_rate_rule_below_threshold(self):
        rule = high_failure_rate_rule(0.3)
        metrics_data = {"episodes_completed_total": 9, "episodes_failed_total": 1}
        assert rule.condition(metrics_data) is False

    def test_high_error_rate_rule(self):
        rule = high_error_rate_rule(0.1)
        metrics_data = {"requests_total": 100, "api_errors_total": 15}
        assert rule.condition(metrics_data) is True

    def test_cost_spike_rule(self):
        # `llm_cost_usd_total` is the metric the LLM path actually emits. The
        # rule previously read `cost_usd_total`, which nothing ever recorded.
        rule = cost_spike_rule(100.0)
        metrics_data = {"llm_cost_usd_total": 150.0}
        assert rule.condition(metrics_data) is True

    def test_cost_spike_rule_below_threshold(self):
        rule = cost_spike_rule(100.0)
        metrics_data = {"llm_cost_usd_total": 50.0}
        assert rule.condition(metrics_data) is False

    def test_cost_spike_rule_reads_the_metric_the_llm_path_emits(self):
        """Guard against the rule and the recorder drifting apart again.

        record_llm_usage is the only thing that records LLM cost; if its metric
        name changes without this rule following, the rule silently stops firing
        rather than failing loudly.
        """
        from telemetry.metrics import get_metrics_output, record_llm_usage

        record_llm_usage(latency_ms=1.0, cost_usd=0.01)
        assert "llm_cost_usd_total" in get_metrics_output()

    def test_alert_manager_get_alerts(self):
        manager = AlertManager()

        def trigger(metrics):
            return True

        rule = AlertRule(name="test", condition=trigger, threshold=1)
        manager.add_rule(rule)
        manager.set_metrics({"val": 1})
        manager.check_rules()
        alerts = manager.get_alerts()
        assert len(alerts) > 0

    def test_alert_manager_clear_alerts(self):
        manager = AlertManager()

        def trigger(metrics):
            return True

        rule = AlertRule(name="test", condition=trigger, threshold=1)
        manager.add_rule(rule)
        manager.set_metrics({"val": 1})
        manager.check_rules()
        manager.clear_alerts()
        assert len(manager.get_alerts()) == 0


class TestAlertWebhook:
    def test_webhook_url_can_be_set(self):
        manager = AlertManager()

        def always_true(metrics):
            return True

        rule = AlertRule(
            name="webhook_test",
            condition=always_true,
            threshold=0.5,
            webhook="https://example.com/hook",
        )
        manager.add_rule(rule)
        manager.set_metrics({"test": 1})
        triggered = manager.check_rules()
        assert len(triggered) == 1


class TestDefaultRules:
    def test_default_rules_registered(self):
        assert len(alert_manager._rules) >= 3
        rule_names = [r.name for r in alert_manager._rules]
        assert "high_failure_rate" in rule_names
        assert "high_error_rate" in rule_names
        assert "cost_spike" in rule_names
