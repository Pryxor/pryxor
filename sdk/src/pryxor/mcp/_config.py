"""
Self-contained config resolution for the Pryxor MCP server.

The MCP server needs to discover which tools to expose. It must work both
from inside the Pryxor repository (where the full config loader lives) and
as a standalone installed package (where it must not depend on the repo).

Resolution order:

1. An explicit config folder (``--policy``).
2. ``PRYXOR_CONFIG_DIR`` (folder).
3. ``./configs`` if it exists.
4. ``./configs.example`` if it exists.

The folder is loaded by merging every ``*.json`` it contains, and, if present,
the ``executors/*.json`` sub-files (keyed by filename). This mirrors the core
loader just enough to discover tool names, descriptions, and input schemas.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["load_policy", "resolve_policy_path"]


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return data


def _load_folder(folder: Path) -> dict[str, Any]:
    """Merge a config folder into a single policy dict."""
    policy: dict[str, Any] = {}

    main = folder / "pryxor.json"
    if main.is_file():
        policy.update(_read_json(main))

    agents = folder / "agents.json"
    if agents.is_file():
        data = _read_json(agents)
        # agents.json holds {"allowed_actions": {...}} — carry it through so the
        # file shape matches the core config even if the MCP server ignores it.
        policy.update(data)

    notifications = folder / "notifications.json"
    if notifications.is_file():
        policy["notifications"] = _read_json(notifications)

    executors_dir = folder / "executors"
    if executors_dir.is_dir():
        executors: dict[str, Any] = {}
        for f in sorted(executors_dir.glob("*.json")):
            executors[f.stem] = _read_json(f)
        if executors:
            policy["executors"] = executors

    sectors_dir = folder / "sectors"
    if sectors_dir.is_dir():
        sectors: dict[str, Any] = {}
        for f in sorted(sectors_dir.glob("*.json")):
            sectors[f.stem] = _read_json(f)
        if sectors:
            policy["sectors"] = sectors

    return policy


def load_policy(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load the config folder from an explicit path, or auto-detect it."""
    if path:
        p = Path(path)
        if p.is_dir():
            return _load_folder(p)
        raise FileNotFoundError(f"Policy path is not a directory: {p}")

    env_dir = os.environ.get("PRYXOR_CONFIG_DIR")
    if env_dir and Path(env_dir).is_dir():
        return _load_folder(Path(env_dir))

    if Path("configs").is_dir():
        return _load_folder(Path("configs"))

    if Path("configs.example").is_dir():
        return _load_folder(Path("configs.example"))

    raise FileNotFoundError(
        "No configuration found. Pass --policy <folder>, or set PRYXOR_CONFIG_DIR."
    )


def resolve_policy_path(explicit: str | os.PathLike | None = None) -> str | None:
    """Best-effort resolution of the config folder, for logging / display."""
    if explicit:
        return str(explicit)
    env_dir = os.environ.get("PRYXOR_CONFIG_DIR")
    if env_dir:
        return env_dir
    if Path("configs").is_dir():
        return "configs"
    if Path("configs.example").is_dir():
        return "configs.example"
    return None
