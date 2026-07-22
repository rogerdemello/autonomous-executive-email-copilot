"""Locust load-testing script for the Executive Email Copilot API.

Usage::

    locust -f tests/load/locustfile.py --host=http://localhost:8000

Or headless::

    locust -f tests/load/locustfile.py --host=http://localhost:8000 \\
           --headless --users 10 --spawn-rate 1 --run-time 60s
"""

from locust import HttpUser, between, task


class CopilotUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Reset the environment to get a fresh episode."""
        resp = self.client.post("/reset", json={"task_id": "easy_classification"})
        if resp.status_code == 200:
            data = resp.json()
            self.episode_id = data.get("episode_id", "")
        else:
            self.episode_id = ""

    @task(3)
    def step(self):
        """Take a simple classify action."""
        if not self.episode_id:
            return
        self.client.post(
            "/step",
            json={
                "episode_id": self.episode_id,
                "action": {
                    "action_type": "classify",
                    "email_id": "msg_0",
                    "label": "not_spam",
                },
            },
            headers={"X-Request-ID": f"load-{self.episode_id}"},
        )

    @task(1)
    def health(self):
        """Check health endpoint."""
        self.client.get("/health")

    @task(2)
    def step_stream(self):
        """Test the SSE streaming endpoint."""
        if not self.episode_id:
            return
        self.client.post(
            "/step/stream",
            json={
                "episode_id": self.episode_id,
                "action": {
                    "action_type": "classify",
                    "email_id": "msg_0",
                    "label": "not_spam",
                },
            },
            headers={"Accept": "text/event-stream"},
        )

    @task(1)
    def state(self):
        """Fetch current environment state."""
        if not self.episode_id:
            return
        self.client.get(f"/state/{self.episode_id}")
