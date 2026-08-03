// Non-streaming result rendering (OlapQueryResponse with format=json).
// v1.9.2 批6:结果消费增强 — 导出下拉(CSV/JSON/MD/Parquet)+ SVG 图表 + 列统计。
import { renderTable } from "../ui/table.js";
import { renderChart, recommendChart } from "./charts.js";
import { renderStats } from "./stats.js";
import { runExport } from "../export.js";
import { toast } from "../ui/toast.js";
import { confirmDialog } from "../ui/modal.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// 当前结果缓存(供 SUMMARIZE/Pivot 等外部动作复用导出/图表)
let _lastResult = null;
export function lastResult() { return _lastResult; }

export function renderResult(host, resp, elapsedMs) {
  host.innerHTML = "";
  if (!resp || !resp.success) {
    host.innerHTML = `<div class="empty error">查询返回失败</div>`;
    return;
  }
  const rows = resp.rows || [];
  const columns = rows.length ? Object.keys(rows[0]) : [];
  _lastResult = { columns, rows, dataset: host.dataset?.ds || null };

  // 1. 元信息行(行/列/耗时)
  const meta = document.createElement("div");
  meta.className = "result-meta";
  meta.innerHTML = `<span class="lamp ok"><i></i></span> <b>${(resp.row_count ?? 0).toLocaleString()}</b> 行 · <b>${resp.column_count ?? columns.length}</b> 列 · <span class="mono">${elapsedMs}ms</span>`;
  host.appendChild(meta);

  if (!rows.length) {
    const e = document.createElement("div");
    e.className = "empty"; e.textContent = "无结果行(查询成功)";
    host.appendChild(e);
    return;
  }

  // 2. 工具条:图表类型 / 统计切换 / 导出下拉
  const tools = document.createElement("div");
  tools.className = "result-tools";
  const dataset = host.dataset?.ds || "";

  // 图表选择器
  const chartLabel = document.createElement("label");
  chartLabel.className = "rt-group";
  chartLabel.innerHTML = `<span class="rt-ico">📊</span>`;
  const chartSel = document.createElement("select");
  chartSel.className = "select rt-sel";
  const rec = recommendChart(columns, rows);
  chartSel.innerHTML = [
    ["none", "图表:关"], ["auto", `图表:自动(${chartName(rec)})`],
    ["bar", "柱状图"], ["pie", "饼图"], ["line", "折线图"],
  ].map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("");
  chartSel.value = "none";
  chartLabel.appendChild(chartSel);

  // 统计切换
  const statBtn = document.createElement("button");
  statBtn.className = "btn btn-ghost btn-sm";
  statBtn.innerHTML = `📐 统计`;

  // 导出下拉
  const expLabel = document.createElement("label");
  expLabel.className = "rt-group";
  expLabel.innerHTML = `<span class="rt-ico">⬇</span>`;
  const expSel = document.createElement("select");
  expSel.className = "select rt-sel";
  expSel.innerHTML = `<option value="">导出…</option>` +
    `<option value="csv">结果 → CSV</option>` +
    `<option value="json">结果 → JSON</option>` +
    `<option value="md">结果 → Markdown</option>` +
    `<option value="parquet" ${dataset ? "" : "disabled"}>数据集 → Parquet(异步)</option>`;
  expLabel.appendChild(expSel);

  tools.append(chartLabel, statBtn, expLabel);
  host.appendChild(tools);

  // 3. 图表宿主 / 统计宿主(table 前挂)
  const chartHost = document.createElement("div");
  chartHost.className = "chart-host";
  host.appendChild(chartHost);

  const statHost = document.createElement("div");
  statHost.className = "stat-host";
  host.appendChild(statHost);

  // 4. 结果表
  host.appendChild(renderTable(columns, rows));

  // ---- 事件绑定 ----
  function drawChart() {
    chartHost.innerHTML = "";
    if (chartSel.value === "none") { chartHost.style.display = "none"; return; }
    const { svg, note } = renderChart(chartSel.value, columns, rows);
    if (!svg) { chartHost.style.display = "block"; chartHost.innerHTML = `<div class="muted" style="padding:10px;font-size:.8rem">${esc(note)}</div>`; return; }
    chartHost.style.display = "block";
    chartHost.innerHTML = svg;
  }
  chartSel.addEventListener("change", drawChart);

  let statOn = false;
  statBtn.addEventListener("click", () => {
    statOn = !statOn;
    statBtn.classList.toggle("btn-primary", statOn);
    statBtn.classList.toggle("btn-ghost", !statOn);
    statHost.innerHTML = "";
    statHost.style.display = statOn ? "block" : "none";
    if (statOn) statHost.appendChild(renderStats(columns, rows));
  });

  expSel.addEventListener("change", async () => {
    const fmt = expSel.value;
    expSel.value = "";
    if (!fmt) return;
    if (fmt === "parquet") {
      if (!dataset) { toast("无数据集上下文,无法导出 Parquet", "warn"); return; }
      await exportDatasetParquet(dataset);
    } else {
      exportResult(fmt, columns, rows);
    }
  });

  chartHost.style.display = "none";
  statHost.style.display = "none";
}

export function renderError(host, message) {
  host.innerHTML = `<div class="empty error">${esc(String(message))}</div>`;
}

const chartName = (t) => ({ bar: "柱", pie: "饼", line: "线", none: "关" }[t] || "");

// ---- 前端 Blob 导出(查询结果)----
function exportResult(fmt, columns, rows) {
  let content, mime, ext;
  if (fmt === "csv") {
    content = toCSV(columns, rows); mime = "text/csv;charset=utf-8"; ext = "csv";
  } else if (fmt === "json") {
    content = JSON.stringify(rows, null, 2); mime = "application/json;charset=utf-8"; ext = "json";
  } else { // md
    content = toMarkdown(columns, rows); mime = "text/markdown;charset=utf-8"; ext = "md";
  }
  const blob = new Blob([fmt === "csv" ? "﻿" + content : content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `olap_result_${Date.now()}.${ext}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast(`已导出 ${rows.length.toLocaleString()} 行 → ${ext.toUpperCase()}`, "ok", 2500);
}

async function exportDatasetParquet(dataset) {
  // 导出整个数据集(不按结果列过滤 —— 结果列可能是聚合别名如 COUNT(*)→c,
  // 数据集上不存在,传 columns 会报 'Field "c" does not exist in schema' 而失败)。
  if (!(await confirmDialog({ title: "导出数据集", message: `导出数据集「${dataset}」为 Parquet?\n(异步任务,导出整个数据集,完成后自动下载)`, confirmText: "导出" }))) return;
  try {
    await runExport(dataset, {
      format: "parquet",
      onProgress: (st) => toast(`导出中… ${Math.round((st.progress || 0) * 100)}%`, "info", 1500),
    });
    toast("Parquet 导出完成,已下载", "ok", 3000);
  } catch (e) {
    toast(`Parquet 导出失败: ${e.message || e}`, "danger", 6000);
  }
}

function toCSV(cols, rows) {
  const e = (v) => {
    v = v === null || v === undefined ? "" : (typeof v === "object" ? JSON.stringify(v) : String(v));
    return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => e(r[c])).join(","))].join("\n");
}

function toMarkdown(cols, rows) {
  const e = (v) => v === null || v === undefined ? "" : String(v).replace(/\|/g, "\\|").replace(/\n/g, " ");
  const lines = [`| ${cols.map(e).join(" | ")} |`, `| ${cols.map(() => "---").join(" | ")} |`];
  for (const r of rows) lines.push(`| ${cols.map((c) => e(r[c])).join(" | ")} |`);
  return lines.join("\n");
}
