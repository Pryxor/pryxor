"""
Unit tests for the SDK. No network, no running Pryxor — the HTTP layer
is monkey-patched.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests as requests_module

from pryxor import (
    Pryxor,
    PryxorAuthError,
    PryxorBlockedError,
    PryxorError,
    PryxorExecutionError,
    PryxorHoldPendingError,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PRYXOR_AGENT_KEY", "pryxor_agent_test_xxx")
    monkeypatch.setenv("PRYXOR_URL", "http://pryxor-test:8000")


def _response(status_code: int = 200, json_body: dict | None = None, text: str | None = None):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value=json_body if json_body is not None else {})
    resp.text = text if text is not None else str(json_body or "")
    return resp


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_agent_key(monkeypatch):
    monkeypatch.delenv("PRYXOR_AGENT_KEY", raising=False)
    with pytest.raises(PryxorError, match="PRYXOR_AGENT_KEY"):
        Pryxor()


def test_reads_env(monkeypatch):
    w = Pryxor()
    assert w.agent_key == "pryxor_agent_test_xxx"
    assert w.base_url == "http://pryxor-test:8000"


def test_explicit_params_override_env():
    w = Pryxor(agent_key="pryxor_agent_explicit", base_url="http://custom:9000/")
    assert w.agent_key == "pryxor_agent_explicit"
    assert w.base_url == "http://custom:9000"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_execute_returns_payload_on_approved(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        assert url == "http://pryxor-test:8000/v1/execute-tool"
        return _response(
            200,
            {
                "status": "APPROVED",
                "execution": {"success": True, "result": {"tx_id": "tx_1"}},
            },
        )

    monkeypatch.setattr(requests_module, "post", fake_post)

    result = Pryxor().execute("send_payment", {"amount": 100.0, "recipient": "X"})
    assert result == {"tx_id": "tx_1"}


def test_execute_sends_agent_key_and_idempotency_key(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        captured["json"] = json
        return _response(
            200,
            {
                "status": "APPROVED",
                "execution": {"success": True, "result": {}},
            },
        )

    monkeypatch.setattr(requests_module, "post", fake_post)

    Pryxor().execute("send_payment", {"amount": 1.0}, idempotency_key="user-cmd-42")

    assert captured["headers"]["X-Agent-Key"] == "pryxor_agent_test_xxx"
    assert captured["headers"]["Idempotency-Key"] == "user-cmd-42"
    assert captured["json"] == {"tool_name": "send_payment", "parameters": {"amount": 1.0}}


# ---------------------------------------------------------------------------
# Business decisions
# ---------------------------------------------------------------------------


def test_execute_raises_blocked(monkeypatch):
    monkeypatch.setattr(
        requests_module,
        "post",
        lambda *a, **k: _response(
            200,
            {
                "status": "BLOCKED",
                "reason": "UNSUPPORTED_TOOL",
                "message": "Tool not allowed.",
            },
        ),
    )
    with pytest.raises(PryxorBlockedError) as exc:
        Pryxor().execute("delete_database", {})
    assert exc.value.reason == "UNSUPPORTED_TOOL"
    assert "Tool not allowed" in exc.value.message


def test_execute_raises_hold(monkeypatch):
    monkeypatch.setattr(
        requests_module,
        "post",
        lambda *a, **k: _response(
            200,
            {
                "status": "HOLD",
                "action_id": "hold_abc123",
                "reason": "EXCEEDS_SINGLE_TRANSACTION_LIMIT",
                "message": "Amount too high.",
            },
        ),
    )
    with pytest.raises(PryxorHoldPendingError) as exc:
        Pryxor().execute("send_payment", {"amount": 5000.0, "recipient": "X"})
    assert exc.value.action_id == "hold_abc123"
    assert exc.value.reason == "EXCEEDS_SINGLE_TRANSACTION_LIMIT"


def test_execute_raises_execution_error_when_approved_but_exec_failed(monkeypatch):
    monkeypatch.setattr(
        requests_module,
        "post",
        lambda *a, **k: _response(
            200,
            {
                "status": "EXECUTION_FAILED",
                "execution": {"success": False, "error": "HTTP 500 from bank", "status_code": 500},
            },
        ),
    )
    with pytest.raises(PryxorExecutionError) as exc:
        Pryxor().execute("send_payment", {"amount": 100.0, "recipient": "X"})
    assert exc.value.status_code == 500
    assert "HTTP 500" in exc.value.message


# ---------------------------------------------------------------------------
# HTTP-level errors
# ---------------------------------------------------------------------------


def test_execute_raises_auth_error_on_401(monkeypatch):
    monkeypatch.setattr(
        requests_module, "post", lambda *a, **k: _response(401, text="Unauthorized")
    )
    with pytest.raises(PryxorAuthError):
        Pryxor().execute("send_payment", {})


def test_execute_raises_pryxor_error_on_500(monkeypatch):
    monkeypatch.setattr(requests_module, "post", lambda *a, **k: _response(500, text="boom"))
    with pytest.raises(PryxorError, match="server error 500"):
        Pryxor().execute("send_payment", {})


def test_execute_raises_on_network_error(monkeypatch):
    def raiser(*a, **k):
        raise requests_module.ConnectionError("refused")

    monkeypatch.setattr(requests_module, "post", raiser)
    with pytest.raises(PryxorError, match="unreachable"):
        Pryxor().execute("send_payment", {})


# ---------------------------------------------------------------------------
# execute_raw and helpers
# ---------------------------------------------------------------------------


def test_execute_raw_returns_full_dict_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        requests_module,
        "post",
        lambda *a, **k: _response(
            200,
            {
                "status": "HOLD",
                "action_id": "hold_xyz",
                "reason": "VELOCITY_LIMIT_EXCEEDED",
                "message": "Too fast.",
            },
        ),
    )
    raw = Pryxor().execute_raw("send_payment", {})
    assert raw["status"] == "HOLD"
    assert raw["action_id"] == "hold_xyz"


def test_static_helpers():
    assert Pryxor.is_approved({"status": "APPROVED", "execution": {"success": True}})
    assert not Pryxor.is_approved({"status": "APPROVED", "execution": {"success": False}})
    assert Pryxor.is_blocked({"status": "BLOCKED"})
    assert Pryxor.is_hold({"status": "HOLD"})
    assert not Pryxor.is_hold({"status": "APPROVED"})
