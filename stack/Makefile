# Directory where your docker-compose.yml lives
STACK_DIR := /opt/bsos-wiki-stack
COMPOSE := docker-compose

.PHONY: up down restart logs ps pull stop build

# Start everything (detached mode)
up:
        @cd $(STACK_DIR) && $(COMPOSE) up -d

# Stop and remove containers
down:
        @cd $(STACK_DIR) && $(COMPOSE) down

# Stop, then start again
restart:
        @cd $(STACK_DIR) && $(COMPOSE) down
        @cd $(STACK_DIR) && $(COMPOSE) up -d

# View logs for all services
logs:
        @cd $(STACK_DIR) && $(COMPOSE) logs -f

# Show running services
ps:
        @cd $(STACK_DIR) && $(COMPOSE) ps

# Pull latest images without starting/restarting yet
pull:
        @cd $(STACK_DIR) && $(COMPOSE) pull

# Stop services without removing containers
stop:
        @cd $(STACK_DIR) && $(COMPOSE) stop

# Rebuild containers (rare)
build:
        @cd $(STACK_DIR) && $(COMPOSE) build


