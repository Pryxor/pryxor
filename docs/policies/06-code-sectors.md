## 6. Code sectors — when declarative is not enough

The declarative language covers most policies. Some do not fit: a
calculation that spans several parameters, a state machine, an external
lookup, a rule that needs the code to be tested with unit tests. For
those, a sector can be a Python class instead of a JSON list.

The class lives in `sectors/<name>.py`, and the config file in
`configs/sectors/<name>.json` declares the type:

```json
{ "type": "code" }
```

Nothing else is required in the config file. The rules are in the
Python.

### 6.1 — The contract

A code sector subclasses `SectorPolicy` — or, more commonly,
`SimpleSector`, which provides the plumbing — and exposes a `name`:

```python
from sectors._framework import Decision, SimpleSector


class EmailSector(SimpleSector):
    name = "email"
    version = "1.0.0"

    def decide(self, agent_id, tool_name, parameters):
        ...
```

The engine calls `decide(agent_id, tool_name, parameters)` and expects a
`Decision` back. Three constructors:

```python
Decision.approve("Authorized.")
Decision.hold("REASON", "Human-readable message.")
Decision.block("REASON", "Human-readable message.")
```

The `REASON` is what appears in the audit trail. The message is what the
agent receives when the call is blocked or held — keep it short and do
not leak internal details.

Two attributes matter: `name` is how the config finds the class, and
`version` is written to the audit trail with every decision. Bump the
version when you change the rules.

### 6.2 — What `SimpleSector` gives you

Subclassing `SimpleSector` instead of `SectorPolicy` gives you the
plumbing for free:

- `self.sector_config` — the contents of `configs/sectors/<name>.json`,
  minus the `type` field. Same as the declarative sector reads.
- `self.sum_window(hours, agent_id=None, tool_name=None)` — sum of
  `amount` over the last N hours for approved calls.
- `self.count_window(hours, agent_id=None, tool_name=None)` — count of
  approved calls over the last N hours.
- `self.storage` — the underlying SQLite storage, if you need it.
- Automatic recording of approved calls for velocity, with idempotence
  guaranteed by a stable dedup key.

With `SectorPolicy`, you would write all of that yourself.

### 6.3 — A worked example

The same email sector as the declarative one in section 2, but written
in Python. Same rules, same behavior. This lets you compare.

```python
from sectors._framework import Decision, SimpleSector


class EmailSector(SimpleSector):
    name = "email"
    version = "1.0.0"

    def decide(self, agent_id, tool_name, parameters):
        if tool_name != "send_email":
            return Decision.block(
                "UNEXPECTED_TOOL",
                "This sector only handles send_email.",
            )

        to = str(parameters.get("to", ""))
        if "@" not in to:
            return Decision.block(
                "INVALID_RECIPIENT",
                "Recipient is not a valid email address.",
            )

        domain = to.split("@", 1)[1].lower()
        allowed = self.sector_config.get("allowed_domains", [])

        if domain in allowed:
            return Decision.approve(f"Internal recipient ({domain}).")

        return Decision.hold(
            "EXTERNAL_EMAIL",
            f"Recipient domain '{domain}' requires human approval.",
        )
```

And the config:

```json
{
  "type": "code",
  "allowed_domains": ["company.local", "company.com"]
}
```

Two things to notice. `self.sector_config` gives you the same JSON keys
the declarative sector would use, so migrating from declarative to code
is a copy of the config minus `type`. And every decision carries a
reason code (`INVALID_RECIPIENT`, `EXTERNAL_EMAIL`), which shows up in
the audit trail exactly like the declarative `then` values.

### 6.4 — Using velocity

The two velocity helpers cover most of what a declarative rule cannot
express. This is the one feature worth learning before you decide a code
sector is needed.

```python
from sectors._framework import Decision, SimpleSector


class FinanceSector(SimpleSector):
    name = "finance"
    version = "2.0.0"

    def decide(self, agent_id, tool_name, parameters):
        try:
            amount = float(parameters.get("amount", 0.0))
        except (TypeError, ValueError):
            return Decision.block("INVALID_TOOL_CALL", "amount must be numeric.")

        max_single = float(self.sector_config.get("max_single_transaction", 0.0))
        if max_single > 0 and amount > max_single:
            return Decision.hold(
                "EXCEEDS_SINGLE_TRANSACTION_LIMIT",
                f"Amount {amount}$ > limit {max_single}$.",
                amount=amount,
            )

        window_hours = int(self.sector_config.get("velocity_window_hours", 24))
        velocity_limit = float(self.sector_config.get("velocity_limit", 0.0))

        if velocity_limit > 0:
            current = self.sum_window(window_hours)
            if current + amount > velocity_limit:
                return Decision.hold(
                    "VELOCITY_LIMIT_EXCEEDED",
                    f"Cumulative {current + amount}$ > {velocity_limit}$ "
                    f"over {window_hours}h.",
                    current_volume=current,
                )

        return Decision.approve(f"Authorized. {amount}$.")
```

The same logic as a declarative rule would require two rules, and even
then the declarative version reads `sum_last_24h_gt` as a boolean — you
cannot add the incoming amount to the running sum before comparing. The
code version can. This is the sort of rule that justifies a code sector.

Two things `SimpleSector` handles for you that are easy to get wrong if
you write a sector from scratch:

- **Idempotence.** Every approved call is recorded with a `dedup_key`
  that is stable across process restarts. If Pryxor crashes and replays
  the approval, the call is not counted twice. You do not have to think
  about this unless you override the recording callbacks.
- **Windows are rolling.** `sum_window(24)` is a rolling 24 hours, not
  "since midnight". This is almost always what you want; it is
  occasionally not.

### 6.5 — Extra callbacks

`SimpleSector` records approved calls automatically. Two optional
callbacks let you act on the lifecycle:

```python
def record_approved(self, agent_id, tool_name, parameters):
    # Called when a call is approved directly.
    super().record_approved(agent_id, tool_name, parameters)
    # ... your own bookkeeping ...

def on_hold_approved(self, agent_id, tool_name, parameters, *, dedup_key=None):
    # Called when a held call is later approved by a human.
    super().on_hold_approved(
        agent_id, tool_name, parameters, dedup_key=dedup_key
    )
    # ... your own bookkeeping ...
```

If you override these, **always call `super()`**. Skipping the parent
loses the velocity tracking and the idempotence guarantee, silently.
This is the most common mistake in code sectors.

The `dedup_key` you see in `on_hold_approved` is stable for a given
outbox event. If you write your own storage, use it as a unique key so a
crash-and-replay does not double-count.

### 6.6 — When a code sector is the right answer

Four signals that it is time to move from declarative to code:

1. **You need arithmetic on the current call.** Sum, subtract, compute
   a percentage. The declarative language compares; it does not
   calculate.
2. **You need external state.** A lookup against a database or a service
   to decide. The declarative language only reads the call and the
   config.
3. **The rule has more than four conditions and they interact.** The
   declarative language ANDs everything; if your logic is
   "A and (B or C) and not D", it does not fit.
4. **You want unit tests.** A code sector is a Python class; you can
   test `decide()` directly, with no HTTP, no database, no Pryxor
   running. If your rules are worth testing, they are worth writing in
   Python.

The counterpart: a code sector is code. It needs review, tests,
versioning. A declarative rule is data. If the decision is "this is a
rule a non-developer will want to edit next quarter", keep it
declarative even if it is a bit verbose. If the decision is "this is
logic, and the operations team will never touch it", a code sector is
cleaner.

### 6.7 — Mixing declarative and code in one policy

A policy can have both kinds of sector at once. Different tools, or
different concerns, can be judged by different sectors.

```text
configs/sectors/
├── finance.json          →  { "type": "code", ... }
├── email.json            →  { "type": "declarative", "rules": [...] }
└── ops.json              →  { "type": "declarative", "rules": [...] }
```

The engine loads each one according to its declared `type`. Nothing else
in the policy needs to change. A given tool is judged by exactly one
sector — the one named in its executor's `sector` field, or the global
default if the executor does not declare one.

To check which sector a tool belongs to, look at the executor:

```json
{
  "type": "http",
  "url": "https://api.example.com/...",
  "sector": "email",
  ...
}
```

If you see `"sector": "email"`, calls to that tool are judged by
`configs/sectors/email.json` — whichever kind it is.

### 6.8 — A sector is not a middleware

The class you write subclasses `SectorPolicy`. It does one thing:
receive a call, return a decision. It does not authenticate, does not
execute, does not write to the database (unless it chooses to for its
own velocity, which `SimpleSector` already handles). If you find
yourself reaching for `requests` or an HTTP client inside a sector, you
are building a different thing — probably an executor, or a business
logic layer that belongs upstream of Pryxor.

The same rule applies to time. A sector is fast and deterministic. If
your sector takes longer than a few milliseconds, something is off.

The next section is the one you will come back to most often after this
one: how to know *why* a rule fired or did not fire, and how to test a
policy change before you deploy it.