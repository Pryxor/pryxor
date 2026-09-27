"""
Tests for the LangChain adapter. No network, no LLM.
The Pryxor client is replaced by a fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow this file to run both through pytest and as `python tests/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# Skip the whole module if LangChain is not installed
pytest.importorskip("langchain_core")


from pryxor.adapters.langchain import (  # noqa: E402
    PryxorHoldPending,
    PryxorProtectedToolError,
    PryxorTool,
)

# ---------------------------------------------------------------------------
# Faux client Pryxor
# ---------------------------------------------------------------------------


class FakePryxor:
    """Simulate pryxor_client.Pryxor with controlled responses."""

    def __init__(self, response: dict | Exception):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool_name, parameters, **kwargs):
        self.calls.append((tool_name, dict(parameters)))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _make_exception(cls_name: str, **attrs):
    """Create an exception with the class name expected by the adapter."""
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
    tool = PryxorTool(pryxor_client=w, name="send_payment", description="...")

    result = tool.run({"amount": 100.0, "recipient": "X"})

    assert "tx_1" in result
    assert w.calls == [("send_payment", {"amount": 100.0, "recipient": "X"})]


def test_blocked_raises_by_default():
    exc = _make_exception("PryxorBlockedError", reason="SENSITIVE_SUBJECT", message="Blocked.")
    w = FakePryxor(exc)
    tool = PryxorTool(pryxor_client=w, name="send_email", description="...")

    with pytest.raises(PryxorProtectedToolError, match="SENSITIVE_SUBJECT"):
        tool.run({"to": "alice@company.com"})


def test_blocked_returns_string_if_not_raising():
    exc = _make_exception("PryxorBlockedError", reason="SENSITIVE_SUBJECT", message="Blocked.")
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        raise_on_block=False,
    )
    result = tool.run({"to": "alice@company.com"})
    assert "[BLOCKED]" in result
    assert "SENSITIVE_SUBJECT" in result


def test_hold_raises_PryxorHoldPending():
    exc = _make_exception(
        "PryxorHoldPendingError",
        action_id="hold_abc123",
        reason="EXTERNAL_EMAIL",
        message="External domain.",
    )
    w = FakePryxor(exc)
    tool = PryxorTool(pryxor_client=w, name="send_email", description="...")

    with pytest.raises(PryxorHoldPending) as excinfo:
        tool.run({"to": "bob@external.com"})

    assert excinfo.value.action_id == "hold_abc123"
    assert excinfo.value.reason == "EXTERNAL_EMAIL"


def test_hold_returns_string_if_not_raising():
    exc = _make_exception(
        "PryxorHoldPendingError",
        action_id="hold_abc123",
        reason="EXTERNAL_EMAIL",
        message="External domain.",
    )
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        raise_on_hold=False,
    )
    result = tool.run({"to": "bob@external.com"})
    assert "[HOLD]" in result
    assert "hold_abc123" in result


def test_execution_error_raises():
    exc = _make_exception("PryxorExecutionError", message="bank timeout", status_code=504)
    w = FakePryxor(exc)
    tool = PryxorTool(pryxor_client=w, name="send_payment", description="...")

    with pytest.raises(PryxorProtectedToolError, match="bank timeout"):
        tool.run({"amount": 100.0, "recipient": "X"})


def test_unexpected_error_wrapped():
    w = FakePryxor(RuntimeError("something weird"))
    tool = PryxorTool(pryxor_client=w, name="send_payment", description="...")

    with pytest.raises(PryxorProtectedToolError, match="something weird"):
        tool.run({"amount": 100.0, "recipient": "X"})


def test_kwargs_and_positional_dict_are_equivalent():
    w1 = FakePryxor({"ok": True})
    t1 = PryxorTool(pryxor_client=w1, name="t", description="d")
    t1.run({"a": 1, "b": 2})

    w2 = FakePryxor({"ok": True})
    t2 = PryxorTool(pryxor_client=w2, name="t", description="d")
    t2.run(a=1, b=2)

    assert w1.calls == w2.calls == [("t", {"a": 1, "b": 2})]


def test_from_langchain_tool_conversion():
    from langchain_core.tools import Tool

    original = Tool(
        name="send_email",
        description="Send an email.",
        func=lambda **kw: "not called",
    )
    w = FakePryxor({"status": "ok"})
    protected = PryxorTool.from_langchain_tool(original, pryxor_client=w)

    assert protected.name == "send_email"
    assert protected.description == "Send an email."

    protected.run({"to": "alice@company.com"})
    assert w.calls == [("send_email", {"to": "alice@company.com"})]


def test_arun_delegates_to_run():
    """The async version must work (delegated to sync)."""
    import asyncio

    w = FakePryxor({"tx_id": "tx_async"})
    tool = PryxorTool(pryxor_client=w, name="send_payment", description="...")

    result = asyncio.run(tool.arun({"amount": 42.0, "recipient": "Y"}))
    assert "tx_async" in result
