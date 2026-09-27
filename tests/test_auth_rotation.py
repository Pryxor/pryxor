"""Key rotation tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pryxor_auth import AdminRegistry, AgentRegistry


@pytest.fixture
def agent_registry(tmp_path):
    return AgentRegistry(state_path=tmp_path / f"state_{uuid4().hex}.sqlite3")


@pytest.fixture
def admin_registry(tmp_path):
    return AdminRegistry(state_path=tmp_path / f"state_{uuid4().hex}.sqlite3")


def test_rotate_agent_generates_new_key(agent_registry):
    original = agent_registry.register_agent("agent_x")
    assert agent_registry.verify_key(original["api_key"]) == "agent_x"

    rotated = agent_registry.rotate_agent("agent_x")

    # Old key invalid
    assert agent_registry.verify_key(original["api_key"]) is None
    # New key valid
    assert agent_registry.verify_key(rotated["api_key"]) == "agent_x"
    # Same agent_id
    assert rotated["agent_id"] == "agent_x"
    # The keys are different
    assert rotated["api_key"] != original["api_key"]


def test_rotate_agent_preserves_history(agent_registry):
    """History (audit) references agent_id, not the key. It is preserved."""
    agent_registry.register_agent("agent_x")
    agent_registry.rotate_agent("agent_x")
    agent_registry.rotate_agent("agent_x")  # 2e rotation

    # The agent still exists with the same id
    agents = agent_registry.list_agents()
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "agent_x"
    assert agents[0]["revoked_at"] is None


def test_rotate_unknown_agent_raises(agent_registry):
    with pytest.raises(ValueError, match="does not exist"):
        agent_registry.rotate_agent("ghost")


def test_rotate_revoked_agent_raises(agent_registry):
    agent_registry.register_agent("agent_x")
    agent_registry.revoke_agent("agent_x")

    with pytest.raises(ValueError, match="is revoked"):
        agent_registry.rotate_agent("agent_x")


def test_rotate_admin_generates_new_key(admin_registry):
    original = admin_registry.register_admin("root")
    assert admin_registry.verify_key(original["api_key"]) == "root"

    rotated = admin_registry.rotate_admin("root")

    assert admin_registry.verify_key(original["api_key"]) is None
    assert admin_registry.verify_key(rotated["api_key"]) == "root"


def test_rotate_preserves_label(agent_registry):
    agent_registry.register_agent("agent_x", label="My agent")
    rotated = agent_registry.rotate_agent("agent_x")
    assert rotated["label"] == "My agent"


def test_rotate_can_change_label(agent_registry):
    agent_registry.register_agent("agent_x", label="Old label")
    rotated = agent_registry.rotate_agent("agent_x", label="New label")
    assert rotated["label"] == "New label"
