# FAQ

Short answers to the questions that come up most often. For definitions
of the terms used here, see the [glossary](glossary.md). For the longer
explanations behind any of these answers, the linked page is the place.

---

## The basics

**What is Pryxor, in one sentence?**

A runtime security layer that sits between an AI agent and the systems
it can act on: it intercepts every tool call, decides deterministically
what may happen, and — when authorised — performs the real action
itself, so the agent never holds a credential.

**How is it different from an allowlist or a firewall?**

An allowlist says yes or no. Pryxor adds a **middle outcome** (`HOLD`)
for actions that need a **human**, and it **executes** the action with
credentials it controls. The agent proposes; Pryxor decides *and* acts.

**Does it replace my agent framework?**

No. It integrates with existing agents — MCP clients, the Python SDK,
LangChain, CrewAI, OpenAI Agents, or plain HTTP. See
[integrations](integrations.md).

**Where do the credentials live?**

On the Pryxor side, in the executor's environment. The agent never
receives them. This is the "gateway pattern", and it is why a
compromised agent cannot call the real system.

**Does it work with the LLM I use?**

Yes, if your agent can call a tool, it can call Pryxor. Pryxor does not
interact with the LLM directly; it intercepts the tool calls the agent
makes, whatever generated them.

---

## Decisions and flow

**What do `APPROVED`, `HOLD`, and `BLOCKED` mean?**

- `APPROVED` — allowed; executed by Pryxor.
- `HOLD` — needs human approval; persisted, not executed yet.
- `BLOCKED` — rejected; never executed.

See the [glossary](glossary.md#decisions) for the full definitions,
including `EXECUTION_FAILED`.

**Should the agent retry when it gets a `HOLD`?**

No. A `HOLD` is a **terminal answer**: the action is queued for a human.
Retrying just creates more pending holds. The agent receives an
`action_id` it can report.

**What happens when an operator approves a hold?**

Pryxor executes the real action **exactly once** through the same
executor, records the result, and marks the hold `APPROVED`. If a crash
happens between the approval and the execution, the action is replayed
from the durable outbox on restart — without double-executing, thanks
to a stable idempotency key.

**Can the same hold be approved twice?**

No. Approvals use an atomic compare-and-swap, so exactly one approval
wins even under concurrency. A second attempt returns the current
status with an "already approved" message.

**What happens if a hold expires?**

Past its TTL (`hold_ttl_minutes`), a hold becomes `EXPIRED` and can no
longer be approved. The agent must submit a new call if the action is
still wanted.

**Why did I get `BLOCKED` with a generic message?**

For unauthorized tools, Pryxor deliberately returns a generic message
and hides the distinction between "this tool doesn't exist" and "you're
not allowed to use it", so a compromised agent cannot enumerate your
tool catalog. The precise reason is recorded in the audit trail.

**Can an agent see the URL it is calling?**

No. The agent sends a `tool_name` and `parameters`. The URL lives in
the executor, inside the policy folder. The agent never sees it, and
cannot reach the real system without Pryxor.

---

## Configuration

**Where does Pryxor read its configuration?**

From a **folder** (`PRYXOR_CONFIG_DIR`, default `configs/`). See
[configuration](../policies/03-the-five-files.md).

**I changed the policy — why isn't it live?**

Policy is file-based and loaded at startup. Edit the file, then restart
Pryxor (`make restart`). Hot reload is not implemented.

**How do I allow a new tool for an agent?**

1. Declare the tool in `executors/<tool>.json` (with its `inputSchema`).
2. Add the tool to the agent's list in `agents.json`.
3. Restart Pryxor.

**How do I add rules without writing Python?**

Use a **declarative sector**: create `configs/sectors/<name>.json` with
`"type": "declarative"` and a `rules` list. See
[sectors](../policies/05-sectors.md).

**Why do my executor secrets come back empty?**

The default secret provider reads from the **proxy's environment**. A
secret referenced by `secret_ref` (e.g. `BANK_API_TOKEN`) must be
present in that environment — in Docker, via `env_file`. See
[operations](operations.md#14-environment-variables).

**Why does my rule not fire?**

Four causes, in order: a rule above it matched first; the field is not
in the parameters; the field is a string where the rule reads a number;
the operator is misspelled. The full walkthrough is in
[debugging](../policies/08-debugging.md).

---

## Keys and access

**How do I create an agent?**

```bash
python admin_cli.py register my_agent
```

The key is printed **once** — store it as `PRYXOR_AGENT_KEY`. See
[cli](cli.md).

**I lost an agent's key. Can I recover it?**

No — keys are stored hashed. Issue a new one with `rotate`:

```bash
python admin_cli.py rotate my_agent
```

**Does rotating a key break the audit history?**

No. The identity id is unchanged, so all past audit entries remain
meaningful.

**Can an agent key approve holds?**

No. Agent and admin identities are separate registries. Approving a
hold requires `X-Admin-Key`.

**Is there per-admin RBAC?**

Not yet. Every admin key is all-powerful. Treat it like a root
credential. Fine-grained roles are on the [roadmap](../ROADMAP.md).

**How many agents can I register?**

As many as you want. There is no per-agent cost, and no limit in the
code. Each agent has one key and one entry in `agents.json`.

---

## Operations

**Can I run multiple Pryxor replicas?**

No. State is a single SQLite database (WAL mode); multiple writers will
hit `database is locked`. Run **one instance** per database. Horizontal
scaling is on the [roadmap](../ROADMAP.md).

**Is `/metrics` authenticated?**

No, by design (Prometheus scraping convention). Expose it only on an
internal network or protect it at the reverse proxy. See
[operations](operations.md#33-metrics-exposure).

**How do I back up the state?**

`make backup` (or `scripts/backup.sh` / `scripts/backup.ps1`). It takes
a consistent snapshot with `VACUUM INTO`, safe while Pryxor runs. See
[operations](operations.md#4-backups).

**What is the smallest production deployment?**

One Docker container, one policy folder mounted read-only, one named
volume for state, a reverse proxy in front for TLS. See
[operations](operations.md#1-deployment).

**How do I know a tool call is safe to retry after a network error?**

Pass an `Idempotency-Key`. A repeated call with the same key and the
same action will not execute twice. See the
[HTTP API](api/http-api.md) and the
[Python SDK](integrations.md#2-python-sdk).

**Does Pryxor work offline?**

Yes, if your executors point to systems reachable from Pryxor. The
demo policy in the Quickstart posts to `httpbin.org` for convenience,
but any URL works, including one on your own network.

---

## Security and scope

**Does Pryxor stop prompt injection?**

It stops the **consequences** of a bad tool call — an injected
instruction still cannot execute an unauthorized action — but it does
not detect the injection itself, and it cannot protect actions that
bypass Pryxor entirely. The security boundary is the set of calls
routed through Pryxor.

**What if the agent bypasses Pryxor and calls the system directly?**

That path is outside the boundary. Give the agent **no** credential for
the real system; route all access through Pryxor, which holds the
credential. This is what makes the boundary meaningful.

**Does Pryxor see my prompts or my model's outputs?**

No. Pryxor sees **tool calls** — a tool name and a dict of parameters.
It does not see the conversation, the system prompt, or the model's
reasoning. It only sees what the agent decides to do.

**Where is the audit trail stored?**

In the same SQLite database as the holds and executions, in an
append-only table. Every decision is written in the same transaction as
the state change, so a crash cannot leave one without the other.

**Is it production-ready?**

It is an early-stage open-source release with a production-minded
architecture. It runs well as a single node behind a reverse proxy.
Multi-tenancy, RBAC, SSO, and horizontal scale are future work. See
[KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md).

---

## Troubleshooting

**`401 Invalid or revoked agent key`**

The `X-Agent-Key` is missing, wrong, or revoked. Re-register or rotate
the agent.

**`413 Request body too large`**

The body exceeds `PRYXOR_MAX_BODY_BYTES` (default 64 KB). Raise the
limit if legitimate calls need it.

**`429 Rate limit exceeded`**

The per-agent rate limit is enabled and was hit. Tune
`rate_limit.requests_per_minute` or the burst, or the agent is
misbehaving.

**`Unknown sector '<name>'`**

An executor declares a sector that does not resolve. Either add
`configs/sectors/<name>.json` or fix the executor's `sector` field.
Pryxor refuses to start rather than silently apply the wrong policy.

**Holds pile up in `pryxor_holds_pending`**

Nobody is approving them (or not fast enough). Review them with
`pryxor_cli.py actions list` and approve/reject. Consider notifications
so the team is alerted.

**`pryxor_outbox_pending` keeps growing**

Side effects are not being processed — check the executor's target API
and the notification routes. See
[operations](operations.md#53-the-four-alerts-worth-having).

**My executor returns "Auth setup failed: Required secret … is not configured"**

The named secret is not in the proxy's environment. Add it and restart.

---

## Contributing

**How do I contribute?**

See [CONTRIBUTING.md](../CONTRIBUTING.md). For extension work, start
with the [sectors](../policies/05-sectors.md),
[executors](../policies/04-executors.md), or
[adapters](integrations.md) page.