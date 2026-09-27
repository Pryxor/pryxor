"""
Tests for the Prometheus metrics.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("prometheus_client")


import pryxor_metrics as metrics  # noqa: E402


def test_metrics_are_available():
    assert metrics.is_enabled()


def test_render_contains_pryxor_metric_names():
    body, content_type = metrics.render_metrics()
    text = body.decode("utf-8")
    # The registry must contain the exposed names
    assert "pryxor_tool_calls_total" in text
    assert "pryxor_executions_total" in text
    assert "pryxor_holds_total" in text
    assert "pryxor_notifications_total" in text
    assert "text/plain" in content_type


def test_record_tool_call_increments():
    from prometheus_client import REGISTRY

    import pryxor_metrics

    body_before, _ = pryxor_metrics.render_metrics()
    metrics.record_tool_call("APPROVED", "send_email", "agent_x", 0.05)
    body_after, _ = pryxor_metrics.render_metrics()

    # The metric is present with the label
    assert "pryxor_tool_calls_total" in body_after.decode()
    assert 'tool="send_email"' in body_after.decode()


def test_record_execution():
    metrics.record_execution("send_payment", "success", 0.2)
    body, _ = metrics.render_metrics()
    assert 'tool="send_payment"' in body.decode()


def test_record_hold_transition():
    metrics.record_hold_transition("approved")
    body, _ = metrics.render_metrics()
    assert "pryxor_holds_total" in body.decode()


def test_record_notification():
    metrics.record_notification("slack_ops", "sent")
    body, _ = metrics.render_metrics()
    assert 'route="slack_ops"' in body.decode()


def test_record_rate_limit_hit():
    metrics.record_rate_limit_hit("window")
    body, _ = metrics.render_metrics()
    assert 'reason="window"' in body.decode()


def test_gauges_can_be_set():
    metrics.set_holds_pending(5)
    metrics.set_outbox_pending(2)
    metrics.set_notifications_pending(3)
    body, _ = metrics.render_metrics()
    text = body.decode()
    assert "pryxor_holds_pending 5" in text
    assert "pryxor_outbox_pending 2" in text


def test_metrics_disabled_via_env(monkeypatch):
    monkeypatch.setenv("PRYXOR_METRICS_ENABLED", "false")
    assert not metrics.is_enabled()
    body, _ = metrics.render_metrics()
    assert b"disabled" in body.lower()


# ---------------------------------------------------------------------------
# Integration test: engine → metrics
# ---------------------------------------------------------------------------


def _write_policy(tmp_path):
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
                "executors": {"send_payment": {"type": "mock"}},
                "metrics": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    return p


def test_engine_records_tool_call_metric(tmp_path):
    from pryxor_engine import PolicyEngine

    policy = _write_policy(tmp_path)
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    eng = PolicyEngine(policy_path=policy, state_path=state)

    try:
        eng.evaluate(
            "agent_finance_01",
            "send_payment",
            {"amount": 100.0, "recipient": "Fournisseur_A"},
        )
        body, _ = metrics.render_metrics()
        text = body.decode()
        assert 'tool="send_payment"' in text
        assert 'agent="agent_finance_01"' in text
        assert 'status="APPROVED"' in text
    finally:
        eng.stop_dispatch_worker()
