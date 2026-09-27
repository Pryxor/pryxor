# syntax=docker/dockerfile:1.6
#
# Pryxor — multi-stage, non-root, hardened runtime image.
#
# Build:   docker build -t pryxor:latest .
# Run:     see docker-compose.yml
#

# =====================================================================
# Stage 1 — Builder: install dependencies in an isolated venv
# =====================================================================
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt


# =====================================================================
# Stage 2 — Runtime: minimal, non-root, healthchecked
# =====================================================================
FROM python:3.11-slim AS runtime

# --- Environment defaults (overridable via docker-compose) ----------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PRYXOR_STATE_PATH=/data/pryxor_state.sqlite3 \
    PRYXOR_CONFIG_DIR=/configs \
    PRYXOR_LOG_LEVEL=INFO

# --- System user (non-root) + directory layout ----------------------
RUN groupadd --system --gid 10001 pryxor \
 && useradd  --system --uid 10001 --gid pryxor \
             --home /app --shell /sbin/nologin pryxor \
 && mkdir -p /app /configs /data \
 && chown -R pryxor:pryxor /app /configs /data

# --- Copy the venv from the builder ---------------------------------
COPY --from=builder /opt/venv /opt/venv

# --- Copy application code (explicit list — no tests, no .git) ------
WORKDIR /app
COPY --chown=pryxor:pryxor \
    pryxor_proxy.py \
    pryxor_engine.py \
    pryxor_auth.py \
    pryxor_config.py \
    pryxor_normalizer.py \
    pryxor_paths.py \
    pryxor_validation.py \
    pryxor_executors.py \
    pryxor_secrets.py \
    pryxor_notifiers.py \
    pryxor_ratelimit.py \
    pryxor_redaction.py \
    pryxor_requestid.py \
    pryxor_metrics.py \
    admin_cli.py \
    pryxor_cli.py \
    requirements.txt \
    ./
COPY --chown=pryxor:pryxor sectors/ ./sectors/

USER pryxor

EXPOSE 8000

# --- Healthcheck (no curl needed) -----------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3).status == 200 else 1)"

# --- Entrypoint -----------------------------------------------------
# Direct CMD (no shell) → uvicorn receives SIGTERM/SIGINT as PID 1.
# workers=1 is intentional: SQLite is single-writer. See ROADMAP.md (scale).
CMD ["uvicorn", "pryxor_proxy:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--access-log"]