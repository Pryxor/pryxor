"""
Pryxor — Secret abstraction.

The proxy NEVER stores secrets in the database.
It reads them through a configurable SecretProvider.

v1: EnvSecretProvider (environment variables).
v2: VaultSecretProvider, AWSSecretProvider, etc. — same interface.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any


class SecretProvider(ABC):
    @abstractmethod
    def get(self, name: str) -> str | None:
        """Return the secret, or None if it does not exist."""

    def require(self, name: str) -> str:
        """Like get(), but raises if the secret is absent."""
        value = self.get(name)
        if value is None:
            raise RuntimeError(f"Required secret '{name}' is not configured.")
        return value


class EnvSecretProvider(SecretProvider):
    """Read secrets from environment variables."""

    def get(self, name: str) -> str | None:
        return os.environ.get(name)


def build_secret_provider(config: dict[str, Any] | None) -> SecretProvider:
    """
    Build a SecretProvider from the config.

    Config:
        {"secrets": {"provider": "env"}}  # default
    """
    config = config or {}
    provider_type = config.get("secrets", {}).get("provider", "env")

    if provider_type == "env":
        return EnvSecretProvider()

    raise ValueError(f"Unknown secret provider: '{provider_type}'.")
