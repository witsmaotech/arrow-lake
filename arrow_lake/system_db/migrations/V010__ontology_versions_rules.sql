-- v1.11.0 MS1 (F1.2): ontology formalization — SHACL shape snapshots with
-- version diffs, and the auditable rule registry (judgement knowledge).
-- Rules are registered here but NOT executed until MS3 (decision layer).
-- Idempotent.
CREATE TABLE IF NOT EXISTS ontology_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scope          TEXT NOT NULL,             -- dataset name or '*' (global)
    template_name  TEXT NOT NULL,
    version        INTEGER NOT NULL,
    shapes_turtle  TEXT NOT NULL,             -- SHACL shapes graph (Turtle)
    source_hash    TEXT NOT NULL,             -- sha1 of template content; same hash → no new snapshot
    diff_json      TEXT,                      -- structured diff vs previous version (first version: NULL)
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(scope, template_name, version)
);

CREATE INDEX IF NOT EXISTS idx_ontology_versions_scope
    ON ontology_versions(scope, template_name);

CREATE TABLE IF NOT EXISTS ontology_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         TEXT NOT NULL UNIQUE,     -- business rule id (pilot domain: gas-domain rules)
    scope           TEXT NOT NULL,            -- dataset name or '*'
    condition_expr  TEXT NOT NULL,            -- registered, NOT executed until MS3
    conclusion      TEXT NOT NULL,
    source_ref      TEXT NOT NULL,            -- provenance incl. standard/guobiao version
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft → active → retired (state machine)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_ontology_rules_scope
    ON ontology_rules(scope, status);
