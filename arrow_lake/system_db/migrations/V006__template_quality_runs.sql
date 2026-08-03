-- V006__template_quality_runs.sql — v1.10.0 M4: extraction-template quality-validation history
--
-- Persists quality-validation runs so an admin can review past validations per
-- template (generated doc, entity/relation counts, graph snapshot for replay,
-- RAG Q&A pairs). The temp dataset + kg graph are cleaned up after a run, so the
-- snapshot here is the durable record. Idempotent.
-- See docs/v1.10.0-extraction-template-management-plan.md §M4.

CREATE TABLE IF NOT EXISTS template_quality_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name TEXT NOT NULL,                -- the template validated
    scenario_hint TEXT,                         -- optional doc-gen hint
    document      TEXT NOT NULL,                -- the ~2000-字 scenario doc used
    temp_dataset  TEXT,                         -- _quality_<token> (informational; cleaned up after)
    entity_count  INTEGER NOT NULL DEFAULT 0,
    relation_count INTEGER NOT NULL DEFAULT 0,
    graph_snapshot TEXT,                        -- JSON {nodes, edges} for vis replay (null = not captured / too large)
    rag_qa        TEXT,                         -- JSON array of {question, answer, citations}
    note          TEXT,                         -- optional admin note
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tqr_template
    ON template_quality_runs(template_name);
