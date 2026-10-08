"""
Refactoring tests: prove that the engine is sector-agnostic and that the
business logic lives in the plugin.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from pryxor_engine import PolicyEngine
from sectors import Decision, DecisionStatus, SectorPolicy, load_sector
from sectors.finance import FinanceSector


@pytest.fixture
def tmp_policy(tmp_path):
    policy = {
        "sector": "finance",
        "hold_ttl_minutes": 60,
        "allowed_actions": {
            "agent_finance_01": ["send_payment"],
        },
        "sectors": {
            "finance": {
                "max_single_transaction": 1000.0,
                "velocity_window_hours": 24,
                "velocity_limit": 1000.0,
                "allowed_recipients": ["Fournisseur_A", "Compte_Trusted"],
            },
        },
    }
    p = tmp_path / f"policy_{uuid4().hex}.json"
    p.write_text(json.dumps(policy), encoding="utf-8")
    return p


@pytest.fixture
def engine(tmp_policy, tmp_path):
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    return PolicyEngine(policy_path=tmp_policy, state_path=state)


# --- Registre ---


def test_load_known_sector(tmp_policy, tmp_path):
    sector = load_sector("finance", json.loads(tmp_policy.read_text()), tmp_path / "s.sqlite3")
    assert isinstance(sector, FinanceSector)
    assert sector.name == "finance"


def test_load_unknown_sector_raises(tmp_policy, tmp_path):
    with pytest.raises(ValueError, match="no module defines it"):
        load_sector("astrophysics", {}, tmp_path / "s.sqlite3")


# --- FinanceSector unitaire ---


def test_finance_approves_normal_transaction(tmp_policy, tmp_path):
    sector = FinanceSector(
        policy=json.loads(tmp_policy.read_text()),
        state_path=tmp_path / "s.sqlite3",
    )
    d = sector.evaluate(
        "agent_finance_01", "send_payment", {"amount": 100.0, "recipient": "Fournisseur_A"}
    )
    assert d.status == DecisionStatus.APPROVED


def test_finance_holds_on_excessive_amount(tmp_policy, tmp_path):
    sector = FinanceSector(
        policy=json.loads(tmp_policy.read_text()),
        state_path=tmp_path / "s.sqlite3",
    )
    d = sector.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    assert d.status == DecisionStatus.HOLD
    assert d.reason == "EXCEEDS_SINGLE_TRANSACTION_LIMIT"


def test_finance_holds_on_unknown_recipient(tmp_policy, tmp_path):
    sector = FinanceSector(
        policy=json.loads(tmp_policy.read_text()),
        state_path=tmp_path / "s.sqlite3",
    )
    d = sector.evaluate(
        "agent_finance_01", "send_payment", {"amount": 100.0, "recipient": "Attaquant"}
    )
    assert d.status == DecisionStatus.HOLD
    assert d.reason == "RECIPIENT_NOT_WHITELISTED"


def test_finance_velocity_triggers_hold(tmp_policy, tmp_path):
    sector = FinanceSector(
        policy=json.loads(tmp_policy.read_text()),
        state_path=tmp_path / "s.sqlite3",
    )
    # 3 approved transactions of $400 = $1200 > $1000 limit
    for _ in range(3):
        sector.record_approved(
            "agent_finance_01",
            "send_payment",
            {"amount": 400.0, "recipient": "Fournisseur_A"},
        )
    d = sector.evaluate(
        "agent_finance_01", "send_payment", {"amount": 100.0, "recipient": "Fournisseur_A"}
    )
    assert d.status == DecisionStatus.HOLD
    assert d.reason == "VELOCITY_LIMIT_EXCEEDED"


# --- Moteur orchestrateur ---


def test_engine_delegates_to_sector(engine):
    """Le moteur approve via le secteur."""
    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 100.0, "recipient": "Fournisseur_A"}
    )
    assert r["status"] == "APPROVED"


def test_engine_blocks_unauthorized_tool(engine):
    """The tool whitelist is handled by the engine, not the sector.

    The reason is intentionally generic. See tests/test_validation.py for
    the full rationale.
    """
    r = engine.evaluate("agent_finance_01", "delete_database", {})
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "NOT_AUTHORIZED"


def test_engine_holds_on_sector_decision(engine):
    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    assert r["status"] == "HOLD"
    assert r["reason"] == "EXCEEDS_SINGLE_TRANSACTION_LIMIT"


def test_approved_hold_updates_sector_velocity(engine):
    """An approved HOLD must be counted in the sector's velocity."""
    r1 = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 400.0, "recipient": "Attaquant"}
    )
    # Attaquant → HOLD
    assert r1["status"] == "HOLD"

    # We approve: it must count toward velocity
    engine.approve_hold(r1["action_id"])

    # Now a new transaction should push past the limit
    r2 = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 700.0, "recipient": "Fournisseur_A"}
    )
    # 400 (approved) + 700 = 1100 > 1000 → HOLD
    assert r2["status"] == "HOLD"
    assert r2["reason"] == "VELOCITY_LIMIT_EXCEEDED"


# --- Test that a new sector integrates without touching the engine ---


def test_custom_sector_can_be_registered(tmp_policy, tmp_path, monkeypatch):
    """
    Proof: registering a custom sector requires NO change to the engine.
    """

    class DummySector(SectorPolicy):
        name = "dummy"
        version = "0.1.0"

        def evaluate(self, agent_id, tool_name, parameters):
            return Decision.block("DUMMY", "Always blocked by dummy.")

    import sectors

    monkeypatch.setitem(sectors.SECTORS, "dummy", DummySector)

    policy = json.loads(tmp_policy.read_text())
    policy["sector"] = "dummy"
    policy["allowed_actions"] = {"agent_x": ["anything"]}

    p = tmp_path / "dummy_policy.json"
    p.write_text(json.dumps(policy), encoding="utf-8")

    engine = PolicyEngine(policy_path=p, state_path=tmp_path / "d.sqlite3")
    r = engine.evaluate("agent_x", "anything", {})
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "DUMMY"
