from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Prompt:
    name: str
    template: str
    version: str = "1.0.0"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def render(self, **kwargs: Any) -> str:
        result = self.template
        for key, val in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(val))
        return result


class PromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[str, Prompt] = {}
        self._versions: dict[str, list[Prompt]] = {}

    def register(self, prompt: Prompt) -> Prompt:
        self._prompts[prompt.name] = prompt
        self._versions.setdefault(prompt.name, []).append(prompt)
        logger.info("Registered prompt %s v%s", prompt.name, prompt.version)
        return prompt

    def get(self, name: str, version: str | None = None) -> Prompt | None:
        if version is None:
            return self._prompts.get(name)
        prompts = self._versions.get(name, [])
        for p in prompts:
            if p.version == version:
                return p
        return None

    def list_names(self) -> list[str]:
        return list(self._prompts.keys())

    def list_versions(self, name: str) -> list[str]:
        return [p.version for p in self._versions.get(name, [])]


registry = PromptRegistry()

registry.register(
    Prompt(
        name="system_prompt",
        template="""You are an AI Chief of Staff helping an executive manage their inbox efficiently.

Your role is to make optimal email management decisions based on:
- Email priority (high/medium/low)
- Business value (0-1 scale)
- Deadline urgency
- Risk level
- Persona preferences (strict_ceo/balanced/chill_manager)

Use the available tools to take the appropriate action. Guidelines:
- For high-value, high-urgency emails → reply immediately
- For legal/security risks → escalate immediately
- For low-value spam → classify and skip
- For unknown senders → defer initially
- Match reply tone to sender role (client: professional, internal: concise, vendor: brief)
""",
        description="Default system prompt for the LLM agent",
        tags=["llm-agent", "system"],
    )
)

registry.register(
    Prompt(
        name="planner_prompt",
        template="""You are a Strategic Planner for an AI Chief of Staff helping an executive manage their inbox.

Your role is to analyze the current inbox state and output a HIGH-LEVEL STRATEGY (not specific actions).

Available strategies:
1. PRIORITIZE_URGENT
2. BATCH_REPLY
3. ESCALATE_CRITICAL
4. DEFER_LOW_VALUE
5. MONITOR

Respond with a JSON object: {{"strategy": "STRATEGY_NAME", "reason": "..."}}
""",
        description="Default planner system prompt for strategy selection",
        tags=["planner", "strategy"],
    )
)
