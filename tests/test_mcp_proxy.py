"""
Tests for the MCP proxy. PolicyDecider is tested in isolation with a
fake http_post.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests as requests_module

from pryxor.mcp import PolicyDecider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_response(status_code: int, body: dict) -> Mock:
    r = Mock()
    r.status_code = status_code
    r.json = Mock(return_value=body)
    r.text = json.dumps(body)
    return r


@pytest.fixture
def decider_factory():
    """Return a factory: give a Pryxor response, get a decider."""

    def _make(pryxor_response: dict | Mock | Exception):
        if isinstance(pryxor_response, Exception):

            def fake_post(*a, **k):
                raise pryxor_response
        elif isinstance(pryxor_response, Mock):
            fake_post = lambda *a, **k: pryxor_response
        else:
            fake_post = lambda *a, **k: _fake_response(200, pryxor_response)

        return PolicyDecider(
            agent_key="pryxor_agent_test_xxx",
            pryxor_url="http://pryxor-test:8000",
            http_post=fake_post,
        )

    return _make


def _tools_call(name: str, arguments: dict, msg_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


# ---------------------------------------------------------------------------
# Non-tools/call methods pass through
# ---------------------------------------------------------------------------


def test_initialize_passes_through(decider_factory):
    d = decider_factory({"status": "APPROVED"})  # peu importe
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert d.decide(msg) is None


def test_tools_list_passes_through(decider_factory):
    d = decider_factory({"status": "APPROVED"})
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    assert d.decide(msg) is None


def test_notification_without_id_passes_through(decider_factory):
    d = decider_factory({"status": "APPROVED"})
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    assert d.decide(msg) is None


# ---------------------------------------------------------------------------
# tools/call — APPROVED gateway (execution result)
# ---------------------------------------------------------------------------


def test_approved_with_execution_result_returns_text(decider_factory):
    d = decider_factory(
        {
            "status": "APPROVED",
            "execution": {"success": True, "result": {"tx_id": "tx_1"}},
        }
    )
    msg = _tools_call("send_payment", {"amount": 100, "recipient": "X"})
    resp = d.decide(msg)

    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["isError"] is False
    assert "tx_1" in resp["result"]["content"][0]["text"]


def test_approved_transparent_mode_passes_through(decider_factory):
    """
    If Pryxor returns APPROVED without an execution field (transparent
    mode), the proxy forwards the message to the upstream.
    """
    d = decider_factory({"status": "APPROVED"})
    msg = _tools_call("read_file", {"path": "/tmp/x"})
    assert d.decide(msg) is None


def test_approved_with_execution_failure_returns_error(decider_factory):
    d = decider_factory(
        {
            "status": "APPROVED",
            "execution": {"success": False, "error": "bank timeout", "status_code": 504},
        }
    )
    msg = _tools_call("send_payment", {"amount": 100, "recipient": "X"})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is True
    assert "bank timeout" in resp["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# tools/call — BLOCKED
# ---------------------------------------------------------------------------


def test_blocked_returns_error(decider_factory):
    d = decider_factory(
        {
            "status": "BLOCKED",
            "reason": "UNSUPPORTED_TOOL",
            "message": "Tool not allowed.",
        }
    )
    msg = _tools_call("delete_database", {})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is True
    assert "UNSUPPORTED_TOOL" in resp["result"]["content"][0]["text"]


def test_auth_error_returns_blocked(decider_factory):
    d = decider_factory(_fake_response(401, {}))
    msg = _tools_call("read_file", {"path": "/x"})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is True
    assert "AUTH_ERROR" in resp["result"]["content"][0]["text"]


def test_rate_limit_returns_blocked(decider_factory):
    d = decider_factory(_fake_response(429, {}))
    msg = _tools_call("read_file", {"path": "/x"})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is True
    assert "RATE_LIMITED" in resp["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# tools/call — HOLD
# ---------------------------------------------------------------------------


def test_hold_returns_action_id(decider_factory):
    d = decider_factory(
        {
            "status": "HOLD",
            "action_id": "hold_abc123",
            "reason": "EXCEEDS_SINGLE_TRANSACTION_LIMIT",
            "message": "Amount too high.",
        }
    )
    msg = _tools_call("send_payment", {"amount": 5000, "recipient": "X"})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert "hold_abc123" in text
    assert "EXCEEDS_SINGLE_TRANSACTION_LIMIT" in text


# ---------------------------------------------------------------------------
# tools/call — erreurs de transport
# ---------------------------------------------------------------------------


def test_pryxor_unreachable_returns_error(decider_factory):
    d = decider_factory(requests_module.ConnectionError("refused"))
    msg = _tools_call("read_file", {"path": "/x"})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is True
    assert "PRYXOR_UNREACHABLE" in resp["result"]["content"][0]["text"]


def test_pryxor_5xx_returns_error(decider_factory):
    d = decider_factory(_fake_response(500, {"detail": "boom"}))
    msg = _tools_call("read_file", {"path": "/x"})
    resp = d.decide(msg)

    assert resp["result"]["isError"] is True
    assert "PRYXOR_SERVER_ERROR" in resp["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# malformed tools/call
# ---------------------------------------------------------------------------


def test_missing_tool_name_returns_jsonrpc_error(decider_factory):
    d = decider_factory({"status": "APPROVED"})
    msg = {"jsonrpc": "2.0", "id": 42, "method": "tools/call", "params": {}}
    resp = d.decide(msg)

    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert resp["id"] == 42


def test_blocked_response_is_ascii_safe(decider_factory):
    """Regression: BLOCKED must not contain non-ASCII characters."""
    d = decider_factory(
        {
            "status": "BLOCKED",
            "reason": "UNSUPPORTED_TOOL",
            "message": "Tool not allowed.",
        }
    )
    msg = _tools_call("delete_database", {})
    resp = d.decide(msg)

    text = resp["result"]["content"][0]["text"]
    # Must be encodable in cp1252 (the worst case on Windows)
    text.encode("cp1252")


def test_hold_response_is_ascii_safe(decider_factory):
    """Regression: HOLD must not contain non-ASCII characters."""
    d = decider_factory(
        {
            "status": "HOLD",
            "action_id": "hold_abc",
            "reason": "EXCEEDS_LIMIT",
            "message": "Amount too high.",
        }
    )
    msg = _tools_call("send_payment", {"amount": 5000})
    resp = d.decide(msg)

    text = resp["result"]["content"][0]["text"]
    text.encode("cp1252")


def test_approved_execution_failure_is_ascii_safe(decider_factory):
    """Regression: EXECUTION_FAILED must not contain non-ASCII characters."""
    d = decider_factory(
        {
            "status": "APPROVED",
            "execution": {"success": False, "error": "bank timeout", "status_code": 504},
        }
    )
    msg = _tools_call("send_payment", {"amount": 5000})
    resp = d.decide(msg)

    text = resp["result"]["content"][0]["text"]
    text.encode("cp1252")
