// console/src/task.js — 异步任务轮询器(共享单例),基于 console api.js
// 符合 v1.9.1-frontend-core-impl-plan §5.3;字段对齐真实 /tasks 返回
import { request } from "./api.js";

const INTERVALS = [2000, 2000, 3000, 5000, 8000, 10000]; // 退避(ms)
const streamCbs = new Set();      // 全量任务流订阅(tasks.html 用)
const taskCbs = new Map();        // taskId -> Set<cb(task)> 单任务状态订阅
let polling = false, curIdx = 0, timer = null;

const isTerminal = (s) => /COMPLETED|SUCCESS|FAILED|ERROR|CANCELL/i.test(s || "");

async function tick() {
  try {
    const data = await request("GET", "/tasks");
    const tasks = Array.isArray(data) ? data : (data?.tasks || []);
    streamCbs.forEach((cb) => { try { cb(tasks); } catch (e) { console.error("[task] stream", e); } });
    if (taskCbs.size) {
      const byId = new Map(tasks.map((t) => [t.task_id, t]));
      for (const [tid, cbs] of taskCbs) {
        const t = byId.get(tid);
        if (!t) continue;
        cbs.forEach((cb) => { try { cb(t); } catch (e) { console.error("[task] watch", e); } });
        if (isTerminal(t.status)) taskCbs.delete(tid);
      }
    }
    curIdx = 0;
  } catch {
    curIdx = Math.min(curIdx + 1, INTERVALS.length - 1);
  }
  if (streamCbs.size || taskCbs.size) {
    timer = setTimeout(tick, INTERVALS[Math.min(curIdx, INTERVALS.length - 1)]);
  } else {
    polling = false; timer = null;
  }
}
function ensurePolling() {
  if (polling) return;
  polling = true; curIdx = 0; tick();
}

// 订阅全量任务流(活跃页实时刷新)
export function streamTasks(cb) {
  streamCbs.add(cb); ensurePolling();
  return () => streamCbs.delete(cb);
}
// 订阅单个任务状态(写操作提交后挂订阅,状态变化/终态回调)
export function watchTask(taskId, cb) {
  if (!taskCbs.has(taskId)) taskCbs.set(taskId, new Set());
  taskCbs.get(taskId).add(cb); ensurePolling();
  return () => taskCbs.get(taskId)?.delete(cb);
}
