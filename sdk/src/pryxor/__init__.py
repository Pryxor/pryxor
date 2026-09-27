"""
Pryxor — runtime security for AI agents.

This package bundles the three ways an agent talks to Pryxor:

* the **client** (``pryxor.Pryxor``) — lightweight, only depends on ``requests``;
* the **framework adapters** (``pryxor.adapters``) — optional, one per framework;
* the **MCP** integration (``pryxor.mcp``) — proxy and standalone server.

Install just the client:

    pip install pryxor

Add adapters as needed:

    pip install "pryxor[langchain]"
    pip install "pryxor[crewai]"
    pip install "pryxor[openai-agents]"
    pip install "pryxor[all]"

Quick start:

    from pryxor import Pryxor, PryxorHoldPendingError, PryxorBlockedError

    pryxor = Pryxor()  # reads PRYXOR_AGENT_KEY / PRYXOR_URL from the environment
    result = pryxor.execute("send_payment", {"amount": 100, "recipient": "Fournisseur_A"})
"""

from __future__ import annotations

from .client import (
    Pryxor,
    PryxorAuthError,
    PryxorBlockedError,
    PryxorError,
    PryxorExecutionError,
    PryxorHoldPendingError,
)

__all__ = [
    "Pryxor",
    "PryxorError",
    "PryxorAuthError",
    "PryxorBlockedError",
    "PryxorExecutionError",
    "PryxorHoldPendingError",
]

__version__ = "0.1.0"
