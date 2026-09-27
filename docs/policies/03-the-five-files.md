## 3. The five files

**In this file:** [The rule of thumb](#the-rule-of-thumb) ·
[`pryxor.json`](#31--pryxorjson) ·
[`agents.json`](#32--agentsjson) ·
[`notifications.json`](#33--notificationsjson) ·
[`executors/<tool>.json`](#34--executorstooljson) ·
[`sectors/<name>.json`](#35--sectorsnamejson) ·
[The full minimal policy](#36--the-full-minimal-policy)

We have already seen the five files in the previous section. This section
is the reference: what each file is for, what belongs in it, and what
does not.

The rule of thumb for all five: **one concern per file**. If you are
tempted to add a rule to `pryxor.json` because it is convenient, resist
it. The separation is what makes a policy readable six months later.

```text
configs/
├── pryxor.json          ← global settings
├── agents.json          ← who may call what
├── notifications.json   ← where to notify
├── executors/
│   └── <tool>.json      ← how to perform each tool
└── sectors/
    └── <name>.json      ← the rules
```

The rest of this section walks through each file. The two that carry the
most weight — `executors/` and `sectors/` — get their own sections later
([4](#34--executorstooljson) and [5](#35--sectorsnamejson)).

### 3.1 — `pryxor.json`

Global settings. Small on purpose. Everything that is not global goes
elsewhere.

```json
{
  "sector": "email",
  "hold_ttl_minutes": 60,
  "secrets": { "provider": "env" },
  "rate_limit": {
    "enabled": true,
    "requests_per_minute": 60,
    "burst": 10
  },
  "redaction": {
    "enabled": true,
    "default_mode": "REDACTED",
    "default_patterns": ["password", "api_key", "bearer", "card_number", "iban"]
  }
}
```

Every key, what it does, and its default:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `sector` | string | `"finance"` | The sector every tool is evaluated against unless its executor overrides it. |
| `hold_ttl_minutes` | int | `60` | How long a HOLD stays approvable before it expires. |
| `secrets.provider` | string | `"env"` | Where secrets are read from. `env` reads from the process environment. |
| `rate_limit.enabled` | bool | `false` | Enable the per-agent rate limiter. |
| `rate_limit.requests_per_minute` | int | — | Sustained request budget per agent. |
| `rate_limit.burst` | int | — | Short burst allowance above the sustained rate. |
| `redaction.enabled` | bool | `false` | Redact sensitive fields before storage. |
| `redaction.default_mode` | string | `"REDACTED"` | `REDACTED` (`[REDACTED]`) or `HASH` (`sha256:<hex>`). |
| `redaction.default_patterns` | list | — | Field-name patterns redacted by default. |
| `redaction.tools.<tool>.fields` | object | — | Per-tool, per-field overrides. |

Two keys deserve a note.

**`sector`** names the sector used when an executor does not declare
one. If every executor declares its sector explicitly (which is what we
recommend once you have more than one), this default is a safety net.
Every tool call goes through *some* sector — this is which one when
nothing else applies.

**`rate_limit`** protects Pryxor from an agent that floods it — a
broken loop, a stuck retry, or a compromised agent. It is per-agent, not
global: one misbehaving agent does not affect the others. `60/min` with
a burst of `10` is a reasonable starting point. Enable it in
production; leave it off in a lab if you want to run tests fast.

### 3.2 — `agents.json`

Authorization. Who may call what.

```json
{
  "allowed_actions": {
    "ops_agent": ["send_email", "read_file"],
    "finance_agent": ["pay_invoice"]
  }
}
```

A tool not listed for an agent is `BLOCKED` with reason
`UNSUPPORTED_TOOL`, and the call never reaches a sector or an executor.

Two important details:

- **Identity comes from the `X-Agent-Key` header, never from the request
  body.** An agent cannot claim to be another agent by putting an
  `agent_id` in the payload. The header is authoritative; the payload
  field, if present, is ignored.
- The key you register with `admin_cli.py register <agent_id>` is what
  determines the entry that applies. If you rotate the key, the
  `agent_id` stays the same, so this file does not need to change.

### 3.3 — `notifications.json`

Where Pryxor sends a message when a HOLD is created, approved, rejected,
or expires.

```json
{
  "routes": {
    "slack_ops": {
      "type": "slack",
      "webhook_url_ref": "SLACK_OPS_WEBHOOK",
      "events": ["hold.created", "hold.approved", "hold.rejected"],
      "mention": "<!channel>"
    },
    "soc_audit": {
      "type": "webhook",
      "url": "https://soc.example.com/pryxor",
      "auth": { "type": "bearer", "secret_ref": "SOC_TOKEN" },
      "events": ["hold.created", "hold.approved", "hold.rejected"]
    }
  }
}
```

Three route types are supported: `slack`, `teams`, `webhook`. Each
route subscribes to a list of events. A route not subscribed to an event
never receives it.

`webhook_url_ref` and `secret_ref` are **names of environment
variables**, not values. The actual URL or token is read at delivery
time from the process environment. This is what keeps secrets out of
your config folder.

To disable notifications, leave `routes` empty:

```json
{ "routes": {} }
```

This is what we recommend for a first local install. Wiring a real Slack
or Teams webhook is a separate, deliberate step.

One caveat: delivery is **at-least-once**. If Pryxor sends a
notification, the receiver acknowledges it, and the acknowledgement is
lost, the notification is retried. Make your downstream handler
idempotent if duplicates would hurt.

### 3.4 — `executors/<tool>.json`

One file per tool. The filename (without `.json`) is the tool name the
agent calls. `executors/send_email.json` defines the tool
`send_email`.

An executor knows three things: the URL, how to authenticate, and what
shape the arguments must have.

```json
{
  "type": "http",
  "method": "POST",
  "url": "https://api.mail.example.com/v1/send",
  "sector": "email",
  "description": "Send an email.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "to":      { "type": "string" },
      "subject": { "type": "string", "maxLength": 200 },
      "body":    { "type": "string", "maxLength": 20000 }
    },
    "required": ["to", "subject", "body"],
    "additionalProperties": false
  },
  "auth": { "type": "bearer", "secret_ref": "MAIL_API_TOKEN" },
  "body": {
    "to":      "{{ parameters.to }}",
    "subject": "{{ parameters.subject }}",
    "body":    "{{ parameters.body }}"
  }
}
```

Every field, and how to use it, is described in
[Section 4](#4-executors--the-tool-catalog). The only thing to carry
away from here: **the URL and the auth secret live in this file, never
in the agent.** That is the whole point.

### 3.5 — `sectors/<name>.json`

The rules. This is where "should this call happen" is decided.

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

The full rule language — conditions, actions, order, and the operators
you can use — is in [05-sectors](05-sectors.md). If your domain
needs logic JSON cannot express, you write a small Python class instead;
that path is described in
[06-code-sector](06-code-sectors.md).

### 3.6 — The full minimal policy

For reference, here is the smallest complete policy that does something
useful: one agent, one tool, one rule.

```text
configs/
├── pryxor.json
├── agents.json
├── executors/
│   └── send_email.json
└── sectors/
    └── email.json
```

Four files. No `notifications.json` — that one is optional and defaults
to no routes. With just these four, Pryxor will authenticate, authorize,
validate, evaluate, and execute.

The two files that carry the most weight — the executor catalogue and
the rule language — are the subject of the next two sections.