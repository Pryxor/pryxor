"""
Tests du request ID propagation.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from pryxor_requestid import (
    clear_request_id,
    generate_request_id,
    get_request_id,
    set_request_id,
)


def test_generate_request_id_is_16_hex():
    rid = generate_request_id()
    assert len(rid) == 16
    assert all(c in "0123456789abcdef" for c in rid)


def test_get_returns_system_when_unset():
    clear_request_id()
    assert get_request_id() == "system"


def test_set_and_get():
    rid = set_request_id("abc123")
    assert rid == "abc123"
    assert get_request_id() == "abc123"


def test_set_none_generates_new():
    rid = set_request_id(None)
    assert rid != "system"
    assert len(rid) == 16


def test_clear_resets():
    set_request_id("xyz")
    assert get_request_id() == "xyz"
    clear_request_id()
    assert get_request_id() == "system"


# ---------------------------------------------------------------------------
# Integration test: HTTP middleware
# ---------------------------------------------------------------------------


def test_middleware_generates_request_id(monkeypatch, tmp_path):
    import importlib
    from uuid import uuid4

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

    client = TestClient(pryxor_proxy.app)
    r = client.get("/v1/health")

    assert r.status_code == 200
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) == 16


def test_middleware_propagates_incoming_request_id(monkeypatch, tmp_path):
    import importlib
    from uuid import uuid4

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

    client = TestClient(pryxor_proxy.app)
    r = client.get("/v1/health", headers={"X-Request-ID": "my-trace-123"})

    assert r.headers["X-Request-ID"] == "my-trace-123"
