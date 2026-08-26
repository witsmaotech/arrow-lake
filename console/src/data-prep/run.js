// 数据准备 · run 模块:预览(query/daft;容器表走 query/olap?table=)+ 提交分派 + 异步任务轮询 + 结果归一
import { request } from "../api.js";
import { watchTask } from "../task.js";

// 预览:取目标列前 8 行
export async function runPreview(ds, op, formEl, table) {
  const cols = (op.previewCols(formEl) || []).filter(Boolean);
  if (table) {
    // 容器表:daft 端点不认 ?table=,复用 OLAP 二段名寻址(P0-7 端点)
    const sel = cols.length ? cols.map((c) => `"${c.replace(/"/g, '""')}"`).join(", ") : "*";
    const sql = `SELECT ${sel} FROM "${ds.replace(/"/g, '""')}"."${table.replace(/"/g, '""')}" LIMIT 8`;
    const resp = await request("POST",
      `/datasets/${encodeURIComponent(ds)}/query/olap?table=${encodeURIComponent(table)}`,
      { body: { sql, format: "json", max_rows: 8 } });
    const rows = resp.rows || [];
    const head = cols.length ? cols : (rows.length ? Object.keys(rows[0]) : ["(空)"]);
    return { head, rows, meta: `show(${rows.length}) · query/olap?table=${table}` };
  }
  const body = { limit: 8, format: "json" };
  if (cols.length) body.columns = cols;
  const resp = await request("POST", `/datasets/${encodeURIComponent(ds)}/query/daft`, { body });
  const rows = resp.rows || [];
  const head = cols.length ? cols : (rows.length ? Object.keys(rows[0]) : ["(空)"]);
  return { head, rows, meta: `show(${rows.length}) · query/daft` };
}

// 提交:同步端点直出结果;异步端点(202)→ 轮询任务
export async function runSubmit(ds, op, formEl, table, hooks) {
  const spec = op.buildRequest(formEl, ds, table);
  const resp = await request(spec.method, spec.path, { body: spec.body });
  if (spec.async) {
    const taskId = resp.task_id;
    if (!taskId) throw new Error("后端未返回 task_id");
    hooks.onTask?.(taskId, resp.operation || op.key);
    await pollTask(taskId, op, hooks);
  } else {
    const parsed = op.parseResp ? op.parseResp(resp) : { detail: resp };
    hooks.onResult?.(parsed);
  }
}

function pollTask(taskId, op, hooks, timeoutMs = 10 * 60 * 1000) {
  return new Promise((resolve, reject) => {
    let done = false;
    let timer;
    const unsub = watchTask(taskId, (t) => {
      if (done) return;
      const p = parseFloat(t.progress) || 0;
      hooks.onProgress?.(p, t.status);
      if (/COMPLETED|SUCCESS/i.test(t.status || "")) {
        done = true; clearTimeout(timer); unsub();
        hooks.onProgress?.(1, "完成");
        hooks.onResult?.(normalizeResult(t.result, op));
        resolve();
      } else if (/FAILED|ERROR|CANCELL/i.test(t.status || "")) {
        done = true; clearTimeout(timer); unsub();
        reject(new Error(t.error || t.status || "任务失败"));
      }
    });
    timer = setTimeout(() => {
      if (done) return; done = true; unsub();
      reject(new Error("任务超时(10min 未结束)"));
    }, timeoutMs);
  });
}

// 把后端任务 result(EnrichReport)归一成统一结果结构
function normalizeResult(result, op) {
  if (!result) return { detail: { ok: true } };
  if (result.operation === "llm_label" || result.operation === "extract" || Array.isArray(result.new_columns)) {
    return {
      input_rows: result.input_rows,
      affected: result.succeeded,
      output: (result.new_columns || []).join(", ") || "—",
      detail: { succeeded: result.succeeded, failed: result.failed, sample: (result.sample || []).slice(0, 3) },
    };
  }
  return { detail: result };
}
