# Optional: load defaults from stack/.env if present.
-include .env

# --- Required knobs (override via env or make vars) ---
WIKI_NAME ?= my-wiki
DOMAIN ?= example.com
WIKI_DATA_DIR ?= /opt/$(WIKI_NAME)-data

# --- Derived paths ---
STACK_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(STACK_DIR)/..)

ASK_SERVICE ?= ask
CADDYFILE ?= /etc/caddy/Caddyfile

# --- Commands ---
DOCKER_COMPOSE = docker-compose -f $(STACK_DIR)/docker-compose.yml

# --- Default ---
default: deploy

# --- Pull latest monorepo ---
pull:
	cd $(REPO_ROOT) && git pull

# --- Stop the whole stack ---
down:
	$(DOCKER_COMPOSE) down

# --- Rebuild and restart everything ---
up:
	$(DOCKER_COMPOSE) up -d --build

# --- Restart only Ask API ---
restart-ask:
	$(DOCKER_COMPOSE) restart $(ASK_SERVICE)

# --- Reload Caddy if the config is present ---
reload-caddy:
	if [ -f $(CADDYFILE) ]; then systemctl reload caddy; fi

# --- Show logs for troubleshooting ---
logs:
	$(DOCKER_COMPOSE) logs -f

# --- Docker service list ---
ps:
	$(DOCKER_COMPOSE) ps

# --- Health checks ---
health:
	@echo "--- Testing Wiki.js ---"
	curl -Ik https://$(DOMAIN) | head -n 1
	@echo "--- Testing Ask UI ---"
	curl -Ik https://$(DOMAIN)/ask | head -n 1
	@echo "--- Testing Ask API ---"
	curl -s -X POST https://$(DOMAIN)/ask/api/ask -H "Content-Type: application/json" \
		-d '{"query": "hello from makefile"}' | head -c 200; echo

# --- Full deployment ---
deploy: pull down up reload-caddy health
	@echo "=== Deployment complete ==="

# --- Clean vector store (optional) ---
reset-vector-store:
	@echo "Run python reset_vector_store.py manually"
	@echo "(We can wire this target once you want full automation.)"

# --- Reorg pipeline hooks (optional) ---
reorg-catalog:
	cd $(REPO_ROOT)/indexer && python organizer/build_catalog.py

reorg-plan:
	cd $(REPO_ROOT)/indexer && python organizer/plan_reorg.py

reorg-apply:
	cd $(REPO_ROOT)/indexer && python organizer/apply_reorg.py
