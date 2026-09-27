# pryxor

The Python SDK for [Pryxor](https://github.com/Pryxor/pryxor) — runtime
security for AI agents. One package, three ways to protect an agent:

- **client** — call Pryxor from your agent (only depends on `requests`);
- **adapters** — protect LangChain, CrewAI, and OpenAI Agents tools;
- **mcp** — an MCP proxy and a standalone MCP server.

## Install

```bash
pip install pryxor                  # just the client
pip install "pryxor[langchain]"     # + LangChain adapter
pip install "pryxor[crewai]"        # + CrewAI adapter
pip install "pryxor[openai-agents]" # + OpenAI Agents adapter
pip install "pryxor[all]"           # everything
```

## Client

```python
from pryxor import Pryxor, PryxorHoldPendingError, PryxorBlockedError

pryxor = Pryxor()  # reads PRYXOR_AGENT_KEY and PRYXOR_URL from the environment

try:
    result = pryxor.execute(
        "send_payment",
        {"amount": 100.0, "recipient": "Fournisseur_A"},
    )
    print("Executed:", result)
except PryxorHoldPendingError as e:
    print("Waiting for human approval:", e.action_id)
except PryxorBlockedError as e:
    print("Blocked by policy:", e.reason)
```

## Framework adapters

Swap the tool class; nothing else changes. The adapter forwards the intent to
Pryxor, which decides and executes — the original function is never called.

```python
from pryxor.adapters.langchain import PryxorTool     # LangChain
from pryxor.adapters.crewai import PryxorTool        # CrewAI
from pryxor.adapters.openai_agents import PryxorTool # OpenAI Agents SDK
```

## MCP

Two entry points are installed with the package:

```bash
pryxor-mcp          # stdio proxy in front of an upstream MCP server
pryxor-mcp-server   # standalone MCP server exposing your Pryxor tools
```

```json
{
  "mcpServers": {
    "pryxor": {
      "command": "pryxor-mcp-server",
      "args": ["--agent-key", "pryxor_agent_my_agent_xxxxx", "--policy", "configs"]
    }
  }
}
```

## License

Apache 2.0.

