# v1.9.2 批 1:system(运维)+ audit(合规)页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans / subagent-driven-development。Steps use `- [ ]` checkbox。

**Goal:** 补 console 最大缺口 —— system.html(运维面板)+ audit.html(审计日志),让界面覆盖后端运维/合规能力域。

**Architecture:** 两页沿用 console 现有模式(`<script type="module"> import { request } from "./src/api.js"` + `renderShell({active, crumb})` + app.css 组件)。system 接 `/health` + `/api/v1/version` + `/metrics`;audit 接 `/api/v1/audit/query` + `/export`。NAV 加「运维」「合规」组。

**Tech Stack:** 原生 JS + ES 模块(零构建)、app.css 设计系统、playwright chromium-1217 验证。

**Spec:** `docs/v1.9.2-roadmap.md` 批 1

## Global Constraints

- 零 npm 依赖、零构建(浏览器原生 ES module)。
- 走 `renderShell`(console-layout.js),鉴权走 api.js 双层(Bearer + X-API-Key)。
- 后端端点已存在(v1.9.0+),**不改后端**(纯前端新页)。
- `audit/query` 需 VIEWER;`audit/export` 需 ADMIN —— UI 按 role 显隐导出按钮(`request.state.user.role`,从 JWT payload 读)。
- `/metrics` 是 Prometheus 文本格式 → 前端只取关键计数(`rate_limit_rejected_total` / `query_*`),不全文渲染。
- trunk-based:每任务 commit master,conventional commits + Co-Authored-By。
- 验证:playwright chromium-1217,0 console error / 0 溢出 / 真数据渲染。

---

## Task 1: system.html — 运维监控面板

**Files:**
- Create: `console/system.html`
- Read(模式参考):`console/tasks.html`(renderShell + request 模式)

**Interfaces:**
- Consumes:`GET /health`(返 status/version/storage/gravitino/lance_rest/duckdb_pool)、`GET /api/v1/version`(版本+依赖)、`GET /metrics`(Prometheus 文本)
- Produces:`console/system.html`(运维页)

- [ ] **Step 1: 创建 system.html 骨架(renderShell + NAV active="system")**

```html
<!doctype html><html lang="zh"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>系统运维 · Arrow Lake Console</title>
<link rel="stylesheet" href="assets/tokens.css"/><link rel="stylesheet" href="assets/app.css"/><link rel="stylesheet" href="assets/console.css"/>
</head><body><div id="app"></div>
<script type="module">
import { request, API_BASE } from "./src/api.js";
const { renderShell, icon } = window;
renderShell({ active: "system", crumb: "<b>系统运维</b>" });
const $ = (s) => document.querySelector(s);
$(".page").innerHTML = `
  <div class="h-sec"><h1>系统运维</h1><span class="desc">健康 / 版本 / 依赖 / 指标</span>
    <button class="btn btn-ghost btn-sm" id="refresh" style="margin-left:auto">${icon("menu")} 刷新</button></div>
  <div class="kpi-row" id="kpis"></div>
  <div class="grid g-2">
    <div class="panel"><div class="panel-h"><h3>${icon("database")} 健康状态</h3></div><div class="panel-b" id="health"></div></div>
    <div class="panel"><div class="panel-h"><h3>${icon("dashboard")} 版本与依赖</h3></div><div class="panel-b" id="version"></div></div>
  </div>
  <div class="panel"><div class="panel-h"><h3>${icon("search")} 关键指标</h3><span class="sub">prometheus</span></div><div class="panel-b" id="metrics"></div></div>`;
$("#refresh").addEventListener("click", load);
load();
```

- [ ] **Step 2: load() — 拉 /health + /version + /metrics 并渲染**

续 system.html `<script>`(接上面):
```js
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function lamp(ok,txt){return `<span class="lamp ${ok?"ok":"warn"}"><i></i>${esc(txt)}</span>`}

async function load(){
  $("#kpis").innerHTML = `<div class="kpi"><div class="label">加载中</div><div class="val skeleton line" style="width:40%"></div></div>`.repeat(4);
  try{
    const h = await request("GET", "/health");  // {status,version,storage,gravitino,lance_rest,duckdb_pool}
    $("#kpis").innerHTML = [
      ["状态", h.status==="ok"?"健康":"异常", h.status==="ok"],
      ["存储", h.storage, h.storage==="accessible"],
      ["Gravitino", h.gravitino, h.gravitino==="healthy"],
      ["Lance REST", h.lance_rest, h.lance_rest==="healthy"],
    ].map(([k,v,ok])=>`<div class="kpi interactive"><div class="label">${esc(k)}</div><div class="val" style="font-size:1rem">${lamp(ok,esc(v))}</div></div>`).join("");
    $("#health").innerHTML = `<div class="grid g-2">
      <div>${lamp(h.storage==="accessible","存储")} <span class="muted">${esc(h.storage)}</span></div>
      <div>${lamp(h.gravitino==="healthy","Gravitino")}</div>
      <div>${lamp(h.lance_rest==="healthy","Lance REST")}</div>
      <div>DuckDB 池 <b class="mono">${h.duckdb_pool?.pool_size??0}</b> 活跃 <b class="mono">${h.duckdb_pool?.active_sessions??0}</b> · 查询 <b class="mono">${h.duckdb_pool?.total_queries??0}</b></div>
    </div>`;
  }catch(e){ $("#health").innerHTML = `<div class="empty error">健康检查失败: ${esc(e.detail||e.message)}</div>`; }

  try{
    const v = await request("GET", "/api/v1/version");  // {version, dependencies, ...}
    $("#version").innerHTML = `<div><b class="mono">Arrow Lake ${esc(v.version)}</b></div>
      <div class="muted mono" style="font-size:.78rem;margin-top:8px">${(v.dependencies||[]).map(d=>`<span class="dep"><span class="lamp"></span>${esc(d.name||d)} ${esc(d.version||"")}</span>`).join(" ")}</div>`;
  }catch(e){ $("#version").innerHTML = `<div class="muted">版本信息需 VIEWER+ 登录</div>`; }

  try{
    const m = await fetch(`${API_BASE.replace(/\/api\/v1$/,"")}/metrics`).then(r=>r.text());  // /metrics public,文本
    const pick = (re) => { const ma = m.match(re); return ma ? ma[1] : "0"; };
    $("#metrics").innerHTML = `<div class="grid g-3">
      <div class="kpi"><div class="label">请求总数</div><div class="val">${pick(/http_requests_total\s+([\d.]+)/)}</div></div>
      <div class="kpi"><div class="label">限流拒绝</div><div class="val">${pick(/rate_limit_rejected_total[^}]*}\s+([\d.]+)/)}</div></div>
      <div class="kpi"><div class="label">查询数</div><div class="val">${pick(/duckdb_queries_total\s+([\d.]+)/)}</div></div>
    </div>`;
  }catch(e){ $("#metrics").innerHTML = `<div class="muted">指标不可达</div>`; }
}
</script></body></html>
```

- [ ] **Step 3: 验证 system.html 渲染**

写 `/tmp/verify_system.py`:
```python
from playwright.sync_api import sync_playwright
import pathlib
CHROME=str(pathlib.Path.home()/".cache/ms-playwright/chromium-1217/chrome-linux64/chrome")
errors=[]
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=CHROME); pg=b.new_page(viewport={"width":1440,"height":900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://localhost:5189/system.html", wait_until="domcontentloaded"); pg.wait_for_timeout(2000)
    assert pg.locator("#health").count()==1, "health panel 缺"
    sw=pg.evaluate("document.documentElement.scrollWidth"); cw=pg.evaluate("document.documentElement.clientWidth")
    assert sw<=cw, f"溢出 {sw-cw}"
    assert not errors, errors[:3]
    print("PASS system"); b.close()
```
Run: `NO_PROXY=127.0.0.1,localhost .venv/bin/python3 /tmp/verify_system.py` → 期望 `PASS system`(未登录时 /health 公开、/version 401 降级显示提示,0 error)。

- [ ] **Step 4: Commit**
```bash
git add console/system.html && git commit -m "feat(console): v1.9.2 批1 system.html 运维面板(健康/版本/指标)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: audit.html — 审计日志(合规)

**Files:**
- Create: `console/audit.html`
- Consumes:`GET /api/v1/audit/query`(VIEWER,返 audit events)、`POST /api/v1/audit/export`(ADMIN,导出)

- [ ] **Step 1: 创建 audit.html(renderShell active="audit"+ 过滤 + 列表)**

```html
<!doctype html><html lang="zh"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>审计日志 · Arrow Lake Console</title>
<link rel="stylesheet" href="assets/tokens.css"/><link rel="stylesheet" href="assets/app.css"/><link rel="stylesheet" href="assets/console.css"/>
</head><body><div id="app"></div>
<script type="module">
import { request } from "./src/api.js";
const { renderShell, icon } = window;
renderShell({ active: "audit", crumb: "<b>审计日志</b>" });
const $ = (s) => document.querySelector(s);
const role = (()=>{ try{ const t=localStorage.getItem("al_access"); if(!t)return "viewer"; const p=JSON.parse(atob(t.split(".")[1].replace(/-/g,"+").replace(/_/g,"/"))); return (p.role||"viewer").toLowerCase(); }catch(e){ return "viewer"; } })();
$(".page").innerHTML = `
  <div class="h-sec"><h1>审计日志</h1><span class="desc">谁 · 何时 · 对什么 · 做了什么</span>
    <button class="btn btn-ghost btn-sm" id="export" style="margin-left:auto;display:${role==="admin"?"inline-flex":"none"}">${icon("logout")} 导出</button></div>
  <div class="panel"><div class="panel-b"><div class="flex wrap" style="gap:var(--s3)">
    <input class="input" id="fUser" placeholder="用户名过滤" style="max-width:160px">
    <input class="input" id="fAction" placeholder="操作过滤(create/delete/...)" style="max-width:180px">
    <input class="input" id="fDs" placeholder="数据集过滤" style="max-width:160px">
    <button class="btn btn-primary btn-sm" id="filter">${icon("search")} 查询</button>
  </div></div></div>
  <div class="panel"><div class="panel-h"><h3>${icon("bell")} 事件</h3><span class="sub" id="cnt">—</span></div>
    <div class="tbl-wrap"><table class="tbl" id="tbl"></table></div></div>`;
$("#filter").addEventListener("click", load);
$("#export").addEventListener("click", exportAudit);
load();
```

- [ ] **Step 2: load() — 调 /audit/query 渲染表格**

续 audit.html:
```js
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function fmtTime(s){ if(!s)return "—"; const d=new Date(s); return isNaN(d)?esc(s).slice(0,19):d.toLocaleString("zh-CN",{hour12:false}).replace(/\//g,"-"); }

async function load(){
  const q = new URLSearchParams();
  const u=$("#fUser").value.trim(), a=$("#fAction").value.trim(), d=$("#fDs").value.trim();
  if(u)q.set("user",u); if(a)q.set("action",a); if(d)q.set("dataset",d);
  $("#tbl").innerHTML = `<thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>详情</th></tr></thead>
    <tbody><tr><td colspan="5" class="muted" style="padding:28px;text-align:center">加载中…</td></tr></tbody>`;
  try{
    const r = await request("GET", `/audit/query?${q}`);  // {events:[...]} 或 {items:[...]}
    const events = r.events || r.items || r.audits || r || [];
    const list = Array.isArray(events)?events:[];
    $("#cnt").textContent = `${list.length} 条`;
    $("#tbl").innerHTML = `<thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>详情</th></tr></thead><tbody>${
      list.length ? list.map(e=>`<tr>
        <td class="mono">${fmtTime(e.timestamp||e.created_at||e.time)}</td>
        <td><b>${esc(e.user||e.username||e.actor||"—")}</b></td>
        <td><span class="tag ${e.action&&/delete|drop/i.test(e.action)?"amber":/create|insert|build/i.test(e.action||"")?"teal":"info"}">${esc(e.action||e.event||"—")}</span></td>
        <td class="mono">${esc(e.dataset||e.resource||e.target||"—")}</td>
        <td class="muted mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis">${esc(typeof e.detail==="string"?e.detail:JSON.stringify(e.detail||e.metadata||{}).slice(0,80))}</td>
      </tr>`).join("") : `<tr><td colspan="5" class="muted" style="padding:28px;text-align:center">无审计事件(需登录 VIEWER+,或 _audit_trail 为空)</td></tr>`
    }</tbody>`;
  }catch(e){ $("#tbl").innerHTML = `<thead><tr><th>事件</th></tr></thead><tbody><tr><td class="empty error">查询失败: ${esc(e.detail||e.message)}(需登录)</td></tr></tbody>`; }
}

async function exportAudit(){
  try{ await request("POST", "/audit/export", {body:{}}); alert("导出任务已提交(ADMIN)"); }
  catch(e){ alert("导出失败: "+(e.detail||e.message)); }
}
</script></body></html>
```

- [ ] **Step 3: 验证 audit.html**
写 `/tmp/verify_audit.py`(同 system 结构,goto audit.html,assert `#tbl` count==1,0 溢出/0 error)。Run → `PASS audit`。

- [ ] **Step 4: Commit**
```bash
git add console/audit.html && git commit -m "feat(console): v1.9.2 批1 audit.html 审计日志(查询/过滤/导出)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: NAV 加「运维/合规」组 + index 入口 + 终验

**Files:**
- Modify: `console/assets/console-layout.js`(NAV 数组加 system/audit)

- [ ] **Step 1: NAV 加运维组(system)+ 合规组(audit)**

console-layout.js 的 `NAV` 数组(之前读过的结构:`[{group, items:[{id,label,ic,href}]}]`),在管理组后加:
```js
{ group: "运维", items: [
  { id: "system", label: "系统运维", ic: "dashboard", href: "system.html" },
]},
{ group: "合规", items: [
  { id: "audit", label: "审计日志", ic: "bell", href: "audit.html" },
]},
```
(Edit:定位 NAV 数组末尾 `]` 前插入两组。)

- [ ] **Step 2: index.html 加快捷入口**(可选,与 dashboard 卡片风格一致)
index.html 快捷入口区加 system/audit 卡片(`<a href="system.html">` / `<a href="audit.html">`)。

- [ ] **Step 3: 终验(两页 + NAV + 全页 0 溢出)**
扩展 `/tmp/verify_9pages.py` 的 pages9 加 `"system","audit"`,跑 → 15 页全 PASS(0 error 0 溢出)。

- [ ] **Step 4: Commit**
```bash
git add console/assets/console-layout.js console/index.html && git commit -m "feat(console): v1.9.2 批1 NAV 加运维/合规组 + index 入口

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage**:批 1 = system.html(运维)+ audit.html(合规)+ NAV → Task 1/2/3 覆盖 ✓
**2. Placeholder**:`r.events||r.items||...` 多键容错(audit 响应字段未 100% 确认,执行时按实际校准 —— 标注在 Task 2 Step 2);无 TBD。
**3. 一致性**:renderShell({active:"system"/"audit"})与 NAV id 一致;request 鉴权一致(api.js)。
**4. 风险**:audit/query 响应字段(执行时 Read audit.py 确认 events/items 键);/metrics 文本解析正则(执行时按实际 metric 名校准)。

---

> **执行注意**:Task 2 audit/query 响应 + Task 1 /metrics 解析,执行时先 curl 实际端点确认字段,再按实际调整(代码已多键容错)。
