"""
Tests for the Cloud sector.

Goal: prove that a custom sector protects infrastructure actions
with the same engine as payments — without touching the engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from sectors.cloud import CloudSector


def _cfg(**overrides) -> dict:
    base = {
        "allowed_regions": ["eu-west-1", "eu-west-3"],
        "allowed_instance_types": ["t3.micro", "t3.small"],
        "max_estimated_cost_per_hour": 5.0,
        "velocity_limit": 100.0,
        "velocity_window_hours": 1,
    }
    base.update(overrides)
    return base


@pytest.fixture
def sector(tmp_path):
    return CloudSector(
        policy={"sector": "cloud", "sectors": {"cloud": _cfg()}},
        state_path=tmp_path / f"cloud_{uuid4().hex}.sqlite3",
    )


# ---------------------------------------------------------------------------
# Lecture seule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    ["list_instances", "get_instance", "describe_cluster", "read_logs", "search_metrics"],
)
def test_readonly_actions_are_approved(sector, tool):
    d = sector.evaluate("agent_ops", tool, {})
    assert d.status.name == "APPROVED"


# ---------------------------------------------------------------------------
# Actions destructives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    ["delete_instance", "terminate_cluster", "destroy_volume", "drop_database", "revoke_key"],
)
def test_destructive_actions_are_held(sector, tool):
    d = sector.evaluate("agent_ops", tool, {"resource_id": "r-1"})
    assert d.status.name == "HOLD"
    assert d.reason == "DESTRUCTIVE_ACTION"


def test_destructive_action_on_prod_has_specific_reason(sector):
    d = sector.evaluate(
        "agent_ops", "delete_instance", {"resource_id": "i-1", "environment": "production"}
    )
    assert d.status.name == "HOLD"
    assert d.reason == "DESTRUCTIVE_ACTION_ON_PROD"


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------


def test_region_outside_allowlist_is_blocked(sector):
    d = sector.evaluate("agent_ops", "create_instance", {"region": "us-east-1"})
    assert d.status.name == "BLOCKED"
    assert d.reason == "REGION_NOT_ALLOWED"


def test_region_in_allowlist_is_approved(sector):
    d = sector.evaluate(
        "agent_ops",
        "create_instance",
        {"region": "eu-west-1", "instance_type": "t3.micro"},
    )
    assert d.status.name == "APPROVED"


def test_no_region_provided_is_not_blocked(sector):
    # No region provided: we do not block on the region.
    d = sector.evaluate("agent_ops", "create_instance", {"instance_type": "t3.micro"})
    assert d.status.name == "APPROVED"


# ---------------------------------------------------------------------------
# Types d'instance
# ---------------------------------------------------------------------------


def test_instance_type_outside_allowlist_is_blocked(sector):
    d = sector.evaluate("agent_ops", "create_instance", {"instance_type": "m5.24xlarge"})
    assert d.status.name == "BLOCKED"
    assert d.reason == "INSTANCE_TYPE_NOT_ALLOWED"


def test_flavor_and_size_aliases_are_checked(sector):
    assert (
        sector.evaluate("a", "create_instance", {"flavor": "m5.24xlarge"}).status.name == "BLOCKED"
    )
    assert sector.evaluate("a", "create_instance", {"size": "m5.24xlarge"}).status.name == "BLOCKED"


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_cost_above_threshold_is_held(sector):
    d = sector.evaluate("agent_ops", "resize_instance", {"estimated_cost_per_hour": 50.0})
    assert d.status.name == "HOLD"
    assert d.reason == "COST_LIMIT_EXCEED"


def test_cost_below_threshold_is_approved(sector):
    d = sector.evaluate("agent_ops", "resize_instance", {"estimated_cost_per_hour": 1.0})
    assert d.status.name == "APPROVED"


def test_cost_aliases_are_checked(sector):
    assert sector.evaluate("a", "resize_instance", {"hourly_cost": 50.0}).status.name == "HOLD"
    assert sector.evaluate("a", "resize_instance", {"cost_per_hour": 50.0}).status.name == "HOLD"


# ---------------------------------------------------------------------------
# Velocity (cumulative budget)
# ---------------------------------------------------------------------------


def test_velocity_limit_triggers_hold(sector):
    # 3 approved creations of $40/h = $120 > $100 (1h limit)
    for _ in range(3):
        sector.record_approved("agent_ops", "create_instance", {"estimated_cost_per_hour": 40.0})
    d = sector.evaluate("agent_ops", "create_instance", {"estimated_cost_per_hour": 5.0})
    assert d.status.name == "HOLD"
    assert d.reason == "VELOCITY_LIMIT_EXCEED"


def test_velocity_is_scoped_per_agent(sector):
    for _ in range(3):
        sector.record_approved("agent_a", "create_instance", {"estimated_cost_per_hour": 40.0})
    # Another agent consumed no budget.
    d = sector.evaluate("agent_b", "create_instance", {"estimated_cost_per_hour": 5.0})
    assert d.status.name == "APPROVED"


# ---------------------------------------------------------------------------
# Robustesse
# ---------------------------------------------------------------------------


def test_invalid_parameters_are_blocked(sector):
    d = sector.evaluate("agent_ops", "create_instance", None)  # type: ignore[arg-type]
    assert d.status.name == "BLOCKED"
    assert d.reason == "INVALID_TOOL_CALL"


def test_non_numeric_cost_is_treated_as_zero(sector):
    d = sector.evaluate("agent_ops", "resize_instance", {"estimated_cost_per_hour": "abc"})
    assert d.status.name == "APPROVED"


# ---------------------------------------------------------------------------
# Integration: auto-discovery by the registry
# ---------------------------------------------------------------------------


def test_cloud_sector_is_discovered_by_registry():
    import sectors

    assert "cloud" in sectors.SECTORS
    assert sectors.SECTORS["cloud"] is CloudSector


def test_cloud_sector_runs_through_the_engine(tmp_path):
    """Proof: the engine orchestrates the cloud sector without knowing it."""
    from pryxor_engine import PolicyEngine

    policy = {
        "sector": "cloud",
        "hold_ttl_minutes": 60,
        "allowed_actions": {"agent_ops": ["delete_instance"]},
        "sectors": {"cloud": _cfg()},
    }
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(policy), encoding="utf-8")

    engine = PolicyEngine(policy_path=p, state_path=tmp_path / f"state_{uuid4().hex}.sqlite3")
    try:
        r = engine.evaluate("agent_ops", "delete_instance", {"resource_id": "i-1"})
        assert r["status"] == "HOLD"
        assert r["reason"] == "DESTRUCTIVE_ACTION"
    finally:
        engine.stop_dispatch_worker()
