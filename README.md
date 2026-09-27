<div>

  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <!-- Affiché si l'utilisateur est en mode CLAIR (défaut) -->
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.png">
    <!-- Fallback pour les vieux navigateurs -->
    <img src="assets/logo-light.png" alt="Pryxor" width="420">
  </picture>

  <h1>Pryxor</h1>

  <p>
    <strong>
      Pryxor is a runtime security layer for AI agents.<br>
      It intercepts every tool call, evaluates it against deterministic
      policies, and executes the real action — the agent never holds a
      credential.
    </strong>
  </p>

  [![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-cyan)](LICENSE)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
  [![Status](https://img.shields.io/badge/status-early--stage-yellow)](KNOWN_LIMITATIONS.md)

</div>

---

## See it in action

An agent, in Claude Desktop, is asked to send an email to an external
address. It calls the tool. Pryxor holds the call. Nothing leaves the
machine. A human approves it in the terminal, and only then does the
real action happen.

![Pryxor demo](assets/demo-video.gif)

---

## What just happened

The same call, step by step, if you prefer text to a GIF.

```text
1. The agent decides to call a tool.

        {"tool_name": "send_email",
         "parameters": {"to": "bob@external.com",
                        "subject": "Quarterly update",
                        "body": "..."}}

2. Pryxor intercepts the call.

        It authenticates the agent from its key.
        It validates the arguments against the executor's schema.
        It evaluates the call against the policy.

3. The policy says: external recipients are held.

        {"status": "HOLD",
         "action_id": "hold_0eda8977",
         "reason": "EXTERNAL_EMAIL",
         "retry": false}

4. The agent receives a HOLD. Nothing else happens.

        No email is sent. The address `bob@external.com`
        is never contacted. The agent's turn ends.

5. A human approves the hold in the terminal.

        $ python pryxor_cli.py actions approve hold_0eda8977

6. Only now does Pryxor perform the real action.

        {"status": "APPROVED",
         "execution": {"success": true, ...},
         "actor_id": "root"}
```

The agent never had the credential. It never saw the mail server's
address. It could not have turned the `HOLD` into a send, no matter how
the model behaved.

That is the whole system.

**Want to run the same walkthrough yourself, in ten minutes?**
→ **[QUICKSTART.md](QUICKSTART.md)**

---

## Why this exists

Agents are moving past generating text. They pay invoices, send emails,
modify infrastructure, open tickets. The common pattern today is to hand
an agent a token with broad permissions and trust the model to use it
correctly.

That puts the model in the wrong seat. Prompt injection, hallucinations,
and bad plans are the *normal* behaviour of a probabilistic system. When
the model is your security boundary, every one of those failures is an
incident.

Pryxor moves the boundary. The agent holds no credential. It sends an
*intention*; Pryxor decides whether the intention is allowed; only if it
is, Pryxor performs the real action with credentials the agent never
sees.

**The agent proposes. Pryxor decides. The executor acts.**

If that distinction matters to you, it is worth five minutes:
→ **[docs/concepts.md](docs/concepts.md)** — why Pryxor.

---

## The three gates

Every tool call routed through Pryxor returns one of three decisions.

| Decision | What happens | Who acts |
|---|---|---|
| ✅ **APPROVED** | The action is allowed. | Pryxor executes it with its own credential. |
| ⏸ **HOLD** | The action is queued for a human. | Nothing happens until someone approves or rejects. |
| ⛔ **BLOCKED** | The action is refused. | The real system is never contacted. |

The GIF above shows the middle gate — a HOLD. It is the interesting one.
Most tools only allow or deny. Pryxor adds the middle: *"this might be
legitimate, but it is not mine to decide"*. It is how a company that
actually has humans in the loop can put an agent next to a real system
without giving the model the final word.

**Wondering what a HOLD looks like end-to-end, with a real approval?**
→ **[QUICKSTART.md](QUICKSTART.md)** has the full walkthrough.

---

## The gateway pattern

A traditional guardrail sits *next to* the agent and tells it what it
should not do. Pryxor sits *between* the agent and the system, and does
not trust the agent at all.

```text
        traditional guardrail                    Pryxor
        ─────────────────────                    ──────

        agent holds the credential               agent holds nothing
        guardrail reads the prompt               policy reads the call
        guardrail advises the agent              Pryxor executes
        bypass is possible                       bypass is impossible
```

The last line is the point. An agent that is compromised cannot steal a
credential it was never given. An agent that is prompt-injected cannot
reach a URL it does not know. The failure mode of the model no longer
determines the failure mode of the system.

**Wondering how a policy is actually written?**
→ **[docs/policies/](docs/policies/README.md)** — one file per concern,
walked through end to end.

---

## What it looks like in practice

A policy is a folder of small JSON files. This is a complete one:

```text
configs/
├── pryxor.json          # global: default sector, hold TTL, rate limit
├── agents.json          # which agent may call which tool
├── executors/
│   └── send_email.json  # URL, auth, input schema
└── sectors/
    └── email.json       # the rules
```

The rules file is the interesting one. This is a real one — the policy
running in the GIF above:

```json
{
  "type": "declarative",
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
      "then": {
        "hold": "EXTERNAL_EMAIL",
        "message": "External recipient requires human approval."
      }
    }
  ]
}
```

Rules run in order. The first match wins. The first rule approves
internal emails; the second holds anything that reached it. Nothing in
this file is code. It is a contract a reviewer can read.

**Want to know every field, every operator, every edge case?**
→ **[docs/policies/05-sectors.md](docs/policies/05-sectors.md)** is the
rule language reference.

---

## Where it fits

Pryxor is not your whole security stack, and it does not try to be.

**What it is:**
- A gate at the point where an agent turns an intention into an effect
  on a real system.
- A source of identity for agents, and only for the calls that route
  through it.
- A durable audit trail of every decision, with the human who approved
  it recorded.

**What it is not:**
- An LLM firewall. It does not scan prompts or outputs.
- An IAM system. It does not replace your identity provider.
- A monitoring tool. It does not trace the agent's reasoning.
- A sandbox. It does not isolate code.

The one thing it is, sharply: a runtime control on the boundary that
matters — the moment an intention becomes an action.

**Wondering where the boundary ends, honestly?**
→ **[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)** is the full list of
what Pryxor protects against and what it deliberately does not.

---

## Install

Pryxor ships as two independent pieces.

**The runtime** — the proxy that intercepts calls, decides, and
executes. It is what you deploy.

```bash
git clone https://github.com/Pryxor/pryxor.git && cd pryxor
make init && make up
```

**The SDK** — what your agent imports to talk to the runtime. It is on
PyPI, and the client has a single dependency (`requests`).

```bash
pip install pryxor
```

Both are independent. You can run the runtime without the SDK (an agent
speaks HTTP directly), and you can read the SDK without deploying the
runtime yet.

**Want the full walkthrough, ending with an approved HOLD?**
→ **[QUICKSTART.md](QUICKSTART.md)** gets you there in ten minutes.

---

## Integrate

Pryxor reaches your agent whichever way your agent is built.

| Integration | Effort | Code change |
|---|---|---|
| **MCP** (Claude Desktop, Cursor, Zed) | ~5 minutes | None |
| **Python SDK** | ~15 minutes | 5 lines |
| **LangChain** | ~10 minutes | Swap `Tool` → `PryxorTool` |
| **CrewAI** | ~10 minutes | Swap `BaseTool` → `PryxorTool` |
| **OpenAI Agents SDK** | ~15 minutes | Swap `FunctionTool` → `PryxorTool` |

The MCP path requires no code: Pryxor exposes your tools as an MCP
server, and the client's config points at it instead of the upstream.
The framework paths are adapters that replace the tool class; the
framework's tool forwards its intent to Pryxor, which decides and
executes.

**Want the exact config for your framework?**
→ **[docs/integrations.md](docs/integrations.md)** has one section per
path, each with a complete, copyable example.

---

## Deploy

Pryxor runs as a single, non-root container. It is designed for a
single protected node: state lives in SQLite, and the service is
intended to run as one instance per policy.

A production deployment looks like this:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

That starts Pryxor with the hardened overlay: no published port, TLS
termination expected in front, resource caps applied, and the policy
mounted read-only. Metrics are exposed at `/metrics` for Prometheus.

**Want the hardening checklist, backup steps, and metrics reference?**
→ **[docs/operations.md](docs/operations.md)** is the deployment guide.

---

## Status

Pryxor is an **early-stage open-source release**. It is built for a
single protected node, and it is honest about where that stops.

- The runtime core is feature complete for a single-node deployment.
- The policy language covers the common cases and stops where it should.
- Multi-tenancy, SSO, horizontal scale, and a management UI are
  planned, not built.
- A few operational features — scheduled backups, a policy dry-run,
  a dashboard — are on the roadmap.

For a security product, knowing the boundaries of the guarantee matters
as much as the guarantee itself. **[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)**
is the definitive list, kept up to date with each release.

The runtime core will remain open source under Apache 2.0 permanently.

---

## Documentation map

The full documentation lives in [`docs/`](docs/README.md), in three
levels.

**Understand the idea** (no code, no config)
- [concepts.md](docs/concepts.md) — why Pryxor exists, in five minutes.

**Write a policy** (the reference)
- [policies/README.md](docs/policies/README.md) — the index.
- [policies/02-getting-started.md](docs/policies/02-getting-started.md) —
  your first policy, end to end.
- [policies/05-sectors.md](docs/policies/05-sectors.md) — the rule
  language.
- [policies/08-debugging.md](docs/policies/08-debugging.md) — when a
  rule does not fire.

**Operate and integrate**
- [integrations.md](docs/integrations.md) — MCP, SDK, framework
  adapters.
- [operations.md](docs/operations.md) — deploy, back up, observe.
- [cli.md](docs/cli.md) — the two command-line tools.

**Reference**
- [glossary.md](docs/glossary.md) — the vocabulary.
- [faq.md](docs/faq.md) — short answers to common questions.
- [ROADMAP.md](ROADMAP.md) — where the project is going.
- [CHANGELOG.md](CHANGELOG.md) — what changed in each release.

---

## Contributing

Contributions are welcome: new sectors, new executors, framework
adapters, tests, documentation, security reviews. The most useful
first contribution is often a scenario that breaks something — a call
the policy should have caught and did not.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit a change, and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the community
expectations.

Security issues: [SECURITY.md](SECURITY.md) describes how to report one
privately. Please do not open a public issue for a vulnerability.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

<div align="center">

<p><em>Intelligence proposes. Trust decides whether it can act.</em></p>

</div>