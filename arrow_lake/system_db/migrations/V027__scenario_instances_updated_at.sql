-- v1.11.6.6 H-1(四维 review): scenario_instances 加 updated_at ——
-- 孤儿回收的年龄锚点。原 mark_orphaned_running() 无条件杀全部 running,
-- sibling worker 重启(panic/OOM 常客)即误杀活 runner;改为仅回收
-- updated_at(回退 created_at)老于阈值的实例(runner 心跳每 20s 触写,
-- 对照 tasks.py _ORPHAN_STALE_SECONDS=180 先例)。
ALTER TABLE scenario_instances ADD COLUMN updated_at TEXT;

-- 存量行回填 created_at(升级前无 updated_at,年龄回退本也可兜住,
-- 回填使 SELECT 列稳定)。
UPDATE scenario_instances SET updated_at = created_at WHERE updated_at IS NULL;

-- idempotent guard: ALTER 在重复执行时抛 duplicate column,由 Migrator
-- 的既有序号集机制(skip 已应用版本)保证只跑一次。
