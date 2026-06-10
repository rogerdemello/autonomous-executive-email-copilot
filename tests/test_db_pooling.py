"""Trajectory-store connection: WAL journal mode + reusable pooled connection."""

from __future__ import annotations

from env.learning.trajectory_store import trajectory_engine


def test_wal_journal_mode_enabled() -> None:
    with trajectory_engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert str(mode).lower() == "wal"


def test_connection_is_usable() -> None:
    with trajectory_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT 1").scalar() == 1
