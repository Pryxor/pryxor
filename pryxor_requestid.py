"""
Pryxor — Request ID propagation.

Every HTTP request gets a unique ID, generated or taken from the
`X-Request-ID` header. This ID is:
    - Injected into every log line of the request (via contextvars).
    - Returned in the response (header `X-Request-ID`).
    - Propagated into async worker logs (falls back to the ID "system").

Usage in code:
    from pryxor_requestid import get_request_id
    logger.info("Something happened | request_id=%s", get_request_id())
"""

from __future__ import annotations

import contextvars
import uuid

# Context variable: each coroutine/task has its own value.
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pryxor_request_id", default=None
)


def generate_request_id() -> str:
    """Generate a new short request ID (16 hex characters)."""
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str | None) -> str:
    """Set the current request ID. Generate one if None."""
    if not request_id:
        request_id = generate_request_id()
    _request_id_var.set(request_id)
    return request_id


def get_request_id() -> str:
    """Return the current request ID, or 'system' when outside a request."""
    return _request_id_var.get() or "system"


def clear_request_id() -> None:
    """Clear the current request ID (end of request)."""
    _request_id_var.set(None)
