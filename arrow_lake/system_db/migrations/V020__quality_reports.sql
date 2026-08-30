-- v1.11.4 MS5 (W1.4 / F5.1): five-dimension quality assessment reports —
-- 发布门报告物(评估历史链;最新报告驱动 W3 发布准入)。
-- 命名沿版本计划口径(sys_ 前缀=系统运行性质,与 Lance 系统表语义一致)。
CREATE TABLE IF NOT EXISTS sys_quality_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset         TEXT NOT NULL,
    total_score     REAL,                         -- 重归一加权总分;NULL=无 assessed 维度
    star            INTEGER NOT NULL,             -- 0-5
    admission       TEXT NOT NULL,                -- gold | silver | bronze | none
    verdict         TEXT NOT NULL,                -- pass | degraded | veto
    dimensions_json TEXT NOT NULL,                -- {dim: {score, details, source}}
    vetoes_json     TEXT NOT NULL,                -- 触发的一票否决项
    degraded_json   TEXT NOT NULL,                -- 未评估维度名列表
    spec_json       TEXT NOT NULL,                -- 生效配置快照(权重/阈值/准入)
    assessed_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    assessed_by     TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_quality_reports_dataset
    ON sys_quality_reports(dataset);
