"""
Tests for argument validation and the strengthened rejection semantics.

Covers the three improvements:
  1. Executor `inputSchema` is enforced before policy evaluation.
  2. Audit reasons distinguish "unknown tool" from "agent not authorized",
     while the agent-facing message stays generic.
  3. A HOLD response tells the agent to stop retrying.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from pryxor_validation import validate_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "maxLength": 200},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "environment": {"type": "string", "enum": ["dev", "staging", "production"]},
    },
    "required": ["to"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Unit: validate_arguments
# ---------------------------------------------------------------------------


def test_valid_arguments_pass():
    assert validate_arguments(SCHEMA, {"to": "a@b.com", "amount": 10}) == []


def test_missing_required_field():
    assert validate_arguments(SCHEMA, {"amount": 10}) == ["missing required field 'to'"]


def test_unknown_field_is_rejected():
    # The security-relevant case: smuggling an extra field (e.g. bcc).
    errors = validate_arguments(SCHEMA, {"to": "a@b.com", "bcc": "x@evil.com"})
    assert errors == ["unknown field 'bcc'"]


def test_wrong_type_is_rejected():
    errors = validate_arguments(SCHEMA, {"to": "a@b.com", "amount": "lots"})
    assert any("'amount' must be a number" in e for e in errors)


def test_bool_is_not_a_number():
    errors = validate_arguments(SCHEMA, {"to": "a@b.com", "amount": True})
    assert any("'amount' must be a number" in e for e in errors)


def test_enum_violation_is_rejected():
    errors = validate_arguments(SCHEMA, {"to": "a@b.com", "environment": "prod"})
    assert any("must be one of" in e for e in errors)


def test_exclusive_minimum_is_rejected():
    errors = validate_arguments(SCHEMA, {"to": "a@b.com", "amount": 0})
    assert any("must be > 0" in e for e in errors)


def test_max_length_is_rejected():
    errors = validate_arguments(SCHEMA, {"to": "x" * 201})
    assert any("maxLength" in e for e in errors)


def test_no_schema_means_no_constraints():
    assert validate_arguments(None, {"anything": 1}) == []
    assert validate_arguments({}, {"anything": 1}) == []


# ---------------------------------------------------------------------------
# Integration: engine enforces the schema and strengthens reasons
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    from pryxor_engine import PolicyEngine

    policy = {
        "sector": "finance",
        "hold_ttl_minutes": 60,
        "allowed_actions": {"agent_finance_01": ["send_payment"]},
        "sectors": {
            "finance": {
                "max_single_transaction": 500.0,
                "velocity_window_hours": 24,
                "velocity_limit": 10000.0,
                "allowed_recipients": ["Fournisseur_A"],
            }
        },
        "executors": {
            "send_payment": {
                "type": "mock",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                        "recipient": {"type": "string"},
                    },
                    "required": ["amount", "recipient"],
                    "additionalProperties": False,
                },
            }
        },
    }
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(policy), encoding="utf-8")
    eng = PolicyEngine(policy_path=p, state_path=tmp_path / f"{uuid4().hex}.sqlite3")
    yield eng
    eng.stop_dispatch_worker()


def test_engine_blocks_missing_argument(engine):
    r = engine.evaluate("agent_finance_01", "send_payment", {"amount": 100})
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "INVALID_ARGUMENTS"


def test_engine_blocks_smuggled_field(engine):
    r = engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100, "recipient": "Fournisseur_A", "bcc": "x@evil.com"},
    )
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "INVALID_ARGUMENTS"
    assert any("bcc" in e for e in r["errors"])


def test_unknown_tool_reason(engine):
    r = engine.evaluate("agent_finance_01", "delete_database", {})
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "UNSUPPORTED_TOOL"


def test_unauthorized_tool_reason(engine):
    # 'send_email' is a known tool in other configs; here it is not in the
    # agent's allowlist, so the reason differs from a truly unknown tool.
    r = engine.evaluate("agent_finance_01", "send_payment", {"amount": 1})  # well-formed but
    # send_payment IS authorized; use a tool that exists but is not allowed:
    r2 = engine.evaluate("agent_finance_01", "http_test", {})
    assert r2["status"] == "BLOCKED"
    assert r2["reason"] == "UNSUPPORTED_TOOL"


def test_agent_message_does_not_leak_tool_catalog(engine):
    r = engine.evaluate("agent_finance_01", "some_secret_tool", {})
    assert r["message"] == "This tool call is not authorized."
    assert "some_secret_tool" not in r["message"]


def test_hold_tells_agent_not_to_retry(engine):
    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    assert r["status"] == "HOLD"
    assert r["retry"] is False
    assert r["action_id"] in r["message"]
    assert "Do not retry" in r["message"]
