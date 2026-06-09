#!/bin/bash
# HugeGraph 1.7 all-in-one entrypoint wrapper.
#
# Patches gremlin-server.yaml to register graph bindings before the
# server starts, then delegates to the original entrypoint.
#
# Problem: The all-in-one image ships with `graphs: {}` in
# gremlin-server.yaml, which means g.V() / graph.V() /
# hugegraph.traversal() all fail with MissingPropertyException.
#
# Solution: Inject the hugegraph graph binding before startup so
# the Gremlin script engine registers `g`, `graph`, and
# `{graph_name}` as traversal source variables.
#
# Usage in docker-compose:
#   entrypoint: ["/usr/local/bin/entrypoint-hugegraph.sh"]
#   volumes:
#     - ./scripts/entrypoint-hugegraph.sh:/usr/local/bin/entrypoint-hugegraph.sh:ro
#
set -euo pipefail

log() { echo "[entrypoint-wrapper] $*"; }

# ── Patch gremlin-server.yaml ────────────────────────────────────────
CONF="/hugegraph-server/conf/gremlin-server.yaml"
GRAPH_PROPS="conf/hugegraph.properties"

if [ -f "$CONF" ]; then
    if grep -qE '^graphs:\s*\{\s*\}' "$CONF" 2>/dev/null; then
        log "Patching gremlin-server.yaml — adding hugegraph graph binding"
        sed -i "s|^graphs:\s*{\s*}|graphs: {\n  hugegraph: ${GRAPH_PROPS}|" "$CONF"

        if grep -q "hugegraph: ${GRAPH_PROPS}" "$CONF"; then
            log "Graph binding registered successfully"
        else
            log "WARNING: Failed to patch gremlin-server.yaml"
        fi
    else
        log "gremlin-server.yaml already has graph bindings — skipping patch"
    fi
else
    log "WARNING: ${CONF} not found — skipping Gremlin patch"
fi

# ── Delegate to original entrypoint ──────────────────────────────────
# The original image entrypoint is /hugegraph-server/docker-entrypoint.sh
# which runs init-store, start-hugegraph, then tail -f /dev/null.
log "Starting HugeGraph server..."
exec "/hugegraph-server/docker-entrypoint.sh" "$@"
