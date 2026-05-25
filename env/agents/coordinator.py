"""Coordinator agent for delegating to specialized agents."""

from typing import Any

from .base import BaseAgent
from .classifier import ClassifierAgent
from .escalator import EscalatorAgent
from .responder import ResponderAgent
from env.models import Action, Observation


class AgentMessage:
    """Message passed between agents during coordination."""

    def __init__(
        self,
        from_agent: str,
        to_agent: str | None,
        content: Any,
        message_type: str = "proposal",
    ):
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.content = content
        self.message_type = message_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "content": self.content,
            "message_type": self.message_type,
        }


class CoordinatorAgent(BaseAgent):
    """Coordinator agent that delegates to specialists and resolves conflicts.

    The coordinator:
    - Maintains a registry of specialized agents
    - Delegates observations to appropriate agents
    - Resolves conflicts when multiple agents can handle the same observation
    - Implements inter-agent communication protocol
    """

    def __init__(self):
        super().__init__("CoordinatorAgent")
        self.classifier = ClassifierAgent()
        self.responder = ResponderAgent()
        self.escalator = EscalatorAgent()
        self.specialists: list[BaseAgent] = [self.classifier, self.responder, self.escalator]
        self.message_log: list[AgentMessage] = []

    @property
    def system_prompt(self) -> str:
        return (
            "I am a CoordinatorAgent responsible for delegating observations to specialized agents. "
            f"I manage {len(self.specialists)} specialists: ClassifierAgent, ResponderAgent, EscalatorAgent. "
            "I route observations to the appropriate agent and resolve conflicts when multiple agents can handle."
        )

    def can_handle(self, observation: Observation) -> bool:
        return any(agent.can_handle(observation) for agent in self.specialists)

    def execute(self, observation: Observation) -> Action | None:
        capable_agents = [agent for agent in self.specialists if agent.can_handle(observation)]
        if not capable_agents:
            return None
        if len(capable_agents) == 1:
            return self._delegate(observation, capable_agents[0])
        return self._resolve_conflict(observation, capable_agents)

    def _delegate(self, observation: Observation, agent: BaseAgent) -> Action | None:
        self._send_message(AgentMessage(self.name, agent.name, observation, "delegate"))
        action = agent.execute(observation)
        if action:
            self._send_message(AgentMessage(agent.name, self.name, action, "response"))
        return action

    def _resolve_conflict(self, observation: Observation, agents: list[BaseAgent]) -> Action | None:
        priority = self._get_priority_order()
        sorted_agents = sorted(agents, key=lambda a: priority.index(a.name) if a.name in priority else len(priority))
        self._broadcast_message(
            AgentMessage(self.name, None, {"candidates": [a.name for a in agents]}, "conflict_detection")
        )
        return self._delegate(observation, sorted_agents[0])

    def _get_priority_order(self) -> list[str]:
        return ["EscalatorAgent", "ClassifierAgent", "ResponderAgent"]

    def _send_message(self, message: AgentMessage) -> None:
        self.message_log.append(message)

    def _broadcast_message(self, message: AgentMessage) -> None:
        self.message_log.append(message)

    def get_messages(self) -> list[dict[str, Any]]:
        return [msg.to_dict() for msg in self.message_log]

    def clear_messages(self) -> None:
        self.message_log.clear()


class MultiAgentSystem:
    """System that manages the multi-agent coordination."""

    def __init__(self):
        self.coordinator = CoordinatorAgent()

    def process(self, observation: Observation) -> Action | None:
        return self.coordinator.execute(observation)

    def get_communication_log(self) -> list[dict[str, Any]]:
        return self.coordinator.get_messages()

    def clear_log(self) -> None:
        self.coordinator.clear_messages()