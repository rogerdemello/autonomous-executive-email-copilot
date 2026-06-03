"""Shared pytest fixtures.

Test isolation: ``env.config.Settings`` loads a developer's real ``.env`` file by
default (``SettingsConfigDict(env_file=".env")``). Without this fixture a local
``.env`` (e.g. a real ``HF_TOKEN``) leaks into ``get_settings()`` and breaks
config tests that assert on a clean environment. We disable ``.env`` loading for
the whole test session so tests depend only on ``monkeypatch.setenv`` and the
process environment, exactly as CI sees them.
"""

from __future__ import annotations

import pytest

from env import config


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Prevent the on-disk .env from bleeding into Settings during tests."""
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    yield
