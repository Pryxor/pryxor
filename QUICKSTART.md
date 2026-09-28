# Quickstart

Get from zero to a blocked call and a held call in under ten minutes.

By the end of this page you will have:

- Pryxor running locally.
- An agent registered.
- A policy that approves internal emails and holds external ones.
- Seen a real HOLD, approved it, and watched the real action happen.

No prior Pryxor knowledge is assumed. Only Docker and a shell.

---

## Before you start

You need two things.

- **Docker** (version 24 or later) and **Docker Compose v2**.
- **An empty directory** where Pryxor will keep its state.

You do **not** need Python, pip, or any runtime on the host. The
container image carries everything.

To verify Docker is installed:

```bash
docker --version
docker compose version
```

If either command fails, install Docker Desktop (or Docker Engine on
Linux) and come back.

---

## 1. Get Pryxor

```bash
git clone https://github.com/Pryxor/pryxor.git
cd pryxor
```

You are now in the repository root. Everything below runs from here.

---

## 2. Create the local config folder

```bash
make init
```

This creates two things:

- **`.env`** — a file for secrets, mode `600` on Unix.
- **`configs/`** — the policy folder, copied from `configs.example/`.

You will edit both in the next two steps.

> If `make` is not available, the underlying commands are simple. On
> Windows with Git for Windows installed, `choco install make` is enough.
> Otherwise, run `cp .env.example .env && cp -r configs.example configs`
> from a bash shell.

---

## 3. Set the secrets

Open `.env` in a text editor.

For the **email demo** used in this quickstart, **no secret is required**.
The `send_email` executor posts to `httpbin.org` and does not use any
credential.

You can leave `.env` empty (or just keep the comments).  
If you later switch to the finance sector (`send_payment`), you will need:
```
BANK_API_TOKEN=replace_me_with_any_value
```
Save the file and close the editor.

---

## 4. Point the policy at the demo

Open `configs/pryxor.json` and make sure the default sector is `email`:

```json
{
  "sector": "email",
  "hold_ttl_minutes": 60,
  "secrets": { "provider": "env" }
}
```

Now open `configs/agents.json` and allow the agent we are about to register:

```json
{
  "allowed_actions": {
    "ops_agent": ["send_email"]
  }
}
```

Save both files.

---

## 5. Look at the executor

Open `configs/executors/send_email.json`. This file describes how the
`send_email` tool actually works: which URL it calls, which auth it
uses, what shape the arguments must have.

```json
{
  "type": "http",
  "method": "POST",
  "url": "https://httpbin.org/post",
  "sector": "email",
  "description": "Send an email.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "to":      { "type": "string" },
      "subject": { "type": "string" },
      "body":    { "type": "string" }
    },
    "required": ["to", "subject", "body"],
    "additionalProperties": false
  },
  "body": {
    "to":      "{{ parameters.to }}",
    "subject": "{{ parameters.subject }}",
    "body":    "{{ parameters.body }}"
  }
}
```

Two things worth noting before moving on.

- The **URL** lives here, in Pryxor's policy folder. The agent will
  never see it. That is the point of the whole system.
- The **inputSchema** declares the three fields the tool accepts.
  Anything outside this shape is rejected before any rule runs.

For this walkthrough, the URL points to `httpbin.org`, which echoes
back what we send. It requires outbound internet access. If you are
behind a strict firewall, change `"url"` to `"http://127.0.0.1:8000/v1/health"`
and confirm the call still returns a 200 — the point of the walkthrough
is the decision, not the destination.

---

## 6. Look at the sector

Open `configs/sectors/email.json`. This is the rule file: the file that
decides whether a call is APPROVED, HELD, or BLOCKED.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "allow-internal-emails",
      "when": {
        "tool": "send_email",
        "to_domain_in": ["company.local"]
      },
      "then": { "approve": true }
    },
    {
      "name": "hold-external-emails",
      "when": { "tool": "send_email" },
      "then": {
        "hold": "EXTERNAL_EMAIL",
        "message": "External recipient requires human approval."
      }
    }
  ]
}
```

Rules run in order, and the first match wins. The first rule approves
emails to `company.local`. The second holds everything else that reaches
it. If the two rules were swapped, every email — including internal ones
— would be held, because the second rule matches all of them.

**« After `make init` these should already be correct. Just verify them. »**

---

## 7. Start Pryxor

```bash
make up
```

This builds the image and starts the container. It takes a minute the
first time. When it returns, the proxy is listening on
`http://127.0.0.1:8000`.

Confirm it is up:

```bash
make health
```

Expected output:

```json
{ "status": "ok", "service": "Pryxor Engine" }
```

If you see this, Pryxor is running and read the policy folder. Look at
the logs if you want to be sure:

```bash
make logs
```

You should see lines like:

```text
INFO pryxor.config: Loading config from folder /configs
INFO pryxor.engine: Loaded 1 sector(s): ['email'] (default: email)
INFO pryxor.engine: Loaded 1 executor(s): ['send_email']
```

If `send_email` is missing from the list, the executor file has a JSON
syntax error. Fix it, restart with `make restart`, and check again.

---

## 8. Register an agent

An agent is a name and an API key. Register the one we allowed in
`agents.json`:

```bash
make register AGENT=ops_agent
```

The command prints a JSON object containing the new agent's key:

```json
{
  "agent_id": "ops_agent",
  "api_key": "pryxor_agent_ops_agent_xxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "pryxor_agent_ops_agent_",
  "created_at": "2026-09-26T15:02:11.108431+00:00",
  "label": "ops_agent"
}
```

**Copy the `api_key` now.** It is shown once and never again. Store it
in your shell for the rest of this walkthrough:

```bash
export PRYXOR_AGENT_KEY=pryxor_agent_ops_agent_xxxxxxxxxxxxxxxxxxxxxxxx
export PRYXOR_URL=http://127.0.0.1:8000
```
> **Windows (PowerShell):** use `$env:NAME = "value"` instead of `export NAME=value`.
---

## 9. Send an internal email — should be approved

```bash
curl.exe -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email",
       "parameters":{"to":"alice@company.local","subject":"Hi","body":"Hello."}}' \
  | python -m json.tool
```
> Prefer `curl.exe` (not the `curl` alias) or `Invoke-RestMethod`.

Expected output, abridged:

```json
{
  "status": "APPROVED",
  "reason": "APPROVED",
  "message": "Action authorized.",
  "execution": {
    "success": true,
    "idempotency_key": "direct:9f2c1a4b6e8d0f3a5c7b9e1d2f4a6c8e",
    "status_code": 200,
    "result": {
      "json": {
        "to": "alice@company.local",
        "subject": "Hi",
        "body": "Hello."
      },
      "url": "https://httpbin.org/post"
    }
  }
}
```

Three things to notice.

- `status: "APPROVED"` — Pryxor decided the call was allowed, and the
  executor performed it.
- `execution.idempotency_key` — a stable key Pryxor generated for this
  execution. Retrying with the same key will not re-execute.
- `execution.result` — the response from the real endpoint. Here,
  `httpbin` echoes back what we sent. In production this is your mail
  API's real response.

---

## 10. Send an external email — should be held

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email",
       "parameters":{"to":"bob@external.com","subject":"Hi","body":"Hello."}}' \
  | python -m json.tool
```

Expected output:

```json
{
  "status": "HOLD",
  "action_id": "hold_0eda8977",
  "reason": "EXTERNAL_EMAIL",
  "message": "External recipient requires human approval. This action is pending human approval (action_id=hold_0eda8977). Do not retry.",
  "retry": false,
  "quarantine_payload": {
    "agent_id": "ops_agent",
    "tool_name": "send_email",
    "parameters": {
      "to": "bob@external.com",
      "subject": "Hi",
      "body": "Hello."
    }
  }
}
```

The call did not go through. Nothing was sent to `bob@external.com`. It
is sitting in Pryxor's store, waiting for a human. Note the
`action_id` — we will use it in the next step.

Two things to notice.

- `retry: false` — the message to the agent is: do not retry. A HOLD
  is a terminal answer. Retrying creates more pending holds, not a
  different outcome.
- `quarantine_payload` — a copy of the call Pryxor captured, so the
  agent can reference it. This is what the agent sent; the stored copy
  inside Pryxor's state is redacted according to your `redaction`
  settings.

---

## 11. Approve the hold

The hold needs an admin key. Create one now:

```bash
make register-admin NAME=root
```

The command prints a second JSON object with a `pryxor_admin_...` key.
Copy it and export it in the same shell:

```bash
export PRYXOR_ADMIN_KEY=pryxor_admin_root_xxxxxxxxxxxxxxxxxxxxxxxx
```

Now approve the hold. Use the `action_id` from the previous step —
`hold_0eda8977` in the example above:

```bash
python -c "import os, requests; \
  r = requests.post('http://127.0.0.1:8000/v1/holds/hold_0eda8977/approve', \
                    headers={'X-Admin-Key': os.environ['PRYXOR_ADMIN_KEY']}); \
  print(r.status_code); print(r.json())"
```

If you have a local Python with `requests` installed, this is fine. If
not, use `curl`:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/holds/hold_0eda8977/approve \
  -H "X-Admin-Key: $PRYXOR_ADMIN_KEY" \
  | python -m json.tool
```

Expected output:

```json
{
  "status": "APPROVED",
  "action_id": "hold_0eda8977",
  "message": "Hold hold_0eda8977 approved for execution.",
  "approved_at": "2026-09-26T15:14:22.108431+00:00",
  "agent_id": "ops_agent",
  "tool_name": "send_email",
  "actor_id": "root",
  "parameters": {
    "to": "bob@external.com",
    "subject": "Hi",
    "body": "Hello."
  },
  "execution": {
    "success": true,
    "idempotency_key": "hold:hold_0eda8977",
    "status_code": 200,
    "result": {
      "json": {
        "to": "bob@external.com",
        "subject": "Hi",
        "body": "Hello."
      },
      "url": "https://httpbin.org/post"
    },
    "error": null
  }
}
```

Two things to notice.

- `actor_id: "root"` — the admin who approved the call is recorded in
  the audit trail. If someone asks "who let this through", the answer
  is written down.
- `execution.idempotency_key: "hold:hold_0eda8977"` — this time the
  key is derived from the hold's `action_id`, and is stable. If Pryxor
  restarts between the approval and the execution, the same key replays
  and the real endpoint does not see the action twice.

---

## 12. Look at the audit trail

Everything that happened is recorded. List the last few audit events:

```bash
curl -s "http://127.0.0.1:8000/v1/audit?limit=5" \
  -H "X-Admin-Key: $PRYXOR_ADMIN_KEY" \
  | python -m json.tool
```

You will see, for the hold we just approved:

```json
{
  "audit_events": [
    {
      "event_type": "approved",
      "action_id": "hold_0eda8977",
      "agent_id": "ops_agent",
      "actor_id": "root",
      "created_at": "2026-09-26T15:14:22.108431+00:00",
      "payload": {
        "approved_at": "2026-09-26T15:14:22.108431+00:00",
        "actor_id": "root",
        "actor_type": "admin"
      }
    },
    {
      "event_type": "created",
      "action_id": "hold_0eda8977",
      "agent_id": "ops_agent",
      "created_at": "2026-09-26T15:13:55.201847+00:00",
      "payload": {
        "reason": "EXTERNAL_EMAIL",
        "message": "External recipient requires human approval."
      }
    }
  ]
}
```

Two events: the hold was created, and it was approved by `root`. The
`reason` on the first event is the string from the rule's `then` block —
that is how you trace a decision back to the rule that produced it.

---

## 13. Stop and clean up

```bash
make down
```

This stops the container. State is kept in a named volume
(`pryxor_data`) so a restart preserves everything.

To stop **and** delete all state — holds, audit, executions, keys:

```bash
make clean
```

The command asks for confirmation. It is the right thing to run when
you are done experimenting.

---

## What you just did

In ten minutes you:

- started Pryxor with a policy that grants one agent access to one
  tool;
- watched an internal email pass through to the executor and be
  delivered;
- watched an external email be held, without any action taken;
- approved the hold as an admin and watched the real action happen;
- read the audit trail that recorded both decisions.

The agent you sent the calls as never saw the URL in
`executors/send_email.json`. It never saw the token in `.env`. It only
saw the words `APPROVED` and `HOLD`. That is the whole system.

---

## Where to go next

- **Understand the boundary** —
  [concepts](docs/concepts.md) explains why Pryxor is built this way.
- **Write a real policy** —
  [policies/](docs/policies/README.md) walks through every file.
- **Wire your actual agent** —
  [integrations](docs/integrations.md) covers MCP, the Python SDK, and
  framework adapters.
- **Deploy for real** —
  [operations](docs/operations.md) covers Docker, backup, metrics, and
  the hardening checklist.
- **Know the limits** —
  [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) is the honest list of
  what Pryxor does and does not protect against.

When something in this walkthrough does not behave as documented,
[the FAQ](docs/faq.md) is the first place to look. It collects the
questions that come up most often, with the short answer for each.
