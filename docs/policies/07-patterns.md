## 7. Common patterns

Nine recipes that come up over and over. Each is a complete, standalone
sector that you can copy and adapt. The pattern matters more than the
details — the field names are examples.

### 7.1 — Allowlist with a fallback hold

Approve a short list, hold everything else. The allowlist rule comes
first; the fallback catches the rest.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "allow-trusted-vendors",
      "when": {
        "tool": "pay_invoice",
        "recipient_in": ["Fournisseur_A", "Fournisseur_B", "Acme_Corp"]
      },
      "then": { "approve": true }
    },
    {
      "name": "hold-unknown-vendors",
      "when": { "tool": "pay_invoice" },
      "then": {
        "hold": "VENDOR_NOT_ALLOWLISTED",
        "message": "Vendor is not on the allowlist."
      }
    }
  ]
}
```

Use this whenever "the default is caution". The allowlist rule is
narrow; the fallback is broad and last.

### 7.2 — Threshold with two tiers

Approve small amounts, hold medium, block anything above a hard cap.
Three rules, most specific first.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "block-above-hard-cap",
      "when": { "tool": "pay_invoice", "amount_gt": 10000 },
      "then": {
        "block": "ABOVE_HARD_CAP",
        "message": "Above the absolute limit for this agent."
      }
    },
    {
      "name": "hold-above-soft-limit",
      "when": { "tool": "pay_invoice", "amount_gt": 1000 },
      "then": {
        "hold": "ABOVE_AUTO_LIMIT",
        "message": "Above the automatic limit; needs a human."
      }
    },
    {
      "name": "approve-small-payments",
      "when": { "tool": "pay_invoice" },
      "then": { "approve": true }
    }
  ]
}
```

Notice the order: block first, hold second, approve last. If the
approve rule were first, it would fire on every call and nothing would
ever be held.

### 7.3 — Per-agent limits

Different agents have different ceilings. The most specific rule first.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "allow-cfo-large",
      "when": {
        "tool": "pay_invoice",
        "agent_id_in": ["cfo_agent", "treasury_agent"],
        "amount_lt": 50000
      },
      "then": { "approve": true }
    },
    {
      "name": "allow-ops-medium",
      "when": {
        "tool": "pay_invoice",
        "agent_id": "ops_agent",
        "amount_lt": 1000
      },
      "then": { "approve": true }
    },
    {
      "name": "hold-others",
      "when": { "tool": "pay_invoice" },
      "then": {
        "hold": "ABOVE_AGENT_LIMIT",
        "message": "Above this agent's limit."
      }
    }
  ]
}
```

The first rule matches two agents at once via `agent_id_in`. The second
handles one agent. The third catches everything else. Adding a new agent
means adding a rule above the catch-all — the catch-all stays.

### 7.4 — Block a subject pattern, then hold external

Two-step filtering. Block the clearly dangerous first, then decide by
destination.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "block-sensitive-subject",
      "when": {
        "tool": "send_email",
        "subject_matches": "(?i)password|secret|api[_-]?key|credentials"
      },
      "then": {
        "block": "SENSITIVE_SUBJECT",
        "message": "Subject looks sensitive."
      }
    },
    {
      "name": "allow-internal",
      "when": {
        "tool": "send_email",
        "to_domain_in": ["company.local"]
      },
      "then": { "approve": true }
    },
    {
      "name": "hold-external",
      "when": { "tool": "send_email" },
      "then": {
        "hold": "EXTERNAL_EMAIL",
        "message": "External recipient requires approval."
      }
    }
  ]
}
```

Blocking is absolute — it fires before any consideration of who is
sending. The allow and hold rules then sort by destination.

### 7.5 — Rate-limit a tool

The `count_last_<N>h_gt` operator counts *approved* calls in a rolling
window. Use it to cap the rate at which an agent can do something.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "limit-emails-per-hour",
      "when": {
        "tool": "send_email",
        "count_last_1h_gt": 10
      },
      "then": {
        "hold": "EMAIL_RATE",
        "message": "Too many emails this hour."
      }
    }
  ]
}
```

The rule fires on the eleventh call in a rolling hour. Approved calls
are counted; held and blocked calls are not. If a held call is later
approved by a human, it joins the count.

For a hard cap instead of a soft one, use `block` instead of `hold` in
the `then`.

### 7.6 — Daily budget

The `sum_last_<N>h_gt` operator sums the `amount` field over a rolling
window. Use it to hold once the day's total crosses a line.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "hold-daily-budget",
      "when": {
        "tool": "pay_invoice",
        "sum_last_24h_gt": 5000
      },
      "then": {
        "hold": "DAILY_LIMIT",
        "message": "Daily spend would exceed 5000."
      }
    }
  ]
}
```

The rule fires when the *sum of already-approved calls* over the last
24 hours is already above 5000. In practice this means the first few
calls go through, the sixth or seventh is held, and every subsequent one
is also held until the window rolls forward. This is usually what you
want; if you want "the call that crosses the line to be held but not the
ones after", you need a code sector
([Section 6](#6-code-sectors-when-declarative-is-not-enough)).

### 7.7 — Different rules for production and staging

The `environment` field is just another parameter. Compare it directly.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "block-destructive-on-prod",
      "when": {
        "tool_in": ["delete_instance", "drop_database"],
        "environment": "production"
      },
      "then": {
        "block": "DESTRUCTIVE_ON_PROD",
        "message": "Destructive tools are never allowed on production."
      }
    },
    {
      "name": "hold-destructive-elsewhere",
      "when": {
        "tool_in": ["delete_instance", "drop_database"]
      },
      "then": {
        "hold": "DESTRUCTIVE_ACTION",
        "message": "Destructive tools need a human."
      }
    },
    {
      "name": "allow-the-rest",
      "when": { "tool_in": ["delete_instance", "drop_database"] },
      "then": { "approve": true }
    }
  ]
}
```

The strictest rule fires first. On any other environment the second rule
holds. The third rule is unreachable as written — it exists as a
placeholder if you later want to allow destructive actions on some
environments via `environment_in`. This pattern of "block strict,
hold moderate, allow lenient" reads naturally and is easy to extend.

### 7.8 — Guard a sensitive path

Block a set of paths absolutely; hold anything outside a working
directory.

```json
{
  "type": "declarative",
  "rules": [
    {
      "name": "block-system-paths",
      "when": {
        "tool_in": ["read_file", "write_file"],
        "path_matches": "^/etc/|^/root/|^/var/lib/"
      },
      "then": {
        "block": "SYSTEM_PATH",
        "message": "System paths are out of scope."
      }
    },
    {
      "name": "hold-outside-workspace",
      "when": {
        "tool_in": ["read_file", "write_file"],
        "path_not_matches": "^/workspace/|^/tmp/"
      },
      "then": {
        "hold": "OUTSIDE_WORKSPACE",
        "message": "Outside the workspace; needs approval."
      }
    }
  ]
}
```

`path_not_matches` is the negative form of `path_matches`. The block
rule runs first, so even a path that would also trigger the hold rule
is blocked cleanly.

### 7.9 — When the parameter is optional

A rule whose condition refers to a field the call does not include does
not match. This is deliberate, but it interacts badly with optional
fields. Consider:

```json
{
  "name": "hold-urgent",
  "when": { "tool": "send_email", "priority": "urgent" },
  "then": { "hold": "URGENT_PRIORITY", "message": "Urgent sends need review." }
}
```

If `priority` is missing from a call, the rule does not fire — which is
correct, since a call without a priority is not urgent. But if the
executor's `inputSchema` declares `priority` with a default, and the
default is `"urgent"`, the schema validator will not inject the default
into the parameters. Pryxor does not fill defaults. A field that is
absent stays absent.

If you want to treat a missing `priority` as normal priority, either the
agent must send it, or a later rule must handle the default case:

```json
{
  "name": "approve-normal-or-missing",
  "when": { "tool": "send_email" },
  "then": { "approve": true }
}
```

Place this after the `hold-urgent` rule. Anything urgent is held; the
rest — including calls without `priority` — is approved.