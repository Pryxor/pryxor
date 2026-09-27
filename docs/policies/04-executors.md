## 4. Executors — the tool catalog

**In this file:** [The shape of an executor](#41--the-shape-of-an-executor) ·
[Every field](#42--every-field) ·
[inputSchema](#43--the-inputschema-is-not-optional-in-practice) ·
[Templating](#45--templating) ·
[Authentication](#46--authentication) ·
[Retries](#47--retries) ·
[Mock executor](#48--the-mock-executor) ·
[Worked example](#49--a-worked-example-paying-an-invoice)

An executor is what turns an agent's *intention* into a real action on a
real system. It knows the URL, the auth, the shape of the arguments, and
how to retry. The agent knows none of this. That gap is the design.

Executors live in `configs/executors/`, one file per tool. The filename
(without `.json`) is the tool name the agent calls:
`executors/send_email.json` defines the tool `send_email`.

### 4.1 — The shape of an executor

Every executor has four kinds of field:

- **Identity** — `type`, and for HTTP `method`, `url`.
- **Presentation** — `description`, `inputSchema`, `sector`.
- **Execution** — `auth`, `headers`, `body`, `retry`, `timeout_seconds`.
- **Optional** — everything else.

The minimal HTTP executor:

```json
{
  "type": "http",
  "url": "https://api.example.com/v1/action",
  "body": { "value": "{{ parameters.value }}" }
}
```

The same executor with everything spelled out:

```json
{
  "type": "http",
  "method": "POST",
  "url": "https://api.example.com/v1/action",
  "timeout_seconds": 10,
  "sector": "ops",
  "description": "Perform a thing.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "value": { "type": "string" }
    },
    "required": ["value"],
    "additionalProperties": false
  },
  "auth": { "type": "bearer", "secret_ref": "EXAMPLE_TOKEN" },
  "headers": { "X-Trace-Id": "{{ idempotency_key }}" },
  "body": {
    "value": "{{ parameters.value }}",
    "idempotency_key": "{{ idempotency_key }}"
  },
  "retry": { "attempts": 2, "backoff_seconds": 0.5 }
}
```

### 4.2 — Every field

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | `"mock"` | `"http"` performs an HTTP request. `"mock"` returns the parameters back (useful for tests and demos). |
| `method` | string | `"POST"` | HTTP method. |
| `url` | string | — | Target URL. Required for `type: "http"`. |
| `timeout_seconds` | number | `10` | Per-attempt timeout. |
| `sector` | string | *(global default)* | Which sector evaluates this tool. Overrides `pryxor.json`'s `sector`. |
| `description` | string | — | Human-readable. Also used as the MCP tool description when this tool is exposed over MCP. |
| `inputSchema` | object | — | JSON Schema for the arguments. Enforced before policy evaluation. |
| `auth` | object | `{ "type": "none" }` | How to authenticate the outbound request. |
| `headers` | object | `{}` | Static headers. Values support templating. |
| `body` | object | `{}` | Request body. Values support templating. |
| `retry` | object | `{ "attempts": 0 }` | Retry policy for transient failures. |

### 4.3 — The `inputSchema` is not optional in practice

Pryxor validates every incoming call against the executor's
`inputSchema` **before** the sector runs. If the schema is declared, a
malformed call — a missing required field, a wrong type, an unexpected
extra field — is rejected with `INVALID_ARGUMENTS` and never reaches the
rule engine.

This matters for a specific reason. Imagine a `send_email` tool whose
sector only checks `to` and `subject`. Without an `inputSchema`, an
agent could smuggle a `bcc` field through and have the executor forward
it. The sector's check passed — the schema never saw it.

With this:

```json
"inputSchema": {
  "type": "object",
  "properties": {
    "to":      { "type": "string" },
    "subject": { "type": "string" },
    "body":    { "type": "string" }
  },
  "required": ["to", "subject", "body"],
  "additionalProperties": false
}
```

the call with a `bcc` field is rejected before the sector sees it. The
sector only has to reason about valid calls.

**If you omit `inputSchema` entirely**, Pryxor publishes a strict empty
schema for the tool when it is exposed over MCP:
`{ "type": "object", "properties": {}, "additionalProperties": false }`.
For calls made directly through `/v1/execute-tool`, no validation is
performed against the arguments.

Write the schema. Always.

### 4.4 — `additionalProperties: false`

The single most important line in any executor schema. It tells the
validator that fields not declared in `properties` are forbidden. That
is what stops an agent from smuggling a field past the sector.

Set it to `false` unless you have a specific reason not to. If your
downstream API tolerates extra fields, that is the API's choice, not the
policy's.

### 4.5 — Templating

Four contexts are available inside `url`, `headers`, and `body`:

| Syntax | Value |
|---|---|
| `{{ parameters.<field> }}` | A tool-call argument the agent provided. |
| `{{ agent_id }}` | The authenticated agent's id. |
| `{{ tool_name }}` | The tool name. |
| `{{ idempotency_key }}` | The stable idempotency key for this execution. |

Example:

```json
"body": {
  "amount":          "{{ parameters.amount }}",
  "recipient":       "{{ parameters.recipient }}",
  "idempotency_key": "{{ idempotency_key }}",
  "requested_by":    "{{ agent_id }}"
}
```

Templating is a string substitution. It does not perform type coercion.
If the target API expects a number and you pass a string, the API will
decide what to do — this is not Pryxor's job.

`Content-Type: application/json` and `Idempotency-Key: <key>` are always
set on the outbound request unless you override them.

### 4.6 — Authentication

The `auth` block tells Pryxor how to sign the outbound request. The
secret is never sent to the agent — it is read at execution time.

```json
"auth": { "type": "bearer", "secret_ref": "BANK_API_TOKEN" }
```

| `auth.type` | Header produced |
|---|---|
| `"bearer"` | `Authorization: Bearer <secret>` |
| `"basic"` | `Authorization: Basic <base64(secret)>` — the secret is `user:password` |
| `"header"` | `<header_name or X-API-Key>: <secret>` |
| `"none"` or absent | No auth header |

`secret_ref` is the **name of an environment variable**, not the value.
Set it in your deployment environment:

```bash
export BANK_API_TOKEN=sk_live_xxx
```

The value is read from the process running Pryxor, never sent to the
agent, and never stored in the config folder.

For `type: "header"`, `header_name` selects the header:

```json
"auth": { "type": "header", "header_name": "X-Api-Key", "secret_ref": "EXAMPLE_TOKEN" }
```

**Where the secret lives is your decision.** By default, Pryxor reads
from the environment (`.env` in dev, a secret manager in production).
The `secrets.provider` key in `pryxor.json` selects the backend. Today
`"env"` is the only provider; the abstraction is there so a Vault or
KMS provider can be added without changing your executors.

### 4.7 — Retries

The `retry` block retries transient failures — network errors, HTTP
5xx, and HTTP 429. A 4xx other than 429 is treated as final and is not
retried.

```json
"retry": { "attempts": 2, "backoff_seconds": 0.5 }
```

- `attempts` — the number of **additional** attempts, not total. `2`
  means up to three tries.
- `backoff_seconds` — linear backoff. The wait before attempt *n* is
  `backoff_seconds * n`. `0.5` gives waits of 0.5s, then 1.0s.

Retries are safe because the request carries an `Idempotency-Key`. If
your downstream API honors that header, the retry will not duplicate the
real action. If it does not, a retry could cause a duplicate. This is
why the header exists — use it where the API supports it.

### 4.8 — The mock executor

`type: "mock"` returns the parameters back to the caller without
performing any action. It is useful for three things:

- Testing a policy without an external service.
- A demo where you want to show decisions but not real effects.
- A placeholder while you wire a real API later.

```json
{ "type": "mock" }
```

The response is always:

```json
{
  "success": true,
  "status_code": 200,
  "result": {
    "mock": true,
    "tool_name": "send_email",
    "agent_id": "ops_agent",
    "idempotency_key": "direct:...",
    "parameters": { "to": "alice@company.local" }
  }
}
```

### 4.9 — A worked example: paying an invoice

The invoice tool from a real policy. Three things matter: the schema
constrains the arguments, the idempotency key travels with the request
so a retry does not double-pay, and the amount is available to the
sector as a number.

```json
{
  "type": "http",
  "method": "POST",
  "url": "https://api.bank.example.com/v1/payments",
  "sector": "finance",
  "description": "Send a payment to a recipient.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "amount": {
        "type": "number",
        "exclusiveMinimum": 0
      },
      "recipient": {
        "type": "string"
      },
      "reference": {
        "type": "string",
        "maxLength": 140
      }
    },
    "required": ["amount", "recipient"],
    "additionalProperties": false
  },
  "auth": {
    "type": "bearer",
    "secret_ref": "BANK_API_TOKEN"
  },
  "headers": {
    "Idempotency-Key": "{{ idempotency_key }}"
  },
  "body": {
    "amount": "{{ parameters.amount }}",
    "recipient": "{{ parameters.recipient }}",
    "reference": "{{ parameters.reference }}"
  },
  "retry": {
    "attempts": 2,
    "backoff_seconds": 0.5
  }
}
```

A matching sector for this tool would hold anything above a threshold:

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "hold-large-payments",
      "when": { "tool": "pay_invoice", "amount_gt": 1000 },
      "then": { "hold": "ABOVE_AUTO_LIMIT", "message": "Above the limit." }
    }
  ]
}
```

The sector reads `amount` as a number because the schema declared it as
`"type": "number"`. If the schema had declared it as a string, the
condition `amount_gt` would fail to parse and the rule would not match.

**This is the practical rule.** If a rule reads a field numerically, the
schema must declare that field as `number`. If a rule reads a field as a
list, the schema must declare it as `array`. The schema is the contract;
the sector trusts it.

### 4.10 — What the agent never sees

The agent sends this:

```json
{
  "tool_name": "pay_invoice",
  "parameters": {
    "amount": 800,
    "recipient": "Fournisseur_A",
    "reference": "inv-001"
  }
}
```

The agent never sees the URL, the token, the retry policy, or the
idempotency key Pryxor generates for the call. If the agent is
compromised and tries to call the bank API directly, it has neither the
URL nor the credential. That is the boundary.

Two more things the agent never gets back from the executor:

- The authorization header Pryxor added to the outbound request.
- The full outbound request body, if the executor transformed it.

The agent sees the *result* of the real action, nothing more.