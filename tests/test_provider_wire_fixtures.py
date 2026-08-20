"""Recorded wire-format fixtures: what each provider actually sends.

`tests/test_provider_layer.py` proves the translation *logic*; these tests pin
the *payload*. One canonical request (system + user + one OpenAI-shaped tool)
runs through every provider with a recording client injected in place of the
SDK, and the exact kwargs handed to the SDK are compared byte-for-byte against
committed fixtures in ``tests/fixtures/provider_wire/``. Any change to what
goes over the wire — a renamed field, a lost ``system`` param, tools silently
dropped — fails loudly with a diff instead of surfacing as a provider 400 in
production.

To regenerate after an intentional wire-format change:

    REGEN_WIRE_FIXTURES=1 python -m pytest tests/test_provider_wire_fixtures.py

then review the fixture diff like any other code change.

Also here: the product-path failover test — the drafter, wrapped in the real
circuit breaker, produces prose from the secondary family when the primary
raises mid-call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.openai_provider import OpenAIProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider_wire"

# The one canonical request every provider is asked to send.
CANONICAL_MESSAGES = [
    {"role": "system", "content": "You draft executive email replies."},
    {"role": "user", "content": "Draft a reply confirming the £480k claim goes to counsel."},
]
CANONICAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_draft",
            "description": "Submit the drafted reply",
            "parameters": {
                "type": "object",
                "properties": {"body": {"type": "string"}},
                "required": ["body"],
            },
        },
    }
]


class RecordingOpenAIClient:
    """Stands in for the OpenAI SDK client; records the create() payload."""

    def __init__(self):
        self.captured: dict | None = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.captured = kwargs
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="ok", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                    model="gpt-4o-mini",
                    usage=None,
                )

        self.chat = SimpleNamespace(completions=_Completions())


class RecordingAnthropicClient:
    """Stands in for the Anthropic SDK client; records the create() payload."""

    def __init__(self):
        self.captured: dict | None = None
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.captured = kwargs
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="ok")],
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                    model="claude-3-5-sonnet-20241022",
                    stop_reason="end_turn",
                )

        self.messages = _Messages()


def _openai_payload() -> dict:
    provider = OpenAIProvider(model="gpt-4o-mini")
    client = RecordingOpenAIClient()
    provider._client = client  # bypass key detection; the SDK is the stub
    provider.generate(CANONICAL_MESSAGES, temperature=0.2, max_tokens=256, tools=CANONICAL_TOOLS)
    return client.captured


def _anthropic_payload() -> dict:
    provider = AnthropicProvider(api_key="test-key", model="claude-3-5-sonnet-20241022")
    client = RecordingAnthropicClient()
    provider._client = client
    provider.generate(CANONICAL_MESSAGES, temperature=0.2, max_tokens=256, tools=CANONICAL_TOOLS)
    return client.captured


@pytest.mark.parametrize(
    ("name", "build"),
    [("openai", _openai_payload), ("anthropic", _anthropic_payload)],
)
def test_wire_format_matches_the_recorded_fixture(name: str, build) -> None:
    payload = build()
    assert payload is not None, "the provider never called its client"
    path = FIXTURES / f"{name}_request.json"
    canonical = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("REGEN_WIRE_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical, encoding="utf-8")
    assert path.is_file(), (
        f"missing {path} — record it once with REGEN_WIRE_FIXTURES=1 and commit it"
    )
    assert json.loads(path.read_text(encoding="utf-8")) == payload, (
        f"the {name} wire format changed; if intentional, regenerate with "
        "REGEN_WIRE_FIXTURES=1 and review the fixture diff"
    )


class TestShapeInvariants:
    """Independent of the fixture files: the properties that must never drift."""

    def test_openai_keeps_the_openai_tool_shape(self):
        payload = _openai_payload()
        assert payload["tools"] == CANONICAL_TOOLS
        assert payload["messages"][0]["role"] == "system"

    def test_anthropic_translates_at_the_boundary(self):
        payload = _anthropic_payload()
        # System prompt is the top-level param, not a fake first turn.
        assert payload["system"] == "You draft executive email replies."
        assert all(m["role"] != "system" for m in payload["messages"])
        # Tools are input_schema-shaped, not the OpenAI function wrapper.
        tool = payload["tools"][0]
        assert tool["name"] == "submit_draft"
        assert "input_schema" in tool
        assert "function" not in tool


class TestFailoverProductPath:
    """The PLAN item's end state: a primary outage costs nothing but latency —
    the drafter's prose comes from the secondary family, mid-call."""

    class _DownProvider:
        provider_name = "openai"
        capabilities: set = set()

        def generate(self, *a, **k):
            raise RuntimeError("simulated provider outage")

    class _SecondaryProvider:
        provider_name = "anthropic"
        capabilities: set = set()

        def __init__(self):
            self.calls = 0

        def generate(self, *a, **k):
            self.calls += 1
            return SimpleNamespace(
                content='{"body": "Drafted by the secondary family.", '
                '"rationale": ["failover"], "confidence": 0.8}',
                model="claude-3-5-sonnet-20241022",
                usage=None,
            )

    def test_drafter_survives_a_primary_outage(self):
        from app.copilot.providers.base import FetchedMessage
        from app.llm.drafter import EmailDrafter
        from app.llm.providers import CircuitBreakingProvider

        secondary = self._SecondaryProvider()
        breaker = CircuitBreakingProvider(primary=self._DownProvider(), secondary=secondary)
        drafter = EmailDrafter(provider=breaker)
        result = drafter.draft(
            message=FetchedMessage(
                provider_message_id="m-1",
                thread_id="t-1",
                sender="a@b.c",
                sender_name="A",
                subject="Claim",
                body="Please confirm how we proceed.",
            ),
            action_type="reply",
        )
        assert result is not None
        assert result.body == "Drafted by the secondary family."
        assert secondary.calls == 1
