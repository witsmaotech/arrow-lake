// SQL Worksheet controller: dataset select / run / dispatch stream vs non-stream
import { request, ApiError, API_BASE } from "../api.js";
import { getAccessToken } from "../auth.js";
import { createEditor } from "./editor.js";
import { renderResult, renderError } from "./results.js";
import { streamQuery } from "./stream.js";
import { toast } from "../ui/toast.js";

export async function initWorksheet() {
  const dsSel = document.getElementById("dsSel");
  const editorMount = document.getElementById("editor");
  const runBtn = document.getElementById("runBtn");
  const apiBtn = document.getElementById("apiBtn");
  const streamChk = document.getElementById("streamChk");
  const maxRowsInp = document.getElementById("maxRows");
  const batchSizeInp = document.getElementById("batchSize");
  const resultHost = document.getElementById("result");

  // 1. Load datasets
  try {
    const list = await request("GET", "/datasets?limit=500");
    // createElement + textContent: d.name 来自后端,防御性防注入(后端虽有 _NAME_PATTERN,纵深防御)
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

    const stream = streamChk.checked;
    const max_rows = parseInt(maxRowsInp.value) || undefined;
    runBtn.disabled = true; runBtn.dataset.label = runBtn.innerHTML; runBtn.innerHTML = "运行中…";

    const t0 = performance.now();
    try {
      if (stream) {
        await runStream(ds, sql, { max_rows, batch_size: parseInt(batchSizeInp.value) || 1000 }, t0);
      } else {
        const resp = await request("POST", `/datasets/${encodeURIComponent(ds)}/query/olap`,
          { body: { sql, format: "json", max_rows } });
        renderResult(resultHost, resp, Math.round(performance.now() - t0));
      }
    } catch (e) {
      handleError(e);
    } finally {
      runBtn.disabled = false; runBtn.innerHTML = runBtn.dataset.label;
    }
  }

  async function runStream(ds, sql, opts, t0) {
    resultHost.innerHTML = `<div class="result-meta"><span class="lamp warn"><i></i></span> 流式接收中…</div>`;
    let columns = null, allRows = [];
    await streamQuery({
      url: `${API_BASE}/datasets/${encodeURIComponent(ds)}/query/olap`,
      token: getAccessToken(),
      body: { sql, format: "arrow_ipc", stream: true, batch_size: opts.batch_size, max_rows: opts.max_rows },
      onSchema: (s) => { columns = s.columns; },
      onBatch: (b) => {
        allRows = allRows.concat(b.rows);
        if (!columns && b.rows[0]) columns = Object.keys(b.rows[0]);
        const meta = resultHost.querySelector(".result-meta");
        if (meta) meta.innerHTML = `<span class="lamp ok"><i></i></span> 已接收 <b>${allRows.length.toLocaleString()}</b> 行…`;
      },
      onDone: (d) => {
        const elapsed = Math.round(performance.now() - t0);
        const resp = { success: true, format: "json", row_count: d.total_rows, column_count: (columns || []).length, rows: allRows };
        renderResult(resultHost, resp, elapsed);
        toast(`完成: ${d.total_rows.toLocaleString()} 行 · ${elapsed}ms`, "ok");
      },
      onError: (e) => handleError(e),
    });
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
