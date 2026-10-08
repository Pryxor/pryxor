"""
Authentication tests — verify that identity spoofing through the
payload is impossible.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_app(monkeypatch, tmp_path):
    """
    Create a fresh app instance with an isolated state DB.
    Reload the modules to avoid state leaking between tests.
    """
    state_path = tmp_path / f"state_{uuid4().hex}.sqlite3"

    # Patch les chemins avant de recharger les modules Pryxor
    monkeypatch.setenv("PRYXOR_STATE_PATH", str(state_path))

    # Recharger pryxor_auth, pryxor_engine, pryxor_proxy avec le nouveau state
    import pryxor_auth
    import pryxor_engine
    import pryxor_proxy

    importlib.reload(pryxor_auth)
    importlib.reload(pryxor_engine)
    importlib.reload(pryxor_proxy)

    # Patcher les singletons de pryxor_proxy
    monkeypatch.setattr(pryxor_proxy, "STATE_PATH", str(state_path))
    monkeypatch.setattr(pryxor_proxy, "engine", pryxor_engine.PolicyEngine(state_path=state_path))
    monkeypatch.setattr(
        pryxor_proxy, "agent_registry", pryxor_auth.AgentRegistry(state_path=state_path)
    )
    monkeypatch.setattr(
        pryxor_proxy, "admin_registry", pryxor_auth.AdminRegistry(state_path=state_path)
    )

    return pryxor_proxy


def test_register_and_verify_key(fresh_app):
    registry = fresh_app.agent_registry
    result = registry.register_agent("agent_test_01", label="Test")
    assert result["api_key"].startswith("pryxor_agent_agent_test_01_")
    assert registry.verify_key(result["api_key"]) == "agent_test_01"


def test_invalid_key_rejected(fresh_app):
    registry = fresh_app.agent_registry
    assert registry.verify_key("pryxor_agent_bogus_key") is None
    assert registry.verify_key("") is None
    assert registry.verify_key("not_pryxor_format") is None


def test_revoked_key_rejected(fresh_app):
    registry = fresh_app.agent_registry
    result = registry.register_agent("agent_test_02")
    assert registry.verify_key(result["api_key"]) == "agent_test_02"
    assert registry.revoke_agent("agent_test_02") is True
    assert registry.verify_key(result["api_key"]) is None


def test_pryxor_proxy_rejects_missing_key(fresh_app):
    client = TestClient(fresh_app.app)
    r = client.post("/v1/execute-tool", json={"tool_name": "send_payment", "parameters": {}})
    assert r.status_code == 401


def test_pryxor_proxy_rejects_invalid_key(fresh_app):
    client = TestClient(fresh_app.app)
    r = client.post(
        "/v1/execute-tool",
        json={"tool_name": "send_payment", "parameters": {}},
        headers={"X-Agent-Key": "pryxor_agent_fake_key"},
    )
    assert r.status_code == 401


def test_pryxor_proxy_ignores_payload_agent_id(fresh_app):
    """
    ⚠️ CRITICAL TEST: an agent authenticates with its real key, but tries
    to impersonate another agent via the payload.
    The engine must see the AUTHENTICATED identity, not the payload one.
    """
    registry = fresh_app.agent_registry
    engine = fresh_app.engine

    alice = registry.register_agent("agent_alice")
    bob = registry.register_agent("agent_bob")

    # Alice is authorized, Bob is not
    engine.policy["allowed_actions"] = {
        "agent_alice": ["send_payment"],
        "agent_bob": [],
    }

    client = TestClient(fresh_app.app)
    r = client.post(
        "/v1/execute-tool",
        json={
            "agent_id": "agent_alice",  # ⚠️ spoofing attempt
            "tool_name": "send_payment",
            "parameters": {"amount": 100.0, "recipient": "Fournisseur_A"},
        },
        headers={"X-Agent-Key": bob["api_key"]},  # Bob s'authentifie
    )

    assert r.status_code == 200
    data = r.json()
    # Bob does not have send_payment → BLOCKED. The spoofing failed.
    assert data["status"] == "BLOCKED"
    # The reason is deliberately generic: distinguishing "unknown tool"
    # from "known but unauthorized" would let an agent probe for the
    # tool catalog one call at a time. The distinction lives only in
    # the operator-facing audit, never in the agent-facing response.
    assert data["reason"] == "NOT_AUTHORIZED"


def test_engine_requires_agent_id(fresh_app):
    """The engine refuses a call without agent_id (defense in depth)."""
    engine = fresh_app.engine
    result = engine.evaluate("", "send_payment", {"amount": 100.0})
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "MISSING_AGENT_IDENTITY"
