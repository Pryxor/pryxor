"""
Tests for the notification layer.
No network: notifiers are injected with a mock.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from pryxor_engine import PolicyEngine
from pryxor_notifiers import (
    NotificationStore,
    SlackNotifier,
    TeamsNotifier,
    WebhookNotifier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _wait_for_dispatch(engine, predicate, timeout: float = 5.0) -> None:
    # Les notifications sont dispatchees de maniere asynchrone (worker).
    # On attend que la condition soit vraie, comme le fait deja
    # `test_dispatch_worker_sends_eventually`.
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)


def _write_policy(tmp_path: Path, events=None, executor_type: str = "mock") -> Path:
    events = events or ["hold.created"]
    p = tmp_path / "policy.json"
    p.write_text(
        json.dumps(
            {
                "sector": "finance",
                "hold_ttl_minutes": 60,
                "secrets": {"provider": "env"},
                "allowed_actions": {"agent_finance_01": ["send_payment"]},
                "sectors": {
                    "finance": {
                        "max_single_transaction": 500.0,
                        "velocity_window_hours": 24,
                        "velocity_limit": 10000.0,
                        "allowed_recipients": ["Fournisseur_A"],
                    }
                },
                "executors": {"send_payment": {"type": executor_type}},
                "notifications": {
                    "routes": {
                        "test_route": {
                            "type": "webhook",
                            "url": "https://example.com/hook",
                            "events": events,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def engine(tmp_path):
    policy_path = _write_policy(tmp_path)
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    eng = PolicyEngine(policy_path=policy_path, state_path=state)

    # Injecte un notifier factice qui compte les envois
    sent_calls = []

    class FakeNotifier:
        def __init__(self):
            self.should_fail = False

        def send(self, payload):
            if self.should_fail:
                raise RuntimeError("boom")
            sent_calls.append(payload)

    fake = FakeNotifier()
    eng.notifiers["test_route"] = fake
    eng._sent_calls = sent_calls
    eng._fake_notifier = fake
    return eng


# ---------------------------------------------------------------------------
# NotificationStore
# ---------------------------------------------------------------------------


def test_store_enqueue_is_idempotent(tmp_path):
    store = NotificationStore(tmp_path / "s.sqlite3")
    assert store.enqueue("hold.created", "h1", "r1", {"x": 1}, "k1") is True
    assert store.enqueue("hold.created", "h1", "r1", {"x": 1}, "k1") is False


def test_store_mark_sent(tmp_path):
    store = NotificationStore(tmp_path / "s.sqlite3")
    store.enqueue("hold.created", "h1", "r1", {"x": 1}, "k1")
    due = store.fetch_due()
    assert len(due) == 1

    assert store.mark_sent(due[0]["id"]) is True
    assert store.fetch_due() == []


def test_store_retry_with_backoff(tmp_path):
    store = NotificationStore(tmp_path / "s.sqlite3", max_attempts=3, backoff_base_seconds=30)
    store.enqueue("hold.created", "h1", "r1", {"x": 1}, "k1")
    due = store.fetch_due()
    nid = due[0]["id"]

    state = store.mark_failed(nid, "network error")
    assert state["status"] == "PENDING"
    assert state["attempts"] == 1

    # next_attempt_at est dans le futur → pas de fetch
    assert store.fetch_due() == []


def test_store_dead_after_max_attempts(tmp_path):
    store = NotificationStore(tmp_path / "s.sqlite3", max_attempts=2, backoff_base_seconds=0)
    store.enqueue("hold.created", "h1", "r1", {"x": 1}, "k1")
    nid = store.fetch_due()[0]["id"]

    store.mark_failed(nid, "e1")  # attempts=1, PENDING
    state = store.mark_failed(nid, "e2")  # attempts=2, DEAD
    assert state["status"] == "DEAD"


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


def test_hold_created_triggers_notification(engine):
    r = engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 5000.0, "recipient": "Fournisseur_A"},
    )
    assert r["status"] == "HOLD"
    _wait_for_dispatch(engine, lambda: len(engine._sent_calls) == 1)
    assert len(engine._sent_calls) == 1
    assert engine._sent_calls[0]["event_type"] == "hold.created"
    assert engine._sent_calls[0]["action_id"] == r["action_id"]


def test_approved_event_routes_only_to_subscribers(tmp_path):
    policy_path = _write_policy(tmp_path, events=["hold.approved"])
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    engine = PolicyEngine(policy_path=policy_path, state_path=state)

    sent = []

    class FakeNotifier:
        def send(self, payload):
            sent.append(payload)

    engine.notifiers["test_route"] = FakeNotifier()

    # A HOLD must NOT trigger a notification (the route only listens to approved)
    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    assert r["status"] == "HOLD"
    assert len(sent) == 0

    # We approve → it must trigger (async dispatch)
    engine.approve_hold(r["action_id"])
    _wait_for_dispatch(engine, lambda: len(sent) == 1)
    assert len(sent) == 1
    assert sent[0]["event_type"] == "hold.approved"


def test_hold_rejected_triggers_notification(tmp_path):
    policy_path = _write_policy(tmp_path, events=["hold.rejected"])
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    engine = PolicyEngine(policy_path=policy_path, state_path=state)

    sent = []

    class FakeNotifier:
        def send(self, payload):
            sent.append(payload)

    engine.notifiers["test_route"] = FakeNotifier()

    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    engine.reject_hold(r["action_id"], actor_id="root")

    _wait_for_dispatch(engine, lambda: len(sent) == 1)
    assert len(sent) == 1
    assert sent[0]["event_type"] == "hold.rejected"
    assert sent[0]["actor_id"] == "root"


def test_notification_retried_on_failure(engine):
    engine._fake_notifier.should_fail = True

    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    assert r["status"] == "HOLD"
    assert len(engine._sent_calls) == 0
    # The dispatch is async: wait until the failure is recorded.
    _wait_for_dispatch(
        engine,
        lambda: (
            bool(engine.notifications.list_recent())
            and engine.notifications.list_recent()[0]["attempts"] == 1
        ),
    )

    # La notification est PENDING avec attempts=1
    pending = engine.notifications.fetch_due()
    assert pending == []  # not due yet (backoff)

    all_notifs = engine.notifications.list_recent()
    assert len(all_notifs) == 1
    assert all_notifs[0]["status"] == "PENDING"
    assert all_notifs[0]["attempts"] == 1
    assert "boom" in all_notifs[0]["last_error"]


def test_dispatch_marks_sent(engine):
    r = engine.evaluate(
        "agent_finance_01", "send_payment", {"amount": 5000.0, "recipient": "Fournisseur_A"}
    )
    _wait_for_dispatch(
        engine,
        lambda: (
            bool(engine.notifications.list_recent())
            and engine.notifications.list_recent()[0]["status"] == "SENT"
        ),
    )
    all_notifs = engine.notifications.list_recent()
    assert len(all_notifs) == 1
    assert all_notifs[0]["status"] == "SENT"
    assert all_notifs[0]["sent_at"] is not None


# ---------------------------------------------------------------------------
# Notifier formatting
# ---------------------------------------------------------------------------


def test_slack_payload_shape():
    import os

    from pryxor_secrets import EnvSecretProvider

    os.environ["SLACK_TEST"] = "https://hooks.slack.com/test"

    import requests

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        r = Mock()
        r.status_code = 200
        r.text = "ok"
        return r

    requests.post = fake_post

    n = SlackNotifier({"webhook_url_ref": "SLACK_TEST"}, EnvSecretProvider())
    n.send(
        {
            "event_type": "hold.created",
            "agent_id": "a1",
            "tool_name": "send_payment",
            "parameters": {"amount": 5000, "recipient": "X"},
            "reason": "OVER_LIMIT",
            "action_id": "h1",
        }
    )

    assert captured["url"] == "https://hooks.slack.com/test"
    assert "text" in captured["body"]
    assert "a1" in captured["body"]["text"]


def test_teams_payload_shape():
    import os

    from pryxor_secrets import EnvSecretProvider

    os.environ["TEAMS_TEST"] = "https://outlook.office.com/test"

    import requests

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        r = Mock()
        r.status_code = 200
        r.text = "ok"
        return r

    requests.post = fake_post

    n = TeamsNotifier({"webhook_url_ref": "TEAMS_TEST"}, EnvSecretProvider())
    n.send(
        {
            "event_type": "hold.created",
            "agent_id": "a1",
            "tool_name": "t",
            "reason": "R",
            "action_id": "h1",
            "parameters": {},
        }
    )

    assert captured["body"]["@type"] == "MessageCard"
    assert "sections" in captured["body"]


def test_webhook_bearer_auth():
    import os

    from pryxor_secrets import EnvSecretProvider

    os.environ["SOC_TOKEN"] = "sk_soc_123"

    import requests

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        r = Mock()
        r.status_code = 200
        r.text = "ok"
        return r

    requests.post = fake_post

    n = WebhookNotifier(
        {"url": "https://soc.example/hook", "auth": {"type": "bearer", "secret_ref": "SOC_TOKEN"}},
        EnvSecretProvider(),
    )
    n.send({"event_type": "hold.created", "action_id": "h1"})

    assert captured["headers"]["Authorization"] == "Bearer sk_soc_123"


def test_slack_mention_is_prepended():
    import os

    from pryxor_secrets import EnvSecretProvider

    os.environ["SLACK_TEST"] = "https://hooks.slack.com/test"

    import requests

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        r = Mock()
        r.status_code = 200
        r.text = "ok"
        return r

    requests.post = fake_post

    n = SlackNotifier(
        {"webhook_url_ref": "SLACK_TEST", "mention": "<!subteam^S12345>"},
        EnvSecretProvider(),
    )
    n.send(
        {
            "event_type": "hold.created",
            "agent_id": "a1",
            "tool_name": "t",
            "reason": "R",
            "action_id": "h1",
            "parameters": {},
        }
    )

    assert "<!subteam^S12345>" in captured["body"]["text"]


def test_evaluate_does_not_block_on_slack(engine, monkeypatch):
    """
    Regression: evaluate() must NOT dispatch synchronously.
    The agent must never wait on Slack.
    """
    import time

    # Simulate a very slow notifier (5 seconds)
    def slow_send(payload):
        time.sleep(5.0)

    class SlowNotifier:
        def send(self, payload):
            slow_send(payload)

    engine.notifiers["test_route"] = SlowNotifier()

    start = time.monotonic()
    r = engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 5000.0, "recipient": "Fournisseur_A"},
    )
    elapsed = time.monotonic() - start

    assert r["status"] == "HOLD"
    # evaluate() must NOT wait for the (5s) notifier. We allow a generous
    # margin so the test is not flaky on slow/loaded CI machines; the point
    # is that elapsed stays well under the notifier's 5s sleep.
    assert elapsed < 2.5, f"evaluate() blocked for {elapsed:.2f}s"


def test_dispatch_worker_sends_eventually(engine):
    """
    The background worker must eventually send the notification.
    """
    import time

    r = engine.evaluate(
        "agent_finance_01",
        "send_payment",
        {"amount": 5000.0, "recipient": "Fournisseur_A"},
    )
    assert r["status"] == "HOLD"

    # Wait up to 5s for the worker to process
    for _ in range(50):
        notifs = engine.notifications.list_recent()
        if notifs and notifs[0]["status"] == "SENT":
            break
        time.sleep(0.1)

    notifs = engine.notifications.list_recent()
    assert len(notifs) == 1
    assert notifs[0]["status"] == "SENT"


def test_stop_dispatch_worker_is_idempotent(engine):
    engine.stop_dispatch_worker()
    engine.stop_dispatch_worker()  # ne doit pas lever
