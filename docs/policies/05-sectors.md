## 5. Sectors — the rules

**In this file:** [Two kinds of sector](#51--two-kinds-of-sector) ·
[Shape of a rule](#52--the-shape-of-a-rule) ·
[Order matters](#53--order-matters-first-match-wins) ·
[Block, hold, approve](#54--block-hold-approve) ·
[The when language](#55--the-when-language) ·
[Operator reference](#57--operator-reference) ·
[What does not exist](#58--what-does-not-exist) ·
[Recipes](#59--recipes) ·
[When a rule does not fire](#511--when-a-rule-does-not-fire)

A sector decides *whether* a call should happen. It receives a
normalized tool call — an authenticated `agent_id`, a `tool_name`, and a
dict of `parameters` — and returns one of three things:

- **APPROVED** — the call may proceed.
- **HOLD** — the call is queued for human review.
- **BLOCKED** — the call is refused.

Nothing else. A sector does not execute, does not authenticate, does not
touch credentials. It reads the call and answers a question.

A sector lives in `configs/sectors/<name>.json`. The name must match the
`sector` declared in the executors that use it. One sector can serve many
tools; one tool belongs to exactly one sector.

### 5.1 — Two kinds of sector

A sector is declared with a `type` field. There are two kinds, and they
answer the same question with different tools.

```json
{
  "type": "declarative",
  "rules": [ ... ]
}
```

```json
{
  "type": "code"
}
```

- **`declarative`** — the rules are a JSON list. No Python. This is what
  you want 90% of the time. Everything in this section applies to it.
- **`code`** — the rules are a Python class under `sectors/<name>.py`.
  Use this when the logic does not fit the JSON language: a state
  machine, an external lookup, a calculation that spans more than one
  parameter. [Code sectors](06-code-sectors.md)
  covers it.

The rest of this section is about `declarative`.

### 5.2 — The shape of a rule

A declarative sector is a list of rules. Each rule has a name, a
condition (`when`), and an action (`then`).

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "hold-large-payments",
      "when": {
        "tool": "pay_invoice",
        "amount_gt": 1000
      },
      "then": {
        "hold": "ABOVE_AUTO_LIMIT",
        "message": "Above the automatic limit."
      }
    }
  ]
}
```

Three things to notice.

- **`name`** is free-form. It appears in the audit trail so you can tell
  which rule fired. Choose names that describe the *intent* of the rule,
  not its condition: `hold-large-payments` reads better than
  `amount-rule-1`.
- **`when`** is a set of conditions. All of them must hold for the rule
  to match. It is a logical AND, never an OR.
- **`then`** is the action. Exactly one of `block`, `hold`, or `approve`.

### 5.3 — Order matters: first match wins

Rules are evaluated top to bottom. The **first rule whose `when`
matches** is applied, and evaluation stops. Later rules are never
consulted for that call.

This is the single most common source of confusion. Look at this:

```json
{
  "rules": [
    {
      "name": "hold-external-emails",
      "when": { "tool": "send_email" },
      "then": { "hold": "EXTERNAL_EMAIL" }
    },
    {
      "name": "allow-internal-emails",
      "when": {
        "tool": "send_email",
        "to_domain_in": ["company.local"]
      },
      "then": { "approve": true }
    }
  ]
}
```

The first rule matches every `send_email` call — including internal
ones. The second rule never fires. Every email ends up held.

The correct order is the opposite: **the most specific rule first**.

```json
{
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
      "then": { "hold": "EXTERNAL_EMAIL" }
    }
  ]
}
```

**Rule of thumb.** Order your rules from most specific to most general.
Blocks first, then holds, then approvals, then the catch-all. If a
rule's `when` is just `{"tool": "..."}` and it is not last, it is
swallowing everything below it.

### 5.4 — Block, hold, approve

The three actions, and when to use each.

**`block`** — the call is refused. The agent receives an error and never
sees the URL. Use this for calls that should *never* happen:

- A tool the agent has no business calling in this context.
- A destructive action outside a maintenance window.
- A payment above an absolute hard cap.
- A request that matches a known attack pattern.

```json
{ "block": "DESTRUCTIVE_TOOL", "message": "Destructive tools are never allowed." }
```

**`hold`** — the call is queued. A human approves or rejects. Use this
for calls that *might* be legitimate but need a human decision:

- A payment above a soft threshold.
- An email to an external domain.
- A production change during a change freeze.
- A new vendor the policy has never seen.

```json
{ "hold": "ABOVE_AUTO_LIMIT", "message": "Above the limit; needs a human." }
```

**`approve`** — the call passes. Use this for allowlists that need to
precede a broader rule:

```json
{ "approve": true }
```

An `approve` rule with no message is fine. The decision reason will be
`APPROVED` and the rule name appears in the audit.

**The difference between block and hold is not severity.** It is whether
a human can say yes. If the answer is ever "yes, under a special
circumstance", it is a hold. If the answer is always "no", it is a
block.

### 5.5 — The `when` language

Every condition is a key-value pair. The key names the field and the
operator; the value is what to compare against.

Three families of conditions:

- **Tool and agent identity** — `tool`, `tool_in`, `tool_not_in`,
  `agent_id`, `agent_id_in`.
- **Parameter conditions** — `<field>_gt`, `<field>_matches`,
  `<field>_in`, and their siblings. The `<field>` is a key in the
  call's `parameters`.
- **Numeric shortcuts** — `amount_gt`, `amount_gte`, and their siblings
  operate directly on `parameters.amount`.
- **Velocity and rate** — `sum_last_<N>h_gt`, `count_last_<N>h_gt`.

The full operator table is in [Section 5.7](#5.7-operator-reference).
Everything below builds up to it.

### 5.6 — The four condition families, by example

**Tool and agent.** Which tool is being called, by which agent.

```json
{ "tool": "send_email" }
{ "tool_in": ["send_email", "send_sms"] }
{ "tool_not_in": ["delete_database"] }
{ "agent_id": "ops_agent" }
{ "agent_id_in": ["ops_agent", "finance_agent"] }
```

**Parameters — comparison.** Numeric fields use `_gt`, `_gte`, `_lt`,
`_lte`, `_eq`, `_ne`. `amount_gt` is a shortcut for
`parameters.amount_gt`:

```json
{ "amount_gt": 1000 }
{ "amount_lte": 5000 }
```

For non-numeric fields, use strict equality:

```json
{ "environment": "production" }
```

**Parameters — membership.** A field whose value is one of a list, or
not one of a list:

```json
{ "recipient_in": ["Fournisseur_A", "Fournisseur_B"] }
{ "recipient_not_in": ["Blacklisted_Vendor"] }
{ "status_in": ["pending", "active"] }
```

**Parameters — pattern matching.** Regex on a string field:

```json
{ "subject_matches": "(?i)password|secret|key" }
{ "path_matches": "^/etc/|^/root/" }
{ "body_not_matches": "(?i)confidential" }
```

**Domains.** `_domain_in` and `_domain_not_in` extract the part after
the `@` from an email field and compare it to a list. These are what you
want for email allowlists, not `_in` — `to_in` compares the whole
address string.

```json
{ "to_domain_in": ["company.local", "company.com"] }
{ "from_domain_not_in": ["trusted-partner.example"] }
```

If the field has no `@`, the condition does not match. An email address
that is not really an email address never passes a domain rule.

**Velocity.** A count or a sum over a rolling window of hours. The
number before the `h` is the window length.

```json
{ "sum_last_24h_gt": 5000 }
{ "count_last_1h_gt": 10 }
```

`sum_last_24h_gt` sums the `amount` field of all *approved* calls that
match the rest of the rule, over the last 24 hours. `count_last_1h_gt`
counts the *number* of approved calls instead of summing their amount.

Only approved calls are counted. A held call is not included until a
human approves it. A blocked call is never included.

### 5.7 — Operator reference

Every condition operator, in one table. If an operator is not in this
table, it does not exist.

| Operator | Applies to | Value | Meaning |
|---|---|---|---|
| `tool` | — | string | The tool name equals the value. |
| `tool_in` | — | list of strings | The tool name is in the list. |
| `tool_not_in` | — | list of strings | The tool name is not in the list. |
| `agent_id` | — | string | The authenticated agent id equals the value. |
| `agent_id_in` | — | list of strings | The agent id is in the list. |
| `amount_gt` | `parameters.amount` | number | `amount > value` |
| `amount_gte` | `parameters.amount` | number | `amount >= value` |
| `amount_lt` | `parameters.amount` | number | `amount < value` |
| `amount_lte` | `parameters.amount` | number | `amount <= value` |
| `amount_eq` | `parameters.amount` | number | `amount == value` |
| `amount_ne` | `parameters.amount` | number | `amount != value` |
| `<field>` | `parameters.<field>` | any | Strict equality. |
| `<field>_in` | `parameters.<field>` | list | The value is in the list. |
| `<field>_not_in` | `parameters.<field>` | list | The value is not in the list. |
| `<field>_matches` | `parameters.<field>` | regex | The value matches the pattern. |
| `<field>_not_matches` | `parameters.<field>` | regex | The value does not match the pattern. |
| `<field>_domain_in` | `parameters.<field>` | list of domains | The domain part is in the list. |
| `<field>_domain_not_in` | `parameters.<field>` | list of domains | The domain part is not in the list. |
| `sum_last_<N>h_gt` | any approved call in the sector | number | Sum of `amount` over the last N hours is greater than the value. |
| `count_last_<N>h_gt` | any approved call in the sector | number | Count of approved calls over the last N hours is greater than the value. |

If a condition names a field that is not present in the call's
`parameters`, the condition **fails to match**, and the rule is skipped.
A rule with `{"recipient_in": [...]}` applied to a call with no
`recipient` key simply does not fire. This is deliberate: a rule that
checks a field should not accidentally match a call that lacks it.

All conditions inside one `when` are joined with AND. There is no OR.
If you need an OR, write two rules — the first one that matches will
fire.

### 5.8 — What does not exist

For clarity, the following are *not* part of the language today. If you
find yourself needing one, the honest answer is: write a code sector
([Code sectors](06-code-sectors.md)).

- **No `or`.** Two conditions inside a `when` are ANDed, never ORed. To
  get an OR, write two rules.
- **No `not` at the rule level.** You cannot negate a whole rule.
  Individual conditions can be negative (`_not_in`, `_not_matches`,
  `_domain_not_in`), but the `when` block as a whole cannot.
- **No cross-field comparison.** You cannot say "`amount` is less than
  `budget`". You can compare a field to a constant, not to another
  field.
- **No arithmetic.** No addition, no multiplication, no percentages.
  `sum_last_24h_gt` is the only aggregate.
- **No time-of-day conditions.** No "only on weekends", no "only between
  9am and 5pm".
- **No external lookups.** A declarative rule cannot call an API to
  check whether a vendor is active. It reads the call and the config.
- **No fallthrough with a value.** Every rule is a terminal decision.
  There is no "if X then Y, otherwise continue to the next rule with
  the same data". The first match wins, period.

Each of these is by design. A rule language that grows without limit
becomes a programming language, and a policy becomes code that nobody
reviews. The line is drawn here on purpose.

### 5.9 — Recipes

Nine recipes that cover most real policies. Each is complete and can be
copied directly into a `configs/sectors/<name>.json`.

**Block a tool unconditionally.**

```json
{
  "name": "block-delete",
  "when": { "tool": "delete_database" },
  "then": { "block": "FORBIDDEN_TOOL", "message": "This tool is disabled." }
}
```

**Hold every payment above a threshold.**

```json
{
  "name": "hold-large-payments",
  "when": { "tool": "pay_invoice", "amount_gt": 1000 },
  "then": { "hold": "ABOVE_AUTO_LIMIT", "message": "Above the limit." }
}
```

**Allowlist a set of recipients, hold everything else.**

```json
{
  "name": "allow-trusted-recipients",
  "when": {
    "tool": "pay_invoice",
    "recipient_in": ["Fournisseur_A", "Fournisseur_B", "Acme_Corp"]
  },
  "then": { "approve": true }
},
{
  "name": "hold-other-recipients",
  "when": { "tool": "pay_invoice" },
  "then": { "hold": "VENDOR_NOT_ALLOWLISTED", "message": "Not on the allowlist." }
}
```

**Approve internal emails, hold external ones.**

```json
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
  "then": { "hold": "EXTERNAL_EMAIL", "message": "External recipient." }
}
```

**Block a subject pattern outright.**

```json
{
  "name": "block-sensitive-subject",
  "when": {
    "tool": "send_email",
    "subject_matches": "(?i)password|secret|api[_-]?key"
  },
  "then": { "block": "SENSITIVE_SUBJECT", "message": "Subject looks sensitive." }
}
```

**Hold writes outside an allowed directory.**

```json
{
  "name": "hold-outside-allowed-paths",
  "when": {
    "tool": "write_file",
    "path_not_matches": "^/workspace/|^/tmp/"
  },
  "then": { "hold": "OUTSIDE_WORKSPACE", "message": "Write outside the workspace." }
}
```

**Rate-limit a tool to ten calls per hour.**

```json
{
  "name": "limit-email-rate",
  "when": {
    "tool": "send_email",
    "count_last_1h_gt": 10
  },
  "then": { "hold": "EMAIL_RATE", "message": "Too many emails this hour." }
}
```

Note this fires on the *eleventh* call in an hour, because
`count_last_1h_gt: 10` means "count is greater than ten", which is true
at eleven.

**Hold when a rolling sum exceeds a daily budget.**

```json
{
  "name": "hold-daily-budget",
  "when": {
    "tool": "pay_invoice",
    "sum_last_24h_gt": 5000
  },
  "then": { "hold": "DAILY_LIMIT", "message": "Daily budget exceeded." }
}
```

Same off-by-one: this fires when the sum of the last 24 hours *plus the
current call* would push the total above 5000, because the current call
is already counted toward the sum by the time the rule runs.

**Different rules for different agents, same tool.**

```json
{
  "name": "allow-senior-agent-large-payments",
  "when": {
    "tool": "pay_invoice",
    "agent_id": "finance_agent",
    "amount_lt": 10000
  },
  "then": { "approve": true }
},
{
  "name": "hold-other-agents-above-1000",
  "when": { "tool": "pay_invoice", "amount_gt": 1000 },
  "then": { "hold": "ABOVE_AGENT_LIMIT", "message": "Above this agent's limit." }
},
{
  "name": "allow-other-agents-under-1000",
  "when": { "tool": "pay_invoice" },
  "then": { "approve": true }
}
```

Order matters here: the most specific rule (senior agent, large amount)
comes first; the catch-all comes last.

### 5.10 — Reading a rule

Given a call and a list of rules, the way to know which rule fired is
not to read the rules in order in your head. It is to ask Pryxor. The
audit trail records the `rule` field for every decision, and the
`reason` field names the action. `curl /v1/audit` returns entries like:

```json
{
  "event_type": "created",
  "action_id": "hold_0eda8977",
  "agent_id": "ops_agent",
  "payload": {
    "reason": "EXTERNAL_EMAIL",
    "message": "External recipient."
  }
}
```

`reason` is the value you gave in the `then` block — `EXTERNAL_EMAIL`
in this case, which came from the rule named `hold-external-emails`.

Section 5.11 walks through the debugging workflow when a rule does not
fire the way you expected.

### 5.11 — When a rule does not fire

Four questions, in this order.

**1. Did the rule match at all?**

Look at the reason in the response or in `/v1/audit`. If the reason is
`UNSUPPORTED_TOOL` or `INVALID_ARGUMENTS`, the call never reached the
sector — the problem is in `agents.json` or the executor's
`inputSchema`, not in the rule.

If the reason is one you recognise from your rules, the rule fired. If
it is `APPROVED` and you expected a hold, no rule with a hold matched —
your `when` conditions did not all hold.

**2. Did a rule above it match first?**

Because the first match wins, a broader rule above the specific one will
swallow it. Temporarily move the rule you are debugging to the top of
the list. If it fires, the problem is ordering, not the condition.

**3. Is the field actually present, and the right type?**

A rule with `amount_gt` will not match if `amount` is missing from the
parameters, or if it is a string. Check the executor's `inputSchema`:
if `amount` is declared as `"type": "string"`, the value arriving at the
sector is a string, and `amount_gt` fails to parse it.

**4. Is the operator spelled the way the language spells it?**

Condition keys are exact. `recipient_domain_in` works;
`recipientDomainIn` does not. `amount_gt` works; `amountGreater` does
not. A rule with an unrecognized operator is treated as a strict
equality on a field name that does not exist, and silently fails to
match.

If none of those four resolve it, [Debugging](08-debugging.md)
has a step-by-step walkthrough with curl commands and the exact shape of
the audit entries to look for.