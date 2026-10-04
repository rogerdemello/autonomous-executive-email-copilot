"""Tests for the marketing surface: landing, security.txt, and the standing
rule that no page anywhere publishes a price or names a plan tier."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def frozen_minute(monkeypatch):
    """Pin the rate limiter's window for the duration of a test.

    ``FixedWindowRateLimiter`` buckets on ``int(time.time() // 60)``, so a burst
    that straddles a wall-clock minute boundary has its counter reset half way
    through and the request that should be refused is allowed. That made both
    throttle tests fail roughly once per hour of CI — the flake rate rises with
    however long the suite takes to reach them, which is not a property a
    release gate should have. Freezing the clock tests the actual rule (N
    submissions inside one window, then refusal) instead of racing it.

    Pins only the limiter's clock. Freezing the `time` module wholesale would
    also freeze CSRF token issue/expiry stamps, which is a much larger claim
    than this test needs to make.
    """
    import app.core.security as security

    real_allow = security.FixedWindowRateLimiter.allow
    monkeypatch.setattr(
        security.FixedWindowRateLimiter,
        "allow",
        lambda self, key, limit_per_minute, now=None: real_allow(
            self, key, limit_per_minute, now=1_800_000_000.0
        ),
    )


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


class TestBenchmarkIsMeasured:
    """The proof section is headed "Measured, not guessed."

    It used to be hardcoded <td> values and literal bar widths. These tests
    hold the rendered page to the artifact, and the artifact to the benchmark.
    """

    def test_every_rendered_score_comes_from_the_artifact(self, client):
        from app.web.routes import landing_metrics

        body = client.get("/").text
        artifact = landing_metrics()
        assert artifact["columns"], "the artifact must carry at least one column"
        for column in artifact["columns"]:
            assert column["label"] in body
            for cell in column["cells"].values():
                assert f"{cell['score']:.2f}" in body
                # The bar width is the score, not a separately-typed number.
                assert f"--v: {cell['score']}" in body

    def test_grid_tile_is_derived_not_asserted(self, client):
        from app.web.routes import landing_metrics

        grid = landing_metrics()["grid"]
        shape = f"{len(grid['tasks'])}×{len(grid['personas'])}×{len(grid['seeds'])}"
        assert shape in client.get("/").text

    def test_working_day_counts_come_from_the_demo_mailbox(self, client):
        """ "38 messages classified" described no run of anything. These are the
        shipped demo mailbox through the real policy."""
        from app.web.routes import landing_metrics

        demo = landing_metrics()["demo"]
        body = client.get("/").text
        assert str(demo["messages"]) in body
        assert str(demo["held_for_approval"]) in body
        assert str(demo["auto_applied"]) in body

    def test_a_missing_artifact_fails_loudly(self, monkeypatch, client):
        """Never fall back to invented numbers under a heading that says
        "measured" — a silent fallback is invisible in production."""
        import app.web.routes as routes

        monkeypatch.setattr(routes, "_landing_metrics_cache", None)
        monkeypatch.setattr(routes, "_LANDING_METRICS_PATH", Path("no-such-artifact.json"))
        with pytest.raises(RuntimeError, match="build_landing_metrics"):
            routes.landing_metrics()

    def test_the_artifact_agrees_with_a_fresh_benchmark_run(self):
        """The gate CI runs. Slow-ish but deterministic and offline: it is the
        only thing standing between the page and a stale number."""
        import scripts.build_landing_metrics as build

        assert build.check() == 0


def test_security_txt_served(client):
    resp = client.get("/.well-known/security.txt")
    assert resp.status_code == 200
    assert "Contact:" in resp.text
    assert "Expires:" in resp.text


PUBLIC_PAGES = [
    "/",
    "/login",
    "/signup",
    "/contact-sales",
    "/privacy",
    "/terms",
    "/forgot-password",
]


class TestNoPlaceholderContact:
    """A placeholder address is a statement that nobody is there.

    The deployed site rendered ``sales@example.com`` as a live mailto link in the
    footer of every public page, and told readers of the privacy policy to send
    their complaints and vulnerability reports to it. ``SALES_CONTACT_EMAIL`` was
    never set on the deployment, and the default was the placeholder.
    """

    @pytest.fixture(autouse=True)
    def _unconfigured(self, monkeypatch):
        monkeypatch.delenv("SALES_CONTACT_EMAIL", raising=False)
        monkeypatch.delenv("SECURITY_CONTACT_EMAIL", raising=False)

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_no_public_page_publishes_a_reserved_address(self, client, path):
        import re

        from app.saas.demo_seed import DEMO_OWNER_EMAIL

        # The sign-in page deliberately shows the shared demo account's address
        # (and prefills its password) whenever that account exists, and an
        # earlier test in a full run will have seeded it. That is a credential on
        # purpose, not a contact address, so it is not what this test is about.
        body = client.get(path).text.replace(DEMO_OWNER_EMAIL, "")
        offenders = re.findall(
            r"[\w.+-]+@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|\w+\.example)", body
        )
        assert not offenders, f"{path} publishes a placeholder address: {offenders}"
        assert "mailto:" not in body, f"{path} links an address nobody configured"

    def test_footer_points_at_the_contact_form_instead(self, client):
        body = client.get("/").text
        assert "Sales:" not in body
        assert 'href="/contact-sales"' in body

    def test_the_privacy_policy_still_tells_people_where_to_write(self, client):
        """Hiding the address must not leave the policy with a hole in it."""
        body = client.get("/privacy").text
        assert 'href="/contact-sales"' in body
        assert "GitHub&#39;s private vulnerability reporting" in body or (
            "GitHub's private vulnerability reporting" in body
        )

    def test_a_configured_address_is_published_everywhere_it_belongs(self, client, monkeypatch):
        monkeypatch.setenv("SALES_CONTACT_EMAIL", "hello@northwindlabs.io")
        for path in ("/", "/privacy", "/terms", "/contact-sales"):
            assert "mailto:hello@northwindlabs.io" in client.get(path).text, path

    @pytest.mark.parametrize(
        "copied", ["sales@example.com", "sales@acme.example", "x@sandbox.invalid"]
    )
    def test_a_copied_placeholder_is_treated_as_unset(self, client, monkeypatch, copied):
        """`.env.example` used to ship `sales@example.com`; copying it must not publish it."""
        monkeypatch.setenv("SALES_CONTACT_EMAIL", copied)
        assert "mailto:" not in client.get("/").text

    def test_security_txt_does_not_invent_a_security_mailbox(self, client):
        """It used to rewrite `sales@` to `security@` — a guess at a mailbox that may not exist."""
        body = client.get("/.well-known/security.txt").text
        assert "example" not in body.lower().split("policy:")[0]
        assert "Contact: https://github.com/" in body
        assert "/security/advisories/new" in body

    def test_security_txt_uses_a_configured_security_address(self, client, monkeypatch):
        monkeypatch.setenv("SECURITY_CONTACT_EMAIL", "security@northwindlabs.io")
        assert (
            "Contact: mailto:security@northwindlabs.io"
            in client.get("/.well-known/security.txt").text
        )

    def test_a_sales_address_is_never_promoted_to_a_security_one(self, client, monkeypatch):
        monkeypatch.setenv("SALES_CONTACT_EMAIL", "hello@northwindlabs.io")
        assert "security@northwindlabs.io" not in client.get("/.well-known/security.txt").text


@pytest.mark.parametrize("path", ["/", "/login", "/privacy", "/demo"])
def test_the_footer_links_to_the_source(client, path):
    """The project is open source and the code is the strongest proof on offer."""
    body = client.get(path).text
    assert 'href="https://github.com/rogerdemello/autonomous-executive-email-copilot"' in body
    assert "Source code" in body


class TestPublicCopyMatchesTheProduct:
    """Copy that sells a capability nobody built is a bug an engineer will find.

    The hero said the product "summarizes long threads", the marquee said
    "Threads summarized in seconds", and the privacy policy listed "thread
    summary" among the things it does with your mail. Nothing generates a thread
    summary: threads are grouped and shown, never summarized. "RAG" and "Semantic
    search" were chips on the landing page with no embedding, vector store or
    search behind them, and the "typed tools" pillar described the benchmark's LLM
    agent — the product's own routing is deterministic code and the model writes
    prose only.

    Each phrase below is off-limits *until the thing exists*. If you build it,
    delete the phrase here in the same change; that is the point of the friction.
    """

    NOT_BUILT = {
        "summarizes long threads": "no thread summarizer exists",
        "threads summarized": "no thread summarizer exists",
        "thread summary": "no thread summarizer exists",
        "semantic search": "no embeddings, vector store or search",
        ">rag<": "no retrieval index; the drafter gets one message, signals and style examples",
        "typed tools": "the product routes with BaselinePolicy; tool calling is the benchmark agent",
        "retrieves the thread": "the drafter is not given the thread's earlier messages",
    }

    @pytest.mark.parametrize("path", ["/", "/privacy", "/terms", "/contact-sales", "/demo"])
    def test_no_page_claims_what_is_not_built(self, client, path):
        body = client.get(path).text.lower()
        for phrase, why in self.NOT_BUILT.items():
            assert phrase not in body, f"{path} claims {phrase!r}, but {why}"

    def test_the_share_preview_does_not_either(self, client):
        """The meta description is what a link unfurls to, so it is public copy too."""
        import re

        html = client.get("/").text
        og = re.search(r'property="og:description"\s+content="([^"]*)"', html).group(1).lower()
        assert "summariz" not in og and "context-aware" not in og

    def test_what_the_page_says_instead_is_true(self, client):
        """The replacements name things with code behind them."""
        body = client.get("/").text
        assert (
            "Code decides, the model writes" in body
        )  # app/copilot/policy.py + app/llm/drafter.py
        assert "Draft verification" in body  # app/llm/verifier.py
        assert "Injection screening" in body  # app/llm/safety/guardrails.py


class TestLinkPreviewAndCrawlers:
    """A link someone pastes to a recruiter is read by a crawler before it is read
    by a person. Without Open Graph tags it unfurls as a bare title; without a
    canonical and a sitemap, a search engine guesses."""

    def _meta(self, html: str, key: str, attr: str = "property") -> str:
        import re

        match = re.search(rf'<meta\s+{attr}="{re.escape(key)}"\s+content="([^"]*)"', html)
        assert match, f"no <meta {attr}={key!r}> on the page"
        return match.group(1)

    def test_the_landing_page_unfurls_with_a_picture(self, client):
        html = client.get("/").text
        assert self._meta(html, "og:title").startswith("Executive Email Copilot")
        assert len(self._meta(html, "og:description")) > 60
        assert self._meta(html, "og:type") == "website"
        assert self._meta(html, "twitter:card", "name") == "summary_large_image"
        assert (self._meta(html, "og:image:width"), self._meta(html, "og:image:height")) == (
            "1200",
            "630",
        )

    def test_the_preview_image_is_absolute_and_actually_there(self, client):
        """A crawler has no page to resolve a relative URL against."""
        image = self._meta(client.get("/").text, "og:image")
        assert image.startswith("http://testserver/"), image
        response = client.get(image.removeprefix("http://testserver"))
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        # WhatsApp drops previews whose image is much past ~300 KB.
        assert 10_000 < len(response.content) < 300_000

    def test_the_canonical_url_is_this_page_without_the_query(self, client):
        html = client.get("/privacy?utm_source=linkedin").text
        assert '<link rel="canonical" href="http://testserver/privacy" />' in html
        assert self._meta(html, "og:url") == "http://testserver/privacy"

    def test_a_configured_public_url_wins(self, client, monkeypatch):
        monkeypatch.setenv("APP_PUBLIC_URL", "https://copilot.northwindlabs.io/")
        html = client.get("/").text
        assert (
            self._meta(html, "og:image")
            == "https://copilot.northwindlabs.io/static/img/og-card.jpg"
        )
        assert "https://copilot.northwindlabs.io/sitemap.xml" in client.get("/robots.txt").text

    def test_nothing_publishes_localhost_when_unconfigured(self, client, monkeypatch):
        """`resolved_app_public_url` falls back to http://localhost:8000."""
        monkeypatch.delenv("APP_PUBLIC_URL", raising=False)
        monkeypatch.delenv("OAUTH_REDIRECT_BASE_URL", raising=False)
        for path in ("/", "/robots.txt", "/sitemap.xml", "/.well-known/security.txt"):
            assert "localhost" not in client.get(path).text, path

    def test_private_pages_are_not_indexable_and_public_ones_are(self, client):
        public = client.get("/").text
        assert 'name="robots"' not in public

        signed_in = TestClient(app, follow_redirects=False)
        signed_in.post("/demo", data={"csrf_token": _csrf(signed_in.get("/").text)})
        assert (
            '<meta name="robots" content="noindex, nofollow" />' in signed_in.get("/app/inbox").text
        )

    def test_robots_txt_hides_the_product_and_points_at_the_sitemap(self, client):
        response = client.get("/robots.txt")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        assert "User-agent: *" in body
        for private in ("/app/", "/auth/", "/org/", "/mailbox/", "/inbox/", "/operator"):
            assert f"Disallow: {private}" in body, private
        assert "Sitemap: http://testserver/sitemap.xml" in body

    def test_every_sitemap_url_is_a_real_public_page(self, client):
        import xml.etree.ElementTree as ET

        response = client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text for el in ET.fromstring(response.text).findall("s:url/s:loc", ns)]
        assert locs and all(loc.startswith("http://testserver/") for loc in locs)
        for loc in locs:
            assert client.get(loc.removeprefix("http://testserver")).status_code == 200, loc
        assert "http://testserver/demo" in locs

    def test_the_sitemap_does_not_list_a_demo_that_is_off(self, client, monkeypatch):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "false")
        assert "/demo" not in client.get("/sitemap.xml").text

    def test_security_txt_names_the_real_origin(self, client):
        assert (
            "Canonical: http://testserver/.well-known/security.txt"
            in client.get("/.well-known/security.txt").text
        )


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

    def test_submissions_are_throttled(self, client, frozen_minute):
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

    def test_json_endpoint_is_throttled_too(self, client, frozen_minute):
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
