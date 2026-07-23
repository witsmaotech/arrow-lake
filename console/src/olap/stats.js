// Column statistics for OLAP result — pure frontend scan of the result rows.
// 算每列:null%/distinct/min/max/avg/sum/std + 类型推断;渲染 .kpi-row + 每列表 + .bar(null%)。
// 复用 app.css 的 .kpi-row/.kpi/.tbl/.bar。仅扫当前结果集(非全表,标注口径)。

const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const isNum = (v) => typeof v === "number" && !Number.isNaN(v) && Number.isFinite(v);

function inferType(values) {
  let n = 0, num = 0, bool = 0, str = 0, dt = 0;
  for (const v of values) {
    if (v === null || v === undefined) continue;
    n++;
    if (isNum(v)) num++;
    else if (typeof v === "boolean") bool++;
    else if (typeof v === "string") {
      if (/^\d{4}-\d{2}-\d{2}[T ]/.test(v) && !isNaN(Date.parse(v))) dt++;
      else str++;
    }
  }
  if (!n) return "null";
  if (num / n >= 0.6) return "number";
  if (bool / n >= 0.6) return "boolean";
  if (dt / n >= 0.6) return "datetime";
  return "string";
}

function colStats(rows, col) {
  const vals = rows.map((r) => r[col]);
  const total = vals.length;
  const nonNull = vals.filter((v) => v !== null && v !== undefined);
  const nullCount = total - nonNull.length;
  const distinct = new Set(nonNull.map((v) => (isNum(v) ? v : String(v)))).size;
  const type = inferType(vals);
  const out = { col, type, total, nullCount, nullPct: total ? (nullCount / total) * 100 : 0, distinct };
  if (type === "number") {
    const nums = nonNull.filter(isNum);
    if (nums.length) {
      let min = Infinity, max = -Infinity, sum = 0;
      for (const x of nums) { if (x < min) min = x; if (x > max) max = x; sum += x; }
      const avg = sum / nums.length;
      let sq = 0;
      for (const x of nums) sq += (x - avg) ** 2;
      out.min = min; out.max = max; out.sum = sum; out.avg = avg;
      out.std = nums.length > 1 ? Math.sqrt(sq / nums.length) : 0;
    }
  }
  return out;
}

const fmtNum = (v) => {
  if (v === null || v === undefined || !isNum(v)) return "—";
  if (Number.isInteger(v) && Math.abs(v) < 1e15) return v.toLocaleString();
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + "M";
  return Number(v).toFixed(3).replace(/\.?0+$/, "");
};

export function renderStats(columns, rows) {
  const host = document.createElement("div");
  host.className = "stat-panel";
  if (!rows.length || !columns.length) {
    host.innerHTML = `<div class="muted" style="padding:12px;font-size:.8rem">无结果行,无法统计</div>`;
    return host;
  }
  const stats = columns.map((c) => colStats(rows, c));
  const totalRows = rows.length;
  const totalCells = totalRows * columns.length;
  const nullCells = stats.reduce((s, x) => s + x.nullCount, 0);
  const numericCols = stats.filter((s) => s.type === "number").length;
  const overallNullPct = totalCells ? (nullCells / totalCells) * 100 : 0;

  // KPI 概览行
  const kpi = document.createElement("div");
  kpi.className = "kpi-row";
  kpi.style.gridTemplateColumns = "repeat(4,1fr)";
  kpi.style.marginBottom = "10px";
  kpi.innerHTML = [
    ["总行数", totalRows.toLocaleString(), `${columns.length} 列 · ${totalCells.toLocaleString()} 单元`],
    ["数值列", String(numericCols), `${columns.length - numericCols} 非数值`],
    ["空值单元", nullCells.toLocaleString(), `${overallNullPct.toFixed(1)}% 空`],
    ["口径", "结果集", `前 ${Math.min(rows.length, 1000000).toLocaleString()} 行采样`],
  ].map(([label, val, delta]) =>
    `<div class="kpi"><div class="label">${label}</div><div class="val">${esc(val)}</div><div class="delta">${esc(delta)}</div></div>`
  ).join("");
  host.appendChild(kpi);

  // 口径提示
  const note = document.createElement("div");
  note.className = "muted";
  note.style.cssText = "font-size:.7rem;padding:2px 2px 8px";
  note.textContent = "⚠ 统计基于当前返回的结果集(受 LIMIT/max_rows 限制),非全表;SUMMARIZE 概览可看全表质量。";
  host.appendChild(note);

  // 每列明细表
  const wrap = document.createElement("div");
  wrap.className = "tbl-wrap";
  const head = `<thead><tr>
    <th>列</th><th>类型</th><th class="num">空值%</th><th>空值分布</th><th class="num">distinct</th>
    <th class="num">min</th><th class="num">max</th><th class="num">avg</th><th class="num">std</th>
  </tr></thead>`;
  const body = stats.map((s) => {
    const nullBar = `<div class="bar ${s.nullPct > 50 ? "danger" : s.nullPct > 20 ? "amber" : ""}" style="width:120px"><i style="width:${Math.min(s.nullPct, 100)}%"></i></div>`;
    return `<tr>
      <td class="mono">${esc(s.col)}</td>
      <td><span class="tag" style="font-size:.62rem">${esc(s.type)}</span></td>
      <td class="num">${s.nullPct.toFixed(1)}%</td>
      <td>${nullBar}</td>
      <td class="num">${s.distinct.toLocaleString()}</td>
      <td class="num">${fmtNum(s.min)}</td>
      <td class="num">${fmtNum(s.max)}</td>
      <td class="num">${fmtNum(s.avg)}</td>
      <td class="num">${fmtNum(s.std)}</td>
    </tr>`;
  }).join("");
  wrap.innerHTML = `<table class="tbl">${head}<tbody>${body}</tbody></table>`;
  host.appendChild(wrap);
  return host;
}
