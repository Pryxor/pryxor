"""
Tests for the OpenAI Agents SDK adapter. No network, no LLM.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# Skip the whole module if the SDK is not installed
pytest.importorskip("agents")


from pryxor.adapters.openai_agents import (  # noqa: E402
    PryxorHoldPending,
    PryxorProtectedToolError,
    PryxorTool,
)

# ---------------------------------------------------------------------------
# Faux client Pryxor
# ---------------------------------------------------------------------------


class FakePryxor:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool_name, parameters, **kwargs):
        self.calls.append((tool_name, dict(parameters)))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _make_exception(cls_name: str, **attrs):
    ExcType = type(cls_name, (Exception,), {})
    e = ExcType(attrs.get("message", "test"))
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_approved_returns_json_string():
    w = FakePryxor({"tx_id": "tx_1", "status": "ok"})
    tool = PryxorTool(
        pryxor_client=w,
        name="send_payment",
        description="...",
        params_json_schema={
            "type": "object",
            "properties": {"amount": {"type": "number"}},
        },
    )

    result = asyncio.run(tool.on_invoke_tool(None, json.dumps({"amount": 100.0})))
    assert "tx_1" in result
    assert w.calls == [("send_payment", {"amount": 100.0})]


def test_blocked_raises_by_default():
    exc = _make_exception("PryxorBlockedError", reason="SENSITIVE_SUBJECT", message="Blocked.")
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
    )

    with pytest.raises(PryxorProtectedToolError, match="SENSITIVE_SUBJECT"):
        asyncio.run(tool.on_invoke_tool(None, json.dumps({"to": "alice@c.com"})))


def test_blocked_returns_string_when_not_raising():
    exc = _make_exception("PryxorBlockedError", reason="SENSITIVE_SUBJECT", message="Blocked.")
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
        raise_on_block=False,
    )
    result = asyncio.run(tool.on_invoke_tool(None, json.dumps({"to": "a@c.com"})))
    assert "[BLOCKED]" in result
    assert "SENSITIVE_SUBJECT" in result


def test_hold_raises_PryxorHoldPending():
    exc = _make_exception(
        "PryxorHoldPendingError",
        action_id="hold_abc123",
        reason="EXTERNAL_EMAIL",
        message="External.",
    )
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
    )

    with pytest.raises(PryxorHoldPending) as excinfo:
        asyncio.run(tool.on_invoke_tool(None, json.dumps({"to": "bob@e.com"})))

    assert excinfo.value.action_id == "hold_abc123"
    assert excinfo.value.reason == "EXTERNAL_EMAIL"


def test_hold_returns_string_when_not_raising():
    exc = _make_exception(
        "PryxorHoldPendingError",
        action_id="hold_abc",
        reason="EXTERNAL_EMAIL",
        message="External.",
    )
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
        raise_on_hold=False,
    )
    result = asyncio.run(tool.on_invoke_tool(None, json.dumps({"to": "b@e.com"})))
    assert "[HOLD]" in result
    assert "hold_abc" in result


def test_execution_error_raises():
    exc = _make_exception("PryxorExecutionError", message="bank timeout", status_code=504)
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_payment",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
    )

    with pytest.raises(PryxorProtectedToolError, match="bank timeout"):
        asyncio.run(tool.on_invoke_tool(None, json.dumps({"amount": 100.0})))


def test_invalid_json_args_returns_error_string():
    w = FakePryxor({"ok": True})
    tool = PryxorTool(
        pryxor_client=w,
        name="t",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
    )
    result = asyncio.run(tool.on_invoke_tool(None, "{not valid json"))
    assert "[ERROR]" in result
    assert w.calls == []  # Pryxor never called


def test_empty_args_is_treated_as_empty_dict():
    w = FakePryxor({"ok": True})
    tool = PryxorTool(
        pryxor_client=w,
        name="t",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
    )
    result = asyncio.run(tool.on_invoke_tool(None, ""))
    assert w.calls == [("t", {})]


def test_non_object_args_returns_error():
    w = FakePryxor({"ok": True})
    tool = PryxorTool(
        pryxor_client=w,
        name="t",
        description="...",
        params_json_schema={"type": "object", "properties": {}},
    )
    result = asyncio.run(tool.on_invoke_tool(None, "[1, 2, 3]"))
    assert "[ERROR]" in result
    assert w.calls == []


def test_from_openai_tool_conversion():
    from agents import FunctionTool

    async def _noop(ctx, args_json):
        return "not called"

    original = FunctionTool(
        name="send_email",
        description="Send an email.",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=_noop,
        strict_json_schema=False,
    )

    w = FakePryxor({"status": "ok"})
    protected = PryxorTool.from_openai_tool(original, pryxor_client=w)

    assert protected.name == "send_email"
    assert protected.description == "Send an email."

    asyncio.run(protected.on_invoke_tool(None, json.dumps({"to": "a@c.com"})))
    assert w.calls == [("send_email", {"to": "a@c.com"})]


def test_schema_from_signature_via_original_fn():
    async def send_email(to: str, subject: str, body: str) -> str:
        """Demo."""
        return ""

    w = FakePryxor({"ok": True})
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="Send email",
        original_fn=send_email,
    )
    schema = tool.params_json_schema
    assert "to" in schema["properties"]
    assert "subject" in schema["properties"]
    assert "body" in schema["properties"]
    assert set(schema["required"]) == {"to", "subject", "body"}
