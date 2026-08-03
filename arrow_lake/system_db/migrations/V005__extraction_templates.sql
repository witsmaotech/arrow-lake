-- V005__extraction_templates.sql — v1.10.0: user extraction-template registry
--
-- Metadata index for user-authored knowledge extraction templates (the YAML on
-- the writable volume /data/lake/templates/ is the single source of truth; this
-- table is the searchable index + per-dataset bindings). Idempotent.
-- See docs/v1.10.0-extraction-template-management-plan.md §7.

CREATE TABLE IF NOT EXISTS extraction_templates (
    name          TEXT PRIMARY KEY,           -- matches YAML `name` == filename stem
    source        TEXT NOT NULL DEFAULT 'user', -- always 'user' here (system/project scanned at runtime)
    doc_type      TEXT,                       -- domain this template fits (nullable = general)
    file_path     TEXT NOT NULL,              -- absolute path to the YAML on the writable volume
    description   TEXT,
    owner         TEXT,                       -- creating admin username
    is_default_for TEXT,                      -- doc_type this is the default for (nullable)
    content_hash  TEXT NOT NULL,              -- sha256 of YAML text (change detection / self-heal)
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS dataset_template_bindings (
    dataset_name  TEXT PRIMARY KEY,
    template_name TEXT NOT NULL,
    bound_by      TEXT,
    bound_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_extraction_templates_doc_type
    ON extraction_templates(doc_type);
