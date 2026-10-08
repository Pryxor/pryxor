"""
Regression tests for the three latent bugs identified in Phase 0.5.1:

    1. `process_outbox` routed hold callbacks to the default sector instead of
       the sector responsible for the tool. Security-relevant: the wrong
       sector's velocity was incremented and the right one was never notified.

    2. `atexit.register(self.stop_dispatch_worker)` was called once per
       PolicyEngine instance. Leaked references and grew the callback list
       linearly with the number of engines created.

    3. The rate limit handler in `pryxor_proxy.process_tool_call` raised
       `HTTPException(...)` with a literal Ellipsis, producing a 500 instead
       of a 429.
"""

from __future__ import annotations

import atexit
import json
from uuid import uuid4

import pytest

from pryxor_engine import PolicyEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_routing_policy(tmp_path):
    """Two sectors, two tools, each routed to its own sector."""
    policy = {
        "sector": "finance",  # global default
        "hold_ttl_minutes": 60,
        "allowed_actions": {"agent_x": ["send_email", "send_payment"]},
        "sectors": {
            "finance": {
                "type": "declarative",
                "rules": [
                    {
                        "name": "hold-big-payments",
                        "when": {"tool": "send_payment", "amount_gt": 500},
                        "then": {"hold": "OVER_LIMIT"},
                    }
                ],
            },
            "email": {
                "type": "declarative",
                "rules": [
                    {
                        "name": "hold-external-emails",
                        "when": {"tool": "send_email"},
                        "then": {"hold": "EXTERNAL_EMAIL"},
                    }
                ],
            },
        },
        "executors": {
            "send_email": {"type": "mock", "sector": "email"},
            "send_payment": {"type": "mock", "sector": "finance"},
        },
    }
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(policy), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Bug #1 — process_outbox must notify the tool's sector, not the default
# ---------------------------------------------------------------------------


def test_approved_hold_notifies_the_right_sector(tmp_path):
    """
    A HOLD on `send_email` (routed to `email`) must, when approved, call
    `on_hold_approved` on the *email* sector — never on the default sector
    (`finance`).
    """
    policy = _write_routing_policy(tmp_path)
    engine = PolicyEngine(policy_path=policy, state_path=tmp_path / f"{uuid4().hex}.sqlite3")

    notified: list[str] = []
    email_sector = engine._sectors["email"]
    finance_sector = engine._sectors["finance"]

    original_email = email_sector.on_hold_approved
    original_finance = finance_sector.on_hold_approved

    def spy_email(*args, **kwargs):
        notified.append("email")
        return original_email(*args, **kwargs)

    def spy_finance(*args, **kwargs):
        notified.append("finance")
        return original_finance(*args, **kwargs)

    email_sector.on_hold_approved = spy_email
    finance_sector.on_hold_approved = spy_finance

    try:
        r = engine.evaluate(
            "agent_x",
            "send_email",
            {"to": "bob@external.com", "subject": "Hi", "body": "x"},
        )
        assert r["status"] == "HOLD", r
        action_id = r["action_id"]

        engine.approve_hold(action_id)
    finally:
        email_sector.on_hold_approved = original_email
        finance_sector.on_hold_approved = original_finance
        engine.stop_dispatch_worker()

    assert "email" in notified, (
        f"the email sector must be notified, got {notified!r}"
    )
    assert "finance" not in notified, (
        f"the finance sector must NOT be notified, got {notified!r}"
    )


# ---------------------------------------------------------------------------
# Bug #2 — atexit must be registered once per process, not per engine
# ---------------------------------------------------------------------------


def test_atexit_registered_only_once(tmp_path, monkeypatch):
    """
    Creating N PolicyEngine instances must register the process-wide
    shutdown callback exactly once.
    """
    import pryxor_engine

    # Reset module state so this test is repeatable
    monkeypatch.setattr(pryxor_engine, "_shutdown_registered", False)
    import weakref

    monkeypatch.setattr(pryxor_engine, "_live_engines", weakref.WeakSet())

    registered: list[str] = []
    original_register = atexit.register

    def counting_register(fn, *args, **kwargs):
        name = getattr(fn, "__name__", repr(fn))
        registered.append(name)
        return original_register(fn, *args, **kwargs)

    monkeypatch.setattr(atexit, "register", counting_register)

    policy = _write_routing_policy(tmp_path)
    engines = []
    try:
        for i in range(3):
            eng = PolicyEngine(
                policy_path=policy,
                state_path=tmp_path / f"state_{i}_{uuid4().hex}.sqlite3",
            )
            engines.append(eng)
    finally:
        for eng in engines:
            eng.stop_dispatch_worker()

    shutdown_calls = [n for n in registered if n == "_stop_all_live_engines"]
    assert len(shutdown_calls) == 1, (
        f"expected exactly one process-wide shutdown registration, "
        f"got {len(shutdown_calls)} (calls: {registered!r})"
    )


# ---------------------------------------------------------------------------
# Bug #3 — rate limit handler must return a real 429, not a 500
# ---------------------------------------------------------------------------


def test_rate_limit_returns_429(tmp_path, monkeypatch):
    """
    When the per-agent rate limit is exceeded, the endpoint must return
    HTTP 429 with the documented headers. A regression here (Ellipsis in
    the raise) produced a 500 with no actionable detail.
    """
    import importlib

    from fastapi.testclient import TestClient

    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    monkeypatch.setenv("PRYXOR_STATE_PATH", str(state))

    # Minimal config with rate limit enabled and a tiny bucket
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "pryxor.json").write_text(
        json.dumps(
            {
                "sector": "finance",
                "hold_ttl_minutes": 60,
                "secrets": {"provider": "env"},
                "rate_limit": {"enabled": True, "requests_per_minute": 1, "burst": 1},
            }
        ),
        encoding="utf-8",
    )
    (configs / "agents.json").write_text(
        json.dumps({"allowed_actions": {"agent_rl": ["send_payment"]}}),
        encoding="utf-8",
    )
    (configs / "executors").mkdir()
    (configs / "executors" / "send_payment.json").write_text(
        json.dumps({"type": "mock"}),
        encoding="utf-8",
    )
    (configs / "sectors").mkdir()
    (configs / "sectors" / "finance.json").write_text(
        json.dumps({"type": "declarative", "rules": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PRYXOR_CONFIG_DIR", str(configs))

    import pryxor_auth
    import pryxor_engine
    import pryxor_proxy

    importlib.reload(pryxor_auth)
    importlib.reload(pryxor_engine)
    importlib.reload(pryxor_proxy)

    monkeypatch.setattr(pryxor_proxy, "STATE_PATH", str(state))
    monkeypatch.setattr(
        pryxor_proxy,
        "engine",
        pryxor_engine.PolicyEngine(state_path=state, policy_path=configs),
    )
    monkeypatch.setattr(
        pryxor_proxy, "agent_registry", pryxor_auth.AgentRegistry(state_path=state)
    )
    monkeypatch.setattr(
        pryxor_proxy, "admin_registry", pryxor_auth.AdminRegistry(state_path=state)
    )

    agent = pryxor_proxy.agent_registry.register_agent("agent_rl")
    client = TestClient(pryxor_proxy.app)

    body = {"tool_name": "send_payment", "parameters": {"amount": 1}}
    headers = {"X-Agent-Key": agent["api_key"]}

    # First call: allowed (consumes the single token)
    r1 = client.post("/v1/execute-tool", json=body, headers=headers)
    assert r1.status_code == 200, r1.text

    # Second call: must be 429, not 500
    r2 = client.post("/v1/execute-tool", json=body, headers=headers)
    assert r2.status_code == 429, (
        f"expected 429, got {r2.status_code}: {r2.text}"
    )
    assert "Retry-After" in r2.headers
    assert "X-RateLimit-Limit" in r2.headers
    assert "Rate limit exceeded" in r2.json()["detail"]

    try:
        pryxor_proxy.engine.stop_dispatch_worker()
    except Exception:
        pass
