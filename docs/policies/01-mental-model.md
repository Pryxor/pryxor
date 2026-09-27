## 1. The mental model

Before writing anything, it helps to know how a single tool call flows
through Pryxor.

```text
   agent
     │
     │  POST /v1/execute-tool
     │  header: X-Agent-Key
     │  body:   {tool_name, parameters}
     ▼
┌─────────────────────────────────────────────────────┐
│                    PRYXOR                           │
│                                                     │
│  1. Authenticate     who is this agent?             │
│                      (the agent_id in the body is   │
│                       IGNORED — only the header     │
│                       counts)                       │
│                                                     │
│  2. Authorize        is this agent allowed to call  │
│                      this tool at all?              │
│                      (agents.json)                  │
│                                                     │
│  3. Validate         do the arguments match the     │
│                      executor's inputSchema?        │
│                      (executors/<tool>.json)        │
│                                                     │
│  4. Evaluate         what do the rules say about    │
│                      this specific call?            │
│                      (sectors/<name>.json)          │
│                                                     │
│  5. Decide           APPROVED / HOLD / BLOCKED      │
│                                                     │
│  6. Execute          if APPROVED, perform the real  │
│                      action with credentials the    │
│                      agent never sees.              │
│                      (executors/<tool>.json)        │
│                                                     │
│  7. Record           write the decision, the actor  │
│                      and the result to the audit.   │
└─────────────────────────────────────────────────────┘
     │
     ▼
   real system (bank, mail, internal API, ...)
```

Three ideas matter.

### 1.1 — The decision happens before the action

Pryxor never lets an agent perform an action and then decide whether it
was a good idea. The decision is made **first**. An APPROVED call is the
only one that reaches the real system. A HOLD is queued. A BLOCKED call
never exists anywhere except in the audit log.

This is why an agent that is compromised, hallucinating, or
prompt-injected still cannot do damage outside the policy: the policy
is the wall, and the agent never had the key.

### 1.2 — The agent holds no credential

The agent sends an *intention*, not a request to a real API. Pryxor's
**executor** knows the URL, the auth header, and the secret. Those live
in Pryxor's environment, never in the agent's.

This is what makes "the security boundary is the set of actions routed
through Pryxor" meaningful: an agent with no credential cannot bypass
Pryxor to reach the real system.

### 1.3 — The policy lives in a folder

Everything Pryxor needs to decide is split into five small files, each
with a single concern:

```text
configs/
├── pryxor.json          # global: default sector, hold TTL, rate limit, redaction
├── agents.json          # which agent may call which tool
├── notifications.json   # where to notify on HOLD / approved / rejected
├── executors/
│   └── <tool>.json      # one file per tool: URL, auth, inputSchema
└── sectors/
    └── <name>.json      # the rules: when a call is approved, held or blocked
```

You edit one file without fear of breaking another. `agents.json` says
*who may call what*. `executors/<tool>.json` says *how to perform the
action*. `sectors/<name>.json` says *whether this specific call should
happen*.

That separation is deliberate: authorization (who) and policy (whether)
are different questions, asked by different people at different times.

With the folder structure in mind, the fastest way to see it in action
is to write one small policy end-to-end. [The starting guide](02-getting-started.md)
does exactly that — one tool, one rule, five minutes.
