---
name: Bug report
about: Something does not behave as documented
title: "[bug] "
labels: ["bug", "triage"]
assignees: []
---

<!--
Before opening a bug report, please check:

1. The same issue does not already exist (search the open and closed issues).
2. Your Pryxor version matches the one referenced in the issue template below.
3. The problem reproduces against a fresh checkout of the current main branch,
   not against a version you have locally modified.

If you are reporting a security vulnerability, do NOT open an issue.
See SECURITY.md for how to report it privately.
-->

## What happened

<!-- A short, factual description of what you observed. -->

## What you expected

<!-- A short, factual description of what you expected instead. -->

## Steps to reproduce

<!--
The exact commands, the exact payload, and the exact configuration.
A bug report is only as good as its reproduction.

If the bug involves a policy, paste the smallest sector that shows the
problem — remove everything that is not needed to trigger it.
-->

1.
2.
3.

## Minimal reproduction

<!--
If you can, provide a copy-paste-ready snippet. For example:

```
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email","parameters":{...}}'
```
-->

```text
( paste here )
```

## Environment

- Pryxor version / commit:
- Deployment: Docker / local Python / other
- Operating system:
- Python version (if running locally):

## Logs

<!--
The relevant lines from `make logs` or the proxy terminal.
Please include the request_id if present.
Do not include real credentials or customer data.
-->

```text
( paste here )
```

## Additional context

<!--
Anything else that might help: a related issue, a workaround you found,
a screenshot, a hunch. Optional.
-->