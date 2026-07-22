"""Tests for server.app — importability, CLI entrypoint, and env-var wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_app_is_importable_and_callable() -> None:
    from server.app import app

    assert callable(app)


def test_main_exists_and_is_callable() -> None:
    from server.app import main

    assert callable(main)


def test_main_defaults_port_to_7860(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORT", raising=False)
    uvicorn_run = MagicMock()
    monkeypatch.setattr("uvicorn.run", uvicorn_run)

    from server.app import main

    main()

    uvicorn_run.assert_called_once_with("server.app:app", host="0.0.0.0", port=7860)


def test_main_reads_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8080")
    uvicorn_run = MagicMock()
    monkeypatch.setattr("uvicorn.run", uvicorn_run)

    from server.app import main

    main()

    uvicorn_run.assert_called_once_with("server.app:app", host="0.0.0.0", port=8080)
