"""
Pryxor — Argument validation against an executor's JSON Schema.

The `inputSchema` declared on each executor is the single source of truth for
what an agent may send. It is:

  - published to MCP clients (so a model knows the real fields), and
  - enforced here, *before* the policy engine runs, so a malformed call is
    rejected deterministically instead of reaching (and possibly bypassing)
    the sector's whitelist checks (e.g. smuggling a `bcc` field).

We support the subset of JSON Schema that matters for tool arguments:
type, required, properties, additionalProperties, enum, minLength/maxLength,
minimum/maximum (and exclusive counterparts). No external dependency.
"""

from __future__ import annotations

from typing import Any

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_arguments(schema: dict[str, Any] | None, parameters: dict[str, Any]) -> list[str]:
    """
    Validate `parameters` against `schema`.

    Returns a list of human-readable error strings (empty = valid).
    """
    if not isinstance(schema, dict) or not schema:
        return []
    if not isinstance(parameters, dict):
        return ["arguments must be an object"]

    errors: list[str] = []

    # additionalProperties: false → reject unknown fields.
    if schema.get("additionalProperties") is False:
        allowed = set((schema.get("properties") or {}).keys())
        for key in parameters:
            if key not in allowed:
                errors.append(f"unknown field '{key}'")

    # required
    for field in schema.get("required") or []:
        if field not in parameters:
            errors.append(f"missing required field '{field}'")

    # per-property constraints
    properties = schema.get("properties") or {}
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if name not in parameters or not isinstance(spec, dict):
                continue
            errors.extend(_check_value(name, parameters[name], spec))

    return errors


def _check_value(name: str, value: Any, spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    expected = spec.get("type")
    if expected in _JSON_TYPES:
        py_type = _JSON_TYPES[expected]
        # bool is a subclass of int in Python — guard against `True` as number.
        if expected in ("number", "integer") and isinstance(value, bool):
            errors.append(f"field '{name}' must be a {expected}")
            return errors
        if not isinstance(value, py_type):
            errors.append(f"field '{name}' must be a {expected}")
            return errors

    if "enum" in spec and value not in (spec["enum"] or []):
        errors.append(f"field '{name}' must be one of {spec['enum']}")

    if isinstance(value, str):
        if "maxLength" in spec and len(value) > int(spec["maxLength"]):
            errors.append(f"field '{name}' exceeds maxLength {spec['maxLength']}")
        if "minLength" in spec and len(value) < int(spec["minLength"]):
            errors.append(f"field '{name}' is shorter than minLength {spec['minLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"field '{name}' exceeds maximum {spec['maximum']}")
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"field '{name}' is below minimum {spec['minimum']}")
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            errors.append(f"field '{name}' must be > {spec['exclusiveMinimum']}")
        if "exclusiveMaximum" in spec and value >= spec["exclusiveMaximum"]:
            errors.append(f"field '{name}' must be < {spec['exclusiveMaximum']}")

    return errors
