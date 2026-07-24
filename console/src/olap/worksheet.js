// SQL Worksheet controller: dataset select / run / history / EXPLAIN (non-streaming JSON).
// dataset 名统一加双引号(DuckDB 标识符引用),兼容含 "-" 等特殊字符的 dataset(如 api-test)。
// stream 模式已于 2026-07-07 移除(消除 apache-arrow CDN 供应链依赖)。
import { request, ApiError } from "../api.js";
import { createEditor } from "./editor.js";
import { renderResult, renderError } from "./results.js";
import { toast } from "../ui/toast.js";

const HIST_KEY = "al-sql-history";
const HIST_MAX = 20;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// DuckDB 标识符引用:name → "name"(含双引号,转义内部双引号)
const q = (name) => `"${String(name).replace(/"/g, '""')}"`;

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY)) || []; } catch { return []; }
}
function saveHistory(sql) {
  const s = sql.trim(); if (!s) return;
  let h = loadHistory().filter(x => x !== s);
  h.unshift(s);
  localStorage.setItem(HIST_KEY, JSON.stringify(h.slice(0, HIST_MAX)));
}

export async function initWorksheet() {
  const dsSel = document.getElementById("dsSel");
  const editorMount = document.getElementById("editor");
  const runBtn = document.getElementById("runBtn");
  const explainBtn = document.getElementById("explainBtn");
  const apiBtn = document.getElementById("apiBtn");
  const maxRowsInp = document.getElementById("maxRows");
  const historySel = document.getElementById("historySel");
  const resultHost = document.getElementById("result");

  // 模板:含 <dataset> 占位符,run 时替换为 "实际名"
  const tpl = (name) => `SELECT *\nFROM ${name ? q(name) : "<dataset>"}\nLIMIT 100;`;

  // 1. Load datasets
  try {
    const list = await request("GET", "/datasets?limit=500");
    // createElement + textContent: d.name 来自后端,防御性防注入
    dsSel.textContent = "";
    for (const d of list.datasets) {
      const opt = document.createElement("option");
      opt.value = d.name;
      opt.textContent = `${d.name} · ${(d.num_rows || 0).toLocaleString()} 行`;
      dsSel.appendChild(opt);
    }
    if (list.datasets.length) dsSel.value = list.datasets[0].name;
  } catch (e) {
    toast(`加载数据集失败: ${e.message}`, "danger");
  }
  // 结果宿主记下当前数据集名(供 results.js 的 Parquet 导出 / SUMMARIZE 复用)
  resultHost.dataset.ds = dsSel.value;

  // 2. Editor
  const initial = localStorage.getItem("al-last-sql") || tpl(dsSel.value);
  const editor = createEditor(editorMount, { onRun: run, initial });
  const schemaList = document.getElementById("schemaList");
  async function loadSchema() {
    const ds = dsSel.value;
    if (!ds || !schemaList) return;
    schemaList.innerHTML = `<div class="muted" style="padding:8px;font-size:.75rem">加载 schema…</div>`;
    try {
      const s = await request("GET", `/datasets/${encodeURIComponent(ds)}/schema`);
      const fields = s.fields || [];
      schemaList.innerHTML = (fields.length
        ? `<div class="muted" style="padding:6px 8px;font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--line-soft)">${fields.length} 字段 · 点击插入编辑器</div>` +
          fields.map((f) => {
            const cm = f.comment || "";
            const cmHtml = cm ? `<div class="sc-comment" title="${esc(cm)}">// ${esc(cm)}</div>` : "";
            return `<div class="schema-col" data-col="${encodeURIComponent(f.name)}" title="${esc(f.type)} · ${f.nullable ? "nullable" : "not null"} · 点击插入"><div class="sc-head"><span class="sc-name">${esc(f.name)}</span><span class="sc-type">${esc(f.type)}</span></div>${cmHtml}</div>`;
          }).join("")
        : `<div class="muted" style="padding:12px;text-align:center">无字段</div>`);
      schemaList.querySelectorAll(".schema-col").forEach((el) => {
        el.addEventListener("click", () => {
          const col = decodeURIComponent(el.dataset.col);
          editor.insert(col);
          toast(`已插入 ${col}`, "info", 1200);
        });
      });
    } catch (e) {
      schemaList.innerHTML = `<div class="muted" style="padding:8px;color:var(--danger);font-size:.75rem">schema 加载失败</div>`;
    }
  }
  renderHistory();
  loadSchema();

  function renderHistory() {
    if (!historySel) return;
    const h = loadHistory();
    historySel.textContent = "";
    const ph = document.createElement("option");
    ph.value = ""; ph.textContent = `📋 历史 (${h.length})`;
    historySel.appendChild(ph);
    h.forEach((s, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = s.replace(/\s+/g, " ").slice(0, 64);
      historySel.appendChild(o);
    });
  }

  // 核心执行(非流式 JSON),复用 RBAC + SELECT 校验 + 行级 ACL
  async function execute(sql, label) {
    const ds = dsSel.value;
    if (!ds) { toast("请先选择数据集", "warn"); return null; }
    if (!sql.trim()) { toast("请输入 SQL", "warn"); return null; }
    const max_rows = parseInt(maxRowsInp.value) || undefined;
    runBtn.disabled = true; runBtn.dataset.label = runBtn.innerHTML; runBtn.innerHTML = `${label || "运行"}…`;
    const t0 = performance.now();
    try {
      const resp = await request("POST", `/datasets/${encodeURIComponent(ds)}/query/olap`,
        { body: { sql, format: "json", max_rows } });
      const ms = Math.round(performance.now() - t0);
      renderResult(resultHost, resp, ms);
      const m = document.getElementById("meta");
      if (m) m.textContent = resp.success ? `${(resp.row_count ?? 0).toLocaleString()} 行 · ${resp.column_count ?? 0} 列 · ${ms}ms` : "查询失败";
      return resp;
    } catch (e) {
      const m = document.getElementById("meta");
      if (m) m.textContent = "错误";
      handleError(e);
      return null;
    } finally {
      runBtn.disabled = false; runBtn.innerHTML = runBtn.dataset.label;
    }
  }

  // 替换 <dataset> 占位符为 "实际名"(带引号,兼容 api-test 等含 - 的名字);
  // 规范化(DuckDB 单语句 execute 要求):取首个分号前的单句 + 去结尾分号 + trim。
  // 否则后端 conn.execute 对结尾/多分号裸 500(无 JSON detail)。
  const fillSql = () =>
    editor.value
      .replace(/<dataset>/g, q(dsSel.value))
      .split(/;\s*\n|;\s*(?=\w)/)[0]   // 多语句 → 取首句(保留语句内分号,如字符串字面量极少见)
      .replace(/;\s*$/, "")            // 去结尾分号
      .trim();

  async function run() {
    const sql = fillSql();
    const resp = await execute(sql);
    if (resp && resp.success) {
      saveHistory(sql);
      renderHistory();
      localStorage.setItem("al-last-sql", editor.value);
    }
  }

  // EXPLAIN: DuckDB 计划(EXPLAIN 不在 _BLOCKED_SQL_PREFIXES,放行)
  async function runExplain() {
    const inner = fillSql().replace(/;\s*$/, "");
    if (!inner) { toast("请先输入要 EXPLAIN 的 SQL", "warn"); return; }
    await execute(`EXPLAIN ${inner}`, "EXPLAIN");
  }

  // SUMMARIZE 概览:全表每列质量(min/max/avg/std/分位数/null%)。
  // bridge._validate_sql 要求 SELECT 前缀 → 用子查询 SELECT * FROM (SUMMARIZE "ds") 包装;
  // _apply_limit 追加 LIMIT 到外层 SELECT,合法。(实测 8 列统计通过)
  async function runSummarize() {
    const ds = dsSel.value;
    if (!ds) { toast("请先选择数据集", "warn"); return; }
    await execute(`SELECT * FROM (SUMMARIZE ${q(ds)})`, "SUMMARIZE");
  }

  function handleError(e) {
    if (e instanceof ApiError && e.status === 401) {
      toast("未授权或登录过期,请重新登录", "danger");
      setTimeout(() => (location.href = "login.html"), 1000);
      return;
    }
    const msg = e.message || String(e);
    renderError(resultHost, msg);
    if (e.status === 400 || e.status === 422) toast(`SQL 校验失败: ${msg}`, "danger", 6000);
    else toast(`错误: ${msg}`, "danger", 6000);
  }

  // 3. Wire UI
  runBtn.addEventListener("click", run);
  explainBtn?.addEventListener("click", runExplain);
  document.getElementById("summarBtn")?.addEventListener("click", runSummarize);
  // 暴露当前 SQL getter 给物化视图面板(olap.html 内联脚本用)
  window.__olapGetSql = fillSql;
  window.__olapDataset = () => dsSel.value;
  historySel?.addEventListener("change", () => {
    const h = loadHistory();
    const idx = parseInt(historySel.value);
    if (!isNaN(idx) && h[idx] !== undefined) {
      editor.value = h[idx];
      historySel.value = "";
    }
  });
  dsSel.addEventListener("change", () => {
    editor.value = tpl(dsSel.value);
    toast(`已切换到 ${dsSel.value}`, "info", 1500);
    loadSchema();
  });
  apiBtn?.addEventListener("click", () => {
    const ds = dsSel.value || "<name>";
    const sql = fillSql();
    window.openApi("OLAP SQL · query/olap",
      `curl -X POST $API/api/v1/datasets/${ds}/query/olap \\\n  -H "Authorization: Bearer $TOKEN" \\\n  -H "X-API-Key: $KEY" \\\n  -d '{"sql": "${sql.replace(/\n/g, " ").replace(/"/g, '\\"')}", "format": "json", "max_rows": 1000}'`,
      `lake.olap_query("${ds}", """${sql}""", max_rows=1000)`);
  });
}
