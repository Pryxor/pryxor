# Glossary

The vocabulary used across Pryxor. If a term appears in the code, the
API, or the documentation, it is defined here.

---

## Decisions

**`APPROVED`**
The action passed authorization and policy evaluation. If an executor
is configured for the tool, Pryxor **executes** the real action; the
response carries the executor's result under `execution`.

**`HOLD`**
The action is plausible but requires **human approval** before it can
happen. The action is persisted (durably, with a TTL) and the agent
receives an `action_id`. A HOLD is a **terminal answer for the agent**:
do not retry. An operator approves or rejects it later.

**`BLOCKED`**
The action is rejected and will **never** be executed. The response
carries a `reason` (e.g. `UNSUPPORTED_TOOL`, `RECIPIENT_NOT_WHITELISTED`).

**`EXECUTION_FAILED`**
The action was **authorised** (`APPROVED`), but the real call to the
external system failed (timeout, 5xx, network error). The agent should
treat this as an error, not as a policy decision.

---

## Core concepts

**Agent**
Any AI program that calls tools. An agent is identified by an **agent
key** (`X-Agent-Key`), never by a value in the payload. Each agent has
a list of tools it is allowed to call.

**Admin / operator**
A human (or service) that manages keys and reviews holds. Identified by
an **admin key** (`X-Admin-Key`). The agent and admin registries are
strictly separate: an agent key can never act as an admin, and vice
versa.

**Tool call**
The agent's request to perform an action: a tool name plus arguments.
Pryxor normalizes it to a canonical `(tool_name, parameters)` pair
regardless of the payload shape it arrived in.

**Sector**
A **deterministic policy plugin** that decides `APPROVED` / `HOLD` /
`BLOCKED` for a tool call. A sector is two halves: **code** (in
`sectors/<name>.py`, only for the `code` kind) and **config** (in
`configs/sectors/<name>.json`). Built-in code sectors: `finance`,
`email`, `cloud`. A **declarative** sector is config-only (a `rules`
list, no Python).

**Executor**
The component that performs the **real action** on an external system,
holding the credential. Types: `mock` (tests, demos) and `http`. See
[policies/04-executors.md](policies/04-executors.md).

**Gateway pattern**
The architecture where Pryxor not only *decides* but also *executes*,
holding the credentials itself. The agent therefore never holds a
production secret and cannot bypass the decision.

**Normalization**
Turning the many accepted payload shapes (legacy, generic, nested LLM
tool-call) into one canonical `(tool_name, parameters)`. Identity is
never extracted from the payload.

**Policy**
The full configuration that governs decisions: authorization, sectors,
executors, notifications, rate limits, redaction. A **folder**
(`configs/`) of JSON files.

**Request ID**
A correlation identifier attached to every request. Taken from the
incoming `X-Request-ID` if present (so it can cross services),
otherwise generated; echoed in the response header and included in
logs.

---

## Durable state

**Hold / action**
A persisted pending action awaiting human approval. Carries an
`action_id` (e.g. `hold_0eda8977`), a TTL, and lifecycle timestamps.
Stored in the `holds` table.

**Outbox**
A durable queue of **side effects** to perform (sector velocity
updates, execution of an approved hold). An outbox event is written in
the **same transaction** as the state transition that produced it, so a
crash cannot lose it. On startup, pending events are replayed.

**Idempotency key**
A stable key that makes an action safe to repeat. A **client** may
supply one via the `Idempotency-Key` header; Pryxor also derives
stable keys internally (`direct:<uuid4>` for inline executions,
`hold:<action_id>` for executed holds). A repeated call with the same
key does not execute twice.

**Dedup key**
The key a sector uses to guarantee its own writes (e.g. velocity) are
not duplicated when an outbox event is replayed after a crash. Stable
per outbox event (e.g. `outbox:42`).

**CAS (compare-and-swap)**
The atomic `UPDATE … WHERE status = 'PENDING'` used to resolve holds.
It guarantees that, under concurrency, a hold is approved or rejected
**exactly once**.

**TTL (time-to-live)**
How long a hold stays approvable (`hold_ttl_minutes`). After it
lapses, the hold is `EXPIRED` and cannot be approved.

---

## Identity and secrets

**Agent key**
A per-agent secret (`pryxor_agent_<id>_<random>`) sent in
`X-Agent-Key`. Hashed at rest with PBKDF2-SHA256; shown only once at
registration.

**Admin key**
A per-admin secret (`pryxor_admin_<id>_<random>`) sent in
`X-Admin-Key`. Same lifecycle as an agent key, separate registry.

**Registration / revocation / rotation**
The key lifecycle, managed by `admin_cli.py`. Rotation issues a new key
and invalidates the old one while **preserving the identity id** (and
thus the audit history).

**Secret provider**
The abstraction Pryxor uses to read secrets (executor credentials,
webhook URLs). The default `EnvSecretProvider` reads from the
environment. See
[operations](operations.md#14-environment-variables).

**Redaction**
Replacing sensitive fields with `[REDACTED]` or a hash
(`sha256:<hex>`) **before storage** (holds, audit, executions). Values
are only masked at rest; in-memory processing uses the real values.

---

## Interfaces

**MCP (Model Context Protocol)**
The protocol Pryxor speaks to expose its tools to MCP clients (Claude
Desktop, Cursor, Zed…). Pryxor ships a standalone MCP server and an MCP
proxy mode. See [integrations](integrations.md#1-mcp-standalone-server).

**Adapter**
A thin wrapper that makes a framework's tool route through Pryxor
(LangChain, CrewAI, OpenAI Agents). The framework tool only
*proposes*; Pryxor decides and executes.

**SDK / client**
The Python client (`pryxor.Pryxor`, from the `pryxor` package) agents
use to call Pryxor. See [integrations](integrations.md#2-python-sdk).

**inputSchema**
The JSON Schema declared on an executor. Pryxor validates every call
against it **before** policy evaluation, and the MCP server publishes
it to the model so the model knows the exact fields.

**Proxy**
The Pryxor process itself: a FastAPI service that receives tool calls
from agents, evaluates them, and returns a decision. Configured by the
`PRYXOR_URL` environment variable on the client side, and by
`PRYXOR_CONFIG_DIR` / `PRYXOR_STATE_PATH` on its own side.

**Policy folder**
The `configs/` directory. Five small files plus two sub-folders
(`executors/`, `sectors/`). See
[policies/03-the-five-files.md](policies/03-the-five-files.md).

---

## Things this documentation mentions

**`X-Agent-Key`**
The HTTP header carrying an agent's API key. The only source of agent
identity. Any `agent_id` field in the request body is ignored.

**`X-Admin-Key`**
The HTTP header carrying an admin's API key. Required on every endpoint
under `/v1/holds`, `/v1/audit`, `/v1/executions`, `/v1/notifications`.

**`X-Request-ID`**
Optional correlation header. If the client sends one, Pryxor reuses it
and echoes it back. If not, Pryxor generates one.