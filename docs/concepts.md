# Concepts

Pryxor sits between an AI agent and the systems that agent can act on.
It intercepts every tool call, evaluates it against a policy, and — when
authorised — performs the real action itself. The agent never holds the
credential.

That paragraph is the whole idea. This page explains why it matters, and
what it implies.

No code, no config. Just the model.

---

## 1. The problem Pryxor addresses

An AI agent with credentials is a trust boundary in the wrong place.

The common pattern today is to hand an agent a token with broad
permissions and rely on the model to use it correctly. The security of
the system then depends on the model's behaviour: on the prompt holding,
on the agent's reasoning staying on track, on nobody managing to slip an
instruction past it.

That is the wrong place for the boundary. Prompts can be injected, models
can hallucinate, plans can go wrong for reasons no one anticipated.
None of that is exotic — it is the normal behaviour of a probabilistic
system asked to act on the world.

When the model *is* the security boundary, every one of those failures
is a security incident.

Pryxor moves the boundary. Instead of trusting the agent to use its
credentials wisely, the agent holds no credential at all. It sends an
*intention* to Pryxor, Pryxor decides whether the intention is allowed,
and — only if it is — Pryxor performs the real action with credentials
the agent never sees.

The model can be as confused, mistaken, or manipulated as it likes. It
still cannot act outside the policy. That is the design.

---

## 2. The shape of the boundary

```text
                          ┌───────────────┐
                          │   AI Agent    │
                          │  (no creds)   │
                          └───────┬───────┘
                                  │
                                  │  tool call: "pay 800 to X"
                                  │  header: X-Agent-Key
                                  ▼
                     ┌────────────────────────┐
                     │        PRYXOR          │
                     │                        │
                     │  authenticate          │
                     │  authorize             │
                     │  validate              │
                     │  evaluate policy       │
                     │                        │
                     │  decide:               │
                     │    APPROVED / HOLD /   │
                     │    BLOCKED             │
                     │                        │
                     │  if APPROVED:          │
                     │    perform the action  │
                     │    with a credential   │
                     │    the agent never     │
                     │    sees                │
                     └───────────┬────────────┘
                                 │
                                 ▼
                     ┌────────────────────────┐
                     │     Real system        │
                     │  (bank, mail, API, …)  │
                     └────────────────────────┘
```

The agent's job ends at *proposing*. Pryxor owns *deciding* and
*executing*. The real system only ever sees requests that came from
Pryxor, and only ever with credentials that live in Pryxor's
environment.

Two consequences follow from this shape.

**The agent cannot bypass Pryxor by accident.** If it is not routed
through Pryxor, it has no way to reach the real system. It does not know
the URL and does not hold the token. The bypass is not forbidden — it is
impossible from inside the agent.

**The policy is the only place decisions are made.** No rule is buried
in the agent's prompt, no safety check is scattered through the agent's
code. Every decision is a pure function of the call and a file on disk.
That is what makes it reviewable.

---

## 3. Three ideas

The rest of the documentation builds on three ideas. They are worth
stating explicitly, because they are what makes Pryxor different from
"put some guardrails around the LLM".

### 3.1 — The decision happens before the action

Pryxor does not watch the agent act and then judge. It decides *first*.
The action never happens unless a policy allowed it.

This is what separates a runtime control from an observability tool. An
observability tool can tell you, after the fact, that the agent did
something wrong. A runtime control stops the action before it happens.
Pryxor is the second kind.

The three decisions:

- **APPROVED** — the action is allowed and executed.
- **HOLD** — the action is plausible but needs a human. It is queued,
  and nothing happens until someone approves or rejects it.
- **BLOCKED** — the action is refused. It never reaches the real system.

A HOLD is a first-class outcome, not a fancy error. It is how the system
says "I am not qualified to decide this; find a person". That middle
outcome is what makes Pryxor usable in a real company, where some
decisions belong to a person and no policy should pretend otherwise.

### 3.2 — The agent holds no credential

Credentials live on the Pryxor side. The agent never sees them.

This is not a nicety. It is the property that makes the boundary hold
under adversarial pressure. An agent that is compromised cannot steal a
token it was never given. An agent that is prompt-injected cannot
exfiltrate a secret it does not have. An agent that is hallucinating
cannot call an API directly, because it does not know the URL and has no
way to authenticate.

The prompt-injection case is the one people ask about. Pryxor does not
*detect* prompt injection. It makes prompt injection *useless*. An
injected instruction can still reach the model — it cannot make the
model hold a credential it never had, or reach a system the model
cannot reach. The consequences of the injection are bounded by the
policy, because the only path from the model to the world is through
Pryxor.

### 3.3 — Every decision is written down

Pryxor records every decision in a durable audit trail. The trail holds,
for each call:

- the authenticated agent,
- the tool and its parameters (redacted at storage where configured),
- the decision (APPROVED / HOLD / BLOCKED),
- the reason (a stable code — for example `EXTERNAL_EMAIL`),
- the timestamp,
- and, for HOLDs, the human who approved or rejected the action.

The reason is a stable string the policy author chose. It is not free
text. An auditor can query `reason = 'EXTERNAL_EMAIL'` and get every
decision that fired that rule, across time. The name of the rule is
recorded with the reason, so a change to the policy is visible in the
trail even months later.

An audit trail is only useful if it is complete. Pryxor writes one entry
per decision, in the same transaction as the decision, so a crash cannot
leave a decision that was applied but not recorded, or recorded but not
applied.

---

## 4. What Pryxor is not

The boundary is not a general-purpose security system. It is a
specific control with a specific shape. Knowing the shape prevents
disappointment.

**It is not an LLM firewall.** It does not scan prompts or outputs for
malicious content. It does not attempt to detect jailbreaks. Those
belong to a different category of tool, and Pryxor does not try to be
one.

**It is not IAM.** It does not replace your identity provider. It does
not issue tokens for humans. It does not run on your users'
workstations. It provides identity for *agents*, and only for the calls
those agents route through it.

**It is not a monitoring tool.** It records decisions, but it does not
watch an agent's reasoning. If you want to know *why* an agent chose to
call a tool, that is a tracing question for the agent framework, not for
Pryxor.

**It is not a sandbox.** It does not isolate code. It sits between an
agent and a set of real systems, and it decides whether specific actions
on those systems are allowed. A sandbox is a different answer to a
different problem.

**It does not replace the model's judgement.** The model still decides
what it wants to do. Pryxor decides whether the model is allowed to do
it.

The one thing Pryxor *is*, sharply: a gate at the point where an agent
turns an intention into an effect on a real system. Everything else is
somebody else's job.

---

## 5. Where the boundary ends

The boundary is the set of actions routed through Pryxor. That sentence
is short and load-bearing. It has two practical implications.

### 5.1 — An agent that reaches a system directly is outside the boundary

If an agent has a credential for a real system *and* can reach that
system without going through Pryxor, Pryxor cannot protect that path.
This is not a bug. It is a property of any boundary that is defined by
routing.

The mitigation is to give the agent no credential for the real system,
and to route every access through Pryxor. That is the intended
deployment. Where it is not possible — an agent embedded in a system
that grants it network reach, for example — Pryxor is not the right
tool for that path, and the documentation is honest about it.

### 5.2 — The proxy is the only entry point in production

Pryxor is a proxy. In a real deployment, the agent is configured to
talk to Pryxor and to nothing else. Everything the agent does that has
an effect on the world goes through the proxy.

The same property that makes the boundary hold — the agent has no other
path — makes deployment a task of configuration rather than enforcement.
The agent is not *told* to use Pryxor; it is *given no way to avoid it*.

---

## 6. The three ideas in one picture

```text
                    agent reasoning
                          │
                          │ proposes an action
                          ▼
   ┌──────────────────────────────────────────────┐
   │                THE BOUNDARY                  │
   │                                              │
   │   1. Decided before executed.                │
   │   2. Credentials never leave this side.      │
   │   3. Every decision is written down.         │
   │                                              │
   └──────────────────────┬───────────────────────┘
                          │
                          │  executed with a credential
                          │  the agent never sees
                          ▼
                    real effect on the world
```

Everything else in the documentation is detail. The mental model is
these three properties, and the deployment practice that follows from
them.

---

## Where to go next

- To write your first policy, start with
  [the policy mental model](policies/01-mental-model.md), then
  [getting started](policies/02-getting-started.md).
- To understand what Pryxor does *not* protect against, read
  [known limitations](../KNOWN_LIMITATIONS.md).
- To deploy Pryxor with a real agent, read
  [integrations](integrations.md).