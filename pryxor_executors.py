"""
Pryxor — ToolExecutors.

An executor knows how to perform a real action against an external API.
It holds the knowledge of:
    - the URL
    - the HTTP method
    - the body schema
    - the auth secret

YAML/JSON configuration (in the `executors` field of the policy):

    "executors": {
      "send_payment": {
        "type": "http",
        "method": "POST",
        "url": "https://bank.example.com/v1/payments",
        "timeout_seconds": 10,
        "auth": {"type": "bearer", "secret_ref": "BANK_API_TOKEN"},
        "headers": {"X-Trace-Id": "{{ idempotency_key }}"},
        "body": {
          "amount": "{{ parameters.amount }}",
          "recipient": "{{ parameters.recipient }}",
          "idempotency_key": "{{ idempotency_key }}"
        },
        "retry": {"attempts": 0}
      }
    }
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from pryxor_secrets import SecretProvider

logger = logging.getLogger("pryxor.executors")


# ----------------------------------------------------------------------
# Execution result
# ----------------------------------------------------------------------


@dataclass
class ExecutionResult:
    success: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    status_code: int | None = None


# ----------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------


class ToolExecutor(ABC):
    @abstractmethod
    def execute(
        self,
        tool_name: str,
        agent_id: str,
        parameters: dict[str, Any],
        idempotency_key: str,
    ) -> ExecutionResult:
        """Perform the real action. Never raises: returns an ExecutionResult."""


# ----------------------------------------------------------------------
# Mock (useful for tests and demos)
# ----------------------------------------------------------------------


class MockExecutor(ToolExecutor):
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def execute(self, tool_name, agent_id, parameters, idempotency_key):
        return ExecutionResult(
            success=True,
            status_code=200,
            result={
                "mock": True,
                "tool_name": tool_name,
                "agent_id": agent_id,
                "idempotency_key": idempotency_key,
                "parameters": parameters,
            },
        )


# ----------------------------------------------------------------------
# HTTP executor
# ----------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _resolve_path(context: dict[str, Any], path: str) -> Any:
    """Resolve 'parameters.amount' within {'parameters': {'amount': 42}}."""
    value: Any = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _render_string(template: str, context: dict[str, Any]) -> str:
    """Replace {{ path }} inside a string."""

    def repl(match: re.Match) -> str:
        value = _resolve_path(context, match.group(1))
        if value is None:
            return ""
        return str(value)

    return _TEMPLATE_RE.sub(repl, template)


def _render(value: Any, context: dict[str, Any]) -> Any:
    """Recursively render an object (dict/list/str)."""
    if isinstance(value, str):
        return _render_string(value, context)
    if isinstance(value, dict):
        return {k: _render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, context) for v in value]
    return value


class HttpExecutor(ToolExecutor):
    def __init__(self, config: dict[str, Any], secrets: SecretProvider):
        self.config = config
        self.secrets = secrets
        self.method = config.get("method", "POST").upper()
        self.url = config.get("url")
        if not self.url:
            raise ValueError("HttpExecutor requires 'url' in config.")
        self.timeout = float(config.get("timeout_seconds", 10.0))
        self.static_headers = config.get("headers", {})
        self.body_template = config.get("body", {})
        self.auth = config.get("auth", {}) or {}
        self.retry_config = config.get("retry", {}) or {}

    def execute(self, tool_name, agent_id, parameters, idempotency_key):
        context = {
            "parameters": parameters,
            "agent_id": agent_id,
            "idempotency_key": idempotency_key,
            "tool_name": tool_name,
        }

        url = self.url
        headers = {k: _render_string(v, context) for k, v in self.static_headers.items()}
        headers["Content-Type"] = headers.get("Content-Type", "application/json")
        headers["Idempotency-Key"] = idempotency_key

        # Auth
        try:
            auth_header = self._build_auth_header()
        except Exception as e:
            return ExecutionResult(success=False, error=f"Auth setup failed: {e}")
        if auth_header:
            headers.update(auth_header)

        body = _render(self.body_template, context)

        attempts = int(self.retry_config.get("attempts", 0)) + 1
        backoff = float(self.retry_config.get("backoff_seconds", 0.5))
        last_error: str | None = None
        last_status: int | None = None

        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        self.method,
                        url,
                        headers=headers,
                        json=body if body else None,
                    )
                last_status = response.status_code

                if 200 <= response.status_code < 300:
                    try:
                        data = response.json()
                    except Exception:
                        data = {"raw": response.text[:1000]}
                    return ExecutionResult(
                        success=True,
                        result=data,
                        status_code=response.status_code,
                    )

                last_error = f"HTTP {response.status_code}: {response.text[:300]}"

                # Retry only the 5xx and 429
                if response.status_code < 500 and response.status_code != 429:
                    break

            except httpx.TimeoutException:
                last_error = f"Timeout after {self.timeout}s"
            except httpx.RequestError as e:
                last_error = f"Network error: {e}"
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                break

            if attempt < attempts:
                time.sleep(backoff * attempt)

        return ExecutionResult(
            success=False,
            error=last_error,
            status_code=last_status,
        )

    def _build_auth_header(self) -> dict[str, str]:
        auth_type = self.auth.get("type")
        if not auth_type or auth_type == "none":
            return {}

        secret_ref = self.auth.get("secret_ref")
        if not secret_ref:
            raise ValueError("auth.secret_ref is required.")

        secret = self.secrets.require(secret_ref)

        if auth_type == "bearer":
            return {"Authorization": f"Bearer {secret}"}
        if auth_type == "basic":
            # secret format: "user:password"
            import base64

            encoded = base64.b64encode(secret.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        if auth_type == "header":
            header_name = self.auth.get("header_name", "X-API-Key")
            return {header_name: secret}

        raise ValueError(f"Unknown auth type: '{auth_type}'.")


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


def build_executor_registry(
    policy: dict[str, Any],
    secrets: SecretProvider,
) -> dict[str, ToolExecutor]:
    """
    Build the tool_name → ToolExecutor map from the policy.

    Each entry of `policy["executors"]`:
        {
          "type": "http" | "mock",
          ... type-specific config ...
        }
    """
    executors: dict[str, ToolExecutor] = {}
    config = policy.get("executors", {}) or {}

    for tool_name, exec_config in config.items():
        if not isinstance(exec_config, dict):
            logger.warning("Skipping executor '%s': config must be a dict.", tool_name)
            continue

        exec_type = exec_config.get("type", "mock")

        try:
            if exec_type == "http":
                executors[tool_name] = HttpExecutor(exec_config, secrets)
            elif exec_type == "mock":
                executors[tool_name] = MockExecutor(exec_config)
            else:
                logger.warning(
                    "Unknown executor type '%s' for tool '%s'.",
                    exec_type,
                    tool_name,
                )
                continue
        except Exception as e:
            logger.error("Failed to build executor '%s': %s", tool_name, e)

    return executors
