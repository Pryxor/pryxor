"""
Pryxor — sector framework (internal).

This sub-package holds the building blocks a sector is written against. It is
**not** a sector and is never auto-discovered as one: the leading underscore on
the folder, and the `_framework` name in particular, keep it out of the registry.

What lives here:

- ``base``        — ``SectorPolicy`` (the contract) and ``Decision``.
- ``simple``      — ``SimpleSector``, the DX base class with the plumbing.
- ``declarative`` — ``DeclarativeSector``, rules-only sectors (no Python).
- ``storage``     — ``SectorStorage``, the shared idempotent event store.

A **sector** is a module directly under ``sectors/`` (e.g. ``sectors/finance.py``)
that defines one class inheriting from ``SectorPolicy`` with a ``name``.

Import from here like so:

    from sectors._framework import Decision, SimpleSector
"""

from __future__ import annotations

from .base import Decision, DecisionStatus, SectorPolicy
from .declarative import DeclarativeSector
from .simple import SimpleSector
from .storage import SectorStorage

__all__ = [
    "Decision",
    "DecisionStatus",
    "SectorPolicy",
    "SimpleSector",
    "DeclarativeSector",
    "SectorStorage",
]
