"""
Pryxor — Prometheus metrics.

Metrics exposed on /metrics in the Prometheus text format.
If `prometheus_client` is not installed, all functions are no-ops —
Pryxor keeps working normally.

Pour activer : pip install prometheus-client
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("pryxor.metrics")

# Availability detection
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    Counter = Gauge = Histogram = None  # type: ignore
    CollectorRegistry = None  # type: ignore
    generate_latest = None  # type: ignore
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"


# ---------------------------------------------------------------------------
# Registry (isolated to avoid collisions with other libraries)
# ---------------------------------------------------------------------------

_REGISTRY = CollectorRegistry() if _AVAILABLE else None


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

if _AVAILABLE:
    TOOL_CALLS = Counter(
        "pryxor_tool_calls_total",
        "Total tool calls evaluated by Pryxor.",
        ["status", "tool", "agent"],
        registry=_REGISTRY,
    )

    TOOL_CALL_DURATION = Histogram(
        "pryxor_tool_call_duration_seconds",
        "Duration of a tool call evaluation (engine only, excl. execution).",
        ["status"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        registry=_REGISTRY,
    )

    EXECUTIONS = Counter(
        "pryxor_executions_total",
        "Executions attempted by Pryxor executors.",
        ["tool", "status"],
        registry=_REGISTRY,
    )

    EXECUTION_DURATION = Histogram(
        "pryxor_execution_duration_seconds",
        "Duration of executor calls (external API).",
        ["tool", "status"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        registry=_REGISTRY,
    )

    HOLDS_TOTAL = Counter(
        "pryxor_holds_total",
        "HOLD transitions.",
        ["status"],  # approved, rejected, expired
        registry=_REGISTRY,
    )

    HOLDS_PENDING = Gauge(
        "pryxor_holds_pending",
        "Current number of PENDING holds.",
        registry=_REGISTRY,
    )

    NOTIFICATIONS = Counter(
        "pryxor_notifications_total",
        "Notification delivery attempts.",
        ["route", "status"],  # sent, failed, dead
        registry=_REGISTRY,
    )

    NOTIFICATIONS_PENDING = Gauge(
        "pryxor_notifications_pending",
        "Current number of PENDING notifications.",
        registry=_REGISTRY,
    )

    RATE_LIMIT_HITS = Counter(
        "pryxor_rate_limit_hits_total",
        "Tool calls rejected by the rate limiter.",
        ["reason"],  # window, burst
        registry=_REGISTRY,
    )

    OUTBOX_PENDING = Gauge(
        "pryxor_outbox_pending",
        "Current number of unprocessed outbox events.",
        registry=_REGISTRY,
    )

    AGENTS_TOTAL = Gauge(
        "pryxor_agents_total",
        "Number of active agents (not revoked).",
        registry=_REGISTRY,
    )

    POLICY_INFO = Gauge(
        "pryxor_policy_info",
        "Static info about the current policy (value=1).",
        ["sector", "version"],
        registry=_REGISTRY,
    )


# ---------------------------------------------------------------------------
# publc API — no-op if prometheus_client isnt available
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Return True if metrics are enabled."""
    if not _AVAILABLE:
        return False
    # Can be disabled via env var
    return os.environ.get("PRYXOR_METRICS_ENABLED", "true").lower() != "false"


def record_tool_call(status: str, tool: str, agent: str, duration: float) -> None:
    if not is_enabled():
        return
    try:
        TOOL_CALLS.labels(status=status, tool=tool, agent=agent).inc()
        TOOL_CALL_DURATION.labels(status=status).observe(duration)
    except Exception:
        logger.debug("Failed to record tool_call metric.", exc_info=True)


def record_execution(tool: str, status: str, duration: float) -> None:
    if not is_enabled():
        return
    try:
        EXECUTIONS.labels(tool=tool, status=status).inc()
        EXECUTION_DURATION.labels(tool=tool, status=status).observe(duration)
    except Exception:
        logger.debug("Failed to record execution metric.", exc_info=True)


def record_hold_transition(status: str) -> None:
    if not is_enabled():
        return
    try:
        HOLDS_TOTAL.labels(status=status).inc()
    except Exception:
        logger.debug("Failed to record hold metric.", exc_info=True)


def set_holds_pending(value: int) -> None:
    if not is_enabled():
        return
    try:
        HOLDS_PENDING.set(value)
    except Exception:
        logger.debug("Failed to set holds_pending metric.", exc_info=True)


def record_notification(route: str, status: str) -> None:
    if not is_enabled():
        return
    try:
        NOTIFICATIONS.labels(route=route, status=status).inc()
    except Exception:
        logger.debug("Failed to record notification metric.", exc_info=True)


def set_notifications_pending(value: int) -> None:
    if not is_enabled():
        return
    try:
        NOTIFICATIONS_PENDING.set(value)
    except Exception:
        logger.debug("Failed to set notifications_pending metric.", exc_info=True)


def record_rate_limit_hit(reason: str) -> None:
    if not is_enabled():
        return
    try:
        RATE_LIMIT_HITS.labels(reason=reason).inc()
    except Exception:
        logger.debug("Failed to record rate limit metric.", exc_info=True)


def set_outbox_pending(value: int) -> None:
    if not is_enabled():
        return
    try:
        OUTBOX_PENDING.set(value)
    except Exception:
        logger.debug("Failed to set outbox_pending metric.", exc_info=True)


def set_agents_total(value: int) -> None:
    if not is_enabled():
        return
    try:
        AGENTS_TOTAL.set(value)
    except Exception:
        logger.debug("Failed to set agents_total metric.", exc_info=True)


def set_policy_info(sector: str, version: str) -> None:
    if not is_enabled():
        return
    try:
        # Reset the previous values (in case the sector changes)
        POLICY_INFO.clear()
        POLICY_INFO.labels(sector=sector, version=version).set(1)
    except Exception:
        logger.debug("Failed to set policy_info metric.", exc_info=True)


def render_metrics() -> tuple[bytes, str]:
    """
    Return (body, content_type) for the /metrics endpoint.
    If metrics are disabled, return a minimal text.
    """
    if not is_enabled():
        return (b"# metrics disabled\n", "text/plain; charset=utf-8")
    try:
        return (generate_latest(_REGISTRY), CONTENT_TYPE_LATEST)
    except Exception:
        logger.exception("Failed to render metrics.")
        return (b"# metrics error\n", "text/plain; charset=utf-8")
