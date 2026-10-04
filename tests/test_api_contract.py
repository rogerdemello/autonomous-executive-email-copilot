from fastapi.testclient import TestClient

from app.core.models import Action
from app.main import app
from research.sim.environment import ExecutiveEmailEnv

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


def test_static_subroutes_not_shadowed_by_path_params() -> None:
    # Regression: /approval/{request_id} and /episodes/{episode_id} must not
    # shadow the static /approval/pending, /approval/history, /episodes/stats
    # routes (FastAPI matches in declaration order).
    pending = client.get("/approval/pending")
    assert pending.status_code == 200
    assert isinstance(pending.json(), list)

    history = client.get("/approval/history")
    assert history.status_code == 200
    assert isinstance(history.json(), list)

    stats = client.get("/episodes/stats")
    assert stats.status_code == 200
    assert isinstance(stats.json(), dict)


# --------------------------------------------------------------------------- #
# What /docs shows an engineer who opens it
# --------------------------------------------------------------------------- #
def _operations() -> list[tuple[str, str, dict]]:
    spec = app.openapi()
    return [(m.upper(), p, op) for p, ops in spec["paths"].items() for m, op in ops.items()]


def test_every_documented_endpoint_is_grouped() -> None:
    """46 of 67 endpoints used to sit under one undifferentiated heading.

    The product's own five groups were lost among the benchmark's routes, so the
    first thing /docs showed an evaluator was a flat wall of simulator calls.
    """
    untagged = [f"{method} {path}" for method, path, op in _operations() if not op.get("tags")]
    assert untagged == [], (
        f"endpoints with no tag (add a prefix to app.main._TAG_BY_PREFIX): {untagged}"
    )


def test_every_tag_in_use_is_described() -> None:
    spec = app.openapi()
    described = {tag["name"] for tag in spec["tags"]}
    used = {tag for _m, _p, op in _operations() for tag in op.get("tags", [])}
    assert used <= described, (
        f"tags with no description in _OPENAPI_TAGS: {sorted(used - described)}"
    )


def test_the_product_is_listed_before_the_benchmark() -> None:
    names = [tag["name"] for tag in app.openapi()["tags"]]
    assert names.index("inbox") < names.index("benchmark")
    assert names.index("auth") < names.index("benchmark")


def test_the_api_describes_the_product_not_only_the_simulator() -> None:
    info = app.openapi()["info"]
    assert "holds every outbound" in info["description"]
    assert "RL-style" not in info["description"]
    assert info["title"] == "Executive Email Copilot API"
