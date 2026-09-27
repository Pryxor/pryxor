"""
Tests for per-tool sector routing.

The engine must judge each tool with the sector that tool belongs to, not
with a single global sector. Regression guard for the bug where a
`send_email` call was evaluated by the finance sector (which then reported
an empty 'recipient').
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from pryxor_engine import PolicyEngine


def _policy(**overrides) -> dict:
    base = {
        "sector": "finance",  # global default
        "hold_ttl_minutes": 60,
        "allowed_actions": {"agent_x": ["send_email", "send_payment"]},
        "sectors": {
            "finance": {
                "max_single_transaction": 500.0,
                "velocity_window_hours": 24,
                "velocity_limit": 10000.0,
                "allowed_recipients": ["Fournisseur_A"],
            },
            "email": {
                "max_recipients": 3,
                "allowed_domains": ["company.com"],
                "blocked_subject_pattern": "(?i)password",
            },
        },
        "executors": {
            "send_email": {"type": "mock", "sector": "email"},
            "send_payment": {"type": "mock", "sector": "finance"},
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def engine(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(_policy()), encoding="utf-8")
    eng = PolicyEngine(policy_path=p, state_path=tmp_path / f"{uuid4().hex}.sqlite3")
    yield eng
    eng.stop_dispatch_worker()


def test_both_sectors_are_loaded(engine):
    assert set(engine._sectors) == {"finance", "email"}
    # self.sector stays the global default (back-compat).
    assert engine.sector.name == "finance"


def test_tool_routes_to_its_own_sector(engine):
    assert engine._sector_for("send_email").name == "email"
    assert engine._sector_for("send_payment").name == "finance"


def test_unknown_tool_falls_back_to_default_sector(engine):
    assert engine._sector_for("something_else").name == "finance"


def test_email_is_not_judged_by_finance(engine):
    # The regression: this used to return BLOCKED with an empty recipient
    # because the finance sector evaluated it.
    r = engine.evaluate(
        "agent_x",
        "send_email",
        {"to": "bob@company.com", "subject": "Hello", "body": "hi"},
    )
    assert r["status"] != "BLOCKED" or "recipient" not in r.get("message", "").lower()
    assert r["status"] == "APPROVED"


def test_email_sector_rules_apply(engine):
    # External domain → HOLD (email rule), not a finance rule.
    r = engine.evaluate(
        "agent_x",
        "send_email",
        {"to": "bob@external.com", "subject": "Hi", "body": "x"},
    )
    assert r["status"] == "HOLD"
    assert r["reason"] == "EXTERNAL_EMAIL"


def test_finance_sector_rules_apply(engine):
    r = engine.evaluate(
        "agent_x",
        "send_payment",
        {"amount": 5000.0, "recipient": "Fournisseur_A"},
    )
    assert r["status"] == "HOLD"
    assert r["reason"] == "EXCEEDS_SINGLE_TRANSACTION_LIMIT"
