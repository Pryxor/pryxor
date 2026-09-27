# Writing policies

A Pryxor policy is a folder of small JSON files. Every tool call an
agent makes is evaluated against it, and the outcome is one of three
decisions: **APPROVED**, **HOLD**, or **BLOCKED**.

This page is the index. Each chapter below is a self-contained file:
open the one that matches your question and ignore the rest.

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

## Chapters

**[01 — Mental model](01-mental-model.md)**
How a single tool call flows through Pryxor, and why the decision happens
before the action. Read this first if you are new to Pryxor.

**[02 — Getting started](02-getting-started.md)**
Write one small policy end-to-end in five minutes: one tool, one rule,
one HOLD approved by a human.

**[03 — The five files](03-the-five-files.md)**
The reference for each of the five files in a policy folder. What
belongs in each one, and what does not.

**[04 — Executors](04-executors.md)**
The tool catalog. Every field of an executor, the templating syntax, the
auth modes, and why `inputSchema` is not optional.

**[05 — Sectors](05-sectors.md)**
The rule language. Conditions, actions, order, and the operator
reference.

**[06 — Code sectors](06-code-sectors.md)**
When JSON rules are not enough, and how to write a sector in Python.

**[07 — Patterns](07-patterns.md)**
Nine recipes for the most common policies: allowlists, thresholds,
per-agent limits, rate limits, daily budgets.

**[08 — Debugging](08-debugging.md)**
What to do when a rule does not fire the way you expected. The four
causes, in order, with concrete commands.

**[09 — Testing](09-testing.md)**
From a single `curl` to a scripted scenario, and why ground truth
matters more than the response status.

**[10 — What we don't support](10-what-we-dont-support.md)**
The deliberate limits of the rule language, and the reason for each one.

## Where to go next

- To understand *why* Pryxor is built this way, read
  [concepts](../concepts.md).
- To deploy Pryxor and manage it in production, read
  [operations](../operations.md).
- To wire Pryxor to a specific framework, read
  [integrations](../integrations.md).
