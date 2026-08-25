-- V012__dataset_contracts.sql (DR13/DR14, v1.11.0.1 W2)
-- Dataset (container) contract version chain. One contract document per
-- dataset; ``tables:`` sections address container tables. Versioning mirrors
-- V010 ontology_versions: same source_hash → no new version; content change
-- → next version + structured diff (columns/enums/ranges/pattern/refs).
-- Idempotent.

CREATE TABLE IF NOT EXISTS dataset_contracts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT NOT NULL,            -- dataset (container) name
    version       INTEGER NOT NULL,
    contract_yaml TEXT NOT NULL,
    source_hash   TEXT NOT NULL,            -- sha1 of contract yaml; same hash → skip
    diff_json     TEXT,                     -- structured diff vs previous version (first: NULL)
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(scope, version)
);

CREATE INDEX IF NOT EXISTS idx_dataset_contracts_scope
    ON dataset_contracts(scope);
