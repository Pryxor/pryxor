"""
Pryxor — Cloud sector.

Protects infrastructure actions (instances, clusters, storage, databases)
triggered by an AI agent. It is the "infra" counterpart of the finance
sector: same engine, a different policy.

Rules:
    - Destructive / irreversible actions → HOLD
    - Read actions (read_*, list_*, get_*) → APPROVED
    - Instance type / size outside the whitelist → BLOCK
    - Unauthorized region → BLOCK
    - Costly actions above a threshold → HOLD
    - Hourly budget exceeded (velocity) → HOLD

Inherits from SimpleSector: the plumbing (SQLite, idempotence, time
windows) is provided by the base. This file contains ONLY the business logic.
"""

from __future__ import annotations

import re
from typing import Any

from ._framework.base import Decision
from ._framework.simple import SimpleSector


class CloudSector(SimpleSector):
    name = "cloud"
    version = "1.0.0"

    #: Irreversible actions: always reviewed by a human.
    DESTRUCTIVE_PATTERN = re.compile(
        r"(?i)^(delete|terminate|destroy|drop|purge|remove|detach|revoke)_"
    )

    #: Read-only actions: always allowed (non-destructive, low-cost).
    READONLY_PATTERN = re.compile(r"(?i)^(read|list|get|describe|head|stat|fetch|search)_")

    #: "prod" keywords: an action on a production environment is more
    #: sensitive than one on dev/staging.
    PROD_PATTERN = re.compile(r"(?i)\b(prod|production|live|prd)\b")

    def decide(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        if not isinstance(parameters, dict):
            return Decision.block("INVALID_TOOL_CALL", "'parameters' must be a dict.")

        # SimpleSector's velocity plumbing sums the `amount` field. For cloud
        # actions the relevant quantity is the estimated hourly cost, so we
        # expose it under `amount` (without mutating the caller's dict) to make
        # the cumulative-budget check work out of the box.
        cost = self._as_float(
            parameters.get("estimated_cost_per_hour")
            or parameters.get("cost_per_hour")
            or parameters.get("hourly_cost")
            or parameters.get("amount")
        )
        if cost:
            parameters = {**parameters, "amount": cost}

        cfg = self.sector_config or {}

        # ------------------------------------------------------------------
        # 0) Read-only: approve early (most common, safest).
        # ------------------------------------------------------------------
        if self.READONLY_PATTERN.match(tool_name):
            return Decision.approve(
                f"Read-only action '{tool_name}' authorized.",
                tool_name=tool_name,
            )

        # ------------------------------------------------------------------
        # 1) Region: outside the allowed list → BLOCK.
        # ------------------------------------------------------------------
        allowed_regions = [str(r).lower() for r in (cfg.get("allowed_regions") or [])]
        region = str(parameters.get("region") or parameters.get("location") or "").lower()
        if allowed_regions and region and region not in allowed_regions:
            return Decision.block(
                "REGION_NOT_ALLOWED",
                f"Region '{region}' is not in the allowed list.",
                region=region,
            )

        # ------------------------------------------------------------------
        # 2) Type d'instance / taille : hors whitelist → BLOCK.
        # ------------------------------------------------------------------
        allowed_sizes = [str(s).lower() for s in (cfg.get("allowed_instance_types") or [])]
        instance_type = str(
            parameters.get("instance_type")
            or parameters.get("flavor")
            or parameters.get("size")
            or ""
        ).lower()
        if allowed_sizes and instance_type and instance_type not in allowed_sizes:
            return Decision.block(
                "INSTANCE_TYPE_NOT_ALLOWED",
                f"Instance type '{instance_type}' is not in the allowed list.",
                instance_type=instance_type,
            )

        # ------------------------------------------------------------------
        # 3) Action destructive → HOLD (toujours revue par un humain).
        # ------------------------------------------------------------------
        if self.DESTRUCTIVE_PATTERN.match(tool_name):
            on_prod = bool(
                self.PROD_PATTERN.search(
                    str(parameters.get("environment") or parameters.get("env") or "")
                )
            )
            reason = "DESTRUCTIVE_ACTION_ON_PROD" if on_prod else "DESTRUCTIVE_ACTION"
            return Decision.hold(
                reason,
                f"Destructive action '{tool_name}' requires human approval.",
                tool_name=tool_name,
                resource=str(parameters.get("resource_id") or parameters.get("id") or ""),
            )

        # ------------------------------------------------------------------
        # 4) Estimated cost above the threshold → HOLD.
        # ------------------------------------------------------------------
        max_cost = float(cfg.get("max_estimated_cost_per_hour", 0.0) or 0.0)
        estimated_cost = cost
        if max_cost > 0 and estimated_cost > max_cost:
            return Decision.hold(
                "COST_LIMIT_EXCEED",
                f"Estimated cost {estimated_cost}$/h > limit {max_cost}$/h.",
                estimated_cost=estimated_cost,
            )

        # ------------------------------------------------------------------
        # 5) Cumulative hourly budget (velocity) → HOLD.
        # ------------------------------------------------------------------
        velocity_limit = float(cfg.get("velocity_limit", 0.0) or 0.0)
        velocity_window = int(cfg.get("velocity_window_hours", 1) or 1)
        if velocity_limit > 0:
            already_spent = self._current_volume(
                velocity_window, agent_id=agent_id, tool_name=tool_name
            )
            if already_spent + estimated_cost > velocity_limit:
                return Decision.hold(
                    "VELOCITY_LIMIT_EXCEED",
                    f"Cumulative cost {already_spent + estimated_cost}$ "
                    f"> {velocity_window}h limit {velocity_limit}$.",
                    current_spend=already_spent,
                )

        return Decision.approve(
            f"Cloud action '{tool_name}' authorized.",
            tool_name=tool_name,
        )

    # ------------------------------------------------------------------
    # Cost normalization → `amount` (for the SimpleSector plumbing)
    # ------------------------------------------------------------------

    def record_approved(self, agent_id, tool_name, parameters):
        super().record_approved(agent_id, tool_name, self._with_amount(parameters))

    def on_hold_approved(self, agent_id, tool_name, parameters, *, dedup_key=None):
        super().on_hold_approved(
            agent_id, tool_name, self._with_amount(parameters), dedup_key=dedup_key
        )

    @classmethod
    def _with_amount(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        # Expose the estimated hourly cost as `amount` so velocity sums work.
        if not isinstance(parameters, dict):
            return parameters
        if "amount" in parameters:
            return parameters
        cost = cls._as_float(
            parameters.get("estimated_cost_per_hour")
            or parameters.get("cost_per_hour")
            or parameters.get("hourly_cost")
        )
        return {**parameters, "amount": cost} if cost else parameters

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
