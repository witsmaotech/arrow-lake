// Result table renderer (columns + rows → HTMLElement). Caps rendered rows.
export function renderTable(columns, rows, { max = 10000 } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "tbl-wrap";
  const shown = rows.slice(0, max);
  const head = `<thead><tr>${columns.map(c => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>`;
  const body = `<tbody>${shown.map(r =>
    `<tr>${columns.map(c => `<td>${fmtCell(r[c])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  wrap.innerHTML = `<table class="tbl">${head}${body}</table>`;
  if (rows.length > max) {
    const note = document.createElement("div");
    note.className = "muted";
    note.style.cssText = "padding:8px;font-size:.75rem";
    note.textContent = `仅渲染前 ${max.toLocaleString()} 行(共 ${rows.length.toLocaleString()} 行,可导出 CSV 查看全部)`;
    wrap.appendChild(note);
  }
  return wrap;
}

function fmtCell(v) {
  if (v === null || v === undefined) return '<span class="muted">∅</span>';
  if (typeof v === "object") return `<span class="mono" style="font-size:.72rem">${escapeHtml(JSON.stringify(v))}</span>`;
  if (typeof v === "number" || typeof v === "boolean") return `<span class="num mono">${escapeHtml(String(v))}</span>`;
  return escapeHtml(String(v));
}
function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
