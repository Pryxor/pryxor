#Requires -Version 5.1
<#
.SYNOPSIS
    Pryxor — end-to-end smoke test (Windows / PowerShell).

.DESCRIPTION
    Starts an isolated Pryxor stack on port 8001 with a mock executor,
    exercises the full pipeline (auth, policy, execution, HOLD, approve,
    reject, audit), then tears everything down.

.PARAMETER Keep
    If set, keep the stack running after the test (useful for debugging).
    Prints the teardown command at the end.

.PARAMETER ComposeFile
    Path to the base compose file. Defaults to docker-compose.yml.

.EXAMPLE
    .\scripts\smoke_test.ps1

.EXAMPLE
    .\scripts\smoke_test.ps1 -Keep

.NOTES
    Exit code 0 = all checks passed.
    Exit code 1 = at least one check failed.
#>

[CmdletBinding()]
param(
    [switch]$Keep,
    [string]$ComposeFile = "docker-compose.yml"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- Move to repo root -----------------------------------------------
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

# --- Config ----------------------------------------------------------
$ComposeSmoke = "docker-compose.smoke.yml"
$Service      = "pryxor"
$BaseUrl      = "http://127.0.0.1:8001"

# --- Colors ----------------------------------------------------------
function Write-Step { param($msg) Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "  ✅ $msg" -ForegroundColor Green }
function Write-Info { param($msg) Write-Host "  ℹ  $msg" -ForegroundColor Yellow }
function Write-Fail {
    param($msg)
    Write-Host "  ❌ $msg" -ForegroundColor Red
    throw "Smoke test failed: $msg"
}

# --- Helpers ---------------------------------------------------------
function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments)][string[]]$Args)
    $all = @("-f", $ComposeFile, "-f", $ComposeSmoke) + $Args
    & docker compose @all
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($Args -join ' ') exited with code $LASTEXITCODE"
    }
}

function Get-AdminKey {
    param([string]$AdminId)
    $raw = Invoke-Compose exec -T $Service python admin_cli.py register-admin $AdminId 2>$null
    # admin_cli writes human messages to stderr and JSON to stdout.
    # Invoke-Compose captures both, so we look for the first line that starts with '{'.
    $jsonText = ($raw | Where-Object { $_ -match '^\s*\{' } | Select-Object -First 1)
    if (-not $jsonText) { throw "Could not parse admin key output: $raw" }
    $obj = $jsonText | ConvertFrom-Json
    return $obj.api_key
}

function Get-AgentKey {
    param([string]$AgentId)
    $raw = Invoke-Compose exec -T $Service python admin_cli.py register $AgentId 2>$null
    $jsonText = ($raw | Where-Object { $_ -match '^\s*\{' } | Select-Object -First 1)
    if (-not $jsonText) { throw "Could not parse agent key output: $raw" }
    $obj = $jsonText | ConvertFrom-Json
    return $obj.api_key
}

function Get-HttpStatus {
    param(
        [string]$Method,
        [string]$Url,
        [hashtable]$Headers = @{},
        [string]$Body = $null
    )
    try {
        $params = @{
            Method      = $Method
            Uri         = $Url
            Headers     = $Headers
            ErrorAction = "Stop"
        }
        if ($Body) {
            $params["Body"]        = $Body
            $params["ContentType"] = "application/json"
        }
        $resp = Invoke-WebRequest @params
        return [int]$resp.StatusCode
    } catch {
        if ($_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode.value__
        }
        throw
    }
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Url,
        [hashtable]$Headers = @{},
        [string]$Body = $null
    )
    $params = @{ Method = $Method; Uri = $Url; Headers = $Headers }
    if ($Body) {
        $params["Body"]        = $Body
        $params["ContentType"] = "application/json"
    }
    return Invoke-RestMethod @params
}

# --- Cleanup ---------------------------------------------------------
$cleanupDone = $false
function Invoke-Cleanup {
    if ($script:cleanupDone) { return }
    $script:cleanupDone = $true

    if ($Keep) {
        Write-Info "KEEP flag set — leaving the smoke stack running."
        Write-Info "Teardown:"
        Write-Info "  docker compose -f $ComposeFile -f $ComposeSmoke down -v"
        return
    }
    Write-Step "Tearing down smoke stack"
    try {
        Invoke-Compose down -v --remove-orphans 2>$null | Out-Null
    } catch {
        # Ignore teardown errors — the test outcome is what matters.
    }
    Write-Ok "Smoke stack removed"
}

trap {
    Write-Host ""
    Write-Host "Unexpected error: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Invoke-Cleanup
    Pop-Location
    exit 1
}

# =====================================================================
# SETUP
# =====================================================================

Write-Step "Starting smoke stack"
try { Invoke-Compose down -v --remove-orphans 2>$null | Out-Null } catch {}
Invoke-Compose up -d --build | Out-Null
Write-Ok "Container started"

Write-Step "Waiting for /v1/health"
$ready = $false
for ($i = 1; $i -le 45; $i++) {
    try {
        $code = Get-HttpStatus -Method "GET" -Url "$BaseUrl/v1/health"
        if ($code -eq 200) {
            Write-Ok "Health OK after ${i}s"
            $ready = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Fail "Health check timed out after 45s. Try: .\scripts\smoke_test.ps1 -Keep"
}

Write-Step "Registering admin key"
$AdminKey = Get-AdminKey -AdminId "smoke_admin"
if (-not $AdminKey) { Write-Fail "Empty admin key" }
Write-Ok "Admin key: $($AdminKey.Substring(0, [Math]::Min(32, $AdminKey.Length)))..."

Write-Step "Registering agent key"
$AgentKey = Get-AgentKey -AgentId "agent_smoke_01"
if (-not $AgentKey) { Write-Fail "Empty agent key" }
Write-Ok "Agent key: $($AgentKey.Substring(0, [Math]::Min(32, $AgentKey.Length)))..."

$AdminHeaders = @{ "X-Admin-Key" = $AdminKey }
$AgentHeaders = @{ "X-Agent-Key" = $AgentKey }

# =====================================================================
# TESTS
# =====================================================================

$testNumber = 0
function Next-Test { param($label) $script:testNumber++; Write-Step "$($script:testNumber). $label" }

# --- 1 --------------------------------------------------------------
Next-Test "Public health endpoint"
$code = Get-HttpStatus -Method "GET" -Url "$BaseUrl/v1/health"
if ($code -ne 200) { Write-Fail "expected 200, got $code" }
Write-Ok "200"

# --- 2 --------------------------------------------------------------
Next-Test "Admin endpoint without key"
$code = Get-HttpStatus -Method "GET" -Url "$BaseUrl/v1/holds"
if ($code -ne 401) { Write-Fail "expected 401, got $code" }
Write-Ok "401"

# --- 3 --------------------------------------------------------------
Next-Test "Admin endpoint with agent key (must be rejected)"
$code = Get-HttpStatus -Method "GET" -Url "$BaseUrl/v1/holds" -Headers $AgentHeaders
if ($code -ne 401) { Write-Fail "expected 401, got $code" }
Write-Ok "401 (agent key rejected)"

# --- 4 --------------------------------------------------------------
Next-Test "Admin endpoint with admin key"
$code = Get-HttpStatus -Method "GET" -Url "$BaseUrl/v1/holds" -Headers $AdminHeaders
if ($code -ne 200) { Write-Fail "expected 200, got $code" }
Write-Ok "200 (admin key accepted)"

# --- 5 --------------------------------------------------------------
Next-Test "Agent endpoint without key"
$body = '{"tool_name":"send_payment","parameters":{"amount":100.0,"recipient":"Fournisseur_A"}}'
$code = Get-HttpStatus -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Body $body
if ($code -ne 401) { Write-Fail "expected 401, got $code" }
Write-Ok "401"

# --- 6 --------------------------------------------------------------
Next-Test "Agent endpoint with admin key (must be rejected)"
$code = Get-HttpStatus -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $AdminHeaders -Body $body
if ($code -ne 401) { Write-Fail "expected 401, got $code" }
Write-Ok "401 (admin key rejected)"

# --- 7 --------------------------------------------------------------
Next-Test "APPROVED flow — small payment"
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $AgentHeaders -Body $body
if ($resp.status -ne "APPROVED") { Write-Fail "expected APPROVED, got $($resp.status)" }
if (-not $resp.execution.success) { Write-Fail "execution.success is false" }
Write-Ok "APPROVED + executed (idempotency_key=$($resp.execution.idempotency_key))"

# --- 8 --------------------------------------------------------------
Next-Test "BLOCKED flow — unsupported tool"
$blockBody = '{"tool_name":"delete_everything","parameters":{}}'
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $AgentHeaders -Body $blockBody
if ($resp.status -ne "BLOCKED") { Write-Fail "expected BLOCKED, got $($resp.status)" }
if ($resp.reason -ne "UNSUPPORTED_TOOL") { Write-Fail "expected reason UNSUPPORTED_TOOL, got $($resp.reason)" }
Write-Ok "BLOCKED ($($resp.reason))"

# --- 9 --------------------------------------------------------------
Next-Test "HOLD flow — amount over limit"
$holdBody = '{"tool_name":"send_payment","parameters":{"amount":5000.0,"recipient":"Fournisseur_A"}}'
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $AgentHeaders -Body $holdBody
if ($resp.status -ne "HOLD") { Write-Fail "expected HOLD, got $($resp.status)" }
$ActionId = $resp.action_id
if (-not $ActionId) { Write-Fail "empty action_id" }
Write-Ok "HOLD created ($ActionId)"

# --- 10 -------------------------------------------------------------
Next-Test "Approve without admin key"
$code = Get-HttpStatus -Method "POST" -Url "$BaseUrl/v1/holds/$ActionId/approve"
if ($code -ne 401) { Write-Fail "expected 401, got $code" }
Write-Ok "401"

# --- 11 -------------------------------------------------------------
Next-Test "Approve with admin key"
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/holds/$ActionId/approve" -Headers $AdminHeaders
if ($resp.status -ne "APPROVED") { Write-Fail "expected APPROVED, got $($resp.status)" }
if (-not $resp.execution.success) { Write-Fail "HOLD execution.success is false" }
Write-Ok "HOLD approved + executed"

# --- 12 -------------------------------------------------------------
Next-Test "Double approve is refused"
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/holds/$ActionId/approve" -Headers $AdminHeaders
if ($resp.message -notmatch "already") { Write-Fail "expected 'already' in message, got: $($resp.message)" }
Write-Ok "Second approve refused"

# --- 13 -------------------------------------------------------------
Next-Test "Audit records actor_id"
$resp = Invoke-Api -Method "GET" -Url "$BaseUrl/v1/audit" -Headers $AdminHeaders
$approved = $resp.audit_events | Where-Object { $_.event_type -eq "approved" } | Select-Object -First 1
if (-not $approved) { Write-Fail "no 'approved' event in audit" }
$actor = $approved.payload.actor_id
if ($actor -ne "smoke_admin") { Write-Fail "actor_id mismatch: '$actor' (expected 'smoke_admin')" }
Write-Ok "actor_id=smoke_admin found in audit payload"

# --- 14 -------------------------------------------------------------
Next-Test "Reject flow"
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $AgentHeaders `
    -Body '{"tool_name":"send_payment","parameters":{"amount":3000.0,"recipient":"Fournisseur_A"}}'
if ($resp.status -ne "HOLD") { Write-Fail "expected HOLD, got $($resp.status)" }
$ActionId2 = $resp.action_id

$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/holds/$ActionId2/reject" -Headers $AdminHeaders
if ($resp.status -ne "REJECTED") { Write-Fail "expected REJECTED, got $($resp.status)" }
Write-Ok "HOLD rejected"

# --- 15 -------------------------------------------------------------
Next-Test "Executions endpoint requires admin key"
$code = Get-HttpStatus -Method "GET" -Url "$BaseUrl/v1/executions"
if ($code -ne 401) { Write-Fail "expected 401, got $code" }
Write-Ok "401 without key"

# --- 16 -------------------------------------------------------------
Next-Test "Executions endpoint — list"
$resp = Invoke-Api -Method "GET" -Url "$BaseUrl/v1/executions" -Headers $AdminHeaders
$count = @($resp.executions).Count
if ($count -lt 2) { Write-Fail "expected >=2 executions, got $count" }
Write-Ok "$count executions recorded"

# --- 17 -------------------------------------------------------------
Next-Test "Idempotency — same key returns cached result"
$idemKey = [guid]::NewGuid().ToString()
$headersIdem = @{ "X-Agent-Key" = $AgentKey; "Idempotency-Key" = $idemKey }
$idemBody = '{"tool_name":"send_payment","parameters":{"amount":50.0,"recipient":"Fournisseur_A"}}'

$r1 = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $headersIdem -Body $idemBody
$r2 = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $headersIdem -Body $idemBody

if ($r1.execution.idempotency_key -ne $r2.execution.idempotency_key) {
    Write-Fail "idempotency_key differs between calls"
}
if ($r1.execution.result.tx_id -ne $r2.execution.result.tx_id) {
    Write-Fail "results differ despite same idempotency_key"
}
Write-Ok "Both calls returned identical tx_id=$($r1.execution.result.tx_id)"

# --- 18 -------------------------------------------------------------
Next-Test "Impersonation — payload agent_id is ignored"
# Send a request with a forged agent_id in the payload.
# The engine must use the authenticated identity, so the whitelist
# lookup will fail (agent_smoke_01 is the only registered agent and
# it IS authorized — we impersonate a non-existent agent to prove
# the payload field is ignored).
$forgedBody = '{"agent_id":"agent_nonexistent","tool_name":"send_payment","parameters":{"amount":10.0,"recipient":"Fournisseur_A"}}'
$resp = Invoke-Api -Method "POST" -Url "$BaseUrl/v1/execute-tool" -Headers $AgentHeaders -Body $forgedBody
if ($resp.status -ne "APPROVED") {
    Write-Fail "payload agent_id was used (status=$($resp.status)); expected APPROVED via authenticated identity"
}
Write-Ok "Payload agent_id ignored; authenticated identity used"

# =====================================================================
# DONE
# =====================================================================

Write-Host ""
Write-Host "══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ ALL $testNumber CHECKS PASSED" -ForegroundColor Green
Write-Host "══════════════════════════════════════════════════" -ForegroundColor Green

Invoke-Cleanup
Pop-Location
exit 0