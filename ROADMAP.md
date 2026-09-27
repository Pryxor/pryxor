# Roadmap

Pryxor exists to make one thing true: **an AI agent should never be able to act
on the world without a boundary it cannot cross.**

The open-source core already delivers that boundary — interception, deterministic
policy, human approval, controlled execution, and a durable audit trail. The
roadmap below is about turning that boundary into something a serious
organisation can run at scale, with a team, under real compliance pressure.

It is driven by real usage. Priorities shift as we learn from deployments, and
every item on this page is shaped by the people actually running Pryxor in
production.

---

## Where we are today

The current release is a complete runtime security core, usable on a single node.
The precise boundaries of what it protects today are documented in
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) — read that alongside this roadmap:
the roadmap is how those limits get lifted.

- **Interception** of every tool call before it reaches a real system
- **Deterministic policy** evaluation with explicit `APPROVED / HOLD / BLOCKED` outcomes
- **Human approval** for risky actions, with a review queue that survives restarts
- **Controlled execution** — the agent proposes, Pryxor holds the credentials and performs the call
- **Durable audit** of the full decision lifecycle, with PII redaction at rest
- **Operations** — metrics, notifications, CLI administration, and a hardened single-image deployment
- **Integrations** — MCP, a Python SDK, and adapters for popular agent frameworks

This is the foundation. Everything below builds on it — nothing rewrites it.

---

## The direction

### 1. Scale beyond a single node

Today Pryxor is designed to run as one trusted instance. The next chapter is
making it run as many, safely:

- a backend that scales horizontally without a single-writer bottleneck
- shared state across replicas so a hold created on one node is reviewable on another
- high-availability deployment patterns with no data loss on failover

### 2. Make it ready for a team

Approving a hold today is an operator action. For a real team, it should feel
like a workflow:

- a management interface for reviewing holds, tuning policy, and reading analytics
- interactive approvals delivered where the team already works
- role-aware access so not everyone holds the same power
- a clear separation between the people who govern policy and the people who approve actions

### 3. Open the door to the enterprise

The controls exist; the enterprise layer around them is coming:

- multi-tenant isolation, so one deployment can safely serve many organisations
- single sign-on and fine-grained roles
- policy versioning and change management, with every decision tied to the policy that produced it
- integrations with the secret managers and identity systems enterprises already run
- a compliance story: audit export, retention, and controls mapped to the frameworks teams are held to

### 4. Deepen the security core

Runtime control is the product; it keeps getting sharper:

- richer intent and trajectory checks, so risk is judged across a sequence of actions, not one call at a time
- stronger guarantees around execution — exactly-once behaviour, resilient retries, and safe failure
- more ways to connect to real systems: new executor types and broader protocol coverage
- policy simulation, so a change can be tested against real traffic before it ships

### 5. Grow the ecosystem

The boundary is only useful where agents actually are:

- first-class support for the frameworks and protocols teams already build on
- an SDK that makes protection a one-line change
- a growing library of ready-to-use policy modules and executor templates
- examples, recipes, and reference deployments

---

## How the roadmap evolves

Pryxor is open source, and the roadmap is shaped in the open. If a capability you
need is missing, that is a signal — open an issue, describe the real problem you
are solving, and it will inform what comes next.

The core will always stay open. The path above is how it becomes something teams
can trust with the actions that matter.