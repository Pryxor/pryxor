"""
Pryxor — Configuration loader.

Configuration is a **folder** (`configs/`) that splits each concern into its own
file:

    configs/
    ├── pryxor.json          # global: sector, hold TTL, secrets, rate limit, redaction
    ├── agents.json          # allowed_actions (agent → tools)
    ├── notifications.json   # notification routes
    ├── executors/*.json     # one file per executor (the tool catalog)
    └── sectors/*.json       # one file per sector (its rules)

The loader assembles this into a single dict — the shape
`PolicyEngine._load_policy()` expects.

Path resolution (highest priority first):
    1. Env var PRYXOR_CONFIG_DIR (a folder)
    2. ./configs/                (the canonical layout)
    3. ./configs.example/        (bundled example — so a fresh clone runs)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("pryxor.config")


class ConfigError(Exception):
    """Configuration loading error."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file; raise ConfigError on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"Cannot read {path}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object, got {type(data).__name__}")
    return data


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge (keys from `override` win)."""
    result = dict(base)
    result.update(override)
    return result


def _load_folder_of_json(folder: Path) -> dict[str, Any]:
    """
    Load every *.json file in a folder and merge them.
    The filename (without .json) becomes the key.
    E.g. executors/send_email.json → {"send_email": {...}}
    """
    result: dict[str, Any] = {}
    if not folder.exists() or not folder.is_dir():
        return result
    for file in sorted(folder.glob("*.json")):
        key = file.stem
        result[key] = _read_json(file)
    return result


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_config_dir(root: Path | str | None = None) -> Path:
    """
    Return the configuration folder to use.

    Priority:
        1. PRYXOR_CONFIG_DIR (explicit folder)
        2. ./configs/               (the canonical layout)
        3. ./configs.example/        (bundled example — so a fresh clone runs)
    """
    root_path = Path(root) if root else Path.cwd()

    # 1. Explicit folder via env
    env_dir = os.environ.get("PRYXOR_CONFIG_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.exists() and p.is_dir():
            return p
        raise ConfigError(f"PRYXOR_CONFIG_DIR={env_dir} does not exist or is not a directory.")

    # 2. Canonical ./configs/ (must contain pryxor.json)
    configs = root_path / "configs"
    if configs.is_dir() and (configs / "pryxor.json").exists():
        return configs

    # 3. Bundled example, so a fresh clone works without `make init`.
    example = root_path / "configs.example"
    if example.is_dir() and (example / "pryxor.json").exists():
        return example

    raise ConfigError(
        "No configuration found. Pryxor reads a config folder containing "
        "pryxor.json. Expected one of:\n"
        "  - $PRYXOR_CONFIG_DIR (a folder)\n"
        "  - ./configs/\n"
        "  - ./configs.example/"
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_config(root: Path | str | None = None) -> dict[str, Any]:
    """
    Load the config folder and return a single dict, identical to what
    `PolicyEngine._load_policy()` expects.
    """
    folder = resolve_config_dir(root)
    logger.info("Loading config from folder %s", folder)
    return _load_config_folder(folder)


def _load_config_folder(folder: Path) -> dict[str, Any]:
    """
    Assemble the config from its sub-files and return a single dict.

    Expected structure:
        folder/
            pryxor.json              (global: sector, hold_ttl_minutes, secrets, ...)
            agents.json              (allowed_actions)
            notifications.json       (notifications)
            executors/*.json         (one file = one executor)
            sectors/*.json           (one file = one sector)
    """
    merged: dict[str, Any] = {}

    # 1. Global config
    main_file = folder / "pryxor.json"
    if not main_file.exists():
        raise ConfigError(f"Missing required file: {main_file}")
    main = _read_json(main_file)
    merged = _merge_dict(merged, main)

    # 2. Agents (allowed_actions)
    agents_file = folder / "agents.json"
    if agents_file.exists():
        agents_data = _read_json(agents_file)
        # Accept either {"allowed_actions": {...}} or the mapping directly.
        if "allowed_actions" in agents_data:
            merged["allowed_actions"] = agents_data["allowed_actions"]
        else:
            merged["allowed_actions"] = agents_data
    # Otherwise, keep whatever pryxor.json provides, if anything.

    # 3. Notifications
    notif_file = folder / "notifications.json"
    if notif_file.exists():
        merged["notifications"] = _read_json(notif_file)
    # Otherwise, keep pryxor.json.

    # 4. Executors (folder)
    executors_folder = folder / "executors"
    if executors_folder.exists():
        merged["executors"] = _load_folder_of_json(executors_folder)
    # Otherwise, keep pryxor.json, if any.

    # 5. Sectors (folder)
    sectors_folder = folder / "sectors"
    if sectors_folder.exists():
        merged["sectors"] = _load_folder_of_json(sectors_folder)
    # Otherwise, keep pryxor.json, if any.

    return merged
