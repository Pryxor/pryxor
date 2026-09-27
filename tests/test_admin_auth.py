"""
Admin auth tests: strict separation of agents / admins.
"""

from __future__ import annotations

import importlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_app(monkeypatch, tmp_path):
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    import pryxor_auth
    import pryxor_engine
    import pryxor_proxy

    importlib.reload(pryxor_auth)
    importlib.reload(pryxor_engine)
    importlib.reload(pryxor_proxy)

    monkeypatch.setattr(pryxor_proxy, "STATE_PATH", str(state))
    monkeypatch.setattr(pryxor_proxy, "engine", pryxor_engine.PolicyEngine(state_path=state))
    monkeypatch.setattr(pryxor_proxy, "agent_registry", pryxor_auth.AgentRegistry(state_path=state))
    monkeypatch.setattr(pryxor_proxy, "admin_registry", pryxor_auth.AdminRegistry(state_path=state))
    return pryxor_proxy


def test_agent_key_prefix(fresh_app):
    result = fresh_app.agent_registry.register_agent("agent_x")
    assert result["api_key"].startswith("pryxor_agent_agent_x_")


def test_admin_key_prefix(fresh_app):
    result = fresh_app.admin_registry.register_admin("admin_x")
    assert result["api_key"].startswith("pryxor_admin_admin_x_")


def test_admin_key_cannot_auth_as_agent(fresh_app):
    admin = fresh_app.admin_registry.register_admin("admin_y")
    # The agent registry must NOT recognize this key
    assert fresh_app.agent_registry.verify_key(admin["api_key"]) is None


def test_agent_key_cannot_auth_as_admin(fresh_app):
    agent = fresh_app.agent_registry.register_agent("agent_y")
    assert fresh_app.admin_registry.verify_key(agent["api_key"]) is None


def test_admin_endpoint_requires_admin_key(fresh_app):
    client = TestClient(fresh_app.app)
    assert client.get("/v1/holds").status_code == 401
    assert client.get("/v1/audit").status_code == 401
    assert client.get("/v1/executions").status_code == 401


def test_agent_key_rejected_on_admin_endpoint(fresh_app):
    agent = fresh_app.agent_registry.register_agent("agent_z")
    client = TestClient(fresh_app.app)
    r = client.get("/v1/holds", headers={"X-Admin-Key": agent["api_key"]})
    assert r.status_code == 401


def test_admin_key_rejected_on_agent_endpoint(fresh_app):
    admin = fresh_app.admin_registry.register_admin("admin_z")
    client = TestClient(fresh_app.app)
    r = client.post(
        "/v1/execute-tool",
        json={"tool_name": "send_payment", "parameters": {"amount": 100, "recipient": "X"}},
        headers={"X-Agent-Key": admin["api_key"]},
    )
    assert r.status_code == 401


def test_approve_records_actor_in_audit(fresh_app):
    """The name of the approving admin must be in the audit."""
    admin = fresh_app.admin_registry.register_admin("root")
    agent = fresh_app.agent_registry.register_agent("agent_finance_01")

    # Configure une politique qui produit un HOLD
    fresh_app.engine.policy["allowed_actions"] = {
        "agent_finance_01": ["send_payment"],
    }
    fresh_app.engine.sector.sector_config = {
        "max_single_transaction": 500.0,
        "velocity_window_hours": 24,
        "velocity_limit": 10000.0,
        "allowed_recipients": ["Fournisseur_A"],
    }
    from pryxor_executors import MockExecutor

    fresh_app.engine.executors["send_payment"] = MockExecutor({})

    client = TestClient(fresh_app.app)

    # Trigger a HOLD
    r = client.post(
        "/v1/execute-tool",
        json={
            "tool_name": "send_payment",
            "parameters": {"amount": 5000.0, "recipient": "Fournisseur_A"},
        },
        headers={"X-Agent-Key": agent["api_key"]},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "HOLD"
    action_id = r.json()["action_id"]

    # Approuve
    r2 = client.post(
        f"/v1/holds/{action_id}/approve",
        headers={"X-Admin-Key": admin["api_key"]},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "APPROVED"
    assert r2.json()["actor_id"] == "root"

    # Check the audit
    r3 = client.get("/v1/audit", headers={"X-Admin-Key": admin["api_key"]})
    events = r3.json()["audit_events"]
    approved = [e for e in events if e["event_type"] == "approved"]
    assert len(approved) == 1
    assert approved[0]["payload"]["actor_id"] == "root"
    assert approved[0]["payload"]["actor_type"] == "admin"


def test_revoke_admin_blocks_endpoint(fresh_app):
    admin = fresh_app.admin_registry.register_admin("temp_admin")
    fresh_app.admin_registry.revoke_admin("temp_admin")

    client = TestClient(fresh_app.app)
    r = client.get("/v1/holds", headers={"X-Admin-Key": admin["api_key"]})
    assert r.status_code == 401
