"""Security properties of the server-rendered surface.

The UI introduced three things the JSON API never had: a cookie the browser
sends automatically, HTML rendered from untrusted mailbox content, and a
redirect target supplied in a query string. Each gets pinned here.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.saas.deps import SESSION_COOKIE
from app.web.routes import _safe_next

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match
    return match.group(1)


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


# --------------------------------------------------------------------------- #
# Redirect safety
# --------------------------------------------------------------------------- #
class TestRedirectTarget:
    @pytest.mark.parametrize(
        "hostile",
        [
            "https://evil.example/phish",
            "http://evil.example",
            "//evil.example/phish",
            r"/\evil.example",  # browsers normalize the backslash into the authority
            r"\\evil.example",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "/app\r\nX-Injected: yes",  # control characters -> header injection
            "/app\nSet-Cookie: a=b",
            "/app\x00",
        ],
    )
    def test_off_site_and_malformed_targets_are_discarded(self, hostile):
        assert _safe_next(hostile) == "/app/inbox"

    @pytest.mark.parametrize(
        "legitimate",
        ["/app/inbox", "/app/settings", "/app/inbox?message=abc123", "/app/approvals"],
    )
    def test_in_site_targets_are_preserved(self, legitimate):
        assert _safe_next(legitimate) == legitimate

    def test_percent_encoded_slashes_stay_on_this_host(self):
        """%2f is not decoded before the authority is parsed, so this is a path."""
        assert _safe_next("/%2f%2fevil.example") == "/%2f%2fevil.example"


# --------------------------------------------------------------------------- #
# Untrusted mailbox content
# --------------------------------------------------------------------------- #
class TestUntrustedContentIsEscaped:
    def test_a_hostile_message_body_cannot_inject_script(self, client, monkeypatch):
        """Message bodies come from a real mailbox — an attacker writes them.

        Rendered through Jinja with autoescaping on, so the payload must appear
        as text, never as live markup.
        """
        from app.copilot.providers import demo as demo_mod
        from app.copilot.providers.base import FetchedMessage
        from app.copilot.providers.demo import DemoProvider

        payload = "<script>window.__pwned=1</script>"
        hostile = FetchedMessage(
            provider_message_id="m-xss",
            thread_id="t-xss",
            sender="attacker@evil.example",
            sender_name="<img src=x onerror=alert(1)>",
            subject=f"URGENT outage {payload}",
            body=f"failed {payload}",
            received_at="2026-08-16T09:00:00+00:00",
        )
        monkeypatch.setattr(DemoProvider, "fetch_messages", lambda self, *a, **k: [hostile])
        demo_mod._load.cache_clear()

        email = f"xss_{uuid.uuid4().hex[:8]}@northwind.example"
        page = client.get("/signup").text
        client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Acme",
                "full_name": "A Person",
                "email": email,
                "password": "a-strong-password",
            },
        )
        page = client.get("/app/connect").text
        client.post("/app/connect/demo", data={"csrf_token": csrf_from(page)})

        html = client.get("/app/inbox").text
        assert payload not in html, "raw <script> reached the page"
        assert "<img src=x onerror" not in html
        # It is present, but as escaped text.
        assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# Session cookie
# --------------------------------------------------------------------------- #
class TestSessionCookie:
    def _signup(self, client):
        email = f"c_{uuid.uuid4().hex[:8]}@northwind.example"
        page = client.get("/signup").text
        response = client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Acme",
                "full_name": "A Person",
                "email": email,
                "password": "a-strong-password",
            },
        )
        return response

    def test_cookie_is_not_readable_from_javascript(self, client):
        header = self._signup(client).headers["set-cookie"].lower()
        assert "httponly" in header

    def test_cookie_is_not_sent_on_cross_site_requests(self, client):
        header = self._signup(client).headers["set-cookie"].lower()
        assert "samesite=lax" in header

    def test_cookie_is_plain_http_only_in_development(self, client, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        header = self._signup(client).headers["set-cookie"].lower()
        assert "secure" not in header

    def test_cookie_is_secure_outside_development(self, client, monkeypatch):
        """A session cookie sent in the clear is one anyone on the path can take."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_SECRET_KEY", "a-long-random-production-secret")
        header = self._signup(client).headers["set-cookie"].lower()
        assert "secure" in header

    def test_logout_expires_the_cookie(self, client):
        self._signup(client)
        response = client.post(
            "/logout", data={"csrf_token": csrf_from(client.get("/app/inbox").text)}
        )
        header = response.headers["set-cookie"]
        assert SESSION_COOKIE in header
        assert "Max-Age=0" in header or "1970" in header


# --------------------------------------------------------------------------- #
# Demo credentials
# --------------------------------------------------------------------------- #
class TestDemoCredentialDisclosure:
    def test_the_demo_password_is_never_shown_in_production(self, client, monkeypatch):
        """Seeding the demo on a public deployment must not publish a password."""
        from app.web.routes import DEMO_OWNER_PASSWORD

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_SECRET_KEY", "a-long-random-production-secret")

        html = client.get("/login").text
        assert DEMO_OWNER_PASSWORD not in html
        assert "Demo workspace" not in html
