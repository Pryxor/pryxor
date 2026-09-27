"""
Tests de la PII redaction.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from pryxor_redaction import RedactionMode, Redactor, build_redactor

# ---------------------------------------------------------------------------
# Redactor unitaire
# ---------------------------------------------------------------------------


def test_disabled_redactor_is_noop():
    r = Redactor({"enabled": False})
    params = {"password": "secret123", "to": "a@b.com"}
    assert r.redact("send_email", params) == params


def test_field_name_password_is_redacted():
    r = Redactor({"enabled": True})
    result = r.redact("any_tool", {"password": "secret123", "user": "alice"})
    assert result["password"] == "[REDACTED]"
    assert result["user"] == "alice"


def test_field_name_token_is_redacted():
    r = Redactor({"enabled": True})
    result = r.redact("any_tool", {"api_key": "sk_live_abc123", "user": "bob"})
    assert result["api_key"] == "[REDACTED]"


def test_pattern_card_number_in_text():
    r = Redactor({"enabled": True})
    result = r.redact("any_tool", {"note": "my card is 4111 1111 1111 1111"})
    assert "[REDACTED]" in result["note"]
    assert "4111" not in result["note"]


def test_pattern_bearer_token():
    r = Redactor({"enabled": True})
    result = r.redact("any_tool", {"header": "Authorization: Bearer eyJhbGciOi..."})
    assert "eyJhbGciOi" not in result["header"]
    assert "[REDACTED]" in result["header"]


def test_pattern_api_key_in_text():
    r = Redactor({"enabled": True})
    result = r.redact("any_tool", {"log": "using key sk_live_abcdef1234567890"})
    assert "sk_live_abcdef1234567890" not in result["log"]


def test_nested_dict_is_recursed():
    r = Redactor({"enabled": True})
    params = {
        "user": {
            "name": "alice",
            "password": "secret",
            "profile": {"token": "abc"},
        }
    }
    result = r.redact("any_tool", params)
    assert result["user"]["name"] == "alice"
    assert result["user"]["password"] == "[REDACTED]"
    assert result["user"]["profile"]["token"] == "[REDACTED]"


def test_list_of_strings_is_scrubbed():
    r = Redactor({"enabled": True})
    params = {"lines": ["ok", "password=abc", "fine"]}
    result = r.redact("any_tool", params)
    assert result["lines"][0] == "ok"
    assert "abc" not in result["lines"][1]
    assert result["lines"][2] == "fine"


def test_hash_mode_on_explicit_field():
    r = Redactor(
        {
            "enabled": True,
            "default_mode": "REDACTED",
            "tools": {"send_email": {"fields": {"body": "HASH"}}},
        }
    )
    result = r.redact("send_email", {"body": "hello world"})
    assert result["body"].startswith("sha256:")
    assert len(result["body"]) == len("sha256:") + 32


def test_explicit_redacted_overrides_default():
    r = Redactor(
        {
            "enabled": True,
            "default_mode": "NONE",
            "tools": {"send_email": {"fields": {"body": "REDACTED"}}},
        }
    )
    result = r.redact("send_email", {"body": "sensitive text", "to": "a@b.com"})
    assert result["body"] == "[REDACTED]"
    assert result["to"] == "a@b.com"  # untouched


def test_none_mode_keeps_value():
    r = Redactor(
        {
            "enabled": True,
            "tools": {"send_payment": {"fields": {"amount": "NONE"}}},
        }
    )
    result = r.redact("send_payment", {"amount": 100.0})
    assert result["amount"] == 100.0


def test_does_not_mutate_original():
    r = Redactor({"enabled": True})
    params = {"password": "secret", "user": "alice"}
    _ = r.redact("any_tool", params)
    assert params["password"] == "secret"  # l'original est intact


def test_build_redactor_from_policy():
    policy = {"redaction": {"enabled": True}}
    r = build_redactor(policy)
    assert r.enabled is True


def test_build_redactor_empty_policy():
    r = build_redactor({})
    assert r.enabled is False


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


def _write_policy(tmp_path: Path) -> Path:
    p = tmp_path / "policy.json"
    p.write_text(
        json.dumps(
            {
                "sector": "email",
                "hold_ttl_minutes": 60,
                "secrets": {"provider": "env"},
                "allowed_actions": {"agent_x": ["send_email"]},
                "sectors": {
                    "email": {
                        "max_recipients": 10,
                        "allowed_domains": ["company.com"],
                    }
                },
                "executors": {"send_email": {"type": "mock"}},
                "redaction": {
                    "enabled": True,
                    "default_mode": "REDACTED",
                    "tools": {
                        "send_email": {
                            "fields": {"body": "HASH"},
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return p


def test_engine_redacts_hold_parameters(tmp_path):
    from pryxor_engine import PolicyEngine

    policy = _write_policy(tmp_path)
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    eng = PolicyEngine(policy_path=policy, state_path=state)

    try:
        # Trigger a HOLD (external domain)
        r = eng.evaluate(
            "agent_x",
            "send_email",
            {
                "to": "bob@external.com",
                "subject": "Hi",
                "body": "my password is hunter2",
            },
        )
        assert r["status"] == "HOLD"

        hold = eng.hold_store.get(r["action_id"])
        # subject is not in the redacted fields, but body is hashed
        assert hold["parameters"]["body"].startswith("sha256:")
        assert "hunter2" not in json.dumps(hold["parameters"])
    finally:
        eng.stop_dispatch_worker()


def test_audit_has_actor_id_column(tmp_path):
    from pryxor_engine import PolicyEngine

    policy = _write_policy(tmp_path)
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    eng = PolicyEngine(policy_path=policy, state_path=state)

    try:
        r = eng.evaluate(
            "agent_x",
            "send_email",
            {"to": "bob@external.com", "subject": "x", "body": "y"},
        )
        assert r["status"] == "HOLD"

        eng.approve_hold(r["action_id"], actor_id="admin_root")

        events = eng.list_audit_events()
        approved = [e for e in events if e["event_type"] == "approved"]
        assert len(approved) == 1
        # Dedicated column
        assert approved[0]["actor_id"] == "admin_root"
        # And also in the payload (backward compat)
        assert approved[0]["payload"]["actor_id"] == "admin_root"
    finally:
        eng.stop_dispatch_worker()
