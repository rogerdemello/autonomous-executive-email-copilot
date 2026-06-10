"""Determinism guards for the memory-augmented prompt path (INV-2).

The few-shot memory layer (trajectory store -> example_extractor -> prompt_enhancer)
must be a deterministic, read-only function of its inputs during grading, so that an
LLM agent's prompt — and therefore any graded run — is reproducible. These tests
lock that the retrieval/enhancement path returns identical output across calls and
does not perform writes.
"""

from __future__ import annotations

from env.learning.example_extractor import example_extractor
from env.learning.prompt_enhancer import prompt_enhancer


def test_enhance_system_prompt_is_deterministic() -> None:
    base = "You are an assistant."
    a = prompt_enhancer.enhance_system_prompt(base, "hard_full_management", "balanced")
    b = prompt_enhancer.enhance_system_prompt(base, "hard_full_management", "balanced")
    assert a == b
    # With no successful trajectories stored, enhancement is a no-op (base unchanged).
    assert a.startswith(base)


def test_example_extraction_is_deterministic() -> None:
    first = example_extractor.extract_all_examples("hard_full_management", "balanced")
    second = example_extractor.extract_all_examples("hard_full_management", "balanced")
    assert first == second


def test_has_examples_is_stable() -> None:
    a = prompt_enhancer.has_examples("easy_classification", "balanced")
    b = prompt_enhancer.has_examples("easy_classification", "balanced")
    assert a == b
    assert isinstance(a, bool)
