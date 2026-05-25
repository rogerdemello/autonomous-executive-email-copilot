import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class AlertRule:
    name: str
    condition: Callable[[dict], bool]
    threshold: float
    webhook: str | None = None
    message: str = ""


@dataclass
class Alert:
    rule_name: str
    triggered: bool
    message: str
    timestamp: str


class AlertManager:
    def __init__(self):
        self._rules: list[AlertRule] = []
        self._alerts: list[Alert] = []
        self._metrics: dict | None = None

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def set_metrics(self, metrics: dict) -> None:
        self._metrics = metrics

    def check_rules(self) -> list[Alert]:
        if self._metrics is None:
            return []
        triggered = []
        for rule in self._rules:
            try:
                if rule.condition(self._metrics):
                    alert = Alert(
                        rule_name=rule.name,
                        triggered=True,
                        message=rule.message
                        or f"Alert: {rule.name} threshold ({rule.threshold}) exceeded",
                        timestamp=_get_timestamp(),
                    )
                    triggered.append(alert)
                    self._alerts.append(alert)
                    if rule.webhook:
                        self._send_webhook(rule.webhook, alert)
            except Exception:
                pass
        return triggered

    def _send_webhook(self, webhook: str, alert: Alert) -> None:
        # Only POST to http(s) webhooks; reject file:// and other schemes.
        if not webhook.lower().startswith(("http://", "https://")):
            return
        try:
            payload = json.dumps(
                {
                    "alert": alert.rule_name,
                    "message": alert.message,
                    "timestamp": alert.timestamp,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                webhook,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)  # nosec B310 - scheme restricted to http(s) above
        except Exception:
            pass

    def get_alerts(self) -> list[Alert]:
        return self._alerts

    def clear_alerts(self) -> None:
        self._alerts = []


def _get_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def high_failure_rate_rule(threshold: float = 0.2) -> AlertRule:
    def condition(metrics: dict) -> bool:
        completed = metrics.get("episodes_completed_total", 0)
        failed = metrics.get("episodes_failed_total", 0)
        total = completed + failed
        if total == 0:
            return False
        return (failed / total) > threshold

    return AlertRule(
        name="high_failure_rate",
        condition=condition,
        threshold=threshold,
        message="Episode failure rate exceeded threshold",
    )


def high_error_rate_rule(threshold: float = 0.1) -> AlertRule:
    def condition(metrics: dict) -> bool:
        requests = metrics.get("requests_total", 0)
        errors = metrics.get("api_errors_total", 0)
        if requests == 0:
            return False
        return (errors / requests) > threshold

    return AlertRule(
        name="high_error_rate",
        condition=condition,
        threshold=threshold,
        message="API error rate exceeded threshold",
    )


def cost_spike_rule(threshold: float = 100.0) -> AlertRule:
    def condition(metrics: dict) -> bool:
        total_cost = metrics.get("cost_usd_total", 0)
        return total_cost > threshold

    return AlertRule(
        name="cost_spike",
        condition=condition,
        threshold=threshold,
        message="Total cost exceeded threshold",
    )


alert_manager = AlertManager()


def register_default_rules() -> None:
    alert_manager.add_rule(high_failure_rate_rule(0.2))
    alert_manager.add_rule(high_error_rate_rule(0.1))
    alert_manager.add_rule(cost_spike_rule(100.0))


register_default_rules()
