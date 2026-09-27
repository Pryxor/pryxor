<!--
Thanks for sending a pull request.

Before opening it, please read CONTRIBUTING.md. Three things matter most:

1. Open an issue first for anything non-trivial — a behavior change,
   a new sector, a new integration, or anything touching auth, holds,
   execution, or the outbox. This avoids effort spent on an approach
   we cannot merge.
2. Keep the change focused. One concern per pull request.
3. Add a test. Every behavior change comes with one. Security-critical
   flows must keep their existing coverage and gain coverage for new
   paths.
-->

## What this changes

<!-- A short summary, in one or two paragraphs. -->

## Why

<!--
The problem this solves. Link the issue if there is one:
"Closes #123."
-->

## How it was tested

<!--
The commands you ran, the scenario you exercised, the cases you covered.
If a test was added or updated, say which file.
-->

## Does it touch the security boundary?

- [ ] No — this is a documentation, tooling, or isolated refactor change.
- [ ] Yes — this affects authentication, holds, execution, the outbox,
      or the audit trail. I have described the change in the section
      below.

<!-- If yes, describe the change here. -->

## Checklist

- [ ] The change is focused and clearly described.
- [ ] Tests added or updated, and the test suite passes locally.
- [ ] The linter and formatter pass (`ruff check .` / `ruff format --check .`).
- [ ] Documentation updated if behavior or configuration changed.
- [ ] No secrets, no debug artefacts, no unrelated files.
- [ ] I have read `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`.