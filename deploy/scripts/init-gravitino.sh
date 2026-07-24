#!/usr/bin/env bash
# Gravitino Metalake + Catalog initialization for Arrow Lake.
# Idempotent: safe to run multiple times.
set -euo pipefail

GRAVITINO_URI="${GRAVITINO_URI:-http://localhost:8090}"
METALAKE="${GRAVITINO_METALAKE:-arrow_lake}"

echo "==> Initializing Gravitino Metalake: ${METALAKE}"

_accept="Accept: application/vnd.gravitino.v1+json"
_ct="Content-Type: application/json"
_base="${GRAVITINO_URI}/api/metalakes"

# --- Metalake ---
if curl -sf "${_base}/${METALAKE}" -H "${_accept}" >/dev/null 2>&1; then
  echo "  Metalake '${METALAKE}' already exists"
else
  echo "  Creating Metalake..."
  resp=$(curl -sf -w "\n%{http_code}" -X POST -H "${_accept}" -H "${_ct}" \
    -d "{\"name\":\"${METALAKE}\",\"comment\":\"Arrow Lake data lake\",\"properties\":{}}" \
    "${_base}" 2>&1) || true
  http_code=$(echo "$resp" | tail -1)
  if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
    echo "  Metalake created"
  else
    echo "  ERROR: Failed to create metalake (HTTP $http_code)"
    echo "$resp" | head -n -1
    exit 1
  fi
fi

echo "  Enabling Metalake..."
curl -sf -X PATCH -H "${_accept}" -H "${_ct}" \
  -d '{"inUse":true}' \
  "${_base}/${METALAKE}" 2>/dev/null || true

_cat_base="${_base}/${METALAKE}/catalogs"

# --- lakehouse-generic (Lance) ---
if curl -sf "${_cat_base}/lance-catalog" -H "${_accept}" >/dev/null 2>&1; then
  echo "  lance-catalog already exists"
else
  echo "  Creating lance-catalog (lakehouse-generic + S3)..."
  curl -sf -X POST -H "${_accept}" -H "${_ct}" \
    -d '{
      "name":"lance-catalog",
      "type":"RELATIONAL",
      "comment":"Lance datasets via lakehouse-generic",
      "provider":"lakehouse-generic",
      "properties":{
        "location":"s3://arrow-lake/",
        "s3.endpoint":"http://minio:9000",
        "s3.access-key-id":"${MINIO_ROOT_USER:-minioadmin}",
        "s3.secret-access-key":"${MINIO_ROOT_PASSWORD:-minioadmin}"
      }
    }' "${_cat_base}" || { echo "  ERROR: Failed to create lance-catalog"; exit 1; }
fi

# --- Fileset (MinIO) — used for dataset metadata registration ---
if curl -sf "${_cat_base}/minio-fileset" -H "${_accept}" >/dev/null 2>&1; then
  echo "  minio-fileset already exists"
else
  echo "  Creating minio-fileset (Fileset Catalog + S3)..."
  curl -sf -X POST -H "${_accept}" -H "${_ct}" \
    -d '{
      "name":"minio-fileset",
      "type":"FILESET",
      "comment":"MinIO dataset metadata",
      "provider":"fileset",
      "properties":{
        "location":"s3a://arrow-lake/",
        "s3-endpoint":"http://minio:9000",
        "s3-access-key-id":"${MINIO_ROOT_USER:-minioadmin}",
        "s3-secret-access-key":"${MINIO_ROOT_PASSWORD:-minioadmin}",
        "s3-path-style-access":"true"
      }
    }' "${_cat_base}" || { echo "  ERROR: Failed to create minio-fileset"; exit 1; }
fi

# --- Model Catalog ---
if curl -sf "${_cat_base}/ml-models" -H "${_accept}" >/dev/null 2>&1; then
  echo "  ml-models already exists"
else
  echo "  Creating ml-models (Model Catalog)..."
  curl -sf -X POST -H "${_accept}" -H "${_ct}" \
    -d '{
      "name":"ml-models",
      "type":"MODEL",
      "comment":"ML model version management",
      "provider":"model",
      "properties":{}
    }' "${_cat_base}" || { echo "  ERROR: Failed to create ml-models"; exit 1; }
fi

echo "==> Gravitino initialization complete"

# ==============================================================================
# Default Schemas — needed before tables can be created
# ==============================================================================

echo "==> Creating default schemas..."

_schema_base="${_cat_base}/lance-catalog/schemas"

if curl -sf "${_schema_base}/arrow_lake" -H "${_accept}" >/dev/null 2>&1; then
  echo "  Schema 'arrow_lake' in lance-catalog already exists"
else
  echo "  Creating schema 'arrow_lake' in lance-catalog..."
  curl -sf -X POST -H "${_accept}" -H "${_ct}" \
    -d '{"name":"arrow_lake","comment":"Arrow Lake default schema"}' \
    "${_schema_base}" || { echo "  WARN: Failed to create arrow_lake schema"; }
fi

_schema_base="${_cat_base}/minio-fileset/schemas"
if curl -sf "${_schema_base}/arrow_lake" -H "${_accept}" >/dev/null 2>&1; then
  echo "  Schema 'arrow_lake' in minio-fileset already exists"
else
  echo "  Creating schema 'arrow_lake' in minio-fileset..."
  curl -sf -X POST -H "${_accept}" -H "${_ct}" \
    -d '{"name":"arrow_lake","comment":"MinIO fileset default schema"}' \
    "${_schema_base}" || { echo "  WARN: Failed to create arrow_lake schema"; }
fi

_schema_base="${_cat_base}/ml-models/schemas"
if curl -sf "${_schema_base}/arrow_lake" -H "${_accept}" >/dev/null 2>&1; then
  echo "  Schema 'arrow_lake' in ml-models already exists"
else
  echo "  Creating schema 'arrow_lake' in ml-models..."
  curl -sf -X POST -H "${_accept}" -H "${_ct}" \
    -d '{"name":"arrow_lake","comment":"ML model default schema"}' \
    "${_schema_base}" || { echo "  WARN: Failed to create arrow_lake schema"; }
fi

echo "==> Schema initialization complete"
