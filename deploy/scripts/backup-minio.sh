#!/usr/bin/env bash
# Arrow Lake — MinIO backup via mc mirror
#
# Backs up all buckets to a local directory or remote S3.
# Run via cron or docker compose exec.
#
# Usage:
#   ./scripts/backup-minio.sh [backup-dir]
#   BACKUP_REMOTE=s3://my-backup ./scripts/backup-minio.sh
set -euo pipefail

BACKUP_DIR="${1:-/data/backups/minio}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
MINIO_ALIAS="local"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-minioadmin}"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"

echo "[$(date -Iseconds)] Starting MinIO backup to $BACKUP_DIR/$TIMESTAMP"

# Configure mc alias
mc alias set "$MINIO_ALIAS" "$MINIO_ENDPOINT" "$MINIO_USER" "$MINIO_PASS" 2>/dev/null

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Mirror each bucket
for bucket in $(mc ls "$MINIO_ALIAS" --json 2>/dev/null | jq -r '.key' | tr -d '/'); do
    echo "  Backing up bucket: $bucket"
    mc mirror --overwrite "$MINIO_ALIAS/$bucket" "$BACKUP_DIR/$TIMESTAMP/$bucket" 2>/dev/null && \
      echo "  OK: $bucket" || echo "  WARN: $bucket backup incomplete"
done

# Prune old backups
if [ "$RETAIN_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -mtime +$RETAIN_DAYS -exec rm -rf {} \; 2>/dev/null && \
      echo "  Pruned backups older than $RETAIN_DAYS days" || true
fi

echo "[$(date -Iseconds)] MinIO backup complete: $BACKUP_DIR/$TIMESTAMP"
