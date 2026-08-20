from __future__ import annotations

from unittest.mock import patch

from research.sim.learning.prompt_enhancer import PromptEnhancer


def test_enhance_system_prompt_returns_base_when_no_examples() -> None:
    enhancer = PromptEnhancer()
    base = "You are a helpful assistant."
    result = enhancer.enhance_system_prompt(base, "test_task", "balanced")
    assert result == base


def test_enhance_system_prompt_includes_examples_when_exist() -> None:
    fake_examples = {
        "classify": [{"email_id": "e1", "label": "urgent"}],
        "reply": [],
        "escalate": [],
        "prioritize": [],
    }
    enhancer = PromptEnhancer()
    with (
        patch.object(enhancer, "has_examples", return_value=True),
        patch(
            "research.sim.learning.prompt_enhancer.example_extractor.extract_all_examples",
            return_value=fake_examples,
        ),
    ):
        base = "You are a helpful assistant."
        result = enhancer.enhance_system_prompt(base, "test_task", "balanced")

    assert result.startswith(base)
    assert "## Few-shot examples from successful runs:" in result
    assert "Classify examples:" in result
    assert "e1 -> urgent" in result


def test_get_examples_for_action_returns_correct_examples() -> None:
    fake_examples = {
        "classify": [
            {"email_id": "e1", "label": "urgent"},
            {"email_id": "e2", "label": "spam"},
        ],
        "reply": [{"email_id": "e3", "content": "Thanks for the update"}],
        "escalate": [],
        "prioritize": [],
    }
    enhancer = PromptEnhancer()
    with patch(
        "research.sim.learning.prompt_enhancer.example_extractor.extract_all_examples",
        return_value=fake_examples,
    ):
        classify_examples = enhancer.get_examples_for_action("task", "balanced", "classify")
        reply_examples = enhancer.get_examples_for_action("task", "balanced", "reply")

    assert len(classify_examples) == 2
    assert classify_examples[0]["email_id"] == "e1"
    assert classify_examples[1]["label"] == "spam"
    assert len(reply_examples) == 1
    assert reply_examples[0]["email_id"] == "e3"


def test_get_examples_for_action_respects_max_examples() -> None:
    many_classify = [{"email_id": f"e{i}", "label": "urgent"} for i in range(10)]
    fake_examples = {
        "classify": many_classify,
        "reply": [],
        "escalate": [],
        "prioritize": [],
    }
    enhancer = PromptEnhancer(max_examples_per_action=3)
    with patch(
        "research.sim.learning.prompt_enhancer.example_extractor.extract_all_examples",
        return_value=fake_examples,
    ):
        result = enhancer.get_examples_for_action("task", "balanced", "classify")

    assert len(result) == 3


def test_has_examples_returns_false_when_no_examples() -> None:
    empty_examples = {"classify": [], "reply": [], "escalate": [], "prioritize": []}
    enhancer = PromptEnhancer()
    with patch(
        "research.sim.learning.prompt_enhancer.example_extractor.extract_all_examples",
        return_value=empty_examples,
    ):
        assert enhancer.has_examples("task", "balanced") is False


def test_has_examples_returns_true_when_examples_exist() -> None:
    nonempty_examples = {
        "classify": [{"email_id": "e1", "label": "urgent"}],
        "reply": [],
        "escalate": [],
        "prioritize": [],
    }
    enhancer = PromptEnhancer()
    with patch(
        "research.sim.learning.prompt_enhancer.example_extractor.extract_all_examples",
        return_value=nonempty_examples,
    ):
        assert enhancer.has_examples("task", "balanced") is True
