"""
Pryxor — Sector registry.

A **sector** is a deterministic policy plugin: it receives a normalized action
and returns a ``Decision`` (``APPROVED`` / ``HOLD`` / ``BLOCKED``).

There are two kinds of sectors, and both are declared explicitly in
``configs/sectors/<name>.json`` via a ``type`` field:

    ``"type": "code"``         → implemented by a module under ``sectors/``
                                 (e.g. ``sectors/finance.py``), configured by
                                 this JSON file.
    ``"type": "declarative"``  → expressed entirely as ``rules`` in the JSON
                                 file; no Python needed.

The building blocks (``SectorPolicy``, ``SimpleSector``, ``DeclarativeSector``,
``SectorStorage``) live in ``sectors/_framework/`` — deliberately out of the way
so it is never mistaken for a sector.

Auto-discovery scans only the modules **directly** under ``sectors/`` (never
``_framework``). Any class inheriting from ``SectorPolicy`` with a ``name`` is
registered under that name.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any

from ._framework.base import Decision, DecisionStatus, SectorPolicy
from ._framework.declarative import DeclarativeSector
from ._framework.simple import SimpleSector

logger = logging.getLogger("pryxor.sectors")

#: Folder holding the framework. Never scanned as a sector, never registered.
FRAMEWORK_DIR = "_framework"

#: Sector kinds, as declared by the ``type`` field in ``configs/sectors/*.json``.
KIND_CODE = "code"
KIND_DECLARATIVE = "declarative"
VALID_KINDS = {KIND_CODE, KIND_DECLARATIVE}


def _discover_builtin_sectors() -> dict[str, type[SectorPolicy]]:
    """Find every sector class in the modules directly under ``sectors/``."""
    registry: dict[str, type[SectorPolicy]] = {}
    package_path = Path(__file__).parent

    for module_info in pkgutil.iter_modules([str(package_path)]):
        name = module_info.name
        # `_framework` and any private module is framework, not a sector.
        if name == FRAMEWORK_DIR or name.startswith("_"):
            continue
        if not module_info.ispkg and name in {"__init__"}:
            continue

        try:
            module = importlib.import_module(f".{name}", package=__name__)
        except Exception as e:
            logger.warning("Failed to import sector module '%s': %s", name, e)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, SectorPolicy):
                continue
            if obj in (SectorPolicy, SimpleSector, DeclarativeSector):
                continue
            sector_name = getattr(obj, "name", None)
            if not sector_name or sector_name == "base":
                continue
            registry[sector_name] = obj
            logger.debug("Discovered sector '%s' in %s", sector_name, name)

    return registry


SECTORS: dict[str, type[SectorPolicy]] = _discover_builtin_sectors()


def declared_kind(sector_conf: Any) -> str:
    """
    Return the declared kind of a sector config entry.

    Defaults to ``"code"`` when no ``type`` is set, so existing configs keep
    working. Accepts only ``code`` / ``declarative``.
    """
    if not isinstance(sector_conf, dict):
        return KIND_CODE
    return str(sector_conf.get("type", KIND_CODE)).lower()


def describe_sectors(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build a catalogue of the sectors declared in the config, for tooling (an
    operator listing them, or the future dashboard).

    Each entry: ``{name, kind, builtin, configured, version, parameters}``.
    """
    catalogue: list[dict[str, Any]] = []
    for name, conf in (policy.get("sectors") or {}).items():
        kind = declared_kind(conf)
        cls = SECTORS.get(name)
        catalogue.append(
            {
                "name": name,
                "kind": kind,
                "builtin": cls is not None,
                "configured": isinstance(conf, dict),
                "version": getattr(cls, "version", None) if cls else None,
                "parameters": sorted(k for k in conf if k != "type")
                if isinstance(conf, dict)
                else [],
            }
        )
    # Also surface built-ins that are not explicitly configured.
    for name, cls in SECTORS.items():
        if not any(entry["name"] == name for entry in catalogue):
            catalogue.append(
                {
                    "name": name,
                    "kind": KIND_CODE,
                    "builtin": True,
                    "configured": False,
                    "version": getattr(cls, "version", None),
                    "parameters": [],
                }
            )
    return sorted(catalogue, key=lambda e: e["name"])


def load_sector(
    name: str,
    policy: dict[str, Any],
    state_path: Path,
) -> SectorPolicy:
    """
    Instantiate the sector ``name`` declared in the config.

    Resolution is explicit, driven by the ``type`` field:

    - ``code`` (default) → the built-in class registered under ``name``.
    - ``declarative``    → a ``DeclarativeSector`` built from the ``rules`` list.
    """
    sector_conf = (policy.get("sectors") or {}).get(name)
    kind = declared_kind(sector_conf)

    if kind == KIND_DECLARATIVE:
        if not isinstance(sector_conf, dict) or "rules" not in sector_conf:
            raise ValueError(
                f"Sector '{name}' is declared as type 'declarative' but has no "
                f"'rules' list in configs/sectors/{name}.json."
            )
        return DeclarativeSector(policy=policy, state_path=state_path, name=name)

    if kind != KIND_CODE:
        raise ValueError(
            f"Sector '{name}' has unknown type '{kind}'. Expected one of: {sorted(VALID_KINDS)}."
        )

    if name in SECTORS:
        return SECTORS[name](policy=policy, state_path=state_path)

    available = sorted(SECTORS.keys())
    raise ValueError(
        f"Sector '{name}' is declared as type 'code' but no module defines it. "
        f'Add sectors/{name}.py, or set "type": "declarative" with a '
        f"'rules' list. Built-in sectors: {available}."
    )


__all__ = [
    "Decision",
    "DecisionStatus",
    "SectorPolicy",
    "SimpleSector",
    "DeclarativeSector",
    "SECTORS",
    "VALID_KINDS",
    "declared_kind",
    "describe_sectors",
    "load_sector",
]
