"""
Pryxor — runtime path resolution.

A single source of truth for locating the configuration and state files, so
that the proxy, the engine, the CLI, and the MCP server always point at the
SAME files.

Config resolution order (a folder):

1. ``PRYXOR_CONFIG_DIR`` (explicit folder — Docker/production).
2. ``configs/`` (the canonical layout).
3. ``configs.example/`` (example fallback, to run without ``make init``).
"""

from __future__ import annotations

import os
from pathlib import Path

from pryxor_config import load_config, resolve_config_dir

#: Canonical local folder, the one used by `make init`, the README, and Docker.
CANONICAL_CONFIG_DIR = "configs"
EXAMPLE_CONFIG_DIR = "configs.example"

#: Default state database path (relative to the cwd locally).
DEFAULT_STATE_PATH = "pryxor_state.sqlite3"


def resolve_policy_path(explicit: str | Path | None = None) -> str:
    """
    Return the configuration folder to use.

    An explicit ``explicit`` value always wins. Otherwise the resolution order
    described in the module docstring applies. Falls back to the canonical
    folder name when nothing is found (the loader reports the real error later).
    """
    if explicit:
        return str(explicit)

    env_dir = os.environ.get("PRYXOR_CONFIG_DIR")
    if env_dir:
        return env_dir
    if Path(CANONICAL_CONFIG_DIR).exists():
        return CANONICAL_CONFIG_DIR
    if Path(EXAMPLE_CONFIG_DIR).exists():
        return EXAMPLE_CONFIG_DIR
    try:
        return str(resolve_config_dir())
    except Exception:
        return CANONICAL_CONFIG_DIR


def resolve_state_path(explicit: str | Path | None = None) -> str:
    """Return the state database path (``PRYXOR_STATE_PATH`` or the default)."""
    if explicit:
        return str(explicit)
    return os.environ.get("PRYXOR_STATE_PATH", DEFAULT_STATE_PATH)


def get_config() -> dict:
    """Return the merged config. Use this everywhere instead of reading the file by hand."""
    return load_config()
