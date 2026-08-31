"""Schema migrations must upgrade an existing database, not just a fresh one.

``create_all`` builds missing tables but never alters existing ones, so a new
column on a table that is already deployed is invisible to it. That made the
migration hook a stub with a comment rather than a mechanism. These tests pin
the behaviour that matters: an older database gains the column, keeps its rows,
and records the new version.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest
from sqlalchemy import inspect

_V1_PROCESSED_MESSAGES = """
CREATE TABLE saas_processed_messages (
  id VARCHAR(32) PRIMARY KEY,
  org_id VARCHAR(32),
  connection_id VARCHAR(32),
  provider_message_id VARCHAR(255),
  thread_id VARCHAR(255),
  sender VARCHAR(320),
  subject TEXT,
  body_preview TEXT,
  sender_role VARCHAR(32),
  priority_hint VARCHAR(16),
  risk_tag VARCHAR(32),
  deadline_minutes INTEGER,
  business_value FLOAT,
  synced_at VARCHAR(50)
);
INSERT INTO saas_processed_messages (id, org_id, subject)
VALUES ('m1', 'o1', 'a message from before the upgrade');

CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at VARCHAR(50));
INSERT INTO schema_version VALUES (1, '2026-01-01T00:00:00+00:00');
"""


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database at schema version 1, then the module rebound to it."""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(_V1_PROCESSED_MESSAGES)
    connection.commit()
    connection.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    from app.core import db as db_module

    reloaded = importlib.reload(db_module)
    yield path, reloaded

    # Put the module back on whatever database the session was actually using.
    #
    # `monkeypatch.undo()` rather than `delenv`: this fixture rebinds a
    # process-global engine, so the teardown has to restore the *previous*
    # DATABASE_URL, not assume there wasn't one. Under CI's Postgres job there
    # is — and deleting it here rebound `app.core.db.engine` to a SQLite file
    # that nothing had ever migrated, for the whole rest of the session. Every
    # test after this one then failed with "no such table: saas_users", in the
    # one job whose entire purpose is to prove Postgres works.
    monkeypatch.undo()
    importlib.reload(db_module)
    # And make sure the schema exists on it: conftest migrated the original
    # engine at import, which this reload has just replaced with a new object.
    db_module.migrate_db()


def test_migration_adds_columns_to_an_existing_table(legacy_db):
    path, db_module = legacy_db
    db_module.migrate_db()

    columns = {c["name"] for c in inspect(db_module.engine).get_columns("saas_processed_messages")}
    assert "received_at" in columns
    assert "sender_name" in columns


def test_migration_preserves_existing_rows(legacy_db):
    path, db_module = legacy_db
    db_module.migrate_db()

    connection = sqlite3.connect(path)
    subject = connection.execute("SELECT subject FROM saas_processed_messages").fetchone()[0]
    connection.close()
    assert subject == "a message from before the upgrade"


def test_migration_records_the_new_version(legacy_db):
    path, db_module = legacy_db
    db_module.migrate_db()

    connection = sqlite3.connect(path)
    version = connection.execute("SELECT max(version) FROM schema_version").fetchone()[0]
    connection.close()
    assert version == db_module._SCHEMA_VERSION


def test_migration_is_idempotent(legacy_db):
    """Re-running must not fail on the column it already added."""
    _path, db_module = legacy_db
    db_module.migrate_db()
    db_module.migrate_db()  # must not raise "duplicate column name"

    columns = {c["name"] for c in inspect(db_module.engine).get_columns("saas_processed_messages")}
    assert "received_at" in columns
