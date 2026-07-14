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

# ── RocksDB write-path tuning + JVM heap ──────────────────────────────
# Mitigate "too busy to write" under sustained bulk load (kg_build): give
# rocksdb larger memtables, more bg compaction/flush threads, and raise the
# level0 stop-writes trigger so compaction has runway to keep up. All knobs
# are env-overridable; defaults target a 6G/4cpu container. NOTE: properties
# values must be bare (no inline '#') — java properties treats '# ...' as part
# of the value, so comments live here in the script, not on the value lines.
HG_CONF="/hugegraph-server/conf/graphs/hugegraph.properties"
apply_rocksdb () {  # <key> <value> — replace existing line, else append
    local key="$1" val="$2"
    [ -z "$val" ] && return
    if [ -f "$HG_CONF" ] && grep -qE "^[#[:space:]]*${key}=" "$HG_CONF"; then
        sed -i "s|^[#[:space:]]*${key}=.*|${key}=${val}|" "$HG_CONF" 2>/dev/null || true
    else
        echo "${key}=${val}" >> "$HG_CONF"
    fi
}
# write_buffer_size = 128MB memtable; max_write_buffer_number=4 memtables before stall
apply_rocksdb "rocksdb.write_buffer_size"              "${HG_ROCKSDB_WRITE_BUFFER_SIZE:-134217728}"
apply_rocksdb "rocksdb.max_write_buffer_number"        "${HG_ROCKSDB_MAX_WRITE_BUFFER_NUMBER:-4}"
# more compaction/flush threads (needs CPU — see compose HUGEGRAPH_CPU_LIMIT)
apply_rocksdb "rocksdb.max_background_compaction"      "${HG_ROCKSDB_MAX_BG_COMPACTION:-4}"
apply_rocksdb "rocksdb.max_background_flushes"         "${HG_ROCKSDB_MAX_BG_FLUSHES:-2}"
# raise level0 stall/stop thresholds so compaction has runway (verify these
# keys exist in HugeGraph 1.7 RocksDBOptions; if unknown, they're ignored)
apply_rocksdb "rocksdb.level0_slowdown_writes_trigger" "${HG_ROCKSDB_LEVEL0_SLOWDOWN:-36}"
apply_rocksdb "rocksdb.level0_stop_writes_trigger"     "${HG_ROCKSDB_LEVEL0_STOP:-72}"
log "rocksdb write-path tuning applied to $HG_CONF"

# ── Clean orphan per-dataset graph backends (ghost graphs) ───────────
# HugeGraph rocksdb single-node: dropping/clearing a dynamic per-dataset
# graph — or a create that 500'd mid-init — can leave a backend dir under
# /var/lib/hugegraph/graphs/ AFTER the in-memory GraphManager (and sometimes
# the conf/graphs/{name}.properties registration) is gone. On the next
# hg-server start the manager doesn't know the graph, but the orphan rocksdb
# dir blocks re-creation (ensure_graph POST → 500 conflict — recurring
# "Failed to create graph" in kg_build). Scan at every startup and remove any
# backend dir whose graph is not registered (no matching .properties).
# Override: SKIP_ORPHAN_GRAPH_CLEANUP=1 disables.
GRAPHS_BACKEND_DIR="${HG_GRAPHS_BACKEND_DIR:-/var/lib/hugegraph/graphs}"
GRAPHS_CONF_DIR="${HG_GRAPHS_CONF_DIR:-/hugegraph-server/conf/graphs}"
if [ -z "${SKIP_ORPHAN_GRAPH_CLEANUP:-}" ] && [ -d "$GRAPHS_BACKEND_DIR" ] && [ -d "$GRAPHS_CONF_DIR" ]; then
    _orphans=0
    for _dir in "$GRAPHS_BACKEND_DIR"/*/; do
        [ -d "$_dir" ] || continue
        _name="$(basename "$_dir")"
        if [ ! -f "$GRAPHS_CONF_DIR/${_name}.properties" ]; then
            log "Removing orphan graph backend '${_name}' (no conf/${_name}.properties) — ghost-graph cleanup"
            rm -rf "${_dir%/}"
            _orphans=$((_orphans + 1))
        fi
    done
    [ "$_orphans" -gt 0 ] && log "Removed ${_orphans} orphan graph backend(s)"
fi

# NOTE: JVM heap is managed in docker-compose (JAVA_OPTS env, set per deployment
# in the hg-server service — e.g. prod_minimal.yml) so it stays in sync with the
# cgroup memory limit. Do NOT export JAVA_OPTS here: ${JAVA_OPTS:-default} would
# pass through the compose value and silently drop the GC flags this script
# intended to add, while a hard override would desync heap from the cgroup.

# ── Delegate to original entrypoint ──────────────────────────────────
# Original image: ENTRYPOINT ["/usr/bin/dumb-init", "--"]
#                 CMD ["./docker-entrypoint.sh"]
# We replaced ENTRYPOINT with this wrapper, so $@ = ./docker-entrypoint.sh
# Re-chain through dumb-init for proper PID 1 signal handling.
log "Starting HugeGraph server..."
exec /usr/bin/dumb-init -- "$@"
