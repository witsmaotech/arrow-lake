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
      // 样本标准差(÷(N-1)),对齐 DuckDB SUMMARIZE 的 std 列(默认 stddev_samp);
      // 原 ÷N(总体)与 SUMMARIZE 不一致会误导用户对比。
      out.std = nums.length > 1 ? Math.sqrt(sq / (nums.length - 1)) : 0;
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

  // 字段明细:选一列看分布(数值:分位+直方图;类别:Top 值频次)
  const detailBar = document.createElement("div");
  detailBar.className = "result-tools";
  detailBar.style.cssText = "border:none;margin-top:12px;padding:0 0 8px";
  const dl = document.createElement("label");
  dl.className = "rt-group";
  dl.style.cssText = "font-size:.78rem;color:var(--fg-md)";
  const sel = document.createElement("select");
  sel.className = "select";
  sel.style.cssText = "max-width:300px";
  sel.innerHTML = columns.map((c) => {
    const t = stats.find((s) => s.col === c)?.type || "?";
    return `<option value="${esc(c)}">${esc(c)} · ${t}</option>`;
  }).join("");
  dl.append("字段分布明细 ", sel);
  detailBar.appendChild(dl);
  host.appendChild(detailBar);

  const detailHost = document.createElement("div");
  host.appendChild(detailHost);

  function drawDetail(col) {
    const s = stats.find((x) => x.col === col);
    detailHost.innerHTML = "";
    if (!s) return;
    detailHost.appendChild(s.type === "number" ? numericDetail(rows, col, s) : categoricalDetail(rows, col, s));
  }
  sel.addEventListener("change", () => drawDetail(sel.value));
  const firstPick = stats.find((s) => s.type === "number") || stats[0];
  if (firstPick) { sel.value = firstPick.col; drawDetail(firstPick.col); }

  return host;
}

// ---- 字段分布明细(数值:分位 + 直方图)----
function quantile(sorted, p) {
  if (!sorted.length) return null;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function histogram(nums, bins = 12) {
  if (!nums.length) return [];
  let min = Infinity, max = -Infinity;
  for (const x of nums) { if (x < min) min = x; if (x > max) max = x; }
  if (min === max) return [{ lo: min, hi: max, count: nums.length }];
  const w = (max - min) / bins;
  const buckets = Array.from({ length: bins }, (_, i) => ({ lo: min + i * w, hi: min + (i + 1) * w, count: 0 }));
  for (const x of nums) {
    let i = Math.floor((x - min) / w);
    if (i >= bins) i = bins - 1;
    buckets[i].count++;
  }
  return buckets;
}

function numericDetail(rows, col, s) {
  const host = document.createElement("div");
  const nums = rows.map((r) => r[col]).filter(isNum).sort((a, b) => a - b);
  const qlabels = ["min", "P25", "中位", "P75", "P90", "P95", "P99", "max"];
  const qvals = [0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1].map((p) => quantile(nums, p));

  const kpi = document.createElement("div");
  kpi.className = "kpi-row";
  kpi.style.gridTemplateColumns = "repeat(8,1fr)";
  kpi.style.marginBottom = "10px";
  kpi.innerHTML = qvals.map((v, i) =>
    `<div class="kpi"><div class="label">${qlabels[i]}</div><div class="val" style="font-size:.88rem">${fmtNum(v)}</div></div>`
  ).join("");
  host.appendChild(kpi);

  const title = document.createElement("div");
  title.className = "muted";
  title.style.cssText = "font-size:.72rem;padding:2px 0 6px";
  title.textContent = `分布直方图(12 桶)· ${nums.length.toLocaleString()} 个数值 · distinct ${s.distinct.toLocaleString()} · 空 ${s.nullPct.toFixed(1)}%`;
  host.appendChild(title);

  const bins = histogram(nums, 12);
  const maxC = Math.max(...bins.map((b) => b.count), 1);
  const wrap = document.createElement("div");
  wrap.className = "tbl-wrap";
  wrap.innerHTML = `<table class="tbl"><thead><tr><th>区间</th><th class="num">count</th><th class="num">占比</th><th>分布</th></tr></thead><tbody>${
    bins.map((b) => {
      const pct = nums.length ? (b.count / nums.length) * 100 : 0;
      return `<tr><td class="mono" style="font-size:.7rem">${fmtNum(b.lo)} ~ ${fmtNum(b.hi)}</td>
        <td class="num">${b.count.toLocaleString()}</td><td class="num">${pct.toFixed(1)}%</td>
        <td><div class="bar" style="width:180px"><i style="width:${(b.count / maxC) * 100}%"></i></div></td></tr>`;
    }).join("")
  }</tbody></table>`;
  host.appendChild(wrap);
  return host;
}

// ---- 字段分布明细(类别:Top 值频次)----
function categoricalDetail(rows, col, s) {
  const host = document.createElement("div");
  const counts = new Map();
  let nonNull = 0;
  for (const v of rows.map((r) => r[col])) {
    if (v === null || v === undefined) continue;
    nonNull++;
    const k = typeof v === "object" ? JSON.stringify(v) : String(v);
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, 15);
  const rest = sorted.length - top.length;
  const maxC = top.length ? top[0][1] : 1;

  const title = document.createElement("div");
  title.className = "muted";
  title.style.cssText = "font-size:.72rem;padding:2px 0 6px";
  title.textContent = `Top ${top.length} 值频次 · distinct ${s.distinct.toLocaleString()} · 非空 ${nonNull.toLocaleString()} · 空 ${s.nullPct.toFixed(1)}%`;
  host.appendChild(title);

  const wrap = document.createElement("div");
  wrap.className = "tbl-wrap";
  let body = top.map(([val, c]) => {
    const pct = nonNull ? (c / nonNull) * 100 : 0;
    return `<tr><td class="mono" style="font-size:.74rem;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(val)}</td>
      <td class="num">${c.toLocaleString()}</td><td class="num">${pct.toFixed(2)}%</td>
      <td><div class="bar" style="width:180px"><i style="width:${(c / maxC) * 100}%"></i></div></td></tr>`;
  }).join("");
  if (rest > 0) body += `<tr><td class="muted">… 其他 ${rest} 个值</td><td></td><td></td><td></td></tr>`;
  wrap.innerHTML = `<table class="tbl"><thead><tr><th>值</th><th class="num">count</th><th class="num">占比</th><th>频次</th></tr></thead><tbody>${body}</tbody></table>`;
  host.appendChild(wrap);
  return host;
}
