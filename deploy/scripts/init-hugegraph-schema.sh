#!/usr/bin/env sh
# Initialize HugeGraph schema for Arrow Lake Knowledge Graph.
# Idempotent: safe to run multiple times (ignores 400 "already exists").
#
# Creates: 9 property keys, 8 vertex labels, 9 edge labels, 8 index labels.
set -eu

HG_HOST="${HUGEGRAPH_HOST:-hg-server}"
HG_PORT="${HUGEGRAPH_PORT:-8080}"
GRAPH="hugegraph"
BASE_URL="http://${HG_HOST}:${HG_PORT}/graphs/${GRAPH}/schema"

echo "==> Initializing HugeGraph schema at ${BASE_URL}"

# Wait for HugeGraph to be ready
for i in $(seq 1 30); do
    if curl -sf "${BASE_URL}" >/dev/null 2>&1; then
        echo "  HugeGraph is ready"
        break
    fi
    echo "  Waiting for HugeGraph... ($i/30)"
    sleep 2
done

_ct="Content-Type: application/json"

_post() {
    curl -sf -X POST -H "${_ct}" -d "$1" "${BASE_URL}/$2" >/dev/null 2>&1 || true
}

# --- Property Keys ---
echo "  Creating property keys (9)..."
_post '{"name":"id","data_type":"TEXT","cardinality":"SINGLE"}'            propertykeys
_post '{"name":"name","data_type":"TEXT","cardinality":"SINGLE"}'          propertykeys
_post '{"name":"type","data_type":"TEXT","cardinality":"SINGLE"}'          propertykeys
_post '{"name":"content","data_type":"TEXT","cardinality":"SINGLE"}'       propertykeys
_post '{"name":"embedding_id","data_type":"TEXT","cardinality":"SINGLE"}'  propertykeys
_post '{"name":"chunk_index","data_type":"INT","cardinality":"SINGLE"}'    propertykeys
_post '{"name":"weight","data_type":"DOUBLE","cardinality":"SINGLE"}'      propertykeys
_post '{"name":"doc_name","data_type":"TEXT","cardinality":"SINGLE"}'      propertykeys
_post '{"name":"date","data_type":"TEXT","cardinality":"SINGLE"}'          propertykeys

# --- Vertex Labels ---
echo "  Creating vertex labels (8)..."
_post '{"name":"document","id_strategy":"PRIMARY_KEY","primary_keys":["id"],"properties":["id","name"],"nullable_keys":[]}'              vertexlabels
_post '{"name":"chunk","id_strategy":"PRIMARY_KEY","primary_keys":["id"],"properties":["id","content","chunk_index"],"nullable_keys":[]}' vertexlabels
_post '{"name":"entity","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name","type"],"nullable_keys":["type"]}'        vertexlabels
_post '{"name":"person","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name"],"nullable_keys":[]}'                     vertexlabels
_post '{"name":"organization","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name"],"nullable_keys":[]}'               vertexlabels
_post '{"name":"location","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name"],"nullable_keys":[]}'                   vertexlabels
_post '{"name":"concept","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name"],"nullable_keys":[]}'                    vertexlabels
_post '{"name":"event","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name","date"],"nullable_keys":["date"]}'          vertexlabels

# --- Edge Labels ---
echo "  Creating edge labels (9)..."
_post '{"name":"contains_chunk","source_label":"document","target_label":"chunk","properties":[]}'               edgelabels
_post '{"name":"references","source_label":"chunk","target_label":"entity","properties":[]}'                     edgelabels
_post '{"name":"next_chunk","source_label":"chunk","target_label":"chunk","properties":[]}'                      edgelabels
_post '{"name":"related_to","source_label":"entity","target_label":"entity","properties":["weight"]}'            edgelabels
_post '{"name":"part_of","source_label":"entity","target_label":"entity","properties":[]}'                       edgelabels
_post '{"name":"belongs_to","source_label":"person","target_label":"organization","properties":[]}'              edgelabels
_post '{"name":"located_in","source_label":"person","target_label":"location","properties":[]}'                  edgelabels
_post '{"name":"participates_in","source_label":"person","target_label":"event","properties":[]}'                edgelabels
_post '{"name":"depicts","source_label":"event","target_label":"entity","properties":[]}'                        edgelabels

# --- Index Labels (skip primary-key fields: document.id, chunk.id) ---
echo "  Creating index labels (6)..."
_post '{"name":"entity_name_idx","base_type":"VERTEX_LABEL","base_value":"entity","index_type":"SECONDARY","fields":["name"]}'       indexlabels
_post '{"name":"person_name_idx","base_type":"VERTEX_LABEL","base_value":"person","index_type":"SECONDARY","fields":["name"]}'       indexlabels
_post '{"name":"org_name_idx","base_type":"VERTEX_LABEL","base_value":"organization","index_type":"SECONDARY","fields":["name"]}'    indexlabels
_post '{"name":"location_name_idx","base_type":"VERTEX_LABEL","base_value":"location","index_type":"SECONDARY","fields":["name"]}'   indexlabels
_post '{"name":"concept_name_idx","base_type":"VERTEX_LABEL","base_value":"concept","index_type":"SECONDARY","fields":["name"]}'     indexlabels
_post '{"name":"event_name_idx","base_type":"VERTEX_LABEL","base_value":"event","index_type":"SECONDARY","fields":["name"]}'         indexlabels

echo "==> HugeGraph schema initialization complete"
