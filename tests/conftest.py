"""Shared pytest fixtures.

Two kinds of isolation happen here, both at IMPORT time rather than in a
fixture, because the things they protect are read when modules are first
imported — which pytest does during collection, before any fixture can run.

**Filesystem isolation.** ``app.core.paths`` resolves ``DATA_ROOT`` from
``DATA_DIR`` at import, and ``app.core.db`` derives the default SQLite path from
it and builds the engine immediately. Without the redirect below, the whole
suite reads and writes the developer's real ``data/episodes.db`` — which is how
that file grew to 80 MB and why a failing test could not be reproduced from a
clean slate. We copy the small read-only fixtures (the demo mailbox, scenarios,
the task and settings YAML) into a throwaway directory and point ``DATA_DIR``
there, so tests get real fixtures but every write lands in the temp tree.

Note this deliberately does *not* set ``DATABASE_URL``: leaving it unset keeps
the default-is-SQLite assertions in ``test_db_engine`` meaningful, and lets the
CI Postgres job override the backend without fighting this file.

**Config isolation.** ``app.core.config.Settings`` loads a developer's real
``.env`` by default (``SettingsConfigDict(env_file=".env")``). Without this a
local ``.env`` (e.g. a real ``HF_TOKEN``, or a restricted ``CORS_ORIGINS``)
leaks into ``get_settings()`` and breaks config tests that assert on a clean
environment. We disable ``.env`` loading for the whole session so tests depend
only on ``monkeypatch.setenv`` and the process environment, exactly as CI sees
them.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# --- Filesystem isolation (must run before any `app.` import) --------------- #

_REPO_DATA = Path(__file__).resolve().parents[1] / "data"


def _isolated_data_dir() -> str:
    """Copy the repo's read-only data fixtures into a throwaway directory.

    Databases are skipped rather than copied: they are outputs, and every one of
    them is recreated on demand by ``init_db``/``migrate_db``. Everything else
    in ``data/`` is small (~100 KB) input the tests legitimately need.
    """
    tmp = Path(tempfile.mkdtemp(prefix="eec-tests-"))
    if _REPO_DATA.is_dir():
        shutil.copytree(
            _REPO_DATA,
            tmp,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.db", "*.db-journal", "*.sqlite", "*.sqlite3"),
        )
    atexit.register(shutil.rmtree, tmp, True)
    return str(tmp)


# Respect an explicit DATA_DIR (someone pointing the suite at a fixture tree on
# purpose); otherwise redirect writes away from the working copy.
if not os.environ.get("DATA_DIR"):
    os.environ["DATA_DIR"] = _isolated_data_dir()

from app.core import config  # noqa: E402 - must follow the DATA_DIR redirect above
from app.core.db import migrate_db  # noqa: E402 - same

# Mirror what the app does at startup. The migration used to run as a side
# effect of importing app.main, which meant importing the app was enough to
# create the schema; it now runs in the FastAPI lifespan, and the module-level
# ``TestClient(app)`` most test modules use never triggers a lifespan. Doing it
# once here keeps the schema guaranteed and order-independent.
migrate_db()

# Disable .env loading at conftest IMPORT time as well. The FastAPI app
# (``app.main``) reads settings such as ``CORS_ORIGINS`` when it is first imported
# — which happens at test-module collection, before the autouse fixture below can
# run. pytest imports this conftest before any test module, so setting the config
# here guarantees the app is built from a clean environment, not a developer's
# on-disk .env.
config.Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Prevent the on-disk .env from bleeding into Settings during tests."""
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    yield
