# NOTE: This is the canonical Pryxor client, shipped in the `pryxor` package
# (import with `from pryxor import Pryxor`).
"""
Pryxor client — minimal SDK for AI agents.

Copy this file into your agent's project. The only dependency is
`requests` (available in virtually every Python environment).

Usage:
    from pryxor import (
        Pryxor, PryxorBlockedError, PryxorHoldPendingError, PryxorError,
    )

    pryxor = Pryxor()  # reads PRYXOR_AGENT_KEY and PRYXOR_URL from env

    try:
        result = pryxor.execute(
            "send_payment",
            {"amount": 100.0, "recipient": "Fournisseur_A"},
        )
        print("Executed:", result)   # the real API payload
    except PryxorHoldPendingError as e:
        print(f"Waiting for human approval: {e.action_id}")
    except PryxorBlockedError as e:
        print(f"Blocked by policy: {e.reason}")
    except PryxorError as e:
        print(f"Pryxor error: {e}")

Philosophy:
    - `execute()` raises on every non-success outcome. Clean control flow.
    - `execute_raw()` returns the full Pryxor response, never raises on
      business decisions (BLOCKED / HOLD). Useful when you want to
      branch yourself.
"""

from __future__ import annotations

import os
from typing import Any

import requests

__all__ = [
    "Pryxor",
    "PryxorError",
    "PryxorAuthError",
    "PryxorBlockedError",
    "PryxorHoldPendingError",
    "PryxorExecutionError",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PryxorError(Exception):
    """Base exception for every Pryxor client error."""


class PryxorAuthError(PryxorError):
    """HTTP 401 — missing, invalid, or revoked agent key."""


class PryxorBlockedError(PryxorError):
    """Policy engine returned BLOCKED."""

    def __init__(self, reason: str, message: str):
        super().__init__(f"Blocked ({reason}): {message}")
        self.reason = reason
        self.message = message


class PryxorHoldPendingError(PryxorError):
    """Policy engine returned HOLD — a human must approve."""

    def __init__(self, action_id: str, reason: str, message: str):
        super().__init__(f"HOLD ({reason}): {message} — action_id={action_id}")
        self.action_id = action_id
        self.reason = reason
        self.message = message


class PryxorExecutionError(PryxorError):
    """The action was approved but the executor failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(f"Execution failed: {message}")
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class Pryxor:
    """Minimal client for the Pryxor runtime-security gateway."""

    def __init__(
        self,
        agent_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ):
        self.agent_key = agent_key or os.environ.get("PRYXOR_AGENT_KEY")
        if not self.agent_key:
            raise PryxorError(
                "PRYXOR_AGENT_KEY is not set and no agent_key was provided. "
                "Register an agent with `admin_cli.py register <id>` and "
                "export the returned key."
            )

        self.base_url = (base_url or os.environ.get("PRYXOR_URL") or "http://pryxor:8000").rstrip(
            "/"
        )
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute a tool through Pryxor.

        Returns:
            The real API payload (the value of `execution.result`).

        Raises:
            PryxorAuthError         — invalid agent key (401)
            PryxorBlockedError      — policy says no
            PryxorHoldPendingError  — waiting for human approval
            PryxorExecutionError    — approved but external call failed
            PryxorError             — anything else (network, malformed...)
        """
        raw = self.execute_raw(tool_name, parameters, idempotency_key=idempotency_key)
        status = raw.get("status")

        if status == "APPROVED":
            exec_block = raw.get("execution") or {}
            if exec_block.get("success") is True:
                return exec_block.get("result") or {}
            # Approved but the executor itself failed → EXECUTION_FAILED
            raise PryxorExecutionError(
                exec_block.get("error") or "Unknown execution error",
                status_code=exec_block.get("status_code"),
            )

        if status == "BLOCKED":
            raise PryxorBlockedError(
                raw.get("reason", "UNKNOWN"),
                raw.get("message", ""),
            )

        if status == "HOLD":
            raise PryxorHoldPendingError(
                raw.get("action_id", ""),
                raw.get("reason", "UNKNOWN"),
                raw.get("message", ""),
            )

        if status == "EXECUTION_FAILED":
            exec_block = raw.get("execution") or {}
            raise PryxorExecutionError(
                exec_block.get("error") or raw.get("message", "Unknown"),
                status_code=exec_block.get("status_code"),
            )

        raise PryxorError(f"Unexpected Pryxor status '{status}': {raw}")

    def execute_raw(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Low-level call. Returns the raw Pryxor response dict and never
        raises on business decisions (BLOCKED / HOLD).
        """
        headers = {
            "X-Agent-Key": self.agent_key,
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        url = f"{self.base_url}/v1/execute-tool"
        payload = {"tool_name": tool_name, "parameters": parameters or {}}

        try:
            r = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise PryxorError(f"Pryxor unreachable at {self.base_url}: {e}") from e

        if r.status_code == 401:
            raise PryxorAuthError("Invalid or revoked agent key.")
        if r.status_code >= 500:
            raise PryxorError(f"Pryxor server error {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise PryxorError(f"Pryxor client error {r.status_code}: {r.text[:200]}")

        try:
            return r.json()
        except ValueError as e:
            raise PryxorError(f"Invalid JSON from Pryxor: {r.text[:200]}") from e

    def list_holds(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self._get(f"/v1/holds?limit={limit}&offset={offset}", admin=True)

    def list_audit(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self._get(f"/v1/audit?limit={limit}&offset={offset}", admin=True)

    # ------------------------------------------------------------------
    # Convenience helpers (static so callers can use them without a client)
    # ------------------------------------------------------------------

    @staticmethod
    def is_approved(raw: dict[str, Any]) -> bool:
        return (
            raw.get("status") == "APPROVED" and (raw.get("execution") or {}).get("success") is True
        )

    @staticmethod
    def is_blocked(raw: dict[str, Any]) -> bool:
        return raw.get("status") == "BLOCKED"

    @staticmethod
    def is_hold(raw: dict[str, Any]) -> bool:
        return raw.get("status") == "HOLD"
