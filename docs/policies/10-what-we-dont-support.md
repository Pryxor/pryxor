## 10. What we deliberately don't support

A rule language grows by accident. Every addition is a feature request
that seems small at the time, and two years later the policy is code
that nobody can read. The line below is drawn on purpose. Knowing what
is *not* there is as important as knowing what is.

### 10.1 — No `or` inside a `when`

All conditions in a `when` block are ANDed. To get the effect of an
`or`, write two rules. The first that matches wins.

Why: an `or` inside a rule means the rule's intent can no longer be
read from its name. `hold-external-or-large` is two rules pretending to
be one, and the audit trail says `EXTERNAL_EMAIL` for a call that was
actually large. Two rules, two reasons, two audit entries.

### 10.2 — No `not` at the rule level

Individual conditions have negative forms: `tool_not_in`,
`recipient_not_in`, `path_not_matches`, `to_domain_not_in`. The `when`
block as a whole cannot be negated. There is no `"not": { ... }`.

Why: a negated rule is a trap. "Hold everything that is not
internal" reads one way, but "hold everything that is not internal *and*
has an attachment" is what the author meant, and the two are not the
same. Negative conditions at the field level are unambiguous; negative
rules are not.

### 10.3 — No cross-field comparison

You cannot say `amount` is less than `budget`. You can compare a field
to a constant, not to another field.

Why: cross-field comparison turns every policy into a contract about
the shape of the payload. If `budget` is missing from one call and
present in another, the rule's behavior depends on the caller, and the
policy no longer has a stable meaning.

### 10.4 — No arithmetic

No addition, no multiplication, no percentages, no expressions. The
`sum_last_<N>h_gt` aggregate is the only operation on more than one
value.

Why: arithmetic is where a rule language becomes a programming
language. The moment you allow `amount * 1.2`, the next request is
`amount * exchange_rate`, and then someone wants to look up
`exchange_rate` from an API, and the policy is now a distributed
system.

### 10.5 — No time-of-day conditions

No "only on weekends", no "only between 9am and 5pm", no "only during
the change freeze".

Why: time conditions require a timezone, and a timezone requires a
configuration, and the configuration drifts between staging and
production. If a policy must behave differently on weekends, that is a
human process — a scheduler that enables and disables the policy, or
two policies and a switch. Not a rule.

### 10.6 — No external lookups

A declarative rule reads the call and the config. It cannot call an API
to check whether a vendor is active, look up a database for a user's
role, or query a ticket system for an approval ID.

Why: an external lookup makes the policy's behavior depend on a service
that can be down, slow, or compromised. The safety property of a
declarative policy is that a decision is a pure function of the call
and the file on disk. Break that, and the property is gone.

If you need an external lookup, write a code sector
([Code sectors](06-code-sectors.md)) — and
accept that the sector can now fail, and design for that failure
explicitly.

### 10.7 — No fallthrough with a value

Every rule is a terminal decision. There is no "if X, then Y, otherwise
continue to the next rule *with the same data*". The first match wins.
The rules below it are never consulted for that call.

Why: fallthrough is a control-flow construct. A policy that relies on
control flow is a program with a state machine, and a reviewer cannot
tell what a rule does without simulating the whole list. Terminal rules
are individually readable.

### 10.8 — No default in the schema is filled in

If an executor's `inputSchema` declares a field with a `default`
value, Pryxor does not fill it in before the sector runs. The field is
either in the call's parameters or it is absent. A rule that names an
absent field does not match.

Why: `default` is a JSON Schema feature that tooling can use to
generate forms. Interpreting it as "the value the caller should have
sent" conflates two different ideas. The caller sends what the caller
sends; the schema describes what is valid; the sector reads what
arrived.

If a rule needs to handle a missing field, write a rule for the missing
case explicitly (see 7.9).

### 10.9 — No state beyond velocity

The only stateful feature is the rolling window for `sum_last_<N>h_gt`
and `count_last_<N>h_gt`. There is no "this user has been seen before",
no "this is the third time today", no "this vendor is new".

Why: velocity is a specific, narrow, well-understood property. Broad
state opens the policy to the same problems as external lookups and
arithmetic. A stateful rule cannot be evaluated by reading the file; it
requires knowing the history. The velocity window is the one exception
because it is bounded, deterministic, and reset by the clock.

### 10.10 — What this means in practice

The rule language is a filter, not a decision engine. It reads one
call and one file and answers one question. If your problem does not
fit that shape, the answer is not to grow the language. It is to
choose a different tool for that part of the problem, and to keep the
policy as the layer that decides whether the call crosses the boundary.