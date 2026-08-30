-- v1.11.4 MS5 (W2.2 / F5.3): drift baselines — 漂移基线快照
-- (每列:数值 32 桶直方图 / 类别 top-32 频率+other)。发布时自动快照
-- (source=release,W3 接线)+ 手动重置(source=manual);检测对比最新基线。
CREATE TABLE IF NOT EXISTS sys_drift_baselines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT NOT NULL,
    columns_json  TEXT NOT NULL,                -- {col: snapshot}
    source        TEXT NOT NULL DEFAULT 'manual',  -- manual | release | assess
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_drift_baselines_dataset
    ON sys_drift_baselines(dataset);
