# Known limitations

Pryxor is an early-stage release. It is built for a **single protected node**,
and it is honest about where that stops. Read this before deploying anything
real — for a security product, knowing the boundary matters as much as the
guarantee.

## What it protects

Pryxor controls the tool calls routed through it: it authenticates the agent,
validates the arguments, applies deterministic policy, holds risky actions for a
human, and performs the real action itself — so the agent never holds the
credential.

## What it does not

- **Anything that bypasses Pryxor.** The boundary is the set of actions routed
  through it. If an agent can reach a real system by another path, that path is
  outside the boundary. Give the agent no credential for the real system.
- **Prompt injection itself.** Pryxor stops the *consequences* of a bad tool
  call — an injected instruction still cannot execute an unauthorized action —
  but it does not detect the injection.
- **A compromised agent host.** An attacker holding a valid agent key can submit
  calls as that agent.

## Where it is not ready yet

- **One instance, one node.** State lives in a single SQLite database. Run one
  Pryxor per database; horizontal scaling is future work.
- **Not multi-tenant.** One organisation per instance.
- **No management UI.** Reviewing holds is the CLI or the API. There is no web
  console and no interactive approval in Slack or Teams.
- **No RBAC or SSO.** An admin key is all-powerful — treat it like a root
  credential.
- **TLS is out of scope.** Terminate it in front (nginx, Caddy, Traefik), and
  keep the runtime bound to `127.0.0.1` by default.

## What this means in practice

- **Good for:** evaluating the concept, staging, and a single production node
  behind a reverse proxy, with a small number of agents.
- **Not yet for:** multi-tenant SaaS, high-availability clusters, or regulated
  environments that require RBAC, SSO, or a tamper-evident audit trail.

Where the project is heading: [`ROADMAP.md`](ROADMAP.md). If a limitation here
blocks a real use case for you, open an issue describing the problem — that is
exactly the signal we want.