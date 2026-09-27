"""
Pryxor MCP Server — standalone MCP server (no upstream required).

Exposes the tools configured in your Pryxor policy as MCP tools.
Each tools/call is evaluated by Pryxor's policy engine and executed
by Pryxor's executors (gateway mode).

⚠️ STDOUT IS RESERVED FOR JSON-RPC. All logs go to STDERR.

Usage:
    pryxor-mcp-server --agent-key pryxor_agent_xxx
    pryxor-mcp-server --config mcp_server_config.json
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from typing import Any, Optional

import requests

# ⚠️ Windows Python defaults stdout to cp1252, which crashes as soon as
# a non-ASCII character is written (emoji, accents...).
# Force UTF-8 to avoid UnicodeEncodeError on JSON-RPC responses.
try:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    # Python < 3.7 : fallback via io.TextIOWrapper
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        newline="\n",
        write_through=True,
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding="utf-8",
        write_through=True,
    )

try:
    from dotenv import load_dotenv
    load_dotenv()
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False
from ._config import load_policy, resolve_policy_path

# ---------------------------------------------------------------------------
# Logging → stderr only
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("PRYXOR_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] pryxor-mcp: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("pryxor.mcp.server")


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _emit(msg: dict) -> None:
    """Write a JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _text_content(text: str, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------


class PryxorMCPServer:
    """
    Standalone MCP server backed by Pryxor.

    Tools are discovered from the Pryxor config folder (the `executors/` section).
    Every tools/call is evaluated by Pryxor and executed in gateway mode.
    """

    def __init__(
        self,
        agent_key: str,
        pryxor_url: str = "http://127.0.0.1:8000",
        policy_path: str | None = None,
        timeout: float = 30.0,
    ):
        self.agent_key = agent_key
        self.pryxor_url = pryxor_url.rstrip("/")
        self.policy_path = policy_path
        self.timeout = timeout
        self._initialized = False

    # ------------------------------------------------------------------
    # Policy / tools discovery
    # ------------------------------------------------------------------

    def _load_tools(self) -> list[dict[str, Any]]:
        """
        Read the config folder and return the list of exposed tools (the keys
        of `executors`).
        """
        try:
            # Self-contained resolution (works standalone or inside the repo).
            policy = load_policy(self.policy_path)
        except Exception as e:
            logger.error("Failed to load config: %s", e)
            return []

        executors = policy.get("executors") or {}
        tools: list[dict[str, Any]] = []
        for tool_name, cfg in executors.items():
            if not isinstance(cfg, dict):
                continue
            description = cfg.get("description") or f"Execute {tool_name} via Pryxor."
            # The argument schema is the single source of truth, declared on the
            # executor itself. If an executor omits it, we publish a strict empty
            # schema (additionalProperties: false) rather than a permissive one:
            # a permissive default would let an agent smuggle unexpected fields
            # (e.g. bcc) past the sector's whitelist checks.
            input_schema = cfg.get("inputSchema") or {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
            tools.append(
                {
                    "name": tool_name,
                    "description": description,
                    "inputSchema": input_schema,
                }
            )
        logger.info("Loaded %d tool(s) from policy: %s", len(tools), [t["name"] for t in tools])
        return tools

    # ------------------------------------------------------------------
    # Pryxor call
    # ------------------------------------------------------------------

    def _call_pryxor(self, tool_name: str, arguments: dict) -> dict:
        try:
            r = requests.post(
                f"{self.pryxor_url}/v1/execute-tool",
                json={"tool_name": tool_name, "parameters": arguments},
                headers={
                    "X-Agent-Key": self.agent_key,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            return {
                "status": "ERROR",
                "reason": "PRYXOR_UNREACHABLE",
                "message": str(e),
            }

        if r.status_code == 401:
            return {
                "status": "BLOCKED",
                "reason": "AUTH_ERROR",
                "message": "Invalid or revoked agent key.",
            }
        if r.status_code == 413:
            return {
                "status": "BLOCKED",
                "reason": "PAYLOAD_TOO_LARGE",
                "message": "Tool call exceeds the maximum size.",
            }
        if r.status_code == 429:
            return {
                "status": "BLOCKED",
                "reason": "RATE_LIMITED",
                "message": "Rate limit exceeded.",
            }
        if r.status_code >= 500:
            return {
                "status": "ERROR",
                "reason": "PRYXOR_SERVER_ERROR",
                "message": f"HTTP {r.status_code}: {r.text[:200]}",
            }

        try:
            return r.json()
        except ValueError:
            return {"status": "ERROR", "reason": "INVALID_RESPONSE", "message": r.text[:200]}

    # ------------------------------------------------------------------
    # MCP method handlers
    # ------------------------------------------------------------------

    def handle_initialize(self, msg_id: Any, params: dict) -> dict:
        self._initialized = True
        return _result(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pryxor", "version": "1.0.0"},
            },
        )

    def handle_tools_list(self, msg_id: Any) -> dict:
        tools = self._load_tools()
        return _result(msg_id, {"tools": tools})

    def handle_tools_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if not tool_name:
            return _error(msg_id, -32602, "Missing tool name.")

        logger.info("Intercepted MCP tool call: %s", tool_name)

        result = self._call_pryxor(tool_name, arguments)
        status = result.get("status")

        if status == "APPROVED":
            exec_block = result.get("execution") or {}
            if exec_block.get("success") is True:
                payload = exec_block.get("result") or {}
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                return _result(msg_id, _text_content(text, is_error=False))
            err = exec_block.get("error") or "Execution failed."
            return _result(msg_id, _text_content(f" Execution failed: {err}", is_error=True))

        if status == "BLOCKED":
            return _result(
                msg_id,
                _text_content(
                    f" Blocked by Pryxor: {result.get('reason')} — {result.get('message')}",
                    is_error=True,
                ),
            )

        if status == "HOLD":
            return _result(
                msg_id,
                _text_content(
                    (
                        f" Held for human approval by Pryxor.\n"
                        f"action_id={result.get('action_id')}\n"
                        f"reason={result.get('reason')}\n"
                        f"message={result.get('message')}"
                    ),
                    is_error=False,
                ),
            )

        return _result(
            msg_id,
            _text_content(
                f"Pryxor error: {result.get('reason', 'UNKNOWN')} — {result.get('message', '')}",
                is_error=True,
            ),
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> int:
        logger.info("Pryxor MCP Server started. Pryxor URL: %s", self.pryxor_url)
        logger.info("Config folder: %s", self.policy_path)

        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from client: %s", line[:200])
                    continue

                method = msg.get("method")
                msg_id = msg.get("id")
                params = msg.get("params") or {}

                # Notifications → no response
                if msg_id is None:
                    continue

                try:
                    if method == "initialize":
                        response = self.handle_initialize(msg_id, params)
                    elif method == "tools/list":
                        response = self.handle_tools_list(msg_id)
                    elif method == "tools/call":
                        response = self.handle_tools_call(msg_id, params)
                    elif method == "ping":
                        response = _result(msg_id, {})
                    else:
                        response = _error(msg_id, -32601, f"Method not found: {method}")
                except Exception:
                    logger.exception("Handler raised for method=%s", method)
                    response = _error(msg_id, -32603, "Internal server error.")

                _emit(response)

        except KeyboardInterrupt:
            logger.info("Interrupted by user.")

        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pryxor-mcp-server",
        description="Pryxor standalone MCP server.",
    )
    p.add_argument("--agent-key", help="Pryxor agent key (or env PRYXOR_AGENT_KEY).")
    p.add_argument("--pryxor-url", help="Pryxor base URL (or env PRYXOR_URL).")
    p.add_argument(
        "--policy",
        help="Path to a config folder. If omitted, auto-detect (configs/, configs.example/).",
        default=None,
    )
    p.add_argument("--config", help="Path to a JSON config file.")

    return p


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {
        "agent_key": cfg.get("agent_key") or os.environ.get("PRYXOR_AGENT_KEY"),
        "pryxor_url": cfg.get("pryxor_url")
        or os.environ.get("PRYXOR_URL", "http://127.0.0.1:8000"),
        "policy_path": resolve_policy_path(cfg.get("policy_path")),
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.config:
        try:
            cfg = _load_config(args.config)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error("Invalid config file: %s", e)
            return 1
    else:
        cfg = {
            "agent_key": args.agent_key or os.environ.get("PRYXOR_AGENT_KEY"),
            "pryxor_url": args.pryxor_url or os.environ.get("PRYXOR_URL", "http://127.0.0.1:8000"),
            "policy_path": args.policy,
        }

    if not cfg["agent_key"]:
        logger.error("Missing --agent-key or PRYXOR_AGENT_KEY.")
        return 1

    server = PryxorMCPServer(
        agent_key=cfg["agent_key"],
        pryxor_url=cfg["pryxor_url"],
        policy_path=cfg["policy_path"],
    )
    return server.run()


if __name__ == "__main__":
    sys.exit(main())
