-- V028(v1.11.6.6 收敛):M9 查重原子化——running 态部分唯一索引。
-- 背景:instantiate 的 dup 检查原是 list-then-create(TOCTOU),并发重复
-- 实例化可双双通过 → 双活 runner 写竞争。索引在 DB 层封死:同
-- (scenario_id, dataset, object_type, object_id) 至多一条 running。
-- 存量清理:重复 running 组保留最新(其余 failed 可 resume)——先清后建,
-- 否则历史重复会让 CREATE UNIQUE INDEX 失败。
UPDATE scenario_instances
SET status='failed',
    error='duplicate running reaped by V028',
    finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
    updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE status='running'
  AND id NOT IN (
      SELECT MAX(id) FROM scenario_instances
      WHERE status='running'
      GROUP BY scenario_id, dataset, object_type, object_id
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_scn_inst_running_uniq
ON scenario_instances(scenario_id, dataset, object_type, object_id)
WHERE status='running';
