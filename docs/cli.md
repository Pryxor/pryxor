# Command-line tools

Pryxor ships with two command-line tools. They solve two different
problems and are used by two different people.

| Tool | Purpose | Used by | Frequency |
|---|---|---|---|
| `admin_cli.py` | Manage agent and admin keys. | Whoever registers identities. | Occasionally. |
| `pryxor_cli.py` | Review holds, inspect the audit, list executions. | Whoever operates Pryxor day to day. | Regularly. |

Both are standalone Python scripts. Neither needs Pryxor to be running
to be useful — `admin_cli.py` talks directly to the state database, and
`pryxor_cli.py` talks to the proxy over HTTP. What each one needs is
documented in its section.

---

## 1. `admin_cli.py` — key management

`admin_cli.py` manages **identities**. It registers, lists, revokes, and
rotates agent and admin keys. It talks directly to the SQLite state
database, so it must run with access to the same `PRYXOR_STATE_PATH` as
the running proxy.

### 1.1 — Running it

Two ways. Inside the container is the recommended one:

```bash
docker compose exec pryxor python admin_cli.py list
```

From a local checkout, pointing at the same state database:

```bash
python admin_cli.py --state ./pryxor_state.sqlite3 list
```

The examples below use the local form. To run inside the container,
prefix with `docker compose exec pryxor` and drop `--state`.

### 1.2 — State location

The CLI reads the state DB path from `--state`. If not given, it falls
back to `PRYXOR_STATE_PATH`, then to `pryxor_state.sqlite3` in the
working directory.

```bash
python admin_cli.py --state /data/pryxor_state.sqlite3 list
```

If the CLI cannot find agents you know exist, this path is the first
thing to check. It must match the proxy's `PRYXOR_STATE_PATH`.

### 1.3 — Agent commands

**`register`** creates a new agent and prints its key **once**.

```bash
python admin_cli.py register <agent_id> [--label LABEL]
```

```bash
python admin_cli.py register support_bot --label "Support automation"
```

The response:

```json
{
  "agent_id": "support_bot",
  "api_key": "pryxor_agent_support_bot_xxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "pryxor_agent_support_bot_",
  "created_at": "2026-09-26T15:02:11.108431+00:00",
  "label": "Support automation"
}
```

The `api_key` is what the agent puts in the `X-Agent-Key` header. It is
**never shown again**. Store it in the agent's environment (or a secret
manager) as `PRYXOR_AGENT_KEY`.

Registering an id that already exists and is active fails. Use
`rotate` instead, or `revoke` and re-register if you want a clean slate.

**`list`** shows every registered agent:

```bash
python admin_cli.py list
```

Output includes the key prefix (not the key), the label, the creation
date, and the revocation date if any.

**`revoke`** marks an agent key unusable:

```bash
python admin_cli.py revoke <agent_id>
```

Any call using that key afterwards returns `401 Unauthorized`. The agent
record itself stays in the database — this matters for the audit trail,
which still refers to the agent by its id.

**`rotate`** issues a new key for an existing agent:

```bash
python admin_cli.py rotate <agent_id> [--label LABEL]
```

The previous key becomes invalid immediately. The `agent_id` does not
change, so the audit history stays intact — everything the agent did
before is still attributed to the same identity.

### 1.4 — Admin commands

Admin keys authenticate the operator endpoints: holds, audit,
executions, notifications. The lifecycle is the same as agent keys, in a
separate registry.

**`register-admin`**

```bash
python admin_cli.py register-admin <admin_id> [--label LABEL]
```

```bash
python admin_cli.py register-admin root --label "Ops lead"
```

Response:

```json
{
  "admin_id": "root",
  "api_key": "pryxor_admin_root_xxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "pryxor_admin_root_",
  "created_at": "2026-09-26T15:03:45.201847+00:00",
  "label": "Ops lead"
}
```

Store the key as `PRYXOR_ADMIN_KEY`. It is shown once.

**`list-admins`**

```bash
python admin_cli.py list-admins
```

**`revoke-admin`**

```bash
python admin_cli.py revoke-admin <admin_id>
```

**`rotate-admin`**

```bash
python admin_cli.py rotate-admin <admin_id> [--label LABEL]
```

### 1.5 — The three registries rule

Agent keys and admin keys are **separate registries**. There is no
overlap.

- An agent key cannot call any admin endpoint. A `GET /v1/holds` with
  `X-Agent-Key` returns `401`.
- An admin key cannot call `/v1/execute-tool`. A POST with
  `X-Admin-Key` returns `401`.

This is not a policy. It is a hard separation in the code. Registering
one does not grant the other.

### 1.6 — Rotation preserves history

Rotation is the safe operation. It replaces the secret without changing
the identity.

```text
before rotation:   agent_id=ops_agent, key=A
after rotation:    agent_id=ops_agent, key=B

the audit trail still says `ops_agent` for every call that used A
```

If you need to invalidate a key without issuing a new one — an agent
that should be gone — use `revoke`. If you need to rotate a compromised
key while keeping the agent active, use `rotate`.

### 1.7 — What the CLI prints where

Human-readable messages go to **stderr**. The machine-readable result
goes to **stdout**, as a single JSON object.

This means the CLI composes cleanly with `jq` and with shell scripting:

```bash
python admin_cli.py list | jq '.[] | select(.revoked_at == null) | .agent_id'
```

Only active agents, one per line. The `✅ Agent registered.` message
does not pollute the JSON.

---

## 2. `pryxor_cli.py` — operations

`pryxor_cli.py` is the operator's day-to-day tool. It talks to the
running proxy over HTTP, so it must be able to reach `PRYXOR_URL` and
must be given either an agent key or an admin key (or both), depending
on the command.

It is the CLI counterpart of the HTTP API. Every command it exposes
exists as an endpoint; the CLI is a convenience for humans.

### 2.1 — Running it

Inside the container:

```bash
docker compose exec pryxor python pryxor_cli.py actions list
```

From a local checkout:

```bash
python pryxor_cli.py actions list
```

### 2.2 — Configuration

| Option | Environment variable | Header | Default |
|---|---|---|---|
| `--base-url` | `PRYXOR_BASE_URL` | — | `http://127.0.0.1:8000` |
| `--api-key` | `PRYXOR_AGENT_KEY` | `X-Agent-Key` | — |
| `--admin-key` | `PRYXOR_ADMIN_KEY` | `X-Admin-Key` | — |

```bash
export PRYXOR_BASE_URL=http://127.0.0.1:8000
export PRYXOR_ADMIN_KEY=pryxor_admin_root_xxxxx
export PRYXOR_AGENT_KEY=pryxor_agent_my_agent_xxxxx
```

Output is JSON on stdout, so it pipes cleanly into `jq`. Error messages
go to stderr with a non-zero exit code.

### 2.3 — Holds

The most common operation. A hold is a call waiting for human approval;
the CLI is how you find them and decide them.

**List pending holds:**

```bash
python pryxor_cli.py actions list [--limit 50] [--offset 0]
```

```bash
python pryxor_cli.py actions list | jq '.holds[] | {action_id, agent_id, tool_name, reason}'
```

Each entry contains the `action_id`, the `agent_id` that made the call,
the `tool_name`, the `reason` (a stable code from the policy), and the
timestamps. The `parameters` field is what the agent sent, redacted
according to your `redaction` settings.

**Approve a hold:**

```bash
python pryxor_cli.py actions approve hold_0eda8977
```

This executes the real action exactly once, through the same executor
that would have run it if the call had been approved directly. If the
executor fails, the failure is recorded and the call is not retried.

**Reject a hold:**

```bash
python pryxor_cli.py actions reject hold_0eda8977
```

The action is never executed. The decision is written to the audit
trail with your admin identity as the actor.

In both cases, `actor_id` records who made the decision. This is what an
auditor looks at when they ask "who let this through?".

### 2.4 — Audit

The audit trail holds every decision Pryxor has made. This is the
operator's forensic view.

```bash
python pryxor_cli.py audit list
```

The output is a list of events, newest first. Each event has:

- `event_type` — `created`, `approved`, `rejected`, `expired`.
- `action_id` — the hold's identifier.
- `agent_id` — which agent made the call.
- `actor_id` — which admin decided it, when applicable.
- `payload` — event-specific details, including the `reason` code.
- `created_at` — an ISO 8601 timestamp in UTC.

A typical sequence for a single HOLD is two events: `created` when the
policy held the call, `approved` or `rejected` when a human decided.
An expired hold produces a third event, `expired`.

For deeper filtering, use the HTTP API directly. The
[HTTP API reference](api/http-api.md) covers pagination and the exact
shape of each event.

### 2.5 — Executions

Every real action Pryxor performed, with its result.

```bash
python pryxor_cli.py executions [--limit 100]
```

Each row shows the tool, the agent, the status (`PENDING`, `SUCCESS`,
`FAILED`), the HTTP status code the real endpoint returned, and the
payload if the call succeeded.

This is where you go when a call was approved but you are not sure it
actually happened. `SUCCESS` means the executor got a 2xx back from the
real API. `FAILED` means it did not, and `error` explains why.

### 2.6 — Sending a tool call

For testing and debugging, the CLI can submit a tool call as an agent.

```bash
python pryxor_cli.py tool send_payment \
  --params '{"amount": 100.0, "recipient": "Fournisseur_A"}'
```

It prints the raw Pryxor response — `APPROVED`, `HOLD`, or `BLOCKED` —
exactly as the SDK would return it. Use it to verify a policy change
without writing a client.

The `--params` value must be valid JSON on one line. On Windows
PowerShell, use single quotes around the outer string and double quotes
inside:

```powershell
python pryxor_cli.py tool send_payment --params '{\"amount\": 100.0, \"recipient\": \"Fournisseur_A\"}'
```

### 2.7 — Command reference

```text
usage: pryxor_cli.py [--base-url URL] [--api-key KEY] [--admin-key KEY] COMMAND ...

Commands:
  actions list [--limit N] [--offset N]   List holds.
  actions approve <action_id>             Approve a hold and execute the action.
  actions reject  <action_id>             Reject a hold.
  audit list                              Show the audit log.
  executions [--limit N]                  List executions.
  tool <tool_name> [--params JSON]        Send an authenticated tool call.
```

---

## 3. Exit codes

Both tools follow a small, consistent convention:

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A handled error — the id was not found, the key was already revoked, the HTTP call failed. |
| `2` | A usage error — a required argument was missing, the JSON was malformed. |

For scripting, the JSON is on stdout and the human message is on stderr,
so `2>/dev/null` gives you a clean, parseable response.

---

## 4. Which tool for which problem

A short lookup table.

| You want to… | Use |
|---|---|
| Create a new agent | `admin_cli.py register` |
| Create a new admin | `admin_cli.py register-admin` |
| See who is registered | `admin_cli.py list` / `list-admins` |
| Invalidate a lost key | `admin_cli.py revoke` |
| Rotate a key without losing history | `admin_cli.py rotate` |
| Approve a held call | `pryxor_cli.py actions approve` |
| Reject a held call | `pryxor_cli.py actions reject` |
| See what is waiting for review | `pryxor_cli.py actions list` |
| Investigate a past decision | `pryxor_cli.py audit list` |
| Check whether an approved action actually ran | `pryxor_cli.py executions` |
| Test a policy change | `pryxor_cli.py tool <name> --params '...'` |
| Manage notification routes | The HTTP API at `/v1/notifications` |

The two tools do not overlap. If a task requires a decision by a human,
it is `pryxor_cli.py`. If it modifies identities, it is `admin_cli.py`.