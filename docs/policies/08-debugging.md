## 8. Debugging a rule that doesn't match

**In this file:** [Read the response first](#81--read-the-response-first) ·
[Ask the audit](#82--ask-the-audit-what-happened) ·
[The four causes](#83--the-four-causes-in-order) ·
[The debug rule](#84--the-debug-rule) ·
[Reloading the policy](#85--reloading-the-policy) ·
[When the config loader fails](#86--when-the-config-loader-itself-fails) ·
[When you are still stuck](#87--when-you-are-still-stuck)

The single most common question after writing a first policy is: "why
did this rule not fire?". The answer is almost always one of four
things. This section walks through them with concrete commands.

### 8.1 — Read the response first

Before anything else, look at what Pryxor actually returned for the
call. In the terminal:

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email",
       "parameters":{"to":"bob@external.com","subject":"Hi","body":"x"}}' \
  | python -m json.tool
```

Read the `status` and `reason` fields.

- `status: "BLOCKED"`, `reason: "UNSUPPORTED_TOOL"` — the call never
  reached the sector. The tool is not in `agents.json` for this agent,
  or the `tool_name` in the payload does not match the executor's
  filename.
- `status: "BLOCKED"`, `reason: "INVALID_ARGUMENTS"` — the call reached
  the schema validator and failed. Look at the `errors` field for the
  specific field and reason.
- `status: "BLOCKED"` with a `reason` you recognise from your own
  rules — the block rule fired. Good.
- `status: "HOLD"` with a `reason` you recognise — a hold rule fired.
- `status: "APPROVED"` — no block rule fired, and no hold rule fired.
  Either an approve rule fired, or no rule matched and the sector
  returned its default. The default is APPROVED.
- `status: "EXECUTION_FAILED"` — a rule fired and approved, but the
  executor itself failed. The error is in `execution.error`.

If the reason is one of the framework reasons
(`UNSUPPORTED_TOOL`, `INVALID_ARGUMENTS`), the problem is upstream of
the sector. Fix `agents.json` or the executor schema, and come back.

If the reason is your own, or if there is no reason because the call was
approved and you expected a hold, read on.

### 8.2 — Ask the audit what happened

Every decision writes one event to the audit trail. The `reason` field
records the value from the `then` block. Reading the last few events
tells you which rule fired — or that none did.

You need an admin key for this. In one command:

```bash
curl -s "http://127.0.0.1:8000/v1/audit?limit=5" \
  -H "X-Admin-Key: $PRYXOR_ADMIN_KEY" \
  | python -m json.tool
```

Or with the CLI:

```bash
python pryxor_cli.py audit list
```

The response contains the last five audit events. Each one has:

```json
{
  "event_type": "created",
  "action_id": "hold_0eda8977",
  "agent_id": "ops_agent",
  "payload": {
    "reason": "EXTERNAL_EMAIL",
    "message": "External recipient requires approval."
  },
  "created_at": "2026-09-26T15:14:22.108431+00:00"
}
```

Two things to look for:

- **`event_type`** — `created` means a hold was created (a hold rule
  fired). `approved` and `rejected` are for human decisions. There is
  no event for an approved-directly call in the audit trail; those are
  recorded in `executions`, not `audit_events`.
- **`payload.reason`** — the string you gave in the rule's `then`
  block. `EXTERNAL_EMAIL` here comes from the rule named
  `hold-external-emails`.

If you see a reason you do not recognise, the rule that produced it is
in your policy — search the files for the string. The reason is unique
per rule in a well-written policy; if two rules share a reason code, fix
that first.

### 8.3 — The four causes, in order

When a rule you expected to fire does not, one of these is the cause.
Check them in this order.

**1. A rule above it fired first.**

Because the first match wins, a broader rule placed above the specific
one will swallow it. The fix is to move the specific rule to the top.

To check whether ordering is the cause, temporarily move the rule you
are debugging to the very top of the list in its sector file. Restart
Pryxor (`Ctrl+C`, then `python pryxor_proxy.py`), and send the call
again.

- If the rule now fires: the problem was ordering. Move it to its
  intended position and check which rule above it was matching.
- If the rule still does not fire: ordering is not the cause. Move on
  to cause 2.

The audit does not tell you which rule *would* have matched; it tells
you which one did. The temporary-move test is the cleanest way to ask
Pryxor directly.

**2. The condition refers to a field that is not in the parameters.**

A rule with `{"recipient_in": ["Fournisseur_A"]}` does not match a call
with no `recipient` key. This is deliberate
([Section 5.7 -- The when language](05-sectors.md)), but it surprises people
whose executor schema does not declare the field.

Print what the agent is actually sending. In the terminal where the
agent runs, log the payload before it goes to Pryxor. Or, more simply,
send the call yourself with the exact payload you expect the agent to
send:

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"pay_invoice",
       "parameters":{"amount":800,"recipient":"Fournisseur_A"}}' \
  | python -m json.tool
```

If this matches your rule, then the agent is sending a different
payload — a missing field, a different field name, or a different tool
name.

**3. The field is present but has the wrong type.**

A rule with `amount_gt: 1000` will not match if `amount` is the string
`"800"`. This happens when the executor's `inputSchema` declares
`amount` as `"type": "string"`. The schema validator only checks types
when a schema is declared; if the schema says string, the sector sees a
string, and the numeric comparison fails silently.

Check the executor:

```bash
cat configs/executors/pay_invoice.json | python -m json.tool | grep -A2 '"amount"'
```

If the type is `"string"` and your rule uses `amount_gt`, either change
the schema to `"number"`, or change the rule to compare the string:
`{"amount": "800"}`. The first is almost always what you want.

**4. The operator is misspelled.**

Condition keys are exact. `amount_gt` works; `amountGreater` does not.
`recipient_domain_in` works; `recipientDomainIn` does not. A rule with
an unrecognised operator is treated as a strict equality on a field
name that does not exist, and silently fails to match.

There is no error message. This is a deliberate trade-off: strict
validation would reject a policy file for a single typo, and many people
prefer a warning in the logs to a hard failure. The cost is that typos
are silent.

The mitigation is to compare against
[Section 5.7 -- The when language](05-sectors.md). If your operator is not in that
table, it does not exist. Rename it to the version that does.

### 8.4 — The debug rule

When none of the four causes above explain the behavior, the fastest way
to isolate the problem is a **debug rule** that always matches. Insert
it at the very top of the sector:

```json
{
  "name": "DEBUG-always-hold",
  "when": { "tool": "send_email" },
  "then": {
    "hold": "DEBUG",
    "message": "Debug rule fired; every send_email is held."
  }
}
```

Restart Pryxor and send a call. Two possible outcomes.

- **The call is held with reason `DEBUG`.** The sector is loaded
  correctly and the call is reaching it. The problem is in the original
  rules above the debug rule's position — since the debug rule is at
  the top and matches, no original rule is firing first, which is what
  you want. Move the debug rule below the rule you are debugging. If
  the original rule fires now, the debug rule was masking an ordering
  problem. If it does not, the original rule's condition is the
  problem — go back to cause 2, 3, or 4.

- **The call is not held.** The sector is not being loaded for this
  tool. Check the executor's `sector` field. If it names a sector other
  than the one you edited, you are editing the wrong file. If it names
  no sector, the global default in `pryxor.json` is used — check that
  one.

Remove the debug rule when you are done. It is a tool, not a
permanent addition.

### 8.5 — Reloading the policy

**Pryxor reads the config folder at startup and never again.** This is
deliberate: a running instance uses a fixed, reviewed policy. Editing
a JSON file on disk does not affect a running Pryxor.

After any change to a file under `configs/`, restart:

```bash
# In the terminal running Pryxor
Ctrl+C
python pryxor_proxy.py
```

If your changes seem to have no effect, this is the first thing to
check.

The startup log prints exactly what was loaded. Look for lines like:

```text
INFO pryxor.config: Loading config from folder configs
INFO pryxor.engine: Loaded 2 sector(s): ['email', 'finance'] (default: email)
INFO pryxor.engine: Loaded 5 executor(s): ['fetch_url', 'pay_invoice', 'read_file', 'send_email', 'send_payment']
INFO pryxor.engine: Rate limiting enabled: 60/min, burst=10
```

If a sector you expected to see is missing, its module failed to import
— the log line above it will explain why. If an executor you expected to
see is missing, its config file has a JSON syntax error, or its
`inputSchema` is invalid, or its `type` is unrecognised. Fix the file
and restart.

### 8.6 — When the config loader itself fails

Two startup errors have specific causes.

**`ConfigError: No configuration found`.** Pryxor looked for a folder
named `configs/` in the working directory and did not find it, or it
found the folder but no `pryxor.json` inside. Two fixes:

- Confirm `configs/` exists next to `pryxor_proxy.py`.
- Confirm `configs/pryxor.json` is present and contains a JSON object
  (`{}` is valid; an empty file is not).

You can also point Pryxor at another folder with the environment
variable:

```bash
export PRYXOR_CONFIG_DIR=/path/to/my/configs
python pryxor_proxy.py
```

**`ConfigError: Missing required file: configs/pryxor.json`.** The
folder exists but `pryxor.json` is absent. Even a minimal policy needs
this file. An empty object is valid:

```json
{}
```

The defaults will apply.

### 8.7 — When you are still stuck

Two things to check before asking for help.

**The full request.** Copy the exact `curl` command you ran, including
the JSON body. Almost every "the rule does not fire" ends with "ah, the
field was named `amount` not `value`" once the payload is visible.

**The full response.** Copy the JSON body, not just the status line.
`BLOCKED` and `HOLD` look the same in a terminal summary and are wildly
different in the body.

With both, and the last ten lines from the Pryxor startup log, any
maintainer can reproduce the issue in one run. Without them, the
conversation starts with a question and a waiting period.