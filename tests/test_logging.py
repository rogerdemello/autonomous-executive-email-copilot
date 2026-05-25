"""Tests for request-id correlation and logging setup."""

from __future__ import annotations

from fastapi.testclient import TestClient

from env.api import app
from env.logging_config import configure_logging, set_request_id, get_request_id

client = TestClient(app)


def test_response_includes_request_id_header():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_request_id_echoes_incoming_header():
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers.get("X-Request-ID") == "abc123"


def test_set_request_id_roundtrip():
    rid = set_request_id("fixed-id")
    assert rid == "fixed-id"
    assert get_request_id() == "fixed-id"
    generated = set_request_id()
    assert generated and generated != "fixed-id"


def test_configure_logging_idempotent():
    # Should not raise or duplicate handlers when called repeatedly.
    configure_logging()
    configure_logging()
