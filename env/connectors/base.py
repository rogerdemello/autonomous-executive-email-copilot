"""Connector contracts: the provider-neutral RawEmail and the read-only base class.

A connector's ONLY job is to produce ``Observation`` objects from a real inbox. It
never sends, deletes, or mutates mail — the abstract surface intentionally has no
such methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from env.models import Observation


@dataclass(frozen=True)
class RawEmail:
    """A provider-neutral, read-only snapshot of one fetched message.

    Deliberately carries only the fields an agent may legitimately see — there is
    no place here for ground-truth labels or expected actions.
    """

    id: str
    sender: str
    subject: str
    body: str
    references: list[str] = field(default_factory=list)


class ReadOnlyEmailConnector(ABC):
    """Base class for read-only inbox connectors.

    Subclasses implement ``fetch_raw`` (provider-specific) and inherit
    ``fetch_observation`` which maps into the un-privileged Observation schema.
    No send/delete/mutate methods exist by design.
    """

    @abstractmethod
    def fetch_raw(self, mailbox: str = "INBOX", limit: int = 20) -> list[RawEmail]:
        """Fetch up to ``limit`` messages from ``mailbox`` as RawEmail snapshots."""
        ...

    def fetch_observation(self, mailbox: str = "INBOX", limit: int = 20) -> Observation:
        # Local import keeps the heavy mapping out of the import path until used.
        from .mapping import to_observation

        return to_observation(self.fetch_raw(mailbox=mailbox, limit=limit))
