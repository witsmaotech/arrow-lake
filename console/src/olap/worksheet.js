// SQL Worksheet controller: dataset select / run (non-streaming JSON only).
// stream 模式已于 2026-07-07 移除(消除 apache-arrow CDN 供应链依赖)。
import { request, ApiError } from "../api.js";
import { createEditor } from "./editor.js";
import { renderResult, renderError } from "./results.js";
import { toast } from "../ui/toast.js";

export async function initWorksheet() {
  const dsSel = document.getElementById("dsSel");
  const editorMount = document.getElementById("editor");
  const runBtn = document.getElementById("runBtn");
  const apiBtn = document.getElementById("apiBtn");
  const maxRowsInp = document.getElementById("maxRows");
  const resultHost = document.getElementById("result");

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

  // 2. Editor
  const initial = localStorage.getItem("al-last-sql") ||
    `SELECT *\nFROM ${dsSel.value || "<dataset>"}\nLIMIT 100;`;
  const editor = createEditor(editorMount, { onRun: run, initial });

  async function run() {
    const ds = dsSel.value;
    const sql = editor.value.replace(/<dataset>/g, ds).trim();
    if (!sql) { toast("请输入 SQL", "warn"); return; }
    if (!ds) { toast("请先选择数据集", "warn"); return; }
    localStorage.setItem("al-last-sql", editor.value);

    const max_rows = parseInt(maxRowsInp.value) || undefined;
    runBtn.disabled = true; runBtn.dataset.label = runBtn.innerHTML; runBtn.innerHTML = "运行中…";

    const t0 = performance.now();
    try {
      const resp = await request("POST", `/datasets/${encodeURIComponent(ds)}/query/olap`,
        { body: { sql, format: "json", max_rows } });
      renderResult(resultHost, resp, Math.round(performance.now() - t0));
    } catch (e) {
      handleError(e);
    } finally {
      runBtn.disabled = false; runBtn.innerHTML = runBtn.dataset.label;
    }
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
  dsSel.addEventListener("change", () => {
    editor.value = `SELECT *\nFROM ${dsSel.value}\nLIMIT 100;`;
    toast(`已切换到 ${dsSel.value}`, "info", 1500);
  });
  apiBtn?.addEventListener("click", () => {
    const ds = dsSel.value || "<name>";
    const sql = editor.value.replace(/<dataset>/g, ds);
    window.openApi("OLAP SQL · query/olap",
      `curl -X POST $API/api/v1/datasets/${ds}/query/olap \\\n  -H "Authorization: Bearer $TOKEN" \\\n  -d '{"sql": "${sql.replace(/\n/g, " ")}", "format": "json", "max_rows": 1000}'`,
      `lake.olap_query("${ds}", """${sql}""", max_rows=1000)`);
  });
}
