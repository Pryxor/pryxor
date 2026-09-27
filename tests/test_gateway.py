"""
Tests du Gateway pattern.
Prove that:
    - APPROVED → the executor is called.
    - Approved HOLD → the executor is called (via outbox).
    - Idempotence: the same idempotency_key twice = a single execution.
    - BLOCKED → the executor is NOT called.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from pryxor_engine import PolicyEngine


class CountingExecutor:
    """Executor that counts calls (not idempotent on the external API side)."""

    def __init__(self):
        self.calls = []
        self.should_fail = False

    def execute(self, tool_name, agent_id, parameters, idempotency_key):
        from pryxor_executors import ExecutionResult

        self.calls.append(
            {
                "tool_name": tool_name,
                "agent_id": agent_id,
                "parameters": parameters,
                "idempotency_key": idempotency_key,
            }
        )
        if self.should_fail:
            return ExecutionResult(success=False, error="Simulated failure", status_code=500)
        return ExecutionResult(
            success=True,
            result={"tx_id": f"tx_{len(self.calls)}", "echo": parameters},
            status_code=200,
        )


@pytest.fixture
def engine_with_counter(tmp_path):
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
            },
        },
        "executors": {"send_payment": {"type": "mock"}},
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"

    engine = PolicyEngine(policy_path=policy_path, state_path=state)

    # Injecte un CountingExecutor
    counter = CountingExecutor()
    engine.executors["send_payment"] = counter
    engine._counter = counter  # pour les tests
    return engine


def test_approved_executes_action(engine_with_counter):
    r = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
    )
    assert r["status"] == "APPROVED"
    assert r["execution"]["success"] is True
    assert r["execution"]["result"]["tx_id"] == "tx_1"
    assert len(engine_with_counter._counter.calls) == 1


def test_blocked_does_not_execute(engine_with_counter):
    r = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Attaquant"},  # pas whitelist → HOLD, pas BLOCK
    )
    # Here it is a HOLD; let's check BLOCKED with an unauthorized tool
    r2 = engine_with_counter.evaluate(
        "agent_finance_01",
        "delete_everything",
        {},
    )
    assert r2["status"] == "BLOCKED"
    assert len(engine_with_counter._counter.calls) == 0


def test_hold_executes_only_after_approval(engine_with_counter):
    r = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Attaquant"},  # → HOLD
    )
    assert r["status"] == "HOLD"
    assert len(engine_with_counter._counter.calls) == 0  # not executed yet

    engine_with_counter.approve_hold(r["action_id"])
    assert len(engine_with_counter._counter.calls) == 1

    # We can check the idempotency_key
    assert engine_with_counter._counter.calls[0]["idempotency_key"] == f"hold:{r['action_id']}"


def test_client_idempotency_key_prevents_double_execution(engine_with_counter):
    r1 = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
        client_idempotency_key="user-cmd-42",
    )
    r2 = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
        client_idempotency_key="user-cmd-42",  # same key
    )
    assert r1["execution"]["success"] is True
    assert r2["execution"]["success"] is True
    # ⚠️ Only 1 call was made
    assert len(engine_with_counter._counter.calls) == 1
    # The second returned the cached result
    assert r2["execution"]["result"] == r1["execution"]["result"]


def test_execution_failure_returns_execution_failed(engine_with_counter):
    engine_with_counter._counter.should_fail = True
    r = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
    )
    assert r["status"] == "EXECUTION_FAILED"
    assert r["execution"]["success"] is False
    assert "Simulated failure" in r["execution"]["error"]


def test_hold_execution_idempotent_on_replay(engine_with_counter):
    """Replaying process_outbox must NOT re-execute the action."""
    r = engine_with_counter.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Attaquant"},
    )
    engine_with_counter.approve_hold(r["action_id"])
    assert len(engine_with_counter._counter.calls) == 1

    # Rejoue l'outbox
    engine_with_counter.process_outbox()
    # Toujours 1 appel
    assert len(engine_with_counter._counter.calls) == 1


def test_execution_recovery_after_crash(tmp_path):
    """
    Simulate a crash after the CAS but before execution.
    The restart must execute the action exactly once.
    """
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
            },
        },
        "executors": {"send_payment": {"type": "mock"}},
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"

    engine1 = PolicyEngine(policy_path=policy_path, state_path=state)
    counter = CountingExecutor()
    engine1.executors["send_payment"] = counter

    # Create + approve via the store (not the engine) → simulate a crash
    r = engine1.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Attaquant"},
    )
    engine1.hold_store.approve(r["action_id"])
    # No call executed yet
    assert len(counter.calls) == 0

    # Nouveau moteur (crash recovery)
    engine2 = PolicyEngine(policy_path=policy_path, state_path=state)
    counter2 = CountingExecutor()
    engine2.executors["send_payment"] = counter2
    # The startup process_outbox should have executed it
    # (but with the original mock, since the injection happens after __init__)
    # Instead, let's check that the outbox was processed:
    assert len(engine2.hold_store.list_pending_outbox()) == 0
    # And that a SUCCESS execution exists
    ex = engine2.hold_store.get_execution(f"hold:{r['action_id']}")
    assert ex is not None
    assert ex["status"] == "SUCCESS"
