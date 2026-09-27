"""
Pryxor — Tool-call format normalization.

This module turns the various tool-call formats (legacy, generic, native LLM)
into a single ``(tool_name, parameters)`` pair.

⚠️ FUNDAMENTAL SECURITY RULE:
    This function NEVER extracts the agent_id.
    Identity comes exclusively from authentication (X-Agent-Key).
    Any agent_id present in the payload is IGNORED.
"""

from __future__ import annotations

from typing import Any


def normalize_tool_call(
    payload: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    """
    Extract ``(tool_name, parameters)`` from:

    1. Legacy:
        {"tool_name": "send_payment", "parameters": {...}}

    2. Generic:
        {"type": "payment.create", "parameters": {...}, "context": {...}}

    3. Native LLM (OpenAI / Anthropic style):
        {"tool_call": {"function": {"name": "send_payment",
                                    "arguments": {...}}}}

    4. Alternate LLM:
        {"action": {"name": "send_payment", "parameters": {...}}}
    """
    if not isinstance(payload, dict):
        return None, {}

    # Sub-blocks (defensive: never assume the type)
    raw_action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
    raw_tool_call = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else {}
    function_block = (
        raw_tool_call.get("function") if isinstance(raw_tool_call.get("function"), dict) else {}
    )

    # --- tool_name ---
    tool_name = (
        payload.get("tool_name")
        or raw_action.get("tool_name")
        or payload.get("type")
        or raw_action.get("type")
        or raw_action.get("name")
        or function_block.get("name")
        or raw_tool_call.get("name")
    )

    # --- parameters ---
    parameters = (
        payload.get("parameters")
        or raw_action.get("parameters")
        or function_block.get("arguments")
        or {}
    )

    if not isinstance(parameters, dict):
        parameters = {}

    return tool_name, parameters
