"""
Pryxor MCP Proxy.

Sits between an MCP agent (Claude Desktop, Cursor, ...) and an upstream
MCP server. Every tools/call is intercepted, evaluated by Pryxor, then
either forwarded, blocked, or put on HOLD.

⚠️ STDOUT IS RESERVED FOR JSON-RPC MESSAGES.
    All logs go to STDERR.

Usage:
    pryxor-mcp --config mcp_config.json
    pryxor-mcp --agent-key pryxor_agent_x_yyy --upstream npx -y @modelcontextprotocol/server-filesystem /tmp
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from typing import Any, Callable, Optional

import requests

# ---------------------------------------------------------------------------
# Logging — ALWAYS to stderr
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("PRYXOR_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] pryxor-mcp: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("pryxor.mcp")


# ---------------------------------------------------------------------------
# Decider — pure logic, testable without I/O
# ---------------------------------------------------------------------------


class PolicyDecider:
    """
    Decide what to do with an MCP tools/call.

    Returns:
        None                       → forward to the upstream (transparent mode)
        {"jsonrpc":..., "id":...} → answer the agent directly

    Testable by injecting a fake `http_post`.
    """

    def __init__(
        self,
        agent_key: str,
        pryxor_url: str,
        http_post: Callable[..., Any] = requests.post,
        timeout: float = 30.0,
    ):
        self.agent_key = agent_key
        self.pryxor_url = pryxor_url.rstrip("/")
        self._http_post = http_post
        self.timeout = timeout

    def _call_pryxor(self, tool_name: str, parameters: dict) -> dict:
        try:
            r = self._http_post(
                f"{self.pryxor_url}/v1/execute-tool",
                json={"tool_name": tool_name, "parameters": parameters},
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
            return {
                "status": "ERROR",
                "reason": "INVALID_RESPONSE",
                "message": r.text[:200],
            }

    @staticmethod
    def _error_response(msg_id: Any, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _text_result(msg_id: Any, text: str, is_error: bool = False) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            },
        }

    def decide(self, msg: dict) -> Optional[dict]:
        """
        Take a JSON-RPC message from the agent.
        Return either a direct response (dict) or None (forward).
        """
        method = msg.get("method")
        msg_id = msg.get("id")

        # Notifications → always forwarded
        if msg_id is None:
            return None

        # Only tools/call is intercepted. Everything else passes through.
        if method != "tools/call":
            return None

        params = msg.get("params") or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if not tool_name:
            return self._error_response(msg_id, -32602, "Missing tool name.")

        logger.info("Intercepted MCP tool call: %s", tool_name)

        result = self._call_pryxor(tool_name, arguments)
        status = result.get("status")

        if status == "APPROVED":
            # Transparent mode: Pryxor approved but did not execute the tool.
            # In that case the original MCP call must pass through upstream.
            exec_block = result.get("execution")
            if not exec_block:
                return None

            # Gateway mode: Pryxor already executed → return the result
            if exec_block.get("success") is True:
                payload = exec_block.get("result") or {}
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                return self._text_result(msg_id, text, is_error=False)
            # Approved but execution failed → error back to the agent
            err = exec_block.get("error") or "Execution failed."
            return self._text_result(msg_id, f"Execution failed: {err}", is_error=True)

        if status == "BLOCKED":
            return self._text_result(
                msg_id,
                f"Blocked by Pryxor: {result.get('reason')} - {result.get('message')}",
                is_error=True,
            )

        if status == "HOLD":
            return self._text_result(
                msg_id,
                (
                    f"Held for human approval by Pryxor.\n"
                    f"action_id={result.get('action_id')}\n"
                    f"reason={result.get('reason')}\n"
                    f"message={result.get('message')}"
                ),
                is_error=False,
            )

        # ERROR or unknown status → error
        return self._text_result(
            msg_id,
            f"Pryxor error: {result.get('reason', 'UNKNOWN')} - {result.get('message', '')}",
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Proxy — I/O loop
# ---------------------------------------------------------------------------


class MCPProxy:
    """
    Run Pryxor as an MCP stdio proxy.

    - Reads the agent's JSON-RPC messages on stdin.
    - Intercepts tools/call via PolicyDecider.
    - Forwards the rest to the upstream.
    - Relays the upstream responses back to the agent.
    """

    def __init__(
        self,
        decider: PolicyDecider,
        upstream_command: list[str],
    ):
        self.decider = decider
        self.upstream_command = upstream_command
        self.upstream: Optional[subprocess.Popen] = None
        self._shutdown = threading.Event()
        self._stdout_lock = threading.Lock()

    # --- low level -----------------------------------------------------

    def _emit(self, msg: dict) -> None:
        """Write a JSON-RPC message to stdout (thread-safe)."""
        line = json.dumps(msg, ensure_ascii=False)
        with self._stdout_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def _forward_upstream(self, msg: dict) -> None:
        if not self.upstream or not self.upstream.stdin:
            return
        line = json.dumps(msg, ensure_ascii=False)
        try:
            self.upstream.stdin.write(line + "\n")
            self.upstream.stdin.flush()
        except (BrokenPipeError, OSError):
            logger.warning("Upstream stdin closed.")

    # --- upstream → agent loop ----------------------------------------

    def _upstream_reader(self) -> None:
        assert self.upstream and self.upstream.stdout
        try:
            for line in self.upstream.stdout:
                if self._shutdown.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from upstream: %s", line[:200])
                    continue
                self._emit(msg)
        except Exception:
            logger.exception("Upstream reader crashed.")

    # --- agent → decision loop ----------------------------------------

    def _handle_agent_message(self, msg: dict) -> None:
        try:
            response = self.decider.decide(msg)
        except Exception:
            logger.exception("Decider raised. Returning error to agent.")
            if msg.get("id") is not None:
                self._emit(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32603, "message": "Internal proxy error."},
                    }
                )
            return

        if response is not None:
            self._emit(response)
        else:
            self._forward_upstream(msg)

    # --- run ----------------------------------------------------------

    def run(self) -> int:
        logger.info("Spawning upstream: %s", " ".join(self.upstream_command))
        try:
            self.upstream = subprocess.Popen(
                self.upstream_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,  # logs upstream → stderr
                text=True,
                bufsize=1,  # line-buffered
            )
        except FileNotFoundError as e:
            logger.error("Upstream command not found: %s", e)
            return 1
        except Exception as e:
            logger.error("Failed to spawn upstream: %s", e)
            return 1

        reader = threading.Thread(target=self._upstream_reader, daemon=True)
        reader.start()

        try:
            for line in sys.stdin:
                if self._shutdown.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from agent: %s", line[:200])
                    continue
                self._handle_agent_message(msg)
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        finally:
            self._shutdown.set()
            self._terminate_upstream()

        return 0

    def _terminate_upstream(self) -> None:
        if not self.upstream:
            return
        try:
            if self.upstream.stdin:
                self.upstream.stdin.close()
        except Exception:
            pass
        try:
            self.upstream.terminate()
            self.upstream.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.upstream.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Config & CLI
# ---------------------------------------------------------------------------


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    agent_key = cfg.get("agent_key") or os.environ.get("PRYXOR_AGENT_KEY")
    pryxor_url = cfg.get("pryxor_url") or os.environ.get("PRYXOR_URL", "http://pryxor:8000")

    upstream = cfg.get("upstream") or {}
    command = upstream.get("command")
    if isinstance(command, str):
        command = command.split()
    if not command:
        raise ValueError("Config must define upstream.command")

    return {
        "agent_key": agent_key,
        "pryxor_url": pryxor_url,
        "upstream_command": command,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pryxor-mcp",
        description="Pryxor MCP proxy — runtime security for MCP tool calls.",
    )
    p.add_argument("--config", help="Path to MCP config JSON file.")
    p.add_argument("--agent-key", help="Pryxor agent key (or env PRYXOR_AGENT_KEY).")
    p.add_argument("--pryxor-url", help="Pryxor base URL (or env PRYXOR_URL).")
    p.add_argument(
        "--upstream",
        nargs=argparse.REMAINDER,
        help="Upstream MCP server command (e.g. -- npx -y @modelcontextprotocol/server-filesystem /tmp)",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.config:
        try:
            cfg = _load_config(args.config)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error("Invalid config file: %s", e)
            return 1
    else:
        agent_key = args.agent_key or os.environ.get("PRYXOR_AGENT_KEY")
        pryxor_url = args.pryxor_url or os.environ.get("PRYXOR_URL", "http://pryxor:8000")
        upstream = args.upstream or []
        # argparse.REMAINDER may include a leading "--": drop it
        if upstream and upstream[0] == "--":
            upstream = upstream[1:]
        if not agent_key:
            logger.error("Missing --agent-key or PRYXOR_AGENT_KEY.")
            return 1
        if not upstream:
            logger.error("Missing --upstream command.")
            return 1
        cfg = {
            "agent_key": agent_key,
            "pryxor_url": pryxor_url,
            "upstream_command": upstream,
        }

    if not cfg["agent_key"]:
        logger.error("Missing agent_key (via config or PRYXOR_AGENT_KEY).")
        return 1

    decider = PolicyDecider(
        agent_key=cfg["agent_key"],
        pryxor_url=cfg["pryxor_url"],
    )
    proxy = MCPProxy(decider=decider, upstream_command=cfg["upstream_command"])
    return proxy.run()


if __name__ == "__main__":
    sys.exit(main())
