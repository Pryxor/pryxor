# Pryxor — developer & operator shortcuts.
# Run `make help` to see available targets.

COMPOSE ?= docker compose
SERVICE := pryxor
ifeq ($(OS),Windows_NT)
    SHELL := C:/Program Files/Git/bin/bash.exe
    .SHELLFLAGS := -c

else
    SHELL := /bin/bash
    .SHELLFLAGS := -c
endif
.DEFAULT_GOAL := help

# --------------------------------------------------------------------
# Help
# --------------------------------------------------------------------
.PHONY: help
help:  # Show this help
	@echo "Usage: make <target>"
	@echo ""
	@echo "Lifecycle:"
	@echo "  init        Create .env and ./configs from templates"
	@echo "  build       Build the Docker image"
	@echo "  up          Start Pryxor in the background"
	@echo "  down        Stop Pryxor (keeps volumes)"
	@echo "  restart     Restart Pryxor (picks up config changes)"
	@echo "  logs        Tail logs"
	@echo "  ps          Show container status"
	@echo "  health      Check /v1/health"
	@echo "  shell       Open a shell inside the container"
	@echo ""
	@echo "Agents & holds:"
	@echo "  register        Register an agent (AGENT=my_agent)"
	@echo "  register-admin  Register an admin (NAME=root)"
	@echo "  agents          List registered agents"
	@echo "  revoke          Revoke an agent key (AGENT=my_agent)"
	@echo "  holds           List pending holds (needs PRYXOR_ADMIN_KEY)"
	@echo "  approve         Approve a hold (ID=hold_xxx, needs PRYXOR_ADMIN_KEY)"
	@echo "  reject          Reject a hold (ID=hold_xxx, needs PRYXOR_ADMIN_KEY)"
	@echo "  audit           Show the audit log (needs PRYXOR_ADMIN_KEY)"
	@echo "  executions      List recent executions (needs PRYXOR_ADMIN_KEY)"
	@echo ""
	@echo "Other:"
	@echo "  test        Run the test suite locally (needs a Python venv)"
	@echo "  backup      Back up the state DB"
	@echo "  restore     Restore a backup (FILE=./backups/xxx.sqlite3.gz)"
	@echo "  clean       Stop and remove volumes (DELETES ALL STATE)"
	@echo "  fclean      clean + remove the built image"

# --------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------
.PHONY: init
init:  # Create .env and ./configs from templates (first-time setup)
	@test -f .env || (cp .env.example .env && chmod 600 .env && echo "[ok] .env created (chmod 600)")
	@test -f configs/pryxor.json || (mkdir -p configs && cp -r configs.example/. configs/ && echo "[ok] ./configs created")
	@grep -q "^PRYXOR_ENCRYPTION_KEY=." .env 2>/dev/null || \
		(printf "PRYXOR_ENCRYPTION_KEY=%s\n" "$$(python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')" >> .env && echo "[ok] PRYXOR_ENCRYPTION_KEY generated")
	@echo "[!] Edit .env and configs/pryxor.json before 'make up'."

.PHONY: build
build:  # Build the Docker image
	$(COMPOSE) build

.PHONY: up
up:  # Start Pryxor in the background
	$(COMPOSE) up -d --build

.PHONY: down
down:  # Stop Pryxor (keeps volumes)
	$(COMPOSE) down

.PHONY: restart
restart:  # Restart Pryxor (picks up config changes)
	$(COMPOSE) restart $(SERVICE)

.PHONY: logs
logs:  # Show the last 200 log lines
	$(COMPOSE) logs --tail=200 $(SERVICE)

.PHONY: logs-f
logs-f:  # Follow the logs (Ctrl+C to stop)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

.PHONY: ps
ps:  # Show container status
	$(COMPOSE) ps

# --------------------------------------------------------------------
# Agent management
# --------------------------------------------------------------------
.PHONY: register
register:  # Register a new agent (usage: make register AGENT=my_agent)
	@test -n "$(AGENT)" || (echo "Usage: make register AGENT=my_agent" && exit 1)
	$(COMPOSE) exec $(SERVICE) python admin_cli.py register $(AGENT) --label "$(AGENT)"

.PHONY: register-admin
register-admin:  # Register a new admin (usage: make register-admin NAME=root)
	@test -n "$(NAME)" || (echo "Usage: make register-admin NAME=root" && exit 1)
	$(COMPOSE) exec $(SERVICE) python admin_cli.py register-admin $(NAME) --label "$(NAME)"

.PHONY: agents
agents:  # List registered agents
	$(COMPOSE) exec $(SERVICE) python admin_cli.py list

.PHONY: revoke
revoke:  # Revoke an agent's key (usage: make revoke AGENT=my_agent)
	@test -n "$(AGENT)" || (echo "Usage: make revoke AGENT=my_agent" && exit 1)
	$(COMPOSE) exec $(SERVICE) python admin_cli.py revoke $(AGENT)

# --------------------------------------------------------------------
# HOLD review
#
# All targets below talk to admin endpoints and require PRYXOR_ADMIN_KEY
# in the CALLER's shell. `docker compose exec` does NOT forward host
# environment variables, so we pass the key explicitly with `-e`.
# --------------------------------------------------------------------
.PHONY: holds
holds:  # List pending HOLDs (needs PRYXOR_ADMIN_KEY)
	@test -n "$$PRYXOR_ADMIN_KEY" || (echo "PRYXOR_ADMIN_KEY is not set in this shell. Export it first (see QUICKSTART step 11)." && exit 1)
	$(COMPOSE) exec -e PRYXOR_ADMIN_KEY=$$PRYXOR_ADMIN_KEY $(SERVICE) python pryxor_cli.py actions list

.PHONY: approve
approve:  # Approve a HOLD (usage: make approve ID=hold_abc123, needs PRYXOR_ADMIN_KEY)
	@test -n "$(ID)" || (echo "Usage: make approve ID=hold_abc123" && exit 1)
	@test -n "$$PRYXOR_ADMIN_KEY" || (echo "PRYXOR_ADMIN_KEY is not set in this shell. Export it first (see QUICKSTART step 11)." && exit 1)
	$(COMPOSE) exec -e PRYXOR_ADMIN_KEY=$$PRYXOR_ADMIN_KEY $(SERVICE) python pryxor_cli.py actions approve $(ID)

.PHONY: reject
reject:  # Reject a HOLD (usage: make reject ID=hold_abc123, needs PRYXOR_ADMIN_KEY)
	@test -n "$(ID)" || (echo "Usage: make reject ID=hold_abc123" && exit 1)
	@test -n "$$PRYXOR_ADMIN_KEY" || (echo "PRYXOR_ADMIN_KEY is not set in this shell. Export it first (see QUICKSTART step 11)." && exit 1)
	$(COMPOSE) exec -e PRYXOR_ADMIN_KEY=$$PRYXOR_ADMIN_KEY $(SERVICE) python pryxor_cli.py actions reject $(ID)

.PHONY: audit
audit:  # Show the audit log (needs PRYXOR_ADMIN_KEY)
	@test -n "$$PRYXOR_ADMIN_KEY" || (echo "PRYXOR_ADMIN_KEY is not set in this shell. Export it first (see QUICKSTART step 11)." && exit 1)
	$(COMPOSE) exec -e PRYXOR_ADMIN_KEY=$$PRYXOR_ADMIN_KEY $(SERVICE) python pryxor_cli.py audit list

.PHONY: executions
executions:  # List recent executions (needs PRYXOR_ADMIN_KEY)
	@test -n "$$PRYXOR_ADMIN_KEY" || (echo "PRYXOR_ADMIN_KEY is not set in this shell. Export it first (see QUICKSTART step 11)." && exit 1)
	$(COMPOSE) exec -e PRYXOR_ADMIN_KEY=$$PRYXOR_ADMIN_KEY $(SERVICE) python pryxor_cli.py executions

# --------------------------------------------------------------------
# Debug
# --------------------------------------------------------------------
.PHONY: shell
shell:  # Open a shell inside the container
	$(COMPOSE) exec $(SERVICE) /bin/sh

.PHONY: health
health:  # Check /v1/health (no curl needed)
	@$(COMPOSE) exec -T $(SERVICE) python -c "import json,urllib.request; \
		d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3)); \
		print(json.dumps(d, indent=2))"

.PHONY: test
test:  # Run the test suite (uses .venv to avoid a mismatched global Python)
	@if [ -x .venv/Scripts/python.exe ]; then \
		.venv/Scripts/python.exe -m pytest tests/ -q; \
	elif [ -x .venv/bin/python ]; then \
		.venv/bin/python -m pytest tests/ -q; \
	else \
		echo "No .venv found — create one first (see CONTRIBUTING.md)."; exit 1; \
	fi

# --------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------
.PHONY: clean
clean:  # Stop and remove volumes (DELETES ALL STATE)
	@printf "[!] This will delete ALL Pryxor state (holds, audit, executions). Continue? [y/N] " && read ans && [ "$$ans" = "y" ]
	$(COMPOSE) down -v

.PHONY: fclean
fclean: clean  # Also remove the built image
	-docker rmi pryxor:latest

# --------------------------------------------------------------------
# BACKUP/RESTORE
# --------------------------------------------------------------------
.PHONY: backup
backup:  # Create a backup of the state DB
	bash scripts/backup.sh

.PHONY: restore
restore:  # Restore from a backup (usage: make restore FILE=./backups/xxx.sqlite3.gz)
	@test -n "$(FILE)" || (echo "Usage: make restore FILE=..." && exit 1)
	bash scripts/restore.sh $(FILE)
