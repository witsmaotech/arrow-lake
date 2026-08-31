-- v1.11.4 MS5 (发版前清偿 D 项): decisions history — 研判历史持久化。
-- MS3 assess 是无状态即时求值;RLHF 配对(F5.6③)与飞轮低置信自动检测
-- (F5.8)需要数据面。opt-in 落库(POST /decisions/assess?record_history=true)。
CREATE TABLE IF NOT EXISTS decisions_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT NOT NULL,
    object_type   TEXT NOT NULL,
    object_id     TEXT NOT NULL,
    lifecycle_state TEXT,
    matched_rules INTEGER NOT NULL DEFAULT 0,
    rule_ids_json TEXT NOT NULL DEFAULT '[]',
    conclusions_json TEXT NOT NULL DEFAULT '[]',
    confidence    REAL NOT NULL DEFAULT 1.0,
    actor         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_history_object
    ON decisions_history(dataset, object_id);
CREATE INDEX IF NOT EXISTS idx_decisions_history_confidence
    ON decisions_history(dataset, confidence);
