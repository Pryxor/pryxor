# Contributing to Pryxor

Thanks for considering a contribution. Pryxor is a runtime security layer for AI
agents, and contributions that make it safer, clearer, or easier to adopt are
genuinely valuable.

This document is about **how to contribute**. For how to run, configure, or
extend Pryxor, see the [documentation](docs/README.md).

---

## Ways to contribute

- **Code** — sectors, executors, adapters, runtime fixes, tests.
- **Security review** — adversarial thinking about the boundary and the flows.
- **Documentation** — corrections, clarifications, and examples.
- **Bug reports** — precise, reproducible reports are a real contribution.
- **Design discussion** — for anything touching the security boundary, opening
  an issue to discuss the approach before writing code is very welcome.

If you are unsure where to start, open an issue describing the problem you want
to solve.

> **Adding a sector?** A sector is a module under `sectors/` (never in
> `sectors/_framework/`, which is the toolkit). Pair it with
> `configs/sectors/<name>.json` using `"type": "code"`. If your rules fit the
> declarative language, you can skip Python entirely. See
> [`docs/policies and see 05- and 06-`](docs/policies).

---

## Development setup

Use a **virtual environment** — always. Running the tests with your system
Python (instead of the venv) is the most common source of confusing failures:
the globally installed `fastapi`/`starlette` may be a different, incompatible
pair than the project's `requirements.txt` pins.

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## Running the tests

```bash
make test
```

`make test` runs the suite through `.venv` explicitly, so it cannot pick up the
wrong Python. If you run pytest yourself, do the same:

```bash
# Linux / macOS
.venv/bin/python -m pytest tests/ -q

# Windows
.venv\Scripts\python.exe -m pytest tests/ -q
```

The suite lives in `tests/`. A few tests **skip** when an optional dependency
is absent (the framework adapters, `prometheus_client`) or when an OS feature is
unavailable (POSIX file modes, `bash`). **Skipped integration tests are not
proof the integration works** — install the framework (`pip install
"pryxor[langchain]"`) if you are touching that adapter.

Before opening a PR, also run:

```bash
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

CI runs exactly these, on Linux and Windows, across Python 3.10–3.12. See
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## Reporting bugs

A good bug report includes:

- what you expected to happen, and what happened instead,
- the exact steps to reproduce,
- the Pryxor version / commit,
- relevant configuration (with secrets removed) and logs.

Do **not** include real credentials, tokens, or customer data in an issue.

---

## Proposing a change

1. **Open an issue first** for anything non-trivial — a behaviour change, a new
   sector, a new integration, or anything touching auth, holds, execution, or the
   outbox. This avoids effort spent on an approach we cannot merge.
2. **Keep pull requests focused.** One concern per PR. A small, well-scoped diff
   is much easier to review and to trust.
3. **Describe the change** in the PR: what it does, why, and how you tested it.

---

## What we look for in a change

- **Tests.** Every behaviour change comes with a test. Security-critical flows
  (authentication, holds, execution, outbox, crash recovery) must keep their
  existing coverage and gain coverage for new paths.
- **Deterministic policy.** Sectors make clear allow / hold / block decisions
  from the call and the configuration — no hidden state, no model calls.
- **Fail-safe defaults.** When in doubt, a new rule should hold or block rather
  than silently approve.
- **Small, readable code.** Match the surrounding style; prefer clarity over
  cleverness.
- **No secrets, ever.** Never commit credentials, tokens, a populated `.env`, or
  generated state databases.

---

## Pull request checklist

- [ ] The change is focused and clearly described.
- [ ] Tests added or updated, and the test suite passes.
- [ ] The linter and formatter pass (`ruff`).
- [ ] The type checker passes (`mypy`).
- [ ] Documentation updated if behaviour or configuration changed.
- [ ] No secrets, no debug artefacts, no unrelated files.

CI runs all of the above on every pull request; a change is merged when it is
green and reviewed.

---

## Review process

- A maintainer reviews the pull request, may request changes, and merges once it
  is approved and CI is green.
- Reviews prioritise security and correctness over style.
- For changes to the security boundary, expect a closer review and possibly a
  request for a design discussion.

---

## Code of conduct

Be respectful and constructive. We are building a security tool in the open, and
good-faith questions and disagreement make it better.

The full expectations are in [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

---

## Security issues

Do not open a public issue for a vulnerability. See [`SECURITY.md`](SECURITY.md)
for how to report it privately.