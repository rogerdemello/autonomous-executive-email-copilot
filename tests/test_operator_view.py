"""The operator health page: one URL that answers "is this working right now?"

All of this was already queryable — over seven JSON endpoints, with a bearer
token, from a shell. That is a fine way to run a workspace and a useless way to
answer the question that gets asked from a phone on a Sunday.

The properties worth pinning are the ones that make it safe to have at all: it
is unreachable without the operator token, it never reveals it exists on a
deployment that hasn't configured one, the token never lands in a URL, and one
broken query does not take down the page whose entire job is to be up when
things are broken.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.db import migrate_db
from app.main import app
from app.saas import operator_views
from app.saas.operator_views import OPERATOR_COOKIE, build_health

TOKEN = "test-operator-token"


@pytest.fixture(autouse=True)
def _schema():
    migrate_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", TOKEN)


@pytest.fixture
def signed_in(client, configured):
    response = client.post("/operator/session", data={"operator_token": TOKEN})
    assert response.status_code == 303
    assert response.headers["location"] == "/operator"
    return client


# --------------------------------------------------------------------------- #
# Getting in, and not getting in
# --------------------------------------------------------------------------- #
class TestAuth:
    def test_the_page_does_not_exist_without_an_operator_token(self, client, monkeypatch):
        """404, not 401: an unconfigured deployment should not admit to an admin surface."""
        monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
        assert client.get("/operator").status_code == 404
        assert client.get("/operator/session").status_code == 404
        assert client.post("/operator/session", data={"operator_token": "x"}).status_code == 404

    def test_an_anonymous_visit_is_sent_to_the_sign_in_form(self, client, configured):
        response = client.get("/operator")
        assert response.status_code == 303
        assert response.headers["location"] == "/operator/session"

    def test_a_wrong_token_does_not_sign_you_in(self, client, configured):
        response = client.post("/operator/session", data={"operator_token": "not-it"})
        assert response.status_code == 303
        assert response.headers["location"] == "/operator/session?error=1"
        assert OPERATOR_COOKIE not in response.cookies

    def test_an_empty_token_does_not_sign_you_in(self, client, configured):
        response = client.post("/operator/session", data={"operator_token": ""})
        assert OPERATOR_COOKIE not in response.cookies

    def test_the_right_token_gets_the_page(self, signed_in):
        response = signed_in.get("/operator")
        assert response.status_code == 200
        assert "Is it working?" in response.text

    def test_the_token_is_never_accepted_from_the_url(self, client, configured):
        """Query strings land in access logs, proxy logs and Referer headers."""
        response = client.get(f"/operator?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/operator/session"

    def test_the_cookie_is_httponly_and_scoped_to_the_operator_surface(self, client, configured):
        response = client.post("/operator/session", data={"operator_token": TOKEN})
        header = response.headers["set-cookie"]
        assert "HttpOnly" in header
        assert "Path=/operator" in header
        assert "samesite=strict" in header.lower()

    def test_rotating_the_operator_token_invalidates_outstanding_cookies(
        self, signed_in, monkeypatch
    ):
        """Which is the entire reason anyone rotates it."""
        assert signed_in.get("/operator").status_code == 200

        monkeypatch.setenv("OPERATOR_TOKEN", "a-brand-new-token")

        response = signed_in.get("/operator")
        assert response.status_code == 303
        assert response.headers["location"] == "/operator/session"

    def test_a_forged_cookie_is_refused(self, client, configured):
        client.cookies.set(OPERATOR_COOKIE, "not.a.real.token", path="/operator")
        assert client.get("/operator").status_code == 303

    def test_signing_out_clears_the_cookie(self, signed_in):
        response = signed_in.post("/operator/session/end")
        assert response.status_code == 303
        assert signed_in.get("/operator").status_code == 303

    def test_the_json_routes_still_answer_to_a_bearer_token(self, client, configured):
        """The view router mounts on the same prefix; it must not shadow them."""
        response = client.get("/operator/orgs", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert "organizations" in response.json()


# --------------------------------------------------------------------------- #
# What it says
# --------------------------------------------------------------------------- #
class TestTheNumbers:
    def _workspace(self, name: str) -> str:
        from app.saas.provisioning import provision_org

        return provision_org(
            org_name=name,
            owner_email=f"op-{uuid.uuid4().hex[:10]}@example.test",
            owner_name="Owner",
            password="correct horse battery staple",
        )["organization"]["id"]

    def _health(self, worker=None) -> dict:
        class _Request:
            class app:  # noqa: N801 - mimicking Starlette's shape
                class state:
                    sync_worker = None

        _Request.app.state.sync_worker = worker
        return build_health(_Request())

    def test_a_workspace_appears_with_its_owner(self):
        org_id = self._workspace("Health Co")
        health = self._health()

        row = next(w for w in health["workspaces"] if w["id"] == org_id)
        assert row["name"] == "Health Co"
        assert row["owner_email"]
        assert row["members"] == 1
        assert health["totals"]["workspaces"] >= 1

    def test_spend_is_attributed_to_the_workspace_that_incurred_it(self):
        from app.saas.repository import LlmUsageRepository

        org_id = self._workspace("Spendy Co")
        LlmUsageRepository().record(org_id=org_id, cost_usd=3.50, model="gpt-4o-mini")

        health = self._health()

        row = next(w for w in health["workspaces"] if w["id"] == org_id)
        assert row["spend_usd"] == pytest.approx(3.50)
        assert health["totals"]["spend_usd"] >= 3.50

    def test_the_workspaces_needing_attention_sort_first(self):
        """A status page ordered by creation date buries the thing you opened it for."""
        health = self._health()
        rows = health["workspaces"]
        actives = [i for i, r in enumerate(rows) if r["active"]]
        lapsed = [i for i, r in enumerate(rows) if not r["active"]]
        if lapsed and actives:
            assert max(lapsed) < min(actives)

    def test_a_disabled_worker_reads_as_off_not_broken(self):
        health = self._health(worker=None)
        assert health["worker"] == {"enabled": False}

    def test_a_stale_worker_is_reported_as_stale(self):
        from app.saas.sync_worker import BackgroundSyncWorker

        worker = BackgroundSyncWorker(poll_seconds=30, interval_seconds=900)
        worker._record_pass({"checked": 0}, datetime.now(timezone.utc) - timedelta(hours=6))

        health = self._health(worker=worker)

        assert health["worker"]["enabled"] is True
        assert health["worker"]["stale"] is True

    def test_the_budget_and_drafting_flags_are_shown(self):
        health = self._health()
        assert "budget_usd" in health
        assert "drafting_enabled" in health

    def test_a_workspace_at_its_cap_is_flagged(self, monkeypatch):
        """It has silently stopped getting model-written prose. That is the
        designed behaviour, and it is invisible from anywhere else."""
        from app.saas.repository import LlmUsageRepository

        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "25")
        org_id = self._workspace("Capped Co")
        LlmUsageRepository().record(org_id=org_id, cost_usd=25.50)

        health = self._health()

        row = next(w for w in health["workspaces"] if w["id"] == org_id)
        assert row["at_budget"] is True
        assert row["near_budget"] is False
        assert health["totals"]["at_budget"] >= 1

    def test_a_workspace_approaching_its_cap_is_warned_about(self, monkeypatch):
        """Far enough ahead to talk to the customer before their drafts change."""
        from app.saas.repository import LlmUsageRepository

        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "25")
        org_id = self._workspace("Nearly Capped Co")
        LlmUsageRepository().record(org_id=org_id, cost_usd=21.00)  # 84%

        row = next(w for w in self._health()["workspaces"] if w["id"] == org_id)

        assert row["near_budget"] is True
        assert row["at_budget"] is False

    def test_no_cap_means_no_flags(self, monkeypatch):
        from app.saas.repository import LlmUsageRepository

        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0")
        org_id = self._workspace("Uncapped Co")
        LlmUsageRepository().record(org_id=org_id, cost_usd=9_999.0)

        row = next(w for w in self._health()["workspaces"] if w["id"] == org_id)

        assert row["at_budget"] is False
        assert row["near_budget"] is False


class TestItStaysUpWhenThingsAreBroken:
    def test_one_failed_query_does_not_take_the_page_down(self, signed_in, monkeypatch):
        """The page whose job is to be up when things are broken, being up."""

        def _explode(*args, **kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(operator_views._orgs, "list_all", _explode)
        monkeypatch.setattr(operator_views._usage, "by_org", _explode)

        response = signed_in.get("/operator")

        assert response.status_code == 200
        assert "Is it working?" in response.text

    def test_a_hostile_worker_heartbeat_does_not_take_the_page_down(self, signed_in):
        class _Hostile:
            def heartbeat(self, *args, **kwargs):
                raise RuntimeError("nope")

        signed_in.app.state.sync_worker = _Hostile()
        try:
            assert signed_in.get("/operator").status_code == 200
        finally:
            signed_in.app.state.sync_worker = None
