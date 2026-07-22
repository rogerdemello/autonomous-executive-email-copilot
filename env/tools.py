from __future__ import annotations

import json
from typing import Any, Literal

from .models import Action

ActionTypeStr = Literal["classify", "reply", "defer", "escalate", "prioritize"]
LabelTypeStr = Literal["spam", "normal", "urgent"]


def build_tool_definitions() -> list[dict[str, Any]]:
    """Build OpenAI-compatible tool definitions for all email actions."""
    return [
        {
            "type": "function",
            "function": {
                "name": "classify",
                "description": "Label an email as spam, normal, or urgent based on priority and business value",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "The ID of the email to classify",
                        },
                        "label": {
                            "type": "string",
                            "enum": ["spam", "normal", "urgent"],
                            "description": "The classification label",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for the classification decision",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence in this decision (0-1)",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["email_id", "label"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reply",
                "description": "Reply to an email with appropriate content matching sender role and context",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "The ID of the email to reply to",
                        },
                        "content": {
                            "type": "string",
                            "description": "The reply text content",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for replying now rather than deferring or escalating",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence in this decision (0-1)",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["email_id", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "escalate",
                "description": "Escalate an email to legal team or chief of staff due to risk or importance",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "The ID of the email to escalate",
                        },
                        "escalate_to": {
                            "type": "string",
                            "enum": ["legal_team", "chief_of_staff"],
                            "description": "Who to escalate to",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this email needs escalation",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence in this decision (0-1)",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["email_id", "escalate_to"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "defer",
                "description": "Defer an email for later processing when it is low priority or waiting on more information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "The ID of the email to defer",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this email is being deferred",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence in this decision (0-1)",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["email_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "prioritize",
                "description": "Order all emails by importance to set the processing sequence",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "priority_order": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Email IDs in priority order (most important first)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reasoning behind the priority ordering",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence in this ordering (0-1)",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["priority_order"],
                },
            },
        },
    ]


TOOL_DEFINITIONS = build_tool_definitions()


def parse_tool_call_to_action(function_name: str, arguments: str) -> Action | None:
    """Convert a tool call function name + JSON arguments into an Action model."""
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        return None

    action_type = function_name
    valid_types: list[str] = ["classify", "reply", "defer", "escalate", "prioritize"]
    if action_type not in valid_types:
        return None

    return Action(
        action_type=action_type,
        email_id=args.get("email_id"),
        label=args.get("label"),
        content=args.get("content"),
        priority_order=args.get("priority_order", []),
        escalate_to=args.get("escalate_to"),
    )


def extract_action_from_tool_calls(
    tool_calls: list[Any] | None,
) -> tuple[Action | None, dict[str, Any]]:
    """Extract the first valid action from a list of tool calls.

    Returns (action, metadata) where metadata includes reason, confidence.
    """
    if not tool_calls:
        return None, {}

    tc = tool_calls[0]
    function_name = getattr(tc, "function_name", None) or tc.get("function_name", "")
    arguments = getattr(tc, "arguments", None) or tc.get("arguments", "")

    action = parse_tool_call_to_action(function_name, arguments)
    if action is None:
        return None, {}

    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        args = {}

    metadata = {
        "reason": args.get("reason", f"Tool call: {function_name}"),
        "confidence": args.get("confidence", 0.9),
    }
    return action, metadata
