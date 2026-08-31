-- v1.11.4 四维 review 批 B(C1 治本): annotation_projects 加
-- recovered_counts —— per-task 已回收标注数(JSON {task_id: n})。
-- 复合增量判据:task id 前进(原语义)∪ 已回收 task 的标注数增长
-- (第二标注者/重标注;原纯 id watermark 使其永落增量窗口外)。
-- 升级首跑 counts 为空 → id≤watermark 且有标注的 task 全部重回收一轮
-- (adl_id 内容幂等,无重复;恰好补捞历史丢失的第二标注)。
ALTER TABLE annotation_projects ADD COLUMN recovered_counts TEXT;

-- idempotent guard: ALTER 在重复执行时抛 duplicate column,由 Migrator
-- 的既有序号集机制(skip 已应用版本)保证只跑一次。
