"""Tests for the marketing surface: landing, security.txt, and the standing
rule that no page anywhere publishes a price or names a plan tier."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_landing_renders(client):
    resp = client.get("/welcome")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Executive Email Copilot" in resp.text
    assert "Start free" in resp.text


def test_landing_leads_with_the_self_serve_cta(client):
    """The motion is self-serve: connect an inbox, don't book a call."""
    body = client.get("/").text
    assert "Start free — connect your inbox" in body
    assert 'href="/signup"' in body


def test_landing_links_to_the_live_demo(client):
    """login.html prefills the demo credentials in production and nothing on
    the landing page used to link there — a visitor not ready to hand over a
    mailbox had nowhere to go."""
    body = client.get("/").text
    assert "Try the live demo" in body
    assert 'href="/login"' in body


def test_security_txt_served(client):
    resp = client.get("/.well-known/security.txt")
    assert resp.status_code == 200
    assert "Contact:" in resp.text
    assert "Expires:" in resp.text


def test_pricing_page_redirects_home(client):
    # There is no public pricing page; old links land home rather than on a 404.
    resp = client.get("/pricing", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/"


def test_pricing_json_is_gone(client):
    assert client.get("/api/pricing").status_code == 404


def test_landing_has_no_pricing_links(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/pricing" not in resp.text


@pytest.mark.parametrize("path", ["/", "/login", "/signup", "/contact-sales"])
def test_no_public_page_names_a_plan_tier(client, path):
    """A tier name the visitor cannot look up is worse than naming none.

    The landing page's hero note used to read "SSO & audit log on Business+",
    which invites exactly one question and there is no page that answers it.
    """
    body = client.get(path).text
    for tier in ("Business+", "Team plan", "Enterprise plan", "per seat", "/month"):
        assert tier not in body, f"{path} names a plan tier or a price: {tier}"


def test_marketing_is_public_even_with_operator_token(client, monkeypatch):
    # Marketing pages are GET (non-mutating) so they stay reachable regardless of
    # the operator API_AUTH_TOKEN gate.
    monkeypatch.setenv("API_AUTH_TOKEN", "operator-secret")
    assert client.get("/welcome").status_code == 200


# --------------------------------------------------------------------------- #
# The contact-sales funnel
# --------------------------------------------------------------------------- #
import re
import uuid

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match
    return match.group(1)


def _lead_emails() -> set[str]:
    from app.saas.repository import SalesLeadRepository

    return {lead["email"] for lead in SalesLeadRepository().list(limit=500)}


class TestContactSalesForm:
    def test_form_renders_publicly(self, client):
        response = client.get("/contact-sales")
        assert response.status_code == 200
        assert "Request a walkthrough" in response.text

    def test_landing_cta_points_at_the_form(self, client):
        assert 'href="/contact-sales"' in client.get("/").text

    def test_post_without_csrf_is_rejected(self, client):
        response = client.post("/contact-sales", data={"email": "p@example.com"})
        assert response.status_code == 403

    def test_submission_persists_a_lead(self, client):
        from app.core.security import lead_rate_limiter

        lead_rate_limiter.reset()
        email = f"lead-{uuid.uuid4().hex[:10]}@example.com"
        page = client.get("/contact-sales").text
        response = client.post(
            "/contact-sales",
            data={
                "csrf_token": _csrf(page),
                "email": email,
                "name": "Pat Prospect",
                "company": "Prospect Co",
                "seats": "12",
                "message": "We drown in email.",
            },
        )
        assert response.status_code == 200
        assert "Thanks" in response.text
        assert email in _lead_emails()

    def test_honeypot_pretends_success_but_drops_the_lead(self, client):
        from app.core.security import lead_rate_limiter

        lead_rate_limiter.reset()
        email = f"bot-{uuid.uuid4().hex[:10]}@example.com"
        page = client.get("/contact-sales").text
        response = client.post(
            "/contact-sales",
            data={
                "csrf_token": _csrf(page),
                "email": email,
                "website": "https://spam.example",
            },
        )
        assert response.status_code == 200
        assert "Thanks" in response.text
        assert email not in _lead_emails()

    def test_submissions_are_throttled(self, client):
        from app.core.security import LEAD_SUBMISSIONS_PER_MINUTE, lead_rate_limiter

        lead_rate_limiter.reset()
        page = client.get("/contact-sales").text
        statuses = []
        for i in range(LEAD_SUBMISSIONS_PER_MINUTE + 1):
            statuses.append(
                client.post(
                    "/contact-sales",
                    data={
                        "csrf_token": _csrf(page),
                        "email": f"burst-{i}-{uuid.uuid4().hex[:6]}@example.com",
                    },
                ).status_code
            )
        assert statuses[-1] == 429
        assert all(code == 200 for code in statuses[:-1])
        lead_rate_limiter.reset()

    def test_json_endpoint_is_throttled_too(self, client):
        from app.core.security import LEAD_SUBMISSIONS_PER_MINUTE, lead_rate_limiter

        lead_rate_limiter.reset()
        statuses = [
            client.post(
                "/billing/contact-sales",
                json={"email": f"api-{i}-{uuid.uuid4().hex[:6]}@example.com"},
            ).status_code
            for i in range(LEAD_SUBMISSIONS_PER_MINUTE + 1)
        ]
        assert statuses[-1] == 429
        lead_rate_limiter.reset()
