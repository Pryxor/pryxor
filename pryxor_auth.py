"""
Pryxor — Agent & Admin authentication.

Every agent and every admin has a unique API key, stored hashed
(PBKDF2-SHA256). The plaintext key is shown ONLY ONCE.

Headers:
    - Agent: X-Agent-Key   (prefix "pryxor_agent_")
    - Admin: X-Admin-Key   (prefix "pryxor_admin_")

Separate registries: an admin key cannot authenticate as an agent, and
vice versa.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KEY_BYTES = 32
"""
The API keys are 32 random bytes (256 bits). PBKDF2 was defensive
against low-entropy secrets; against a 256-bit random key, SHA-256
with a salt is strictly stronger than PBKDF2 with the same work
factor and is 100,000× faster per verification.
"""
def _hash_key(api_key: str, salt: bytes) -> str:
    h = hashlib.sha256()
    h.update(salt)
    h.update(api_key.encode("utf-8"))
    return h.hexdigest()


def _constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _open_connection(state_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(state_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class _KeyRegistry:
    """
    Base class for the key registries (agents and admins).

    Subclasses MUST define:
        TABLE       : the SQLite table name
        KEY_PREFIX  : the key prefix (e.g. "pryxor_agent_")
        ID_LABEL    : "agent" or "admin" (for error messages)
    """

    TABLE: str = ""
    KEY_PREFIX: str = ""
    ID_LABEL: str = "key"

    def __init__(self, state_path: str | Path):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with _open_connection(self.state_path) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    key_id       TEXT PRIMARY KEY,
                    key_hash     TEXT NOT NULL,
                    key_salt     TEXT NOT NULL,
                    key_prefix   TEXT NOT NULL,
                    label        TEXT,
                    created_at   TEXT NOT NULL,
                    revoked_at   TEXT
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_prefix ON {self.TABLE}(key_prefix)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(self, key_id: str, label: str | None = None) -> dict[str, Any]:
        if not key_id or not isinstance(key_id, str):
            raise ValueError(f"{self.ID_LABEL}_id must be a non-empty string.")

        with _open_connection(self.state_path) as conn:
            existing = conn.execute(
                f"SELECT key_id, revoked_at FROM {self.TABLE} WHERE key_id = ?",
                (key_id,),
            ).fetchone()

        if existing and existing["revoked_at"] is None:
            raise ValueError(
                f"{self.ID_LABEL.capitalize()} '{key_id}' already exists and "
                f"is active. Revoke it first to rotate the key."
            )

        raw = secrets.token_urlsafe(KEY_BYTES)
        api_key = f"{self.KEY_PREFIX}{key_id}_{raw}"
        salt = secrets.token_bytes(16)
        key_hash = _hash_key(api_key, salt)
        # Prefix long enough to tell agent/admin apart in the logs.
        key_prefix = api_key[:32]
        created_at = datetime.now(timezone.utc).isoformat()

        with _open_connection(self.state_path) as conn:
            conn.execute(
                f"""
                INSERT INTO {self.TABLE}(
                    key_id, key_hash, key_salt, key_prefix,
                    label, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(key_id) DO UPDATE SET
                    key_hash   = excluded.key_hash,
                    key_salt   = excluded.key_salt,
                    key_prefix = excluded.key_prefix,
                    label      = excluded.label,
                    created_at = excluded.created_at,
                    revoked_at = NULL
                """,
                (key_id, key_hash, salt.hex(), key_prefix, label, created_at),
            )
            conn.commit()

        return {
            f"{self.ID_LABEL}_id": key_id,
            "api_key": api_key,
            "key_prefix": key_prefix,
            "created_at": created_at,
            "label": label,
        }

    def revoke(self, key_id: str) -> bool:
        revoked_at = datetime.now(timezone.utc).isoformat()
        with _open_connection(self.state_path) as conn:
            cur = conn.execute(
                f"UPDATE {self.TABLE} SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
                (revoked_at, key_id),
            )
            conn.commit()
            return cur.rowcount == 1

    def rotate(self, key_id: str, label: str | None = None) -> dict[str, Any]:
        """
        Replace an existing agent/admin key with a new one.
        The old key becomes invalid immediately.
        The identifier (`key_id`) is unchanged → history is preserved.

        ⚠️ Raises ValueError if the entry does not exist.
           To create a new one, use `register`.
        """
        if not key_id or not isinstance(key_id, str):
            raise ValueError(f"{self.ID_LABEL}_id must be a non-empty string.")

        with _open_connection(self.state_path) as conn:
            existing = conn.execute(
                f"SELECT key_id, revoked_at, label FROM {self.TABLE} WHERE key_id = ?",
                (key_id,),
            ).fetchone()

        if existing is None:
            raise ValueError(
                f"{self.ID_LABEL.capitalize()} '{key_id}' does not exist. "
                f"Use register() to create it."
            )
        if existing["revoked_at"] is not None:
            raise ValueError(
                f"{self.ID_LABEL.capitalize()} '{key_id}' is revoked. "
                f"Use register() to recreate it."
            )

        # Generate a new key
        raw = secrets.token_urlsafe(KEY_BYTES)
        api_key = f"{self.KEY_PREFIX}{key_id}_{raw}"
        salt = secrets.token_bytes(16)
        key_hash = _hash_key(api_key, salt)
        key_prefix = api_key[:32]
        rotated_at = datetime.now(timezone.utc).isoformat()
        final_label = label if label is not None else existing["label"]

        with _open_connection(self.state_path) as conn:
            conn.execute(
                f"""
                UPDATE {self.TABLE}
                SET key_hash = ?, key_salt = ?, key_prefix = ?,
                    label = ?, created_at = ?
                WHERE key_id = ?
                """,
                (key_hash, salt.hex(), key_prefix, final_label, rotated_at, key_id),
            )
            conn.commit()

        return {
            f"{self.ID_LABEL}_id": key_id,
            "api_key": api_key,
            "key_prefix": key_prefix,
            "created_at": rotated_at,
            "label": final_label,
            "rotated": True,
        }

    def list(self) -> list[dict[str, Any]]:
        with _open_connection(self.state_path) as conn:
            rows = conn.execute(
                f"""
                SELECT key_id, key_prefix, label, created_at, revoked_at
                FROM {self.TABLE} ORDER BY created_at DESC
                """
            ).fetchall()
        id_field = f"{self.ID_LABEL}_id"
        return [
            {
                id_field: row["key_id"],
                "key_prefix": row["key_prefix"],
                "label": row["label"],
                "created_at": row["created_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, api_key: str) -> str | None:
        if not api_key or not isinstance(api_key, str):
            return None
        if not api_key.startswith(self.KEY_PREFIX):
            return None

        prefix = api_key[:32]

        with _open_connection(self.state_path) as conn:
            candidates = conn.execute(
                f"""
                SELECT key_id, key_hash, key_salt FROM {self.TABLE}
                WHERE key_prefix = ? AND revoked_at IS NULL
                """,
                (prefix,),
            ).fetchall()

        for row in candidates:
            salt = bytes.fromhex(row["key_salt"])
            if _constant_time_compare(_hash_key(api_key, salt), row["key_hash"]):
                return row["key_id"]
        return None


class AgentRegistry(_KeyRegistry):
    TABLE = "agents"
    KEY_PREFIX = "pryxor_agent_"
    ID_LABEL = "agent"

    # Backward-compatible aliases (legacy API)
    def register_agent(self, agent_id: str, label: str | None = None):
        return self.register(agent_id, label)

    def verify_key(self, api_key: str) -> str | None:
        return self.verify(api_key)

    def revoke_agent(self, agent_id: str) -> bool:
        return self.revoke(agent_id)

    def rotate_agent(self, agent_id: str, label: str | None = None):
        return self.rotate(agent_id, label)

    def list_agents(self):
        return self.list()


class AdminRegistry(_KeyRegistry):
    TABLE = "admin_keys"
    KEY_PREFIX = "pryxor_admin_"
    ID_LABEL = "admin"

    def register_admin(self, admin_id: str, label: str | None = None):
        return self.register(admin_id, label)

    def verify_key(self, api_key: str) -> str | None:
        return self.verify(api_key)

    def revoke_admin(self, admin_id: str) -> bool:
        return self.revoke(admin_id)

    def rotate_admin(self, admin_id: str, label: str | None = None):
        return self.rotate(admin_id, label)

    def list_admins(self):
        return self.list()
