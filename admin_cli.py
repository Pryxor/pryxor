"""
Pryxor — admin CLI for key management (agents and admins).

⚠️ JSON on stdout (parseable), human messages on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pryxor_auth import AdminRegistry, AgentRegistry

# Must match the server's state DB. In Docker this is set via
# PRYXOR_STATE_PATH (/data/pryxor_state.sqlite3); locally it defaults to a
# file in the current directory.

STATE_PATH = os.environ.get("PRYXOR_STATE_PATH", "pryxor_state.sqlite3")

def _print_json(data) -> None:
    # stdout only → parseable by jq / python
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pryxor admin CLI (key management).")
    parser.add_argument("--state", default=STATE_PATH, help="State DB path.")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- Agents --------------------------------------------------------
    reg = sub.add_parser("register", help="Register a new agent.")
    reg.add_argument("agent_id")
    reg.add_argument("--label", default=None)

    sub.add_parser("list", help="List registered agents.")

    rev = sub.add_parser("revoke", help="Revoke an agent key.")
    rev.add_argument("agent_id")

    # --- Admins --------------------------------------------------------
    reg_adm = sub.add_parser("register-admin", help="Register a new admin.")
    reg_adm.add_argument("admin_id")
    reg_adm.add_argument("--label", default=None)

    sub.add_parser("list-admins", help="List registered admins.")

    rev_adm = sub.add_parser("revoke-admin", help="Revoke an admin key.")
    rev_adm.add_argument("admin_id")

    rot = sub.add_parser("rotate", help="Rotate an agent's API key.")
    rot.add_argument("agent_id")
    rot.add_argument("--label", default=None)

    rot_adm = sub.add_parser("rotate-admin", help="Rotate an admin's API key.")
    rot_adm.add_argument("admin_id")
    rot_adm.add_argument("--label", default=None)

    args = parser.parse_args()

    if args.command == "register":
        registry = AgentRegistry(state_path=args.state)
        try:
            result = registry.register_agent(args.agent_id, label=args.label)
        except ValueError as e:
            _err(f"Error: {e}")
            return 1
        _err("✅ Agent registered.")
        _err("⚠️  Store this API key securely. It will NOT be shown again.\n")
        _print_json(result)
        return 0

    if args.command == "list":
        registry = AgentRegistry(state_path=args.state)
        _print_json(registry.list_agents())
        return 0

    if args.command == "revoke":
        registry = AgentRegistry(state_path=args.state)
        ok = registry.revoke_agent(args.agent_id)
        _err("✅ Agent revoked." if ok else "❌ Agent not found or already revoked.")
        return 0 if ok else 1

    if args.command == "register-admin":
        registry = AdminRegistry(state_path=args.state)
        try:
            result = registry.register_admin(args.admin_id, label=args.label)
        except ValueError as e:
            _err(f"Error: {e}")
            return 1
        _err("✅ Admin registered.")
        _err("⚠️  Store this API key securely. It will NOT be shown again.\n")
        _print_json(result)
        return 0

    if args.command == "list-admins":
        registry = AdminRegistry(state_path=args.state)
        _print_json(registry.list_admins())
        return 0

    if args.command == "revoke-admin":
        registry = AdminRegistry(state_path=args.state)
        ok = registry.revoke_admin(args.admin_id)
        _err("✅ Admin revoked." if ok else "❌ Admin not found or already revoked.")
        return 0 if ok else 1

    if args.command == "rotate":
        registry = AgentRegistry(state_path=args.state)
        try:
            result = registry.rotate_agent(args.agent_id, label=args.label)
        except ValueError as e:
            _err(f"Error: {e}")
            return 1
        _err("✅ Agent key rotated.")
        _err("⚠️  The previous key is now invalid.")
        _err("⚠️  Store the new key securely. It will NOT be shown again.\n")
        _print_json(result)
        return 0

    if args.command == "rotate-admin":
        registry = AdminRegistry(state_path=args.state)
        try:
            result = registry.rotate_admin(args.admin_id, label=args.label)
        except ValueError as e:
            _err(f"Error: {e}")
            return 1
        _err("✅ Admin key rotated.")
        _err("⚠️  The previous key is now invalid.")
        _err("⚠️  Store the new key securely. It will NOT be shown again.\n")
        _print_json(result)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
