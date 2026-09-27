## 9. Testing a policy

A policy is code. It deserves tests. This section covers three levels:
a single call with curl, a scripted scenario, and the local sandbox that
ships with Pryxor.

**In this file:** [The one-call test](#91--the-one-call-test) ·
[Scripted scenario](#92--a-scripted-scenario) ·
[Ground truth](#93--ground-truth-matters) ·
[Idempotence](#94--idempotence-is-a-scenario-not-an-assertion) ·
[Local sandbox](#95--the-local-sandbox)

### 9.1 — The one-call test

The fastest test is a single `curl` against a running Pryxor. Use it
while you iterate on a rule: write the rule, restart, send the call,
read the response.

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: $PRYXOR_AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_email",
       "parameters":{"to":"bob@external.com","subject":"Hi","body":"x"}}' \
  | python -m json.tool
```

The response body is what you assert against. If you are checking for a
`HOLD`, confirm `"status": "HOLD"` and the `reason` you expect. If you
are checking for an `APPROVED` execution, confirm
`"execution.success": true`.

This is not a test suite. It is the loop you use *while writing* the
suite — the same way you would run a single `pytest` case while
debugging a function.

### 9.2 — A scripted scenario

A scenario is a list of calls with expected verdicts. Two ways to write
one: in a shell script or in Python. Python is worth the small extra
setup because you can assert on the full response, not just the status
line.

A minimal scenario runner:

```python
import json
import os
import urllib.request

PRYXOR_URL = os.environ.get("PRYXOR_URL", "http://127.0.0.1:8000")
AGENT_KEY = os.environ["PRYXOR_AGENT_KEY"]

SCENARIOS = [
    {
        "name": "internal email is approved",
        "tool": "send_email",
        "parameters": {"to": "alice@company.local", "subject": "Hi", "body": "x"},
        "expect_status": "APPROVED",
    },
    {
        "name": "external email is held",
        "tool": "send_email",
        "parameters": {"to": "bob@external.com", "subject": "Hi", "body": "x"},
        "expect_status": "HOLD",
        "expect_reason": "EXTERNAL_EMAIL",
    },
    {
        "name": "subject with password is blocked",
        "tool": "send_email",
        "parameters": {"to": "alice@company.local", "subject": "my password", "body": "x"},
        "expect_status": "BLOCKED",
        "expect_reason": "SENSITIVE_SUBJECT",
    },
]


def call(tool, parameters):
    req = urllib.request.Request(
        f"{PRYXOR_URL}/v1/execute-tool",
        data=json.dumps({"tool_name": tool, "parameters": parameters}).encode(),
        headers={"X-Agent-Key": AGENT_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    passed = 0
    failed = []

    for s in SCENARIOS:
        resp = call(s["tool"], s["parameters"])
        ok = resp.get("status") == s["expect_status"]
        if "expect_reason" in s:
            ok = ok and resp.get("reason") == s["expect_reason"]

        print(f"[{'PASS' if ok else 'FAIL'}] {s['name']}")
        if not ok:
            print(f"        got: {json.dumps(resp, indent=2)[:400]}")
            failed.append(s["name"])
        else:
            passed += 1

    print(f"\n{passed}/{len(SCENARIOS)} scenarios passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

Save it as `tests/policy_test.py`, run it after every policy change:

```bash
python tests/policy_test.py
```

Exit code is `0` on success, `1` on any failure. That is enough to
wire it into a pre-commit hook or CI later.

### 9.3 — Ground truth matters

A scripted scenario that only checks the response status verifies what
Pryxor *decided*. It does not verify what actually *happened*. Those are
not the same thing.

For an APPROVED call that hits a real external service, check the
service. If your executor posts to a mock bank, query the mock bank for
its balance and confirm it changed by the amount you expect. If your
executor sends email through an SMTP sink, confirm the message is in the
sink's folder. The `pryxor-demo` repository ships a working example: a
`bank_api` with an account endpoint, a file-based SMTP sink, and a
scenario runner that checks both the response and the ground truth.

The pattern:

```python
# Before
before = get_bank_balance()

# The call
resp = call("pay_invoice", {"amount": 800, "recipient": "Fournisseur_A"})
assert resp["status"] == "APPROVED"

# After
after = get_bank_balance()
assert before - after == 800
```

Without the ground-truth check, an executor that silently failed to
reach the real API would still show an APPROVED response — the decision
was correct, the execution was not. The audit trail records the failure
under `EXECUTION_FAILED`, but the scenario itself would pass.

### 9.4 — Idempotence is a scenario, not an assertion

The most important test after correctness is **idempotence**: the same
call twice with the same key results in exactly one real action. You
cannot test this with a single call. Send two, and check both the
response and the ground truth.

```python
# Before
before_balance = get_bank_balance()

# Two calls with the same client-supplied idempotency key
r1 = call_with_idem("pay_invoice", {"amount": 100, "recipient": "Fournisseur_A"}, "test-key")
r2 = call_with_idem("pay_invoice", {"amount": 100, "recipient": "Fournisseur_A"}, "test-key")

# After
after_balance = get_bank_balance()

assert r1["status"] == "APPROVED"
assert r2["status"] == "APPROVED"
assert before_balance - after_balance == 100  # one charge, not two
```

The header is sent by the client:

```python
def call_with_idem(tool, parameters, idem):
    req = urllib.request.Request(
        f"{PRYXOR_URL}/v1/execute-tool",
        data=json.dumps({"tool_name": tool, "parameters": parameters}).encode(),
        headers={
            "X-Agent-Key": AGENT_KEY,
            "Content-Type": "application/json",
            "Idempotency-Key": idem,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())
```

Two behaviors matter here. The first call executes and stores the
result under the key. The second call finds the key, returns the cached
result, and does **not** re-execute. The bank sees one charge; the agent
sees two APPROVED responses with identical `execution.result`.

This is the property that makes retries safe. Test it explicitly; it is
easy to break with a small change to the executor or the sector.

### 9.5 — The local sandbox

The `pryxor-demo` repository is a self-contained sandbox designed for
exactly this: running scenarios against a real Pryxor with no real
consequences. It ships:

- A mock bank API with an account balance and an idempotency-key check.
- A dependency-free SMTP sink that stores every email as a file.
- A set of inbox messages, workspace files, and a vendor portal page
  used to exercise adversarial scenarios.
- A scripted test agent that replays a fixed list of calls and asserts
  on both the response and the ground truth.

To use it:

```bash
cd pryxor-demo
make up        # start the mock services
make seed      # reset workspace, mail sink, transcripts
```

Then point Pryxor at the sandbox policy that ships with the demo
(`configs.demo/` in the Pryxor repository), start Pryxor, register an
agent, and run:

```bash
python test_agent.py
```

The sandbox has two goals. First, it is the way to write your own
scenarios without needing an external service: everything is local,
deterministic, and reset between runs. Second, it is the way to check
that a policy change did not break anything else — the whole scenario
list runs in seconds.

For a first policy, the one-call test from 9.1 is enough. Once you have
three or four rules and have been bitten once by a rule that silently
stopped matching, add the scripted scenario from 9.2. The ground-truth
check from 9.3 is what catches the execution failures that a status-only
test would miss.