"""
Pryxor — PII / secret redaction.
Two modes:
    REDACTED → replaces the value with "[REDACTED]"
    HASH     → replaces it with "sha256:<hex>" (lets you correlate without revealing)

Configuration (configs/pryxor.json):

    "redaction": {
      "enabled": true,
      "default_mode": "REDACTED",
      "default_patterns": ["password", "card_number", "iban", "api_key", "bearer"],
      "tools": {
        "send_email": {
          "fields": {"body": "HASH"}
        }
      }
    }

⚠️ Redaction applies AT STORAGE TIME (holds, audit, executions,
    sector_events). In-memory values and execution results are NOT
    modified — Pryxor keeps doing its job with the real values.
"""

from __future__ import annotations

import hashlib
import logging
import re
from enum import Enum
from typing import Any

logger = logging.getLogger("pryxor.redaction")


class RedactionMode(str, Enum):
    REDACTED = "REDACTED"
    HASH = "HASH"
    NONE = "NONE"


# ---------------------------------------------------------------------------
# Fields whose NAME alone makes the value sensitive (regardless of content)
# ---------------------------------------------------------------------------

SENSITIVE_FIELD_NAMES: set[str] = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "api_key",
    "apikey",
    "api_token",
    "private_key",
    "privatekey",
    "credit_card",
    "card_number",
    "cardnumber",
    "cvv",
    "cvc",
    "ssn",
    "social_security",
    "iban",
    "bic",
    "swift",
    "authorization",
}


# ---------------------------------------------------------------------------
# Default patterns (regex)
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS: dict[str, re.Pattern] = {
    # "password=xxx", "pwd: xxx", "secret='xxx'"
    "password": re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{3,})"),
    # sk_live_xxx, pk_test_yyy, api_zzz...
    "api_key": re.compile(r"\b(sk|pk|api|key|token)[_-]?(live|test|prod)?[_-]?[A-Za-z0-9]{16,}\b"),
    # "Bearer eyJ..."
    "bearer": re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    # 13-19 digits (with or without spaces/dashes)
    "card_number": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    # Simple IBAN
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    # AWS keys
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Generic JWT
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_value(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _apply_mode(value: Any, mode: RedactionMode) -> Any:
    if mode == RedactionMode.NONE:
        return value
    if mode == RedactionMode.REDACTED:
        return "[REDACTED]"
    if mode == RedactionMode.HASH:
        if isinstance(value, str):
            return _hash_value(value)
        return "[REDACTED]"  # not hashable → fallback
    return value


def _redact_by_patterns(text: str, patterns: list[re.Pattern]) -> str:
    """Replace every match with [REDACTED]."""
    result = text
    for pat in patterns:
        result = pat.sub("[REDACTED]", result)
    return result


# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------


class Redactor:
    """
    Apply redaction to a parameters dict.

    Thread-safe (immutable after init).
    """

    def __init__(self, config: dict[str, Any] | None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        self.default_mode = RedactionMode(str(config.get("default_mode", "REDACTED")).upper())

        # Patterns enabled by default
        pattern_names = config.get("default_patterns") or [
            "password",
            "api_key",
            "bearer",
            "card_number",
            "iban",
            "aws_key",
            "jwt",
        ]
        self.patterns: list[re.Pattern] = [
            DEFAULT_PATTERNS[name] for name in pattern_names if name in DEFAULT_PATTERNS
        ]

        # Config per tool
        self.tools_config: dict[str, dict[str, Any]] = config.get("tools") or {}

    def redact(
        self,
        tool_name: str | None,
        parameters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Return a NEW dict with sensitive values redacted.
        Does not mutate the original.
        """
        if not self.enabled or not parameters:
            return dict(parameters) if parameters else {}

        result = dict(parameters)
        tool_cfg = (self.tools_config.get(tool_name) or {}) if tool_name else {}
        field_modes: dict[str, RedactionMode] = {}

        for field, mode_str in (tool_cfg.get("fields") or {}).items():
            try:
                field_modes[field] = RedactionMode(str(mode_str).upper())
            except ValueError:
                logger.warning("Unknown redaction mode '%s' for field '%s'", mode_str, field)

        return self._redact_dict(result, field_modes, path="")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _redact_dict(
        self,
        data: dict[str, Any],
        field_modes: dict[str, RedactionMode],
        path: str,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in data.items():
            full_key = f"{path}.{key}" if path else key
            out[key] = self._redact_value(key, value, field_modes, full_key)
        return out

    def _redact_value(
        self,
        key: str,
        value: Any,
        field_modes: dict[str, RedactionMode],
        full_key: str,
    ) -> Any:
        # 1) Explicit per-field mode (tool config)
        explicit_mode = field_modes.get(full_key) or field_modes.get(key)

        # 2) Otherwise, if the field name is sensitive → default mode
        if explicit_mode is None and key.lower() in SENSITIVE_FIELD_NAMES:
            explicit_mode = self.default_mode

        if explicit_mode is not None:
            # Direct redaction: do not descend any further.
            if isinstance(value, str):
                return _apply_mode(value, explicit_mode)
            if isinstance(value, dict):
                return {
                    k: _apply_mode(v, explicit_mode) if isinstance(v, str) else v
                    for k, v in value.items()
                }
            if isinstance(value, list):
                return [_apply_mode(v, explicit_mode) if isinstance(v, str) else v for v in value]
            return value  # number / bool / None: nothing to do

        # 3) Otherwise, descend recursively and apply the patterns.
        if isinstance(value, dict):
            return self._redact_dict(value, field_modes, full_key)
        if isinstance(value, list):
            return [self._redact_value(key, v, field_modes, full_key) for v in value]
        if isinstance(value, str):
            return _redact_by_patterns(value, self.patterns)

        return value


# ---------------------------------------------------------------------------
# Default instance (no-op)
# ---------------------------------------------------------------------------

_NULL_REDACTOR = Redactor({"enabled": False})


def build_redactor(policy: dict[str, Any]) -> Redactor:
    """Construit un Redactor depuis la policy."""
    return Redactor(policy.get("redaction") or {})
