-- V011__container_registry.sql (DR14 W1.2, v1.11.0.1)
-- Registry for multi-table container datasets (dataset = schema container).
--
-- Container IDENTITY lives here and only here (D3): storage directories are
-- never sniffed to decide whether a dataset is a container. The declared
-- table list is the control-plane mirror of the physical tables; callers
-- reconcile it with storage enumeration (list_container_tables) as needed.
--
-- Idempotent: safe to re-run on upgrade.

CREATE TABLE IF NOT EXISTS container_registry (
  dataset     TEXT PRIMARY KEY,
  tables_json TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
