-- v1.11.1 MS2 (W3.2 / F2.2): semantic alignment configs — per-dataset closed
-- transforms (unit affine + value_map), stored separately from contracts
-- (D/S1: contract = ontology constraint, alignment = transform op; different
-- lifecycles). Version chain mirrors dataset_contracts minus the structured
-- diff (deferred until configs actually churn; design §4.2 gap register).
CREATE TABLE IF NOT EXISTS semantic_alignments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scope          TEXT NOT NULL,            -- dataset (container) name
    version        INTEGER NOT NULL,
    alignment_yaml TEXT NOT NULL,
    source_hash    TEXT NOT NULL,            -- sha1 of yaml; same hash → skip
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(scope, version)
);

CREATE INDEX IF NOT EXISTS idx_semantic_alignments_scope
    ON semantic_alignments(scope);
