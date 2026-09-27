"""Tests du rate limiter."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from pryxor_ratelimit import RateLimiter


@pytest.fixture
def rl(tmp_path):
    return RateLimiter(tmp_path / f"rl_{uuid4().hex}.sqlite3", requests_per_minute=5, burst=10)


def test_allows_under_limit(rl):
    for i in range(5):
        d = rl.check("agent_a")
        assert d["allowed"], f"call {i} should be allowed"


def test_rejects_over_window_limit(tmp_path):
    rl = RateLimiter(tmp_path / f"rl_window_{uuid4().hex}.sqlite3", requests_per_minute=5, burst=10)
    for _ in range(5):
        rl.check("agent_a")
    d = rl.check("agent_a")
    assert not d["allowed"]
    assert d["reason"] == "window"
    assert d["retry_after"] >= 1


def test_burst_rejects_fast(tmp_path):
    # burst=2: the 3rd call within <1s must be rejected by burst
    rl = RateLimiter(tmp_path / f"rl_burst_{uuid4().hex}.sqlite3", requests_per_minute=10, burst=2)
    rl.check("agent_a")
    rl.check("agent_a")
    d = rl.check("agent_a")
    assert not d["allowed"]
    assert d["reason"] == "burst"


def test_agents_have_separate_buckets(rl):
    for _ in range(5):
        rl.check("agent_a")
    # agent_b consumed nothing
    d = rl.check("agent_b")
    assert d["allowed"]


def test_remaining_decreases(rl):
    d1 = rl.check("agent_a")
    assert d1["remaining"] == 4
    d2 = rl.check("agent_a")
    assert d2["remaining"] == 3
