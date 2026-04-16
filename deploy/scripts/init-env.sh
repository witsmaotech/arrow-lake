#!/usr/bin/env bash
# Arrow Lake — Environment Initialization
#
# Generates .env from .env.example with smart detection.
# Usage: ./scripts/init-env.sh [--force]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[info]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }

FORCE=false
[[ "${1:-}" == "--force" ]] && FORCE=true

# --- Check .env.example exists ---
if [[ ! -f "$ENV_EXAMPLE" ]]; then
    error ".env.example not found at $ENV_EXAMPLE"
    exit 1
fi

# --- Check if .env already exists ---
if [[ -f "$ENV_FILE" ]] && [[ "$FORCE" == false ]]; then
    echo ""
    warn ".env already exists at $ENV_FILE"
    read -rp "Overwrite? [y/N] " confirm
    if [[ "$confirm" != [yY] ]]; then
        info "Aborted. Use --force to overwrite."
        exit 0
    fi
fi

# --- Copy template ---
cp "$ENV_EXAMPLE" "$ENV_FILE"
ok "Created .env from .env.example"

# --- Generate random passwords for MinIO ---
if command -v openssl &>/dev/null; then
    MINIO_USER="minioadmin"
    MINIO_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)

    sed -i "s|^MINIO_ROOT_USER=.*|MINIO_ROOT_USER=$MINIO_USER|" "$ENV_FILE"
    sed -i "s|^MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=$MINIO_PASS|" "$ENV_FILE"
    sed -i "s|^ARROW_LAKE__STORAGE__S3_ACCESS_KEY=.*|ARROW_LAKE__STORAGE__S3_ACCESS_KEY=$MINIO_USER|" "$ENV_FILE"
    sed -i "s|^ARROW_LAKE__STORAGE__S3_SECRET_KEY=.*|ARROW_LAKE__STORAGE__S3_SECRET_KEY=$MINIO_PASS|" "$ENV_FILE"

    ok "Generated MinIO credentials (user: $MINIO_USER)"
else
    warn "openssl not found — keeping default MinIO credentials"
    warn "Change MINIO_ROOT_PASSWORD before deploying to shared environments"
fi

# --- Generate Grafana password ---
if command -v openssl &>/dev/null; then
    GRAFANA_PASS=$(openssl rand -base64 16 | tr -d '/+=' | head -c 16)
    sed -i "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=$GRAFANA_PASS|" "$ENV_FILE"
    ok "Generated Grafana admin password"
fi

# --- Summary ---
echo ""
info "Environment initialized: $ENV_FILE"
info ""
info "Quick start:"
info "  cd deploy"
info "  make up          # Start API + MinIO"
info "  make dev         # Start development mode (all services)"
info "  make full        # Start full stack (with monitoring)"
info ""
warn "Review .env and update credentials before deploying to shared environments"
