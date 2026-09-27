# HTTP API

Pryxor exposes a small, explicit HTTP API. There are two identity headers, and
every endpoint requires exactly one of them:

| Header | Identity | Used by |
|---|---|---|
| `X-Agent-Key` | An agent | `POST /v1/execute-tool` |
| `X-Admin-Key` | An operator | Everything under `/v1/holds`, `/v1/audit`, `/v1/executions`, `/v1/notifications` |

The two registries are separate: an agent key can never act as an admin, and an
admin key can never submit a tool call.

Base URL in the examples: `http://127.0.0.1:8000`.

---

## `GET /v1/health`

Unauthenticated liveness probe.

```bash
curl -s http://127.0.0.1:8000/v1/health
```

```json
{ "status": "ok", "service": "Pryxor Engine" }
```

---

## `POST /v1/execute-tool` — agent

Submit a tool call. **Requires `X-Agent-Key`.** This is the endpoint agents talk
to; the agent proposes, Pryxor decides and (when authorised) executes.

### Request

Optional header:

| Header | Purpose |
|---|---|
| `Idempotency-Key` | If present, a repeated call with the same key and same action will not execute twice. |

The body accepts several shapes; Pryxor normalizes them all to
`(tool_name, parameters)`:

```json
{ "tool_name": "send_payment", "parameters": { "amount": 100, "recipient": "Fournisseur_A" } }
```

```json
{ "type": "payment.create", "parameters": { "...": "..." } }
```

```json
{ "tool_call": { "function": { "name": "send_payment", "arguments": { "...": "..." } } } }
```

```json
{ "action": { "name": "send_payment", "parameters": { "...": "..." } } }
```

> The `agent_id` field, if present, is **ignored**. Identity is taken only from
> the authenticated key.

### Responses

Every response is HTTP 200 with a `status` field, except authentication (401).

**`APPROVED`** — allowed and executed:

```json
{
  "status": "APPROVED",
  "reason": "APPROVED",
  "message": "Authorized. 100$ to Fournisseur_A.",
  "execution": {
    "success": true,
    "idempotency_key": "direct:9f2c…",
    "status_code": 200,
    "result": { "tx_id": "tx_123" }
  }
}
```

**`HOLD`** — needs human approval; the action is persisted, not executed:

```json
{
  "status": "HOLD",
  "action_id": "hold_0eda8977",
  "reason": "EXCEEDS_SINGLE_TRANSACTION_LIMIT",
  "message": "Amount 5000$ > limit 500$. This action is pending human approval (action_id=hold_0eda8977). Do not retry.",
  "retry": false,
  "quarantine_payload": {
    "agent_id": "agent_finance_01",
    "tool_name": "send_payment",
    "parameters": { "amount": 5000, "recipient": "Fournisseur_A" }
  }
}
```

The `quarantine_payload` echoes back the call Pryxor captured so the agent can
reference it; it holds no secrets the agent did not already send. The stored copy
(in the hold and the audit trail) is redacted at rest.

A HOLD is a terminal answer for the agent: **do not retry**. An operator approves
or rejects it, and the result is visible in `/v1/executions`.

**`BLOCKED`** — rejected, never executed:

```json
{
  "status": "BLOCKED",
  "reason": "UNSUPPORTED_TOOL",
  "message": "This tool call is not authorized."
}
```

Common `reason` values:

| Reason | Meaning |
|---|---|
| `UNSUPPORTED_TOOL` | The agent is not allowed to call this tool. |
| `AGENT_NOT_AUTHORIZED_FOR_TOOL` | The tool exists but this agent may not call it. |
| `INVALID_TOOL_CALL` | No tool name could be extracted. |
| `INVALID_ARGUMENTS` | Arguments failed the executor's JSON Schema (a short `errors` list is included). |
| `MISSING_AGENT_IDENTITY` | No authenticated identity (internal guard). |
| Sector reasons | e.g. `RECIPIENT_NOT_WHITELISTED`, `VELOCITY_LIMIT_EXCEEDED`, … |

**`EXECUTION_FAILED`** — authorised, but the real action failed:

```json
{
  "status": "EXECUTION_FAILED",
  "reason": "APPROVED",
  "message": "Authorized.",
  "execution": {
    "success": false,
    "idempotency_key": "direct:…",
    "status_code": 504,
    "error": "Timeout after 10.0s"
  }
}
```

**401** — missing, invalid, or revoked key:

```json
{ "detail": "Invalid or revoked agent key." }
```

**429** — rate limited (only when `rate_limit.enabled` is true):

```json
{ "detail": "Rate limit exceeded." }
```

### Example

```bash
curl -s http://127.0.0.1:8000/v1/execute-tool \
  -H "X-Agent-Key: pryxor_agent_my_agent_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_payment","parameters":{"amount":100,"recipient":"Fournisseur_A"}}'
```

---

## Admin endpoints

All require `X-Admin-Key`. Pagination uses `limit` (1–500, default 50) and
`offset` (≥ 0). Responses include `total`, `limit`, and `offset`.

### `GET /v1/holds`

List holds, newest first.

```bash
curl -s "http://127.0.0.1:8000/v1/holds?limit=50&offset=0" \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

```json
{
  "holds": [
    {
      "action_id": "hold_0eda8977",
      "status": "PENDING",
      "agent_id": "my_agent",
      "tool_name": "send_payment",
      "parameters": { "amount": 5000, "recipient": "Fournisseur_A" },
      "reason": "EXCEEDS_SINGLE_TRANSACTION_LIMIT",
      "message": "Amount 5000$ > limit 500$.",
      "created_at": "…",
      "expires_at": "…"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/holds/{action_id}`

Fetch a single hold by id. `404` if it does not exist.

```bash
curl -s http://127.0.0.1:8000/v1/holds/hold_0eda8977 \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

### `POST /v1/holds/{action_id}/approve`

Approve a pending hold. If approved, Pryxor immediately executes the real action
through the same executor, exactly once (idempotent), and records the result.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/holds/hold_0eda8977/approve \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

```json
{
  "status": "APPROVED",
  "action_id": "hold_0eda8977",
  "message": "Hold hold_0eda8977 approved.",
  "execution": { "success": true, "result": { "tx_id": "tx_123" } }
}
```

A hold can be approved **once**. A second call returns the current status with a
message such as `Hold … is already APPROVED.` An expired hold returns
`status: "EXPIRED"`.

### `POST /v1/holds/{action_id}/reject`

Reject a pending hold. The action is never executed.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/holds/hold_0eda8977/reject \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

The approving/rejecting admin identity is recorded as the `actor_id` in the audit
trail.

### `GET /v1/audit`

The decision and lifecycle trail.

```bash
curl -s "http://127.0.0.1:8000/v1/audit?limit=50" \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

```json
{
  "audit_events": [
    { "event_type": "hold.approved", "action_id": "hold_0eda8977", "agent_id": "my_agent", "actor_id": "root", "created_at": "…" }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/executions`

Results of every real action, keyed by idempotency key.

```bash
curl -s "http://127.0.0.1:8000/v1/executions?limit=50" \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

Each entry includes `idempotency_key`, `action_id`, `agent_id`, `tool_name`,
`status` (`PENDING`, `SUCCESS`, `FAILED`), `status_code`, and `result`.

### `GET /v1/notifications`

List recent notifications and their delivery status.

```bash
curl -s "http://127.0.0.1:8000/v1/notifications?limit=100" \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

### `POST /v1/notifications/dispatch`

Force a dispatch of pending notifications. Useful after re-enabling a route.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/notifications/dispatch \
  -H "X-Admin-Key: pryxor_admin_root_xxxxx"
```

```json
{ "sent": 1, "failed": 0, "dead": 0, "errors": 0 }
```

---

## `GET /metrics`

Prometheus metrics. **Unauthenticated by design** (Prometheus scraping
convention). Expose it only on an internal network or protect it at the reverse
proxy. See [observability](../operations/observability.md).

---

## Status code summary

| Code | Meaning |
|---|---|
| 200 | A decision was reached (`APPROVED`, `HOLD`, `BLOCKED`, `EXECUTION_FAILED`). |
| 400 | Malformed request (e.g. bad `Content-Length`). |
| 401 | Missing/invalid/revoked key. |
| 404 | Admin resource not found (e.g. hold). |
| 413 | Request body larger than `PRYXOR_MAX_BODY_BYTES`. |
| 429 | Rate limited. |
| 500 | Server error. |