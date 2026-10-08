# Operations

This page covers running Pryxor in production: the container, the
configuration, the backup story, and what to monitor. It assumes you
have a working policy and an agent already wired to it. If you do not,
[QUICKSTART.md](../QUICKSTART.md) is the ten-minute path.

Pryxor is designed for a **single protected node**. One Pryxor, one
policy, one state database. That is deliberate and the page explains
why below.

---

## 1. Deployment

Pryxor ships as a single Docker image. It is a multi-stage build, runs
as a non-root user, has a healthcheck, and expects its policy folder to
be mounted read-only.

### 1.1 — The minimal deployment

```bash
git clone https://github.com/Pryxor/pryxor.git
cd pryxor
make init                # create .env and ./configs from templates
# edit .env with your secrets
# edit configs/pryxor.json with your policy
make up                  # build the image and start the container
```

`make up` builds the image if needed and starts the container in the
background. When it returns, the proxy is listening on
`http://127.0.0.1:8000`.

Three commands to verify:

```bash
make ps                  # is the container up?
make health              # does /v1/health answer?
make logs                # what does Pryxor say about the config?
```

The startup log prints exactly what was loaded:

```text
INFO pryxor.config: Loading config from folder /configs
INFO pryxor.engine: Loaded 2 sector(s): ['email', 'finance'] (default: email)
INFO pryxor.engine: Loaded 3 executor(s): ['send_email', 'pay_invoice', 'read_file']
INFO pryxor.engine: Loaded 1 notification route(s): ['slack_ops']
INFO pryxor.engine: Rate limiting enabled: 60/min, burst=10
```

If a sector or executor you expected is missing from that list, its
config file has a problem. The line above it will explain what.

### 1.2 — The production overlay

The base `docker-compose.yml` is intended for local development. For a
real deployment, layer the production overlay on top:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d
```

The overlay makes four changes.

- **No published port.** The base file binds to `127.0.0.1:8000`. The
  overlay removes the binding entirely and expects a reverse proxy on
  the same Docker network to reach Pryxor. This is the correct shape
  for production: Pryxor is not the front door.
- **Read-only root filesystem.** The container image runs with a
  read-only filesystem except for `/tmp` (a small tmpfs) and `/data`
  (the state volume). This prevents an exploited process from writing
  to disk.
- **Resource caps.** CPU and memory limits, so a misbehaving Pryxor
  cannot starve the host.
- **Restart policy.** `on-failure:5`, so the container restarts a few
  times if it crashes on boot, but does not loop forever.

### 1.3 — What the container mounts

| Host path | Container | Mode | Purpose |
|---|---|---|---|
| `./configs` | `/configs` | read-only | The policy folder. |
| Named volume `pryxor_data` | `/data` | read-write | The SQLite state database. |

The policy mount is read-only on purpose. Pryxor never writes to the
policy; a restart is required to pick up a change. This is not an
inconvenience, it is the safety property: a running instance uses a
fixed, reviewed policy.

The state volume is a named Docker volume, not a bind mount. Docker
manages ownership and permissions, which matters because the container
runs as uid `10001` (the `pryxor` user), not as root.

### 1.4 — Environment variables

| Variable | Default in image | Purpose |
|---|---|---|
| `PRYXOR_STATE_PATH` | `/data/pryxor_state.sqlite3` | State database. |
| `PRYXOR_CONFIG_DIR` | `/configs` | Policy folder. |
| `PRYXOR_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `PRYXOR_MAX_BODY_BYTES` | `65536` | Max accepted request body size. |
| `PRYXOR_METRICS_ENABLED` | `true` | Set to `false` to disable metrics. |
| `PRYXOR_DISPATCH_WORKER` | `true` | Set to `false` to disable the background notification worker. |
| `<SECRET_NAME>` | — | Any secret referenced by `secret_ref` in an executor, or `webhook_url_ref` in a notification route. |

Secrets referenced by `secret_ref` in executors **must be present in the
container environment**. The `env_file` entry in the compose file is
where they come from.

| `PRYXOR_ENCRYPTION_KEY` | — | Fernet key (32 bytes, base64-urlsafe). Encrypts the real parameters of held actions at rest. **Required.** If it changes, old holds become unreadable. Generate with `python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`. |

### 1.5 — Running without Docker

If you cannot use Docker, Pryxor runs as a normal Python service.

```bash
python -m venv .venv
source .venv/bin/activate      # or .venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt

export PRYXOR_CONFIG_DIR=/etc/pryxor/configs
export PRYXOR_STATE_PATH=/var/lib/pryxor/state.sqlite3
export BANK_API_TOKEN=sk_live_xxx

python pryxor_proxy.py
```

For a process manager, run uvicorn directly with a single worker:

```bash
uvicorn pryxor_proxy:app --host 127.0.0.1 --port 8000 --workers 1
```

The `--workers 1` is not a suggestion. See section 2.

---

## 2. The single-writer constraint

State lives in a single SQLite database, in WAL mode. This is robust
for a single instance and no more.

> **Do not run multiple Pryxor replicas against the same database.**
> You will hit `database is locked` under any nontrivial load, and the
> failures will be intermittent and hard to debug.

The container image runs `uvicorn` with `--workers 1` for exactly this
reason. Running two containers against the same `pryxor_data` volume is
not supported.

What this means in practice:

- **One Pryxor per policy.** If you have two teams with different
  policies, run two Pryxors, one per team, each with its own database.
- **Scale up, not out.** A single Pryxor on a well-provisioned node
  handles the load of a small-to-medium agent fleet. When it does not,
  the answer is a different backend, not more replicas of SQLite.
- **Horizontal scaling is a roadmap item**, not a today item. It
  requires a Postgres backend and a distributed locking strategy for
  the notification outbox. Both are planned.

The honest summary: one node, one database, one policy. That covers a
surprising amount of real usage, and it is the deployment the code was
built for.

---

## 3. Networking and TLS

The container binds to `127.0.0.1:8000` by default. **Do not expose
that port to the internet.** Pryxor speaks plain HTTP; TLS is not its
job.

The correct shape is a reverse proxy in front. Caddy is the simplest
to configure; nginx and Traefik both work.

### 3.1 — A minimal Caddyfile

```text
warden.internal {
    reverse_proxy pryxor:8000
    tls internal
}
```

`tls internal` uses Caddy's local CA. For a public hostname, replace it
with a real certificate (Caddy does this automatically with Let's
Encrypt if the hostname is public).

### 3.2 — What the reverse proxy must not do

- **Do not buffer the request body.** Pryxor has its own size limit
  (`PRYXOR_MAX_BODY_BYTES`). A reverse proxy that buffers the whole body
  before forwarding defeats that limit.
- **Do not rewrite the `X-Agent-Key` header.** It is the identity.
- **Do not strip the `X-Request-ID` header** if the client sends one.
  Pryxor uses it to correlate logs.

### 3.3 — Metrics exposure

`/metrics` is unauthenticated by design — that is the Prometheus
convention. It must not be reachable from the public internet. Two
options.

- Keep it on an internal Docker network that only your Prometheus
  scraper reaches.
- Protect it at the reverse proxy with an IP allowlist.

Do not rely on "nobody knows the URL". The URL is in this file.

---

## 4. Backups

All of Pryxor's durable state — holds, audit events, executions, keys,
and the notification queue — lives in a single SQLite database. Losing
it means losing the audit trail and every pending hold.

Pryxor ships two scripts for this: `scripts/backup.sh` and
`scripts/backup.ps1`. Both are safe to run while Pryxor is running.

### 4.1 — The backup script

```bash
make backup
# or, directly:
bash scripts/backup.sh
```

The script:

1. Takes a snapshot with `VACUUM INTO`. This is a **consistent** copy,
   safe under concurrent writes. It is not a `cp` of the live file,
   which would be corrupt.
2. Compresses the snapshot (`gzip`).
3. Rotates: keeps the last N backups and deletes older ones.

| Option | Default | Environment variable |
|---|---|---|
| `--state <path>` | `./pryxor_state.sqlite3` | `PRYXOR_STATE_PATH` |
| `--out <dir>` | `./backups` | `PRYXOR_BACKUP_DIR` |
| `--keep <n>` | `14` | `PRYXOR_BACKUP_KEEP` |
| `--no-gzip` | compress | — |

```bash
bash scripts/backup.sh \
  --state /data/pryxor_state.sqlite3 \
  --out /backup \
  --keep 30
```

Requirements: the `sqlite3` CLI must be installed on the host. On
Windows, install via `choco install sqlite`.

### 4.2 — Backing up from inside a running container

If you prefer not to install `sqlite3` on the host:

```bash
bash scripts/backup_docker.sh pryxor ./backups
```

The script runs `sqlite3` **inside** the container, copies the result
out, compresses it, and cleans up. The container already has `sqlite3`
because the Python image includes it.

### 4.3 — What a backup looks like

```text
backups/pryxor_backup_20260926T150000Z.sqlite3.gz
```

The timestamp is UTC. One file per run, compressed.

### 4.4 — Restoring

> **Stop Pryxor first.** Restoring while it runs will corrupt the
> database. There is no safe way to restore a live SQLite file.

```bash
make restore FILE=./backups/pryxor_backup_20260926T150000Z.sqlite3.gz
```

Or:

```bash
bash scripts/restore.sh ./backups/pryxor_backup_20260926T150000Z.sqlite3.gz
```

The script:

1. Asks for explicit confirmation (`type 'yes'`).
2. Decompresses the backup if needed.
3. Runs `PRAGMA integrity_check` and confirms the required tables are
   present (`holds`, `audit_events`, `outbox_events`). A file that is a
   valid SQLite database but not a Pryxor one is rejected.
4. **Moves the current database aside**
   (`*.before_restore_<timestamp>`) and cleans up stale WAL and SHM
   files.
5. Copies the backup into place.

Then start Pryxor. It reads the restored state on boot.

If something is wrong with the restore, the previous database is still
on disk under the `.before_restore_...` suffix. Rename it back and
restart.

### 4.5 — What you should back up

| Path | Why |
|---|---|
| The state database (`PRYXOR_STATE_PATH`) | Holds, audit, executions, keys, notifications. |
| The config folder (`PRYXOR_CONFIG_DIR`) | The policy. Usually in version control, but back it up anyway. |

The `.env` file is **not** part of a backup. Secrets should live in a
secret manager — Vault, AWS Secrets Manager, or the platform's own —
and be injected into the environment at deploy time.

### 4.6 — Retention and testing

Three things that are easy to skip and expensive to regret:

- **Schedule backups.** The scripts are not scheduled for you. A cron
  job or a platform scheduler is the right place.
- **Keep backups off the same volume.** A backup on the same disk as
  the state is a backup against accidental deletion, not against disk
  failure.
- **Test the restore.** A backup that has never been restored is an
  assumption. Once a quarter, restore into a scratch directory and
  confirm the hold count and audit tail look right.

---

## 5. Observability

Pryxor exposes Prometheus metrics at `/metrics`. It does not ship
distributed tracing or structured JSON logs. What it has is what this
section documents.

### 5.1 — Metrics

Metrics are enabled by default and become no-ops if `prometheus_client`
is not installed in the image, so Pryxor always runs. To turn them off
explicitly, set `PRYXOR_METRICS_ENABLED=false`.

The metric set is small and deliberate. Each one answers a specific
operational question.

| Metric | Type | Answers |
|---|---|---|
| `pryxor_tool_calls_total` | counter | How many calls, by status, tool, agent. |
| `pryxor_tool_call_duration_seconds` | histogram | How long the engine took to decide. |
| `pryxor_executions_total` | counter | How many real actions were attempted, by outcome. |
| `pryxor_execution_duration_seconds` | histogram | How long the real API took. |
| `pryxor_holds_total` | counter | How many holds were approved, rejected, expired. |
| `pryxor_holds_pending` | gauge | How many holds are waiting right now. |
| `pryxor_notifications_total` | counter | Notifications sent, failed, or dead. |
| `pryxor_notifications_pending` | gauge | How many notifications are queued. |
| `pryxor_rate_limit_hits_total` | counter | Calls refused by the rate limiter. |
| `pryxor_outbox_pending` | gauge | Side effects waiting to be processed. |
| `pryxor_agents_total` | gauge | Active agents. |
| `pryxor_policy_info` | gauge | Which sector is active and its version. |

All metric names start with `pryxor_`. That is the allowlist for a
Prometheus scrape config.

### 5.2 — A Prometheus scrape config

```yaml
scrape_configs:
  - job_name: pryxor
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ["pryxor:8000"]
```

If you are running the reverse proxy from section 3, the scrape target
is the proxy, not the Pryxor container directly.

### 5.3 — The four alerts worth having

Four alerts cover most of what can go wrong.

**Holds are piling up.**

```promql
pryxor_holds_pending > 50
```

A backlog of pending holds means nobody is reviewing them, or the
review is too slow. The agents that made those calls are blocked. This
is the single most important alert.

**The notification outbox is stuck.**

```promql
pryxor_outbox_pending > 100
```

Side effects — sector velocity updates, real executions after approval
— are queued and not being processed. Either the background worker is
down, or the outbox is failing to reach something it needs. Investigate
immediately: an approval that did not execute is worse than a refusal.

**A notification route is dead.**

```promql
rate(pryxor_notifications_total{status="dead"}[5m]) > 0
```

A route is failing after the maximum number of retries. Either the
webhook URL is wrong or the destination is gone. The notification will
never be sent; the hold itself is still safe, but nobody was told about
it.

**Real API latency is spiking.**

```promql
histogram_quantile(0.95,
  rate(pryxor_execution_duration_seconds_bucket[5m])
) > 5
```

The p95 latency of real executions is over five seconds. Either an
external API is slow, or an executor's timeout is misconfigured. Both
are worth knowing before they turn into timeouts.

### 5.4 — Logs

Logs go to **stdout** in plain text. The one exception: the MCP server
logs to stderr, because its stdout is reserved for JSON-RPC.

The log level is controlled by `PRYXOR_LOG_LEVEL`. `INFO` is the
default and the right choice for production. `DEBUG` is useful while
debugging a policy, and expensive in steady state.

Every log line includes a `request_id` when the request carried one.
The value comes from the incoming `X-Request-ID` header if present,
otherwise Pryxor generates one. The response echoes it back in the
`X-Request-ID` response header, so a client can correlate its log with
Pryxor's.

A single tool call produces three log lines with the same `request_id`:

```text
Intercepted | request_id=a75d1674e9584872 | agent=ops_agent | tool=pay_invoice
Decision    | request_id=a75d1674e9584872 | status=HOLD | reason=ABOVE_AUTO_LIMIT
Executed    | request_id=a75d1674e9584872 | tool=pay_invoice | status=SUCCESS
```

The three together are how you follow a call from arrival to result.

### 5.5 — Shipping logs

Logs are plain text, not JSON. That is deliberate for v1: it keeps the
runtime dependency-free and the logs readable on a terminal. The
trade-off is that ingestion into a log aggregator needs a small parser
on your side.

The simplest shape:

- Run the container with `--log-driver=json-file` (the default in the
  compose files) and ship the file.
- Or attach a Docker log driver that forwards to your aggregator
  directly (`fluentd`, `gelf`, `awslogs`, and so on).

The `request_id` is the key. Grep for it in your aggregator to see
everything that happened to a single call across all lines.

---

## 6. Hardening checklist

Before exposing Pryxor to any real traffic, go through this list. Each
item is short.

- [ ] **TLS in front.** Never expose port 8000 directly. Section 3.
- [ ] **`.env` is `chmod 600`,** owned by the deploy user, not in
  version control. Check with `ls -l .env`.
- [ ] **The `/data` volume is on a disk that is backed up.** Section 4.
- [ ] **`/metrics` is on an internal network** or behind an IP
  allowlist. Section 3.3.
- [ ] **Admin keys are treated as root.** There is no RBAC yet; every
  admin key is all-powerful. Rotate them like root passwords.
- [ ] **`rate_limit` is enabled.** The default in
  `configs.example/pryxor.json` is `60/min` with `burst: 10`. Tune it
  to your traffic.
- [ ] **`redaction` is enabled** if agents handle PII or secrets. The
  default pattern list covers passwords, API keys, card numbers, and
  IBANs. Add fields specific to your domain.
- [ ] **Backups are scheduled.** Section 4.6.
- [ ] **Restore has been tested** at least once. Section 4.6.
- [ ] **`KNOWN_LIMITATIONS.md` has been read.** It is the honest list of
  what Pryxor does and does not protect against. No hardening checklist
  replaces knowing the boundary.

---

## 7. Upgrades

Pryxor applies schema migrations automatically on startup. There is no
versioned migration framework yet, and there is no rollback beyond
restoring a backup.

The upgrade sequence:

```bash
# 1. Back up first. This is not optional.
make backup

# 2. Update the code or the image.
git pull
# or:
docker compose pull

# 3. Restart.
make up

# 4. Verify.
make health
make logs
```

If something is wrong after an upgrade, the correct move is to restore
the pre-upgrade backup with `make restore`. Do not attempt a manual
schema downgrade.

Before upgrading production, test the upgrade against a **copy** of
your state. The command is the same, the volume is different:

```bash
docker run --rm \
  -v $(pwd)/backups:/backups \
  -v /tmp/test-data:/data \
  alpine sh -c "gunzip -c /backups/pryxor_backup_*.sqlite3.gz > /data/pryxor_state.sqlite3"

docker compose run --rm \
  -v /tmp/test-data:/data \
  pryxor
```

If Pryxor starts cleanly against the copy, the real upgrade will too.

---

## 8. Troubleshooting

Five things that go wrong, and the first thing to check for each.

**The container starts but `make health` fails.**

Look at `make logs`. The most common cause is a malformed
`configs/pryxor.json` — a missing comma, a stray character. The log
line points at the file.

**Calls return `401 Unauthorized`.**

The `X-Agent-Key` header is missing, wrong, or the agent was revoked.
Re-register the agent (`make register AGENT=...`) and use the new key
exactly as it was printed.

**Calls return `429 Too Many Requests`.**

The rate limiter is engaged. Either the agent is flooding or the limit
is too low. Check `pryxor_rate_limit_hits_total` to see whether this is
one agent or all of them, and adjust
`rate_limit.requests_per_minute`.

**Calls succeed but nothing happens on the real system.**

Look at `pryxor_cli.py executions`. If the row is `FAILED`, the error is
in the `error` column. If there is no row at all, the executor was not
called — check that the tool is actually `APPROVED` and that the
executor's `url` is correct.

**`database is locked` appears in the logs.**

Two Pryxor instances are running against the same state database.
Section 2. Stop one of them.