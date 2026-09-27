"""
Pryxor — Notification layer.

Sends Slack/Teams/webhook notifications for the events
hold.created / hold.approved / hold.rejected / hold.expired.

⚠️ Reliability: notifications are persisted to SQLite before sending. If
    sending fails, they are retried with exponential backoff up to a
    maximum. When the engine starts, pending notifications are replayed.

Configuration (configs/notifications.json):

    "notifications": {
      "routes": {
        "slack_ops": {
          "type": "slack",
          "webhook_url_ref": "SLACK_OPS_WEBHOOK",
          "events": ["hold.created", "hold.expired"]
        },
        "soc_audit": {
          "type": "webhook",
          "url": "https://soc.example.com/pryxor",
          "auth": {"type": "bearer", "secret_ref": "SOC_TOKEN"},
          "events": ["hold.created", "hold.approved", "hold.rejected"]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from pryxor_secrets import SecretProvider

logger = logging.getLogger("pryxor.notifiers")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _open_connection(state_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(state_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Notifier abstraction
# ---------------------------------------------------------------------------


class Notifier(ABC):
    """Envoie une notification vers une destination externe."""

    def __init__(self, config: dict[str, Any], secrets: SecretProvider):
        self.config = config
        self.secrets = secrets

    @abstractmethod
    def send(self, payload: dict[str, Any]) -> None:
        """
        Send the notification. Raises on failure.
        Success = HTTP 2xx.
        """


class SlackNotifier(Notifier):
    """Send through a Slack Incoming Webhook."""

    def send(self, payload: dict[str, Any]) -> None:
        url_ref = self.config.get("webhook_url_ref")
        if not url_ref:
            raise ValueError("SlackNotifier requires 'webhook_url_ref'.")
        url = self.secrets.require(url_ref)

        body = self._format(payload)
        r = requests.post(url, json=body, timeout=5.0)
        if not (200 <= r.status_code < 300):
            raise RuntimeError(f"Slack returned {r.status_code}: {r.text[:200]}")

    def _format(self, p: dict[str, Any]) -> dict[str, Any]:
        event_type = p.get("event_type", "unknown")
        emoji, title = {
            "hold.created": ("⏸", "Pryxor HOLD — action requires approval"),
            "hold.approved": ("✅", "Pryxor HOLD — approved"),
            "hold.rejected": ("🚫", "Pryxor HOLD — rejected"),
            "hold.expired": ("⌛", "Pryxor HOLD — expired"),
        }.get(event_type, ("ℹ️", f"Pryxor event — {event_type}"))

        lines = [
            f"*{title}*",
            f"• *Agent:* `{p.get('agent_id', '?')}`",
            f"• *Tool:* `{p.get('tool_name', '?')}`",
            f"• *Reason:* `{p.get('reason', '?')}`",
        ]
        mention = self.config.get("mention")
        if mention:
            lines.insert(1, mention)
        params = p.get("parameters") or {}
        if params:
            preview = ", ".join(f"{k}={v}" for k, v in list(params.items())[:5])
            lines.append(f"• *Parameters:* `{preview}`")
        if p.get("action_id"):
            lines.append(f"• *Action ID:* `{p['action_id']}`")
        if p.get("message"):
            lines.append(f"\n_{p['message']}_")

        return {"text": "\n".join(lines)}


class TeamsNotifier(Notifier):
    """Send through a Microsoft Teams Incoming Webhook (MessageCard)."""

    def send(self, payload: dict[str, Any]) -> None:
        url_ref = self.config.get("webhook_url_ref")
        if not url_ref:
            raise ValueError("TeamsNotifier requires 'webhook_url_ref'.")
        url = self.secrets.require(url_ref)

        body = self._format(payload)
        r = requests.post(url, json=body, timeout=5.0)
        if not (200 <= r.status_code < 300):
            raise RuntimeError(f"Teams returned {r.status_code}: {r.text[:200]}")

    def _format(self, p: dict[str, Any]) -> dict[str, Any]:
        event_type = p.get("event_type", "unknown")
        theme, title = {
            "hold.created": ("FFA500", "Pryxor HOLD — action requires approval"),
            "hold.approved": ("36A64F", "Pryxor HOLD — approved"),
            "hold.rejected": ("D93F3F", "Pryxor HOLD — rejected"),
            "hold.expired": ("808080", "Pryxor HOLD — expired"),
        }.get(event_type, ("0078D4", f"Pryxor event — {event_type}"))

        mention = self.config.get("mention")
        if mention:
            title = f"{mention} — {title}"

        facts = [
            {"name": "Agent", "value": p.get("agent_id", "?")},
            {"name": "Tool", "value": p.get("tool_name", "?")},
            {"name": "Reason", "value": p.get("reason", "?")},
            {"name": "Action ID", "value": p.get("action_id", "?")},
        ]
        if p.get("message"):
            facts.append({"name": "Message", "value": p["message"]})

        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": title,
            "themeColor": theme,
            "title": title,
            "sections": [{"facts": facts}],
        }


class WebhookNotifier(Notifier):
    """Raw JSON POST to an arbitrary URL (SOC, PagerDuty, custom)."""

    def send(self, payload: dict[str, Any]) -> None:
        url = self.config.get("url")
        if not url:
            raise ValueError("WebhookNotifier requires 'url'.")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Pryxor-Notifier/1.0",
        }
        for k, v in (self.config.get("headers") or {}).items():
            headers[k] = str(v)

        auth = self.config.get("auth") or {}
        auth_type = auth.get("type")
        if auth_type == "bearer":
            secret = self.secrets.require(auth["secret_ref"])
            headers["Authorization"] = f"Bearer {secret}"
        elif auth_type == "header":
            header_name = auth.get("header_name", "X-API-Key")
            secret = self.secrets.require(auth["secret_ref"])
            headers[header_name] = secret
        elif auth_type and auth_type != "none":
            raise ValueError(f"Unknown auth type '{auth_type}'.")

        r = requests.post(url, json=payload, headers=headers, timeout=5.0)
        if not (200 <= r.status_code < 300):
            raise RuntimeError(f"Webhook returned {r.status_code}: {r.text[:200]}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def build_notifier_registry(
    policy: dict[str, Any],
    secrets: SecretProvider,
) -> dict[str, Notifier]:
    """
    Build the route_name -> Notifier map from the config.

    routes = policy["notifications"]["routes"]
    """
    notifications_cfg = policy.get("notifications") or {}
    routes_cfg = notifications_cfg.get("routes") or {}
    registry: dict[str, Notifier] = {}

    for route_name, route_cfg in routes_cfg.items():
        if not isinstance(route_cfg, dict):
            logger.warning("Skipping route '%s': config must be a dict.", route_name)
            continue

        kind = route_cfg.get("type")
        try:
            if kind == "slack":
                registry[route_name] = SlackNotifier(route_cfg, secrets)
            elif kind == "teams":
                registry[route_name] = TeamsNotifier(route_cfg, secrets)
            elif kind == "webhook":
                registry[route_name] = WebhookNotifier(route_cfg, secrets)
            else:
                logger.warning(
                    "Skipping route '%s': unknown type '%s'.",
                    route_name,
                    kind,
                )
        except Exception as e:
            logger.error("Failed to build notifier '%s': %s", route_name, e)

    return registry


def routes_for_event(policy: dict[str, Any], event_type: str) -> list[str]:
    """List the routes listening to this event_type."""
    routes_cfg = (policy.get("notifications") or {}).get("routes") or {}
    return [
        name
        for name, cfg in routes_cfg.items()
        if isinstance(cfg, dict) and event_type in (cfg.get("events") or [])
    ]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class NotificationStore:
    """
    Notification persistence.

    Statuses:
        PENDING  — waiting to be sent, retry allowed
        SENT     — sent successfully
        DEAD     — max attempts reached, no more retries
    """

    def __init__(
        self,
        state_path: Path,
        max_attempts: int = 3,
        backoff_base_seconds: int = 30,
    ):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return _open_connection(self.state_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type      TEXT NOT NULL,
                    action_id       TEXT NOT NULL,
                    route_name      TEXT NOT NULL,
                    payload         TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    attempts        INTEGER NOT NULL DEFAULT 0,
                    max_attempts    INTEGER NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    last_error      TEXT,
                    created_at      TEXT NOT NULL,
                    sent_at         TEXT,
                    dedup_key       TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notif_status_due "
                "ON notifications(status, next_attempt_at)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(
        self,
        event_type: str,
        action_id: str,
        route_name: str,
        payload: dict[str, Any],
        dedup_key: str,
    ) -> bool:
        """Return True if a new row was inserted, False if it already existed."""
        now = _utcnow_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO notifications(
                    event_type, action_id, route_name, payload, status,
                    attempts, max_attempts, next_attempt_at, created_at, dedup_key
                ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    action_id,
                    route_name,
                    json.dumps(payload, ensure_ascii=False),
                    self.max_attempts,
                    now,
                    now,
                    dedup_key,
                ),
            )
            conn.commit()
            return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def fetch_due(self, limit: int = 50) -> list[dict[str, Any]]:
        """PENDING notifications whose next_attempt_at has passed."""
        now = _utcnow_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM notifications
                WHERE status = 'PENDING' AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "action_id": row["action_id"],
            "route_name": row["route_name"],
            "payload": json.loads(row["payload"]),
            "status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "next_attempt_at": row["next_attempt_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "sent_at": row["sent_at"],
            "dedup_key": row["dedup_key"],
        }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_sent(self, notif_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE notifications
                SET status = 'SENT', sent_at = ?, last_error = NULL
                WHERE id = ? AND status = 'PENDING'
                """,
                (_utcnow_iso(), notif_id),
            )
            conn.commit()
            return cur.rowcount == 1

    def mark_failed(self, notif_id: int, error: str) -> dict[str, Any]:
        """
        Increment attempts. If max_attempts is reached, switch to DEAD.
        Otherwise, schedule a retry with exponential backoff.
        """
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts FROM notifications WHERE id = ?",
                (notif_id,),
            ).fetchone()
            if not row:
                return {"status": "NOT_FOUND"}

            attempts = row["attempts"] + 1
            max_attempts = row["max_attempts"]

            if attempts >= max_attempts:
                new_status = "DEAD"
                next_attempt = now.isoformat()  # pas de retry
            else:
                new_status = "PENDING"
                delay = self.backoff_base_seconds * (2 ** (attempts - 1))
                next_attempt = (now + timedelta(seconds=delay)).isoformat()

            conn.execute(
                """
                UPDATE notifications
                SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?
                WHERE id = ?
                """,
                (new_status, attempts, next_attempt, error, notif_id),
            )
            conn.commit()

        return {"status": new_status, "attempts": attempts}
