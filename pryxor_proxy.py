"""
Pryxor — Proxy HTTP d'interception.

⚠️ Agent identity: X-Agent-Key header, resolved by AgentRegistry.
⚠️ Admin identity: X-Admin-Key header, resolved by AdminRegistry.
    The two registries are separate: an agent key cannot authenticate as
    an admin, and vice versa.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import pryxor_metrics as metrics
from pryxor_auth import AdminRegistry, AgentRegistry
from pryxor_engine import PolicyEngine
from pryxor_normalizer import normalize_tool_call
from pryxor_paths import resolve_policy_path, resolve_state_path
from pryxor_requestid import clear_request_id, get_request_id, set_request_id

load_dotenv()  # loads .env automatically

logging.basicConfig(
    level=os.environ.get("PRYXOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pryxor.proxy")


# ======================================================================
# Lifespan
# ======================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup/shutdown hook.

    The engine is constructed at import time (module-level singletons below).
    This lifespan exists so FastAPI has a valid callable and so future
    startup work (outbox recovery, connection warm-up) has a home that
    does not require touching every call site.
    """
    logger.info("Pryxor proxy starting.")
    yield
    logger.info("Pryxor proxy stopping.")


# ======================================================================
# Middlewares
# ======================================================================


MAX_BODY_BYTES = int(os.environ.get("PRYXOR_MAX_BODY_BYTES", 64 * 1024))


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": (f"Request body too large. Max {MAX_BODY_BYTES} bytes.")
                        },
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})

        # Read the body to check the real size (guards against spoofed CL).
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": (f"Request body too large. Max {MAX_BODY_BYTES} bytes.")},
            )

        # Re-inject the body so FastAPI can read it.
        def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Inject a request ID into every request.

    - Reuse `X-Request-ID` if present (useful for propagation).
    - Otherwise, generate one.
    - Put it into the logs via contextvars.
    - Return it in the response header.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Request-ID")
        request_id = set_request_id(incoming)
        try:
            response = await call_next(request)
        finally:
            clear_request_id()
        response.headers["X-Request-ID"] = request_id
        return response


app = FastAPI(
    title="Pryxor — Runtime security for AI agents",
    lifespan=lifespan,
)

app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

STATE_PATH = resolve_state_path()
POLICY_PATH = resolve_policy_path()

engine = PolicyEngine(state_path=STATE_PATH)
agent_registry = AgentRegistry(state_path=STATE_PATH)
admin_registry = AdminRegistry(state_path=STATE_PATH)


# ======================================================================
# Payload
# ======================================================================


class ToolCallPayload(BaseModel):
    tool_name: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    type: Optional[str] = None
    agent_id: Optional[str] = None  # ignored, kept for compatibility
    context: dict[str, Any] = Field(default_factory=dict)
    tool_call: Optional[dict[str, Any]] = None
    action: Optional[dict[str, Any]] = None


# ======================================================================
# Auth dependencies
# ======================================================================


def get_authenticated_agent(
    x_agent_key: Optional[str] = Header(default=None, alias="X-Agent-Key"),
) -> str:
    if not x_agent_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-Key header.",
            headers={"WWW-Authenticate": "AgentKey"},
        )
    agent_id = agent_registry.verify_key(x_agent_key)
    if not agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked agent key.",
            headers={"WWW-Authenticate": "AgentKey"},
        )
    return agent_id


def get_authenticated_admin(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
) -> str:
    if not x_admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Admin-Key header.",
            headers={"WWW-Authenticate": "AdminKey"},
        )
    admin_id = admin_registry.verify_key(x_admin_key)
    if not admin_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked admin key.",
            headers={"WWW-Authenticate": "AdminKey"},
        )
    return admin_id


# ======================================================================
# Endpoints
# ======================================================================


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "Pryxor Engine"}


@app.post("/v1/execute-tool")
def process_tool_call(
    payload: ToolCallPayload,
    agent_id: str = Depends(get_authenticated_agent),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if engine.rate_limiter is not None:
        decision = engine.rate_limiter.check(agent_id)
        if not decision["allowed"]:
            metrics.record_rate_limit_hit(decision["reason"])
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(f"Rate limit exceeded ({decision['reason']}, limit={decision['limit']})."),
                headers={
                    "Retry-After": str(decision["retry_after"]),
                    "X-RateLimit-Limit": str(decision["limit"]),
                },
            )
    raw = payload.model_dump()
    tool_name, parameters = normalize_tool_call(raw)

    if not tool_name:
        return {
            "status": "BLOCKED",
            "reason": "INVALID_TOOL_CALL",
            "message": "Could not extract tool name from payload.",
        }

    logger.info(
        "Intercepted |request_id=%s | agent=%s | tool=%s | idem=%s",
        get_request_id(),
        agent_id,
        tool_name,
        idempotency_key,
    )
    return engine.evaluate(
        agent_id,
        tool_name,
        parameters,
        client_idempotency_key=idempotency_key,
    )


# --- Admin endpoints --------------------------------------------------


@app.get("/v1/holds")
def list_holds(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    logger.info("List holds | admin=%s | limit=%d offset=%d", admin_id, limit, offset)
    return {
        "holds": engine.list_holds(limit=limit, offset=offset),
        "total": engine.hold_store.count_holds(),
        "limit": limit,
        "offset": offset,
    }


@app.get("/v1/holds/{action_id}")
def get_hold(
    action_id: str,
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    # Return the current status of a single hold.
    # Intended for operators and dashboards (admin-authenticated). Agents are
    # deliberately not given a polling endpoint: a HOLD is a terminal answer
    # for them ("pending human approval, do not retry").
    logger.info("Get hold | admin=%s | action=%s", admin_id, action_id)
    hold = engine.hold_store.get(action_id)
    if hold is None:
        raise HTTPException(status_code=404, detail=f"Hold '{action_id}' not found.")
    return hold


@app.get("/v1/audit")
def list_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    logger.info("List audit | admin=%s | limit=%d offset=%d", admin_id, limit, offset)
    return {
        "audit_events": engine.list_audit_events(limit=limit, offset=offset),
        "total": engine.hold_store.count_audit(),
        "limit": limit,
        "offset": offset,
    }


@app.post("/v1/holds/{action_id}/approve")
def approve_hold(
    action_id: str,
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    logger.info(
        "Approve |request_id=%s | admin=%s | action=%s",
        get_request_id(),
        admin_id,
        action_id,
    )
    return engine.approve_hold(action_id, actor_id=admin_id)


@app.post("/v1/holds/{action_id}/reject")
def reject_hold(
    action_id: str,
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    logger.info(
        "Reject |request_id=%s | admin=%s | action=%s",
        get_request_id(),
        admin_id,
        action_id,
    )
    return engine.reject_hold(action_id, actor_id=admin_id)


@app.get("/v1/executions")
def list_executions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    return {
        "executions": engine.list_executions(limit=limit, offset=offset),
        "total": engine.hold_store.count_executions(),
        "limit": limit,
        "offset": offset,
    }


@app.get("/v1/notifications")
def list_notifications(
    limit: int = 100,
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    return {"notifications": engine.list_notifications(limit=limit)}


@app.post("/v1/notifications/dispatch")
def dispatch_notifications(
    admin_id: str = Depends(get_authenticated_admin),
) -> dict[str, Any]:
    """
    Force a dispatch of pending notifications.
    Useful for the operator when a route has just been re-enabled.
    """
    return engine.dispatch_pending_notifications()


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    """
    Prometheus endpoint.

    ⚠️ Unauthenticated by default — this is the standard for Prometheus
       scraping. In production, restrict access to the internal network or
       protect it via a reverse proxy.
    """
    body, content_type = metrics.render_metrics()
    return Response(content=body, media_type=content_type)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
