// 数据准备 · profile 模块:数据集列表 + 质量画像 KPI + 文本列识别
import { request } from "../api.js";

const TEXT_DTYPES = ["string", "utf8", "str", "large_string"];
// 即便 profile 拿不到 dtype,这些列名也视为文本
const TEXT_NAME_HINTS = ["text_content", "text", "title", "chunk", "content", "body", "summary", "question", "answer"];

function isTextCol(col) {
  const dt = String(col.dtype || "").toLowerCase();
  if (TEXT_DTYPES.some((t) => dt.includes(t))) return true;
  const nm = String(col.name || "").toLowerCase();
  return TEXT_NAME_HINTS.some((h) => nm === h || nm.includes(h));
}

export async function loadDatasets() {
  const resp = await request("GET", "/datasets?limit=200");
  return Array.isArray(resp) ? resp : (resp.datasets || []);
}

export async function loadProfile(name) {
  const resp = await request("GET", `/datasets/${encodeURIComponent(name)}/quality/profile`);
  const data = (resp && resp.data) || {};
  const columns = Array.isArray(data.columns) ? data.columns : [];
  const textCols = columns.filter(isTextCol).map((c) => c.name);
  return {
    dataset_name: data.dataset_name || name,
    total_rows: data.total_rows ?? 0,
    total_columns: data.total_columns ?? columns.length,
    quality_score: data.overall_quality_score,
    columns,
    textCols: textCols.length ? textCols : ["text_content"],
  };
}

export function renderKpis(p, into) {
  const score = p.quality_score;
  const scoreTxt = (typeof score === "number") ? (score * 100).toFixed(0) + "%" : "—";
  const scoreCls = (typeof score === "number")
    ? (score >= 0.8 ? "var(--ok)" : score >= 0.5 ? "var(--warn)" : "var(--danger)")
    : "var(--fg-hi)";
  // 平均空值率(取文本列;无则全列)
  const cols = p.columns.length ? p.columns : [];
  const nullCols = cols.filter((c) => typeof c.null_percentage === "number");
  const avgNull = nullCols.length
    ? (nullCols.reduce((s, c) => s + c.null_percentage, 0) / nullCols.length)
    : null;
  into.style.gridTemplateColumns = "repeat(5,1fr)";
  into.innerHTML = `
    ${kpi("总行数", (p.total_rows ?? 0).toLocaleString())}
    ${kpi("质量分", scoreTxt, scoreCls)}
    ${kpi("列数", p.total_columns ?? 0)}
    ${kpi("文本列", p.textCols.length, "var(--teal-bright)")}
    ${kpi("平均空值%", avgNull == null ? "—" : avgNull.toFixed(1) + "%", avgNull != null && avgNull > 20 ? "var(--warn)" : "var(--fg-hi)")}
  `;
}

function kpi(label, val, color) {
  return `<div class="kpi"><div class="label">${label}</div><div class="val" ${color ? `style="color:${color}"` : ""}>${val}</div></div>`;
}

// 给 op 配置用的列下拉 HTML(列名转义防 XSS)
const _esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export function colOptions(textCols, selected) {
  return textCols.map((c) => `<option value="${_esc(c)}" ${c === selected ? "selected" : ""}>${_esc(c)}</option>`).join("");
}
