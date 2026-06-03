import threading
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Counter:
    value: float = 0.0
    labels: dict = field(default_factory=dict)


@dataclass
class Gauge:
    value: float = 0.0
    labels: dict = field(default_factory=dict)


@dataclass
class Histogram:
    values: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)


class PrometheusMetrics:
    def __init__(self):
        self._counters: dict[str, dict[tuple, Counter]] = defaultdict(dict)
        self._gauges: dict[str, dict[tuple, Gauge]] = defaultdict(dict)
        self._histograms: dict[str, dict[tuple, Histogram]] = defaultdict(dict)
        self._lock = threading.Lock()

    def counter(
        self,
        name: str,
        labels: dict | None = None,
        description: str = "",
        amount: float = 1.0,
    ) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            if key not in self._counters[name]:
                self._counters[name][key] = Counter(labels=labels or {})
            self._counters[name][key].value += amount

    def gauge(
        self, name: str, value: float, labels: dict | None = None, description: str = ""
    ) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._gauges[name][key] = Gauge(value=value, labels=labels or {})

    def histogram(
        self, name: str, value: float, labels: dict | None = None, description: str = ""
    ) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            if key not in self._histograms[name]:
                self._histograms[name][key] = Histogram(labels=labels or {})
            self._histograms[name][key].values.append(value)

    def get_metrics(self) -> str:
        lines = []
        with self._lock:
            for name, counters in self._counters.items():
                for counter in counters.values():
                    labels_str = self._format_labels(counter.labels)
                    lines.append(f"{name}{labels_str} {counter.value}")
            for name, gauges in self._gauges.items():
                for gauge in gauges.values():
                    labels_str = self._format_labels(gauge.labels)
                    lines.append(f"{name}{labels_str} {gauge.value}")
            for name, histograms in self._histograms.items():
                for hist in histograms.values():
                    labels_str = self._format_labels(hist.labels)
                    if hist.values:
                        lines.append(f"{name}_count{labels_str} {len(hist.values)}")
                        lines.append(f"{name}_sum{labels_str} {sum(hist.values)}")
                        lines.append(f'{name}_bucket{{le="+Inf"}}{labels_str} {len(hist.values)}')
        return "\n".join(lines) + "\n"

    def _format_labels(self, labels: dict) -> str:
        if not labels:
            return ""
        label_parts = [f'{k}="{v}"' for k, v in labels.items()]
        return "{" + ",".join(label_parts) + "}"


metrics = PrometheusMetrics()


def record_request(duration_ms: float, labels: dict | None = None) -> None:
    metrics.histogram("request_duration_ms", duration_ms, labels)
    metrics.counter("requests_total", labels)


def record_episode_start() -> None:
    metrics.gauge("active_episodes", 1)


def record_episode_end(success: bool = True) -> None:
    metrics.gauge("active_episodes", 0)
    if success:
        metrics.counter("episodes_completed_total")
    else:
        metrics.counter("episodes_failed_total")


def record_api_error(error_type: str | None = None) -> None:
    labels = {"type": error_type} if error_type else None
    metrics.counter("api_errors_total", labels)


def record_tokens_used(tokens: int) -> None:
    metrics.counter("tokens_used_total", {"tokens": str(tokens)})


def record_cost_usd(cost: float) -> None:
    metrics.counter("cost_usd_total", {"cost": str(cost)})


def record_llm_usage(
    *,
    latency_ms: float,
    cost_usd: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str | None = None,
) -> None:
    """Record one LLM call's cost, latency, and token usage.

    Accumulates cumulative USD cost (``llm_cost_usd_total``) and token counts
    (``llm_tokens_total`` split by ``kind``), and observes the call latency in
    the ``llm_latency_ms`` histogram. ``model`` is attached as a label where it
    keeps label cardinality bounded (one series per model name).
    """
    label = {"model": model} if model else None
    metrics.histogram("llm_latency_ms", latency_ms, label)
    if cost_usd:
        metrics.counter("llm_cost_usd_total", label, amount=float(cost_usd))
    total_tokens = int(prompt_tokens) + int(completion_tokens)
    if prompt_tokens:
        prompt_label = {**(label or {}), "kind": "prompt"}
        metrics.counter("llm_tokens_total", prompt_label, amount=float(prompt_tokens))
    if completion_tokens:
        completion_label = {**(label or {}), "kind": "completion"}
        metrics.counter("llm_tokens_total", completion_label, amount=float(completion_tokens))
    if total_tokens:
        metrics.counter("llm_calls_total", label)


def get_metrics_output() -> str:
    return metrics.get_metrics()
