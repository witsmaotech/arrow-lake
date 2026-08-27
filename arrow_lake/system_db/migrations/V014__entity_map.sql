-- v1.11.1 MS2 (W2.1 / F2.1): entity_map — source-system id → canonical
-- object id. Explicitly maintained (ADMIN API / bulk upsert); NEVER wired
-- into the ingest hot path (red line). One row per (scope, table, source).
CREATE TABLE IF NOT EXISTS entity_map (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT NOT NULL,            -- dataset (container) name
    table_name    TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT '',
    source_id     TEXT NOT NULL,
    object_id     TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(scope, table_name, source_system, source_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_map_scope
    ON entity_map(scope, table_name);
