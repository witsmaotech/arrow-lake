// Async dataset export: POST /datasets/{name}/export (202) → poll status → download (FileResponse blob).
//
// 分工:查询结果(CSV/JSON/Markdown)在 results.js 里前端 Blob 即时落盘;
//       本模块只管服务端异步导出(Parquet/CSV 整数据集),走 202 任务 + status 轮询 + FileResponse 下载。
// 批4 全局导出统一会复用本模块的 runExport。
import { request, API_BASE, ApiError } from "./api.js";
import { getAccessToken, getRefreshToken, setTokens, getApiKey } from "./auth.js";

const POLL_MS = 1200;
const TIMEOUT_MS = 120_000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function authHeaders() {
  const h = {};
  const tok = getAccessToken();
  if (tok) h["Authorization"] = `Bearer ${tok}`;
  const ak = getApiKey();
  if (ak) h["X-API-Key"] = ak;
  return h;
}

// 下载是 FileResponse(非 JSON),不能用 request();裸 fetch + blob() + <a download>。
// 401 时刷一次 token 重试(与 request() 的 auto-refresh 一致,避免长导出后 token 过期)。
async function refreshOnce() {
  const rt = getRefreshToken();
  if (!rt) throw new ApiError(401, "无 refresh token,请重新登录");
  const r = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { Authorization: `Bearer ${rt}`, "Content-Type": "application/json" },
  });
  if (!r.ok) throw new ApiError(r.status, "token 刷新失败");
  const tok = await r.json();
  setTokens(tok.access_token, tok.refresh_token);
}

async function downloadBlob(dataset, task_id, fallbackName) {
  const url = `${API_BASE}/datasets/${encodeURIComponent(dataset)}/export/${task_id}/download`;
  let r = await fetch(url, { headers: authHeaders() });
  if (r.status === 401) {
    await refreshOnce();
    r = await fetch(url, { headers: authHeaders() });
  }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (_) {}
    throw new ApiError(r.status, `下载失败: ${detail}`);
  }
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  triggerDownload(blob, m ? decodeURIComponent(m[1]) : fallbackName);
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/**
 * Run an async dataset export (Parquet/CSV) and trigger download on completion.
 *
 * @param {string} dataset  Dataset name.
 * @param {object} opts
 * @param {string} [opts.format="parquet"]  "parquet" | "csv".
 * @param {string[]} [opts.columns]  Column subset (None = all).
 * @param {boolean} [opts.overwrite=true]
 * @param {(status: object) => void} [opts.onProgress]  Status poll callback.
 * @returns {Promise<object>} Final status object.
 */
export async function runExport(dataset, { format = "parquet", columns, overwrite = true, onProgress } = {}) {
  const output_path = `console_export_${Date.now()}.${format}`;
  const created = await request("POST", `/datasets/${encodeURIComponent(dataset)}/export`, {
    body: { output_path, format, columns, overwrite },
  });
  const task_id = created.task_id;
  const t0 = Date.now();
  while (Date.now() - t0 < TIMEOUT_MS) {
    await sleep(POLL_MS);
    const st = await request("GET", `/datasets/${encodeURIComponent(dataset)}/export/${task_id}/status`);
    onProgress?.(st);
    if (st.status === "completed") {
      await downloadBlob(dataset, task_id, output_path);
      return st;
    }
    if (st.status === "failed" || st.status === "cancelled") {
      throw new Error(st.error || `导出${st.status}`);
    }
  }
  throw new Error("导出超时(120s)");
}
