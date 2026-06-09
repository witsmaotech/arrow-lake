#!/bin/sh
# Fix HugeGraph 1.7 Gremlin graph bindings (manual, for running containers).
#
# The all-in-one image's empty-sample.groovy does not create a `g`
# TraversalSource binding.  This script patches the Groovy init script
# and restarts HugeGraph to pick up the changes.
#
# For new containers, use entrypoint-hugegraph.sh instead which patches
# before the server starts.
#
# Usage:
#   docker exec arrow-lake-hg-server sh /usr/local/bin/fix-hugegraph-gremlin.sh
#
set -e

INIT_SCRIPT="/hugegraph-server/scripts/empty-sample.groovy"

echo "[fix-gremlin] Patching ${INIT_SCRIPT} ..."

if grep -q "HugeFactory.open" "$INIT_SCRIPT" 2>/dev/null; then
    echo "[fix-gremlin] Init script already has HugeFactory bindings — nothing to do"
    exit 0
fi

cat >> "$INIT_SCRIPT" <<'GREMLIN'

import org.apache.hugegraph.HugeFactory

// Open the hugegraph instance and bind traversal source + graph to globals.
graph = HugeFactory.open("conf/graphs/hugegraph.properties")
globals << [graph: graph, g: graph.traversal()]
GREMLIN

if grep -q "HugeFactory.open" "$INIT_SCRIPT"; then
    echo "[fix-gremlin] Bindings added successfully"
else
    echo "[fix-gremlin] Failed to patch"
    exit 1
fi

echo "[fix-gremlin] Restarting HugeGraph Server..."
/hugegraph-server/bin/stop-hugegraph.sh 2>/dev/null || true
sleep 3
/hugegraph-server/bin/start-hugegraph.sh -j "${JAVA_OPTS:-}" -t 120

echo "[fix-gremlin] Done — g.V() should now work"
