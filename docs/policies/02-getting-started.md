## 2. Your first policy in 5 minutes

We are going to protect a single tool: `send_email`. The rule will be
simple and realistic: **any email to an external domain must be held for
human approval**. Emails to `company.local` pass through.

By the end of this section you will have written four small files and
seen Pryxor return a real decision on a real HTTP call.

### 2.1 — Create the folder

```text
configs/
├── pryxor.json
├── agents.json
├── executors/
│   └── send_email.json
└── sectors/
    └── email.json
```

Create it in the root of your Pryxor deployment (next to
`pryxor_proxy.py`). Pryxor auto-detects `configs/` — you do not need to
configure anything.

### 2.2 — `configs/pryxor.json`

Global settings. For this first policy we only need to name the default
sector and set a hold TTL.

```json
{
  "sector": "email",
  "hold_ttl_minutes": 60
}
```

- `sector` — the sector every tool is evaluated against unless its
  executor overrides it. We only have one sector here, `email`.
- `hold_ttl_minutes` — how long a held action stays approvable before it
  expires. Sixty minutes is a sane default.

### 2.3 — `configs/agents.json`

Who may call what. In production this is per-agent; for the demo we
register one agent, `ops_agent`.

```json
{
  "allowed_actions": {
    "ops_agent": ["send_email"]
  }
}
```

If an agent not listed here calls `send_email`, the call is `BLOCKED`
before any rule runs. Authorization is the outer wall; the sector is the
inner one.

### 2.4 — `configs/executors/send_email.json`

The executor is what actually performs the action. The agent will never
see the URL inside this file — that is the point.

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

Three fields to understand:

- `url` — where the real action happens. For this walkthrough we point
  it at `httpbin.org/post`, which echoes back whatever we send. In
  production this is your mail API or an SMTP bridge.
- `inputSchema` — the shape of the arguments. Pryxor validates every
  call against it **before** the sector runs. A call missing `to`, or
  carrying a field not declared here, is rejected as
  `INVALID_ARGUMENTS` and never reaches the sector's checks.
- `body` — the request Pryxor sends to the URL. `{{ parameters.to }}`
  is replaced with the value the agent provided. `{{ idempotency_key }}`
  is also available here, and so is `{{ agent_id }}`.

### 2.5 — `configs/sectors/email.json`

The rules. This file says *whether* a call should happen.

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

Two things to notice:

- Rules are evaluated **in order**, and the **first match wins**. The
  first rule approves internal emails; the second holds everything else
  that reaches it. If we swapped them, the hold would fire first and no
  email would ever be approved.
- `to_domain_in` extracts the part after the `@` and compares it to a
  list of domains. This is not the same as `to_in`, which compares the
  entire address string. Both exist; they answer different questions.

### 2.6 — Start Pryxor and register the agent

In one terminal:

```bash
python pryxor_proxy.py
```

In another:

```bash
python admin_cli.py register ops_agent
```

The command prints an API key **once**, of the form
`pryxor_agent_ops_agent_...`. Copy it now — it will not be shown again.

Export it in the shell where you will send the test calls:

```bash
export PRYXOR_AGENT_KEY=pryxor_agent_ops_agent_xxxxx
export PRYXOR_URL=http://127.0.0.1:8000
```

### 2.7 — Send two calls

**An internal email — should be approved:**

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email",
       "parameters":{"to":"alice@company.local","subject":"Hi","body":"Hello."}}'
```

Expected response:

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
      "args": {},
      "data": "{\"to\": \"alice@company.local\", \"subject\": \"Hi\", \"body\": \"Hello.\"}",
      "headers": { "Accept": "*/*", "Content-Type": "application/json" },
      "json": {
        "body": "Hello.",
        "subject": "Hi",
        "to": "alice@company.local"
      },
      "url": "https://httpbin.org/post"
    }
  }
}
```

Three things to notice in that response:

- `idempotency_key` — a stable key Pryxor generated for this execution.
  A retry with the same key will not re-execute the action. If the agent
  had sent an `Idempotency-Key` header, that value would be used instead.
- `status_code` — the HTTP status the real endpoint returned. `200` here
  because `httpbin.org` accepted the request.
- `result` — the real payload from the executor. This is what the
  executor got back from the URL.

**An external email — should be held:**

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email",
       "parameters":{"to":"bob@external.com","subject":"Hi","body":"Hello."}}'
```

Expected response:

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

Two things to notice in that response:

- `retry: false` — the message to the agent is: do not retry. A HOLD is
  a terminal answer. Retrying just creates more pending holds.
- `quarantine_payload` — a copy of the call Pryxor captured so the agent
  can reference it. This is what the agent sent; nothing more. The copy
  that Pryxor stores (in the hold and the audit trail) is redacted at
  rest according to your `redaction` settings.

The second call did not go through. It is sitting in Pryxor's store,
waiting for a human. Nothing was sent to `bob@external.com`.

### 2.8 — Approve the hold

Grab the `action_id` from the previous response, and approve it with an
admin key:

```bash
python admin_cli.py register-admin root    # only once, save the key
export PRYXOR_ADMIN_KEY=pryxor_admin_root_xxxxx

python pryxor_cli.py actions approve hold_0eda8977
```

The action is now executed. The response includes the real execution
result:

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

Look at `execution.idempotency_key` — it is `hold:hold_0eda8977`, not
`direct:...`. The key is stable, derived from the hold's `action_id`. If
Pryxor restarts between the approval and the execution, the same key
replays, and the real endpoint does not see the action twice.

If you call `python pryxor_cli.py executions` you will see the execution
row. If you call `python pryxor_cli.py audit list`, you will see the full
lifecycle: `created`, `approved`, and the execution — with the admin who
approved it recorded as `actor_id`.

### 2.9 — What you just built

- An **executor** that knows the real URL and holds the credential.
- An **authorization rule** that says which agent may call which tool.
- A **policy** that approves internal emails and holds external ones.
- An **approval workflow** with a durable queue and an audit trail.

The agent you wired up here never saw the URL in
`executors/send_email.json`. It never saw an SMTP token. It only saw the
word `HOLD` — and it cannot turn that into an email no matter how it
tries. That is the entire point.

From here, the natural next questions are: how do executors actually
speak to real APIs, how do I write richer rules, and how do I know a rule
fired. [The-five-files](03-the-five-files) walks through each of the five
files in detail, then [Sectors The-rules](05-sectors.md) gives you the
full rule language.