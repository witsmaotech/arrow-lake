/* ============================================================
   Arrow Lake Console — 精简 layout shell + icon + API drawer
   从 docs/frontend-prototype/assets/layout.js 接真(去 mock,加登出)。
   挂 window: icon / renderShell / openApi / applyIcons / AL
   ============================================================ */
(function () {
"use strict";

/* favicon: 用空 data URI 消除浏览器 /favicon.ico 404 噪声(showcase/narrative 不引本脚本,接受其展示页噪声) */
if (document.head && !document.querySelector('link[rel="icon"]')) {
  const fav = document.createElement('link');
  fav.rel = 'icon'; fav.href = 'data:,';
  document.head.appendChild(fav);
}

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* —— Icon set (stroke 1.6) —— */
const P = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  sparkles: '<path d="M12 2l2.4 7.4L22 12l-7.6 2.6L12 22l-2.4-7.4L2 12l7.6-2.6z"/>',
  olap: '<path d="M9 6L4 12l5 6M15 6l5 6-5 6"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  command: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
  bell: '<path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 19a2 2 0 0 0 4 0"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  code: '<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  check: '<path d="M5 12l5 5 9-9"/>',
  arrowR: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  key: '<circle cx="8" cy="8" r="4"/><path d="M11 11l9 9M16 16l2-2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
};
function icon(name, cls = "") {
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`;
}
window.icon = icon;

/* —— Nav(v1.11.0.1 W4.2:表格/文本两条数据线分组隔离,便于各自演进)—— */
const NAV = [
  { group: "表格数据", items: [
    { id: "datasets", label: "表格数据集", ic: "database", href: "datasets-data.html" },
    { id: "ingest-data", label: "数据摄入", ic: "plus", href: "ingest-data.html" },
    { id: "tidy", label: "清洗整理", ic: "olap", href: "tidy.html" },
    { id: "data-prep", label: "数据准备", ic: "file", href: "data-prep.html" },
    { id: "olap", label: "深度分析", ic: "olap", href: "olap.html" },
    { id: "contracts", label: "数据契约", ic: "key", href: "contracts.html" },
    { id: "objects", label: "对象浏览", ic: "olap", href: "objects.html" },
  ] },
  { group: "文本数据", items: [
    { id: "datasets-docs", label: "文档数据集", ic: "file", href: "datasets-docs.html" },
    { id: "ingest-docs", label: "文档摄入", ic: "plus", href: "ingest-docs.html" },
    { id: "embeddings", label: "索引/嵌入", ic: "code", href: "embeddings.html" },
    { id: "search", label: "文本检索", ic: "search", href: "search.html" },
    { id: "image-search", label: "图像检索", ic: "file", href: "search.html?mode=image" },
    { id: "rag", label: "RAG 问答", ic: "search", href: "rag.html" },
    { id: "kg", label: "知识图谱", ic: "code", href: "kg.html" },
    { id: "templates", label: "抽取模板", ic: "code", href: "extraction-templates.html" },
  ] },
  { group: "管理", items: [
    { id: "tasks", label: "异步任务", ic: "dashboard", href: "tasks.html" },
    { id: "admin", label: "管理后台", ic: "key", href: "admin.html" },
    { id: "my-workspace", label: "我的工作区", ic: "bell", href: "my-workspace.html" },
  ] },
  { group: "治理运维", items: [
    { id: "governance", label: "元数据治理", ic: "key", href: "governance.html" },
    { id: "ontology", label: "本体与规则", ic: "code", href: "ontology.html" },
    { id: "actions", label: "行动与场景", ic: "play", href: "actions.html" },
    { id: "decisions", label: "研判台", ic: "sparkles", href: "decisions.html" },
    { id: "lineage", label: "血缘图谱", ic: "database", href: "lineage.html" },
    { id: "audit", label: "审计日志", ic: "bell", href: "audit.html" },
    { id: "system", label: "系统运维", ic: "dashboard", href: "system.html" },
  ] },
];

function decodeUser(tok) {
  try {
    const payload = JSON.parse(atob((tok || "").split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    const _clean = (s) => String(s ?? "").replace(/[<>"&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", '"': "&quot;", "&": "&amp;" }[c]));
    return { user_id: _clean(payload.username || payload.sub || payload.user_id || "user"), role: _clean(String(payload.role || "EDITOR").toUpperCase()) };
  } catch (_) { return { user_id: "user", role: "EDITOR" }; }
}

function renderShell({ active, crumb } = {}) {
  const collapsed = localStorage.getItem("al-collapse") === "1";
  const tok = localStorage.getItem("al_access");
  const u = tok ? decodeUser(tok) : { user_id: "guest", role: "GUEST" };
  const initials = (u.user_id || "U").slice(0, 2).toUpperCase();
  const navHtml = NAV.map(g => `<div class="nav-group-title">${g.group}</div>` + g.items.map(it => `
    <a class="nav-item ${it.id === active ? "active" : ""}" href="${it.href}" data-nav="${it.id}">
      ${icon(it.ic)}<span class="nav-label">${it.label}</span>
    </a>`).join("")).join("");
  const sidebar = `
  <aside class="sidebar">
    <a class="brand" href="index.html">${icon("dashboard")}<span><div class="brand-name">Arrow Lake</div><div class="brand-sub">数据湖仓</div></span></a>
    <nav class="nav">${navHtml}</nav>
    <div style="padding:var(--s3) var(--s4);border-top:1px solid var(--line-soft)">
      <div class="lamp ok pulse" style="margin-bottom:6px"><i></i>湖仓在线</div>
      <div class="muted" style="font-size:.625rem;font-family:var(--font-mono)">© 2026 · DuckDB SQL</div>
    </div>
  </aside>`;
  const header = `
  <header class="header">
    <button class="btn btn-icon btn-ghost" id="navToggle" aria-label="折叠侧栏">${icon("menu")}</button>
    <div class="crumb">${crumb || "深度分析"}</div>
    <div class="h-spacer"></div>
    <button class="btn btn-ghost btn-sm" id="logoutBtn" title="登出">${icon("logout")} 登出</button>
    <div class="user"><span class="avatar">${initials}</span><span class="meta"><b>${u.user_id}</b><span>${u.role}</span></span></div>
  </header>`;
  const root = $("#app"); if (!root) return;
  root.className = "app" + (collapsed ? " collapsed" : "");
  root.insertAdjacentHTML("afterbegin", sidebar + header);
  $("#navToggle")?.addEventListener("click", () => {
    const c = $("#app"); c.classList.toggle("collapsed");
    localStorage.setItem("al-collapse", c.classList.contains("collapsed") ? "1" : "0");
  });
  $("#logoutBtn")?.addEventListener("click", async () => {
    try { await import("../src/auth.js").then(m => m.logout()); } catch (_) {}
    localStorage.removeItem("al_access"); localStorage.removeItem("al_refresh");
    location.href = "login.html";
  });
}
window.renderShell = renderShell;

/* —— View API drawer(等价 cURL / Python,透明可复现)—— */
function openApi(name, curl, py) {
  const el = document.createElement("div"); el.id = "apiDrawer";
  el.innerHTML = `<div style="position:fixed;inset:0;background:rgba(5,8,14,.5);z-index:90" id="apiMask"></div>
  <aside class="panel" style="position:fixed;top:0;right:0;bottom:0;width:min(560px,94vw);z-index:95;border-radius:0;display:flex;flex-direction:column;animation:slideIn var(--dur-3) var(--ease)">
    <div class="panel-h">${icon("code")}<div><h3>${name}</h3><div class="sub">等价 API 调用 · 透明可复现</div></div><span class="actions"><button class="btn btn-icon btn-ghost" id="apiClose">${icon("close")}</button></span></div>
    <div class="panel-b" style="overflow-y:auto;flex:1">
      <div class="muted" style="font-size:var(--fs-cap);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">cURL</div>
      <pre class="codeblk" data-code>${escapeHtml(curl)}</pre>
      <div class="muted" style="font-size:var(--fs-cap);text-transform:uppercase;letter-spacing:.07em;margin:var(--s4) 0 6px">Python SDK</div>
      <pre class="codeblk" data-code>${escapeHtml(py)}</pre>
    </div>
    <style>@keyframes slideIn{from{transform:translateX(20px);opacity:.6}to{transform:none;opacity:1}}.codeblk{background:var(--ink-950);border:1px solid var(--line);border-radius:var(--r-md);padding:var(--s4);font-family:var(--font-mono);font-size:.75rem;color:var(--fg-md);overflow-x:auto;white-space:pre-wrap;line-height:1.6;margin:0}</style>
  </aside>`;
  document.body.appendChild(el);
  const close = () => el.remove();
  $("#apiClose").onclick = close; $("#apiMask").onclick = close;
  $$("pre[data-code]", el).forEach(pre => {
    pre.style.position = "relative";
    const b = document.createElement("button"); b.className = "btn btn-sm btn-ghost";
    b.style.cssText = "position:absolute;top:8px;right:8px";
    b.innerHTML = icon("copy") + " 复制";
    b.onclick = () => { navigator.clipboard?.writeText(pre.textContent); b.innerHTML = icon("check") + " 已复制"; setTimeout(() => b.innerHTML = icon("copy") + " 复制", 1500); };
    pre.appendChild(b);
  });
}
function escapeHtml(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
window.openApi = openApi;
window.AL = { $, $$ };

/* —— Auto-replace <i data-ic> —— */
function applyIcons(root = document) {
  root.querySelectorAll("i[data-ic]").forEach(el => {
    const s = icon(el.dataset.ic, el.className.replace("placeholder", ""));
    if (s) el.outerHTML = s;
  });
}
window.applyIcons = applyIcons;
document.addEventListener("DOMContentLoaded", () => applyIcons());
})();
