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


# --------------------------------------------------------------------------- #
# Security headers
# --------------------------------------------------------------------------- #
class TestSecurityHeaders:
    def test_baseline_headers_on_every_surface(self, client):
        for path in ("/", "/login", "/health"):
            headers = client.get(path).headers
            assert headers["X-Content-Type-Options"] == "nosniff", path
            assert headers["X-Frame-Options"] == "DENY", path
            assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin", path
            assert "Content-Security-Policy" in headers, path
            assert "frame-ancestors 'none'" in headers["Content-Security-Policy"], path

    def test_docs_pages_are_exempt_from_csp_only(self, client):
        """Swagger UI loads its assets from a CDN; a same-origin CSP blanks it.

        The exemption is CSP alone — the other headers still apply."""
        headers = client.get("/docs").headers
        assert "Content-Security-Policy" not in headers
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_hsts_only_in_production(self, client, monkeypatch):
        """HSTS from a plain-HTTP dev server would poison localhost for HTTPS."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert "Strict-Transport-Security" not in client.get("/health").headers

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_SECRET_KEY", "a-long-random-production-secret")
        hsts = client.get("/health").headers.get("Strict-Transport-Security", "")
        assert "max-age=31536000" in hsts

    def test_csp_allows_no_inline_scripts(self):
        """base.html moved its theme snippet into /static/theme-init.js so the
        CSP can hold the line at script-src 'self'. Pin both halves."""
        from app.core.paths import STATIC_DIR, TEMPLATES_DIR

        assert (STATIC_DIR / "theme-init.js").is_file()
        base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
        assert "<script>" not in base


# --------------------------------------------------------------------------- #
# Login throttle
# --------------------------------------------------------------------------- #
class TestLoginThrottle:
    def test_off_by_default(self, client, monkeypatch):
        from app.core.security import login_rate_limiter

        monkeypatch.delenv("LOGIN_RATE_LIMIT_PER_MINUTE", raising=False)
        login_rate_limiter.reset()
        for _ in range(5):
            page = client.get("/login").text
            response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(page),
                    "email": "nobody@example.com",
                    "password": "wrong-password",
                },
            )
            assert response.status_code == 401
        login_rate_limiter.reset()

    def test_web_login_429s_past_the_limit(self, client, monkeypatch):
        from app.core import security
        from app.core.security import login_rate_limiter

        monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "2")
        # Freeze the limiter's clock: bcrypt makes each failed attempt slow
        # enough that three of them can straddle a real minute boundary, which
        # resets the fixed window mid-test.
        monkeypatch.setattr(security.time, "time", lambda: 1_000_000.0)
        login_rate_limiter.reset()
        statuses = []
        for _ in range(3):
            page = client.get("/login").text
            response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(page),
                    "email": "nobody@example.com",
                    "password": "wrong-password",
                },
            )
            statuses.append(response.status_code)
        assert statuses == [401, 401, 429]
        assert "Too many sign-in attempts" in response.text
        login_rate_limiter.reset()

    def test_api_login_429s_past_the_limit(self, client, monkeypatch):
        from app.core import security
        from app.core.security import login_rate_limiter

        monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "2")
        # Same frozen clock as the web variant — see the comment there.
        monkeypatch.setattr(security.time, "time", lambda: 1_000_000.0)
        login_rate_limiter.reset()
        statuses = [
            client.post(
                "/auth/login",
                json={"email": "nobody@example.com", "password": "wrong-password"},
            ).status_code
            for _ in range(3)
        ]
        assert statuses == [401, 401, 429]
        login_rate_limiter.reset()
