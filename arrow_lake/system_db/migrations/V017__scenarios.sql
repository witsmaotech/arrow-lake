-- v1.11.2 MS3 (W2.2 / S5): scenario version chain — 规范+审计词表(S3,
-- 无运行时),console 展示与审计覆盖度量的数据面。同 hash 跳过,无结构化
-- diff(与 V015/V016 同款缺口登记)。
CREATE TABLE IF NOT EXISTS scenarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT NOT NULL,            -- scenario_id
    version       INTEGER NOT NULL,
    scenario_yaml TEXT NOT NULL,
    source_hash   TEXT NOT NULL,            -- sha1 of yaml; same hash → skip
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(scope, version)
);

CREATE INDEX IF NOT EXISTS idx_scenarios_scope
    ON scenarios(scope);
