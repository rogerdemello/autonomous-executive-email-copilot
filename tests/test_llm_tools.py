"""Parsing a model's tool call into an Action.

This is the boundary where untrusted model output becomes a command the system
acts on, so it has to be strict about what it accepts and total about what it
rejects — a malformed call must return ``None``, never raise into the agent
loop, and never fabricate an action type the environment does not implement.
"""

from __future__ import annotations

import json

import pytest

from app.llm.tools import (
    TOOL_DEFINITIONS,
    build_tool_definitions,
    extract_action_from_tool_calls,
    parse_tool_call_to_action,
)

VALID_ACTIONS = {"classify", "reply", "defer", "escalate", "prioritize"}


class TestToolDefinitions:
    def test_every_action_type_is_offered_to_the_model(self):
        names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
        assert names == VALID_ACTIONS

    def test_definitions_are_well_formed_openai_tool_schemas(self):
        for definition in build_tool_definitions():
            assert definition["type"] == "function"
            function = definition["function"]
            assert function["name"] and function["description"]
            assert function["parameters"]["type"] == "object"


class TestParseToolCall:
    def test_classify(self):
        action = parse_tool_call_to_action(
            "classify", json.dumps({"email_id": "e1", "label": "urgent"})
        )
        assert action is not None
        assert action.action_type == "classify"
        assert action.email_id == "e1"
        assert action.label == "urgent"

    def test_reply_carries_its_body(self):
        action = parse_tool_call_to_action(
            "reply", json.dumps({"email_id": "e2", "content": "On it — by 3pm."})
        )
        assert action is not None
        assert action.content == "On it — by 3pm."

    def test_escalate_carries_its_target(self):
        action = parse_tool_call_to_action(
            "escalate", json.dumps({"email_id": "e3", "escalate_to": "legal_team"})
        )
        assert action is not None
        assert action.escalate_to == "legal_team"

    def test_prioritize_carries_the_ordering(self):
        action = parse_tool_call_to_action(
            "prioritize", json.dumps({"priority_order": ["e3", "e1", "e2"]})
        )
        assert action is not None
        assert action.priority_order == ["e3", "e1", "e2"]

    def test_malformed_json_is_rejected_not_raised(self):
        """A truncated stream is the common failure; it must not crash the loop."""
        assert parse_tool_call_to_action("classify", "{not json") is None
        assert parse_tool_call_to_action("classify", "") is None

    @pytest.mark.parametrize("invented", ["delete_everything", "send_wire", "", "Classify"])
    def test_an_action_type_the_system_does_not_implement_is_rejected(self, invented):
        assert parse_tool_call_to_action(invented, json.dumps({"email_id": "e1"})) is None

    def test_missing_optional_fields_default_cleanly(self):
        action = parse_tool_call_to_action("defer", json.dumps({"email_id": "e1"}))
        assert action is not None
        assert action.priority_order == []
        assert action.content is None


class _ToolCall:
    """A provider-style tool call object (attribute access, not a dict)."""

    def __init__(self, function_name: str, arguments: str) -> None:
        self.function_name = function_name
        self.arguments = arguments


class TestExtractFromToolCalls:
    def test_no_tool_calls_yields_nothing(self):
        assert extract_action_from_tool_calls(None) == (None, {})
        assert extract_action_from_tool_calls([]) == (None, {})

    def test_reads_object_style_tool_calls(self):
        action, metadata = extract_action_from_tool_calls(
            [_ToolCall("classify", json.dumps({"email_id": "e1", "label": "spam"}))]
        )
        assert action is not None and action.label == "spam"
        assert metadata["confidence"] == 0.9

    def test_reads_dict_style_tool_calls(self):
        action, _metadata = extract_action_from_tool_calls(
            [{"function_name": "defer", "arguments": json.dumps({"email_id": "e9"})}]
        )
        assert action is not None and action.action_type == "defer"

    def test_carries_the_models_own_reason_and_confidence(self):
        action, metadata = extract_action_from_tool_calls(
            [
                _ToolCall(
                    "escalate",
                    json.dumps(
                        {
                            "email_id": "e1",
                            "escalate_to": "legal_team",
                            "reason": "contract liability",
                            "confidence": 0.42,
                        }
                    ),
                )
            ]
        )
        assert action is not None
        assert metadata["reason"] == "contract liability"
        assert metadata["confidence"] == 0.42

    def test_falls_back_to_a_descriptive_reason(self):
        _action, metadata = extract_action_from_tool_calls(
            [_ToolCall("defer", json.dumps({"email_id": "e1"}))]
        )
        assert "defer" in metadata["reason"]

    def test_only_the_first_call_is_used(self):
        """One step, one action — a model proposing several must not fan out."""
        action, _ = extract_action_from_tool_calls(
            [
                _ToolCall("defer", json.dumps({"email_id": "first"})),
                _ToolCall("reply", json.dumps({"email_id": "second"})),
            ]
        )
        assert action is not None
        assert action.email_id == "first"

    def test_an_unparseable_first_call_yields_nothing(self):
        action, metadata = extract_action_from_tool_calls([_ToolCall("classify", "{broken")])
        assert action is None
        assert metadata == {}
