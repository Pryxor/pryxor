"""
Pryxor — client CLI.

⚠️ Two distinct keys:
    - --api-key   / PRYXOR_AGENT_KEY  → X-Agent-Key
    - --admin-key / PRYXOR_ADMIN_KEY  → X-Admin-Key
"""

from __future__ import annotations
from typing import Any

import argparse
import json
import os
import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class PryxorClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        agent_key: str | None = None,
        admin_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_key = agent_key
        self.admin_key = admin_key

    # --- Headers -------------------------------------------------------

    def _agent_headers(self) -> dict[str, str]:
        return {"X-Agent-Key": self.agent_key} if self.agent_key else {}

    def _admin_headers(self) -> dict[str, str]:
        return {"X-Admin-Key": self.admin_key} if self.admin_key else {}

    # --- HTTP helpers --------------------------------------------------

    def _get(self, path: str, admin: bool = False) -> dict[str, Any]:
        headers = self._admin_headers() if admin else self._agent_headers()
        r = requests.get(f"{self.base_url}{path}", headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(
        self, path: str, body: dict[str, Any] | None = None, admin: bool = False
    ) -> dict[str, Any]:
        headers = self._admin_headers() if admin else self._agent_headers()
        r = requests.post(f"{self.base_url}{path}", json=body, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()

    # --- Agent API -----------------------------------------------------

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return self._post(
            "/v1/execute-tool",
            {"tool_name": tool_name, "parameters": parameters},
            admin=False,
        )

    # --- Admin API -----------------------------------------------------

    def list_holds(self) -> dict[str, Any]:
        return self._get("/v1/holds", admin=True)

    def list_audit(self) -> dict[str, Any]:
        return self._get("/v1/audit", admin=True)

    def list_executions(self) -> dict[str, Any]:
        return self._get("/v1/executions", admin=True)

    def approve_hold(self, action_id: str) -> dict[str, Any]:
        return self._post(f"/v1/holds/{action_id}/approve", admin=True)

    def reject_hold(self, action_id: str) -> dict[str, Any]:
        return self._post(f"/v1/holds/{action_id}/reject", admin=True)


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Pryxor CLI.")
    parser.add_argument("--base-url", default=os.environ.get("PRYXOR_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PRYXOR_AGENT_KEY"),
        help="Agent key (or env PRYXOR_AGENT_KEY).",
    )
    parser.add_argument(
        "--admin-key",
        default=os.environ.get("PRYXOR_ADMIN_KEY"),
        help="Admin key (or env PRYXOR_ADMIN_KEY).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # actions
    actions = sub.add_parser("actions", help="Manage HOLDs.")
    actions_sub = actions.add_subparsers(dest="actions_command", required=True)
    ls = actions_sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--offset", type=int, default=0)
    ap = actions_sub.add_parser("approve")
    ap.add_argument("action_id")
    rp = actions_sub.add_parser("reject")
    rp.add_argument("action_id")

    # audit
    audit = sub.add_parser("audit", help="Inspect audit log.")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_sub.add_parser("list")

    # executions
    execs = sub.add_parser("executions", help="List executions.")
    execs.add_argument("--limit", type=int, default=100)

    # tool
    tool = sub.add_parser("tool", help="Send an authenticated tool call.")
    tool.add_argument("tool_name")
    tool.add_argument("--params", default="{}")

    args = parser.parse_args()
    client = PryxorClient(args.base_url, args.api_key, args.admin_key)

    try:
        if args.command == "actions":
            if args.actions_command == "list":
                _print_json(client.list_holds())
            elif args.actions_command == "approve":
                _print_json(client.approve_hold(args.action_id))
            elif args.actions_command == "reject":
                _print_json(client.reject_hold(args.action_id))
        elif args.command == "audit":
            _print_json(client.list_audit())
        elif args.command == "executions":
            _print_json(client.list_executions())
        elif args.command == "tool":
            _print_json(client.execute_tool(args.tool_name, json.loads(args.params)))
    except requests.HTTPError as e:
        print(f"HTTP error: {e.response.status_code} — {e.response.text}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
