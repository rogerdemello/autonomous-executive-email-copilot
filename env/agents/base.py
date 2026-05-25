"""Base agent abstract class for multi-agent coordination system."""

from abc import ABC, abstractmethod
from typing import Any

from env.models import Action, Observation


class BaseAgent(ABC):
    """Abstract base class for all specialized agents.

    Each agent must implement:
    - system_prompt: A description of the agent's role and capabilities
    - execute(): Process observation and return an action
    - can_handle(): Determine if this agent can handle the given observation
    """

    def __init__(self, name: str):
        """Initialize the agent with a name.

        Args:
            name: The agent's identifier
        """
        self.name = name

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Return the agent's system prompt describing its role.

        Returns:
            A string describing the agent's role, responsibilities, and capabilities
        """
        ...

    @abstractmethod
    def execute(self, observation: Observation) -> Action | None:
        """Process the observation and return an action.

        Args:
            observation: The current environment observation

        Returns:
            An Action if the agent can handle this observation, None otherwise
        """
        ...

    @abstractmethod
    def can_handle(self, observation: Observation) -> bool:
        """Determine if this agent can handle the given observation.

        Args:
            observation: The current environment observation

        Returns:
            True if this agent can handle the observation, False otherwise
        """
        ...

    def __repr__(self) -> str:
        """Return string representation of the agent."""
        return f"{self.__class__.__name__}(name={self.name!r})"