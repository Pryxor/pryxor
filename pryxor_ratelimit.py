"""
Pryxor — Rate limiter (per-agent, sliding-window).

Goal: protect the proxy against a flooding agent (buggy or malicious)
without blocking the others. The sliding window is stored in SQLite and
is consistent across processes.

Config (in configs/pryxor.json):
    "rate_limit": {
      "enabled": true,
      "requests_per_minute": 60,
      "burst": 10
    }

"burst" is the instantaneous tolerance: the bucket allows
`requests_per_minute` in steady state, but accepts up to
`burst` requests within a single second before it starts rejecting.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import sqlite3

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _open_connection(state_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(state_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class RateLimiter:
    """
    Sliding window over `requests_per_minute` plus instantaneous tolerance.
    """

    WINDOW_SECONDS = 60

    def __init__(
        self,
        state_path: Path,
        requests_per_minute: int = 60,
        burst: int = 10,
    ):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.rpm = max(1, int(requests_per_minute))
        self.burst = max(1, int(burst))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return _open_connection(self.state_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limit_events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id  TEXT NOT NULL,
                    ts        TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rl_agent_ts ON rate_limit_events(agent_id, ts)"
            )
            conn.commit()

    def check(self, agent_id: str) -> dict[str, Any]:
        """
        Check and consume a token for this agent.

        Returns:
            {"allowed": True, "remaining": N}
            {"allowed": False, "retry_after": seconds}
        """
        now = _utcnow()
        cutoff = (now - timedelta(seconds=self.WINDOW_SECONDS)).isoformat()
        now_iso = now.isoformat()
        burst_cutoff = (now - timedelta(seconds=1)).isoformat()

        with self._connect() as conn:
            # Opportunistic purge (keeps the table small).
            conn.execute("DELETE FROM rate_limit_events WHERE ts < ?", (cutoff,))

            # Count over the 60s window
            window_row = conn.execute(
                "SELECT COUNT(*) AS c FROM rate_limit_events WHERE agent_id = ? AND ts >= ?",
                (agent_id, cutoff),
            ).fetchone()
            window_count = window_row["c"] if window_row else 0

            # Count over the last second (burst)
            burst_row = conn.execute(
                "SELECT COUNT(*) AS c FROM rate_limit_events WHERE agent_id = ? AND ts >= ?",
                (agent_id, burst_cutoff),
            ).fetchone()
            burst_count = burst_row["c"] if burst_row else 0

            # Decision.
            # Burst: allow up to `burst` events within the second, then reject
            # immediately until an older event expires.
            if burst_count >= self.burst:
                retry_after = 1
                return {
                    "allowed": False,
                    "retry_after": retry_after,
                    "reason": "burst",
                    "limit": self.burst,
                }

            if window_count >= self.rpm:
                # Retry when the oldest request leaves the window.
                oldest_row = conn.execute(
                    "SELECT ts FROM rate_limit_events "
                    "WHERE agent_id = ? AND ts >= ? ORDER BY ts ASC LIMIT 1",
                    (agent_id, cutoff),
                ).fetchone()
                if oldest_row:
                    oldest = datetime.fromisoformat(oldest_row["ts"])
                    retry_after = max(
                        1,
                        int((oldest + timedelta(seconds=self.WINDOW_SECONDS) - now).total_seconds())
                        + 1,
                    )
                else:
                    retry_after = 1
                return {
                    "allowed": False,
                    "retry_after": retry_after,
                    "reason": "window",
                    "limit": self.rpm,
                }

            # Allow + consume
            conn.execute(
                "INSERT INTO rate_limit_events(agent_id, ts) VALUES (?, ?)",
                (agent_id, now_iso),
            )
            conn.commit()

        remaining = max(0, self.rpm - (window_count + 1))
        return {"allowed": True, "remaining": remaining, "limit": self.rpm}
