# Lineage Visualization

> Trace any dataset's full upstream/downstream graph, inspect column-level data
> flow, and keep large graphs browsable with node caps.

Lineage events are recorded by the Lance audit trail on every pipeline transition
(ingest, validate, chunk, embed, query, materialize, clean). The `/lineage`
endpoints surface them as a queryable graph; the `lineage.html` console page
renders it.

## 1. Fetch the Graph

```bash
curl "http://127.0.0.1:8000/api/v1/lineage/graph/reports?max_depth=10&max_nodes=500&format=json" \
  -H "X-API-Key: $KEY"
```

- `max_depth` — BFS depth bound (default 10).
- `max_nodes` — cap on nodes returned (default 500, max 2000). When the true graph
  exceeds the cap, `stats.truncated` is `true` and the UI shows a banner.
- `format` — `json` (default), `dot`, or `mermaid`.

```python
from arrow_lake import Lake
lake = Lake.from_yaml("configs/prod.yaml")
graph = lake.lineage_graph("reports", max_depth=10, max_nodes=500)
print(graph.stats)  # {"total_nodes": 42, "total_edges": 51, "max_depth": 3, "truncated": False}
```

## 2. Node Coloring

Nodes are typed for color-coded rendering:

| Type | Color | Meaning |
|---|---|---|
| `target` | green | The queried dataset |
| `source` | blue | Upstream source |
| `derived` | orange | Downstream derived dataset |

## 3. Column-Level Lineage

Click a node in `lineage.html` (or call history directly) to see which source
column flowed into which target column and through what transform:

```bash
curl "http://127.0.0.1:8000/api/v1/lineage/history/reports" -H "X-API-Key: $KEY"
```

Each event carries an optional `column_lineage` list of `{source_column,
target_column, transform_expr}` mappings. When absent, the node has only
dataset-level lineage.

## 4. Render in the Console

Open `http://127.0.0.1:8000/console/lineage.html`, pick a dataset, and the graph
renders with **vis-network**. `max_nodes` is an API truncation parameter (default
500, max 2000), not a threshold for switching render libraries — `lineage.html`
loads only vis-network and does not pull in G6 (G6 is used solely by `kg.html` for
large graph scenarios). When the true node count exceeds the cap,
`stats.truncated=true` and the UI shows a banner prompting you to raise the node
cap or reduce the depth. Edge labels and node titles are HTML-escaped to block XSS
through crafted labels.

## 5. Actor Provenance

Since v1.9.4, write operations such as ingest and delete thread the authenticated
user (`actor`) into the lineage record, replacing the previous `actor="system"`
placeholder. Every event in the `GET /lineage/history/{dataset}` response carries
an `actor` field, useful for auditing "who changed this dataset and when".

## 6. Downstream Impact Analysis

`POST /api/v1/lineage/impact` analyzes which downstream datasets are affected by
changing a given dataset:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/lineage/impact" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"dataset_name": "reports"}'
```

Returns an `impacted_datasets` list with each downstream dataset and its dependency
path, useful for gauging the blast radius before a change.
