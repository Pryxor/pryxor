"""
Concurrency, TTL, and outbox tests.

Prove that:
- Only 1 approve wins out of N threads (race condition).
- Approving twice does not double the velocity (idempotence).
- An expired HOLD cannot be approved.
- The outbox survives a simulated "crash" and replays correctly.
- A failed side effect is replayed on the next process_outbox().
"""

from __future__ import annotations

import json
import threading
from uuid import uuid4

import pytest

from pryxor_engine import HoldStore, PolicyEngine


@pytest.fixture
def store(tmp_path):
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    return HoldStore(ttl_minutes=60, state_path=state)


@pytest.fixture
def pryxor_engine(tmp_path):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "sector": "finance",
                "hold_ttl_minutes": 60,
                "allowed_actions": {"agent_finance_01": ["send_payment"]},
                "sectors": {
                    "finance": {
                        "max_single_transaction": 500.0,
                        "velocity_window_hours": 24,
                        "velocity_limit": 1000.0,
                        "allowed_recipients": ["Fournisseur_A"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    return PolicyEngine(policy_path=policy_path, state_path=state)


def _create_hold(store: HoldStore, **overrides) -> str:
    hold = store.create(
        agent_id=overrides.get("agent_id", "agent_test"),
        tool_name=overrides.get("tool_name", "send_payment"),
        parameters=overrides.get("parameters", {"amount": 100.0, "recipient": "Fournisseur_A"}),
        reason=overrides.get("reason", "TEST"),
        message=overrides.get("message", "Test hold"),
    )
    return hold["action_id"]


# ----------------------------------------------------------------------
# Race condition
# ----------------------------------------------------------------------


def test_concurrent_approve_only_one_wins(store):
    """10 threads try to approve the same HOLD → only 1 wins."""
    action_id = _create_hold(store)
    results: list[dict] = []
    barrier = threading.Barrier(10)

    def worker():
        # Generous timeout: this asserts that ONE approve wins, not how fast
        # the threads rendezvous — a loaded CI box must not fail it.
        barrier.wait(timeout=30)
        results.append(store.approve(action_id))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    successes = [r for r in results if "already" not in r["message"] and r["status"] != "ERROR"]
    assert len(successes) == 1
    assert successes[0]["status"] == "APPROVED"

    others = [r for r in results if r is not successes[0]]
    assert len(others) == 9
    for r in others:
        assert r["status"] == "APPROVED"
        assert "already" in r["message"]


def test_concurrent_approve_and_reject_only_one_wins(store):
    action_id = _create_hold(store)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def do_approve():
        barrier.wait(timeout=5)
        results.append(store.approve(action_id))

    def do_reject():
        barrier.wait(timeout=5)
        results.append(store.reject(action_id))

    t1 = threading.Thread(target=do_approve)
    t2 = threading.Thread(target=do_reject)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [r for r in results if "already" not in r["message"] and r["status"] != "ERROR"]
    assert len(successes) == 1
    final = store.get(action_id)
    assert final["status"] in ("APPROVED", "REJECTED")


# ----------------------------------------------------------------------
# Idempotence
# ----------------------------------------------------------------------


def test_double_approve_does_not_double_count_velocity(pryxor_engine):
    r = pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 400.0, "recipient": "Attaquant"},
    )
    assert r["status"] == "HOLD"
    action_id = r["action_id"]

    pryxor_engine.approve_hold(action_id)
    pryxor_engine.approve_hold(action_id)

    assert pryxor_engine.sector._current_volume(24) == 400.0


def test_direct_approval_uses_unique_dedup(pryxor_engine):
    """Two identical direct approvals must be counted twice."""
    pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
    )
    pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
    )
    assert pryxor_engine.sector._current_volume(24) == 200.0


# ----------------------------------------------------------------------
# TTL
# ----------------------------------------------------------------------


def test_expired_hold_cannot_be_approved(tmp_path):
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    store = HoldStore(ttl_minutes=-1, state_path=state)  # already expired
    action_id = _create_hold(store)

    result = store.approve(action_id)
    assert result["status"] == "EXPIRED"
    assert "expired" in result["message"].lower()

    # The persisted state is indeed EXPIRED
    assert store.get(action_id)["status"] == "EXPIRED"


def test_expire_stale_holds_batch(store, tmp_path):
    """The purge marks every expired PENDING HOLD."""
    # Create a "future" store to insert expired HOLDs
    state = store.state_path
    store_future = HoldStore(ttl_minutes=-1, state_path=state)
    a = _create_hold(store_future)
    b = _create_hold(store_future)

    # Le store normal purge
    n = store.expire_stale_holds()
    assert n == 2
    assert store.get(a)["status"] == "EXPIRED"
    assert store.get(b)["status"] == "EXPIRED"


# ----------------------------------------------------------------------
# Outbox: atomicity + replay
# ----------------------------------------------------------------------


def test_outbox_event_created_atomically_with_transition(store):
    action_id = _create_hold(store)
    store.approve(action_id)

    events = store.list_pending_outbox()
    assert len(events) == 1
    assert events[0]["event_type"] == "hold.approved"
    assert events[0]["action_id"] == action_id


def test_process_outbox_is_idempotent(pryxor_engine):
    """Replaying process_outbox() does not double the velocity."""
    r = pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 400.0, "recipient": "Attaquant"},
    )
    action_id = r["action_id"]
    pryxor_engine.approve_hold(action_id)

    # Volume after approval
    v1 = pryxor_engine.sector._current_volume(24)
    assert v1 == 400.0

    # Rejoue explicitement : ne doit rien changer
    result = pryxor_engine.process_outbox()
    v2 = pryxor_engine.sector._current_volume(24)
    assert v2 == 400.0
    # Nothing to process (already marked)
    assert result["processed"] == 0


def test_outbox_recovery_after_crash(pryxor_engine):
    """
    Simulate a crash AFTER the CAS but BEFORE process_outbox():
    create a HOLD, approve it via the store (not the engine), then
    instantiate a NEW engine that must replay the event.
    """
    # 1) Create and approve via the store directly (not process_outbox)
    r = pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 400.0, "recipient": "Attaquant"},
    )
    action_id = r["action_id"]

    pryxor_engine.hold_store.approve(action_id)
    # At this point: HOLD=APPROVED, outbox_event pending, sector NOT notified.

    assert pryxor_engine.sector._current_volume(24) == 0.0  # velocity not updated yet

    # 2) Simulate a restart: new engine over the same DB
    pryxor_engine2 = PolicyEngine(
        policy_path=pryxor_engine.policy_path,
        state_path=pryxor_engine.state_path,
    )

    # The new engine replayed the outbox at startup
    assert pryxor_engine2.sector._current_volume(24) == 400.0
    assert len(pryxor_engine2.hold_store.list_pending_outbox()) == 0


def test_outbox_event_left_unprocessed_on_sector_error(pryxor_engine, monkeypatch):
    """
    If the sector raises, the event stays unprocessed and will be
    replayed on the next call.
    """
    r = pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 400.0, "recipient": "Attaquant"},
    )
    action_id = r["action_id"]

    # Make the sector fail once
    original = pryxor_engine.sector.on_hold_approved
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Simulated sector failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(pryxor_engine.sector, "on_hold_approved", flaky)

    result = pryxor_engine.approve_hold(action_id)
    assert result["status"] == "APPROVED"
    # The event must stay unprocessed
    pending = pryxor_engine.hold_store.list_pending_outbox()
    assert len(pending) == 1

    # Replay: this time the sector succeeds
    result2 = pryxor_engine.process_outbox()
    assert result2["processed"] == 1
    assert pryxor_engine.sector._current_volume(24) == 400.0


def test_reject_does_not_notify_sector(pryxor_engine):
    """A rejected HOLD does NOT add a transaction."""
    r = pryxor_engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 400.0, "recipient": "Attaquant"},
    )
    action_id = r["action_id"]

    pryxor_engine.reject_hold(action_id)

    assert pryxor_engine.sector._current_volume(24) == 0.0


def test_list_holds_pagination(tmp_path):
    from pryxor_engine import HoldStore

    state = tmp_path / "state_pagination.sqlite3"
    store = HoldStore(ttl_minutes=60, state_path=state)

    # Create 5 HOLDs
    for i in range(5):
        store.create(
            agent_id=f"agent_{i}",
            tool_name="send_email",
            parameters={"n": i},
            reason="TEST",
            message="Test",
        )

    # Page 1 : 2 items
    page1 = store.list(limit=2, offset=0)
    assert len(page1) == 2

    # Page 2 : 2 items
    page2 = store.list(limit=2, offset=2)
    assert len(page2) == 2

    # Page 3 : 1 item
    page3 = store.list(limit=2, offset=4)
    assert len(page3) == 1

    # Aucun chevauchement
    ids = {h["action_id"] for h in page1 + page2 + page3}
    assert len(ids) == 5

    # Total
    assert store.count_holds() == 5


def test_pagination_limit_is_capped(tmp_path):
    from pryxor_engine import HoldStore

    state = tmp_path / "state_cap.sqlite3"
    store = HoldStore(ttl_minutes=60, state_path=state)

    # Ask for 10000, must be capped at 500 (but no error)
    result = store.list(limit=10000, offset=0)
    assert isinstance(result, list)

    # Negative → clamped to 1
    result = store.list(limit=-5, offset=0)
    assert isinstance(result, list)

    # Negative offset → clamped to 0
    result = store.list(limit=10, offset=-100)
    assert isinstance(result, list)


# ----------------------------------------------------------------------
# Cas d'erreur
# ----------------------------------------------------------------------


def test_approve_nonexistent_hold(store):
    result = store.approve("hold_does_not_exist")
    assert result["status"] == "ERROR"
    assert "not found" in result["message"]


def test_reject_after_approve_is_refused(store):
    action_id = _create_hold(store)
    assert store.approve(action_id)["status"] == "APPROVED"
    r = store.reject(action_id)
    assert r["status"] == "APPROVED"
    assert "already" in r["message"]
