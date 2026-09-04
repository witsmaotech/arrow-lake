-- v1.11.5 W2 (#4): 数据集 PII 分级——登记不校验(沿 quality 节先例:
-- 分级是治理事实,内容核验属后续投放)。四档 public/internal/confidential/
-- restricted;分级-脱敏绑定校验(W2 #5)在 corpus 导出面消费本表。
CREATE TABLE IF NOT EXISTS dataset_classification (
    dataset    TEXT PRIMARY KEY,
    tier       TEXT NOT NULL,               -- public|internal|confidential|restricted
    actor      TEXT NOT NULL DEFAULT '',
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
