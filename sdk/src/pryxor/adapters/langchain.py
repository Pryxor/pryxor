"""
PryxorTool — LangChain tool protected by Pryxor.

⚠️ This tool executes nothing locally.
    It sends the intent to Pryxor, which decides and then executes.
    The original function (if provided) is never called.
    It only serves as a contract / documentation for LangChain.

Compatible with:
    - langchain_core.tools.BaseTool
    - LangChain Agents (initialize_agent, AgentExecutor)
    - LangGraph (ToolNode)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Type

logger = logging.getLogger("pryxor.adapters.langchain")

# --- Optional import: if LangChain is not installed, keep going ---
try:
    from langchain_core.callbacks import (
        AsyncCallbackManagerForToolRun,
        CallbackManagerForToolRun,
    )
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel, ConfigDict, Field, create_model
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False
    BaseTool = object  # type: ignore
    BaseModel = object  # type: ignore
    ConfigDict = None  # type: ignore
    Field = lambda *a, **k: None  # type: ignore
    create_model = None  # type: ignore


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
# Helper: build an args_schema from a Python signature
# ---------------------------------------------------------------------------

_PY_TO_JSON = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _build_schema_from_callable(fn: Callable) -> Any:
    """
    Build a Pydantic model from a function signature.
    Used when the user does not provide an explicit args_schema.
    """
    if not _LANGCHAIN_AVAILABLE or create_model is None:
        return None

    import inspect

    sig = inspect.signature(fn)
    fields: dict[str, Any] = {}

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        annotation = param.annotation if param.annotation is not inspect.Parameter.empty else str
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[name] = (annotation, default)

    if not fields:
        return None

    return create_model(f"{fn.__name__}_Args", **fields)


# ---------------------------------------------------------------------------
# PryxorTool
# ---------------------------------------------------------------------------

if _LANGCHAIN_AVAILABLE:

    class PryxorTool(BaseTool):
        """
        LangChain tool whose execution is delegated to Pryxor.

        Minimal usage:

            tool = PryxorTool(
                name="send_payment",
                description="Send a payment to a recipient.",
                args_schema=PaymentSchema,
                pryxor_client=pryxor,
            )

        The LangChain agent calls `tool.run(...)` like any other tool. The
        tool sends the intent to Pryxor, which decides:
            - APPROVED → returns the result of the real execution
            - BLOCKED  → raises an exception with the reason
            - HOLD     → raises PryxorHoldPending with the action_id
        """
        name: str = ""
        description: str = ""
        args_schema: Optional[Type[Any]] = None
        # Non-pydantic fields (excluded from the schema)
        _pryxor: Any = None
        _raise_on_block: bool = True
        _raise_on_hold: bool = True
        model_config = ConfigDict(arbitrary_types_allowed=True)

        def __init__(
            self,
            pryxor_client: Any,
            name: str,
            description: str,
            # `BaseModel` is imported conditionally above, so static type
            # checkers treat it as a runtime variable rather than a valid
            # type expression when LangChain is unavailable.
            args_schema: Optional[Type[Any]] = None,
            raise_on_block: bool = True,
            raise_on_hold: bool = True,
            **kwargs: Any,
        ):
            super().__init__(
                name=name,
                description=description,
                args_schema=args_schema,
                **kwargs,
            )
            # Contournement : on stocke hors des fields pydantic
            object.__setattr__(self, "_pryxor", pryxor_client)
            object.__setattr__(self, "_raise_on_block", raise_on_block)
            object.__setattr__(self, "_raise_on_hold", raise_on_hold)

        # ------------------------------------------------------------------
        # Core: send to Pryxor
        # ------------------------------------------------------------------

        def _call_pryxor(self, arguments: dict[str, Any]) -> str:
            """Send the tool call to Pryxor. Return the result as text."""
            pryxor = self._pryxor
            if pryxor is None:
                raise PryxorProtectedToolError(
                    "No Pryxor client bound to this tool."
                )

            try:
                result = pryxor.execute(self.name, arguments)
                # APPROVED case with an execution result
                import json
                try:
                    return json.dumps(result, ensure_ascii=True, indent=2)
                except (TypeError, ValueError):
                    return str(result)

            except Exception as e:
                # Detect known Pryxor exceptions without importing the module
                cls_name = type(e).__name__

                if cls_name == "PryxorHoldPendingError":
                    action_id = getattr(e, "action_id", "unknown")
                    reason = getattr(e, "reason", "UNKNOWN")
                    message = getattr(e, "message", str(e))
                    if self._raise_on_hold:
                        raise PryxorHoldPending(action_id, reason, message) from e
                    return f"[HOLD] action_id={action_id} reason={reason}"

                if cls_name == "PryxorBlockedError":
                    reason = getattr(e, "reason", "UNKNOWN")
                    message = getattr(e, "message", str(e))
                    if self._raise_on_block:
                        raise PryxorProtectedToolError(
                            f"[BLOCKED] {reason}: {message}"
                        ) from e
                    return f"[BLOCKED] {reason}: {message}"

                if cls_name == "PryxorExecutionError":
                    message = getattr(e, "message", str(e))
                    raise PryxorProtectedToolError(
                        f"[EXECUTION_FAILED] {message}"
                    ) from e

                # Autre erreur Pryxor → on remonte
                raise PryxorProtectedToolError(f"Pryxor error: {e}") from e

        # ------------------------------------------------------------------
        # BaseTool API
        # ------------------------------------------------------------------

        def run(self, tool_input: Any = None, **kwargs: Any) -> str:
            """Support both LangChain input objects and direct keyword args."""
            if tool_input is None and kwargs:
                return self._run(**kwargs)
            return super().run(tool_input, **kwargs)

        def _run(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> str:
            # LangChain passe les arguments selon args_schema.
            # On reconstruit le dict proprement.
            arguments = self._extract_arguments(args, kwargs)
            return self._call_pryxor(arguments)

        async def _arun(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> str:
            # Async version: reuse the sync version.
            # Calls to Pryxor are themselves blocking but fast.
            # A truly async version will come in v2 (httpx.AsyncClient).
            return self._run(*args, **kwargs)

        # ------------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------------

        def _extract_arguments(
            self,
            args: tuple,
            kwargs: dict,
        ) -> dict[str, Any]:
            """
            Rebuild an arguments dict from what LangChain sends.
            Depending on the version, this can be:
                - direct kwargs
                - a single positional arg (dict)
                - mixed args + kwargs
            """
            if kwargs and not args:
                return dict(kwargs)

            if len(args) == 1 and isinstance(args[0], dict):
                return dict(args[0])

            if args or kwargs:
                # Fallback : on tente un mapping par nom depuis args_schema
                if self.args_schema is not None:
                    fields = list(self.args_schema.model_fields.keys())
                    result: dict[str, Any] = {}
                    for i, val in enumerate(args):
                        if i < len(fields):
                            result[fields[i]] = val
                    result.update(kwargs)
                    return result
                return {**{f"arg{i}": v for i, v in enumerate(args)}, **kwargs}

            return {}

        # ------------------------------------------------------------------
        # Conversion depuis un Tool LangChain existant
        # ------------------------------------------------------------------

        @classmethod
        def from_langchain_tool(
            cls,
            original: Any,
            pryxor_client: Any,
            raise_on_block: bool = True,
            raise_on_hold: bool = True,
        ) -> "PryxorTool":
            """
            Convert an existing LangChain tool into a protected tool.
            The original function is ignored — Pryxor performs the execution.
            """
            return cls(
                pryxor_client=pryxor_client,
                name=original.name,
                description=original.description,
                args_schema=getattr(original, "args_schema", None),
                raise_on_block=raise_on_block,
                raise_on_hold=raise_on_hold,
            )

else:
    # Stub when LangChain is not installed
    class PryxorTool:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            raise ImportError(
                "LangChain is not installed. Run:\n"
                "    pip install langchain-core\n"
                "to use the Pryxor LangChain adapter."
            )


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def pryxor_tool(
    tool_name: str,
    description: str,
    pryxor_client: Any | None = None,
    raise_on_block: bool = True,
    raise_on_hold: bool = True,
) -> Callable:
    """
    Decorator that turns a Python function into a PryxorTool.

    Usage:
        @pryxor_tool("send_payment", description="Send a payment")
        def send_payment(amount: float, recipient: str) -> str:
            '''This function is never called — Pryxor executes.'''
            ...

    If `pryxor_client` is not provided, a default client is created from the
    environment variables (PRYXOR_AGENT_KEY, PRYXOR_URL).
    """
    def decorator(fn: Callable) -> "PryxorTool":
        # Lazy import to avoid cycles.
        from pryxor.client import Pryxor
        client = pryxor_client or Pryxor()
        schema = _build_schema_from_callable(fn)

        return PryxorTool(
            pryxor_client=client,
            name=tool_name,
            description=description,
            args_schema=schema,
            raise_on_block=raise_on_block,
            raise_on_hold=raise_on_hold,
        )

    return decorator
