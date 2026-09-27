"""
PryxorTool — OpenAI Agents SDK tool protected by Pryxor.

⚠️ This tool executes nothing locally.
    It sends the intent to Pryxor, which decides and then executes.
    The original function (if provided) is never called.
    It only serves as a contract / documentation for the agent.

Compatible avec :
    - openai-agents (pip install openai-agents)
    - Agent + Runner
    - Toute fonction asynchrone ou sync
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("pryxor.adapters.openai_agents")

# --- Import optionnel ---
try:
    from agents import FunctionTool
    from agents.tool_context import ToolContext
    _OPENAI_AGENTS_AVAILABLE = True
except ImportError:
    _OPENAI_AGENTS_AVAILABLE = False
    FunctionTool = None  # type: ignore
    ToolContext = None  # type: ignore


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PryxorProtectedToolError(Exception):
    """Base error for protected tools."""


class PryxorHoldPending(PryxorProtectedToolError):
    """The tool was put on HOLD — a human must approve."""

    def __init__(self, action_id: str, reason: str, message: str):
        super().__init__(
            f"[HOLD] action_id={action_id} reason={reason} — {message}"
        )
        self.action_id = action_id
        self.reason = reason
        self.message = message


# ---------------------------------------------------------------------------
# JSON schema helper
# ---------------------------------------------------------------------------

_PY_TO_JSON = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _schema_from_signature(fn: Callable) -> dict[str, Any]:
    """
    Build an OpenAI-compatible JSON Schema from a signature.
    Used when the user does not provide a params_json_schema.
    """
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        annotation = param.annotation if param.annotation is not inspect.Parameter.empty else str
        json_type = _PY_TO_JSON.get(annotation, "string")
        properties[name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    # Non-strict by default → no need for additionalProperties: false
    return schema


# ---------------------------------------------------------------------------
# Core : appel Pryxor depuis un hook asynchrone
# ---------------------------------------------------------------------------

def _call_pryxor_sync(
    pryxor_client: Any,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    raise_on_block: bool,
    raise_on_hold: bool,
) -> str:
    """
    Call Pryxor (blocking) and turn the result into text ready for the
    OpenAI agent.
    """
    if pryxor_client is None:
        raise PryxorProtectedToolError("No Pryxor client bound to this tool.")

    try:
        result = pryxor_client.execute(tool_name, arguments)
        try:
            return json.dumps(result, ensure_ascii=True, indent=2)
        except (TypeError, ValueError):
            return str(result)

    except Exception as e:
        cls_name = type(e).__name__

        if cls_name == "PryxorHoldPendingError":
            action_id = getattr(e, "action_id", "unknown")
            reason = getattr(e, "reason", "UNKNOWN")
            message = getattr(e, "message", str(e))
            if raise_on_hold:
                raise PryxorHoldPending(action_id, reason, message) from e
            return f"[HOLD] action_id={action_id} reason={reason}"

        if cls_name == "PryxorBlockedError":
            reason = getattr(e, "reason", "UNKNOWN")
            message = getattr(e, "message", str(e))
            if raise_on_block:
                raise PryxorProtectedToolError(
                    f"[BLOCKED] {reason}: {message}"
                ) from e
            return f"[BLOCKED] {reason}: {message}"

        if cls_name == "PryxorExecutionError":
            message = getattr(e, "message", str(e))
            raise PryxorProtectedToolError(
                f"[EXECUTION_FAILED] {message}"
            ) from e

        # Toute autre exception Pryxor
        raise PryxorProtectedToolError(f"Pryxor error: {e}") from e


async def _call_pryxor_async(
    pryxor_client: Any,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    raise_on_block: bool,
    raise_on_hold: bool,
) -> str:
    """
    Async version. Pryxor's SDK is sync for now, so we delegate to a
    thread to avoid blocking the event loop.
    """
    import asyncio

    return await asyncio.to_thread(
        _call_pryxor_sync,
        pryxor_client,
        tool_name,
        arguments,
        raise_on_block=raise_on_block,
        raise_on_hold=raise_on_hold,
    )


# ---------------------------------------------------------------------------
# Factory PryxorTool
# ---------------------------------------------------------------------------

def _build_invoke_hook(
    pryxor_client: Any,
    tool_name: str,
    raise_on_block: bool,
    raise_on_hold: bool,
) -> Callable:
    """Construit le hook on_invoke_tool pour FunctionTool."""

    async def _invoke(ctx: Any, args_json: str) -> str:
        # Parse the JSON arguments (may be "" when there are none)
        arguments: dict[str, Any]
        if not args_json:
            arguments = {}
        else:
            try:
                arguments = json.loads(args_json)
            except json.JSONDecodeError as e:
                return f"[ERROR] Invalid JSON arguments: {e}"

            if not isinstance(arguments, dict):
                return f"[ERROR] Arguments must be a JSON object, got {type(arguments).__name__}"

        return await _call_pryxor_async(
            pryxor_client,
            tool_name,
            arguments,
            raise_on_block=raise_on_block,
            raise_on_hold=raise_on_hold,
        )

    return _invoke


def PryxorTool(  # noqa: N802 — factory qui remplace une classe
    pryxor_client: Any,
    name: str,
    description: str,
    params_json_schema: Optional[dict[str, Any]] = None,
    *,
    raise_on_block: bool = True,
    raise_on_hold: bool = True,
    strict_json_schema: bool = False,
    original_fn: Optional[Callable] = None,
) -> Any:
    """
    Create an OpenAI FunctionTool protected by Pryxor.

    Args:
        pryxor_client: instance de pryxor_client.Pryxor
        name: nom du tool (doit matcher allowed_actions dans la policy)
        description: a description the LLM can read
        params_json_schema: JSON Schema of the parameters (OpenAI format).
            If not provided, it is inferred from `original_fn`, or a
            permissive schema is used.
        raise_on_block: if True, BLOCKED raises an exception.
            If False, returns "[BLOCKED] REASON: message" as the output.
        raise_on_hold: idem pour HOLD.
        strict_json_schema: if True, adds additionalProperties: False.
        original_fn: reference function used to infer the schema.

    Returns a FunctionTool ready to be passed to an OpenAI Agent.
    """
    if not _OPENAI_AGENTS_AVAILABLE:
        raise ImportError(
            "openai-agents is not installed. Run:\n"
            "    pip install openai-agents\n"
            "to use the Pryxor OpenAI Agents adapter."
        )

    # Resolve the parameters schema
    if params_json_schema is None:
        if original_fn is not None:
            params_json_schema = _schema_from_signature(original_fn)
        else:
            params_json_schema = {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }

    # Application du flag strict
    if strict_json_schema:
        params_json_schema = dict(params_json_schema)
        params_json_schema["additionalProperties"] = False

    # Hook d'invocation
    invoke_hook = _build_invoke_hook(
        pryxor_client=pryxor_client,
        tool_name=name,
        raise_on_block=raise_on_block,
        raise_on_hold=raise_on_hold,
    )

    return FunctionTool(
        name=name,
        description=description,
        params_json_schema=params_json_schema,
        on_invoke_tool=invoke_hook,
        strict_json_schema=strict_json_schema,
    )


# ---------------------------------------------------------------------------
# Conversion depuis un FunctionTool existant
# ---------------------------------------------------------------------------

def _from_openai_tool(
    original: Any,
    pryxor_client: Any,
    *,
    raise_on_block: bool = True,
    raise_on_hold: bool = True,
) -> Any:
    """
    Convert an existing FunctionTool into a Pryxor-protected FunctionTool.
    The original function is ignored — Pryxor executes in its place.
    """
    return PryxorTool(
        pryxor_client=pryxor_client,
        name=original.name,
        description=getattr(original, "description", "") or "",
        params_json_schema=getattr(original, "params_json_schema", None),
        raise_on_block=raise_on_block,
        raise_on_hold=raise_on_hold,
        strict_json_schema=getattr(original, "strict_json_schema", False),
    )


# Attach the static method to the factory
PryxorTool.from_openai_tool = _from_openai_tool  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def pryxor_tool(
    tool_name: str,
    description: str,
    pryxor_client: Any | None = None,
    *,
    raise_on_block: bool = True,
    raise_on_hold: bool = True,
) -> Callable:
    """
    Decorator that turns a Python function into a protected FunctionTool.

    Usage:
        @pryxor_tool("send_email", description="Send an email")
        async def send_email(to: str, subject: str, body: str) -> str:
            '''This function is never called — Pryxor executes.'''
            ...

    The decorated function returns a FunctionTool, not a function.
    """
    def decorator(fn: Callable) -> Any:
        # Lazy import of the default Pryxor client
        from pryxor.client import Pryxor

        client = pryxor_client or Pryxor()

        return PryxorTool(
            pryxor_client=client,
            name=tool_name,
            description=description,
            params_json_schema=_schema_from_signature(fn),
            raise_on_block=raise_on_block,
            raise_on_hold=raise_on_hold,
            original_fn=fn,
        )

    return decorator
