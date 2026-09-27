"""
Pryxor — Finance sector.

Rules:
    - Per-transaction limit
    - Whitelisted recipients
    - 24h velocity

Inherits from SimpleSector: the plumbing (SQLite, idempotence, velocity)
is provided by the base. This file contains ONLY the business logic.
"""

from __future__ import annotations

from typing import Any

from ._framework.base import Decision
from ._framework.simple import SimpleSector


class FinanceSector(SimpleSector):
    name = "finance"
    version = "2.0.0"

    def decide(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        if not isinstance(parameters, dict):
            return Decision.block("INVALID_TOOL_CALL", "'parameters' must be a dict.")

        try:
            amount = float(parameters.get("amount", 0.0))
        except (TypeError, ValueError):
            return Decision.block("INVALID_TOOL_CALL", "'amount' must be numeric.")

        recipient = parameters.get("recipient", "")

        # 1) Per-transaction limit
        max_single = float(self.sector_config.get("max_single_transaction", 0.0))
        if max_single > 0 and amount > max_single:
            return Decision.hold(
                "EXCEEDS_SINGLE_TRANSACTION_LIMIT",
                f"Amount {amount}$ > limit {max_single}$.",
                amount=amount,
                recipient=recipient,
            )

        # 2) Recipient whitelist
        allowed = self.sector_config.get("allowed_recipients", [])
        if recipient not in allowed:
            return Decision.hold(
                "RECIPIENT_NOT_WHITELISTED",
                f"Recipient '{recipient}' is not whitelisted.",
                amount=amount,
                recipient=recipient,
            )

        # 3) Velocity (provided by SimpleSector)
        velocity_limit = float(self.sector_config.get("velocity_limit", 0.0))
        window_hours = int(self.sector_config.get("velocity_window_hours", 24))
        if velocity_limit > 0:
            current = self.sum_window(window_hours)
            if current + amount > velocity_limit:
                return Decision.hold(
                    "VELOCITY_LIMIT_EXCEEDED",
                    f"Velocity {current + amount}$ > {velocity_limit}$ over {window_hours}h.",
                    amount=amount,
                    recipient=recipient,
                    current_volume=current,
                )

        return Decision.approve(
            f"Authorized. {amount}$ to {recipient}.",
            amount=amount,
            recipient=recipient,
        )
