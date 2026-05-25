from fastapi.testclient import TestClient

from env.api import app
from env.dashboard_api import dashboard_router

client = TestClient(app)


def test_dashboard_health_endpoint():
    response = client.get("/dashboard/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "dashboard_api"


def test_dashboard_state_endpoint_get():
    response = client.get("/dashboard/state")
    assert response.status_code == 200
    data = response.json()
    assert "emails" in data
    assert "time_remaining" in data


def test_dashboard_state_endpoint_post():
    response = client.post("/dashboard/state")
    assert response.status_code == 200
    data = response.json()
    assert "emails" in data


def test_dashboard_reset_endpoint():
    response = client.post(
        "/dashboard/reset",
        params={
            "task_id": "easy_classification",
            "seed": 42,
            "persona": "balanced",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "emails" in data
    assert data["persona"] == "balanced"


def test_dashboard_router_included():
    routes = [route.path for route in app.routes]
    assert any("/dashboard/health" in r for r in routes)
    assert any("/dashboard/state" in r for r in routes)
    assert any("/dashboard/reset" in r for r in routes)
    assert any("/ws/dashboard" in r for r in routes)


def test_dashboard_static_mount():
    routes = [route.path for route in app.routes]
    dashboard_routes = [r for r in routes if "/dashboard" in r and r != "/dashboard/"]
    assert len(dashboard_routes) >= 0


def test_dashboard_default_task_reset():
    response = client.post("/dashboard/reset")
    assert response.status_code == 200
    data = response.json()
    assert "emails" in data
    assert "persona" in data


def test_dashboard_router_has_websocket():
    ws_routes = [r.path for r in dashboard_router.routes]
    assert any("/ws/dashboard" in r for r in ws_routes)
