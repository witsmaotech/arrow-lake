#!/bin/bash
# MinIO entrypoint — create arrow-lake bucket on first start.
# This script runs inside the MinIO container via /docker-entrypoint-init.d/.

set -e

mc alias set local "http://localhost:9000" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}"

# Create the default bucket if it doesn't exist
mc mb --ignore-existing local/arrow-lake

# Set public read policy (adjust for production)
mc anonymous set download local/arrow-lake

echo "MinIO init: arrow-lake bucket ready"
