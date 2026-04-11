from fastapi.testclient import TestClient

from env.api import app
from env.environment import ExecutiveEmailEnv
from env.models import Action

client = TestClient(app)


def test_tasks_endpoint_and_persona_surface() -> None:
    response = client.get("/tasks")
    assert response.status_code == 200

    payload = response.json()
    assert len(payload["tasks"]) == 3

    observation_schema = payload["observation_schema"]
    assert "persona" in observation_schema["properties"]
    assert "remaining_interruptions" in observation_schema["properties"]


def test_runtime_reset_step_state_endpoints() -> None:
    reset_response = client.post(
        "/reset",
        json={
            "task_id": "easy_classification",
            "seed": 42,
            "persona": "balanced",
        },
    )
    assert reset_response.status_code == 200
    observation = reset_response.json()
    assert "emails" in observation
    assert observation["persona"] == "balanced"

    first_email_id = observation["emails"][0]["id"]
    step_response = client.post(
        "/step",
        json={
            "action_type": "classify",
            "email_id": first_email_id,
            "label": "normal",
        },
    )
    assert step_response.status_code == 200
    step_payload = step_response.json()
    assert "observation" in step_payload
    assert "reward" in step_payload
    assert "done" in step_payload

    state_response = client.post("/state", json={})
    assert state_response.status_code == 200
    state_payload = state_response.json()
    assert state_payload["task_id"] == "easy_classification"


def test_runtime_reset_accepts_empty_body() -> None:
    response = client.post("/reset")
    assert response.status_code == 200
    payload = response.json()
    assert "emails" in payload
    assert payload["persona"] == "balanced"


def test_interruptions_arrive_mid_episode() -> None:
    env = ExecutiveEmailEnv(task_id="hard_full_management", seed=42, persona="balanced")
    observation = env.reset(task_id="hard_full_management", seed=42, persona="balanced")
    initial_count = len(observation.emails)

    order = [email.id for email in observation.emails]
    seen_interruptions = []

    for _ in range(8):
        result = env.step(Action(action_type="prioritize", priority_order=order))
        seen_interruptions.extend(result.info.get("interruptions", []))

    assert len(env.state().emails) > initial_count
    assert "i1" in seen_interruptions


def test_baseline_endpoint_accepts_persona() -> None:
    response = client.post(
        "/baseline",
        json={
            "task_id": "hard_full_management",
            "seed": 42,
            "persona": "strict_ceo",
            "max_steps": 40,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["persona"] == "strict_ceo"
    assert 0.0 <= payload["score"] <= 1.0


def test_baseline_endpoint_supports_stress_mode() -> None:
    response = client.post(
        "/baseline",
        json={
            "task_id": "hard_full_management",
            "seed": 42,
            "persona": "balanced",
            "mode": "stress",
            "stress_rate": 0.5,
            "max_steps": 50,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "stress"
    assert payload["stress_rate"] == 0.5


def test_leaderboard_endpoint_available() -> None:
    response = client.post(
        "/leaderboard",
        json={
            "tasks": ["easy_classification"],
            "personas": ["balanced"],
            "seeds": [42],
            "max_steps": 60,
            "mode": "baseline",
            "stress_rate": 0.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "baseline"
    assert len(payload["rows"]) == 1
