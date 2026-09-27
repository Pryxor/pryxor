"""
PryxorTool — CrewAI tool protected by Pryxor.

⚠️ This tool executes nothing locally.
    It sends the intent to Pryxor, which decides and then executes.
    The original function (if provided) is never called.
    It only serves as a contract / documentation for CrewAI.

Compatible with:
    - crewai>=0.60
    - Crew + Agent + Task
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable, Optional, Type

logger = logging.getLogger("pryxor.adapters.crewai")

# --- Optional import ---
try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, ConfigDict, Field, create_model
    _CREWAI_AVAILABLE = True
except ImportError:
    _CREWAI_AVAILABLE = False
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
# Helper : construire un args_schema Pydantic depuis une signature
# ---------------------------------------------------------------------------

def _schema_from_signature(fn: Callable) -> Any:
    if not _CREWAI_AVAILABLE or create_model is None:
        return None

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
# Core : appel Pryxor
# ---------------------------------------------------------------------------

def _call_pryxor(
    pryxor_client: Any,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    raise_on_block: bool,
    raise_on_hold: bool,
) -> str:
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

        raise PryxorProtectedToolError(f"Pryxor error: {e}") from e


# ---------------------------------------------------------------------------
# PryxorTool
# ---------------------------------------------------------------------------

if _CREWAI_AVAILABLE:

    class PryxorTool(BaseTool):
        """
        CrewAI tool whose execution is delegated to Pryxor.

        Utilisation minimale :

            tool = PryxorTool(
                pryxor_client=pryxor,
                name="send_email",
                description="Send an email.",
                args_schema=EmailArgs,
            )

        CrewAI l'appellera comme n'importe quel tool.
        """

        name: str = ""
        description: str = ""
        args_schema: Optional[Type[BaseModel]] = None

        # Private fields (outside Pydantic)
        _pryxor_client: Any = None
        _raise_on_block: bool = True
        _raise_on_hold: bool = True
        model_config = ConfigDict(arbitrary_types_allowed=True)

        def __init__(
            self,
            pryxor_client: Any,
            name: str,
            description: str,
            args_schema: Optional[Type[BaseModel]] = None,
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
            object.__setattr__(self, "_pryxor_client", pryxor_client)
            object.__setattr__(self, "_raise_on_block", raise_on_block)
            object.__setattr__(self, "_raise_on_hold", raise_on_hold)

        # ------------------------------------------------------------------
        # CrewAI BaseTool API
        # ------------------------------------------------------------------

        def _run(self, *args: Any, **kwargs: Any) -> str:
            arguments = self._extract_arguments(args, kwargs)
            return _call_pryxor(
                self._pryxor_client,
                self.name,
                arguments,
                raise_on_block=self._raise_on_block,
                raise_on_hold=self._raise_on_hold,
            )

        # ------------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------------

        def _extract_arguments(
            self,
            args: tuple,
            kwargs: dict,
        ) -> dict[str, Any]:
            """Reconstitue un dict d'arguments depuis ce que CrewAI envoie."""
            if kwargs and not args:
                return dict(kwargs)

            if len(args) == 1 and isinstance(args[0], dict):
                return dict(args[0])

            if args or kwargs:
                if self.args_schema is not None:
                    # Get the field names depending on pydantic v2 or v1
                    fields: list[str] = []
                    if hasattr(self.args_schema, "model_fields"):
                        fields = list(self.args_schema.model_fields.keys())
                    elif hasattr(self.args_schema, "__fields__"):
                        fields = list(self.args_schema.__fields__.keys())

                    result: dict[str, Any] = {}
                    for i, val in enumerate(args):
                        if i < len(fields):
                            result[fields[i]] = val
                    result.update(kwargs)
                    return result
                return {**{f"arg{i}": v for i, v in enumerate(args)}, **kwargs}

            return {}

        # ------------------------------------------------------------------
        # Conversion depuis un BaseTool CrewAI existant
        # ------------------------------------------------------------------

        @classmethod
        def from_crewai_tool(
            cls,
            original: Any,
            pryxor_client: Any,
            *,
            raise_on_block: bool = True,
            raise_on_hold: bool = True,
        ) -> "PryxorTool":
            """
            Convert an existing CrewAI tool into a protected tool.
            The original `_run` method is ignored — Pryxor performs the execution.
            """
            return cls(
                pryxor_client=pryxor_client,
                name=original.name,
                description=getattr(original, "description", "") or "",
                args_schema=getattr(original, "args_schema", None),
                raise_on_block=raise_on_block,
                raise_on_hold=raise_on_hold,
            )

else:
    class PryxorTool:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            raise ImportError(
                "CrewAI is not installed. Run:\n"
                "    pip install crewai\n"
                "to use the Pryxor CrewAI adapter."
            )


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
    Decorator that turns a Python function into a CrewAI PryxorTool.

    Usage :
        @pryxor_tool("send_email", description="Send an email.")
        def send_email(to: str, subject: str, body: str) -> str:
            '''This function is never called — Pryxor executes.'''
            ...

    The decorated function returns a PryxorTool, not a function.
    """
    def decorator(fn: Callable) -> Any:
        from pryxor.client import Pryxor

        client = pryxor_client or Pryxor()
        schema = _schema_from_signature(fn)

        return PryxorTool(
            pryxor_client=client,
            name=tool_name,
            description=description,
            args_schema=schema,
            raise_on_block=raise_on_block,
            raise_on_hold=raise_on_hold,
        )

    return decorator
