-- V002__catalog_tasks_dlq_rag.sql — P1: catalog / task history / DLQ / RAG sessions (v1.9.0)
--
-- Replaces volatile control-plane storage:
--   dataset_registry   ← temp DuckDB catalog_tables (catalog/actor.py)  [store standalone; Ray actor refactor deferred]
--   task_history       ← Redis 2h-TTL completed-state (api/tasks.py)     [additive: durable history beyond TTL]
--   ingest_dead_letter ← JSONL data/ingest_dlq.jsonl (ingest/dead_letter.py)
--   rag_sessions/turns/feedback ← in-memory lists (rag/session.py)
--
-- Idempotent (CREATE ... IF NOT EXISTS).

-- ── dataset registry ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dataset_registry (
    name           TEXT PRIMARY KEY,
    schema_json    TEXT NOT NULL,
    location       TEXT NOT NULL,
    lance_version  TEXT,
    status         TEXT NOT NULL DEFAULT 'active',   -- active | archived
    row_count      INTEGER,
    size_bytes     INTEGER,
    tags           TEXT,                              -- JSON array
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dataset_registry_status ON dataset_registry(status);

-- ── task history (durable; Redis keeps real-time state) ───────────────
CREATE TABLE IF NOT EXISTS task_history (
    task_id       TEXT PRIMARY KEY,
    operation     TEXT NOT NULL,
    dataset_name  TEXT,
    status        TEXT NOT NULL,                      -- pending|running|completed|failed
    progress      REAL NOT NULL DEFAULT 0,
    result        TEXT,                               -- JSON
    detail        TEXT,                               -- JSON
    error         TEXT,
    user_id       INTEGER,
    started_at    TEXT,
    completed_at  TEXT,
    duration_ms   INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_task_history_kind    ON task_history(operation, status);
CREATE INDEX IF NOT EXISTS idx_task_history_started ON task_history(started_at DESC);

-- ── ingest dead-letter queue ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_dead_letter (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT NOT NULL,
    dataset         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|retrying|resolved|permanent
    error           TEXT NOT NULL DEFAULT '',
    last_error      TEXT NOT NULL DEFAULT '',
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    metadata        TEXT,                             -- JSON
    first_failed_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_failed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (file_path, dataset)
);
CREATE INDEX IF NOT EXISTS idx_ingest_dlq_status  ON ingest_dead_letter(status);
CREATE INDEX IF NOT EXISTS idx_ingest_dlq_dataset ON ingest_dead_letter(dataset);

-- ── RAG sessions ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_sessions (
    id          TEXT PRIMARY KEY,                     -- session_id (client-supplied)
    user_id     INTEGER,
    title       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rag_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES rag_sessions(id) ON DELETE CASCADE,
    turn_id     INTEGER NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user',         -- user | assistant
    question    TEXT,
    answer      TEXT,
    model       TEXT,
    dataset_name TEXT,
    citations   TEXT,                                 -- JSON array
    latency_ms  INTEGER,
    llm_usage   TEXT,                                 -- JSON
    created_at  REAL NOT NULL                         -- epoch seconds (matches SessionStore semantics)
);
CREATE INDEX IF NOT EXISTS idx_rag_turns_session ON rag_turns(session_id, turn_id);

CREATE TABLE IF NOT EXISTS rag_feedback (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id               TEXT NOT NULL,
    turn_id                  INTEGER NOT NULL,
    user_id                  INTEGER,
    rating                   TEXT,
    flagged_citation_indices TEXT,                    -- JSON array
    comment                  TEXT,
    created_at               REAL NOT NULL            -- epoch seconds
);
CREATE INDEX IF NOT EXISTS idx_rag_feedback_session ON rag_feedback(session_id);
