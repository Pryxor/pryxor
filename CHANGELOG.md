# Changelog

All notable changes to Pryxor are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The **runtime** (this repository) and the **Python SDK** (`pryxor` on PyPI) are
released together and share a version number.

---

## [Unreleased]

### Added

- `KNOWN_LIMITATIONS.md` — what the runtime does and does not protect today.

---

## [0.1.0] — 2026-09-25

The first public release: a working runtime security core, usable on a single node.

### Added

**Runtime enforcement**

- Tool-call interception at `POST /v1/execute-tool`, with agent identity from the
  `X-Agent-Key` header (never from the payload).
- Deterministic policy evaluation with explicit `APPROVED / HOLD / BLOCKED`
  outcomes.
- Argument validation against each executor's JSON Schema, before policy
  evaluation.
- Request body size limit and a request-id middleware (`X-Request-ID`).

**Controlled execution (gateway)**

- Real HTTP executors plus a mock executor; the agent never holds the credential.
- Credential isolation through a pluggable secret provider (env by default).
- Idempotency keys, execution retries, and a durable execution record.

**Human approval**

- Durable holds with a TTL, and an atomic compare-and-swap so a hold is approved
  or rejected exactly once.
- A durable outbox: side effects are written in the same transaction as the state
  transition and replayed after a crash.

**Audit & operations**

- Durable audit trail of the full decision lifecycle.
- PII / secret redaction at storage time.
- Slack, Teams, and webhook notifications with a retrying outbox.
- Prometheus metrics at `/metrics`.
- Admin and client CLIs (key management, hold review, audit, executions).

**Extensibility**

- Sectors: built-in `finance`, `email`, and `cloud` deterministic policy plugins,
  plus **declarative** (rules-only) sectors with no Python.
- Sector framework in `sectors/_framework/`, with explicit `type` and a startup
  guard that refuses to launch on ambiguous sector routing.

**Integrations**

- A standalone MCP server and an MCP proxy.
- A Python SDK (`pryxor`), published on PyPI: client, framework adapters
  (LangChain, CrewAI, OpenAI Agents), and the MCP integrations, with opt-in
  extras.

**Deployment**

- A hardened, non-root Docker image and Docker Compose files for local,
  production, and smoke-test use.
- Backup and restore scripts for the state database.

### Security

- Separate agent and admin key registries, so an agent key can never act as an
  admin (and vice versa). Keys are hashed with PBKDF2-SHA256 and shown once.

### Known limitations

See [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md). This release is designed for a
single protected node, not yet a multi-tenant, high-availability platform.

### Changed

- **Blocked responses use a single generic reason.** `UNSUPPORTED_TOOL` and
  `AGENT_NOT_AUTHORIZED_FOR_TOOL` are replaced by `NOT_AUTHORIZED`. The
  distinction was a side channel that let an agent probe for the tool catalog
  one call at a time. The distinction remains in the audit trail, not in the
  agent-facing response.
- **API keys are hashed with SHA-256 instead of PBKDF2.** Keys are 256-bit
  random values; PBKDF2 was defensive against low-entropy secrets it never
  saw. Verification is now ~100,000× faster per request.
- **Real hold parameters are encrypted at rest.** The `parameters` column is
  redacted for display; the `params_enc` column holds the real, unredacted
  values, encrypted with a Fernet key. Requires `PRYXOR_ENCRYPTION_KEY`.

[Unreleased]: https://github.com/Pryxor/pryxor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Pryxor/pryxor/releases/tag/v0.1.0