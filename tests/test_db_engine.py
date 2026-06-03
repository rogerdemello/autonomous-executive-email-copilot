"""Tests for database engine configuration (env/db.py).

Covers the optional ``DATABASE_URL`` / Postgres support without requiring a live
Postgres server: we test the pure ``build_engine_kwargs`` builder directly and
confirm the default (no ``DATABASE_URL``) stays on SQLite with working CRUD.
"""

from __future__ import annotations

import pytest

from env import db
from env.config import get_settings
from env.repositories import EpisodeRepository

POSTGRES_URL = "postgresql+psycopg://user:pass@localhost:5432/email_copilot"
POSTGRES2_URL = "postgresql+psycopg2://user:pass@localhost:5432/email_copilot"
SQLITE_MEMORY_URL = "sqlite:///:memory:"


def test_default_resolves_to_sqlite(monkeypatch):
    """With no DATABASE_URL set, the app falls back to local SQLite."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_settings().database_url is None
    resolved = db.resolve_database_url()
    assert resolved.startswith("sqlite:///")
    assert resolved == db.DEFAULT_SQLITE_URL


def test_resolve_database_url_honors_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    assert db.resolve_database_url() == POSTGRES_URL


def test_resolve_database_url_ignores_blank(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert db.resolve_database_url() == db.DEFAULT_SQLITE_URL


def test_engine_kwargs_for_sqlite():
    """SQLite keeps its connect args and gets no pool tuning."""
    kwargs = db.build_engine_kwargs(db.DEFAULT_SQLITE_URL)
    assert kwargs["connect_args"] == {"check_same_thread": False}
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    assert "pool_pre_ping" not in kwargs
    assert "pool_recycle" not in kwargs


def test_engine_kwargs_for_sqlite_memory():
    kwargs = db.build_engine_kwargs(SQLITE_MEMORY_URL)
    assert kwargs["connect_args"] == {"check_same_thread": False}
    assert "pool_size" not in kwargs


@pytest.mark.parametrize("url", [POSTGRES_URL, POSTGRES2_URL])
def test_engine_kwargs_for_postgres_has_pool_tuning(url):
    """A Postgres URL yields pool params and no SQLite connect args."""
    kwargs = db.build_engine_kwargs(url)
    assert kwargs["pool_size"] == db.DEFAULT_POOL_SIZE
    assert kwargs["max_overflow"] == db.DEFAULT_MAX_OVERFLOW
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == db.DEFAULT_POOL_RECYCLE_SECONDS
    # SQLite-only connect args must not leak into a Postgres engine.
    assert "connect_args" not in kwargs


def test_default_engine_is_sqlite_and_crud_works():
    """The module-level engine defaults to SQLite and existing CRUD still works."""
    assert db.engine.dialect.name == "sqlite"
    assert db.DATABASE_URL.startswith("sqlite:///")

    db.init_db()
    repo = EpisodeRepository()
    saved = repo.save_episode(
        {
            "episode_id": "test-db-engine-ep-1",
            "task_id": "task-db-engine",
            "seed": 7,
            "persona": "balanced",
            "steps": 3,
            "score": 0.9,
            "total_reward": 1.5,
            "decisions": [{"step": 0, "action_type": "label"}],
        }
    )
    assert saved.episode_id == "test-db-engine-ep-1"

    fetched = repo.get_episode(episode_id="test-db-engine-ep-1")
    assert fetched is not None
    assert fetched.task_id == "task-db-engine"
    assert fetched.to_dict()["decisions"] == [{"step": 0, "action_type": "label"}]
