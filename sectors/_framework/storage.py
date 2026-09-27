"""
Pryxor — Generic storage for every sector.

⚠️ A SINGLE TABLE for everyone: `sector_events`.
    Adding a sector requires NO schema migration.
    Every event is tagged with `sector_name`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _open_connection(state_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(state_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class SectorStorage:
    """
    Unified, idempotent, thread-safe persistence.

    All operations are atomic at the SQLite level.
    The `dedup_key` field is UNIQUE → INSERT OR IGNORE = idempotence.
    """

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return _open_connection(self.state_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    sector_name   TEXT NOT NULL,
                    agent_id      TEXT NOT NULL,
                    tool_name     TEXT NOT NULL,
                    parameters    TEXT NOT NULL,
                    amount        REAL,
                    metadata      TEXT NOT NULL DEFAULT '{}',
                    timestamp     TEXT NOT NULL,
                    source        TEXT NOT NULL,
                    dedup_key     TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_se_sector_time "
                "ON sector_events(sector_name, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_se_agent_time ON sector_events(agent_id, timestamp)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        sector_name: str,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        source: str,
        dedup_key: str,
        amount: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Record an event.
        Returns True if inserted, False if already present (replay).
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO sector_events(
                    sector_name, agent_id, tool_name, parameters,
                    metadata, amount, timestamp, source, dedup_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sector_name,
                    agent_id,
                    tool_name,
                    json.dumps(parameters, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    amount,
                    datetime.now(timezone.utc).isoformat(),
                    source,
                    dedup_key,
                ),
            )
            conn.commit()
            return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Read / aggregates
    # ------------------------------------------------------------------

    def sum_since(
        self,
        sector_name: str,
        since_iso: str,
        agent_id: str | None = None,
        tool_name: str | None = None,
    ) -> float:
        sql = (
            "SELECT COALESCE(SUM(amount), 0.0) AS total "
            "FROM sector_events WHERE sector_name = ? AND timestamp >= ?"
        )
        args: list[Any] = [sector_name, since_iso]
        if agent_id:
            sql += " AND agent_id = ?"
            args.append(agent_id)
        if tool_name:
            sql += " AND tool_name = ?"
            args.append(tool_name)
        with self._connect() as conn:
            row = conn.execute(sql, args).fetchone()
        return float(row["total"]) if row else 0.0

    def count_since(
        self,
        sector_name: str,
        since_iso: str,
        agent_id: str | None = None,
        tool_name: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) AS c FROM sector_events WHERE sector_name = ? AND timestamp >= ?"
        args: list[Any] = [sector_name, since_iso]
        if agent_id:
            sql += " AND agent_id = ?"
            args.append(agent_id)
        if tool_name:
            sql += " AND tool_name = ?"
            args.append(tool_name)
        with self._connect() as conn:
            row = conn.execute(sql, args).fetchone()
        return int(row["c"]) if row else 0

    def list_recent(self, sector_name: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sector_events
                WHERE sector_name = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (sector_name, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "sector_name": r["sector_name"],
                "agent_id": r["agent_id"],
                "tool_name": r["tool_name"],
                "parameters": json.loads(r["parameters"]),
                "metadata": json.loads(r["metadata"]),
                "amount": r["amount"],
                "timestamp": r["timestamp"],
                "source": r["source"],
                "dedup_key": r["dedup_key"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Migration from the old schema (v1)
    # ------------------------------------------------------------------

    def migrate_from_v1_finance(self) -> int:
        """
        Copy finance_transactions → sector_events (only once).
        Returns the number of migrated rows.
        """
        with self._connect() as conn:
            # La vieille table existe-t-elle ?
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='finance_transactions'"
            ).fetchone()
            if not existing:
                return 0

            # Already migrated?
            already = conn.execute(
                "SELECT COUNT(*) FROM sector_events WHERE sector_name='finance'"
            ).fetchone()[0]
            if already > 0:
                return 0

            conn.execute(
                """
                INSERT OR IGNORE INTO sector_events(
                    sector_name, agent_id, tool_name, parameters,
                    metadata, amount, timestamp, source, dedup_key
                )
                SELECT
                    'finance', agent_id, tool_name, '{}', '{}',
                    amount, timestamp, source, dedup_key
                FROM finance_transactions
                """
            )
            conn.commit()
            return conn.total_changes
