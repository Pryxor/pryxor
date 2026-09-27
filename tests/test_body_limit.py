"""Test de la limite de taille du body."""

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


def test_rejects_large_body(fresh_app):
    client = TestClient(fresh_app.app)
    big = "x" * (200 * 1024)
    r = client.post(
        "/v1/execute-tool",
        json={"tool_name": "send_payment", "parameters": {"data": big}},
        headers={"X-Agent-Key": "anything"},
    )
    assert r.status_code == 413


def test_accepts_small_body(fresh_app):
    client = TestClient(fresh_app.app)
    r = client.post(
        "/v1/execute-tool",
        json={"tool_name": "send_payment", "parameters": {"amount": 1.0}},
        headers={"X-Agent-Key": "anything"},
    )
    # 401 expected (invalid key), but NOT 413
    assert r.status_code == 401
