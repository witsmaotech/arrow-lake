#!/usr/bin/env bash
# Generate self-signed TLS certificates for Arrow Lake API server.
# Usage: ./deploy/scripts/gen-certs.sh [output-dir]
#
# Produces:
#   <output-dir>/server.key  — TLS private key
#   <output-dir>/server.crt  — TLS certificate (self-signed)
set -euo pipefail

OUT_DIR="${1:-deploy/certs}"
DAYS="${TLS_CERT_DAYS:-365}"
CN="${TLS_CERT_CN:-arrow-lake.local}"

mkdir -p "$OUT_DIR"

openssl req -x509 -newkey rsa:2048 \
  -keyout "$OUT_DIR/server.key" \
  -out "$OUT_DIR/server.crt" \
  -days "$DAYS" \
  -nodes \
  -subj "/CN=$CN" \
  -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1" \
  2>/dev/null

chmod 600 "$OUT_DIR/server.key"
chmod 644 "$OUT_DIR/server.crt"

echo "TLS certificates generated in $OUT_DIR/"
echo "  server.key — private key (chmod 600)"
echo "  server.crt — certificate  (valid for $DAYS days)"
