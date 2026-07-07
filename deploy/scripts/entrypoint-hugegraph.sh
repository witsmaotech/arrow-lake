#!/bin/bash
# HugeGraph 1.7 all-in-one entrypoint wrapper.
#
# Patches the Gremlin init script to register graph traversal bindings
# before the server starts, then delegates to the original entrypoint.
#
# Problem: The all-in-one image's scripts/empty-sample.groovy does not
# bind `g` (the default TraversalSource).  The `graphs:` section in
# gremlin-server.yaml is present but HugeGraph 1.7 registers graphs
# dynamically AFTER the init script runs, so the `hugegraph` variable
# is not yet available during script evaluation.
#
# Solution: Use HugeFactory.open() in the init script (same pattern as
# HugeGraph's own example.groovy) to open the graph and bind both
# `graph` and `g` to the globals map.
#
# Usage in docker-compose:
#   entrypoint: ["/usr/local/bin/entrypoint-hugegraph.sh"]
#   volumes:
#     - ./scripts/entrypoint-hugegraph.sh:/usr/local/bin/entrypoint-hugegraph.sh:ro
#
set -euo pipefail

log() { echo "[entrypoint-wrapper] $*"; }

# ── Patch Groovy init script with graph bindings ────────────────────
INIT_SCRIPT="/hugegraph-server/scripts/empty-sample.groovy"

if [ -f "$INIT_SCRIPT" ]; then
    if ! grep -q "HugeFactory.open" "$INIT_SCRIPT" 2>/dev/null; then
        log "Patching empty-sample.groovy — adding g/graph bindings via HugeFactory"
        cat >> "$INIT_SCRIPT" <<'GREMLIN'

import org.apache.hugegraph.HugeFactory

// Open the hugegraph instance and bind traversal source + graph to globals.
// NOTE: The `hugegraph` variable from gremlin-server.yaml `graphs:` section
// is NOT available at init-script evaluation time in HugeGraph 1.7, so we
// use HugeFactory.open() directly (same pattern as example.groovy).
graph = HugeFactory.open("conf/graphs/hugegraph.properties")
globals << [graph: graph, g: graph.traversal()]
GREMLIN
        if grep -q "HugeFactory.open" "$INIT_SCRIPT"; then
            log "Graph bindings (g, graph) registered successfully"
        else
            log "WARNING: Failed to patch Groovy init script"
        fi
    else
        log "Groovy init script already has HugeFactory bindings — skipping patch"
    fi
else
    log "WARNING: ${INIT_SCRIPT} not found — skipping Gremlin patch"
fi

# ── Bind graph under its name for {graph_name}.traversal() queries ───
# queries.py and _import_export.py issue gremlin as `hugegraph.traversal()...`
# (HugeGraph's documented convention). gremlin-server.yaml's `graphs:` section
# would normally bind the name as a global, but HugeGraph 1.7 registers graphs
# AFTER the init script runs, so the `hugegraph` global is never bound server-
# side → every such query fails with MissingPropertyException. Alias the
# already-opened `graph` (set by the block above) under its name here.
if [ -f "$INIT_SCRIPT" ] && ! grep -q "arrow-lake-name-bind" "$INIT_SCRIPT" 2>/dev/null; then
    log "Binding graph under name 'hugegraph' for {graph_name}.traversal() queries"
    cat >> "$INIT_SCRIPT" <<'GREMLIN'

// arrow-lake-name-bind: alias the opened graph under its name so that
// hugegraph.traversal() queries resolve (queries.py / _import_export.py).
globals << [hugegraph: graph]
GREMLIN
else
    [ -f "$INIT_SCRIPT" ] && log "Graph name binding already present — skipping"
fi

# ── Wait for store gRPC readiness (defensive) ────────────────────────
# docker-entrypoint.sh → wait-partition.sh (in the image) only polls the
# store REST endpoint (/v1/partitions on :8520), which can report healthy
# before the gRPC data port :8500 is accepting sessions. Starting the
# server in that window triggers a multi-hour "Connection refused
# hg-store:8500" retry storm in the store gRPC client. Gate startup on the
# gRPC port itself. Override via SKIP_STORE_GRPC_WAIT=1 to disable.
STORE_GRPC_HOST="${HG_STORE_GRPC_HOST:-hg-store}"
STORE_GRPC_PORT="${HG_STORE_GRPC_PORT:-8500}"
if [ -z "${SKIP_STORE_GRPC_WAIT:-}" ]; then
    log "Waiting for store gRPC ${STORE_GRPC_HOST}:${STORE_GRPC_PORT} ..."
    _grpc_ready=0
    for _i in $(seq 1 60); do
        if timeout 2 bash -c "echo > /dev/tcp/${STORE_GRPC_HOST}/${STORE_GRPC_PORT}" 2>/dev/null; then
            _grpc_ready=1
            log "Store gRPC ready (after ${_i} attempt(s))"
            break
        fi
        sleep 2
    done
    if [ "$_grpc_ready" != "1" ]; then
        log "WARNING: store gRPC ${STORE_GRPC_HOST}:${STORE_GRPC_PORT} not ready after 120s — proceeding anyway"
    fi
fi

# ── PD mode at the REST layer — only for the hstore/PD backend ────────
# The image's docker-entrypoint.sh wires HG_SERVER_BACKEND → `backend` and
# HG_SERVER_PD_PEERS → `pd.peers` into hugegraph.properties, but does NOT
# touch rest-server.properties `usePD`. Behavior depends on the backend:
#
#  • hstore + PD cluster: `usePD=true` is MANDATORY in HugeGraph 1.7 for
#    graphspaces / PD mode. Without it the REST server runs standalone and
#    per-dataset graphs created via /graphspaces/DEFAULT/graphs/{name} all
#    collapse onto ONE shared store (v1.8.6 isolation silently broken).
#
#  • rocksdb single-node: graphspaces PD mode is NOT used; per-graph
#    isolation comes from rocksdb's per-graph store directories. usePD must
#    be OFF (the REST standalone path is the correct one here).
REST_CONF="/hugegraph-server/conf/rest-server.properties"
if [ -f "$REST_CONF" ]; then
    if [ "${HG_SERVER_BACKEND:-}" = "hstore" ]; then
        if ! grep -qE "^usePD=true" "$REST_CONF"; then
            log "hstore backend → enabling PD mode (usePD=true) in rest-server.properties"
            sed -i "s|^[#[:space:]]*usePD=.*|usePD=true|" "$REST_CONF" 2>/dev/null || true
            grep -qE "^usePD=true" "$REST_CONF" || echo "usePD=true" >> "$REST_CONF"
        fi
    else
        # rocksdb (or any non-PD backend): make sure PD mode is OFF.
        if grep -qE "^usePD=true" "$REST_CONF"; then
            log "${HG_SERVER_BACKEND:-rocksdb} backend → disabling PD mode in rest-server.properties"
            sed -i "s|^usePD=true|# usePD=true|" "$REST_CONF" 2>/dev/null || true
        fi
    fi
fi

# ── Delegate to original entrypoint ──────────────────────────────────
# Original image: ENTRYPOINT ["/usr/bin/dumb-init", "--"]
#                 CMD ["./docker-entrypoint.sh"]
# We replaced ENTRYPOINT with this wrapper, so $@ = ./docker-entrypoint.sh
# Re-chain through dumb-init for proper PID 1 signal handling.
log "Starting HugeGraph server..."
exec /usr/bin/dumb-init -- "$@"
