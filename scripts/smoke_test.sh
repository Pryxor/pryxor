#!/usr/bin/env bash
#
# Pryxor — end-to-end smoke test.
#
# Lance une instance isolée sur le port 8001 avec un executor mock,
# exerce le pipeline complet (auth, APPROVED, BLOCKED, HOLD, approve,
# reject, audit), puis nettoie.
#
# Usage :
#   bash scripts/smoke_test.sh
#   KEEP=1 bash scripts/smoke_test.sh   # ne pas supprimer le conteneur
#
# Code retour 0 = tout est vert.

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.smoke.yml"
SERVICE=pryxor
BASE_URL="http://127.0.0.1:8001"
KEEP="${KEEP:-0}"

# --- Couleurs --------------------------------------------------------
if [ -t 1 ]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  CYAN=$'\033[36m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; CYAN=""; BOLD=""; RESET=""
fi

step() { printf "\n${BOLD}${CYAN}▶ %s${RESET}\n" "$1"; }
ok()   { printf "  ${GREEN}✅ %s${RESET}\n" "$1"; }
fail() { printf "  ${RED}❌ %s${RESET}\n" "$1"; exit 1; }
info() { printf "  ${YELLOW}ℹ  %s${RESET}\n" "$1"; }

# --- Helpers ---------------------------------------------------------
json_field() {
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1)"
}

register_admin() {
  $COMPOSE exec -T $SERVICE python admin_cli.py register-admin "$1" 2>/dev/null \
    | json_field "['api_key']"
}

register_agent() {
  $COMPOSE exec -T $SERVICE python admin_cli.py register "$1" 2>/dev/null \
    | json_field "['api_key']"
}

http_status() {
  local method="$1"; shift
  local url="$1"; shift
  curl -s -o /dev/null -w "%{http_code}" -X "$method" "$url" "$@"
}

http_body() {
  local method="$1"; shift
  local url="$1"; shift
  curl -s -X "$method" "$url" "$@"
}

# --- Cleanup ---------------------------------------------------------
cleanup() {
  if [ "$KEEP" = "1" ]; then
    info "KEEP=1 → stack conservée. Arrêt manuel :"
    info "  $COMPOSE down -v"
    return
  fi
  step "Tearing down"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
  ok "Smoke stack removed"
}
trap cleanup EXIT

# =====================================================================
# SETUP
# =====================================================================
step "Starting smoke stack"
$COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
$COMPOSE up -d --build >/dev/null
ok "Container started"

step "Waiting for /v1/health"
for i in $(seq 1 30); do
  if [ "$(http_status GET "$BASE_URL/v1/health" || true)" = "200" ]; then
    ok "Health OK after ${i}s"
    break
  fi
  sleep 1
  if [ "$i" = "30" ]; then fail "Health check timed out"; fi
done

step "Registering admin key"
ADMIN_KEY=$(register_admin smoke_admin)
[ -n "$ADMIN_KEY" ] || fail "Failed to obtain admin key"
ok "Admin key: ${ADMIN_KEY:0:32}..."

step "Registering agent key"
AGENT_KEY=$(register_agent agent_smoke_01)
[ -n "$AGENT_KEY" ] || fail "Failed to obtain agent key"
ok "Agent key: ${AGENT_KEY:0:32}..."

# =====================================================================
# TESTS
# =====================================================================

step "1. Public health"
code=$(http_status GET "$BASE_URL/v1/health")
[ "$code" = "200" ] && ok "200" || fail "expected 200, got $code"

step "2. Admin endpoint without key"
code=$(http_status GET "$BASE_URL/v1/holds")
[ "$code" = "401" ] && ok "401" || fail "expected 401, got $code"

step "3. Admin endpoint with agent key"
code=$(http_status GET "$BASE_URL/v1/holds" -H "X-Admin-Key: $AGENT_KEY")
[ "$code" = "401" ] && ok "401 (agent key rejected)" \
                    || fail "expected 401, got $code"

step "4. Admin endpoint with admin key"
code=$(http_status GET "$BASE_URL/v1/holds" -H "X-Admin-Key: $ADMIN_KEY")
[ "$code" = "200" ] && ok "200 (admin key accepted)" \
                    || fail "expected 200, got $code"

step "5. Agent endpoint without key"
code=$(http_status POST "$BASE_URL/v1/execute-tool" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_payment","parameters":{"amount":100,"recipient":"Fournisseur_A"}}')
[ "$code" = "401" ] && ok "401" || fail "expected 401, got $code"

step "6. Agent endpoint with admin key"
code=$(http_status POST "$BASE_URL/v1/execute-tool" \
  -H "X-Agent-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_payment","parameters":{"amount":100,"recipient":"Fournisseur_A"}}')
[ "$code" = "401" ] && ok "401 (admin key rejected)" \
                    || fail "expected 401, got $code"

step "7. APPROVED flow"
body=$(http_body POST "$BASE_URL/v1/execute-tool" \
  -H "X-Agent-Key: $AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_payment","parameters":{"amount":100.0,"recipient":"Fournisseur_A"}}')
status=$(echo "$body" | json_field "['status']")
exec_ok=$(echo "$body" | json_field "['execution']['success']")
[ "$status" = "APPROVED" ] && [ "$exec_ok" = "True" ] \
  && ok "APPROVED + executed" || fail "$body"

step "8. BLOCKED flow (unsupported tool)"
body=$(http_body POST "$BASE_URL/v1/execute-tool" \
  -H "X-Agent-Key: $AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"delete_everything","parameters":{}}')
status=$(echo "$body" | json_field "['status']")
reason=$(echo "$body" | json_field "['reason']")
[ "$status" = "BLOCKED" ] && [ "$reason" = "UNSUPPORTED_TOOL" ] \
  && ok "BLOCKED ($reason)" || fail "$body"

step "9. HOLD flow (amount over limit)"
body=$(http_body POST "$BASE_URL/v1/execute-tool" \
  -H "X-Agent-Key: $AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_payment","parameters":{"amount":5000.0,"recipient":"Fournisseur_A"}}')
status=$(echo "$body" | json_field "['status']")
ACTION_ID=$(echo "$body" | json_field "['action_id']")
[ "$status" = "HOLD" ] && [ -n "$ACTION_ID" ] \
  && ok "HOLD created: $ACTION_ID" || fail "$body"

step "10. Approve without admin key"
code=$(http_status POST "$BASE_URL/v1/holds/$ACTION_ID/approve")
[ "$code" = "401" ] && ok "401" || fail "expected 401, got $code"

step "11. Approve with admin key"
body=$(http_body POST "$BASE_URL/v1/holds/$ACTION_ID/approve" \
  -H "X-Admin-Key: $ADMIN_KEY")
status=$(echo "$body" | json_field "['status']")
exec_ok=$(echo "$body" | json_field "['execution']['success']")
[ "$status" = "APPROVED" ] && [ "$exec_ok" = "True" ] \
  && ok "HOLD approved + executed" || fail "$body"

step "12. Double approve refused"
body=$(http_body POST "$BASE_URL/v1/holds/$ACTION_ID/approve" \
  -H "X-Admin-Key: $ADMIN_KEY")
msg=$(echo "$body" | json_field "['message']")
echo "$msg" | grep -q "already" \
  && ok "Second approve refused" || fail "expected 'already', got: $body"

step "13. Audit records actor_id"
body=$(http_body GET "$BASE_URL/v1/audit" -H "X-Admin-Key: $ADMIN_KEY")
actor=$(echo "$body" | python3 -c "
import json, sys
events = json.load(sys.stdin)['audit_events']
approved = [e for e in events if e['event_type'] == 'approved']
print(approved[0]['payload'].get('actor_id','') if approved else '')
")
[ "$actor" = "smoke_admin" ] \
  && ok "actor_id=smoke_admin in audit" \
  || fail "actor_id mismatch: '$actor' (expected 'smoke_admin')"

step "14. Reject flow"
body=$(http_body POST "$BASE_URL/v1/execute-tool" \
  -H "X-Agent-Key: $AGENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"send_payment","parameters":{"amount":3000.0,"recipient":"Fournisseur_A"}}')
ACTION_ID=$(echo "$body" | json_field "['action_id']")
body=$(http_body POST "$BASE_URL/v1/holds/$ACTION_ID/reject" \
  -H "X-Admin-Key: $ADMIN_KEY")
status=$(echo "$body" | json_field "['status']")
[ "$status" = "REJECTED" ] && ok "HOLD rejected" || fail "$body"

step "15. Executions endpoint"
code=$(http_status GET "$BASE_URL/v1/executions")
[ "$code" = "401" ] && ok "401 without key" || fail "expected 401, got $code"
body=$(http_body GET "$BASE_URL/v1/executions" -H "X-Admin-Key: $ADMIN_KEY")
count=$(echo "$body" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['executions']))")
info "Total executions: $count"
[ "$count" -ge "2" ] && ok "≥2 executions recorded" \
                     || fail "expected ≥2, got $count"

# =====================================================================
printf "\n${BOLD}${GREEN}══════════════════════════════════════════════════${RESET}\n"
printf "${BOLD}${GREEN}  ✅ ALL 15 CHECKS PASSED${RESET}\n"
printf "${BOLD}${GREEN}══════════════════════════════════════════════════${RESET}\n"