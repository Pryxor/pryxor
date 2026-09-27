"""
Tests for the CrewAI adapter. No network, no LLM.
"""

from __future__ import annotations

import pytest

pytest.importorskip("crewai")


from pryxor.adapters.crewai import (  # noqa: E402
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
    )
    result = tool._run(amount=100.0, recipient="X")
    assert "tx_1" in result
    assert w.calls == [("send_payment", {"amount": 100.0, "recipient": "X"})]


def test_approved_with_positional_dict():
    w = FakePryxor({"ok": True})
    tool = PryxorTool(pryxor_client=w, name="t", description="d")
    tool._run({"a": 1, "b": 2})
    assert w.calls == [("t", {"a": 1, "b": 2})]


def test_blocked_raises_by_default():
    exc = _make_exception("PryxorBlockedError", reason="SENSITIVE_SUBJECT", message="Blocked.")
    w = FakePryxor(exc)
    tool = PryxorTool(pryxor_client=w, name="send_email", description="...")

    with pytest.raises(PryxorProtectedToolError, match="SENSITIVE_SUBJECT"):
        tool._run(to="alice@c.com")


def test_blocked_returns_string_when_not_raising():
    exc = _make_exception("PryxorBlockedError", reason="SENSITIVE_SUBJECT", message="Blocked.")
    w = FakePryxor(exc)
    tool = PryxorTool(
        pryxor_client=w,
        name="send_email",
        description="...",
        raise_on_block=False,
    )
    result = tool._run(to="alice@c.com")
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
    tool = PryxorTool(pryxor_client=w, name="send_email", description="...")

    with pytest.raises(PryxorHoldPending) as excinfo:
        tool._run(to="bob@e.com")

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
        raise_on_hold=False,
    )
    result = tool._run(to="b@e.com")
    assert "[HOLD]" in result
    assert "hold_abc" in result


def test_execution_error_raises():
    exc = _make_exception("PryxorExecutionError", message="bank timeout", status_code=504)
    w = FakePryxor(exc)
    tool = PryxorTool(pryxor_client=w, name="send_payment", description="...")

    with pytest.raises(PryxorProtectedToolError, match="bank timeout"):
        tool._run(amount=100.0)


def test_from_crewai_tool_conversion():
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field

    class Args(BaseModel):
        to: str = Field(...)

    class MyTool(BaseTool):
        name: str = "send_email"
        description: str = "Send an email."
        args_schema: type[BaseModel] = Args

        def _run(self, to: str) -> str:
            return "not called"

    original = MyTool()
    w = FakePryxor({"ok": True})
    protected = PryxorTool.from_crewai_tool(original, pryxor_client=w)

    assert protected.name == "send_email"
    assert protected.description == "Send an email."

    protected._run(to="a@c.com")
    assert w.calls == [("send_email", {"to": "a@c.com"})]


def test_schema_from_signature_via_decorator():
    """The decorator infers an args_schema from the signature."""
    from pryxor.adapters.crewai import pryxor_tool

    w = FakePryxor({"ok": True})

    @pryxor_tool("send_email", description="Send email.", pryxor_client=w)
    def send_email(to: str, subject: str, body: str) -> str:
        return ""

    # C'est un PryxorTool CrewAI
    assert isinstance(send_email, PryxorTool)
    assert send_email.name == "send_email"
    assert send_email.args_schema is not None

    # The call with the right arguments is passed through to Pryxor
    send_email._run(to="a@c.com", subject="Hi", body="Hello")
    assert w.calls == [("send_email", {"to": "a@c.com", "subject": "Hi", "body": "Hello"})]
