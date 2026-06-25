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
    # Version-agnostic: verify the dashboard routes respond rather than
    # introspecting app.routes. Starlette changed include_router internals
    # (1.3+) so included routes nest under an _IncludedRouter with no .path.
    assert client.get("/dashboard/health").status_code == 200
    assert client.get("/dashboard/state").status_code == 200
    assert client.post("/dashboard/reset").status_code == 200
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_dashboard_static_mount():
    # The dashboard SPA is mounted only when its build exists (the Docker image
    # or a local `npm run build`). The CI test job doesn't build it, so guard on
    # the dist being present.
    from env.api import dashboard_dist

    if dashboard_dist.exists():
        assert client.get("/dashboard/").status_code == 200


def test_root_serves_dashboard_when_built():
    # When the dashboard build is present, GET and HEAD / both redirect to the
    # dashboard (the app's landing page); otherwise / falls back to the info page.
    from env.api import dashboard_dist

    for method in ("GET", "HEAD"):
        resp = client.request(method, "/", follow_redirects=False)
        if dashboard_dist.exists():
            assert resp.status_code == 307
            assert resp.headers["location"] == "/dashboard/"
        else:
            assert resp.status_code == 200


def test_dashboard_default_task_reset():
    response = client.post("/dashboard/reset")
    assert response.status_code == 200
    data = response.json()
    assert "emails" in data
    assert "persona" in data


def test_dashboard_router_has_websocket():
    ws_routes = [r.path for r in dashboard_router.routes]
    assert any("/ws/dashboard" in r for r in ws_routes)
