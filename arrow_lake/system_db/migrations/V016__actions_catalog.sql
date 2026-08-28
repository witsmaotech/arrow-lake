-- v1.11.2 MS3 (W2.1 / F3.3): actions catalog version chain + idempotency
-- dedup table (S4/M6). Version chain mirrors dataset_contracts minus the
-- structured diff (S5 gap register, same deferral as V015).
CREATE TABLE IF NOT EXISTS actions_catalog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,            -- action_id (域.对象类.行为)
    version     INTEGER NOT NULL,
    action_yaml TEXT NOT NULL,
    source_hash TEXT NOT NULL,            -- sha1 of yaml; same hash → skip
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(scope, version)
);

CREATE INDEX IF NOT EXISTS idx_actions_catalog_scope
    ON actions_catalog(scope);

-- S4 幂等去重:同 (action_id, key) 只执行一次。并发裁决=UNIQUE 约束
-- (INSERT OR IGNORE 恰一行胜出),归属=owner token 比对;failed 态允许
-- 重认领(上次失败,重放可再执行),completed 态重放 → 200 已生效。
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id  TEXT NOT NULL,
    key        TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'running',  -- running | completed | failed
    owner      TEXT,                             -- 认领 worker token(裁决归属)
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(action_id, key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_action
    ON idempotency_keys(action_id);
