# Integrations

Pryxor reaches your agent whichever way your agent is built. There are
five paths, and they are ordered below from the one that requires no
code to the one that gives you the most control.

If you do not know which path is yours, start with the table.

| If your agent is… | Use | Effort | Code change |
|---|---|---|---|
| Claude Desktop, Cursor, Zed, or any MCP client | [MCP server](#1-mcp-standalone-server) | ~5 min | None |
| A Python script or service | [Python SDK](#2-python-sdk) | ~15 min | 5 lines |
| Built on LangChain | [LangChain adapter](#3-langchain) | ~10 min | Swap one class |
| Built on CrewAI | [CrewAI adapter](#4-crewai) | ~10 min | Swap one class |
| Built on the OpenAI Agents SDK | [OpenAI Agents adapter](#5-openai-agents-sdk) | ~15 min | Swap one class |
| Anything that can POST JSON over HTTP | [Raw HTTP](#6-raw-http) | ~15 min | As much as you want |

All five paths share the same property: **the agent never holds the
credential.** Whichever path you pick, the URL of the real system and
the auth token live in Pryxor's policy folder, not in your agent's
environment.

---

## 1. MCP standalone server

Use this if your agent is a client of the **Model Context Protocol**
(Claude Desktop, Cursor, Zed, Windsurf, Claude Code, or any tool that
speaks MCP over stdio). It requires **zero lines of code** in the agent.
The client's config points at Pryxor instead of the upstream server.

### Install

The MCP server is part of the `pryxor` package on PyPI:

```bash
pip install pryxor
```

This installs two console scripts:

- `pryxor-mcp-server` — a standalone MCP server exposing your Pryxor
  tools.
- `pryxor-mcp` — a stdio proxy that sits in front of an existing
  upstream MCP server. Covered in
  [section 1.5](#15-the-mcp-proxy-when-you-already-have-an-upstream).

### Configure Claude Desktop

Edit `claude_desktop_config.json`. The exact path depends on how Claude
Desktop was installed.

| Installation | Path |
|---|---|
| Microsoft Store (MSIX) | `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json` |
| Standard Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

Add a server entry:

```json
{
  "mcpServers": {
    "pryxor": {
      "command": "pryxor-mcp-server",
      "args": [
        "--agent-key", "pryxor_agent_ops_agent_xxxxx",
        "--pryxor-url", "http://127.0.0.1:8000",
        "--policy", "/absolute/path/to/pryxor/configs"
      ]
    }
  }
}
```

Three arguments matter.

- `--agent-key` — the key you registered with `python admin_cli.py
  register ops_agent`. It can also be set via `PRYXOR_AGENT_KEY` in the
  environment instead of in the config file.
- `--pryxor-url` — the URL the proxy is listening on. Default is
  `http://127.0.0.1:8000`.
- `--policy` — the **absolute path** to your policy folder. The MCP
  client resolves relative paths from its own working directory, which
  is usually not what you expect. Use an absolute path if you are not
  certain.

### Restart the client

Close Claude Desktop completely (from the system tray, not just the
window), then reopen it. In a new conversation, ask the model to list
its available tools. You should see every tool declared under
`configs/executors/` — `send_email`, `send_payment`, or whatever is in
your policy.

When the model calls one of them, Pryxor evaluates the call. The three
decisions map to the client like this:

| Pryxor decision | The MCP client sees |
|---|---|
| `APPROVED` | The executor's real result, as the tool output |
| `BLOCKED` | A tool error with the reason |
| `HOLD` | A non-error result carrying the `action_id`, so the model knows a human is involved and does not retry |

### What the model sees

The model sees **only** the tool name, its `description`, and its
`inputSchema`. It never sees the URL, the auth mode, or the token. That
is the point: even a compromised model cannot learn where the real
system lives or how to reach it.

If you want to add a tool to the surface, add an executor file under
`configs/executors/` and restart the MCP client. The tool appears.

### 1.5 The MCP proxy (when you already have an upstream)

The standalone server exposes Pryxor's own tools. A different scenario:
you already have an MCP server you trust, and you want Pryxor to
*intercept* calls to it rather than replace it. That is what
`pryxor-mcp` does.

```json
{
  "mcpServers": {
    "pryxor-filesystem": {
      "command": "pryxor-mcp",
      "args": [
        "--agent-key", "pryxor_agent_ops_agent_xxxxx",
        "--pryxor-url", "http://127.0.0.1:8000",
        "--upstream", "npx", "-y",
        "@modelcontextprotocol/server-filesystem", "/tmp"
      ]
    }
  }
}
```

Every `tools/call` from the client passes through Pryxor first. The
behavior depends on the decision:

- **`BLOCKED`** — the call is refused, the upstream is never contacted.
- **`HOLD`** — the call is queued for human approval, the upstream is
  never contacted until someone approves it.
- **`APPROVED` and Pryxor has an executor for the tool** — Pryxor
  performs the action itself. The upstream is bypassed.
- **`APPROVED` and Pryxor has no executor for the tool** — the call is
  forwarded to the upstream, unchanged. This is "transparent mode": you
  get the decision and the audit, and the upstream still does the work.

The last case is the interesting one. It lets you add a policy to a
server you did not write, without changing how the server works.

---

## 2. Python SDK

Use this if your agent is a Python program. The SDK is the smallest
possible client: one dependency (`requests`), no build step, one method
to call.

### Install

```bash
pip install pryxor
```

The client lives at `pryxor.Pryxor`. The same package ships the
framework adapters and the MCP server, but none of them are loaded until
you import them, and the client has no dependency on them.

### Export the agent key

Get the key once, from the runtime:

```bash
python admin_cli.py register ops_agent
```

Copy the returned `api_key` and set it in your agent's environment:

```bash
export PRYXOR_AGENT_KEY=pryxor_agent_ops_agent_xxxxx
export PRYXOR_URL=http://127.0.0.1:8000
```

### Replace the direct call

The change in your agent is a single function. Where you used to call
the real system directly:

```python
import requests

requests.post(
    "https://api.bank.example.com/v1/payments",
    headers={"Authorization": f"Bearer {os.environ['BANK_API_TOKEN']}"},
    json={"amount": 100, "recipient": "Fournisseur_A"},
)
```

you now call Pryxor:

```python
from pryxor import Pryxor, PryxorBlockedError, PryxorHoldPendingError

pryxor = Pryxor()  # reads PRYXOR_AGENT_KEY and PRYXOR_URL from the environment

try:
    result = pryxor.execute(
        "pay_invoice",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
    )
    print("Executed:", result)   # the real API payload returned by the executor

except PryxorHoldPendingError as e:
    print("Waiting for human approval:", e.action_id)

except PryxorBlockedError as e:
    print("Blocked by policy:", e.reason)
```

Two lines changed, one dependency added, and the agent no longer holds
the bank token.

### Handling the three outcomes

`execute()` raises on every non-success outcome, so your control flow
stays clean. The three exceptions map exactly to the three gates:

| Exception | Meaning | What the agent should do |
|---|---|---|
| `PryxorHoldPendingError` | A HOLD was created. | Stop. Report the `action_id`. Do not retry. |
| `PryxorBlockedError` | The call was refused. | Stop. Report the `reason`. |
| `PryxorExecutionError` | The action was approved but the real call failed. | Handle as an error, not a decision. |

Catching `PryxorError` catches all three, plus transport errors and
malformed responses, in one block. Use the specific subclasses when you
want to branch on the outcome.

### When you want the raw response

`execute()` is the high-level method. It returns the executor's result
directly and raises on any non-success. When you want to branch yourself
without exceptions, use `execute_raw()`:

```python
raw = pryxor.execute_raw("pay_invoice", {"amount": 5000, "recipient": "X"})

if Pryxor.is_hold(raw):
    print("Pending approval:", raw["action_id"])
elif Pryxor.is_blocked(raw):
    print("Blocked:", raw["reason"])
elif Pryxor.is_approved(raw):
    print("Result:", raw["execution"]["result"])
```

`execute_raw()` never raises on business decisions. It only raises on
transport failures — Pryxor unreachable, invalid key, malformed
response — which are errors, not decisions.

### Making a retry safe

Pass an `Idempotency-Key` to make a retry safe. Two calls with the same
key produce one execution, not two:

```python
result = pryxor.execute(
    "pay_invoice",
    {"amount": 100.0, "recipient": "Fournisseur_A"},
    idempotency_key="order-42",
)
```

If the network fails after the action already happened, retrying with
the same key returns the cached result instead of executing again. This
is the SDK counterpart of what Pryxor does internally for HOLDs.

The full client reference, including every exception and the admin
helpers, is in the SDK's own README on PyPI.

---

## 3. LangChain

Use this if your agent is built on LangChain. The adapter provides a
`PryxorTool` that replaces `Tool` in your tool list. Nothing else in the
agent changes.

### Install

```bash
pip install "pryxor[langchain]"
```

This pulls in `langchain-core` and the Pryxor adapter together.

### Replace one class

Before:

```python
from langchain_core.tools import Tool

tools = [
    Tool(
        name="pay_invoice",
        description="Send a payment to a recipient.",
        func=pay_invoice_fn,
    ),
]
```

After:

```python
from pryxor import Pryxor
from pryxor.adapters.langchain import PryxorTool

pryxor = Pryxor()

tools = [
    PryxorTool(
        pryxor_client=pryxor,
        name="pay_invoice",
        description="Send a payment to a recipient.",
        args_schema=PaymentArgs,   # optional: a Pydantic model
    ),
]
```

The `args_schema` is optional. Without it, Pryxor still validates the
call against the executor's `inputSchema` at runtime; the schema is a
hint for the model, not the enforcement point.

### What the tool does

The tool does nothing locally. It forwards its intent to Pryxor and maps
the outcome:

| Pryxor decision | Default behavior |
|---|---|
| `APPROVED` | Returns the executor's real result as a JSON string. |
| `BLOCKED` | Raises `PryxorProtectedToolError` with the reason. |
| `HOLD` | Raises `PryxorHoldPending` carrying `action_id`, `reason`, `message`. |

Two optional flags let the model see the decision instead of crashing:

```python
PryxorTool(..., raise_on_block=False)   # returns "[BLOCKED] <reason>"
PryxorTool(..., raise_on_hold=False)    # returns "[HOLD] action_id=…"
```

This is usually what you want in a reasoning agent: the model can read
the refusal and continue its plan rather than fail the whole chain.

### Async

`_arun()` is supported and delegates to the synchronous path. Calls to
Pryxor are short-lived HTTP requests; they do not block the event loop
for long.

### Converting an existing tool

If you already have a `Tool` instance and do not want to rebuild it:

```python
from pryxor.adapters.langchain import PryxorTool

protected = PryxorTool.from_langchain_tool(original, pryxor_client=pryxor)
```

The original function is ignored. Only its name, description, and
schema are kept.

---

## 4. CrewAI

Use this if your agent is a CrewAI crew. The adapter provides a
`PryxorTool` that replaces `BaseTool` in the tools list of an agent.

### Install

```bash
pip install "pryxor[crewai]"
```

### Replace one class

Before:

```python
from crewai.tools import BaseTool


class SendEmailTool(BaseTool):
    name: str = "send_email"
    description: str = "Send an email."
    args_schema: type[BaseModel] = EmailArgs

    def _run(self, to: str, subject: str, body: str) -> str:
        return real_send_email(to, subject, body)


tools = [SendEmailTool()]
```

After:

```python
from crewai.tools import BaseTool
from pryxor import Pryxor
from pryxor.adapters.crewai import PryxorTool

pryxor = Pryxor()

tools = [
    PryxorTool(
        pryxor_client=pryxor,
        name="send_email",
        description="Send an email.",
        args_schema=EmailArgs,
    ),
]
```

### What the tool does

The same mapping as LangChain:

| Pryxor decision | Default behavior |
|---|---|
| `APPROVED` | Returns the executor's real result as a JSON string. |
| `BLOCKED` | Raises `PryxorProtectedToolError` with the reason. |
| `HOLD` | Raises `PryxorHoldPending` carrying `action_id`, `reason`, `message`. |

The same `raise_on_block` / `raise_on_hold` flags are available.

### Converting an existing tool

```python
from pryxor.adapters.crewai import PryxorTool

protected = PryxorTool.from_crewai_tool(original, pryxor_client=pryxor)
```

Same as the LangChain adapter: name, description, and schema are kept;
the original `_run` is not called.

---

## 5. OpenAI Agents SDK

Use this if your agent is built on the OpenAI Agents SDK (`import
agents`). The adapter provides a `PryxorTool` factory that returns a
`FunctionTool`.

### Install

```bash
pip install "pryxor[openai-agents]"
```

### Replace one class

Before:

```python
from agents import FunctionTool

async def _invoke(ctx, args_json):
    args = json.loads(args_json)
    return await real_send_email(**args)

tools = [
    FunctionTool(
        name="send_email",
        description="Send an email.",
        params_json_schema={...},
        on_invoke_tool=_invoke,
    ),
]
```

After:

```python
from pryxor import Pryxor
from pryxor.adapters.openai_agents import PryxorTool

pryxor = Pryxor()

tools = [
    PryxorTool(
        pryxor_client=pryxor,
        name="send_email",
        description="Send an email.",
        params_json_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    ),
]
```

The OpenAI Agents SDK uses JSON Schema, not a Pydantic model. That is the
one difference from the LangChain and CrewAI adapters.

### What the tool does

The same mapping again. Two differences from the other adapters:

- The invocation is asynchronous (`on_invoke_tool(ctx, args_json)`),
  because that is how the OpenAI SDK calls tools.
- Malformed JSON input never reaches Pryxor. Invalid JSON, non-object
  JSON, and empty strings are all handled before the call is made; the
  client receives a structured error string and the policy is not
  invoked.

### Converting an existing tool

```python
from pryxor.adapters.openai_agents import PryxorTool

protected = PryxorTool.from_openai_tool(original, pryxor_client=pryxor)
```

---

## 6. Raw HTTP

If your agent is not Python, or you want the smallest possible
dependency, call the HTTP API directly. The endpoint is a single POST
and the request shape is fixed.

### The call

```http
POST /v1/execute-tool HTTP/1.1
Host: 127.0.0.1:8000
X-Agent-Key: pryxor_agent_ops_agent_xxxxx
Content-Type: application/json

{
  "tool_name": "send_email",
  "parameters": {
    "to": "bob@external.com",
    "subject": "Hi",
    "body": "Hello."
  }
}
```

An `Idempotency-Key` header is optional and, if present, makes the call
safe to retry.

### The response

`200 OK` with a JSON body. The `status` field is the decision.

```json
{
  "status": "APPROVED",
  "reason": "APPROVED",
  "message": "Action authorized.",
  "execution": {
    "success": true,
    "idempotency_key": "direct:9f2c1a4b6e8d0f3a5c7b9e1d2f4a6c8e",
    "status_code": 200,
    "result": { "...": "..." }
  }
}
```

Three possible status values, plus two error cases.

| `status` | What it means | Where the details are |
|---|---|---|
| `APPROVED` | The action was executed. | `execution.result` |
| `HOLD` | A human must approve. | `action_id`, `reason`, `message` |
| `BLOCKED` | The action was refused. | `reason`, `message` |
| `EXECUTION_FAILED` | Approved, but the real call failed. | `execution.error`, `execution.status_code` |
| `401` (HTTP) | The agent key is missing or invalid. | The body is a short `detail` string. |

The endpoint is `POST /v1/execute-tool`. The full request and response
schemas — including every reason code — are in
[the HTTP API reference](api/http-api.md).

### What to do on each outcome

The same rule for every client: **a HOLD is a terminal answer**. Do not
retry it, do not route it anywhere else. Report the `action_id` and stop.

A `BLOCKED` call is also terminal. The reason is a stable string the
policy author chose; log it, report it, do not retry.

An `EXECUTION_FAILED` call is an error, not a decision. You may retry it
with the same idempotency key; if the underlying failure is transient,
the retry will succeed, and if the previous attempt did reach the real
system, the key ensures it is not executed twice.

---

## A note on framework versions

The three adapters target recent SDK versions of their respective
frameworks. They import the framework lazily and define a stub if it is
not installed, so `import pryxor` never fails because of a missing
optional dependency. If a framework version changes its interface in a
breaking way, the adapter will raise a clear error rather than silently
misbehave.

The one thing every adapter has in common: the framework's tool becomes
a **proxy** to Pryxor. It does not perform the action locally, it does
not know the URL, and it cannot bypass the policy. The enforcement point
is the same regardless of which framework you use — that is the whole
design.