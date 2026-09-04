-- v1.11.5 W3 (scenario runner): 场景实例 + 步运行两表。
-- ScenarioSpec(v1.11.2)原是「规范+审计词表非执行引擎」;W3 补执行面——
-- 实例行是 SoT(status 终态机 running|completed|compensated|timeout|failed|
-- terminated),步行 UNIQUE(instance_id, step_id) 支持断点续跑 upsert。
-- 幂等不新表(复用 idempotency_keys——崩溃窗口 running 步重放兑现
-- already_in_effect)。
CREATE TABLE IF NOT EXISTS scenario_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL,
    scenario_version INTEGER NOT NULL,
    dataset TEXT,
    object_type TEXT,
    object_id TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    current_step TEXT,
    context_json TEXT NOT NULL DEFAULT '{}',
    deadline_at TEXT,
    pending_compensation_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    actor TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scn_inst_scenario
    ON scenario_instances(scenario_id, status);
CREATE INDEX IF NOT EXISTS idx_scn_inst_status
    ON scenario_instances(status);

CREATE TABLE IF NOT EXISTS scenario_step_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    kind TEXT NOT NULL,               -- assess|action
    status TEXT NOT NULL,             -- running|succeeded|failed|skipped|manual_intervention|dead_letter|timeout
    output_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(instance_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_scn_step_instance
    ON scenario_step_runs(instance_id);
