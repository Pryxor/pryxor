# Pryxor documentation

The full documentation for Pryxor, in reading order and by topic.

If you are new here, read [concepts.md](concepts.md) first. It takes
about five minutes and answers *why* Pryxor exists before diving into
*how*.

---

## Concepts

**[concepts.md](concepts.md)**
Why Pryxor exists, the boundary it draws, and the three ideas behind
it. No code, no config. Five minutes.

---

## Writing policies

**[policies/](policies/README.md)**
The index for the policy chapters. Ten short files, each answering one
question:

- [01 — Mental model](policies/01-mental-model.md) — how a tool call
  flows through Pryxor.
- [02 — Getting started](policies/02-getting-started.md) — your first
  policy, end to end.
- [03 — The five files](policies/03-the-five-files.md) — what belongs
  in each file.
- [04 — Executors](policies/04-executors.md) — the tool catalog.
- [05 — Sectors](policies/05-sectors.md) — the rule language.
- [06 — Code sectors](policies/06-code-sectors.md) — when JSON rules
  are not enough.
- [07 — Patterns](policies/07-patterns.md) — nine ready-to-copy
  recipes.
- [08 — Debugging](policies/08-debugging.md) — when a rule does not
  fire.
- [09 — Testing](policies/09-testing.md) — from one `curl` to a
  scripted scenario.
- [10 — What we don't support](policies/10-what-we-dont-support.md) —
  the deliberate limits.

---

## Integrations

**[integrations.md](integrations.md)**
How to wire Pryxor to your agent: the standalone MCP server, the MCP
proxy, the Python SDK, and the framework adapters for LangChain,
CrewAI, and the OpenAI Agents SDK. One section per path, each with a
complete example.

---

## Operating Pryxor

**[operations.md](operations.md)**
Deploy with Docker, harden the container, back up and restore state,
read the metrics, ship the logs, and follow the pre-production
checklist.

---

## Command-line tools

**[cli.md](cli.md)**
`admin_cli.py` for key management, `pryxor_cli.py` for daily
operations. Both tools, every command, with the configuration each one
expects.

---

## HTTP API

**[api/http-api.md](api/http-api.md)**
Every endpoint, every header, every status code, every reason code. The
reference for anything that speaks to Pryxor over HTTP without the
Python SDK.

---

## Reference

**[glossary.md](glossary.md)**
The vocabulary: agent, sector, executor, HOLD, outbox, idempotency
key, and every other term the documentation uses without defining
inline.

**[faq.md](faq.md)**
Short answers to the questions that come up most often, grouped by
topic. Each answer links to the page that explains it in full.

---

## Where to start

If you are adopting Pryxor for the first time, this is the reading
order that works best.

1. [concepts.md](concepts.md) — why Pryxor, in five minutes.
2. [policies/01-mental-model.md](policies/01-mental-model.md) — how a
   call flows through the system.
3. [policies/02-getting-started.md](policies/02-getting-started.md) —
   write and run your first policy.
4. [integrations.md](integrations.md) — wire your actual agent.
5. [operations.md](operations.md) — deploy it for real.

## Where to look things up

- A specific rule operator → [policies/05-sectors.md](policies/05-sectors.md)
- An executor field → [policies/04-executors.md](policies/04-executors.md)
- A CLI command → [cli.md](cli.md)
- An HTTP endpoint → [api/http-api.md](api/http-api.md)
- A term → [glossary.md](glossary.md)
- A specific question → [faq.md](faq.md)
- What Pryxor does not do → [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)

## Outside `docs/`

The repository root holds a few files that are not part of the
documentation proper but are worth knowing:

- **[../README.md](../README.md)** — the project's landing page.
- **[../QUICKSTART.md](../QUICKSTART.md)** — the ten-minute walkthrough.
- **[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)** — the honest
  list of what Pryxor protects against and what it does not.
- **[../CONTRIBUTING.md](../CONTRIBUTING.md)** — how to contribute.
- **[../SECURITY.md](../SECURITY.md)** — how to report a vulnerability
  privately.
- **[../ROADMAP.md](../ROADMAP.md)** — where the project is going.
- **[../CHANGELOG.md](../CHANGELOG.md)** — what changed in each
  release.