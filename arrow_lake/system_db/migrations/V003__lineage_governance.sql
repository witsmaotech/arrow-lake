-- V003__lineage_governance.sql — P2: lineage index + governance history (v1.9.0)
--
-- lineage_edges     — adjacency index over Lance _lineage_events (events stay
--                     in Lance; this table only accelerates upstream/downstream
--                     traversal from LIKE + in-memory BFS to indexed range scans).
-- schema_changelog  — dataset schema change history (was a governance blind spot)
-- maintenance_runs  — maintenance task run history (was in-memory _last_report)
-- schedules         — durable schedule definitions (Metaflow-only before)
-- config_changelog  — config change audit (show/export only before)
--
-- Idempotent (CREATE ... IF NOT EXISTS).

-- ── lineage adjacency index ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lineage_edges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT,
    src_dataset    TEXT NOT NULL,
    dst_dataset    TEXT NOT NULL,
    operation      TEXT,
    transform_type TEXT,
    actor          TEXT,
    lance_version  TEXT,
    occurred_at    TEXT NOT NULL,
    UNIQUE (event_id, src_dataset, dst_dataset)
);
CREATE INDEX IF NOT EXISTS idx_lineage_src ON lineage_edges(src_dataset);
CREATE INDEX IF NOT EXISTS idx_lineage_dst ON lineage_edges(dst_dataset);

-- ── schema change history ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_changelog (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_name TEXT NOT NULL,
    change_type  TEXT NOT NULL,        -- add_column | drop_column | rename | type_change
    from_schema  TEXT,
    to_schema    TEXT,
    details      TEXT,                 -- JSON
    actor        TEXT,
    occurred_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_schema_changelog_ds ON schema_changelog(dataset_name);

-- ── maintenance run history ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS maintenance_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type    TEXT NOT NULL,
    status       TEXT,
    report       TEXT,                 -- JSON
    started_at   TEXT,
    completed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_maintenance_type ON maintenance_runs(task_type);

-- ── durable schedule definitions ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS schedules (
    name        TEXT PRIMARY KEY,
    cron_expr   TEXT NOT NULL,
    task_kind   TEXT NOT NULL,
    params      TEXT,                  -- JSON
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── config change audit ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config_changelog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    actor       TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_config_changelog_key ON config_changelog(key);
