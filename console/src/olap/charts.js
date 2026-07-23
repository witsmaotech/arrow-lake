// Pure-SVG charts for OLAP result visualization. Zero chart vendor.
// Palette mirrors narrative-viz.js C (teal-led, mono labels).
// 每个渲染器接收 (columns, rows) 返回 SVG 字符串;按结果形状自动推荐,可手动覆盖。

const PALETTE = ["#2DD4BF", "#38BDF8", "#A78BFA", "#F472B6", "#A3E635", "#F59E0B", "#22C55E", "#FB7185", "#60A5FA", "#FBBF24"];
const FG_HI = "#EDF1F8", FG_MD = "#A7B2C8", FG_LO = "#8593AC", GRID = "#243149", TRACK = "#182236";
const MONO = "'JetBrains Mono',ui-monospace,monospace";
const MAX_SLICES = 12; // 超 12 类 → 合并「其他」

const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const isNum = (v) => typeof v === "number" && !Number.isNaN(v) && Number.isFinite(v);

// 推断列是否数值列(抽样前 50 行)
function numericCol(rows, col) {
  let n = 0, num = 0;
  for (const r of rows) {
    n++;
    if (isNum(r[col])) num++;
    if (n >= 50) break;
  }
  return n > 0 && num / n >= 0.6;
}

/** 按结果形状推荐图表类型:none/bar/pie/line */
export function recommendChart(columns, rows) {
  if (!rows.length || !columns.length) return "none";
  const nums = columns.filter((c) => numericCol(rows, c));
  const cats = columns.filter((c) => !nums.includes(c));
  if (columns.length === 1) return nums.length ? "bar" : "pie";
  if (cats.length >= 1 && nums.length >= 1) return "bar";
  if (nums.length >= 2) return "line";
  return "pie";
}

/** 入口:type=auto 用推荐,否则按指定;返回 {svg, note} */
export function renderChart(type, columns, rows) {
  if (!rows.length || !columns.length) return { svg: "", note: "无数据可绘图" };
  const t = type === "auto" ? recommendChart(columns, rows) : type;
  if (t === "none") return { svg: "", note: "" };
  try {
    switch (t) {
      case "bar": return barChart(columns, rows);
      case "pie": return pieChart(columns, rows);
      case "line": return lineChart(columns, rows);
      default: return { svg: "", note: "" };
    }
  } catch (e) {
    return { svg: "", note: `绘图失败: ${esc(e.message)}` };
  }
}

// ---- 数据抽取辅助 ----
// 取一个分类列(优先非数值,否则首列)
function pickCategory(columns, rows) {
  for (const c of columns) if (!numericCol(rows, c)) return c;
  return columns[0];
}
function pickNumeric(columns, rows) {
  for (const c of columns) if (numericCol(rows, c)) return c;
  return null;
}

// 按 category 聚合 numeric(sum),返回 [{label, value}],top N + 其他
function aggregateBy(rows, catCol, valCol, max) {
  const m = new Map();
  for (const r of rows) {
    const k = String(r[catCol] ?? "∅");
    const v = isNum(r[valCol]) ? r[valCol] : (valCol ? 0 : 1);
    m.set(k, (m.get(k) || 0) + v);
  }
  let arr = [...m].map(([label, value]) => ({ label, value }));
  arr.sort((a, b) => b.value - a.value);
  if (arr.length > max) {
    const rest = arr.slice(max).reduce((s, x) => s + x.value, 0);
    arr = arr.slice(0, max).concat({ label: "其他", value: rest });
  }
  return arr;
}

// ---- 柱状图 ----
function barChart(columns, rows) {
  const W = 640, H = 300, m = { l: 16, r: 16, t: 18, b: 56 };
  const catCol = pickCategory(columns, rows);
  const valCol = pickNumeric(columns, rows);
  const data = valCol
    ? aggregateBy(rows, catCol, valCol, MAX_SLICES)
    : aggregateBy(rows, catCol, null, MAX_SLICES); // 无数值列 → 计数
  if (!data.length) return { svg: "", note: "无可绘制维度" };
  const cw = W - m.l - m.r, ch = H - m.t - m.b;
  const maxV = Math.max(...data.map((d) => d.value), 1);
  const bw = cw / data.length * 0.62;
  const gap = cw / data.length;
  const fmt = (v) => Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto">`;
  // baseline + gridlines
  for (let i = 0; i <= 4; i++) {
    const y = m.t + ch * (1 - i / 4);
    s += `<line x1="${m.l}" y1="${y}" x2="${m.l + cw}" y2="${y}" stroke="${GRID}" stroke-width="1" stroke-opacity="${i === 0 ? 0.6 : 0.25}"/>`;
    s += `<text x="${m.l + cw + 4}" y="${y + 3}" fill="${FG_LO}" font-family="${MONO}" font-size="9" text-anchor="start">${fmt(maxV * i / 4)}</text>`;
  }
  data.forEach((d, i) => {
    const h = (d.value / maxV) * ch;
    const x = m.l + i * gap + (gap - bw) / 2;
    const y = m.t + ch - h;
    const color = PALETTE[i % PALETTE.length];
    s += `<rect x="${x}" y="${y}" width="${bw}" height="${Math.max(h, 1)}" rx="3" fill="${color}" fill-opacity=".82"><title>${esc(d.label)}: ${fmt(d.value)}</title></rect>`;
    s += `<text x="${x + bw / 2}" y="${m.t + ch + 14}" fill="${FG_MD}" font-family="${MONO}" font-size="9.5" text-anchor="middle">${esc(truncate(d.label, 10))}</text>`;
    s += `<text x="${x + bw / 2}" y="${y - 4}" fill="${FG_HI}" font-family="${MONO}" font-size="9" text-anchor="middle">${fmt(d.value)}</text>`;
  });
  s += `<text x="${m.l}" y="12" fill="${FG_LO}" font-family="${MONO}" font-size="10">${esc(valCol ? `${catCol} × SUM(${valCol})` : `${catCol} · 计数`)} · ${data.length} 项${data[data.length - 1].label === "其他" ? "(截断)" : ""}</text>`;
  s += `</svg>`;
  return { svg: s, note: "" };
}

// ---- 饼图(环形)----
function pieChart(columns, rows) {
  const W = 480, H = 300, cx = 150, cy = 150, R = 110, r = 62;
  const catCol = pickCategory(columns, rows);
  const valCol = pickNumeric(columns, rows);
  const data = valCol
    ? aggregateBy(rows, catCol, valCol, MAX_SLICES)
    : aggregateBy(rows, catCol, null, MAX_SLICES);
  if (!data.length) return { svg: "", note: "无可绘制维度" };
  const total = data.reduce((s, d) => s + Math.abs(d.value), 0) || 1;
  const fmt = (v) => Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto">`;
  let a = -Math.PI / 2;
  data.forEach((d, i) => {
    const frac = Math.abs(d.value) / total;
    const a2 = a + frac * Math.PI * 2;
    const large = frac > 0.5 ? 1 : 0;
    const color = PALETTE[i % PALETTE.length];
    const x1 = cx + R * Math.cos(a), y1 = cy + R * Math.sin(a);
    const x2 = cx + R * Math.cos(a2), y2 = cy + R * Math.sin(a2);
    const xi1 = cx + r * Math.cos(a2), yi1 = cy + r * Math.sin(a2);
    const xi2 = cx + r * Math.cos(a), yi2 = cy + r * Math.sin(a);
    s += `<path d="M${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} L${xi1},${yi1} A${r},${r} 0 ${large} 0 ${xi2},${yi2} Z" fill="${color}" fill-opacity=".85" stroke="${TRACK}" stroke-width="1"><title>${esc(d.label)}: ${fmt(d.value)} (${(frac * 100).toFixed(1)}%)</title></path>`;
    // 图例
    const ly = 24 + i * 20;
    s += `<rect x="300" y="${ly - 9}" width="11" height="11" rx="2" fill="${color}"/>`;
    s += `<text x="316" y="${ly}" fill="${FG_MD}" font-family="${MONO}" font-size="10">${esc(truncate(d.label, 14))}</text>`;
    s += `<text x="455" y="${ly}" fill="${FG_HI}" font-family="${MONO}" font-size="10" text-anchor="end">${(frac * 100).toFixed(1)}%</text>`;
    a = a2;
  });
  s += `<text x="${cx}" y="${cy - 4}" fill="${FG_LO}" font-family="${MONO}" font-size="9" text-anchor="middle">合计</text>`;
  s += `<text x="${cx}" y="${cy + 14}" fill="${FG_HI}" font-family="${MONO}" font-size="15" font-weight="700" text-anchor="middle">${fmt(total)}</text>`;
  s += `</svg>`;
  return { svg: s, note: "" };
}

// ---- 折线图 ----
function lineChart(columns, rows) {
  const W = 640, H = 300, m = { l: 44, r: 16, t: 18, b: 40 };
  const xCol = columns[0];
  const yCol = pickNumeric(columns, rows) || columns[1];
  if (!yCol) return { svg: "", note: "缺数值列" };
  // 按 x 排序(数值 x),否则保持顺序
  let pts = rows.map((r, i) => ({ x: isNum(r[xCol]) ? r[xCol] : i, y: isNum(r[yCol]) ? r[yCol] : 0, raw: r }));
  if (pts.length && isNum(rows[0][xCol])) pts.sort((a, b) => a.x - b.x);
  pts = pts.slice(0, 500);
  const cw = W - m.l - m.r, ch = H - m.t - m.b;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = (maxX - minX) || 1, spanY = (maxY - minY) || 1;
  const px = (x) => m.l + ((x - minX) / spanX) * cw;
  const py = (y) => m.t + ch - ((y - minY) / spanY) * ch;
  const fmt = (v) => Number.isInteger(v) ? v.toLocaleString() : Number(v).toFixed(2);
  let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto">`;
  for (let i = 0; i <= 4; i++) {
    const y = m.t + ch * (1 - i / 4);
    s += `<line x1="${m.l}" y1="${y}" x2="${m.l + cw}" y2="${y}" stroke="${GRID}" stroke-width="1" stroke-opacity="${i === 0 ? 0.6 : 0.25}"/>`;
    s += `<text x="${m.l - 6}" y="${y + 3}" fill="${FG_LO}" font-family="${MONO}" font-size="9" text-anchor="end">${fmt(minY + spanY * i / 4)}</text>`;
  }
  const poly = pts.map((p) => `${px(p.x)},${py(p.y)}`).join(" ");
  s += `<polyline points="${poly}" fill="none" stroke="${PALETTE[0]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  // 面积填充
  s += `<polygon points="${m.l},${m.t + ch} ${poly} ${m.l + cw},${m.t + ch}" fill="${PALETTE[0]}" fill-opacity=".08"/>`;
  pts.forEach((p) => {
    s += `<circle cx="${px(p.x)}" cy="${py(p.y)}" r="2.6" fill="${PALETTE[0]}" stroke="${TRACK}" stroke-width="1"><title>${esc(String(p.x))}: ${fmt(p.y)}</title></circle>`;
  });
  s += `<text x="${m.l}" y="12" fill="${FG_LO}" font-family="${MONO}" font-size="10">${esc(xCol)} → ${esc(yCol)} · ${pts.length} 点</text>`;
  s += `<text x="${m.l}" y="${m.t + ch + 26}" fill="${FG_LO}" font-family="${MONO}" font-size="9">${esc(fmt(minX))}</text>`;
  s += `<text x="${m.l + cw}" y="${m.t + ch + 26}" fill="${FG_LO}" font-family="${MONO}" font-size="9" text-anchor="end">${esc(fmt(maxX))}</text>`;
  s += `</svg>`;
  return { svg: s, note: "" };
}

function truncate(s, n) {
  s = String(s);
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
