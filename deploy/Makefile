# Arrow Lake — Docker Compose Makefile
#
# Usage:
#   make up          # Start core services (API + MinIO)
#   make dev         # Development mode (all services + source mount)
#   make gpu         # GPU mode (Ray with CUDA)
#   make full        # Full stack (all + Prometheus monitoring)
#   make kg          # Knowledge Graph (HugeGraph)
#   make ocr         # OCR processing (TurboOCR, requires GPU)
#   make prod        # Production (all services + Metaflow + monitoring)
#   make prod-minimal # Minimal prod (core+KG+Gravitino, no Ray/monitoring) — dev-server friendly
#   make flow-run    # Run a flow in production container
#   make flow-test   # Run all 4 new flows sequentially
#   make down        # Stop all services, keep data
#   make clean       # Stop all services, remove data volumes

.DEFAULT_GOAL := help
COMPOSE_FILE ?= -f docker-compose.yml
COMPOSE_DEV   ?= -f docker-compose.dev.yml
COMPOSE_GPU    ?= -f docker-compose.gpu.yml
COMPOSE_KG     ?= -f docker-compose.hugegraph.yml
COMPOSE_MON    ?= -f docker-compose.monitoring.yml
COMPOSE_PROD        ?= -f docker-compose.prod.yml
COMPOSE_PROD_MINIMAL ?= -f docker-compose.prod_minimal.yml
PROJECT_NAME  ?= arrow-lake

# Profile flags for each mode
PROFILE_CORE   = --profile core
PROFILE_DEV    = --profile dev
PROFILE_GPU    = --profile gpu
PROFILE_FULL   = --profile dev --profile monitoring
PROFILE_KG     = --profile kg
PROFILE_OCR    = --profile ocr

# Common Compose invocation with all override files (for down/clean/ps/logs)
ALL_FILES = $(COMPOSE_FILE) $(COMPOSE_DEV) $(COMPOSE_GPU) $(COMPOSE_KG) $(COMPOSE_MON) $(COMPOSE_PROD) $(COMPOSE_PROD_MINIMAL)

# All profiles (for teardown commands)
ALL_PROFILES = --profile core --profile compute --profile dev --profile gpu --profile kg --profile monitoring --profile ocr

# ---------------------------------------------------------------------------
# Core commands
# ---------------------------------------------------------------------------

.PHONY: up dev gpu full kg ocr prod prod-minimal prod-minimal-build down clean build ps logs env certs \
        shell-api shell-ray shell-jupyter test-smoke flow-run flow-test scan backup

FLOW ?= ingest
ARGS ?=
PROD_RUNNER ?= api

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

prod:
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) up -d

# Minimal production stack (core + KG + Gravitino; no Ray/monitoring/Hubble).
# Build the shared arrow-lake image (BuildKit shared-tag quirk → build api only).
prod-minimal-build:
	docker compose $(COMPOSE_PROD_MINIMAL) --project-name $(PROJECT_NAME) build api

prod-minimal:
	docker compose $(COMPOSE_PROD_MINIMAL) --project-name $(PROJECT_NAME) up -d

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

logs-metaflow:
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) logs -f metaflow-metadata

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

shell-prod:
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) exec api bash

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test-smoke:
	@echo "=== Container Smoke Tests ==="
	cd .. && .venv/bin/python -m pytest tests/container/ -v --tb=short --timeout=300

# ---------------------------------------------------------------------------
# Metaflow Flow Execution (production)
# ---------------------------------------------------------------------------

flow-run:
	@echo "=== Running $(FLOW) Flow ==="
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) exec $(PROD_RUNNER) \
		/app/.venv/bin/python flows/$(FLOW)_flow.py run $(ARGS)

flow-test:
	@echo "=== Running All 4 Flows (production) ==="
	@echo "--- IngestFlow ---"
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) exec api \
		/app/.venv/bin/python flows/ingest_flow.py run --source-path /data/input --dataset-name test_metaflow
	@echo "--- EmbedFlow ---"
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) exec api \
		/app/.venv/bin/python flows/embed_flow.py run --dataset-name test_metaflow --encoder-type api --shard-size 100
	@echo "--- KGFlow ---"
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) exec api \
		/app/.venv/bin/python flows/kg_flow.py run --dataset-name test_metaflow --chunk-size 20
	@echo "--- BatchRAGFlow ---"
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) exec api \
		/app/.venv/bin/python flows/batch_rag_flow.py run --dataset-name test_metaflow --questions-file /app/examples/questions.json
	@echo "=== All Flows Complete ==="

# ---------------------------------------------------------------------------
# Security & Maintenance
# ---------------------------------------------------------------------------

scan:
	@echo "=== Scanning arrow-lake:latest for vulnerabilities ==="
	@docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
		aquasec/trivy:latest image --severity HIGH,CRITICAL --exit-code 1 arrow-lake:latest || \
		(echo "Vulnerabilities found! Review and fix before deploying." && exit 1)

backup:
	@echo "=== Running MinIO backup ==="
	docker compose $(COMPOSE_PROD) --project-name $(PROJECT_NAME) run --rm minio-backup

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
	@echo "  make prod        Production (all + Metaflow + monitoring)"
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
	@echo "  make logs-metaflow Tail Metaflow metadata logs"
	@echo "  make restart     Restart all services"
	@echo ""
	@echo "  make env         Generate .env from .env.example"
	@echo "  make certs       Generate TLS certificates"
	@echo "  make shell-api   Shell into API container (dev)"
	@echo "  make shell-ray   Shell into Ray Head container (dev)"
	@echo "  make shell-jupyter Shell into Jupyter container (dev)"
	@echo "  make shell-prod  Shell into API container (prod)"
	@echo ""
	@echo "  make test-smoke  Run container integration tests"
	@echo "  make flow-run FLOW=ingest [ARGS='...']  Run a flow (prod)"
	@echo "  make flow-test   Run all 4 flows (prod)"
	@echo "  make scan        Scan images for vulnerabilities (trivy)"
	@echo "  make backup      Run MinIO backup (prod)"
	@echo ""
	@echo "Variables:"
	@echo "  FLOW           Flow name (ingest|embed|kg|batch_rag) default: ingest"
	@echo "  ARGS           Extra arguments passed to the flow"
	@echo "  COMPOSE_PROD   Production compose (default: -f docker-compose.prod.yml)"
	@echo "  PROJECT_NAME   Docker project name (default: arrow-lake)"
