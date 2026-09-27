"""
Pryxor — SectorPolicy interface.

A sector is a plugin that:
1. Receives an action (agent, tool, parameters).
2. Returns a Decision (APPROVED, BLOCKED, or HOLD).
3. May manage its own state (e.g. velocity) and its own persistence.

⚠️ A sector NEVER knows the raw identity from a payload.
    It always receives an agent_id already authenticated by the proxy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class DecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    HOLD = "HOLD"


@dataclass
class Decision:
    """Result of a sector evaluation."""

    status: DecisionStatus
    reason: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def approve(cls, message: str = "Action authorized.", **metadata: Any) -> "Decision":
        return cls(DecisionStatus.APPROVED, "APPROVED", message, metadata)

    @classmethod
    def block(cls, reason: str, message: str, **metadata: Any) -> "Decision":
        return cls(DecisionStatus.BLOCKED, reason, message, metadata)

    @classmethod
    def hold(cls, reason: str, message: str, **metadata: Any) -> "Decision":
        return cls(DecisionStatus.HOLD, reason, message, metadata)


class SectorPolicy(ABC):
    """
    Contract a sector must implement.

    Attributes:
        name    : unique sector identifier ("finance", "health", ...)
        version : sector version, useful for audit and compliance
    """

    name: str = "base"
    version: str = "0.0.0"

    def __init__(self, policy: dict[str, Any], state_path: Path):
        self.policy = policy
        self.state_path = Path(state_path)
        self.sector_config = (
            policy.get("sectors", {}).get(self.name, {}) if isinstance(policy, dict) else {}
        )

    @abstractmethod
    def evaluate(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        """Evaluate an action. MUST return a Decision."""
        ...

    def record_approved(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> None:
        """
        Callback invoked when the sector directly approves an action.
        The sector may record the transaction for its velocity tracking.
        Default: does nothing.
        """
        return None

    def on_hold_approved(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        *,
        dedup_key: str | None = None,
    ) -> None:
        """
        Callback invoked when a HOLD is approved.

        ⚠️ IDEMPOTENCE: `dedup_key` is stable for a given outbox event
        (e.g. "outbox:42"). The sector MUST use this key to guarantee its
        writes are not duplicated on replay (crash between commit and
        dispatch).

        Default: does nothing.
        """
        return None

    def on_hold_rejected(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        *,
        dedup_key: str | None = None,
    ) -> None:
        """
        Callback invoked when a HOLD is rejected.
        Same idempotence contract as on_hold_approved.
        Default: does nothing.
        """
        return None
