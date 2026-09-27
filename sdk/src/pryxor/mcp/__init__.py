"""
Pryxor MCP integration.

Two entry points:

* ``pryxor.mcp.proxy`` — an stdio **proxy** that sits in front of an existing
  upstream MCP server and intercepts ``tools/call``.
  Console script: ``pryxor-mcp``.

* ``pryxor.mcp.server`` — a **standalone** MCP server that exposes the tools
  declared in your Pryxor config directly.
  Console script: ``pryxor-mcp-server``.

The decision logic is in ``pryxor.mcp.proxy.PolicyDecider`` and is pure
(testable with an injected ``http_post``).
"""

from __future__ import annotations

from .proxy import MCPProxy, PolicyDecider

__all__ = ["PolicyDecider", "MCPProxy"]
