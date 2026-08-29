-- v1.11.3 MS4 (W3.4 / F4.5): annotation recover watermark — 轮询对账的
-- 增量游标(已回收的最大 LS task id)。列加在 annotation_projects 上
-- (项目级游标;轮询 30s 主通道,webhook 只加速,S9)。
ALTER TABLE annotation_projects
    ADD COLUMN recover_watermark INTEGER NOT NULL DEFAULT 0;
