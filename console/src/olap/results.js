// Non-streaming result rendering (OlapQueryResponse with format=json)
import { renderTable } from "../ui/table.js";

export function renderResult(host, resp, elapsedMs) {
  host.innerHTML = "";
  if (!resp || !resp.success) {
    host.innerHTML = `<div class="empty error">查询返回失败</div>`;
    return;
  }
  const rows = resp.rows || [];
  const columns = rows.length ? Object.keys(rows[0]) : [];

  const meta = document.createElement("div");
  meta.className = "result-meta";
  meta.innerHTML = `<span class="lamp ok"><i></i></span> <b>${resp.row_count?.toLocaleString()}</b> 行 · <b>${resp.column_count}</b> 列 · <span class="mono">${elapsedMs}ms</span>`;
  const exportBtn = document.createElement("button");
  exportBtn.className = "btn btn-ghost btn-sm";
  exportBtn.innerHTML = "导出 CSV";
  exportBtn.onclick = () => downloadCSV(columns, rows);
  meta.appendChild(exportBtn);
  host.appendChild(meta);

  if (!rows.length) {
    const e = document.createElement("div");
    e.className = "empty"; e.textContent = "无结果行(查询成功)";
    host.appendChild(e);
    return;
  }
  host.appendChild(renderTable(columns, rows));
}

export function renderError(host, message) {
  host.innerHTML = `<div class="empty error">${escapeHtml(String(message))}</div>`;
}

function downloadCSV(cols, rows) {
  const esc = v => {
    v = v === null || v === undefined ? "" : (typeof v === "object" ? JSON.stringify(v) : String(v));
    return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  };
  const csv = [cols.join(","), ...rows.map(r => cols.map(c => esc(r[c])).join(","))].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `olap_result_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
