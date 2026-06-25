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

# Disable .env loading at conftest IMPORT time as well. The FastAPI app
# (``env.api``) reads settings such as ``CORS_ORIGINS`` when it is first imported
# — which happens at test-module collection, before the autouse fixture below can
# run. pytest imports this conftest before any test module, so setting the config
# here guarantees the app is built from a clean environment, not a developer's
# on-disk .env (e.g. a restricted CORS_ORIGINS would otherwise leak in).
config.Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Prevent the on-disk .env from bleeding into Settings during tests."""
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    yield
