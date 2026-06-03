"""Locust load test for the Autonomous Executive Email Copilot API.

Exercises the hot read paths (``/health``, ``/tasks``, ``/metrics``, ``/state``)
and a representative mutating flow (``/reset`` followed by ``/step``). Task
weights bias traffic toward the cheap, high-frequency read endpoints, which is
how the service is exercised in practice (probes, scrapers, dashboards) while
still keeping a steady trickle of episode resets and steps.

Locust is an *optional* development dependency and is intentionally not listed
in ``requirements.txt``. Install it on demand::

    pip install locust

Then run against a locally running server (uvicorn on :8000)::

    locust -f scripts/loadtest/locustfile.py --host http://localhost:8000

See ``scripts/loadtest/README.md`` for full usage and suggested SLOs.
"""

from __future__ import annotations

from typing import Any

from locust import HttpUser, between, task

# A task that ships with the environment. Reset defaults to this server-side, but
# we send it explicitly so the load profile is stable regardless of server config.
DEFAULT_TASK_ID = "easy_classification"
DEFAULT_SEED = 42
DEFAULT_PERSONA = "balanced"


class EmailCopilotUser(HttpUser):
    """A simulated client hitting the read paths and the reset/step flow.

    ``wait_time`` inserts a short think-time between tasks so a single user does
    not behave like a tight benchmark loop; raise concurrency via ``--users`` to
    increase offered load.
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        """Prime the environment so ``/state`` and ``/step`` have something to act on."""
        self._reset_env()

    # --- read paths (weighted heavily) ----------------------------------

    @task(10)
    def health(self) -> None:
        # Cheapest endpoint; mirrors the container HEALTHCHECK and k8s probes.
        self.client.get("/health", name="GET /health")

    @task(5)
    def tasks(self) -> None:
        self.client.get("/tasks", name="GET /tasks")

    @task(5)
    def state(self) -> None:
        self.client.get("/state", name="GET /state")

    @task(3)
    def metrics(self) -> None:
        # Prometheus text exposition; exercised by scrapers on a fixed interval.
        self.client.get("/metrics", name="GET /metrics")

    # --- representative mutating flow (lighter weight) ------------------

    @task(2)
    def reset_then_step(self) -> None:
        """Reset the environment then take one step against a returned email."""
        observation = self._reset_env()
        if observation is None:
            return
        self._step(observation)

    # --- helpers --------------------------------------------------------

    def _reset_env(self) -> dict[str, Any] | None:
        payload = {
            "task_id": DEFAULT_TASK_ID,
            "seed": DEFAULT_SEED,
            "persona": DEFAULT_PERSONA,
        }
        with self.client.post(
            "/reset", json=payload, name="POST /reset", catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(f"reset returned {response.status_code}")
                return None
            try:
                return response.json()
            except ValueError:
                response.failure("reset returned non-JSON body")
                return None

    def _step(self, observation: dict[str, Any]) -> None:
        emails = observation.get("emails") or []
        email_id = emails[0].get("id") if emails else None
        if email_id:
            action = {"action_type": "classify", "email_id": email_id, "label": "urgent"}
        else:
            # No emails to act on (e.g. budget exhausted): fall back to a
            # priority ordering, which does not require an email id.
            action = {"action_type": "prioritize", "priority_order": []}
        with self.client.post(
            "/step", json=action, name="POST /step", catch_response=True
        ) as response:
            # The env may legitimately report the episode is done; only transport
            # / server errors should count as failures here.
            if response.status_code >= 500:
                response.failure(f"step returned {response.status_code}")
            else:
                response.success()
