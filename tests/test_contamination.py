"""Tests for the eval-set contamination check (scripts/contamination_check.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contamination_check.py"
_spec = importlib.util.spec_from_file_location("contamination_check", _SCRIPT)
assert _spec and _spec.loader
contamination_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contamination_check)


def test_repo_has_no_contamination() -> None:
    # The agent-facing prompt surface must not expose any gold answer fields.
    assert contamination_check.check() == []


def test_poisoned_text_is_flagged() -> None:
    poison = "For reference the expected_reply_keywords are [contract, timeline]."
    assert "expected_reply_keywords" in contamination_check.find_gold_field_leakage(poison)


def test_clean_text_has_no_leakage() -> None:
    assert contamination_check.find_gold_field_leakage("A normal email about a contract.") == []
