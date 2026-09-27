"""
Pryxor — SimpleSector: base class for custom sectors.

Provides for free:
    - self.storage        : idempotent storage
    - self.record(...)    : record an event
    - self.sum_window(h)  : sum of amounts over h hours
    - self.count_window(h): count of events over h hours
    - Idempotence via dedup_key
    - Automatic recording on direct approval and on approved HOLD

The developer implements ONLY `decide(agent_id, tool_name, parameters)`.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .base import Decision, SectorPolicy
from .storage import SectorStorage


class SimpleSector(SectorPolicy):
    """
    DX-friendly base class for custom sectors.
    """

    def __init__(self, policy: dict[str, Any], state_path: Path):
        super().__init__(policy, state_path)
        self.storage = SectorStorage(state_path)
        # Silent migration from the old schema if needed
        self.storage.migrate_from_v1_finance()

    @abstractmethod
    def decide(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        """The sector's business rules. Returns a Decision."""
        ...

    def evaluate(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        return self.decide(agent_id, tool_name, parameters)

    # ------------------------------------------------------------------
    # Default hooks: automatic recording
    # ------------------------------------------------------------------

    def record_approved(self, agent_id, tool_name, parameters):
        self._store(
            agent_id,
            tool_name,
            parameters,
            source="DIRECT_APPROVAL",
            dedup_key=f"direct:{uuid4().hex}",
        )

    def on_hold_approved(self, agent_id, tool_name, parameters, *, dedup_key=None):
        self._store(
            agent_id,
            tool_name,
            parameters,
            source="HOLD_APPROVED",
            dedup_key=dedup_key or f"orphan:{uuid4().hex}",
        )

    # ------------------------------------------------------------------
    # Helpers pour les sous-classes
    # ------------------------------------------------------------------

    def _store(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        source: str,
        dedup_key: str,
    ) -> None:
        # `amount` is extracted if present and numeric (for a fast SUM)
        amount: float | None = None
        if isinstance(parameters, dict):
            raw = parameters.get("amount")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                amount = float(raw)
        self.storage.record(
            sector_name=self.name,
            agent_id=agent_id,
            tool_name=tool_name,
            parameters=parameters,
            source=source,
            dedup_key=dedup_key,
            amount=amount,
        )

    def sum_window(
        self,
        hours: int,
        agent_id: str | None = None,
        tool_name: str | None = None,
    ) -> float:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return self.storage.sum_since(self.name, since, agent_id, tool_name)

    def count_window(
        self,
        hours: int,
        agent_id: str | None = None,
        tool_name: str | None = None,
    ) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return self.storage.count_since(self.name, since, agent_id, tool_name)

    def _current_volume(
        self,
        hours: int,
        agent_id: str | None = None,
        tool_name: str | None = None,
    ) -> float:
        return self.sum_window(hours, agent_id=agent_id, tool_name=tool_name)

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.storage.list_recent(self.name, limit)
