#!/bin/sh
# Fix HugeGraph 1.7 Gremlin Server graph bindings (manual, for running containers).
#
# The all-in-one image ships with `graphs: {}` in gremlin-server.yaml,
# which means g.V() / graph.V() / hugegraph.traversal() all fail with
# MissingPropertyException.  This script patches the config and reloads.
#
# For new containers, use entrypoint-hugegraph.sh instead which patches
# before the server starts.
#
# Usage (inside container or via docker exec):
#   docker exec arrow-lake-hg-server sh /usr/local/bin/fix-hugegraph-gremlin.sh
#
set -e

CONF="/hugegraph-server/conf/gremlin-server.yaml"
GRAPH_PROPS="conf/hugegraph.properties"

echo "[fix-gremlin] Patching ${CONF} ..."

# Replace empty graphs: {} with the hugegraph binding
sed -i "s|^graphs:\s*{\s*}|graphs: {\n  hugegraph: ${GRAPH_PROPS}|" "$CONF"

# Verify
if grep -q "hugegraph: ${GRAPH_PROPS}" "$CONF"; then
    echo "[fix-gremlin] Graph binding registered"
else
    echo "[fix-gremlin] Failed to patch"
    exit 1
fi

# Restart HugeGraph Server to pick up the new binding
echo "[fix-gremlin] Restarting HugeGraph Server..."
/hugegraph-server/bin/stop-hugegraph.sh 2>/dev/null || true
/hugegraph-server/bin/start-hugegraph.sh -j "${JAVA_OPTS:-}" -t 120

echo "[fix-gremlin] Done — g.V() should now work"
