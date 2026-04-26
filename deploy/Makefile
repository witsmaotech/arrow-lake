# Arrow Lake — Docker Compose Makefile
#
# Usage:
#   make up          # Start core services (API + MinIO)
#   make dev         # Development mode (all services + source mount)
#   make gpu         # GPU mode (Ray with CUDA)
#   make full        # Full stack (all + Prometheus monitoring)
#   make kg          # Knowledge Graph (HugeGraph)
#   make ocr         # OCR processing (TurboOCR, requires GPU)
#   make down        # Stop all services, keep data
#   make clean       # Stop all services, remove data volumes

.DEFAULT_GOAL := help
COMPOSE_FILE ?= -f docker-compose.yml
COMPOSE_DEV   ?= -f docker-compose.dev.yml
COMPOSE_GPU    ?= -f docker-compose.gpu.yml
COMPOSE_KG     ?= -f docker-compose.hugegraph.yml
COMPOSE_MON    ?= -f docker-compose.monitoring.yml
PROJECT_NAME  ?= arrow-lake

# Profile flags for each mode
PROFILE_CORE   = --profile core
PROFILE_DEV    = --profile dev
PROFILE_GPU    = --profile gpu
PROFILE_FULL   = --profile dev --profile monitoring
PROFILE_KG     = --profile kg
PROFILE_OCR    = --profile ocr

# Common Compose invocation with all override files (for down/clean/ps/logs)
ALL_FILES = $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_GPU) $(COMPOSE_KG) $(COMPOSE_MON)

# All profiles (for teardown commands)
ALL_PROFILES = --profile core --profile compute --profile dev --profile gpu --profile kg --profile monitoring --profile ocr

# ---------------------------------------------------------------------------
# Core commands
# ---------------------------------------------------------------------------

.PHONY: up dev gpu full kg ocr down clean build ps logs env certs shell-api shell-ray shell-jupyter

up:
	docker compose $(COMPOSE_FILE) --project-name $(PROJECT_NAME) $(PROFILE_CORE) up -d

dev:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) --project-name $(PROJECT_NAME) $(PROFILE_DEV) up -d

gpu:
	docker compose $(COMPOSE_FILE) $(COMPOSE_GPU) --project-name $(PROJECT_NAME) $(PROFILE_GPU) up -d

full:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_MON) --project-name $(PROJECT_NAME) $(PROFILE_FULL) up -d

kg:
	docker compose $(COMPOSE_FILE) $(COMPOSE_KG) --project-name $(PROJECT_NAME) $(PROFILE_KG) up -d

ocr:
	docker compose $(COMPOSE_FILE) --project-name $(PROJECT_NAME) $(PROFILE_OCR) up -d

down:
	docker compose $(ALL_FILES) --project-name $(PROJECT_NAME) $(ALL_PROFILES) down

clean:
	docker compose $(ALL_FILES) --project-name $(PROJECT_NAME) $(ALL_PROFILES) down -v

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

.PHONY: build build-api build-gpu

build:
	docker compose $(COMPOSE_FILE) --project-name $(PROJECT_NAME) $(PROFILE_CORE) build
	docker compose $(COMPOSE_FILE) $(COMPOSE_GPU) --project-name $(PROJECT_NAME) $(PROFILE_GPU) build

build-api:
	docker compose $(COMPOSE_FILE) --project-name $(PROJECT_NAME) $(PROFILE_CORE) build api

build-gpu:
	docker compose $(COMPOSE_FILE) $(COMPOSE_GPU) --project-name $(PROJECT_NAME) $(PROFILE_GPU) build ray-head ray-worker

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

ps:
	docker compose $(ALL_FILES) --project-name $(PROJECT_NAME) $(ALL_PROFILES) ps

logs:
	docker compose $(ALL_FILES) --project-name $(PROJECT_NAME) $(ALL_PROFILES) logs -f --tail=50

logs-api:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) --project-name $(PROJECT_NAME) $(PROFILE_DEV) logs -f api

logs-ray:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_GPU) --project-name $(PROJECT_NAME) $(PROFILE_DEV) logs -f ray-head

logs-jupyter:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_MON) --project-name $(PROJECT_NAME) $(PROFILE_DEV) logs -f jupyter

restart:
	docker compose $(ALL_FILES) --project-name $(PROJECT_NAME) $(ALL_PROFILES) restart

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

env:
	@./scripts/init-env.sh

certs:
	@./scripts/gen-certs.sh

shell-api:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) --project-name $(PROJECT_NAME) $(PROFILE_DEV) exec api bash

shell-ray:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_GPU) --project-name $(PROJECT_NAME) $(PROFILE_DEV) exec ray-head bash

shell-jupyter:
	docker compose $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_MON) --project-name $(PROJECT_NAME) $(PROFILE_DEV) exec jupyter bash

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

help:
	@echo "Arrow Lake — Docker Compose Helper"
	@echo ""
	@echo "Usage:"
	@echo "  make up          Start core services (API + MinIO)"
	@echo "  make dev         Development mode (all services + source mount)"
	@echo "  make gpu         GPU mode (Ray with CUDA)"
	@echo "  make full        Full stack (all + Prometheus monitoring)"
	@echo "  make kg          Knowledge Graph (HugeGraph)"
	@echo "  make ocr         OCR processing (TurboOCR, requires GPU)"
	@echo "  make down        Stop all services, keep data"
	@echo "  make clean       Stop all services, remove data volumes"
	@echo ""
	@echo "  make build       Build all images"
	@echo "  make build-api   Build only API image"
	@echo "  make build-gpu   Build GPU images"
	@echo ""
	@echo "  make ps          Show service status"
	@echo "  make logs        Tail logs (all services)"
	@echo "  make logs-api    Tail API logs"
	@echo "  make logs-ray    Tail Ray logs"
	@echo "  make logs-jupyter Tail Jupyter logs"
	@echo "  make restart     Restart all services"
	@echo ""
	@echo "  make env         Generate .env from .env.example"
	@echo "  make certs       Generate TLS certificates"
	@echo "  make shell-api   Shell into API container"
	@echo "  make shell-ray   Shell into Ray Head container"
	@echo "  make shell-jupyter Shell into Jupyter container"
	@echo ""
	@echo "Variables:"
	@echo "  COMPOSE_FILE   Base compose file (default: -f docker-compose.yml)"
	@echo "  COMPOSE_DEV    Dev overlay (default: -f docker-compose.dev.yml)"
	@echo "  COMPOSE_GPU    GPU overlay (default: -f docker-compose.gpu.yml)"
	@echo "  COMPOSE_MON    Monitoring overlay (default: -f docker-compose.monitoring.yml)"
	@echo "  PROJECT_NAME   Docker project name (default: arrow-lake)"
