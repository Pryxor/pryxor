# Security Policy

## Threat model

Pryxor assumes:
- The proxy is the **only** entry point for tool calls in production.
- Agent API keys are **secrets**, stored on the agent side, never logged.
- The state DB (`pryxor_state.sqlite3`) is protected by filesystem permissions.

Pryxor does **NOT** protect against:
- **An agent that bypasses the proxy entirely.** The security boundary is the
  set of actions routed through Pryxor. If an agent can reach a real system by
  another path, that path is outside the boundary.
- A compromised agent host, where the attacker holds a valid agent key.
- Multi-instance deployments sharing a single database (SQLite is single-writer).
- Admin endpoints (`/v1/holds`, `/v1/audit`) are protected by separate admin
  keys, but there is no per-admin RBAC or session expiry yet.
- **Prompt injection itself.** Pryxor stops the consequences of a bad tool call;
  it does not detect an injection.

> The full list of current boundaries is maintained in
> [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md). Read it before deploying.

## Authentication

- Agents authenticate with a per-agent API key sent in the `X-Agent-Key` header.
- Operators authenticate with a separate admin key sent in the `X-Admin-Key`
  header. The two registries are distinct: an agent key can never act as an
  admin, and vice versa.
- The `agent_id` in the request body is **ignored** and replaced with the
  authenticated identity.
- Keys are hashed with PBKDF2-SHA256 (100,000 iterations, per-key salt) and the
  plaintext is shown only once, at registration.
- Keys are revoked or rotated via `admin_cli.py` (`revoke`, `revoke-admin`,
  `rotate`, `rotate-admin`).

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Open a **private security
advisory** on the repository (Security → Advisories) rather than a public issue.

Include a description of the issue and its impact, the steps to reproduce, and
any relevant logs or proof of concept. We will acknowledge your report as
quickly as possible and keep you informed as we investigate and prepare a fix.

Do **not** open a public issue for a security vulnerability.

## Supported versions

Pryxor is at an early stage. Security fixes are applied to the latest release on
the default branch. There are no long-term-support branches yet.