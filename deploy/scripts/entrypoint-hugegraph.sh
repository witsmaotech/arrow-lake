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

# ── Delegate to original entrypoint ──────────────────────────────────
# Original image: ENTRYPOINT ["/usr/bin/dumb-init", "--"]
#                 CMD ["./docker-entrypoint.sh"]
# We replaced ENTRYPOINT with this wrapper, so $@ = ./docker-entrypoint.sh
# Re-chain through dumb-init for proper PID 1 signal handling.
log "Starting HugeGraph server..."
exec /usr/bin/dumb-init -- "$@"
