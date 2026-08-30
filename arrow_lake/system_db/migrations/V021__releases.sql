-- v1.11.4 MS5 (W3.1 / F5.4): release registry — 发布注册表。
-- 发布 = Lance 版本锁定 + 语义化 tag + CHANGELOG + datasheet 存档;
-- 重复 (dataset, tag) 拒;retire=软状态(历史保留);劣化比较基准 =
-- 最新 active 发布的 total_score。
CREATE TABLE IF NOT EXISTS sys_releases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset           TEXT NOT NULL,
    tag               TEXT NOT NULL,             -- vMAJOR.MINOR.PATCH
    major             INTEGER NOT NULL,
    minor             INTEGER NOT NULL,
    patch             INTEGER NOT NULL,
    lance_version     INTEGER NOT NULL,          -- 发布时刻锁定的 Lance 版本
    changelog         TEXT NOT NULL,
    quality_report_id INTEGER,                   -- 发布依据的评估报告
    total_score       REAL,
    star              INTEGER,
    admission         TEXT,                      -- gold | silver | bronze | none
    datasheet_yaml    TEXT NOT NULL,             -- 规格书生成物存档
    status            TEXT NOT NULL DEFAULT 'active',   -- active | retired
    released_by       TEXT NOT NULL DEFAULT 'system',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_releases_dataset_tag
    ON sys_releases(dataset, tag);
CREATE INDEX IF NOT EXISTS idx_releases_dataset
    ON sys_releases(dataset);
