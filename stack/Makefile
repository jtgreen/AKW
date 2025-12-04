# --- Paths ---
REPO_ROOT = /opt/knowledge-wiki-remote
STACK_DIR = $(REPO_ROOT)/stack
DATA_DIR  = /opt/bsos-wiki-data
ASK_SERVICE = bsos-ask        # Adjust to your actual service name
CADDYFILE = /etc/caddy/Caddyfile

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
	curl -Ik https://bsos.wiki | head -n 1
	@echo "--- Testing Ask UI ---"
	curl -Ik https://bsos.wiki/ask | head -n 1
	@echo "--- Testing Ask API ---"
	curl -s -X POST https://bsos.wiki/ask/api/ask -H "Content-Type: application/json" \
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