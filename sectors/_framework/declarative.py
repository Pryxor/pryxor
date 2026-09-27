"""
Pryxor — DeclarativeSector: a sector driven by YAML/JSON rules.

No line of Python for the user.
Supported conditions:
    - tool / tool_in / tool_not_in
    - agent_id / agent_id_in
    - amount_gt / gte / lt / lte / eq / ne
    - <field>_in / <field>_not_in
    - <field>_matches (regex)
    - sum_last_Nh_gt   (SUM sur N heures)
    - count_last_Nh_gt (COUNT sur N heures)

Actions:
    - block: REASON
    - hold: REASON
    - approve: true
"""

from __future__ import annotations

import re
from typing import Any

from .base import Decision
from .simple import SimpleSector


class DeclarativeSector(SimpleSector):
    """
    Declarative sector. The name is injected by the loader.
    """

    version = "1.0.0"

    def __init__(
        self,
        policy: dict[str, Any],
        state_path,
        name: str = "declarative",
    ):
        # ⚠️ Set self.name BEFORE super(): the base uses it to read the config.
        self.name = name
        super().__init__(policy, state_path)

    def decide(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        rules = self.sector_config.get("rules", [])
        if not isinstance(rules, list) or not rules:
            return Decision.block(
                "NO_RULES_CONFIGURED",
                f"No rules defined for sector '{self.name}'.",
            )

        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            when = rule.get("when", {})
            then = rule.get("then", {})
            rule_name = rule.get("name", f"rule_{i}")

            if self._matches(when, agent_id, tool_name, parameters):
                return self._apply(then, rule_name)

        return Decision.approve("No rule matched.")

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _matches(
        self,
        when: dict[str, Any],
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> bool:
        if not isinstance(when, dict):
            return False

        # --- tool / agent ---
        if "tool" in when and when["tool"] != tool_name:
            return False
        if "tool_in" in when and tool_name not in when["tool_in"]:
            return False
        if "tool_not_in" in when and tool_name in when["tool_not_in"]:
            return False
        if "agent_id" in when and when["agent_id"] != agent_id:
            return False
        if "agent_id_in" in when and agent_id not in when["agent_id_in"]:
            return False

        # --- amount_* ---
        for op in ("gt", "gte", "lt", "lte", "eq", "ne"):
            key = f"amount_{op}"
            if key in when:
                try:
                    value = float(parameters.get("amount", 0.0))
                    target = float(when[key])
                except (TypeError, ValueError):
                    return False
                if op == "gt" and not value > target:
                    return False
                if op == "gte" and not value >= target:
                    return False
                if op == "lt" and not value < target:
                    return False
                if op == "lte" and not value <= target:
                    return False
                if op == "eq" and value != target:
                    return False
                if op == "ne" and value == target:
                    return False

        # --- Generic fields ---
        for field, expected in when.items():
            if field in (
                "tool",
                "tool_in",
                "tool_not_in",
                "agent_id",
                "agent_id_in",
            ):
                continue
            if field.startswith("amount_"):
                continue
            if field.startswith("sum_last_") or field.startswith("count_last_"):
                continue

            # Order matters: check the longest suffixes first, so `_domain_not_in`
            # is not swallowed by `_not_in`, and `_domain_in` not by `_in`.
            if field.endswith("_domain_not_in"):
                base = field[: -len("_domain_not_in")]
                if base in parameters:
                    value = str(parameters[base])
                    if "@" not in value:
                        return False
                    domain = value.split("@", 1)[1].lower()
                    blocked = [str(d).lower().lstrip("@") for d in expected]
                    if domain in blocked:
                        return False

            elif field.endswith("_domain_in"):
                base = field[: -len("_domain_in")]
                if base in parameters:
                    value = str(parameters[base])
                    if "@" not in value:
                        return False
                    domain = value.split("@", 1)[1].lower()
                    allowed = [str(d).lower().lstrip("@") for d in expected]
                    if domain not in allowed:
                        return False

            elif field.endswith("_not_in"):
                base = field[: -len("_not_in")]
                if base in parameters and parameters[base] in expected:
                    return False

            elif field.endswith("_in"):
                base = field[: -len("_in")]
                if base in parameters and parameters[base] not in expected:
                    return False

            elif field.endswith("_not_matches"):
                base = field[: -len("_not_matches")]
                if base in parameters:
                    try:
                        if re.search(expected, str(parameters[base])):
                            return False
                    except re.error:
                        return False

            elif field.endswith("_matches"):
                base = field[: -len("_matches")]
                if base in parameters:
                    try:
                        if not re.search(expected, str(parameters[base])):
                            return False
                    except re.error:
                        return False

            else:
                # strict equality
                if field in parameters and parameters[field] != expected:
                    return False
        # --- Velocity / rate limiting ---
        for field, value in when.items():
            if field.startswith("sum_last_") and field.endswith("_gt"):
                try:
                    hours_str = field[len("sum_last_") : -3]
                    hours = int(hours_str.rstrip("hH"))
                except ValueError:
                    continue
                if not self.sum_window(hours) > float(value):
                    return False
            if field.startswith("count_last_") and field.endswith("_gt"):
                try:
                    hours_str = field[len("count_last_") : -3]
                    hours = int(hours_str.rstrip("hH"))
                except ValueError:
                    continue
                if not self.count_window(hours) > float(value):
                    return False

        return True

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def _apply(self, then: dict[str, Any], rule_name: str) -> Decision:
        if not isinstance(then, dict):
            return Decision.approve(f"Rule '{rule_name}' has no action.")

        if "block" in then:
            return Decision.block(
                str(then["block"]),
                then.get("message", f"Blocked by rule '{rule_name}'."),
            )
        if "hold" in then:
            return Decision.hold(
                str(then["hold"]),
                then.get("message", f"Held by rule '{rule_name}'."),
            )
        if then.get("approve"):
            return Decision.approve(then.get("message", f"Approved by rule '{rule_name}'."))
        return Decision.approve(f"Rule '{rule_name}' matched, no action.")
