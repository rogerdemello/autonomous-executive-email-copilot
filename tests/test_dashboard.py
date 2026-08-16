from fastapi.testclient import TestClient

from app.live_api import dashboard_router
from app.main import app

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
    # Version-agnostic: verify the dashboard routes respond rather than
    # introspecting app.routes. Starlette changed include_router internals
    # (1.3+) so included routes nest under an _IncludedRouter with no .path.
    assert client.get("/dashboard/health").status_code == 200
    assert client.get("/dashboard/state").status_code == 200
    assert client.post("/dashboard/reset").status_code == 200
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_root_serves_the_landing_page():
    """`/` is the product's front door, not a redirect into an ops console.

    It used to 307 to the React dashboard, which meant a visitor's first
    impression of the product was a benchmark tool. The page is now served
    directly, for both GET and HEAD.
    """
    for method in ("GET", "HEAD"):
        response = client.request(method, "/", follow_redirects=False)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


def test_dashboard_json_routes_survived_the_spa_removal():
    """Deleting the React app must not have taken its API with it.

    These endpoints back the live-state feed and are consumed by the WebSocket
    clients, independently of any particular frontend.
    """
    assert client.get("/dashboard/health").status_code == 200
    assert client.get("/dashboard/state").status_code == 200


def test_dashboard_default_task_reset():
    response = client.post("/dashboard/reset")
    assert response.status_code == 200
    data = response.json()
    assert "emails" in data
    assert "persona" in data


def test_dashboard_router_has_websocket():
    ws_routes = [r.path for r in dashboard_router.routes]
    assert any("/ws/dashboard" in r for r in ws_routes)
