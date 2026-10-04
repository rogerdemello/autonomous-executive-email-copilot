"""Tests for database engine configuration (app/core/db.py).

Covers the optional ``DATABASE_URL`` / Postgres support without requiring a live
Postgres server: we test the pure ``build_engine_kwargs`` builder directly and
confirm the default (no ``DATABASE_URL``) stays on SQLite with working CRUD.
"""

from __future__ import annotations

import pytest

from app.core import db
from app.core.config import get_settings
from app.core.repositories import EpisodeRepository

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


@pytest.mark.parametrize(
    "raw",
    [
        # Render / Heroku hand out the short scheme, which SQLAlchemy 2 rejects.
        "postgres://user:pass@host:5432/copilot",
        # The plain scheme selects psycopg2, which is not the driver we ship.
        "postgresql://user:pass@host:5432/copilot",
    ],
)
def test_resolve_database_url_normalizes_postgres_schemes(monkeypatch, raw):
    monkeypatch.setenv("DATABASE_URL", raw)
    assert db.resolve_database_url() == "postgresql+psycopg://user:pass@host:5432/copilot"


def test_resolve_database_url_respects_explicit_driver(monkeypatch):
    """An explicit +driver choice is the operator's; never rewrite it."""
    monkeypatch.setenv("DATABASE_URL", POSTGRES2_URL)
    assert db.resolve_database_url() == POSTGRES2_URL


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


@pytest.mark.skipif(
    "sqlite" not in db.DATABASE_URL, reason="SQLite pragmas only apply to the SQLite backend"
)
def test_sqlite_connections_run_in_wal_mode():
    """A web process commits once per repository call; SQLite's defaults fsync each.

    Measured on the demo workspace (~260 commits): 3.7s on the defaults, 0.8s
    with WAL + synchronous=NORMAL. WAL also stops the background sync worker's
    writes from locking out request threads. Asserted on a *new* connection
    because the pragmas are per-connection, not stored in the file's header
    (except the journal mode, which is why that one is also safe to read back).
    """
    with db.engine.connect() as connection:
        journal = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        synchronous = connection.exec_driver_sql("PRAGMA synchronous").scalar()
        busy = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(journal).lower() == "wal"
    assert synchronous == 1  # NORMAL
    assert busy == 5000


def test_sqlite_pragmas_are_not_applied_to_postgres():
    """The listener is attached only for the SQLite dialect.

    A ``PRAGMA`` sent to Postgres is a syntax error at connect time, so this is
    what keeps the CI Postgres job from failing on its first query. Neither
    engine is connected to: ``create_engine`` is lazy, and the question is only
    whether the listener got registered.
    """
    from sqlalchemy import create_engine, event

    postgres = create_engine(POSTGRES_URL)
    sqlite = create_engine(SQLITE_MEMORY_URL)
    try:
        assert db.attach_sqlite_pragmas(postgres) is False
        assert not event.contains(postgres, "connect", db.configure_sqlite_connection)

        assert db.attach_sqlite_pragmas(sqlite) is True
        assert event.contains(sqlite, "connect", db.configure_sqlite_connection)
    finally:
        postgres.dispose()
        sqlite.dispose()


def test_default_engine_is_sqlite_and_crud_works():
    """The module-level engine defaults to SQLite and existing CRUD still works.

    The SQLite assertions only apply when nothing overrode the default — the
    CI Postgres job runs this same suite with DATABASE_URL pointing at a real
    server, where the CRUD half is exactly what we want exercised."""
    import os

    if not os.environ.get("DATABASE_URL"):
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
