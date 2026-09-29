"""
Pryxor — Policy Engine (orchestrator).

⚠️ SECURITY:
    - evaluate() requires an AUTHENTICATED agent_id.
    - The engine knows NO business rule: everything is delegated to the sector.
    - approve/reject use an atomic SQL compare-and-swap.
    - HOLD transitions write an outbox_event in the SAME transaction, to
      guarantee atomicity with the side effects.
    - The TTL is applied at CAS time.
    - process_outbox() is idempotent and replayable.

⚠️ GATEWAY:
    - On a direct APPROVED, the engine calls ToolExecutor.execute().
    - On an approved HOLD, the engine calls ToolExecutor.execute() via the
      outbox (idempotent thanks to a stable idempotency_key).
    - The agent holds NO credential: they live in the proxy.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sqlite3
import threading
import time
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pryxor_metrics as metrics
from pryxor_config import load_config
from pryxor_executors import ExecutionResult, build_executor_registry
from pryxor_notifiers import NotificationStore, build_notifier_registry, routes_for_event
from pryxor_ratelimit import RateLimiter
from pryxor_redaction import build_redactor
from pryxor_requestid import get_request_id
from pryxor_secrets import build_secret_provider
from pryxor_validation import validate_arguments
from sectors import Decision, DecisionStatus, load_sector

# ---------------------------------------------------------------------------
# Process-wide shutdown registration.
#
# Registering atexit.register(self.stop_dispatch_worker) inside every
# PolicyEngine.__init__ leaks one strong reference per instance and grows the
# atexit callback list linearly with the number of engines created (20+ in the
# test suite). Instead we keep a WeakSet of live engines and register a single
# process-wide callback the first time an engine is built.
# ---------------------------------------------------------------------------
_live_engines: "weakref.WeakSet[PolicyEngine]" = weakref.WeakSet()
_shutdown_lock = threading.Lock()
_shutdown_registered = False


def _stop_all_live_engines() -> None:
    """Stop every live PolicyEngine's dispatch worker.

    Registered once per process. Uses a WeakSet, so engines that have already
    been garbage-collected are skipped without us holding a reference to them.
    """
    for eng in list(_live_engines):
        try:
            eng.stop_dispatch_worker()
        except Exception:
            logger.debug("Failed to stop dispatch worker during shutdown.", exc_info=True)


def _ensure_atexit_registered() -> None:
    """Register the process-wide shutdown callback exactly once."""
    global _shutdown_registered
    with _shutdown_lock:
        if _shutdown_registered:
            return
        _shutdown_registered = True
        atexit.register(_stop_all_live_engines)
logger = logging.getLogger("pryxor.engine")


DEFAULT_POLICY: dict[str, Any] = {
    "sector": "finance",
    "hold_ttl_minutes": 60,
    "allowed_actions": {
        "agent_finance_01": ["send_payment"],
    },
    "sectors": {
        "finance": {
            "max_single_transaction": 500.0,
            "velocity_window_hours": 24,
            "velocity_limit": 1000.0,
            "allowed_recipients": ["Fournisseur_A", "Compte_Trusted"],
        },
    },
    "executors": {
        "send_payment": {"type": "mock"},
    },
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dispatch_worker_enabled_from_env() -> bool:
    """Whether the background notification worker starts automatically.

    Default: enabled. Set ``PRYXOR_DISPATCH_WORKER=0`` (or ``false`` / ``no``)
    to disable — used by the test-suite so no daemon worker outlives its engine.
    """
    value = os.environ.get("PRYXOR_DISPATCH_WORKER", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def _open_connection(state_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(state_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class HoldStore:
    """
    Persistance SQLite : holds, audit, outbox, executions.
    """

    def __init__(self, ttl_minutes: int = 60, state_path: str | Path | None = None):
        self.ttl_minutes = ttl_minutes
        self.state_path = Path(state_path) if state_path else Path("pryxor_state.sqlite3")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return _open_connection(self.state_path)

    @staticmethod
    def _paginate(limit: int, offset: int, max_limit: int = 500) -> tuple[int, int]:
        """Normalize limit/offset to prevent abuse."""
        limit = max(1, min(int(limit), max_limit))
        offset = max(0, int(offset))
        return limit, offset

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS holds (
                    action_id     TEXT PRIMARY KEY,
                    status        TEXT NOT NULL,
                    agent_id      TEXT NOT NULL,
                    tool_name     TEXT NOT NULL,
                    parameters    TEXT NOT NULL,
                    reason        TEXT NOT NULL,
                    message       TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    expires_at    TEXT NOT NULL,
                    approved_at   TEXT,
                    rejected_at   TEXT,
                    updated_at    TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_holds_status ON holds(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_holds_expires ON holds(expires_at)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id    TEXT,
                    agent_id     TEXT,
                    event_type   TEXT NOT NULL,
                    payload      TEXT NOT NULL,
                    created_at   TEXT NOT NULL
                )
                """
            )

            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type    TEXT NOT NULL,
                    action_id     TEXT NOT NULL,
                    agent_id      TEXT NOT NULL,
                    tool_name     TEXT NOT NULL,
                    parameters    TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    processed_at  TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_processed ON outbox_events(processed_at)"
            )

            # ⚠️ GATEWAY: execution table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key   TEXT NOT NULL UNIQUE,
                    action_id         TEXT,
                    agent_id          TEXT NOT NULL,
                    tool_name         TEXT NOT NULL,
                    parameters        TEXT NOT NULL,
                    status            TEXT NOT NULL,
                    attempt           INTEGER NOT NULL DEFAULT 1,
                    status_code       INTEGER,
                    result            TEXT,
                    error             TEXT,
                    created_at        TEXT NOT NULL,
                    completed_at      TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_action ON executions(action_id)")
            # Migration : ajoute actor_id si elle n'existe pas (idempotent)
            cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()
            }
            if "actor_id" not in cols:
                conn.execute("ALTER TABLE audit_events ADD COLUMN actor_id TEXT")
                logger.info("Migrated: added audit_events.actor_id column.")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_id)")

            conn.commit()

    # ------------------------------------------------------------------
    # Reading (holds / audit / outbox)
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "action_id": row["action_id"],
            "status": row["status"],
            "agent_id": row["agent_id"],
            "tool_name": row["tool_name"],
            "parameters": json.loads(row["parameters"]),
            "reason": row["reason"],
            "message": row["message"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "approved_at": row["approved_at"],
            "rejected_at": row["rejected_at"],
            "updated_at": row["updated_at"],
        }

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = self._paginate(limit, offset)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM holds ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows if r is not None]

    def count_holds(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM holds").fetchone()
        return int(row["c"]) if row else 0

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM holds WHERE action_id = ?", (action_id,)).fetchone()
        return self._row_to_dict(row)

    def _record_audit(
        self,
        action_id: str | None,
        agent_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events(
                    action_id, agent_id, event_type, payload, created_at, actor_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    agent_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    actor_id,
                ),
            )
            conn.commit()

    def list_audit(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = self._paginate(limit, offset)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            keys = row.keys()
            result.append(
                {
                    "id": row["id"],
                    "action_id": row["action_id"],
                    "agent_id": row["agent_id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload"]),
                    "created_at": row["created_at"],
                    "actor_id": row["actor_id"] if "actor_id" in keys else None,
                }
            )
        return result

    def count_audit(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()
        return int(row["c"]) if row else 0

    def list_pending_outbox(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox_events
                WHERE processed_at IS NULL
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "action_id": r["action_id"],
                "agent_id": r["agent_id"],
                "tool_name": r["tool_name"],
                "parameters": json.loads(r["parameters"]),
                "created_at": r["created_at"],
                "processed_at": r["processed_at"],
            }
            for r in rows
        ]

    def get_execution(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "idempotency_key": row["idempotency_key"],
            "action_id": row["action_id"],
            "agent_id": row["agent_id"],
            "tool_name": row["tool_name"],
            "parameters": json.loads(row["parameters"]),
            "status": row["status"],
            "status_code": row["status_code"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    def list_executions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = self._paginate(limit, offset)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM executions ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {
                "idempotency_key": r["idempotency_key"],
                "action_id": r["action_id"],
                "agent_id": r["agent_id"],
                "tool_name": r["tool_name"],
                "status": r["status"],
                "status_code": r["status_code"],
                "error": r["error"],
                "created_at": r["created_at"],
                "completed_at": r["completed_at"],
            }
            for r in rows
        ]

    def count_executions(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM executions").fetchone()
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # Write: holds
    # ------------------------------------------------------------------

    def create(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        action_id = f"hold_{uuid4().hex[:8]}"
        created_at = utcnow_iso()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=self.ttl_minutes)).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO holds(
                    action_id, status, agent_id, tool_name, parameters, reason,
                    message, created_at, expires_at, updated_at
                ) VALUES (?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    agent_id,
                    tool_name,
                    json.dumps(parameters, ensure_ascii=False),
                    reason,
                    message,
                    created_at,
                    expires_at,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_events(action_id, agent_id, event_type, payload, created_at)
                VALUES (?, ?, 'created', ?, ?)
                """,
                (
                    action_id,
                    agent_id,
                    json.dumps({"reason": reason, "message": message}, ensure_ascii=False),
                    created_at,
                ),
            )
            conn.commit()

        return {
            "action_id": action_id,
            "status": "PENDING",
            "agent_id": agent_id,
            "tool_name": tool_name,
            "parameters": parameters,
            "reason": reason,
            "message": message,
            "created_at": created_at,
            "expires_at": expires_at,
            "approved_at": None,
            "rejected_at": None,
            "updated_at": created_at,
        }

    def _cas_transition(
        self,
        action_id: str,
        new_status: str,
        timestamp_field: str,
        human_message: str,
        audit_event_type: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Atomic compare-and-swap + outbox + audit in the same transaction.
        `actor_id` (if provided) identifies the admin who triggered the transition.
        """
        now = utcnow_iso()

        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE holds
                SET status = ?, {timestamp_field} = ?, updated_at = ?, message = ?
                WHERE action_id = ? AND status = 'PENDING' AND expires_at > ?
                """,
                (new_status, now, now, human_message, action_id, now),
            )

            if cur.rowcount == 1:
                row = conn.execute(
                    "SELECT * FROM holds WHERE action_id = ?", (action_id,)
                ).fetchone()

                conn.execute(
                    """
                    INSERT INTO outbox_events(
                        event_type, action_id, agent_id, tool_name,
                        parameters, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"hold.{new_status.lower()}",
                        action_id,
                        row["agent_id"],
                        row["tool_name"],
                        row["parameters"],
                        now,
                    ),
                )

                # ⚠️ The audit now records the actor
                audit_payload: dict[str, Any] = {timestamp_field: now}
                if actor_id:
                    audit_payload["actor_id"] = actor_id
                    audit_payload["actor_type"] = "admin"

                    conn.execute(
                        """
                    INSERT INTO audit_events(
                        action_id, agent_id, event_type, payload, created_at, actor_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            action_id,
                            row["agent_id"],
                            audit_event_type,
                            json.dumps(audit_payload, ensure_ascii=False),
                            now,
                            actor_id,
                        ),
                    )
                conn.commit()
                metrics.record_hold_transition(new_status.lower())
                response: dict[str, Any] = {
                    "status": new_status,
                    "action_id": action_id,
                    "message": human_message,
                    timestamp_field: now,
                    "parameters": json.loads(row["parameters"]),
                    "agent_id": row["agent_id"],
                    "tool_name": row["tool_name"],
                }

                response["actor_id"] = actor_id
                return response

            # CAS failed: figure out why
            row = conn.execute("SELECT * FROM holds WHERE action_id = ?", (action_id,)).fetchone()

            if row is None:
                return {
                    "status": "ERROR",
                    "action_id": action_id,
                    "message": f"Hold {action_id} not found.",
                }

            if row["status"] == "PENDING" and row["expires_at"] <= now:
                conn.execute(
                    """
                    UPDATE holds SET status='EXPIRED', updated_at=?, message=?
                    WHERE action_id=? AND status='PENDING'
                    """,
                    (now, f"Hold {action_id} expired.", action_id),
                )
                conn.execute(
                    """
                    INSERT INTO audit_events(
                        action_id, agent_id, event_type, payload, created_at
                    ) VALUES (?, ?, 'expired', ?, ?)
                    """,
                    (
                        action_id,
                        row["agent_id"],
                        json.dumps({"expires_at": row["expires_at"]}, ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
                return {
                    "status": "EXPIRED",
                    "action_id": action_id,
                    "message": f"Hold {action_id} has expired.",
                }

            return {
                "status": row["status"],
                "action_id": action_id,
                "message": f"Hold {action_id} is already {row['status']}.",
            }

    def approve(
        self,
        action_id: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        return self._cas_transition(
            action_id,
            "APPROVED",
            "approved_at",
            f"Hold {action_id} approved for execution.",
            "approved",
            actor_id=actor_id,
        )

    def reject(
        self,
        action_id: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        return self._cas_transition(
            action_id,
            "REJECTED",
            "rejected_at",
            f"Hold {action_id} rejected.",
            "rejected",
            actor_id=actor_id,
        )

    def expire_stale_holds(self) -> int:
        now = utcnow_iso()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT action_id, agent_id FROM holds WHERE status='PENDING' AND expires_at <= ?",
                (now,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE holds SET status='EXPIRED', updated_at=?, message=? "
                    "WHERE action_id=? AND status='PENDING'",
                    (now, f"Hold {row['action_id']} expired.", row["action_id"]),
                )
                conn.execute(
                    "INSERT INTO audit_events(action_id, agent_id, event_type, payload, created_at) "
                    "VALUES (?, ?, 'expired', ?, ?)",
                    (row["action_id"], row["agent_id"], json.dumps({}), now),
                )
            conn.commit()
            if len(rows) > 0:
                metrics.record_hold_transition("expired")
        return len(rows)

    # ------------------------------------------------------------------
    # Outbox
    # ------------------------------------------------------------------

    def fetch_unprocessed_outbox(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM outbox_events WHERE processed_at IS NULL ORDER BY id ASC"
            ).fetchall()

    def mark_outbox_processed(self, event_id: int) -> bool:
        now = utcnow_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE outbox_events SET processed_at = ? WHERE id = ? AND processed_at IS NULL",
                (now, event_id),
            )
            conn.commit()
            return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Executions (Gateway)
    # ------------------------------------------------------------------

    def record_execution_pending(
        self,
        idempotency_key: str,
        action_id: str | None,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> bool:
        """Return True if we are the first to insert it."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO executions(
                    idempotency_key, action_id, agent_id, tool_name,
                    parameters, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    idempotency_key,
                    action_id,
                    agent_id,
                    tool_name,
                    json.dumps(parameters, ensure_ascii=False),
                    utcnow_iso(),
                ),
            )
            conn.commit()
            return cur.rowcount == 1

    def record_execution_success(
        self,
        idempotency_key: str,
        result: dict[str, Any],
        status_code: int | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE executions
                SET status='SUCCESS', result=?, status_code=?, completed_at=?
                WHERE idempotency_key = ?
                """,
                (
                    json.dumps(result, ensure_ascii=False),
                    status_code,
                    utcnow_iso(),
                    idempotency_key,
                ),
            )
            conn.commit()

    def record_execution_failure(
        self,
        idempotency_key: str,
        error: str,
        status_code: int | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE executions
                SET status='FAILED', error=?, status_code=?, completed_at=?
                WHERE idempotency_key = ?
                """,
                (error, status_code, utcnow_iso(), idempotency_key),
            )
            conn.commit()


class PolicyEngine:
    """
    Generic orchestrator.
    """

    def __init__(
        self,
        policy_path: str | Path | None = None,
        state_path: str | Path | None = None,
        sector_name: str | None = None,
        enable_dispatch_worker: bool | None = None,
    ):
        from pryxor_paths import resolve_state_path

        self.state_path = Path(resolve_state_path(state_path))
        self._explicit_policy_path = Path(policy_path) if policy_path else None

        # Public for callers/tests: the path actually used to load the policy.
        # Set after loading (see `_load_policy`), which resolves the source.
        self.policy_path: Path | None = self._explicit_policy_path
        self.policy = self._load_policy()

        # --- Sector routing -------------------------------------------------
        # A single global `sector` is rarely enough: an email tool must be
        # judged by the email rules, a payment by the finance rules. So we
        # load every sector referenced by an executor (via `executors.<tool>.sector`)
        # plus the global default, and route each tool call to its own sector.
        default_sector_name = sector_name or self.policy.get("sector") or "finance"
        self._sectors: dict[str, Any] = {}

        # Fail loudly on a misconfigured sector: silently falling back to the
        # default would apply the WRONG policy to a tool with no visible error.
        # A security layer must never guess.
        for name in self._referenced_sector_names(default_sector_name):
            try:
                self._sectors[name] = load_sector(name, self.policy, self.state_path)
            except Exception as e:  # noqa: BLE001
                logger.error("FATAL: could not load sector '%s': %s", name, e)
                raise

        if default_sector_name not in self._sectors:
            # The default sector is mandatory: without it we cannot route calls.
            self._sectors[default_sector_name] = load_sector(
                default_sector_name, self.policy, self.state_path
            )
        logger.info(
            "Loaded %d sector(s): %s (default: %s)",
            len(self._sectors),
            sorted(self._sectors.keys()),
            default_sector_name,
        )
        # `self.sector` stays the default (backward-compatible for callers
        # that reference it directly, e.g. tests and the notification/outbox path).
        self.sector = self._sectors[default_sector_name]
        metrics.set_policy_info(
            sector=self.sector.name,
            version=getattr(self.sector, "version", "unknown"),
        )

        self.hold_store = HoldStore(
            ttl_minutes=self.policy.get("hold_ttl_minutes", 60),
            state_path=self.state_path,
        )

        # GATEWAY : secrets + executors
        self.secrets = build_secret_provider(self.policy)

        # PII redaction
        self.redactor = build_redactor(self.policy)
        if self.redactor.enabled:
            logger.info(
                "PII redaction enabled (default_mode=%s, patterns=%d).",
                self.redactor.default_mode.value,
                len(self.redactor.patterns),
            )
        self.executors = build_executor_registry(self.policy, self.secrets)
        logger.info(
            "Loaded %d executor(s): %s",
            len(self.executors),
            sorted(self.executors.keys()),
        )
        self._validate_sector_routing()

        # Notifications
        self.notifications = NotificationStore(state_path=self.state_path)
        self.notifiers = build_notifier_registry(self.policy, self.secrets)
        logger.info(
            "Loaded %d notification route(s): %s",
            len(self.notifiers),
            sorted(self.notifiers.keys()),
        )
        # Rate limiting
        rl_cfg = self.policy.get("rate_limit") or {}
        self.rate_limiter: RateLimiter | None = None
        if rl_cfg.get("enabled"):
            self.rate_limiter = RateLimiter(
                state_path=self.state_path,
                requests_per_minute=rl_cfg.get("requests_per_minute", 60),
                burst=rl_cfg.get("burst", 10),
            )
            logger.info(
                "Rate limiting enabled: %d/min, burst=%d",
                self.rate_limiter.rpm,
                self.rate_limiter.burst,
            )

        # Recovery
        self.hold_store.expire_stale_holds()

        # Recovery: process the outbox once at startup.
        self.hold_store.expire_stale_holds()
        self.process_outbox()

        # Worker async de notifications (non bloquant, daemon).
        #
        # The worker is started by default in production, but it can be turned
        # off explicitly (``enable_dispatch_worker=False``) or via the
        # ``PRYXOR_DISPATCH_WORKER`` environment variable (``0`` / ``false`` to
        # disable). Tests disable it so a leaked daemon worker cannot keep
        # polling SQLite or firing outbound HTTP after its engine is gone.
        self._dispatch_stop_event = threading.Event()
        self._dispatch_wakeup = threading.Event()
        self._dispatch_lock = threading.Lock()
        self._dispatch_thread: threading.Thread | None = None
        if enable_dispatch_worker is None:
            enable_dispatch_worker = _dispatch_worker_enabled_from_env()
        self._dispatch_worker_enabled = enable_dispatch_worker
        if enable_dispatch_worker:
            self._start_dispatch_worker()

        # Register this engine for the process-wide shutdown. The callback
        # itself is registered only once, no matter how many engines are built.
        _live_engines.add(self)
        _ensure_atexit_registered()
    def _referenced_sector_names(self, default_name: str) -> list[str]:

        # Collect every sector name referenced by the policy.
        names = [default_name]
        for cfg in (self.policy.get("executors") or {}).values():
            if isinstance(cfg, dict) and cfg.get("sector"):
                names.append(str(cfg["sector"]))

        # Preserve order, drop duplicates.
        return list(dict.fromkeys(names))

    def _validate_sector_routing(self) -> None:
        """
        Every executor that declares a sector must resolve to a loaded sector.

        This is a startup guard, not a runtime check: a typo in an executor's
        `sector` field would otherwise send its calls to the default sector —
        the wrong policy, silently. We refuse to start instead.
        """
        problems: list[str] = []
        for tool_name, cfg in (self.policy.get("executors") or {}).items():
            if not isinstance(cfg, dict):
                continue
            declared = cfg.get("sector")
            if declared and str(declared) not in self._sectors:
                problems.append(tool_name)
        if problems:
            raise ValueError(
                "These executors declare a 'sector' that is not configured: "
                f"{sorted(problems)}. Add configs/sectors/<name>.json (and, for a "
                "code sector, sectors/<name>.py), or fix the executor's 'sector' "
                "field. Refusing to start with ambiguous sector routing."
            )

    def _sector_for(self, tool_name: str):

        # Return the sector responsible for this tool (per-executor override).
        cfg = (self.policy.get("executors") or {}).get(tool_name)
        if isinstance(cfg, dict) and cfg.get("sector"):
            name = str(cfg["sector"])
            if name in self._sectors:
                return self._sectors[name]
        return self.sector

    def _load_policy(self) -> dict[str, Any]:

        # 1. An explicit path wins. In production this is always a config
        #    folder; a single JSON file is accepted here as an internal
        #    convenience for tests and embedding Pryxor as a library.
        if self._explicit_policy_path is not None:
            if self._explicit_policy_path.is_dir():
                from pryxor_config import _load_config_folder

                file_policy = _load_config_folder(self._explicit_policy_path)
            else:
                file_policy = json.loads(self._explicit_policy_path.read_text(encoding="utf-8"))
        else:
            # 2. Otherwise, resolve the `configs/` folder.
            file_policy = load_config()

        # Merge with the defaults.
        merged = {**DEFAULT_POLICY, **file_policy}
        for key in ("allowed_actions", "sectors", "executors"):
            if key in DEFAULT_POLICY and key in file_policy:
                merged[key] = {**DEFAULT_POLICY[key], **file_policy[key]}
        return merged

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, agent_id, tool_name, parameters, *, client_idempotency_key=None):
        start = time.monotonic()
        result = self._evaluate_inner(
            agent_id,
            tool_name,
            parameters,
            client_idempotency_key=client_idempotency_key,
        )
        elapsed = time.monotonic() - start
        metrics.record_tool_call(
            status=result.get("status", "UNKNOWN"),
            tool=tool_name or "unknown",
            agent=agent_id or "unknown",
            duration=elapsed,
        )
        return result

    def _evaluate_inner(self, agent_id, tool_name, parameters, *, client_idempotency_key=None):
        if not agent_id or not isinstance(agent_id, str):
            return {
                "status": "BLOCKED",
                "reason": "MISSING_AGENT_IDENTITY",
                "message": "No authenticated agent identity provided.",
            }

        if not tool_name or not isinstance(tool_name, str):
            return {
                "status": "BLOCKED",
                "reason": "INVALID_TOOL_CALL",
                "message": "Tool name is missing or invalid.",
            }

        if not isinstance(parameters, dict):
            parameters = {}

        # --- Authorization ------------------------------------------------
        # We distinguish two cases internally (for the audit) but return a
        # generic message to the agent, so we never reveal which tools exist.
        known_tools = set(self.policy.get("executors", {}).keys()) | set(
            self.policy.get("allowed_actions", {}).get(agent_id, [])
        )
        allowed_actions = self.policy.get("allowed_actions", {})
        if tool_name not in allowed_actions.get(agent_id, []):
            reason = (
                "AGENT_NOT_AUTHORIZED_FOR_TOOL" if tool_name in known_tools else "UNSUPPORTED_TOOL"
            )
            return {
                "status": "BLOCKED",
                "reason": reason,
                # Generic message on purpose — do not leak the tool catalog.
                "message": "This tool call is not authorized.",
            }

        # --- Argument validation ------------------------------------------
        # Enforce the executor's inputSchema before policy evaluation, so a
        # malformed call (or one smuggling extra fields) never reaches the
        # sector's whitelist checks.
        executor_cfg = self.policy.get("executors", {}).get(tool_name)
        schema = executor_cfg.get("inputSchema") if isinstance(executor_cfg, dict) else None
        arg_errors = validate_arguments(schema, parameters)
        if arg_errors:
            return {
                "status": "BLOCKED",
                "reason": "INVALID_ARGUMENTS",
                "message": "The tool call arguments are invalid.",
                "errors": arg_errors,
            }

        sector = self._sector_for(tool_name)
        decision: Decision = sector.evaluate(agent_id, tool_name, parameters)

        if decision.status == DecisionStatus.APPROVED:
            # Velocity recording (before execution)
            recorder = getattr(sector, "record_approved", None)
            if callable(recorder):
                recorder(agent_id, tool_name, parameters)

            executor = self.executors.get(tool_name)
            if executor is None:
                return {
                    "status": "APPROVED",
                    "reason": decision.reason,
                    "message": decision.message,
                    **decision.metadata,
                }

            # ⚠️ GATEWAY: real execution
            idem_key = client_idempotency_key or f"direct:{uuid4().hex}"
            exec_result = self._execute(
                agent_id=agent_id,
                tool_name=tool_name,
                parameters=parameters,
                idempotency_key=idem_key,
                action_id=None,
            )

            response: dict[str, Any] = {
                "status": "APPROVED",
                "reason": decision.reason,
                "message": decision.message,
                "execution": {
                    "success": exec_result.success,
                    "idempotency_key": idem_key,
                    "status_code": exec_result.status_code,
                },
                **decision.metadata,
            }
            if exec_result.success:
                response["execution"]["result"] = exec_result.result
            else:
                response["execution"]["error"] = exec_result.error
                # Error status
                response["status"] = "EXECUTION_FAILED"
            return response

        if decision.status == DecisionStatus.BLOCKED:
            return {
                "status": "BLOCKED",
                "reason": decision.reason,
                "message": decision.message,
                **decision.metadata,
            }

        hold_result = self._hold(
            agent_id,
            tool_name,
            parameters,
            reason=decision.reason,
            message=decision.message,
            metadata=decision.metadata,
        )

        # Notify the routes listening for hold.created
        self._enqueue_notifications(
            event_type="hold.created",
            action_id=hold_result["action_id"],
            payload={
                "event_type": "hold.created",
                "action_id": hold_result["action_id"],
                "agent_id": agent_id,
                "tool_name": tool_name,
                "parameters": parameters,
                "reason": decision.reason,
                "message": decision.message,
                "created_at": utcnow_iso(),
            },
        )
        self._dispatch_notification_batch()
        return hold_result

    def _hold(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        reason: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        redacted_params = self.redactor.redact(tool_name, parameters)
        hold = self.hold_store.create(agent_id, tool_name, redacted_params, reason, message)
        return {
            "status": "HOLD",
            "action_id": hold["action_id"],
            "reason": reason,
            "message": (
                f"{message} This action is pending human approval "
                f"(action_id={hold['action_id']}). Do not retry."
            ),
            "retry": False,
            "quarantine_payload": {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "parameters": parameters,
            },
            **(metadata or {}),
        }

    # ------------------------------------------------------------------
    # Execution (Gateway)
    # ------------------------------------------------------------------

    def _execute(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        idempotency_key: str,
        action_id: str | None,
    ) -> ExecutionResult:
        """
        Execute an action via the configured executor.

        Idempotent: if a SUCCESS execution already exists for this
        idempotency_key, return the cached result.
        """
        # Cache hit
        existing = self.hold_store.get_execution(idempotency_key)
        if existing and existing["status"] == "SUCCESS":
            logger.info("Idempotent replay for key=%s → cached result", idempotency_key)
            return ExecutionResult(
                success=True,
                result=existing["result"] or {},
                status_code=existing["status_code"],
            )

        executor = self.executors.get(tool_name)
        if executor is None:
            error = f"No executor configured for tool '{tool_name}'."
            self.hold_store.record_execution_pending(
                idempotency_key,
                action_id,
                agent_id,
                tool_name,
                parameters,
            )
            self.hold_store.record_execution_failure(idempotency_key, error, None)
            return ExecutionResult(success=False, error=error)

        # Mark PENDING. A key already recorded must never execute the action
        # a second time.
        inserted = self.hold_store.record_execution_pending(
            idempotency_key,
            action_id,
            agent_id,
            tool_name,
            parameters,
        )
        if not inserted:
            existing = self.hold_store.get_execution(idempotency_key)
            if existing and existing["status"] == "SUCCESS":
                return ExecutionResult(
                    success=True,
                    result=existing["result"] or {},
                    status_code=existing["status_code"],
                )
            return ExecutionResult(
                success=False,
                error=f"Execution already in progress for key '{idempotency_key}'.",
            )

        exec_start = time.monotonic()
        try:
            result = executor.execute(
                tool_name=tool_name,
                agent_id=agent_id,
                parameters=parameters,
                idempotency_key=idempotency_key,
            )

        except Exception as e:
            elapsed = time.monotonic() - exec_start
            metrics.record_execution(tool_name, "exception", elapsed)
            logger.exception("Executor raised for tool=%s", tool_name)
            self.hold_store.record_execution_failure(
                idempotency_key,
                f"Executor raised: {e}",
                None,
            )
            return ExecutionResult(success=False, error=f"Executor raised: {e}")

        elapsed = time.monotonic() - exec_start
        metrics.record_execution(
            tool_name,
            "success" if result.success else "failure",
            elapsed,
        )
        if result.success:
            result_payload = result.result if isinstance(result.result, dict) else {}
            self.hold_store.record_execution_success(
                idempotency_key,
                result_payload,
                result.status_code,
            )
        else:
            self.hold_store.record_execution_failure(
                idempotency_key,
                result.error or "Unknown error",
                result.status_code,
            )
        return result

    # ------------------------------------------------------------------
    # HOLD management
    # ------------------------------------------------------------------

    def list_holds(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.hold_store.list(limit=limit, offset=offset)

    def list_audit_events(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.hold_store.list_audit(limit=limit, offset=offset)

    def list_executions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.hold_store.list_executions(limit=limit, offset=offset)

    def list_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.notifications.list_recent(limit=limit)

    def _enqueue_notifications(
        self,
        event_type: str,
        action_id: str,
        payload: dict[str, Any],
    ) -> None:
        """
        Enqueue a notification for every route listening to this event.
        Idempotent thanks to the dedup_key.
        """
        route_names = routes_for_event(self.policy, event_type)
        for route_name in route_names:
            dedup_key = f"{event_type}:{action_id}:{route_name}"
            self.notifications.enqueue(
                event_type=event_type,
                action_id=action_id,
                route_name=route_name,
                payload=payload,
                dedup_key=dedup_key,
            )

    def dispatch_pending_notifications(self) -> dict[str, Any]:
        """
        Try to send every PENDING notification whose retry time has
        passed.

        Idempotent, non-blocking (5s timeout per notifier).
        Returns a summary (sent / failed / dead).
        """
        sent = 0
        failed = 0
        dead = 0
        errors: list[dict[str, Any]] = []

        with self._dispatch_lock:
            for n in self.notifications.fetch_due():
                notifier = self.notifiers.get(n["route_name"])
                if notifier is None:
                    self.notifications.mark_failed(
                        n["id"], f"No notifier registered for route '{n['route_name']}'."
                    )
                    dead += 1
                    metrics.record_notification(n["route_name"], "dead")
                    continue

                try:
                    notifier.send(n["payload"])
                except Exception as e:
                    logger.warning(
                        "Notification %d (route=%s) failed: %s",
                        n["id"],
                        n["route_name"],
                        e,
                    )
                    state = self.notifications.mark_failed(n["id"], str(e))
                    errors.append(
                        {
                            "id": n["id"],
                            "route": n["route_name"],
                            "error": str(e),
                            "state": state,
                        }
                    )
                    if state.get("status") == "DEAD":
                        dead += 1
                        metrics.record_notification(n["route_name"], "dead")
                    else:
                        failed += 1
                        metrics.record_notification(n["route_name"], "failed")
                else:
                    self.notifications.mark_sent(n["id"])
                    sent += 1
                    metrics.record_notification(n["route_name"], "sent")

        return {"sent": sent, "failed": failed, "dead": dead, "errors": errors}

    def _dispatch_notification_batch(self) -> None:
        """
        Dispatch freshly-enqueued notifications in the background.

        Always active — an event enqueues a notification and it should leave
        promptly, without the caller waiting on notifier latency. (The idle
        polling loop, which is separate, can be turned off.)
        """
        worker = threading.Thread(
            target=self.dispatch_pending_notifications,
            name="pryxor-notification-fast-dispatch",
            daemon=True,
        )
        worker.start()

        # Never block the caller on network latency. The daemon worker will
        # process pending notifications in the background and the main dispatch
        # loop will wake up separately when needed.
        self._dispatch_wakeup.set()

    def approve_hold(
        self,
        action_id: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.hold_store.approve(action_id, actor_id=actor_id)
        if result.get("status") == "APPROVED":
            self.process_outbox()
            exec_row = self.hold_store.get_execution(f"hold:{action_id}")
            if exec_row:
                result["execution"] = {
                    "success": exec_row["status"] == "SUCCESS",
                    "idempotency_key": exec_row["idempotency_key"],
                    "result": exec_row["result"],
                    "error": exec_row["error"],
                    "status_code": exec_row["status_code"],
                }

            self._enqueue_notifications(
                event_type="hold.approved",
                action_id=action_id,
                payload={
                    "event_type": "hold.approved",
                    "action_id": action_id,
                    "agent_id": result.get("agent_id"),
                    "tool_name": result.get("tool_name"),
                    "parameters": self.redactor.redact(
                        result.get("tool_name"), result.get("parameters") or {}
                    ),
                    "reason": "APPROVED",
                    "message": result.get("message", ""),
                    "actor_id": actor_id,
                    "created_at": utcnow_iso(),
                },
            )
            self._dispatch_notification_batch()
        return result

    def reject_hold(
        self,
        action_id: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.hold_store.reject(action_id, actor_id=actor_id)
        if result.get("status") == "REJECTED":
            self._enqueue_notifications(
                event_type="hold.rejected",
                action_id=action_id,
                payload={
                    "event_type": "hold.rejected",
                    "action_id": action_id,
                    "agent_id": result.get("agent_id"),
                    "tool_name": result.get("tool_name"),
                    "parameters": self.redactor.redact(
                        result.get("tool_name"), result.get("parameters") or {}
                    ),
                    "reason": "REJECTED",
                    "message": result.get("message", ""),
                    "actor_id": actor_id,
                    "created_at": utcnow_iso(),
                },
            )
            self._dispatch_notification_batch()
        self.process_outbox()
        return result

    # ------------------------------------------------------------------
    # Outbox
    # ------------------------------------------------------------------

    def process_outbox(self) -> dict[str, Any]:
        processed = 0
        skipped = 0
        errors: list[dict[str, Any]] = []

        events = self.hold_store.fetch_unprocessed_outbox()

        for ev in events:
            event_id = ev["id"]
            try:
                dedup_key = f"outbox:{event_id}"
                action_id = ev["action_id"]
                params = json.loads(ev["parameters"])

                # ⚠️ Route the callback to the sector that actually produced
                # the decision for this tool — not the global default. Using
                # self.sector here would notify the wrong sector whenever the
                # tool is routed to a non-default sector (e.g. send_email →
                # email sector while the default is finance).
                sector = self._sector_for(ev["tool_name"])

                if ev["event_type"] == "hold.approved":
                    # 1) Notify the sector (idempotent)
                    sector.on_hold_approved(
                        ev["agent_id"],
                        ev["tool_name"],
                        params,
                        dedup_key=dedup_key,
                    )
                    # 2) ⚠️ GATEWAY: real execution
                    #    STABLE idempotency_key based on action_id
                    self._execute(
                        agent_id=ev["agent_id"],
                        tool_name=ev["tool_name"],
                        parameters=params,
                        idempotency_key=f"hold:{action_id}",
                        action_id=action_id,
                    )
                elif ev["event_type"] == "hold.rejected":
                    handler = getattr(sector, "on_hold_rejected", None)
                    if callable(handler):
                        handler(ev["agent_id"], ev["tool_name"], params, dedup_key=dedup_key)

                if self.hold_store.mark_outbox_processed(event_id):
                    processed += 1
                else:
                    skipped += 1

            except Exception as e:
                logger.exception("Outbox event %d failed", event_id)
                errors.append(
                    {
                        "event_id": event_id,
                        "event_type": ev["event_type"],
                        "error": str(e),
                    }
                )

        return {"processed": processed, "skipped": skipped, "errors": errors}

    def expire_stale_holds(self) -> int:
        return self.hold_store.expire_stale_holds()

    # ------------------------------------------------------------------
    # Async notification dispatch
    # ------------------------------------------------------------------

    def _refresh_gauges(self) -> None:
        """Refresh the gauges exposed on /metrics."""
        try:
            holds = self.hold_store.list()
            pending_holds = sum(1 for h in holds if h.get("status") == "PENDING")
            metrics.set_holds_pending(pending_holds)

            outbox = self.hold_store.list_pending_outbox()
            metrics.set_outbox_pending(len(outbox))

            notifs = self.notifications.list_recent(limit=500)
            pending_notifs = sum(1 for n in notifs if n.get("status") == "PENDING")
            metrics.set_notifications_pending(pending_notifs)
        except Exception:
            logger.debug("Failed to refresh gauges.", exc_info=True)
            # get_request_id() retournera "system" hors contexte HTTP
            logger.debug("Gauges refreshed | request_id=%s", get_request_id())

    def _start_dispatch_worker(self) -> None:
        """
        Start a daemon worker that dispatches PENDING notifications every
        5 seconds, without blocking the agent's request.
        """
        import threading

        def _loop():
            logger.info("Notification dispatch worker started (interval=%.1fs).", interval)
            while not self._dispatch_stop_event.is_set():
                self._dispatch_wakeup.wait(timeout=interval)
                self._dispatch_wakeup.clear()
                if self._dispatch_stop_event.is_set():
                    break
                try:
                    self.dispatch_pending_notifications()
                    # Refresh gauges
                    self._refresh_gauges()
                except Exception:
                    logger.exception("Notification dispatch iteration failed.")

        interval = 1.0
        self._dispatch_thread = threading.Thread(
            target=_loop,
            name="pryxor-notification-dispatch",
            daemon=True,
        )
        self._dispatch_thread.start()

    def stop_dispatch_worker(self) -> None:
        """Stop the worker cleanly (useful for tests)."""
        self._dispatch_stop_event.set()
        self._dispatch_wakeup.set()
        if self._dispatch_thread:
            self._dispatch_thread.join(timeout=0.2)
